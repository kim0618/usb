import glob, os, json
from playwright.sync_api import sync_playwright
chrome = glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux*/chrome"))[0]
os.environ["LD_LIBRARY_PATH"] = os.path.expanduser("~/.local/emulib")
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"]); pg = b.new_page(viewport={"width": 1440, "height": 1000})
    pg.goto("http://127.0.0.1:3197/crypto-paper"); pg.wait_for_selector('[data-testid="market-header"]', timeout=60000)
    pg.locator('[data-testid="symbol-tab-BTCUSDT"]').locator("visible=true").first.click(); pg.wait_for_timeout(1500)
    out = {}
    for frame in ("1m", "15s", "1m"):
        pg.locator(f'[data-testid="timeframe-{frame}"]').locator("visible=true").first.click()
        for t in (2, 6):
            pg.wait_for_timeout(2000 if t == 2 else 4000)
            c = pg.locator('[data-testid="candle-chart"]').locator("visible=true").first
            out.setdefault(frame, []).append((t, c.get_attribute("data-visible-bars"), c.get_attribute("data-price-span")))
    pg.screenshot(path=os.environ["SHOT"]); print(json.dumps(out)); b.close()
