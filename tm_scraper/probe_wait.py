#!/usr/bin/env python3
"""Decisive probe: fresh IP + patience + capture the app's facets DATA.

Answers, in one run:
  1. does a fresh exit IP + waiting long enough clear Kasada's "paused" screen?
  2. what do the app's own facets calls contain (seat-level places + offers?)

Run:  HEADLESS=false python probe_wait.py
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
        return f"<non-json {len(body)}b>"
    facets = d.get("facets", [])
    emb = d.get("_embedded", {})
    offers = emb.get("offer", []) or emb.get("offers", [])
    with_places = sum(1 for f in facets if f.get("places"))
    return (f"facets={len(facets)} with_places={with_places} embedded_offers={len(offers)} "
            f"sample_keys={list(facets[0].keys()) if facets else []}")


async def exit_ip(s):
    r = await s.page_fetch("https://api.ipify.org?format=json")
    try:
        return json.loads(r["body"]).get("ip") if r["status"] == 200 else f"<{r['status']}>"
    except Exception:
        return "<err>"


async def _cap(resp):
    if _is_facets(resp.url):
        try:
            body = await resp.text()
        except Exception:
            body = ""
        RESPS.append((resp.status, resp.url, body))


async def main():
    s = TMSession(headless=config.HEADLESS)
    await s.start()
    print("\ninitial exit IP:", await exit_ip(s))
    print("rotating to a FRESH exit IP before touching an event page...")
    try:
        await s.rotate()
        print("fresh exit IP:", await exit_ip(s))
    except Exception as e:
        print("rotate failed, continuing on current IP:", repr(e)[:80])

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

    print("\npolling the event page (up to 45s):")
    for i in range(9):
        await page.wait_for_timeout(5000)
        content = (await page.content()).lower()
        blocked = "activity" in content and "paused" in content
        print(f"  t={5*(i+1):>2}s  blocked={blocked!s:<5}  facets_seen={len(RESPS)}")
        if len(RESPS) >= 3:
            break

    print(f"\n=== facets captured: {len(RESPS)} ===")
    for status, u, body in RESPS:
        print(f"  {status}  facets{u.split('facets',1)[1][:110]}")
        print(f"       -> {_summ(body)}")

    # --- decisive: does OUR rich show=places fetch work now the page is cleared? ---
    rich = config.FACETS_URL.format(event_id=ev["id"], channel=config.RESALE_CHANNEL,
                                    apikey=config.APIKEY, apisecret=config.APISECRET)
    print("\n=== our rich show=places fetch on the CLEARED page ===")
    a = await s.page_fetch(rich)
    print(f"  in-page fetch -> HTTP {a['status']}")
    if a["status"] == 200:
        print("   ->", _summ(a["body"]))
    else:
        print("   body:", (a["body"] or "")[:140])
    await s.close()


if __name__ == "__main__":
    asyncio.run(main())
