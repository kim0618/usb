import glob, os, json
from playwright.sync_api import sync_playwright
chrome = glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux*/chrome"))[0]
os.environ["LD_LIBRARY_PATH"] = os.path.expanduser("~/.local/emulib")
R = {}
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
    for w in (390, 1440, 1600):
        pg = b.new_page(viewport={"width": w, "height": 950}); writes = []
        pg.on("request", lambda r: writes.append(r.method) if r.method not in ("GET", "OPTIONS", "HEAD") else None)
        pg.goto("http://127.0.0.1:3299/crypto-paper"); pg.wait_for_selector('[data-testid="market-header"]', timeout=60000)
        out = {}
        for tab in ("symbol-tab-BTCUSDT", "symbol-tab-hot"):
            pg.locator(f'[data-testid="{tab}"]').locator("visible=true").first.click(); pg.wait_for_timeout(1500)
            for f in ("1m", "15s", "1m"):
                pg.locator(f'[data-testid="timeframe-{f}"]').locator("visible=true").first.click(); pg.wait_for_timeout(5000)
            c = pg.locator('[data-testid="candle-chart"]').locator("visible=true").first
            out[tab] = {"selected": pg.locator('[role=tab][aria-selected=true]').evaluate_all("e => e.map(x => x.dataset.symbol)"),
                        "timeframe": pg.locator('[aria-pressed=true][data-testid^="timeframe-"]').evaluate_all("e => e.map(x => x.dataset.testid)"),
                        "visible_bars": c.get_attribute("data-visible-bars"),
                        "overflow": pg.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")}
            pg.screenshot(path=os.environ["OUT"] + f"/{w}_{tab}_1m_after_15s.png")
        pg.reload(); pg.wait_for_selector('[data-testid="market-header"]', timeout=60000); pg.wait_for_timeout(5000)
        c = pg.locator('[data-testid="candle-chart"]').locator("visible=true").first
        out["reload"] = {"selected": pg.locator('[role=tab][aria-selected=true]').evaluate_all("e => e.map(x => x.dataset.symbol)"),
                         "visible_bars": c.get_attribute("data-visible-bars")}
        out["writes"] = writes; R[str(w)] = out; pg.close()
    b.close()
print(json.dumps(R, ensure_ascii=False))
