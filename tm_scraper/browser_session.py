"""Stealth browser session: drives real Google Chrome via patchright (a patched,
undetectable Playwright) so Kasada doesn't flag it as automation and "pause" the
page. From inside the trusted page we drive the (protected) search API, and the
seatmap page loads normally -- which also mints the `tmpt` cookie (== the `c-tmpt`
header the facets API needs).

patchright + real Chrome + a persistent profile is what gets past Kasada's
"your browser activity has been paused" screen that plain Playwright triggers.
"""
import asyncio
import shutil
import tempfile
import time
from urllib.parse import urlsplit
from patchright.async_api import async_playwright

import config
import log
from proxies import ProxyNotAvailable, proxy_for_playwright
from proxies.factory import get_proxy_provider

_LOG = log.get("browser")


class ProxyBlocked(Exception):
    """Kasada paused the page / withheld data on the current exit IP -> rotate."""

_SEARCH_FETCH_JS = """
async ([url, region]) => {
  try {
    const r = await fetch(url, {
      headers: {
        'accept': 'application/json',
        'x-tmclient-app': 'marketplace_fe',
        'x-tmlangcode': 'en-us',
        'x-tmplatform': 'global',
        'x-tmregion': region,
      },
      credentials: 'include',
    });
    return { status: r.status, body: await r.text() };
  } catch (e) { return { status: -1, body: 'FETCH_ERROR: ' + String(e) }; }
}
"""


class TMSession:
    """Keeps a warm browser context so we can re-read a fresh `tmpt` on demand."""

    def __init__(self, headless=True):
        self.headless = headless
        self._pw = None
        self._ctx = None          # persistent context IS the browser (patchright)
        self._page = None
        self._profile_dir = None  # temp Chrome profile dir for this context
        self._provider = None
        self._proxy = None        # Proxy the browser AND the facets fetches share
        self._c_tmpt = None       # cached token minted on _proxy's IP
        self._cookie = None       # cached cookie header for the same context
        self._brand_urls = []     # brand-domain event urls to (re)warm on every context

    def _claim_new_proxy(self):
        """Claim a proxy (sync), retrying briefly if the pool is momentarily full."""
        if not self._provider:
            return None
        for _ in range(20):
            try:
                return self._provider.next()
            except ProxyNotAvailable:
                time.sleep(0.5)
        _LOG.warning("no free proxy available")
        return None

    def _new_profile_dir(self):
        """Fresh, empty Chrome profile dir (clean cookies per proxy IP)."""
        self._discard_profile_dir()
        self._profile_dir = tempfile.mkdtemp(prefix="tm_pw_profile_")
        return self._profile_dir

    def _discard_profile_dir(self):
        if self._profile_dir:
            shutil.rmtree(self._profile_dir, ignore_errors=True)
            self._profile_dir = None

    async def goto_retry(self, url, tries=4):
        """Navigate, tolerating the transient proxy hiccups (ERR_EMPTY_RESPONSE /
        ERR_ABORTED) that happen right after an IP rotation. Returns True on load."""
        last = None
        for i in range(1, tries + 1):
            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
                return True
            except Exception as e:  # noqa: BLE001
                last = e
                _LOG.warning("goto failed (try %d/%d): %s", i, tries, repr(e)[:90])
                await self._page.wait_for_timeout(3000 * i)   # let the proxy/IP settle
        _LOG.error("goto giving up after %d tries: %s", tries, repr(last)[:120])
        return False

    async def _open_context(self):
        """(Re)launch real Chrome on the current proxy via patchright and cache
        the fresh tmpt token + cookies (all bound to this proxy's IP).

        Uses launch_persistent_context with a real profile + no init scripts --
        patchright's recommended, least-detectable setup."""
        launch_kwargs = {
            "headless": self.headless,
            "no_viewport": True,          # real window size, not a headless default
            "locale": "en-US",
        }
        if config.BROWSER_CHANNEL:        # "chrome" -> real Google Chrome
            launch_kwargs["channel"] = config.BROWSER_CHANNEL
        if self._proxy:
            launch_kwargs["proxy"] = proxy_for_playwright(self._proxy)
        self._ctx = await self._pw.chromium.launch_persistent_context(
            self._new_profile_dir(), **launch_kwargs
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
        url = config.SEARCH_PAGE_URL.format(
            q=config.QUERY, start=config.START_DATE, end=config.END_DATE
        )
        await self.goto_retry(url)
        await self._page.wait_for_timeout(5000)
        await self._warm_kasada()          # harden the cookie so curl is trusted
        self._c_tmpt = await self.get_tmpt()
        self._cookie = await self.get_cookie_header()
        await self.warm_brands()           # re-solve brand-domain Kasada (.com.au, ...)

    async def _warm_kasada(self, rounds=None):
        """Fire a few in-page (JS-signed) search fetches so Kasada upgrades the
        session cookie from 'provisional' to a state curl can reuse. A bare
        search-page load is NOT enough -- without this, curl gets 403 on every
        facets call (the difference between --mode all, which discovers first, and
        --mode worker, which used to go straight to curl)."""
        rounds = rounds if rounds is not None else config.WARMUP_ROUNDS
        ok = 0
        for p in range(1, rounds + 1):
            api = config.SEARCH_API_URL.format(
                q=config.QUERY, region=config.REGION,
                start=config.START_DATE, end=config.END_DATE, page=p)
            try:
                res = await self._page.evaluate(_SEARCH_FETCH_JS, [api, config.REGION])
                if res["status"] == 200:
                    ok += 1
                else:
                    await self._page.reload(wait_until="domcontentloaded", timeout=60000)
                    await self._page.wait_for_timeout(3000)
            except Exception:  # noqa: BLE001
                pass
            await self._page.wait_for_timeout(700)
        _LOG.info("warmed Kasada session (%d/%d in-page fetches ok)", ok, rounds)

    async def start(self):
        t0 = time.monotonic()
        _LOG.info("launching stealth Chrome (patchright, channel=%s, headless=%s)...",
                  config.BROWSER_CHANNEL or "bundled-chromium", self.headless)
        self._pw = await async_playwright().start()
        self._provider = get_proxy_provider()
        if self._provider:
            self._proxy = self._claim_new_proxy()
            if self._proxy:
                _LOG.info("using proxy for browser+facets: %s:%s", self._proxy.host, self._proxy.port)
        _LOG.info("loading search page (Kasada should pass now)...")
        await self._open_context()
        _LOG.info("browser ready in %s (tmpt=%s)",
                  log.fmt_secs(time.monotonic() - t0), "set" if self._c_tmpt else "MISSING")

    async def _rotate_ip(self, proxy):
        """Ask the proxy's own API for a new exit IP (same endpoint). Returns the
        HTTP status: 200 = rotating, 422 = on cooldown (rotated too recently)."""
        def _hit():
            from curl_cffi import requests as cr
            return cr.get(proxy.refresh_url, timeout=30)   # direct, not through the proxy
        try:
            r = await asyncio.to_thread(_hit)
            _LOG.info("rotate_ip (id=%s) -> HTTP %s", proxy.proxy_id, r.status_code)
            return r.status_code
        except Exception as e:  # noqa: BLE001
            _LOG.warning("rotate_ip request failed: %s", e)
            return None

    async def _swap_to_other_id(self):
        """Claim a DIFFERENT proxy id (each has its own IP + rotation cooldown)."""
        if not self._provider:
            return False
        new = self._claim_new_proxy()
        if new and (not self._proxy or new.proxy_id != self._proxy.proxy_id):
            old = self._proxy
            self._proxy = new
            if old:
                try:
                    self._provider.release(old)
                except Exception as e:  # noqa: BLE001
                    _LOG.debug("proxy release failed: %s", e)
            return True
        return False

    async def rotate(self):
        """Get a fresh exit IP for THIS session's proxy and re-solve Kasada on it.

        The proxy self-rotates: hit its refresh_url, then wait for the new IP to
        take effect (PROXY_ROTATE_WAIT on HTTP 200; PROXY_COOLDOWN_WAIT if the id
        is on cooldown / 422). Each parallel session owns its own proxy, so we
        rotate IN PLACE and only switch ids for a proxy that can't self-rotate."""
        if self._proxy and self._proxy.refresh_url:
            status = await self._rotate_ip(self._proxy)
            await asyncio.sleep(config.PROXY_ROTATE_WAIT if status == 200
                                else config.PROXY_COOLDOWN_WAIT)
        else:
            await self._swap_to_other_id()
        try:
            if self._ctx:
                await self._ctx.close()   # closes the whole persistent Chrome
        except Exception:  # noqa: BLE001
            pass
        await self._open_context()
        _LOG.info("rotated -> proxy id=%s (%s:%s)",
                  self._proxy.proxy_id if self._proxy else None,
                  self._proxy.host if self._proxy else "-",
                  self._proxy.port if self._proxy else "-")

    def current_proxy(self):
        return self._proxy

    @property
    def c_tmpt(self):
        return self._c_tmpt

    @property
    def cookie(self):
        return self._cookie

    async def close(self):
        if self._ctx:
            try:
                await self._ctx.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw:
            await self._pw.stop()
        self._discard_profile_dir()
        if self._provider and self._proxy:
            try:
                self._provider.release(self._proxy)   # free the shared proxy
            except Exception as e:  # noqa: BLE001
                _LOG.debug("proxy release failed: %s", e)

    async def get_tmpt(self):
        """Value of the `tmpt` cookie == the `c-tmpt` header for facets."""
        for c in await self._ctx.cookies():
            if c["name"] == "tmpt":
                return c["value"]
        return None

    async def get_cookie_header(self):
        """All ticketmaster.com cookies as a 'k=v; k=v' string for curl requests."""
        parts = []
        for c in await self._ctx.cookies():
            if "ticketmaster.com" in c.get("domain", ""):
                parts.append(f"{c['name']}={c['value']}")
        return "; ".join(parts)

    async def cookies_for_host(self, host):
        """Cookie header with ONLY cookies valid for `host`, so a .com.au request
        doesn't carry .com cookies (different Kasada session) and vice-versa."""
        parts = []
        for c in await self._ctx.cookies():
            dom = (c.get("domain") or "").lstrip(".")
            if dom and (host == dom or host.endswith("." + dom)):
                parts.append(f"{c['name']}={c['value']}")
        return "; ".join(parts)

    async def warm_brand(self, event_url):
        """Solve Kasada on a NON-US brand domain (e.g. ticketmaster.com.au) so curl
        requests to that domain's endpoints (quickpicks etc.) carry a trusted
        session. Loads the brand HOME first (clears Kasada more gently than a cold
        event page), then the event page (so its APIs fire). Adds its cookies to
        the context. Returns True if Kasada cleared."""
        host = urlsplit(event_url).netloc
        base = f"{urlsplit(event_url).scheme}://{host}"
        _LOG.info("warming brand domain %s ...", host)
        accepted = False
        for target in (base, event_url):          # home first, then the event page
            for attempt in range(2):              # reload once if it won't clear
                if not await self.goto_retry(target):
                    continue
                cleared = False
                for i in range(16):               # up to ~48s for the interstitial
                    await self._page.wait_for_timeout(3000)
                    try:
                        content = (await self._page.content()).lower()
                    except Exception:  # noqa: BLE001
                        content = ""
                    if not accepted:
                        for sel in ("#onetrust-accept-btn-handler", 'button:has-text("Accept")'):
                            try:
                                el = self._page.locator(sel).first
                                if await el.count():
                                    await el.click(timeout=2500); accepted = True; break
                            except Exception:  # noqa: BLE001
                                pass
                    if content and "activity has been paused" not in content \
                            and "browsing activity" not in content:
                        cleared = True
                        break
                if cleared:
                    break
            else:
                _LOG.warning("brand domain %s did not clear on %s", host, target)
        await self._page.wait_for_timeout(8000)   # let the event page fire its APIs
        ok = "activity has been paused" not in (await self._safe_page_text())
        _LOG.info("brand domain %s %s", host, "warmed" if ok else "NOT warmed (Kasada paused)")
        return ok

    async def _safe_page_text(self):
        try:
            return (await self._page.content()).lower()
        except Exception:  # noqa: BLE001
            return ""

    def set_brand_urls(self, urls):
        """One event url per brand domain to (re)warm whenever the context is
        (re)built -- so a rotate/refresh doesn't drop the .com.au Kasada session."""
        self._brand_urls = list(urls)

    async def warm_brands(self):
        """(Re)warm every registered brand domain. Called on each _open_context, so
        rotations keep their .com.au session instead of losing it."""
        for url in self._brand_urls:
            await self.warm_brand(url)

    async def ensure_tmpt(self, event_url=None):
        """Return a tmpt token; if the search page didn't set one, visit an
        event page (which reliably mints it) and re-check. Always refreshes the
        cached token/cookies the facets fetcher reads."""
        tmpt = await self.get_tmpt()
        if not tmpt and event_url:
            _LOG.info("tmpt not set yet; visiting an event page to mint it...")
            try:
                await self._page.goto(event_url, wait_until="domcontentloaded", timeout=60000)
                await self._page.wait_for_timeout(5000)
            except Exception as e:  # noqa: BLE001
                _LOG.warning("event-page visit failed: %s", e)
            tmpt = await self.get_tmpt()
        if not tmpt:
            names = sorted({c["name"] for c in await self._ctx.cookies()})
            _LOG.warning("tmpt still missing. cookies present: %s", ", ".join(names) or "(none)")
        # keep the cached token/cookies in sync (used by the facets fetcher)
        self._c_tmpt = tmpt
        self._cookie = await self.get_cookie_header()
        return tmpt

    async def refresh(self):
        """Re-solve Kasada on the SAME exit IP to mint fresh cookies (reload the
        search page). Used when curl starts getting 403s because the session's
        Kasada cookies went stale. Cheaper than a full IP rotation."""
        url = config.SEARCH_PAGE_URL.format(
            q=config.QUERY, start=config.START_DATE, end=config.END_DATE)
        await self.goto_retry(url)
        await self._page.wait_for_timeout(5000)
        await self._warm_kasada()          # re-harden the cookie for curl
        self._c_tmpt = await self.get_tmpt()
        self._cookie = await self.get_cookie_header()
        _LOG.info("session refreshed (fresh cookies on proxy id=%s)",
                  self._proxy.proxy_id if self._proxy else None)

    async def discover_events(self, query=None, start=None, end=None,
                              max_pages=200, page_delay_ms=1200, attempts=4, max_rotations=3):
        """Page through the search API from inside the browser for one query
        (e.g. 'nfl'/'nba'/'nhl') over a [start, end] date window. Returns event dicts.

        The API caps deep pagination at ~980 (HTTP 400) -> callers pass a narrow
        date window to stay under it. Kasada may block rapid pagination: we re-solve
        a few times, and if a page stays blocked we rotate to a fresh exit IP and
        retry it (up to max_rotations) before giving up with what we have."""
        import json as _json

        q = query or config.QUERY
        start = start or config.START_DATE
        end = end or config.END_DATE
        tag = f"{q} {start[:7]}"          # e.g. "nba 2026-10"
        events, page_num, total, rotations = [], 1, None, 0
        while page_num <= max_pages:
            api = config.SEARCH_API_URL.format(
                q=q, region=config.REGION, start=start, end=end, page=page_num,
            )
            data = None
            capped = False
            for attempt in range(1, attempts + 1):
                try:
                    res = await self._page.evaluate(_SEARCH_FETCH_JS, [api, config.REGION])
                except Exception as e:  # noqa: BLE001 - transient network / page mid-nav
                    res = {"status": -1, "body": repr(e)[:80]}
                if res["status"] == 200:
                    try:
                        data = _json.loads(res["body"])
                        break
                    except Exception:  # noqa: BLE001 - truncated/garbled body -> retry
                        res = {"status": -2, "body": "bad json"}
                if res["status"] == 400:      # ~980 deep-pagination cap, not a block
                    _LOG.info("[%s] page %d -> 400: reached TM's ~980 pagination cap; "
                              "stopping this window at %d events", tag, page_num, len(events))
                    capped = True
                    break
                _LOG.warning("[%s] page %d attempt %d/%d -> HTTP %s (%s); re-solving...",
                             tag, page_num, attempt, attempts,
                             res["status"], (res["body"] or "")[:80])
                try:
                    await self._page.reload(wait_until="domcontentloaded", timeout=60000)
                except Exception:  # noqa: BLE001
                    pass
                await self._page.wait_for_timeout(3000 + attempt * 2500)

            if capped:
                break
            if data is None:
                if rotations < max_rotations:      # IP likely flagged -> fresh IP, retry page
                    rotations += 1
                    _LOG.info("[%s] page %d blocked; rotating exit IP (%d/%d) and retrying...",
                              tag, page_num, rotations, max_rotations)
                    await self.rotate()
                    continue
                _LOG.error("[%s] giving up on page %d after %d attempts + %d rotations; "
                           "keeping %d events", tag, page_num, attempts, rotations, len(events))
                break

            batch = data.get("events", [])
            if not batch:
                break
            events.extend(batch)
            total = data.get("total", total)
            _LOG.info("[%s] page %d: +%d events (%d%s collected)",
                      tag, page_num, len(batch), len(events),
                      "/" + str(total) if total else "")
            if total and len(events) >= total:
                break
            page_num += 1
            await self._page.wait_for_timeout(page_delay_ms)
        return events
