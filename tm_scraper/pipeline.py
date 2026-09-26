"""Orchestration: bootstrap -> discover -> fan-out details -> bulk upsert -> sweep."""
import asyncio
import time
from datetime import datetime, timezone

import config
import db
import log
import parse
from browser_session import TMSession, ProxyBlocked

_LOG = log.get("pipeline")


async def run():
    t_start = time.monotonic()
    run_ts = datetime.now(timezone.utc)
    _LOG.info("=" * 68)
    _LOG.info("TM scrape starting @ %s", run_ts.isoformat())
    _LOG.info("query=%r region=%s window=%s..%s seat_mode=%s",
              config.QUERY, config.REGION, config.START_DATE, config.END_DATE,
              config.SEAT_MODE)
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

    # ---- 2. upsert event/venue/artist entities ----
    _LOG.info("STAGE 2/4  upserting events / venues / artists...")
    venues, event_rows, artists, event_artists = {}, [], {}, []
    skipped = 0
    partner_skipped = 0
    promo_skipped = 0
    for ev in events:
        # only keep Ticketmaster-listed events; partner events aren't our inventory
        if ev.get("isPartner"):
            partner_skipped += 1
            continue
        # promo/parking listings have no seat map -> no facets; skip them
        title = (ev.get("title") or "").upper()
        if any(k in title for k in config.SKIP_TITLE_KEYWORDS):
            promo_skipped += 1
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
    if promo_skipped:
        _LOG.info("skipped %d promo/parking events (no seat map)", promo_skipped)
    if skipped:
        _LOG.warning("skipped %d events without a venue code", skipped)

    n_v = db.upsert(conn, "venues", list(venues.values()), run_ts)
    n_e = db.upsert(conn, "events", event_rows, run_ts)
    n_a = db.upsert(conn, "artists", list(artists.values()), run_ts)
    n_ea = db.upsert(conn, "event_artists", event_artists, run_ts)
    conn.commit()
    _LOG.info("entities upserted: venues=%d events=%d artists=%d event_artists=%d",
              n_v, n_e, n_a, n_ea)

    # ---- 3. per-event seating detail, through the trusted browser ----
    # curl can't fetch facets/manifest anymore (Kasada signs each request), so we
    # load each event's seat map in the browser and read the app's own responses.
    # One browser = one Kasada identity = sequential; on a persistent block we
    # rotate to a fresh exit IP and retry the same event.
    detail_events = [(e["id"], e["url"]) for e in event_rows]
    if config.MAX_EVENTS:
        detail_events = detail_events[:config.MAX_EVENTS]
        _LOG.info("MAX_EVENTS=%d -> limiting detail stage to %d events",
                  config.MAX_EVENTS, len(detail_events))
    total = len(detail_events)
    max_rotations = config.PROXY_MAX_ROTATIONS
    _LOG.info("STAGE 3/4  fetching seating detail for %d events "
              "(browser, sequential, up to %d IP rotations/event)...",
              total, max_rotations)

    done = errors_total = 0
    tally = {"sections": 0, "section_rows": 0, "seats": 0, "offers": 0, "seat_offers": 0}
    t_detail = time.monotonic()

    for event_id, event_url in detail_events:
        facets_doc = manifest = None
        for attempt in range(max_rotations + 1):
            try:
                facets_doc, manifest = await session.fetch_event(event_url, event_id)
                break
            except ProxyBlocked as e:
                if attempt < max_rotations:
                    _LOG.info("  %s blocked (%s); rotating exit IP and retrying",
                              event_id[:8], e)
                    await session.rotate()
                    continue
                _LOG.warning("  %s giving up after %d rotations: %s",
                             event_id[:8], max_rotations, e)
            except Exception as e:  # noqa: BLE001 - keep the run going
                _LOG.warning("  %s failed: %s", event_id[:8], repr(e)[:120])
                break

        if facets_doc is None:
            errors_total += 1
            continue

        rows = parse.build_seating_rows(event_id, facets_doc, manifest, config.SEAT_MODE)
        for table in ("sections", "section_rows", "seats", "offers", "seat_offers"):
            tally[table] += db.upsert(conn, table, rows[table], run_ts)
        conn.commit()
        done += 1

        elapsed = time.monotonic() - t_detail
        rate = done / elapsed if elapsed else 0
        eta = (total - done) / rate if rate else 0
        _LOG.info("  [%d/%d] %s: seats=%d offers=%d (sections=%d) | "
                  "%.3f ev/s | ETA %s | %d err",
                  done, total, event_id[:8], len(rows["seats"]),
                  len(rows["offers"]), len(rows["sections"]), rate,
                  log.fmt_secs(eta), errors_total)

    _LOG.info("detail stage done in %s: %d ok, %d errors | "
              "sections=%d seats=%d offers=%d seat_offers=%d",
              log.fmt_secs(time.monotonic() - t_detail), done, errors_total,
              tally["sections"], tally["seats"], tally["offers"], tally["seat_offers"])

    await session.close()   # releases the shared proxy

    # ---- 4. freshness sweep: seats no longer for sale -> unavailable ----
    _LOG.info("STAGE 4/4  sweeping stale availability...")
    swept = db.sweep_unavailable(conn, [eid for eid, _ in detail_events], run_ts)
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
