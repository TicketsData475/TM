#!/usr/bin/env python3
"""Robust interception probe: what does the app's OWN facets traffic contain?

Since Kasada only signs the app's own requests, interception is our path. This
drives into the seat map and dumps the FULL params + data summary of every facets
call, so we can see whether seat-level inventory (places + offers) is fetched.

Run:  HEADLESS=false python probe_intercept.py
"""
import asyncio
import json

import config
from browser_session import TMSession

SKIP_TITLE = ("HALF PRICE", "PARKING", "HOSPITALITY", "SUITE", "PACKAGE")
RESPS = []   # (status, url, body)


def _is_facets(u):
    return "ismds" in u and "facets" in u


def _summ(body):
    try:
        d = json.loads(body)
    except Exception:
        return f"<non-json {len(body)}b: {body[:60]!r}>"
    facets = d.get("facets", [])
    emb = d.get("_embedded", {})
    offers = emb.get("offer", []) or emb.get("offers", [])
    with_places = sum(1 for f in facets if f.get("places"))
    sample = facets[0] if facets else {}
    return (f"facets={len(facets)} with_places={with_places} embedded_offers={len(offers)} "
            f"| sample keys={list(sample.keys())}")


async def main():
    s = TMSession(headless=config.HEADLESS)
    await s.start()
    events = await s.discover_events(max_pages=2)
    ev = next((e for e in events if not e.get("isPartner")
               and not any(k in (e.get("title") or "").upper() for k in SKIP_TITLE)),
              events[0] if events else None)
    if not ev:
        print("no events"); await s.close(); return
    print("\nevent:", ev["id"], "|", ev.get("title"))
    print("url:", ev.get("url"))
    page = s._page

    async def on_response(resp):
        if _is_facets(resp.url):
            try:
                body = await resp.text()
            except Exception:
                body = ""
            RESPS.append((resp.status, resp.url, body))

    page.on("response", on_response)

    try:
        await page.goto(ev["url"], wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print("goto:", repr(e)[:120])
    await page.wait_for_timeout(6000)

    body = (await page.content()).lower()
    print("looks blocked (activity paused):", "activity" in body and "paused" in body)
    print("page title:", repr(await page.title()))

    # try to enter the seat-selection flow
    for sel in ('a:has-text("Find Tickets")', 'button:has-text("Find Tickets")',
                'a:has-text("Get Tickets")', 'button:has-text("Buy")',
                'button:has-text("Select")', 'text=Select Your Own Seats'):
        try:
            el = page.locator(sel).first
            if await el.count():
                await el.click(timeout=4000)
                print("clicked:", sel)
                break
        except Exception:
            continue

    # wait (up to ~40s) for facets to fire, nudging the map
    for _ in range(7):
        await page.wait_for_timeout(5000)
        try:
            await page.mouse.wheel(0, 1000)
        except Exception:
            pass
        if len(RESPS) >= 3:
            break

    print(f"\n=== captured {len(RESPS)} facets calls ===")
    for status, u, b in RESPS:
        print(f"\n  {status}  facets{u.split('facets',1)[1][:140]}")
        print(f"       -> {_summ(b)}")
    print("\nfinal url:", page.url)
    await s.close()


if __name__ == "__main__":
    asyncio.run(main())
