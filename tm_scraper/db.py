"""Postgres bulk upsert into the "TM" schema.

Semantics per row on every scrape:
  - created_at   : set once on first insert, never overwritten.
  - last_seen_at : bumped to run_ts on every upsert (row was present this run).
  - updated_at   : bumped to run_ts ONLY when a business column actually changed.
"""
import io

import psycopg2
from psycopg2.extras import execute_values

from config import dsn, DB_SCHEMA

# Big per-event tables use COPY-into-staging + INSERT...SELECT...ON CONFLICT
# (much faster than row-by-row execute_values over a remote connection).
COPY_TABLES = {"seats", "seat_offers", "section_rows"}

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
        ["section_name", "row_name", "seat_number", "rank"],
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


def encode_for_copy(value):
    r"""Encode one value for text-format COPY: NULL=\N, booleans t/f, escapes."""
    if value is None:
        return r"\N"
    if value is True:
        return "t"
    if value is False:
        return "f"
    return (str(value).replace("\\", "\\\\").replace("\t", "\\t")
            .replace("\n", "\\n").replace("\r", "\\r"))


def bulk_upsert_via_copy(conn, table, rows, run_ts):
    """COPY rows into a temp staging table, then one INSERT...SELECT...ON CONFLICT.
    Same last_seen_at / updated_at semantics as the execute_values path."""
    primary_key_cols, business_cols, has_updated_at, created_col = TABLES[table]
    target_table = f"{_q(DB_SCHEMA)}.{_q(table)}"
    staging_table = f"_stg_{table}"
    streamed_cols = primary_key_cols + business_cols        # columns we COPY in
    streamed_col_sql = ", ".join(_q(c) for c in streamed_cols)
    conflict_col_sql = ", ".join(_q(c) for c in primary_key_cols)

    # stream the rows as a tab-delimited text buffer
    copy_buffer = io.StringIO()
    for row in rows:
        copy_buffer.write("\t".join(encode_for_copy(row.get(c)) for c in streamed_cols))
        copy_buffer.write("\n")
    copy_buffer.seek(0)

    insert_cols = primary_key_cols + business_cols + [created_col, "last_seen_at"]
    if has_updated_at:
        insert_cols.append("updated_at")
    insert_col_sql = ", ".join(_q(c) for c in insert_cols)
    # de-dupe within the batch with DISTINCT ON (pk); timestamps come from run_ts
    select_col_sql = ", ".join(
        [f"stg.{_q(c)}" for c in streamed_cols]
        + ["%(run_ts)s", "%(run_ts)s"] + (["%(run_ts)s"] if has_updated_at else [])
    )

    set_clauses = [f"{_q(c)} = EXCLUDED.{_q(c)}" for c in business_cols]
    set_clauses.append(f'{_q("last_seen_at")} = EXCLUDED.{_q("last_seen_at")}')
    if has_updated_at and business_cols:
        existing_vals = ", ".join(f"{target_table}.{_q(c)}" for c in business_cols)
        incoming_vals = ", ".join(f"EXCLUDED.{_q(c)}" for c in business_cols)
        set_clauses.append(
            f'{_q("updated_at")} = CASE WHEN ({existing_vals}) IS DISTINCT FROM ({incoming_vals}) '
            f'THEN EXCLUDED.{_q("updated_at")} ELSE {target_table}.{_q("updated_at")} END'
        )

    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS "{staging_table}"')
        cur.execute(f'CREATE TEMP TABLE "{staging_table}" ON COMMIT DROP AS '
                    f"SELECT {streamed_col_sql} FROM {target_table} WITH NO DATA")
        cur.copy_expert(f'COPY "{staging_table}" ({streamed_col_sql}) FROM STDIN', copy_buffer)
        cur.execute(
            f"INSERT INTO {target_table} ({insert_col_sql}) "
            f"SELECT {select_col_sql} FROM "
            f"(SELECT DISTINCT ON ({conflict_col_sql}) {streamed_col_sql} "
            f'FROM "{staging_table}" ORDER BY {conflict_col_sql}) stg '
            f"ON CONFLICT ({conflict_col_sql}) DO UPDATE SET {', '.join(set_clauses)}",
            {"run_ts": run_ts},
        )
    return len(rows)


def upsert(conn, table, rows, run_ts):
    """Bulk upsert row-dicts into a TM table. Dedupes on PK within the batch."""
    if not rows:
        return 0
    if table in COPY_TABLES:
        return bulk_upsert_via_copy(conn, table, rows, run_ts)
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
