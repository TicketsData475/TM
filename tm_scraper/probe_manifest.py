#!/usr/bin/env python3
"""Probe: how do we get the manifest (seat rank + row/seat labels) now that it's
Kasada-protected? Test three ways: intercept the app's own fetch, in-page fetch,
and direct navigation.

Run:  HEADLESS=false python probe_manifest.py
"""
import asyncio
import os

import config
from browser_session import TMSession

SKIP_TITLE = ("HALF PRICE", "PARKING", "HOSPITALITY", "SUITE", "PACKAGE")
DUMP_DIR = os.path.join(os.path.dirname(__file__), "dumps")
MANIFEST_HITS = []   # (status, url, bytes)


async def _cap(resp):
    if "manifest" in resp.url:
        try:
            body = await resp.text()
        except Exception:
            body = ""
        MANIFEST_HITS.append((resp.status, resp.url, len(body)))
        if resp.status == 200 and len(body) > 500:
            with open(os.path.join(DUMP_DIR, "manifest_intercepted.json"), "w") as f:
                f.write(body)


async def main():
    os.makedirs(DUMP_DIR, exist_ok=True)
    s = TMSession(headless=config.HEADLESS)
    await s.start()
    events = await s.discover_events(max_pages=1)
    ev = next((e for e in events if not e.get("isPartner")
               and not any(k in (e.get("title") or "").upper() for k in SKIP_TITLE)),
              events[0] if events else None)
    print("\nevent:", ev["id"], "|", ev.get("title"))
    page = s._page
    page.on("response", lambda r: asyncio.create_task(_cap(r)))

    await s.goto_retry(ev["url"])
    # wait for clear + map to load (map SDK fetches the manifest)
    for i in range(9):
        await page.wait_for_timeout(4000)
        if i == 1:
            for sel in ('#onetrust-accept-btn-handler', 'button:has-text("Accept")'):
                try:
                    el = page.locator(sel).first
                    if await el.count():
                        await el.click(timeout=2500); break
                except Exception:
                    pass
        try:
            await page.mouse.wheel(0, 800)
        except Exception:
            pass

    print(f"\n(1) INTERCEPTED manifest requests: {len(MANIFEST_HITS)}")
    for status, u, n in MANIFEST_HITS:
        print(f"     {status} {n:>7}b  {u[:110]}")

    murl = config.MANIFEST_URL.format(event_id=ev["id"])
    r = await s.page_fetch(murl)
    print(f"\n(2) in-page fetch manifest -> HTTP {r['status']} | {len(r['body'])}b")
    if r["status"] != 200:
        print("     body:", (r["body"] or "")[:120])

    try:
        resp = await page.goto(murl, wait_until="domcontentloaded", timeout=30000)
        txt = await page.content()
        print(f"\n(3) direct goto manifest -> HTTP {resp.status if resp else '?'} | {len(txt)}b")
    except Exception as e:
        print("\n(3) direct goto ->", repr(e)[:100])

    await s.close()


if __name__ == "__main__":
    asyncio.run(main())
