# Future improvements / backlog

Ideas we deliberately deferred. Each has enough detail to pick up later.

---

## 1. Full `rows` table (all rows, not just available)

**Why:** The scraper runs in `available` mode, so the `seats` table only holds rows
that currently have seats for sale. The quote tool needs the venue's **complete**
section+row layout so a seller can pick any row (even sold-out ones).
`get_section_rows()` currently returns available rows only.

**Fix:** Add a small `rows` table populated from the manifest's full `manifestRows`
(every row of every reserved section — ~7k rows/event vs ~65k seats, so cheap):

```
Table rows {
    event_id     varchar
    section_name varchar
    row_name     varchar
    (PK: event_id, section_name, row_name)
}
```
Populate during the scraper's parse step (we already decode `manifestRows`).
Then point `get_section_rows()` at `rows` instead of `seats`.

**Effort:** small.

---

## 2. Artist / performer search

**Why:** Right now search is event-name only. Users may search by team/artist
("Taylor Swift", "Raiders") which isn't always in the event title.

**Fix:** Denormalize a `search_text` column on `events` = title + venue + city +
aggregated artist names (from `event_artists`), maintained by the scraper at
upsert time. Add a trigram GIN index (fuzzy) and optionally a generated tsvector
column (full-text relevance). Extend `search_events()` to match on `search_text`.

**Effort:** small–medium.

---

## 3. GA / parking availability

**Why:** GA events (parking, general admission) have no individual seats, so we
store their `sections` + `offers` but not "how many available at what price."
That info lives in the facet's `count` / `placeGroups`, which the parser skips.

**Fix:** For facets with empty `places` but a `count`, store a section-level
availability row (section + offer + count) — either a new `section_offers` table
or reuse `seat_offers` with a null `place_id` + a `count` column.

**Effort:** small.

---

## 4. Faster remote DB writes (COPY + upsert)

**Why:** Full runs write ~1.5M `seats` + ~1.5M `seat_offers` to a remote DB via
`INSERT ... ON CONFLICT`, which is the run's bottleneck (many network round-trips).

**Fix:** For the big tables, `COPY` rows into a temp table (one bulk stream), then
`INSERT ... SELECT ... ON CONFLICT DO UPDATE` from it. Typically 5–10× faster.

**Effort:** medium (only worth it if the `db Ys` log line dominates).

---

## 5. Price history (dynamic pricing over time)

**Why:** Prices change constantly. Current schema keeps only the latest price.

**Fix:** Add `offer_price_snapshots(event_id, offer_id, list_price, total_price,
available_count, captured_at)` — one row per offer per run. Efficient (per-offer,
not per-seat) and enables price trends.

**Effort:** small.
