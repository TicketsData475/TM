"""Discovery job: find current events, upsert entities, enqueue them for detail.

Fast (~1-2 min). Run on a schedule. Detail scraping is done separately by
worker.py, which claims events from the TM.detail_queue this fills.
"""
import time

import config
import db
import log
import parse

_LOG = log.get("discover")


async def run_discovery(conn, session, run_ts):
    """Discover events -> upsert venues/events/artists -> enqueue events for detail.
    Returns the list of event ids enqueued."""
    _LOG.info("discovering events (query=%r, %s..%s)...",
              config.QUERY, config.START_DATE, config.END_DATE)
    t = time.monotonic()
    events = await session.discover_events()
    _LOG.info("discovery done: %d events in %s", len(events), log.fmt_secs(time.monotonic() - t))

    venues, event_rows, artists, event_artists = {}, [], {}, []
    partner_skipped = promo_skipped = skipped = 0
    for ev in events:
        if ev.get("isPartner"):                       # partner events aren't our inventory
            partner_skipped += 1
            continue
        title = (ev.get("title") or "").upper()       # promo/parking have no seat map
        if any(k in title for k in config.SKIP_TITLE_KEYWORDS):
            promo_skipped += 1
            continue
        venue, event, ev_artists, ea = parse.extract_event_entities(ev)
        if event["venue_id"] is None:
            skipped += 1
            _LOG.warning("skipping event %s (%s): no venue code", event["id"], event.get("title"))
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

    detail_ids = [e["id"] for e in event_rows]
    if config.MAX_EVENTS:
        detail_ids = detail_ids[:config.MAX_EVENTS]
        _LOG.info("MAX_EVENTS=%d -> enqueuing only %d events", config.MAX_EVENTS, len(detail_ids))
    db.enqueue_events(conn, detail_ids, run_ts)
    conn.commit()

    _LOG.info("upserted venues=%d events=%d artists=%d event_artists=%d | enqueued %d for detail",
              n_v, n_e, n_a, n_ea, len(detail_ids))
    return detail_ids
