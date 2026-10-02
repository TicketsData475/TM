"""Discovery job: find current events, upsert entities, enqueue them for detail.

Fast (~1-2 min). Run on a schedule. Detail scraping is done separately by
worker.py, which claims events from the TM.detail_queue this fills.
"""
import time
from datetime import date, timedelta

import config
import db
import log
import parse

_LOG = log.get("discover")


def _month_windows(start_iso, end_iso):
    """Split [start, end] into calendar-month windows so each stays under TM's
    ~980 deep-pagination cap. Returns [(start_iso, end_iso), ...]."""
    start, end = date.fromisoformat(start_iso), date.fromisoformat(end_iso)
    windows, cur = [], start
    while cur <= end:
        first_of_next = (date(cur.year + 1, 1, 1) if cur.month == 12
                         else date(cur.year, cur.month + 1, 1))
        win_end = min(first_of_next - timedelta(days=1), end)
        windows.append((cur.isoformat(), win_end.isoformat()))
        cur = first_of_next
    return windows


async def run_discovery(conn, session, run_ts):
    """Discover events -> upsert venues/events/artists -> enqueue events for detail.
    Returns the list of event ids enqueued."""
    windows = _month_windows(config.START_DATE, config.END_DATE)
    _LOG.info("discovering events (queries=%s, %s..%s, %d month-windows each)...",
              ",".join(config.QUERIES), config.START_DATE, config.END_DATE, len(windows))
    t = time.monotonic()
    by_id = {}
    for q in config.QUERIES:              # each query x month-window stays under the ~980 cap
        before = len(by_id)
        for (win_start, win_end) in windows:
            found = await session.discover_events(query=q, start=win_start, end=win_end)
            for ev in found:
                by_id[ev["id"]] = ev      # dedupe across windows/queries
        _LOG.info("[%s] found %d events (%d unique so far)", q, len(by_id) - before, len(by_id))
    events = list(by_id.values())
    _LOG.info("discovery done: %d unique events across %d queries in %s",
              len(events), len(config.QUERIES), log.fmt_secs(time.monotonic() - t))

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
        event["customer"] = config.CUSTOMER          # which client this event is for
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
