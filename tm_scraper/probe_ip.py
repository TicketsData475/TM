#!/usr/bin/env python3
"""Probe: is the proxy's exit IP STABLE across the browser session?

Kasada binds its token to the IP that solved the challenge. If the proxy rotates
the exit IP mid-session, Kasada pauses the page. This fetches the exit IP several
times (start, rapid burst, after event-page load) and reports whether it changes.

Run:  HEADLESS=false python probe_ip.py
"""
import asyncio
import json

import config
from browser_session import TMSession

SKIP_TITLE = ("HALF PRICE", "PARKING", "HOSPITALITY", "SUITE", "PACKAGE")


async def exit_ip(s):
    r = await s.page_fetch("https://api.ipify.org?format=json")
    if r["status"] == 200:
        try:
            return json.loads(r["body"]).get("ip")
        except Exception:
            return r["body"][:40]
    return f"<HTTP {r['status']}>"


async def main():
    s = TMSession(headless=config.HEADLESS)
    await s.start()
    print("\nclaimed proxy gateway:",
          s.current_proxy() and f"{s.current_proxy().host}:{s.current_proxy().port}")

    ip0 = await exit_ip(s)
    print("exit IP right after Kasada solve:", ip0)

    print("rapid burst (per-connection rotation?):")
    burst = await asyncio.gather(*[exit_ip(s) for _ in range(5)])
    print("  ", burst)

    events = await s.discover_events(max_pages=1)
    ev = next((e for e in events if not e.get("isPartner")
               and not any(k in (e.get("title") or "").upper() for k in SKIP_TITLE)),
              events[0] if events else None)
    if ev:
        try:
            await s._page.goto(ev["url"], wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print("goto:", repr(e)[:100])
        await s._page.wait_for_timeout(8000)
        blk = "activity" in (await s._page.content()).lower()
        print(f"\nafter event-page load: blocked={blk}")
        ip1 = await exit_ip(s)
        print("exit IP after event page:", ip1)
        print("\n==> IP stable across session:", ip0 == ip1 and all(x == ip0 for x in burst))
    await s.close()


if __name__ == "__main__":
    asyncio.run(main())
