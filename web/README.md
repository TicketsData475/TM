# Event Search (Next.js)

Simple front end for the quote tool:
1. Search events (live autocomplete dropdown; Enter = full results list)
2. Click an event → **Section** dropdown (searchable)
3. Pick a section → **Row** dropdown enables (available rows only)

Talks to Postgres via the SQL functions in `../search_functions.sql`
(`TM.search_events`, `TM.get_event_sections`, `TM.get_section_rows`).

## Two customers

| Route | Customer | DB functions | Default price basis |
|-------|----------|--------------|---------------------|
| `/`       | **TC** (leagues: NFL/NBA/NHL) | `TM.search_events`, `get_event_sections`, `get_section_rows`, `quote` | resale |
| `/aceify` | **aceify** (tennis / Australian Open) | `TM.aceify_search_events`, `aceify_get_event_sections`, `aceify_get_section_rows`, `aceify_quote` | **primary** |

`search_events` is filtered by the `events.customer` column, so each page only
ever shows its own customer's events. The two paths are fully independent (separate
pages + separate `/api/aceify/*` routes); changing one never affects the other.

The **aceify** page (`/aceify`) adds a collapsible **Pricing controls** panel so the
customer can override the algorithm — each knob has a plain-language description of
what it does and how it moves the quote:

| Control | What it does |
|---------|--------------|
| Price basis | primary (box-office / face value — default for tennis) · resale · all |
| Percentile  | where in the comparable-seat price range we read the market price (0% = cheapest) |
| Rank window | how far to reach for "comparable" seats, by venue-wide seat-quality rank |
| Max sections| the most nearby sections to pool comparable seats from |
| Your margin | aceify's buyback profit cut; offer = market price − margin (default 30%) |

## Prereqs
- Node 18+
- The DB functions installed (run `../search_functions.sql` once) and
  `pg_trgm` enabled with a lowered threshold (see main project docs).

## Setup & run
```bash
cd web
npm install
# .env.local already has the DB connection (PGHOST/PGUSER/...)
npm run dev          # http://localhost:3000
```

Production:
```bash
npm run build && npm start
```

## How it's wired
```
app/page.js            UI (client): search box, autocomplete, results, section/row selects
app/api/search/route.js   GET /api/search?q=...      -> TM.search_events()
app/api/sections/route.js GET /api/sections?eventId= -> TM.get_event_sections()
app/api/rows/route.js     GET /api/rows?eventId=&section= -> TM.get_section_rows()
lib/db.js              node-postgres pool (reads PG* env vars from .env.local)
```

## Notes
- DB credentials live in `.env.local` (server-side only, never sent to the browser).
- `get_section_rows` returns **available** rows only (see `../future_improvements.md` #1
  for the full-rows enhancement).
- Fuzzy search relies on `pg_trgm`; the match looseness is the DB-level
  `word_similarity_threshold` (set to 0.2 on the `tickets_local` database).
