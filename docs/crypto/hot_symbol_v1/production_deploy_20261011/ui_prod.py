"""Post-deploy UI QA: the deployed artifact on production data through a GET-only proxy."""
import glob
import json
import os
import sys

from playwright.sync_api import sync_playwright

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
URL = "http://127.0.0.1:3299/crypto-paper"
chrome = glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux*/chrome"))[0]
os.environ["LD_LIBRARY_PATH"] = os.path.expanduser("~/.local/emulib")
R = {}


def vis(page, tid):
    return page.locator(f'[data-testid="{tid}"]').locator("visible=true").first


def overflow(page):
    return page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
    for width in (390, 1440, 1600):
        ctx = browser.new_context(viewport={"width": width, "height": 950})
        ctx.route("**/*", lambda route: route.continue_() if route.request.url.startswith("http://127.0.0.1:3299")
                  else route.abort())
        page = ctx.new_page()
        writes = []
        page.on("request", lambda r: writes.append([r.method, r.url]) if r.method not in ("GET", "OPTIONS", "HEAD") else None)
        page.goto(URL)
        page.wait_for_selector('[data-testid="market-header"]', timeout=60000)
        page.wait_for_timeout(2500)
        r = {"tabs": page.locator("[role=tab]").evaluate_all(
                 "e => e.map(x => [x.dataset.testid, x.dataset.symbol, x.innerText.replace(/\\n/g,' '), x.scrollWidth <= x.clientWidth + 1])"),
             "scan_button": vis(page, "hot-scan-button").inner_text(),
             "overflow": overflow(page)}
        vis(page, "symbol-tab-hot").click()
        page.wait_for_function("() => document.querySelector('[data-testid=symbol-tab-hot]').getAttribute('aria-selected') === 'true'")
        page.wait_for_timeout(3000)
        r["selected"] = page.locator("[role=tab][aria-selected=true]").evaluate_all("e => e.map(x => x.dataset.symbol)")
        r["header"] = vis(page, "market-header").inner_text()[:60].replace("\n", " ")
        r["mark"] = vis(page, "mark-price").inner_text()
        r["paper_overflow"] = overflow(page)
        page.screenshot(path=f"{OUT}/{width}_paper_hot.png")
        page.reload(); page.wait_for_selector('[data-testid="market-header"]', timeout=60000); page.wait_for_timeout(2500)
        r["selected_after_reload"] = page.locator("[role=tab][aria-selected=true]").evaluate_all("e => e.map(x => x.dataset.symbol)")
        # LIVE on the HOT symbol: order controls must be closed while the switch is off.
        vis(page, "source-BINANCE_LIVE").click()
        page.wait_for_selector('[data-testid="live-hot-orders-off"]', timeout=30000)
        page.wait_for_timeout(2000)
        r["live"] = {
            "selected": page.locator("[role=tab][aria-selected=true]").evaluate_all("e => e.map(x => x.dataset.symbol)"),
            "long_disabled": vis(page, "live-long").is_disabled(),
            "short_disabled": vis(page, "live-short").is_disabled(),
            "close_disabled": vis(page, "live-close").is_disabled(),
            "hot_off_note": vis(page, "live-hot-orders-off").inner_text(),
            "activate_offered": page.locator('[data-testid="live-order-activate"]').count(),
            "leverage_enabled": page.locator('[data-testid^="live-leverage-"]:not([data-testid="live-leverage-panel"]):not([data-testid="live-leverage-options"]):enabled').count(),
            "leverage_total": page.locator('[data-testid^="live-leverage-"]:not([data-testid="live-leverage-panel"]):not([data-testid="live-leverage-options"])').count(),
            "overflow": overflow(page),
        }
        page.screenshot(path=f"{OUT}/{width}_live_hot.png")
        # BTC under LIVE stays as before (closed only because the session is not armed).
        vis(page, "symbol-tab-BTCUSDT").click()
        page.wait_for_timeout(3000)
        r["live_btc"] = {"hot_off_note": page.locator('[data-testid="live-hot-orders-off"]').count(),
                         "locked_note": page.locator('[data-testid="live-order-locked"]').count(),
                         "long_disabled": vis(page, "live-long").is_disabled()}
        vis(page, "source-PAPER").click()
        page.wait_for_timeout(1000)
        r["browser_writes"] = writes
        R[str(width)] = r
        ctx.close()
    browser.close()
json.dump(R, open(f"{OUT}/report.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(R, ensure_ascii=False, indent=1))
