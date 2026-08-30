"""Playwright session: solves Kasada once, then drives the (protected) search API
from inside the page so the anti-bot headers are attached automatically.

Also exposes the `tmpt` cookie, which is exactly the `c-tmpt` header the facets
API needs.
"""
import asyncio
import time
from playwright.async_api import async_playwright

import config
import log

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


class TMSession:
    """Keeps a warm browser context so we can re-read a fresh `tmpt` on demand."""

    def __init__(self, headless=True):
        self.headless = headless
        self._pw = None
        self._browser = None
        self._ctx = None
        self._page = None

    async def start(self):
        t0 = time.monotonic()
        _LOG.info("launching Chromium (headless=%s)...", self.headless)
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        self._ctx = await self._browser.new_context(
            user_agent=config.USER_AGENT, locale="en-US"
        )
        self._page = await self._ctx.new_page()
        url = config.SEARCH_PAGE_URL.format(
            q=config.QUERY, start=config.START_DATE, end=config.END_DATE
        )
        _LOG.info("loading search page to solve Kasada: %s", url)
        await self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
        # give Kasada's client challenge time to run and set cookies
        await self._page.wait_for_timeout(5000)
        has_tmpt = bool(await self.get_tmpt())
        _LOG.info("browser ready in %s (tmpt cookie=%s)",
                  log.fmt_secs(time.monotonic() - t0), "set" if has_tmpt else "MISSING")

    async def close(self):
        for closer in (self._ctx, self._browser):
            if closer:
                await closer.close()
        if self._pw:
            await self._pw.stop()

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
        event page (which reliably mints it) and re-check."""
        tmpt = await self.get_tmpt()
        if tmpt:
            return tmpt
        if event_url:
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
        return tmpt

    async def refresh(self):
        """Reload the page to mint a fresh tmpt when the old one expires."""
        _LOG.warning("refreshing browser session for a new tmpt token...")
        await self._page.reload(wait_until="domcontentloaded", timeout=60000)
        await self._page.wait_for_timeout(4000)
        tmpt = await self.get_tmpt()
        _LOG.info("tmpt refreshed (%s)", "set" if tmpt else "MISSING")
        return tmpt

    async def discover_events(self, max_pages=200):
        """Page through the search API from inside the browser. Returns event dicts."""
        import json as _json

        events, page_num, total = [], 1, None
        while page_num <= max_pages:
            api = config.SEARCH_API_URL.format(
                q=config.QUERY, region=config.REGION,
                start=config.START_DATE, end=config.END_DATE, page=page_num,
            )
            res = await self._page.evaluate(_SEARCH_FETCH_JS, [api, config.REGION])
            if res["status"] != 200:
                raise RuntimeError(f"search page {page_num} -> HTTP {res['status']}: "
                                   f"{res['body'][:200]}")
            data = _json.loads(res["body"])
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
            await self._page.wait_for_timeout(400)
        return events
