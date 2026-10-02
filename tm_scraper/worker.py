"""Detail worker: claim events from TM.detail_queue, scrape seats/sections/offers,
mark them done. ONE instance = one browser + one proxy (one Kasada identity).

Scale horizontally by running more instances (processes/machines): they share the
queue via FOR UPDATE SKIP LOCKED, so they never collide and self-balance. A crash
is safe -- the lease expires and another worker re-claims the event.
"""
import asyncio
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import config
import db
import log
import parse
from event_details import fetch_batch, RateLimiter

_LOG = log.get("worker")

_US_HOST = "www.ticketmaster.com"


async def _warm_brand_domains(conn, session):
    """HOST events (e.g. Australian Open on .com.au) have their own Kasada; curl to
    their endpoints needs that domain solved. Register one event url per brand
    domain in the queue so the session (re)warms them on every context rebuild
    (start AND rotate -- otherwise a rotation drops the .com.au session), then warm
    them now for the first time."""
    brands = {}
    for url in db.pending_event_urls(conn).values():
        host = urlsplit(url).netloc if url else ""
        if host and "ticketmaster" in host and host != _US_HOST:
            brands.setdefault(host, url)
    session.set_brand_urls(brands.values())
    await session.warm_brands()


async def run_worker(conn, session):
    limiter = RateLimiter(config.REQ_PER_SEC)
    worker_id = config.WORKER_ID
    _LOG.info("worker %s starting (batch=%d, lease=%dmin, max_attempts=%d, idle_sleep=%ds, "
              "concurrency=%d)", worker_id, config.CLAIM_BATCH, config.LEASE_MINUTES,
              config.MAX_ATTEMPTS, config.WORKER_IDLE_SLEEP, config.CONCURRENCY)

    await _warm_brand_domains(conn, session)

    done = errored_total = 0
    t0 = time.monotonic()
    block_cycles = 0

    async def on_result(event_id, facets_doc, manifest):
        """Persist one event + sweep its stale seats + mark done. A fresh per-event
        timestamp keeps the sweep correct (fresh rows == now, older rows < now)."""
        nonlocal done
        now = datetime.now(timezone.utc)
        rows = parse.build_seating_rows(event_id, facets_doc, manifest, config.SEAT_MODE)
        for table in ("sections", "section_rows", "seats", "offers", "seat_offers"):
            db.upsert(conn, table, rows[table], now)
        db.sweep_unavailable(conn, [event_id], now)     # per-event freshness sweep
        db.complete_event(conn, event_id, now)
        conn.commit()
        done += 1
        elapsed = time.monotonic() - t0
        _LOG.info("  done %s: seats=%d offers=%d | %d scraped | %.2f ev/s",
                  event_id[:8], len(rows["seats"]), len(rows["offers"]),
                  done, done / elapsed if elapsed else 0)

    while True:
        event_ids = db.claim_events(conn, worker_id, config.CLAIM_BATCH, config.LEASE_MINUTES)
        conn.commit()
        if not event_ids:
            counts = db.queue_counts(conn)
            # pending>0 with nothing claimable means everything left is deferred
            # (block backoff) -> wait for some to become eligible, don't exit.
            if counts.get("pending", 0) > 0:
                _LOG.info("no eligible events (%d pending but deferred); sleeping 60s...",
                          counts["pending"])
                await asyncio.sleep(60)
                continue
            if config.WORKER_IDLE_SLEEP > 0:
                _LOG.info("queue empty %s; sleeping %ds...", counts, config.WORKER_IDLE_SLEEP)
                await asyncio.sleep(config.WORKER_IDLE_SLEEP)
                continue
            _LOG.info("queue drained %s; worker done (%d scraped, %d errored)",
                      counts, done, errored_total)
            break

        _LOG.info("claimed %d events", len(event_ids))
        url_map = db.event_urls(conn, event_ids)   # brand domain for HOST events
        results, blocked, errored = await fetch_batch(
            event_ids, session, config.CONCURRENCY, limiter, on_result, url_map=url_map)

        # 404 = the event doesn't exist -> fail now; other transient errors -> retry.
        for eid, fatal in errored:
            db.fail_event(conn, eid, config.MAX_ATTEMPTS, fatal=fatal)
            errored_total += 1

        # A 403 is Kasada blocking the IP/session, NOT the event's fault (these events
        # ARE scrapeable). So DEFER every blocked event without penalty -> we move on
        # to other events and retry these after they (and the IP) cool, instead of
        # re-grabbing the same batch or wrongly marking good events 'failed'.
        db.defer_events(conn, blocked, config.BLOCK_DEFER_SECONDS)
        conn.commit()

        # Recover the session when a big share of the batch blocked (IP degrading),
        # not only at 100% -- a mid-batch flag (e.g. 3 ok, 17 blocked) counts too.
        blocked_ratio = len(blocked) / len(event_ids) if event_ids else 0
        if blocked and blocked_ratio >= config.BLOCK_SESSION_RATIO:
            block_cycles += 1
            if block_cycles == 1:
                _LOG.info("%d/%d blocked (%.0f%%); refreshing session (same IP)...",
                          len(blocked), len(event_ids), 100 * blocked_ratio)
                await session.refresh()
            elif block_cycles == 2:
                _LOG.info("still blocked; rotating to a fresh exit IP...")
                await session.rotate()
            else:
                _LOG.warning("blocked %d cycles in a row; cooling down %ds to let the "
                             "exit IPs recover, then rotating (more proxies would help "
                             "at this scale)...", block_cycles, config.WORKER_BLOCK_COOLDOWN)
                await asyncio.sleep(config.WORKER_BLOCK_COOLDOWN)
                await session.rotate()
        else:
            block_cycles = 0
            if blocked:
                _LOG.info("%d event(s) blocked & deferred (kept scraping %d)",
                          len(blocked), len(results))
