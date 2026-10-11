import glob, os, json
from playwright.sync_api import sync_playwright
chrome = glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux*/chrome"))[0]
os.environ["LD_LIBRARY_PATH"] = os.path.expanduser("~/.local/emulib")
R = {}; blocked = {"on": False, "aborted": 0}
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"]); pg = b.new_page(viewport={"width": 1440, "height": 1000})
    def route(r):
        if blocked["on"]:
            blocked["aborted"] += 1; return r.abort()
        return r.continue_()
    pg.route("**/api/crypto/chart-history*", route)
    pg.goto("http://127.0.0.1:3196/crypto-paper"); pg.wait_for_selector('[data-testid="market-header"]', timeout=60000)
    pg.locator('[data-testid="symbol-tab-BTCUSDT"]').locator("visible=true").first.click()
    pg.locator('[data-testid="timeframe-1m"]').locator("visible=true").first.click(); pg.wait_for_timeout(4000)
    c = lambda: pg.locator('[data-testid="candle-chart"]').locator("visible=true").first
    box = c().bounding_box(); pg.mouse.move(box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.4)
    for _ in range(6): pg.mouse.wheel(0, -300); pg.wait_for_timeout(150)
    pg.wait_for_timeout(800); R["zoomed"] = c().get_attribute("data-visible-bars")
    blocked["on"] = True; pg.wait_for_timeout(32000); R["during_outage"] = c().get_attribute("data-visible-bars")
    blocked["on"] = False; pg.wait_for_timeout(17000); R["after_recovery"] = c().get_attribute("data-visible-bars")
    R["aborted_requests"] = blocked["aborted"]; b.close()
print(json.dumps(R))
