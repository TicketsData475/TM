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
from urllib.parse import urlsplit

from curl_cffi.requests import AsyncSession

import config
import log
import parse_au
from browser_session import ProxyBlocked
from proxies import proxy_to_url

_LOG = log.get("details")
_blocked_logged = 0          # log the body of the first few 403s for diagnosis


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
            global _blocked_logged
            if _blocked_logged < 8:         # surface the real reason for the block
                _blocked_logged += 1
                _LOG.warning("%s %s -> %d | body: %s", what, event_id,
                             r.status_code, (r.text or "")[:200])
            raise ProxyBlocked(f"{what} {event_id} -> {r.status_code}: {(r.text or '')[:80]}")
        if r.status_code == 404:
            raise NotFound(f"{what} {event_id} -> 404")
        # ismds answers HOST-system events (e.g. Australian Open) with 400 Error.NotFound
        if r.status_code == 400 and "NotFound" in (r.text or ""):
            raise NotFound(f"{what} {event_id} -> 400 Error.NotFound")
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


_ISMDS_HOST = "www.ticketmaster.com"     # the US site served by ismds/offeradapter


def _host_base(event_url):
    """Brand-domain base (scheme://host) for the HOST endpoints, from the event's
    own url (e.g. https://www.ticketmaster.com.au), else the configured fallback."""
    if event_url:
        u = urlsplit(event_url)
        if u.scheme and u.netloc and "ticketmaster" in u.netloc:
            return f"{u.scheme}://{u.netloc}"
    return config.HOST_FALLBACK_BASE


def _is_brand_domain(event_url):
    """True for a non-US ticketmaster site (e.g. .com.au) -> HOST path, not ismds.
    Those events aren't in ismds anyway, and ismds Kasada-blocks before we'd even
    see the Error.NotFound, so route them straight to the HOST endpoints."""
    host = urlsplit(event_url).netloc if event_url else ""
    return bool(host) and "ticketmaster" in host and host != _ISMDS_HOST


async def _fetch_host(cs, event_id, event_url, session, proxy_kw, limiter):
    """HOST-system events (e.g. Australian Open): availability + prices come from
    seatmapoffered + quickpicks on the brand domain, rank from the manifest. Returns
    an ismds-shaped (facets_doc, manifest) so the parser is unchanged.

    Uses cookies scoped to the brand host (its own Kasada session, warmed by
    session.warm_brand) -- the US .com cookies don't satisfy .com.au's Kasada."""
    base = _host_base(event_url)
    cookie = await session.cookies_for_host(urlsplit(base).netloc)
    offered = await _get_json(cs, base + config.SEATMAPOFFERED_PATH.format(event_id=event_id),
                              cookie, proxy_kw, "seatmapoffered", event_id, limiter)
    offers = offered.get("offers") or []
    tids = "%2C".join(sorted({o["ticketTypeId"] for o in offers if o.get("ticketTypeId")}))

    picks, offset = [], 0
    for _ in range(config.QUICKPICKS_MAX_PAGES):
        qp = await _get_json(cs, base + config.QUICKPICKS_PATH.format(
            event_id=event_id, offset=offset, tids=tids),
            cookie, proxy_kw, "quickpicks", event_id, limiter)
        batch = qp.get("picks") or []
        if not batch:
            break
        picks.extend(batch)
        offset += len(batch)
        total = qp.get("total")
        if total and offset >= total:
            break
        await asyncio.sleep(config.QUICKPICKS_PAGE_DELAY)   # pace to dodge the rate block

    manifest = await _fetch_manifest(cs, event_id, cookie, proxy_kw, limiter)  # pubapi, public
    facets_doc = parse_au.to_facets_doc(manifest, picks, offers)
    _LOG.info("host-event %s: %d available seats, %d offers", event_id,
              sum(len(f["places"]) for f in facets_doc["facets"]), len(offers))
    return facets_doc, manifest


async def _fetch_one(cs, event_id, cookie, proxy_kw, limiter, event_url=None, session=None):
    """Fetch one event -> (event_id, facets_doc, manifest). A non-US brand-domain
    event (e.g. ticketmaster.com.au Australian Open) goes straight to the HOST path.
    Otherwise tries ismds (NFL/NBA/NHL/…) and, if ismds has no inventory for it
    (Error.NotFound = a US HOST event), falls back to the HOST path."""
    if _is_brand_domain(event_url):
        facets_doc, manifest = await _fetch_host(cs, event_id, event_url, session, proxy_kw, limiter)
        return event_id, facets_doc, manifest
    try:
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
    except NotFound:
        facets_doc, manifest = await _fetch_host(cs, event_id, event_url, session, proxy_kw, limiter)
        return event_id, facets_doc, manifest
    # prices live in OFFERS -> graft them onto PLACES for the parser
    places.setdefault("_embedded", {})["offer"] = \
        (offers.get("_embedded") or {}).get("offer") or []
    return event_id, places, manifest


async def fetch_batch(event_ids, session, concurrency=None, limiter=None, on_result=None,
                      url_map=None):
    """Fetch a batch concurrently with the session's solved cookies + proxy.

    `url_map` maps event_id -> event url (used to pick the brand domain for HOST
    events like the Australian Open). As each event finishes,
    `on_result(event_id, facets_doc, manifest)` is awaited (so the caller can
    persist + log live progress); results are also returned.
    Returns (results, blocked, errored):
      blocked = events that hit a Kasada 403 (caller decides: whole batch = session's
                fault -> release, no penalty; partial = event-specific -> penalise)
      errored = list of (event_id, fatal): fatal=True for a 404 (mark failed now),
                fatal=False for a transient error (retry later)."""
    concurrency = concurrency or config.CONCURRENCY
    # default cookies are the US ismds ones; HOST events fetch their own per-domain
    # cookies in _fetch_host, so a mixed queue never crosses .com and .com.au sessions
    cookie = await session.cookies_for_host("www.ticketmaster.com")
    proxy_kw = _proxy_kw(session.current_proxy())
    url_map = url_map or {}
    sem = asyncio.Semaphore(concurrency)
    results, blocked, errored = [], [], []

    async with AsyncSession(impersonate="chrome") as cs:
        async def worker(eid):
            async with sem:
                try:
                    res = await _fetch_one(cs, eid, cookie, proxy_kw, limiter,
                                           url_map.get(eid), session)
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
