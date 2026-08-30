# Ticketmaster scraper → Postgres (`TM` schema)

Scrapes NFL (or any query) events from Ticketmaster and loads events, venues,
artists, and per-event **sections / seats / offers / availability** (primary +
resale) into the `TM` Postgres schema.

## How it works

```
Playwright (headless Chromium)          curl_cffi (async, impersonate=chrome)
─ solves Kasada once                    ─ facets   (services.ticketmaster.com)
─ drives the search API in-page  ─────► ─ manifest (pubapi.ticketmaster.com, cached)
  (page.evaluate fetch)                        │
─ exposes the `tmpt` cookie ──► c-tmpt header  ▼
                                        parse: decompress places · decode manifest · join
                                               │
                                               ▼
                                        psycopg2 bulk upsert → "TM" schema → freshness sweep
```

Why the split: the **search** endpoint is Kasada-protected, so it's called from
inside the browser (anti-bot headers attach automatically). **facets** only needs
the `c-tmpt` header (= the `tmpt` cookie), and **manifest** needs nothing — both
are fast to pull with curl_cffi in parallel.

## Setup

```bash
cd tm_scraper
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # one-time: download the browser

cp .env.example .env                 # then fill in your remote DB creds
psql "$DATABASE_URL" -f ../ticketmaster_schema.sql   # create the TM schema (once)
```

## Run

```bash
python main.py                       # full run from .env settings
python main.py --headed              # watch the browser (debug Kasada)
python main.py --query nfl --seat-mode full
python main.py --refresh-manifest    # ignore the on-disk manifest cache
```

Re-runnable: natural composite keys mean every run upserts. `last_seen_at` is
bumped on every touched row; `updated_at` moves only when a field actually
changed; seats that stop appearing are swept to `is_available = false`.

## Config (.env)

| var | meaning |
|---|---|
| `DATABASE_URL` or `PG*` | remote Postgres connection |
| `DB_SCHEMA` | default `TM` |
| `TM_QUERY` / `TM_REGION` | search query & region (default `nfl` / `200`) |
| `TM_START_DATE` / `TM_END_DATE` | date window (blank = today .. +180d) |
| `SEAT_MODE` | `available` (only seats for sale) or `full` (whole manifest) |
| `CONCURRENCY` | simultaneous event-detail fetches (default 12) |
| `HEADLESS` | run browser headless (default true) |
| `REFRESH_MANIFEST` | `true` = always refetch manifests |

## Files

| file | role |
|---|---|
| `config.py` | env + endpoints + constants |
| `browser_session.py` | Playwright bootstrap, discovery, tmpt refresh |
| `event_details.py` | async facets + manifest fetch (+ manifest cache) |
| `parse.py` | decompress places, decode manifest, join → row dicts |
| `db.py` | table metadata + generic bulk upsert + sweep |
| `pipeline.py` | orchestration |
| `main.py` | CLI entrypoint |
| `manifests/` | on-disk manifest cache (`<eventId>.json`) |

## Notes / gotchas

- **If discovery returns HTTP 403**, Kasada wasn't satisfied — run `--headed`,
  and/or increase the `wait_for_timeout` in `browser_session.start()`. The
  session auto-refreshes `tmpt` if facets calls start failing mid-run.
- `apikey`/`apisecret` in `config.py` are the public web keys the site itself
  uses; the documented limit is ~5 req/s — keep `CONCURRENCY` modest.
- `SEAT_MODE=full` writes every physical seat (~65k/stadium) — millions of rows.
  `available` (default) writes only what's for sale.
