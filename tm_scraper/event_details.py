"""Per-event detail fetch via curl_cffi -- FAST path.

Kasada doesn't sign the facets/manifest requests; it just needs a session that
already solved the challenge. So the browser (patchright) solves Kasada ONCE and
we reuse its cookies with curl_cffi, on the SAME proxy IP, to fetch every event
concurrently. Each event needs three GETs:

  PLACES   (services)     -> available seats per section + offerId   (show=places)
  OFFERS   (offeradapter) -> offerId -> list/face/total price        (embed=offer)
  MANIFEST (pubapi)       -> seat rank + row/seat labels             (static, cached)

PLACES._embedded.offer is filled from OFFERS so parse.build_seating_rows works
unchanged. On a 403/429 (cookies stale or IP flagged) we raise ProxyBlocked; the
caller refreshes the browser session (fresh cookies / new IP) and retries.
"""
import asyncio
import json
import os
import uuid

from curl_cffi.requests import AsyncSession

import config
import log
from browser_session import ProxyBlocked
from proxies import proxy_to_url

_LOG = log.get("details")


class NotFound(Exception):
    """HTTP 404 -- the event doesn't exist at this endpoint (e.g. non-seatmap /
    numeric ids from discovery). Fatal: don't retry, mark the event failed."""


def _proxy_kw(proxy):
    if not proxy:
        return {}
    u = proxy_to_url(proxy)
    return {"proxies": {"http": u, "https": u}}


def _headers(cookie_header):
    return {
        "accept": "*/*",
        "referer": "https://www.ticketmaster.com/",
        "user-agent": config.USER_AGENT,
        "tmps-correlation-id": str(uuid.uuid4()),
        "cookie": cookie_header or "",
    }


class RateLimiter:
    """Global cap: at most `rate` requests/second across all workers."""

    def __init__(self, rate):
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self):
        if not self._interval:
            return
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self._interval
        if delay:
            await asyncio.sleep(delay)


async def _get_json(cs, url, cookie, proxy_kw, what, event_id, limiter, retries=2):
    """GET + parse JSON. Retries transient network errors (timeout / broken pipe)
    a few times; a 403/429 is a block (-> ProxyBlocked, no retry) and other HTTP
    errors fail fast."""
    for attempt in range(retries + 1):
        if limiter:
            await limiter.wait()
        try:
            r = await cs.get(url, headers=_headers(cookie), timeout=45, **proxy_kw)
        except Exception as e:  # noqa: BLE001 - curl timeout / broken pipe / conn reset
            if attempt < retries:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"{what} {event_id} -> {type(e).__name__}: {repr(e)[:80]}")
        if r.status_code in (403, 429):
            raise ProxyBlocked(f"{what} {event_id} -> {r.status_code}")
        if r.status_code == 404:
            raise NotFound(f"{what} {event_id} -> 404")
        if r.status_code != 200:
            raise RuntimeError(f"{what} {event_id} -> HTTP {r.status_code}: {r.text[:120]}")
        return r.json()


def _manifest_path(event_id):
    return os.path.join(config.MANIFEST_CACHE_DIR, f"{event_id}.json")


async def _fetch_manifest(cs, event_id, cookie, proxy_kw, limiter):
    path = _manifest_path(event_id)
    if not config.REFRESH_MANIFEST and os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    data = await _get_json(cs, config.MANIFEST_URL.format(event_id=event_id),
                           cookie, proxy_kw, "manifest", event_id, limiter)
    os.makedirs(config.MANIFEST_CACHE_DIR, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(data, fh)
    return data


async def _fetch_one(cs, event_id, cookie, proxy_kw, limiter):
    """Fetch + merge places/offers/manifest for one event -> (event_id, facets_doc, manifest)."""
    places, offers, manifest = await asyncio.gather(
        _get_json(cs, config.PLACES_URL.format(
            event_id=event_id, channel=config.RESALE_CHANNEL,
            apikey=config.APIKEY, apisecret=config.APISECRET),
            cookie, proxy_kw, "places", event_id, limiter),
        _get_json(cs, config.OFFERS_URL.format(
            event_id=event_id, channel=config.RESALE_CHANNEL,
            apikey=config.APIKEY, apisecret=config.APISECRET),
            cookie, proxy_kw, "offers", event_id, limiter),
        _fetch_manifest(cs, event_id, cookie, proxy_kw, limiter),
    )
    # prices live in OFFERS -> graft them onto PLACES for the parser
    places.setdefault("_embedded", {})["offer"] = \
        (offers.get("_embedded") or {}).get("offer") or []
    return event_id, places, manifest


async def fetch_batch(event_ids, session, concurrency=None, limiter=None, on_result=None):
    """Fetch a batch concurrently with the session's solved cookies + proxy.

    As each event finishes, `on_result(event_id, facets_doc, manifest)` is awaited
    (so the caller can persist + log live progress); results are also returned.
    Returns (results, blocked, errored):
      blocked = events that hit a Kasada 403 (caller decides: whole batch = session's
                fault -> release, no penalty; partial = event-specific -> penalise)
      errored = list of (event_id, fatal): fatal=True for a 404 (mark failed now),
                fatal=False for a transient error (retry later)."""
    concurrency = concurrency or config.CONCURRENCY
    cookie = await session.get_cookie_header()
    proxy_kw = _proxy_kw(session.current_proxy())
    sem = asyncio.Semaphore(concurrency)
    results, blocked, errored = [], [], []

    async with AsyncSession(impersonate="chrome") as cs:
        async def worker(eid):
            async with sem:
                try:
                    res = await _fetch_one(cs, eid, cookie, proxy_kw, limiter)
                except ProxyBlocked as e:
                    blocked.append(eid)
                    _LOG.debug("blocked %s: %s", eid, e)
                    return
                except NotFound as e:
                    errored.append((eid, True))          # fatal -> fail now, no retry
                    _LOG.warning("event %s not found: %s", eid, e)
                    return
                except Exception as e:  # noqa: BLE001 - keep the batch going
                    errored.append((eid, False))         # transient -> retry later
                    _LOG.warning("event %s failed: %s", eid, repr(e)[:120])
                    return
                results.append(res)
                if on_result:
                    await on_result(*res)

        await asyncio.gather(*(worker(e) for e in event_ids))
    return results, blocked, errored
