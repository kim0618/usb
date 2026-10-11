"""Sub-dollar HOT chart + Safe MAX QA. Usage: ui_subdollar.py <url> <outdir> [widths]"""
import glob
import json
import os
import sys

from playwright.sync_api import sync_playwright

URL, OUT = sys.argv[1], sys.argv[2]
WIDTHS = [int(w) for w in (sys.argv[3] if len(sys.argv) > 3 else "390,1440,1600").split(",")]
os.makedirs(OUT, exist_ok=True)
chrome = glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux*/chrome"))[0]
os.environ["LD_LIBRARY_PATH"] = os.path.expanduser("~/.local/emulib")
host = URL.split("/crypto-paper")[0]
R = {}


def vis(page, tid):
    return page.locator(f'[data-testid="{tid}"]').locator("visible=true").first


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
    for width in WIDTHS:
        ctx = browser.new_context(viewport={"width": width, "height": 1000})
        ctx.route("**/*", lambda route: route.continue_()
                  if route.request.url.startswith(host) or "127.0.0.1" in route.request.url else route.abort())
        page = ctx.new_page()
        writes = []
        page.on("request", lambda r: writes.append([r.method, r.url]) if r.method not in ("GET", "OPTIONS", "HEAD") else None)
        page.goto(URL)
        page.wait_for_selector('[data-testid="market-header"]', timeout=60000)
        page.wait_for_timeout(2000)
        out = {}
        for tab in ("symbol-tab-BTCUSDT", "symbol-tab-hot"):
            vis(page, tab).click()
            page.wait_for_function(f"() => document.querySelector('[data-testid={tab}]').getAttribute('aria-selected') === 'true'")
            page.wait_for_timeout(3500)
            sym = vis(page, tab).get_attribute("data-symbol")
            row = {"mark": vis(page, "mark-price").inner_text()}
            for frame in ("15s", "1m", "1h"):
                vis(page, f"timeframe-{frame}").click()
                page.wait_for_timeout(4000)
                chart = vis(page, "candle-chart")
                row[frame] = {"price_span": chart.get_attribute("data-price-span"),
                              "visible_bars": chart.get_attribute("data-visible-bars"),
                              "profile": chart.get_attribute("data-profile")}
                if frame == "15s":
                    page.screenshot(path=f"{OUT}/{width}_{sym}_15s.png")
            ticket = vis(page, "order-ticket")
            row["presets_enabled"] = {lab: not vis(page, f"preset-{lab}").is_disabled() for lab in ("25%", "HALF", "75%", "MAX")}
            text = ticket.inner_text()
            row["safe_max_text"] = [line for line in text.splitlines() if "안전 최대" in line][:2]
            row["overflow"] = page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")
            out[sym] = row
        out["browser_writes"] = writes
        R[str(width)] = out
        ctx.close()
    browser.close()
json.dump(R, open(f"{OUT}/report.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(R, ensure_ascii=False, indent=1))
