"""Orchestration: bootstrap -> discover -> fan-out details -> bulk upsert -> sweep."""
import asyncio
import time
from datetime import datetime, timezone

import config
import db
import log
import parse
from browser_session import TMSession
from event_details import fetch_batch, RateLimiter, Rotator

_LOG = log.get("pipeline")


def _chunk(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


async def run():
    t_start = time.monotonic()
    run_ts = datetime.now(timezone.utc)
    _LOG.info("=" * 68)
    _LOG.info("TM scrape starting @ %s", run_ts.isoformat())
    _LOG.info("query=%r region=%s window=%s..%s seat_mode=%s concurrency=%d",
              config.QUERY, config.REGION, config.START_DATE, config.END_DATE,
              config.SEAT_MODE, config.CONCURRENCY)
    _LOG.info("=" * 68)

    _LOG.info("connecting to Postgres (schema=%s)...", config.DB_SCHEMA)
    conn = db.connect()
    _LOG.info("DB connected.")

    # ---- 1. browser bootstrap + discovery ----
    session = TMSession(headless=config.HEADLESS)
    await session.start()
    _LOG.info("STAGE 1/4  discovering events...")
    t_disc = time.monotonic()
    events = await session.discover_events()
    _LOG.info("discovery done: %d events in %s",
              len(events), log.fmt_secs(time.monotonic() - t_disc))
    c_tmpt = await session.ensure_tmpt(events[0]["url"] if events else None)
    cookie_header = await session.get_cookie_header()
    _LOG.info("tmpt token: %s | forwarding %d browser cookies",
              "captured" if c_tmpt else "MISSING (facets may fail)",
              cookie_header.count("=") if cookie_header else 0)

    # the browser owns the shared proxy + token; the fetcher rotates via it
    rotator = Rotator(session)

    # ---- 2. upsert event/venue/artist entities ----
    _LOG.info("STAGE 2/4  upserting events / venues / artists...")
    venues, event_rows, artists, event_artists = {}, [], {}, []
    skipped = 0
    partner_skipped = 0
    for ev in events:
        # only keep Ticketmaster-listed events; partner events aren't our inventory
        if ev.get("isPartner"):
            partner_skipped += 1
            continue
        venue, event, ev_artists, ea = parse.extract_event_entities(ev)
        if event["venue_id"] is None:
            skipped += 1
            _LOG.warning("skipping event %s (%s): no venue code",
                         event["id"], event.get("title"))
            continue
        venues[venue["id"]] = venue
        event_rows.append(event)
        for a in ev_artists:
            artists[a["name"]] = a
        event_artists.extend(ea)
    if partner_skipped:
        _LOG.info("skipped %d partner events (isPartner=true)", partner_skipped)
    if skipped:
        _LOG.warning("skipped %d events without a venue code", skipped)

    n_v = db.upsert(conn, "venues", list(venues.values()), run_ts)
    n_e = db.upsert(conn, "events", event_rows, run_ts)
    n_a = db.upsert(conn, "artists", list(artists.values()), run_ts)
    n_ea = db.upsert(conn, "event_artists", event_artists, run_ts)
    conn.commit()
    _LOG.info("entities upserted: venues=%d events=%d artists=%d event_artists=%d",
              n_v, n_e, n_a, n_ea)

    # ---- 3. per-event seating detail, batched + concurrent ----
    event_ids = [e["id"] for e in event_rows]
    if config.MAX_EVENTS:
        event_ids = event_ids[:config.MAX_EVENTS]
        _LOG.info("MAX_EVENTS=%d -> limiting detail stage to %d events",
                  config.MAX_EVENTS, len(event_ids))
    total = len(event_ids)
    batch_size = config.CONCURRENCY
    _LOG.info("STAGE 3/4  fetching seating detail for %d events "
              "(batch=%d, %d concurrent, <=%.0f req/s)...",
              total, batch_size, config.CONCURRENCY, config.REQ_PER_SEC)

    limiter = RateLimiter(config.REQ_PER_SEC)
    done = errors_total = 0
    tally = {"sections": 0, "section_rows": 0, "seats": 0, "offers": 0, "seat_offers": 0}
    t_detail = time.monotonic()

    for batch_no, chunk in enumerate(_chunk(event_ids, batch_size), 1):
        _LOG.info("batch %d/%d: fetching %d events...",
                  batch_no, (total + batch_size - 1) // batch_size, len(chunk))
        t_f = time.monotonic()
        # proxy + token rotation (on 403) is handled inside fetch_one
        results, errors = await fetch_batch(chunk, rotator, limiter=limiter)
        errors_total += errors
        t_fetch = time.monotonic() - t_f

        t_u = time.monotonic()
        for event_id, facets, manifest in results:
            rows = parse.build_seating_rows(event_id, facets, manifest, config.SEAT_MODE)
            for table in ("sections", "section_rows", "seats", "offers", "seat_offers"):
                tally[table] += db.upsert(conn, table, rows[table], run_ts)
            conn.commit()
            done += 1
            _LOG.info("  [%d/%d] %s: seats=%d offers=%d (sections=%d)",
                      done, total, event_id[:8],
                      len(rows["seats"]), len(rows["offers"]), len(rows["sections"]))

        elapsed = time.monotonic() - t_detail
        rate = done / elapsed if elapsed else 0
        eta = (total - done) / rate if rate else 0
        _LOG.info("batch %d done: fetch %.1fs, db %.1fs | %d/%d (%.0f%%) | "
                  "%.2f ev/s | ETA %s | %d err",
                  batch_no, t_fetch, time.monotonic() - t_u, done, total,
                  100 * done / total, rate, log.fmt_secs(eta), errors_total)

    _LOG.info("detail stage done in %s: %d ok, %d errors | "
              "sections=%d seats=%d offers=%d seat_offers=%d",
              log.fmt_secs(time.monotonic() - t_detail), done, errors_total,
              tally["sections"], tally["seats"], tally["offers"], tally["seat_offers"])

    await session.close()   # releases the shared proxy

    # ---- 4. freshness sweep: seats no longer for sale -> unavailable ----
    _LOG.info("STAGE 4/4  sweeping stale availability...")
    swept = db.sweep_unavailable(conn, event_ids, run_ts)
    conn.commit()
    _LOG.info("sweep: marked %d seat_offers unavailable", swept)

    conn.close()
    _LOG.info("=" * 68)
    _LOG.info("DONE in %s | events=%d seats=%d offers=%d errors=%d",
              log.fmt_secs(time.monotonic() - t_start), total,
              tally["seats"], tally["offers"], errors_total)
    _LOG.info("=" * 68)


if __name__ == "__main__":
    asyncio.run(run())
