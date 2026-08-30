# Ticketmaster Scraper — Architecture & Flow (reference)

A working reference for how this scraper is built, why, and the gotchas we hit.

---

## 1. What it collects

For a search query (e.g. `nfl`) it loads into the Postgres `TM` schema:

- **events**, **venues**, **artists**, **event_artists** (from the search API)
- per event: **sections**, **seats**, **offers**, **seat_offers** (from facets + manifest)
- Both **primary** (Ticketmaster's own) and **resale** (fan-to-fan) inventory.

---

## 2. The three data sources

| # | Source | Host | Gives | Auth needed |
|---|--------|------|-------|-------------|
| 1 | **Search API** | `www.ticketmaster.com/api/search/events` | events + venue + artists | **Strong** (Kasada, per-request signature) |
| 2 | **Facets API** | `services.ticketmaster.com/api/ismds/event/{id}/facets` | offers, prices, availability (which seats are for sale) | `c-tmpt` token + cookies |
| 3 | **Manifest API** | `pubapi.ticketmaster.com/sdk/static/manifest/v1/{id}` | seat map (section/row/seat for every placeId) | **None** (public) |

**The join key is `placeId`.** Facets says "placeId X is available for $Y"; the manifest says "placeId X = section 319, row P, seat 10". We join them.

---

## 3. Why two different tools (browser + curl)

- The **Search API is Kasada-protected**: every request needs an `x-kpsdk-h` signature generated fresh by Ticketmaster's JavaScript. curl can't produce it → gets blocked. So we let the **browser** make those calls.
- **Facets + Manifest are not** signature-protected: they need only the `c-tmpt` token (and cookies help). So we use **curl_cffi** there — much faster, and the heavy part (hundreds of events).

| Data | Tool | Reason |
|---|---|---|
| Events (search) | **Playwright (browser)** | needs per-request anti-bot signature |
| Seat details (facets + manifest) | **curl_cffi** | no signature → fast, parallelizable |

**We do NOT intercept network traffic.** For search we run `fetch()` *inside* the page via `page.evaluate(...)` — i.e. we tell the browser "you make this call and hand me the JSON." The browser attaches the anti-bot headers automatically.

---

## 4. Full flow (step by step)

```
1. Playwright launches Chromium ONCE, loads a TM search page.
   → Kasada runs its check, sets cookies (KP_UIDz, tmpt, SID, ...).

2. DISCOVERY (in the browser): page.evaluate(fetch) the search API,
   page by page (20 events/page), until all pages collected.
   → ~360 events with venue + artist info.

3. Read from the SAME browser:
   - `tmpt` cookie  → becomes the `c-tmpt` header for facets
   - all ticketmaster.com cookies → forwarded to curl (look like real session)

4. Upsert entities:  venues → events → artists → event_artists.

5. DETAIL FETCH (curl_cffi, concurrent, rate-capped): for each event
   - facets   (needs c-tmpt + cookies)   } run concurrently per event
   - manifest (public, cached on disk)   }
   The browser stays open only to refresh `tmpt` if it expires.

6. Parse + upsert per event:
   decompress facet places → join to manifest → sections, seats, offers, seat_offers.

7. SWEEP: seats that were for sale before but not seen this run
   → mark seat_offers.is_available = false.
```

**Mental model:** the **browser is the key-maker** (solves anti-bot, hands us a token + cookies), **curl_cffi is the fast worker** (uses that token to pull seat/price data for hundreds of events). One browser, reused; never a second profile.

---

## 5. Concurrency model (asyncio, NOT threads)

We use **`asyncio` (async/await)** — one process, **one thread**, **shared memory**. curl_cffi's `AsyncSession` fires many requests using non-blocking I/O.

### Concurrency vs parallelism
- **Parallelism** = things happening at the same instant (multiple cores/threads/processes).
- **Concurrency** = one worker juggling many tasks, switching whenever one is *waiting*.

**We use concurrency.** The "12 workers" are **async tasks (coroutines) taking turns on one thread** — not 12 threads, not 12 programs.

### Waiter analogy
One waiter serving 12 tables: takes an order (sends a request), and while the kitchen cooks (network wait) moves to the next table. One waiter, 12 orders in progress. It works because we spend most time **waiting on the network**, not doing CPU work. (Parallelism = 12 separate waiters = threads; we don't do that.)

### In code
```python
# one coroutine per event; all share memory (same `results`, same limiter)
await asyncio.gather(*(worker(e) for e in event_ids))

# semaphore: at most CONCURRENCY tasks "in the kitchen" at once
sem = asyncio.Semaphore(CONCURRENCY)
async with sem:
    ... fetch facets + manifest ...
```

Why this fits: the work is **network-bound**, not CPU-bound. Real parallelism (threads/processes) only helps CPU-heavy work.

---

## 6. The knobs (tuning)

| Knob | Where | Default | Controls |
|---|---|---|---|
| `CONCURRENCY` | .env | 12 | how many events are **in flight at once** (semaphore) |
| `REQ_PER_SEC` | .env | 5 | global cap on **facets request start rate** (the real throttle) |
| `batch_size` | code (= CONCURRENCY) | 12 | events fetched, then upserted, per batch |
| DB `page_size` | db.py | 5000 | rows per `execute_values` round-trip |
| `SEAT_MODE` | .env | `available` | `available` = only seats for sale; `full` = whole manifest (~10× rows) |
| `REFRESH_MANIFEST` | .env | false | false = reuse on-disk manifest cache |
| `MAX_EVENTS` / `--limit` | CLI | none | cap events (debugging) |

**How CONCURRENCY and REQ_PER_SEC interact:** `CONCURRENCY` says how many can be in flight; `REQ_PER_SEC` says how fast new facets requests *start*. Even with 12 in flight, facets go out at ≤5/s. The rate limiter is what keeps us under TM's limit and avoids the 400/403 blocks.

- **Facets** → rate-limited (5/s).
- **Manifest** → concurrent but NOT rate-limited (public; usually from disk cache).

---

## 7. Performance picture

For a full ~353-event run (`available` mode, ~4,200 seats/event):

- **Fetch (facets):** 353 ÷ 5/s ≈ **~70s** minimum.
- **Manifests:** cached = instant; uncached download concurrently.
- **DB writes (remote):** the **bottleneck** — ~1.5M seats + ~1.5M seat_offers via `INSERT` to a remote DB.

Observed on the 30-event test: ~3 min, almost all in the **remote DB writes** (see the `fetch Xs / db Ys` log line). Fetch is fast; **the remote DB is the limit.**

### Biggest speed win (not yet implemented)
**`COPY` into a temp table + upsert** for `seats`/`seat_offers`:
1. `COPY` bulk-streams all rows into a temp table (few network round-trips).
2. One `INSERT ... SELECT ... ON CONFLICT DO UPDATE` moves them into the real table.
→ often **5–10× faster** than row-by-row `INSERT` for the big tables. Add this if the `db Ys` number dominates.

---

## 8. Data model notes

- **Natural composite keys**, no surrogate ids: `sections(event_id,name)`, `seats(event_id,place_id)`, `offers(event_id,offer_id)`, `seat_offers(event_id,place_id)`.
- `placeId` is **only unique within an event** → keyed with `event_id`.
- **Timestamps:** `created_at`/`captured_at` set once; `last_seen_at` bumped **every run**; `updated_at` bumped **only when a business field actually changed** (`IS DISTINCT FROM`).
- `seat_offers` uses `captured_at` (not `created_at`) and has no `updated_at`.
- In `available` mode, `seats` and `seat_offers` have the same count (one live offer per available seat).

---

## 9. Gotchas we learned (important)

1. **Primary tickets have no seat in the offer object.** Primary is sold "best-available by price level" (P4, P23…); the offer carries a price level, not section/row/seat. The seat identity for primary comes **only from `places` + the manifest**, joined by `placeId`. Resale offers *do* carry section/row/seat directly. → read seats from `places`+manifest, not the offer, and you get **both** primary and resale.
2. **`c-tmpt` header = the `tmpt` cookie value.** Capture it from the browser.
3. **Search needs the browser; facets/manifest don't.** (Kasada per-request signature.)
4. **400/403 at scale = rate limiting**, not a token-type problem. At low volume the same requests succeed. Fix = the 5/s rate cap + forwarding cookies.
5. **facets `places` are compressed** with nested bracket notation (`PRE[A,Q]`, `PRE[C[M[A,Y],NA]]`). Must decompress before joining.
6. **Manifest is parallel arrays**, expanded by counts: walk `manifestSections` in order; **GA sections** (`"ga": true`) have **no rows and no placeIds**, so `manifestRows` is shorter than `manifestSections`. Skip GA when consuming rows. Pure-GA/parking events have no `placeIds` at all.
7. **Venue codes aren't always numeric** (e.g. `20271-150`) → `venue_id` is **varchar**.
8. **Some search results have no venue code** (parking, museum passes) → skipped with a warning.
9. **Prices are dynamic** — re-fetch facets to refresh; the manifest is static-ish (cache it).

---

## 10. Files

| file | role |
|---|---|
| `config.py` | env + endpoints + constants + `_env()` (strips inline `#` comments) |
| `browser_session.py` | Playwright bootstrap, discovery, `tmpt`/cookie capture, refresh |
| `event_details.py` | async facets + manifest fetch, rate limiter, manifest cache |
| `parse.py` | decompress places, decode manifest (GA-aware), join → row dicts |
| `db.py` | table metadata + generic bulk upsert + freshness sweep |
| `pipeline.py` | orchestration + progress logging |
| `main.py` | CLI entrypoint (`--limit`, `--headed`, `--seat-mode`, `-v`) |
| `log.py` | console + rotating-file logging |
| `manifests/` | on-disk manifest cache (`<eventId>.json`) |
| `logs/` | `tm_scraper.log` |

---

## 11. Run

```bash
python main.py                 # full run
python main.py --limit 30 -v   # debug on 30 events, DEBUG logs
python main.py --headed        # show the browser (needs a display / xvfb)
python main.py --seat-mode full
```
