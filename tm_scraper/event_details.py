"""Per-event detail fetch (facets + manifest) with curl_cffi, concurrent.

facets  : needs the c-tmpt header (the tmpt cookie value).
manifest: fully public; cached on disk keyed by event id.
"""
import asyncio
import json
import os
import uuid

from curl_cffi.requests import AsyncSession

import config
import log

_LOG = log.get("details")
_bodies_logged = 0          # log the raw body of the first few failures for diagnosis


class AuthExpired(Exception):
    """Raised when facets rejects us -> caller should refresh tmpt and retry."""


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


async def fetch_facets(session, event_id, c_tmpt, cookie_header=None, limiter=None):
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
    r = await session.get(url, headers=headers, timeout=30)
    if r.status_code != 200 and _bodies_logged < 5:
        _bodies_logged += 1
        _LOG.warning("facets %s HTTP %d | c-tmpt[:16]=%s | body: %s",
                     event_id, r.status_code, (c_tmpt or "")[:16], r.text[:400])
    if r.status_code in (401, 403, 419):
        raise AuthExpired(f"facets {event_id} -> {r.status_code}")
    if r.status_code != 200:
        raise RuntimeError(f"facets {event_id} -> HTTP {r.status_code}")
    return r.json()


async def fetch_manifest(session, event_id):
    path = _manifest_path(event_id)
    if not config.REFRESH_MANIFEST and os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    r = await session.get(
        config.MANIFEST_URL.format(event_id=event_id),
        headers={"user-agent": config.USER_AGENT,
                 "referer": "https://www.ticketmaster.com/"},
        timeout=45,
    )
    if r.status_code != 200:
        raise RuntimeError(f"manifest {event_id} -> HTTP {r.status_code}")
    data = r.json()
    with open(path, "w") as fh:
        json.dump(data, fh)
    return data


async def fetch_one(session, event_id, c_tmpt, cookie_header, limiter):
    """Fetch facets + manifest for one event (manifest usually from disk cache)."""
    facets, manifest = await asyncio.gather(
        fetch_facets(session, event_id, c_tmpt, cookie_header, limiter),
        fetch_manifest(session, event_id),
    )
    return event_id, facets, manifest


async def fetch_batch(event_ids, c_tmpt, cookie_header=None, concurrency=None, limiter=None):
    """Fetch a batch of events concurrently, rate-capped.
    Returns (results, needs_auth_refresh, error_count).
    """
    concurrency = concurrency or config.CONCURRENCY
    limiter = limiter or RateLimiter(config.REQ_PER_SEC)
    sem = asyncio.Semaphore(concurrency)
    results, needs_refresh, errors, auth_errors = [], False, 0, 0

    async with AsyncSession(impersonate="chrome") as session:
        async def worker(eid):
            nonlocal needs_refresh, errors, auth_errors
            async with sem:
                try:
                    results.append(await fetch_one(session, eid, c_tmpt, cookie_header, limiter))
                except AuthExpired as e:
                    needs_refresh = True
                    auth_errors += 1
                    _LOG.debug("auth expired on %s: %s", eid, e)
                except Exception as e:  # noqa: BLE001 - keep the batch going
                    errors += 1
                    _LOG.warning("event %s failed: %s", eid, e)

        await asyncio.gather(*(worker(e) for e in event_ids))
    if auth_errors:
        _LOG.warning("%d/%d events hit auth errors (token likely expired)",
                     auth_errors, len(event_ids))
    return results, needs_refresh, errors
