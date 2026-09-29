"""Detail worker: claim events from TM.detail_queue, scrape seats/sections/offers,
mark them done. ONE instance = one browser + one proxy (one Kasada identity).

Scale horizontally by running more instances (processes/machines): they share the
queue via FOR UPDATE SKIP LOCKED, so they never collide and self-balance. A crash
is safe -- the lease expires and another worker re-claims the event.
"""
import asyncio
import time
from datetime import datetime, timezone

import config
import db
import log
import parse
from event_details import fetch_batch, RateLimiter

_LOG = log.get("worker")


async def run_worker(conn, session):
    limiter = RateLimiter(config.REQ_PER_SEC)
    worker_id = config.WORKER_ID
    _LOG.info("worker %s starting (batch=%d, lease=%dmin, max_attempts=%d, idle_sleep=%ds, "
              "concurrency=%d)", worker_id, config.CLAIM_BATCH, config.LEASE_MINUTES,
              config.MAX_ATTEMPTS, config.WORKER_IDLE_SLEEP, config.CONCURRENCY)

    done = errored_total = 0
    t0 = time.monotonic()
    block_streak = 0

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
            if config.WORKER_IDLE_SLEEP > 0:
                _LOG.info("queue empty %s; sleeping %ds...", counts, config.WORKER_IDLE_SLEEP)
                await asyncio.sleep(config.WORKER_IDLE_SLEEP)
                continue
            _LOG.info("queue drained %s; worker done (%d scraped, %d errored)",
                      counts, done, errored_total)
            break

        _LOG.info("claimed %d events", len(event_ids))
        results, blocked, errored = await fetch_batch(
            event_ids, session, config.CONCURRENCY, limiter, on_result)

        for eid, fatal in errored:
            db.fail_event(conn, eid, config.MAX_ATTEMPTS, fatal=fatal)  # 404 -> fail now
            errored_total += 1

        # 403 handling: if the WHOLE batch blocked, it's the session (nothing got
        # through) -> release without penalty and refresh/rotate. If only SOME
        # blocked while others succeeded, those are event-specific (restricted /
        # persistently-403 events) -> penalise so they fail out instead of looping.
        session_blocked = bool(blocked) and not results
        if session_blocked:
            for eid in blocked:
                db.release_event(conn, eid)
        else:
            for eid in blocked:
                db.fail_event(conn, eid, config.MAX_ATTEMPTS)
                errored_total += 1
        conn.commit()

        if session_blocked:
            block_streak += 1
            if block_streak == 1:
                _LOG.info("%d/%d blocked (session); refreshing (same IP)...",
                          len(blocked), len(event_ids))
                await session.refresh()
            else:
                _LOG.info("%d/%d blocked again; rotating proxy IP...", len(blocked), len(event_ids))
                await session.rotate()
                block_streak = 0
        else:
            block_streak = 0
            if blocked:
                _LOG.info("%d event(s) individually blocked (kept scraping %d); "
                          "penalised, not a session block", len(blocked), len(results))
