#!/usr/bin/env python3
"""Capture the app's real facets responses to disk so we can design the parser.

Loads an event, waits for Kasada to clear, dismisses the cookie popup, tries to
open the seat map (to trigger seat-level show=places calls), and SAVES every
facets response + the manifest to ./dumps/ for inspection.

Run:  HEADLESS=false python probe_capture.py
"""
import asyncio
import json
import os

import config
from browser_session import TMSession
from curl_cffi.requests import AsyncSession

SKIP_TITLE = ("HALF PRICE", "PARKING", "HOSPITALITY", "SUITE", "PACKAGE")
DUMP_DIR = os.path.join(os.path.dirname(__file__), "dumps")
RESPS = []   # (status, url, body)


def _is_facets(u):
    return "ismds" in u and "facets" in u


async def _cap(resp):
    if _is_facets(resp.url):
        try:
            body = await resp.text()
        except Exception:
            body = ""
        RESPS.append((resp.status, resp.url, body))


async def _accept_cookies(page):
    for sel in ('button:has-text("Accept All")', 'button:has-text("Accept")',
                '#onetrust-accept-btn-handler', 'button:has-text("I Accept")',
                'button:has-text("Got it")'):
        try:
            el = page.locator(sel).first
            if await el.count():
                await el.click(timeout=3000)
                print("dismissed popup via:", sel)
                return True
        except Exception:
            continue
    return False


async def main():
    os.makedirs(DUMP_DIR, exist_ok=True)
    s = TMSession(headless=config.HEADLESS)
    await s.start()
    events = await s.discover_events(max_pages=1)
    ev = next((e for e in events if not e.get("isPartner")
               and not any(k in (e.get("title") or "").upper() for k in SKIP_TITLE)),
              events[0] if events else None)
    if not ev:
        print("no events"); await s.close(); return
    print("\nevent:", ev["id"], "|", ev.get("title"))
    page = s._page
    page.on("response", lambda r: asyncio.create_task(_cap(r)))

    await s.goto_retry(ev["url"])

    # wait for Kasada to clear + initial facets to fire
    for i in range(10):
        await page.wait_for_timeout(4000)
        blocked = "activity" in (await page.content()).lower()
        if i == 1:
            await _accept_cookies(page)
        if not blocked and RESPS:
            break
    print(f"initial facets captured: {len(RESPS)}")

    # try to open the seat map to trigger seat-level (show=places) calls
    await _accept_cookies(page)
    for sel in ('a:has-text("Find Tickets")', 'button:has-text("Find Tickets")',
                'button:has-text("Select Your Own Seats")', 'text=View Seat Map',
                'button:has-text("Buy")'):
        try:
            el = page.locator(sel).first
            if await el.count():
                await el.click(timeout=3000)
                print("clicked:", sel)
                break
        except Exception:
            continue
    for _ in range(6):
        await page.wait_for_timeout(4000)
        try:
            await page.mouse.wheel(0, 900)
        except Exception:
            pass

    # save everything
    idx = []
    for i, (status, u, body) in enumerate(RESPS):
        path = os.path.join(DUMP_DIR, f"facets_{i:02d}.json")
        with open(path, "w") as f:
            f.write(body)
        idx.append({"i": i, "status": status, "url": u, "bytes": len(body)})
    with open(os.path.join(DUMP_DIR, "_index.json"), "w") as f:
        json.dump({"event": ev["id"], "title": ev.get("title"), "calls": idx}, f, indent=2)

    # manifest (public API, no Kasada)
    try:
        async with AsyncSession(impersonate="chrome") as cs:
            r = await cs.get(config.MANIFEST_URL.format(event_id=ev["id"]), timeout=30)
            with open(os.path.join(DUMP_DIR, "manifest.json"), "w") as f:
                f.write(r.text)
            print("manifest saved: HTTP", r.status_code, len(r.text), "bytes")
    except Exception as e:
        print("manifest fetch failed:", repr(e)[:100])

    print(f"\nsaved {len(RESPS)} facets responses to {DUMP_DIR}")
    for row in idx:
        print(f"  facets_{row['i']:02d}.json  {row['status']}  {row['bytes']}b  "
              f"...{row['url'].split('facets',1)[1][:70]}")
    await s.close()


if __name__ == "__main__":
    asyncio.run(main())
