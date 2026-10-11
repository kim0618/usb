import glob, os, json
from playwright.sync_api import sync_playwright
chrome = glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux*/chrome"))[0]
os.environ["LD_LIBRARY_PATH"] = os.path.expanduser("~/.local/emulib")
R = {}
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"]); pg = b.new_page(viewport={"width": 1440, "height": 1000})
    pg.goto("http://127.0.0.1:3299/crypto-paper"); pg.wait_for_selector('[data-testid="market-header"]', timeout=60000)
    pg.locator('[data-testid="symbol-tab-BTCUSDT"]').locator("visible=true").first.click()
    pg.locator('[data-testid="timeframe-1m"]').locator("visible=true").first.click(); pg.wait_for_timeout(4000)
    c = lambda: pg.locator('[data-testid="candle-chart"]').locator("visible=true").first
    R["initial"] = c().get_attribute("data-visible-bars")
    box = c().bounding_box(); x, y = box["x"] + box["width"] * 0.6, box["y"] + box["height"] * 0.4
    pg.mouse.move(x, y)
    for _ in range(6): pg.mouse.wheel(0, -300); pg.wait_for_timeout(150)
    pg.wait_for_timeout(800); R["zoomed"] = c().get_attribute("data-visible-bars")
    pg.mouse.down(); pg.mouse.move(x + 250, y, steps=10); pg.mouse.up(); pg.wait_for_timeout(800)
    R["panned_bars"] = c().get_attribute("data-visible-bars")
    pg.wait_for_timeout(int(os.environ.get("WAIT_MS", "16000")))
    R["after_tail_refresh"] = c().get_attribute("data-visible-bars")
    pg.locator('[data-testid="timeframe-15s"]').locator("visible=true").first.click(); pg.wait_for_timeout(3000)
    pg.locator('[data-testid="timeframe-1m"]').locator("visible=true").first.click(); pg.wait_for_timeout(4000)
    R["new_entry_after_15s"] = c().get_attribute("data-visible-bars")
    pg.reload(); pg.wait_for_selector('[data-testid="market-header"]', timeout=60000); pg.wait_for_timeout(5000)
    R["after_reload"] = {"frame": pg.locator('[aria-pressed=true][data-testid^="timeframe-"]').evaluate_all("e => e.map(x => x.dataset.testid)"), "bars": c().get_attribute("data-visible-bars")}
    b.close()
print(json.dumps(R))
