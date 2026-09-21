#!/usr/bin/env python3
"""One-time backfill of seats.rank from cached manifests (manifest seatRanking).

Adds the column if missing, then for each cached manifest updates the rank of the
seats that already exist in the DB (available seats). Run from tm_scraper/.
"""
import glob
import json
import os

import psycopg2
from psycopg2.extras import execute_values

import config
import parse


def main():
    conn = psycopg2.connect(config.dsn())
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute('ALTER TABLE "TM"."seats" ADD COLUMN IF NOT EXISTS "rank" int')
    conn.commit()

    files = sorted(glob.glob(os.path.join(config.MANIFEST_CACHE_DIR, "*.json")))
    events = updated = 0
    for path in files:
        event_id = os.path.basename(path)[:-5]
        try:
            manifest = json.load(open(path))
            rank_by_place = parse.decode_seat_ranks(manifest)
            if not rank_by_place:
                continue
            with conn.cursor() as cur:
                cur.execute('SELECT place_id FROM "TM"."seats" WHERE event_id = %s',
                            (event_id,))
                pairs = [(event_id, pid, rank_by_place[pid])
                         for (pid,) in cur.fetchall() if pid in rank_by_place]
                if not pairs:
                    continue
                execute_values(
                    cur,
                    'UPDATE "TM"."seats" s SET "rank" = v.rank '
                    'FROM (VALUES %s) AS v(event_id, place_id, rank) '
                    'WHERE s.event_id = v.event_id AND s.place_id = v.place_id',
                    pairs, template="(%s,%s,%s::int)", page_size=5000,
                )
                updated += cur.rowcount
            conn.commit()
            events += 1
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            print(f"  skip {event_id}: {str(e).splitlines()[0]}")

    print(f"done: {events} events, {updated} seats got a rank")
    conn.close()


if __name__ == "__main__":
    main()
