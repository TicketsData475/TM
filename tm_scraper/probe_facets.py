#!/usr/bin/env python3
"""Facets probe v3: compare the page's OWN facets request vs OUR in-page fetch.

Key question: does Kasada sign our programmatic fetch (x-kpsdk-* headers)? If yes,
the CORS failure is a header issue we can fix. If our fetch lacks those headers,
Kasada only signs the app's own calls -> we must intercept/trigger them.

Run:  HEADLESS=false python probe_facets.py
"""
import asyncio
import json

import config
from browser_session import TMSession

SKIP_TITLE = ("HALF PRICE", "PARKING", "HOSPITALITY", "SUITE", "PACKAGE")
REQ_HEADERS = {}     # url -> request headers
RESPS = []           # (status, url, body)
WATCH = ("x-kpsdk-ct", "x-kpsdk-h", "x-kpsdk-v", "x-kpsdk-r",
         "authorization", "tmps-correlation-id", "origin", "referer", "accept")


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
    return f"facets={len(facets)} with_places={with_places} embedded_offers={len(offers)}"


def _show_headers(h):
    return {k: (v[:22] + "..." if len(v) > 25 else v) for k, v in h.items() if k.lower() in WATCH}


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
    page = s._page

    async def on_request(req):
        if _is_facets(req.url):
            try:
                REQ_HEADERS[req.url] = await req.all_headers()
            except Exception:
                pass

    async def on_response(resp):
        if _is_facets(resp.url):
            try:
                body = await resp.text()
            except Exception:
                body = ""
            RESPS.append((resp.status, resp.url, body))

    page.on("request", on_request)
    page.on("response", on_response)

    try:
        await page.goto(ev["url"], wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        print("goto:", repr(e)[:120])
    # give the seatmap time to mount + fire its facets (seen ~15s), nudge it
    for _ in range(4):
        await page.wait_for_timeout(6000)
        try:
            await page.mouse.wheel(0, 1200)
        except Exception:
            pass
        if RESPS:
            break

    print(f"\n=== PAGE'S OWN facets calls: {len(RESPS)} ===")
    for status, u, body in RESPS:
        print(f"  {status}  ...facets{u.split('facets',1)[1][:80]}")
        print(f"      resp: {_summ(body)}")
        print(f"      req headers: {_show_headers(REQ_HEADERS.get(u, {}))}")

    # now OUR fetch of the rich url, and capture what headers it actually sent
    rich = config.FACETS_URL.format(event_id=ev["id"], channel=config.RESALE_CHANNEL,
                                    apikey=config.APIKEY, apisecret=config.APISECRET)
    a = await s.page_fetch(rich)
    print(f"\n=== OUR in-page fetch (rich url) -> HTTP {a['status']} ===")
    print("  our req headers:", _show_headers(REQ_HEADERS.get(rich, {})))
    if a["status"] == 200:
        print("  resp:", _summ(a["body"]))
    else:
        print("  body:", (a["body"] or "")[:120])

    await s.close()


if __name__ == "__main__":
    asyncio.run(main())
