#!/usr/bin/env python3
"""One-time backfill of the section_rows table from cached manifests.

Creates the table if missing, then for every cached manifest writes the full
front->back row layout (position per row) for each reserved section.
Events whose sections aren't in the DB yet are skipped (FK).

Run from tm_scraper/:  python backfill_rows.py
"""
import glob
import json
import os
from datetime import datetime, timezone

import config
import db
import parse

DDL = f'''
CREATE TABLE IF NOT EXISTS "{config.DB_SCHEMA}"."section_rows" (
  "event_id" varchar NOT NULL,
  "section_name" varchar NOT NULL,
  "row_name" varchar NOT NULL,
  "position" int,
  "created_at" timestamp,
  "updated_at" timestamp,
  "last_seen_at" timestamp,
  PRIMARY KEY ("event_id", "section_name", "row_name"),
  FOREIGN KEY ("event_id", "section_name")
      REFERENCES "{config.DB_SCHEMA}"."sections" ("event_id", "name")
);
'''


def main():
    run_ts = datetime.now(timezone.utc)
    conn = db.connect()
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()

    files = sorted(glob.glob(os.path.join(config.MANIFEST_CACHE_DIR, "*.json")))
    ok = skipped = total_rows = 0
    for path in files:
        event_id = os.path.basename(path)[:-5]
        try:
            manifest = json.load(open(path))
            _, _, rows_info = parse.decode_manifest(manifest)
            rows = [
                {"event_id": event_id, "section_name": r["section_name"],
                 "row_name": r["row_name"], "position": r["position"]}
                for r in rows_info
            ]
            if not rows:
                continue
            n = db.upsert(conn, "section_rows", rows, run_ts)
            conn.commit()
            ok += 1
            total_rows += n
        except Exception as e:  # noqa: BLE001 - FK miss / bad file -> skip event
            conn.rollback()
            skipped += 1
            print(f"  skip {event_id}: {str(e).splitlines()[0]}")

    print(f"done: {ok} events, {total_rows} section_rows written, {skipped} skipped")
    conn.close()


if __name__ == "__main__":
    main()
