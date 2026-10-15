import json, sys, time
from playwright.sync_api import sync_playwright

OUT = sys.argv[1]
res = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 1000})
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)
    page.goto("http://127.0.0.1:18080/crypto-paper", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_selector('[data-testid="candle-chart"]', timeout=90000)
    page.wait_for_function("() => document.body.innerText.includes('0.064')", timeout=60000)
    time.sleep(20)  # let the normal polling cycles run (C1 used to poll every 15 s)
    txt = page.inner_text("body")
    res["paper"] = {
        "has_SHORT": "SHORT" in txt or "숏" in txt, "has_qty_0.064": "0.064" in txt,
        "has_entry": "83,612.9" in txt or "83612.9" in txt,
        "c1_testids": page.locator('[data-testid*="c1"]').count(),
        "text_C1": [w for w in ("C1 AUTO", "C1 신호", "C1x") if w in txt],
        "btc_context_strip": page.locator('[data-testid="btc-context-strip"]').count(),
        "hot_text": "HOT" in txt, "source_switch": page.locator('[data-testid="account-source-switch"]').count(),
        "chart_visible_bars": page.locator('[data-testid="candle-chart"]').first.get_attribute("data-visible-bars"),
    }
    page.screenshot(path=OUT + "/paper.png", full_page=False)
    t_live = time.time()
    live_btn = page.locator('[data-testid="source-BINANCE_LIVE"]')
    res["live_button_enabled"] = live_btn.count() > 0 and live_btn.is_enabled()
    if res["live_button_enabled"]:
        live_btn.click()
        try:
            page.wait_for_function("() => document.body.innerText.includes('0.108')", timeout=15000)
        except Exception as e:
            res["live_wait_error"] = str(e)[:120]
        time.sleep(3)
        txt = page.inner_text("body")
        res["live"] = {"has_LONG": "LONG" in txt or "롱" in txt, "has_qty_0.108": "0.108" in txt,
                       "has_entry": "83,913.1" in txt or "83913.1" in txt,
                       "c1_testids": page.locator('[data-testid*="c1"]').count(),
                       "btc_context_strip": page.locator('[data-testid="btc-context-strip"]').count(),
                       "chart": page.locator('[data-testid="candle-chart"]').count(),
                       "seconds_on_live": round(time.time() - t_live, 1)}
        page.screenshot(path=OUT + "/live.png", full_page=False)
        page.locator('[data-testid="source-PAPER"]').click()  # leave LIVE before closing
        time.sleep(1)
    res["console_errors"] = errors[:15]
    b.close()
print(json.dumps(res, ensure_ascii=False, indent=1))
