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
from patchright.async_api import async_playwright

import config
import log
from proxies import ProxyNotAvailable, proxy_for_playwright
from proxies.factory import get_proxy_provider

_LOG = log.get("browser")

_SEARCH_FETCH_JS = """
async ([url, region]) => {
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
}
"""

# Generic in-page fetch: runs in the trusted page so Kasada auto-signs the request
# with its per-request headers (x-kpsdk-*). This is how we now hit the facets API.
# NOTE: no `credentials` -> facets auths via apikey/apisecret in the query string,
# and offeradapter returns Access-Control-Allow-Origin:* which the browser refuses
# to combine with credentials (that caused "Failed to fetch"). Kasada still signs
# the request regardless of credentials mode.
_PAGE_FETCH_JS = """
async (url) => {
  try {
    const r = await fetch(url, { headers: { 'accept': 'application/json' }, credentials: 'omit' });
    const t = await r.text();
    return { status: r.status, body: t };
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
        self._c_tmpt = await self.get_tmpt()
        self._cookie = await self.get_cookie_header()

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
        """Single remedy for a 403 / blocked / expired token. Each proxy id is a
        self-rotating endpoint: rotate its exit IP via refresh_url; if that id is
        on cooldown (422) or has no refresh_url, switch to a different id. Then
        rebuild the context to mint a fresh token on the new IP."""
        status = None
        if self._proxy and self._proxy.refresh_url:
            status = await self._rotate_ip(self._proxy)
            if status == 200:
                await asyncio.sleep(config.PROXY_ROTATE_WAIT)   # let the new IP settle
        if status != 200:
            # cooldown / no refresh_url -> use another id (may be off-cooldown)
            await self._swap_to_other_id()
        try:
            if self._ctx:
                await self._ctx.close()   # closes the whole persistent Chrome
        except Exception:  # noqa: BLE001
            pass
        await self._open_context()
        _LOG.info("rotated -> proxy id=%s (%s:%s) tmpt=%s",
                  self._proxy.proxy_id if self._proxy else None,
                  self._proxy.host if self._proxy else "-",
                  self._proxy.port if self._proxy else "-",
                  "set" if self._c_tmpt else "MISSING")

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

    async def page_fetch(self, url):
        """Fetch `url` from inside the trusted page (Kasada signs it for us).
        Returns {'status': int, 'body': str}. status -1 means a JS fetch error."""
        return await self._page.evaluate(_PAGE_FETCH_JS, url)

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

    async def discover_events(self, max_pages=200, page_delay_ms=1200, attempts=4):
        """Page through the search API from inside the browser. Returns event dicts.

        Kasada blocks rapid pagination, so we pace requests and, on a block/non-200,
        reload the page to re-solve the challenge and retry the same page a few
        times before giving up (returns whatever was collected so far)."""
        import json as _json

        events, page_num, total = [], 1, None
        while page_num <= max_pages:
            api = config.SEARCH_API_URL.format(
                q=config.QUERY, region=config.REGION,
                start=config.START_DATE, end=config.END_DATE, page=page_num,
            )
            data = None
            for attempt in range(1, attempts + 1):
                res = await self._page.evaluate(_SEARCH_FETCH_JS, [api, config.REGION])
                if res["status"] == 200:
                    data = _json.loads(res["body"])
                    break
                _LOG.warning("search page %d attempt %d/%d -> HTTP %s (%s); "
                             "re-solving Kasada...", page_num, attempt, attempts,
                             res["status"], (res["body"] or "")[:80])
                await self._page.reload(wait_until="domcontentloaded", timeout=60000)
                await self._page.wait_for_timeout(3000 + attempt * 2500)

            if data is None:
                _LOG.error("giving up on page %d after %d attempts; continuing with "
                           "%d events collected so far", page_num, attempts, len(events))
                break

            batch = data.get("events", [])
            if not batch:
                break
            events.extend(batch)
            total = data.get("total", total)
            _LOG.info("discovery page %d: +%d events (%d%s collected)",
                      page_num, len(batch), len(events),
                      "/" + str(total) if total else "")
            if total and len(events) >= total:
                break
            page_num += 1
            await self._page.wait_for_timeout(page_delay_ms)
        return events
