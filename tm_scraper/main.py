#!/usr/bin/env python3
"""Entrypoint. Two jobs share the DB + a work queue (TM.detail_queue):

  python main.py --mode discover     # find events, upsert, enqueue for detail
  python main.py --mode worker       # claim events from the queue + scrape detail
  python main.py --mode all          # discover then worker, in one process (default)

Scale detail scraping by running `--mode worker` on multiple machines/instances --
each is one browser + one proxy and drains the shared queue. Kasada needs a real
(headed) browser, so pass --headed (or set HEADLESS=false).
"""
import argparse
import asyncio
import os
from datetime import datetime, timezone

import config


def main():
    ap = argparse.ArgumentParser(description="Ticketmaster scraper -> Postgres (TM schema)")
    ap.add_argument("--mode", choices=["discover", "worker", "all"], default="all",
                    help="discover events, run a detail worker, or both (default: all)")
    ap.add_argument("--headed", action="store_true", help="run browser non-headless (needed for Kasada)")
    ap.add_argument("--seat-mode", choices=["available", "full"], help="override SEAT_MODE")
    ap.add_argument("--query", help="override search query (e.g. nfl)")
    ap.add_argument("--refresh-manifest", action="store_true", help="ignore manifest cache")
    ap.add_argument("-v", "--verbose", action="store_true", help="DEBUG-level logging")
    ap.add_argument("--limit", type=int, help="discover: enqueue only the first N events (debug)")
    args = ap.parse_args()

    if args.headed:
        config.HEADLESS = False
    if args.seat_mode:
        config.SEAT_MODE = args.seat_mode
    if args.query:
        config.QUERY = args.query
    if args.refresh_manifest:
        config.REFRESH_MANIFEST = True
    if args.verbose:
        config.LOG_LEVEL = "DEBUG"
    if args.limit:
        config.MAX_EVENTS = args.limit

    import log
    log.setup()
    os.makedirs(config.MANIFEST_CACHE_DIR, exist_ok=True)

    asyncio.run(_dispatch(args.mode))


async def _dispatch(mode):
    import db
    import log
    from browser_session import TMSession
    from discover import run_discovery
    from worker import run_worker

    _LOG = log.get("main")
    _LOG.info("=" * 68)
    _LOG.info("TM scraper | mode=%s | query=%r | window=%s..%s | seat_mode=%s",
              mode, config.QUERY, config.START_DATE, config.END_DATE, config.SEAT_MODE)
    _LOG.info("=" * 68)

    conn = db.connect()
    _LOG.info("DB connected (schema=%s)", config.DB_SCHEMA)

    session = TMSession(headless=config.HEADLESS)
    await session.start()          # launches Chrome, claims a proxy, solves Kasada
    try:
        run_ts = datetime.now(timezone.utc)
        if mode in ("discover", "all"):
            await run_discovery(conn, session, run_ts)
        if mode in ("worker", "all"):
            await run_worker(conn, session)
    finally:
        await session.close()      # releases the proxy
        conn.close()
    _LOG.info("done.")


if __name__ == "__main__":
    main()
