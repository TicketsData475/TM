"""Postgres bulk upsert into the "TM" schema.

Semantics per row on every scrape:
  - created_at   : set once on first insert, never overwritten.
  - last_seen_at : bumped to run_ts on every upsert (row was present this run).
  - updated_at   : bumped to run_ts ONLY when a business column actually changed.
"""
import psycopg2
from psycopg2.extras import execute_values

from config import dsn, DB_SCHEMA

# table -> (pk columns, business columns, has_updated_at, created_col)
#   created_col = the "set once, never overwrite" timestamp column for this table
#   (most use 'created_at'; seat_offers uses 'captured_at' and has no updated_at).
TABLES = {
    "venues": (
        ["id"],
        ["name", "city", "state", "country", "country_code", "postal_code",
         "address_line_one", "latitude", "longitude", "url", "image_url"],
        True, "created_at",
    ),
    "events": (
        ["id"],
        ["discovery_id", "title", "venue_id", "start_date", "end_date", "onsale_date",
         "date_display", "is_span_multiple_days", "url", "upsell_landing_page_url",
         "seatmap_url", "is_partner_event", "is_partner", "is_tm_button_shown",
         "timezone", "is_cancelled", "is_postponed", "is_rescheduled", "is_tba",
         "is_local", "is_same_region", "is_sold_out", "is_limited_availability",
         "is_virtual", "event_change_status"],
        True, "created_at",
    ),
    "artists": (
        ["name"],
        ["url", "image_url", "image_url_retina"],
        True, "created_at",
    ),
    "event_artists": (
        ["event_id", "artist_id"],
        [],
        False, "created_at",
    ),
    "sections": (
        ["event_id", "name"],
        ["seating_type", "num_seats"],
        True, "created_at",
    ),
    "seats": (
        ["event_id", "place_id"],
        ["section_name", "row_name", "seat_number"],
        True, "created_at",
    ),
    "section_rows": (
        ["event_id", "section_name", "row_name"],
        ["position"],
        True, "created_at",
    ),
    "offers": (
        ["event_id", "offer_id"],
        ["inventory_type", "offer_type", "name", "description", "price_level_id",
         "price_level_name", "face_value", "list_price", "total_price",
         "no_charges_price", "sellable_quantities", "rank", "is_online",
         "ticket_type_id", "listing_id", "listing_version_id", "seller_notes"],
        True, "created_at",
    ),
    "seat_offers": (
        ["event_id", "place_id"],
        ["offer_id", "is_available"],
        False, "captured_at",
    ),
}


def connect():
    conn = psycopg2.connect(dsn())
    conn.autocommit = False
    return conn


def _q(ident):
    return '"' + ident.replace('"', '""') + '"'


def build_upsert_sql(table):
    pk, business, has_updated, created_col = TABLES[table]
    tbl = f'{_q(DB_SCHEMA)}.{_q(table)}'

    insert_cols = pk + business + [created_col, "last_seen_at"]
    if has_updated:
        insert_cols.append("updated_at")
    col_sql = ", ".join(_q(c) for c in insert_cols)

    set_parts = [f'{_q(c)} = EXCLUDED.{_q(c)}' for c in business]
    set_parts.append(f'{_q("last_seen_at")} = EXCLUDED.{_q("last_seen_at")}')
    if has_updated and business:
        old = ", ".join(f'{tbl}.{_q(c)}' for c in business)
        new = ", ".join(f'EXCLUDED.{_q(c)}' for c in business)
        set_parts.append(
            f'{_q("updated_at")} = CASE WHEN ({old}) IS DISTINCT FROM ({new}) '
            f'THEN EXCLUDED.{_q("updated_at")} ELSE {tbl}.{_q("updated_at")} END'
        )
    conflict = ", ".join(_q(c) for c in pk)
    return (
        f'INSERT INTO {tbl} ({col_sql}) VALUES %s '
        f'ON CONFLICT ({conflict}) DO UPDATE SET {", ".join(set_parts)}'
    ), insert_cols, business


def upsert(conn, table, rows, run_ts):
    """Bulk upsert row-dicts into a TM table. Dedupes on PK within the batch."""
    if not rows:
        return 0
    pk, business, has_updated, created_col = TABLES[table]
    sql, insert_cols, _ = build_upsert_sql(table)

    # de-duplicate on PK so ON CONFLICT never hits the same row twice per statement
    deduped = {}
    for r in rows:
        deduped[tuple(r[c] for c in pk)] = r
    rows = list(deduped.values())

    ts_cols = {created_col: run_ts, "last_seen_at": run_ts, "updated_at": run_ts}
    values = [
        tuple(ts_cols[c] if c in ts_cols else row.get(c) for c in insert_cols)
        for row in rows
    ]
    with conn.cursor() as cur:
        execute_values(cur, sql, values, page_size=5000)
    return len(rows)


def sweep_unavailable(conn, event_ids, run_ts):
    """Any seat that was for sale but did NOT appear this run -> mark unavailable."""
    if not event_ids:
        return 0
    tbl = f'{_q(DB_SCHEMA)}.{_q("seat_offers")}'
    with conn.cursor() as cur:
        cur.execute(
            f'UPDATE {tbl} SET {_q("is_available")} = false '
            f'WHERE {_q("event_id")} = ANY(%s) '
            f'AND {_q("last_seen_at")} < %s AND {_q("is_available")} = true',
            (list(event_ids), run_ts),
        )
        return cur.rowcount
