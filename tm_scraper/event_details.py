"""Per-event detail fetch (facets + manifest) with curl_cffi, concurrent.

The proxy and the tmpt token are OWNED BY THE BROWSER SESSION (they must share an
IP — TM binds tmpt to the IP that minted it). Facets reuse that same proxy + token.
On a 403 / expired token we ask the session to `rotate()`: switch to a new proxy
AND mint a fresh token on it, then retry the same event.
"""
import asyncio
import json
import os
import uuid

from curl_cffi.requests import AsyncSession

import config
import log
from proxies import proxy_to_url

_LOG = log.get("details")
_bodies_logged = 0          # log the raw body of the first few failures for diagnosis


class AuthExpired(Exception):
    """Raised on 401/419 -> the tmpt token is stale."""


class ProxyBlocked(Exception):
    """Raised on 403/429 -> the current proxy/IP + token are blocked."""


def _proxy_kwargs(proxy):
    """curl_cffi kwargs for a proxy, or {} when there is none."""
    if not proxy:
        return {}
    url = proxy_to_url(proxy)
    return {"proxies": {"http": url, "https": url}}


class Rotator:
    """Serialises session.rotate() so a burst of 403s triggers a single
    proxy+token swap (guarded by a generation counter), not one per worker."""

    def __init__(self, session):
        self.session = session
        self._lock = asyncio.Lock()
        self.generation = 0

    async def rotate(self, seen_generation):
        async with self._lock:
            if seen_generation != self.generation:
                return                     # already rotated by another worker
            await self.session.rotate()
            self.generation += 1


class RateLimiter:
    """Simple global cap: at most `rate` requests per second across all workers."""

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
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self._interval
        if wait:
            await asyncio.sleep(wait)


def _manifest_path(event_id):
    return os.path.join(config.MANIFEST_CACHE_DIR, f"{event_id}.json")


async def fetch_facets(session, event_id, c_tmpt, cookie_header=None, limiter=None, proxy_kw=None):
    global _bodies_logged
    url = config.FACETS_URL.format(
        event_id=event_id, channel=config.RESALE_CHANNEL,
        apikey=config.APIKEY, apisecret=config.APISECRET,
    )
    headers = {
        "accept": "*/*",
        "c-tmpt": c_tmpt or "",
        "referer": "https://www.ticketmaster.com/",
        "user-agent": config.USER_AGENT,
        "tmps-correlation-id": str(uuid.uuid4()),
    }
    if cookie_header:
        headers["cookie"] = cookie_header
    if limiter:
        await limiter.wait()
    r = await session.get(url, headers=headers, timeout=30, **(proxy_kw or {}))
    if r.status_code != 200 and _bodies_logged < 5:
        _bodies_logged += 1
        _LOG.warning("facets %s HTTP %d | c-tmpt[:16]=%s | body: %s",
                     event_id, r.status_code, (c_tmpt or "")[:16], r.text[:400])
    if r.status_code in (403, 429):
        raise ProxyBlocked(f"facets {event_id} -> {r.status_code}")
    if r.status_code in (401, 419):
        raise AuthExpired(f"facets {event_id} -> {r.status_code}")
    if r.status_code != 200:
        raise RuntimeError(f"facets {event_id} -> HTTP {r.status_code}")
    return r.json()


async def fetch_manifest(session, event_id, proxy_kw=None):
    path = _manifest_path(event_id)
    if not config.REFRESH_MANIFEST and os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    r = await session.get(
        config.MANIFEST_URL.format(event_id=event_id),
        headers={"user-agent": config.USER_AGENT,
                 "referer": "https://www.ticketmaster.com/"},
        timeout=45,
        **(proxy_kw or {}),
    )
    if r.status_code in (403, 429):
        raise ProxyBlocked(f"manifest {event_id} -> {r.status_code}")
    if r.status_code != 200:
        raise RuntimeError(f"manifest {event_id} -> HTTP {r.status_code}")
    data = r.json()
    with open(path, "w") as fh:
        json.dump(data, fh)
    return data


async def fetch_one(curl_session, event_id, rot, limiter):
    """Fetch facets + manifest for one event using the session's shared proxy +
    token. On a 403 / expired token, ask the session to rotate (new proxy + fresh
    token on that IP) and retry the SAME event, up to the combined budget."""
    max_rotations = config.PROXY_MAX_ROTATIONS + config.TOKEN_MAX_RENEWS
    for attempt in range(max_rotations + 1):
        s = rot.session
        proxy_kw = _proxy_kwargs(s.current_proxy())
        gen = rot.generation          # for once-per-generation rotation de-dupe
        try:
            facets, manifest = await asyncio.gather(
                fetch_facets(curl_session, event_id, s.c_tmpt, s.cookie, limiter, proxy_kw),
                fetch_manifest(curl_session, event_id, proxy_kw),
            )
            return event_id, facets, manifest
        except (ProxyBlocked, AuthExpired):
            if attempt < max_rotations:   # per-event retry budget
                _LOG.info("event %s blocked; rotating proxy + token and retrying", event_id)
                await rot.rotate(gen)
                continue
            raise RuntimeError(f"event {event_id} still blocked after {max_rotations} rotations")


async def fetch_batch(event_ids, rot, concurrency=None, limiter=None):
    """Fetch a batch of events concurrently, rate-capped. Proxy/token rotation
    happens inside fetch_one. Returns (results, error_count)."""
    concurrency = concurrency or config.CONCURRENCY
    limiter = limiter or RateLimiter(config.REQ_PER_SEC)
    sem = asyncio.Semaphore(concurrency)
    results, errors = [], 0

    async with AsyncSession(impersonate="chrome") as curl_session:
        async def worker(eid):
            nonlocal errors
            async with sem:
                try:
                    results.append(await fetch_one(curl_session, eid, rot, limiter))
                except Exception as e:  # noqa: BLE001 - keep the batch going
                    errors += 1
                    _LOG.warning("event %s failed: %s", eid, e)

        await asyncio.gather(*(worker(e) for e in event_ids))
    return results, errors
