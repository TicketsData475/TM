#!/usr/bin/env python3
"""Decisive: does our rich places+offer query on services.ticketmaster.com work
via in-page fetch (like the manifest does)? If yes -> ONE fetch per event = full
seat-level data, parser unchanged, no interception.

Run:  HEADLESS=false python probe_services.py
"""
import asyncio
import json

import config
from browser_session import TMSession

SKIP_TITLE = ("HALF PRICE", "PARKING", "HOSPITALITY", "SUITE", "PACKAGE")


def _summ(body):
    try:
        d = json.loads(body)
    except Exception:
        return f"<non-json {len(body)}b: {body[:80]!r}>"
    facets = d.get("facets", [])
    emb = d.get("_embedded", {})
    offers = emb.get("offer", []) or emb.get("offers", [])
    with_places = sum(1 for f in facets if f.get("places"))
    priced = [o for o in offers if o.get("listPrice") is not None]
    return (f"facets={len(facets)} with_places={with_places} embedded_offers={len(offers)} "
            f"priced_offers={len(priced)} | facet[0] keys={list(facets[0].keys()) if facets else []}")


async def _accept(page):
    for sel in ('#onetrust-accept-btn-handler', 'button:has-text("Accept")'):
        try:
            el = page.locator(sel).first
            if await el.count():
                await el.click(timeout=2500); return
        except Exception:
            pass


async def main():
    s = TMSession(headless=config.HEADLESS)
    await s.start()
    events = await s.discover_events(max_pages=1)
    ev = next((e for e in events if not e.get("isPartner")
               and not any(k in (e.get("title") or "").upper() for k in SKIP_TITLE)),
              events[0] if events else None)
    print("\nevent:", ev["id"], "|", ev.get("title"))
    page = s._page

    async def _blocked():
        try:
            return "activity" in (await page.content()).lower()
        except Exception:
            return True   # mid-navigation -> treat as not-ready yet

    await s.goto_retry(ev["url"])
    for i in range(8):
        await page.wait_for_timeout(4000)
        if i == 1:
            await _accept(page)
        if not await _blocked():
            break

    url = config.FACETS_URL.format(event_id=ev["id"], channel=config.RESALE_CHANNEL,
                                   apikey=config.APIKEY, apisecret=config.APISECRET)
    print("host:", url.split("/")[2])
    r = await s.page_fetch(url)
    print("in-page fetch (services rich) -> HTTP", r["status"])
    if r["status"] == 200:
        print("  ->", _summ(r["body"]))
    else:
        print("  body:", (r["body"] or "")[:150])
    await s.close()


if __name__ == "__main__":
    asyncio.run(main())
