import json, sys, time
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]
R = {"requests": {"total": 0, "non_get": [], "entry_analysis": 0, "binance": 0}, "dom": {}}
def page_for(b, port, w, h):
    ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2 if w == 390 else 1)
    pg = ctx.new_page()
    def on_req(r):
        R["requests"]["total"] += 1
        if r.method != "GET": R["requests"]["non_get"].append({"m": r.method, "u": r.url})
        if "/entry-analysis" in r.url: R["requests"]["entry_analysis"] += 1
        if "/binance/" in r.url: R["requests"]["binance"] += 1
    pg.on("request", on_req)
    pg.route("**/*", lambda route: route.continue_() if route.request.url.startswith(f"http://127.0.0.1:{port}/") else route.abort())
    pg.goto(f"http://127.0.0.1:{port}/crypto-paper")
    pg.wait_for_selector("[data-testid=market-header]", timeout=60000)
    pg.wait_for_selector("[data-testid=candle-chart] canvas", timeout=60000)
    pg.wait_for_selector("[data-testid=btc-context-path-up-row]", timeout=60000)
    return ctx, pg
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx, pg = page_for(b, 4200, 1440, 900)
    pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{OUT}/1440-button-closed.png")
    chart = pg.evaluate("() => { const c=document.querySelector('[data-testid=candle-chart]'); return c ? c.dataset.contextLines : null; }")
    R["dom"]["chart_lines_default"] = chart
    pg.click("[data-testid=entry-analysis-button]"); pg.wait_for_selector("[data-testid=entry-analysis-panel]")
    pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{OUT}/1440-entry-analysis-open.png")
    R["dom"]["verdict"] = pg.text_content("[data-testid=entry-analysis-verdict]")
    R["dom"]["statement"] = pg.text_content("[data-testid=entry-analysis-statement]")
    pg.evaluate("() => { const d=document.querySelector('[data-testid=entry-analysis-panel] details'); if (d) d.open = true; }")
    pg.wait_for_timeout(300)
    pg.screenshot(path=f"{OUT}/1440-entry-analysis-basis.png")
    R["dom"]["basis"] = pg.text_content("[data-testid=entry-analysis-basis]")
    R["dom"]["panel_text"] = pg.text_content("[data-testid=entry-analysis-panel]")
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    # wall panel: R/S hidden by default, detail rows with NEW/CARRIED and gap
    pg.evaluate("() => { const d=document.querySelector('[data-testid=btc-context-strip] details'); if (d) d.open = true; document.querySelector('[data-testid=btc-context-strip]').scrollIntoView({block:'end'}); }")
    pg.wait_for_timeout(500)
    pg.screenshot(path=f"{OUT}/1440-wall-panel-rs-hidden-detail.png")
    R["dom"]["wall_detail"] = [e.text_content() for e in pg.query_selector_all("[data-testid=wall-detail-row]")][:3]
    R["dom"]["structure_pressed"] = pg.evaluate("() => [...document.querySelectorAll('[data-testid=btc-context-structure] button')].map(b => b.textContent + ':' + b.getAttribute('aria-pressed'))")
    ctx.close()
    ctx, pg = page_for(b, 4200, 390, 844)
    pg.click("[data-testid=entry-analysis-button]"); pg.wait_for_selector("[data-testid=entry-analysis-panel]"); pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{OUT}/390-entry-analysis-open.png")
    R["dom"]["overflow_390"] = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    ctx.close(); b.close()
json.dump(R, open(f"{OUT}/qa_entry.json", "w"), indent=1, ensure_ascii=False)
print(json.dumps(R, ensure_ascii=False)[:1500])
