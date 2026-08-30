#!/usr/bin/env python3
"""Entrypoint.

  python main.py                 # full run using .env settings
  python main.py --headed        # show the browser (debug Kasada)
  python main.py --seat-mode full
"""
import argparse
import asyncio
import os

import config


def main():
    ap = argparse.ArgumentParser(description="Ticketmaster scraper -> Postgres (TM schema)")
    ap.add_argument("--headed", action="store_true", help="run browser non-headless")
    ap.add_argument("--seat-mode", choices=["available", "full"], help="override SEAT_MODE")
    ap.add_argument("--query", help="override search query (e.g. nfl)")
    ap.add_argument("--refresh-manifest", action="store_true", help="ignore manifest cache")
    ap.add_argument("-v", "--verbose", action="store_true", help="DEBUG-level logging")
    ap.add_argument("--limit", type=int, help="only fetch detail for the first N events (debug)")
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

    from pipeline import run
    asyncio.run(run())


if __name__ == "__main__":
    main()
