"""Timeframe-switch trace: console traces from the instrumented build + chart-history network timing.

Usage: trace_run.py <url> <out.json> <symbol-tab-testid> <sequence e.g. 1m,15s,1m> [width]
"""
import glob
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

URL, OUT, TAB, SEQ = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].split(",")
WIDTH = int(sys.argv[5]) if len(sys.argv) > 5 else 1440
chrome = glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux*/chrome"))[0]
os.environ["LD_LIBRARY_PATH"] = os.path.expanduser("~/.local/emulib")
events = []
t0 = time.time()


def stamp():
    return round((time.time() - t0) * 1000)


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": WIDTH, "height": 1000})
    page.on("console", lambda m: events.append({"t": stamp(), "kind": "console", "text": m.text})
            if m.text.startswith("[TRACE") else None)
    page.on("request", lambda r: events.append({"t": stamp(), "kind": "req", "url": r.url.split("8199")[-1]})
            if "chart-history" in r.url else None)
    page.on("response", lambda r: events.append({"t": stamp(), "kind": "resp", "status": r.status,
                                                 "url": r.url.split("8199")[-1]})
            if "chart-history" in r.url else None)
    page.goto(URL)
    page.wait_for_selector('[data-testid="market-header"]', timeout=60000)
    page.locator(f'[data-testid="{TAB}"]').locator("visible=true").first.click()
    page.wait_for_timeout(1500)
    snaps = []
    for frame in SEQ:
        events.append({"t": stamp(), "kind": "click", "frame": frame})
        page.locator(f'[data-testid="timeframe-{frame}"]').locator("visible=true").first.click()
        for _ in range(16):
            page.wait_for_timeout(500)
            c = page.locator('[data-testid="candle-chart"]').locator("visible=true").first
            snaps.append({"t": stamp(), "frame": frame, "visible_bars": c.get_attribute("data-visible-bars"),
                          "price_span": c.get_attribute("data-price-span")})
    page.screenshot(path=OUT.replace(".json", ".png"))
    browser.close()
json.dump({"events": events, "snaps": snaps}, open(OUT, "w"), indent=1)
for e in sorted(events, key=lambda e: e["t"]):
    print(e["t"], e["kind"], e.get("frame") or e.get("url") or "", (e.get("text") or "")[:400], e.get("status") or "")
print("final snaps per frame:", [(s["frame"], s["visible_bars"]) for s in snaps[15::16]])
