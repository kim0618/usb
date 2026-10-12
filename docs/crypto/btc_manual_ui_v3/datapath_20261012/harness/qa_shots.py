"""V3 data-path preview QA: screenshots + DOM extraction (stage E/F) through the caching GET-only proxies.
usage: qa_shots.py OUT_DIR  (proxies: 3100 REAL paper (real SHORT), 3200 FLAT, 3300 LONG 0.108@83913.1 no store,
                             3400 LONG sim with fixture store allowed)"""
import json, sys, time
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]
R = {"shots": {}, "dom": {}, "requests": {"total": 0, "non_get": [], "binance": 0}}
STRIP = """() => { const s=document.querySelector('[data-testid=btc-context-strip]'); if(!s) return null;
  const rows = id => [...s.querySelectorAll(`[data-testid=${id}-row]`)].map(r => ({price: r.dataset.price,
     primary: r.querySelector('[data-testid=path-primary]')?.textContent, gap: r.querySelector('[data-testid=path-gap]')?.textContent}));
  const t = id => s.querySelector(`[data-testid=${id}]`)?.textContent ?? null;
  return {mode: s.dataset.mode, up: rows('btc-context-path-up'), down: rows('btc-context-path-down'),
    upCount: t('btc-context-path-up-count'), downCount: t('btc-context-path-down-count'),
    upEmpty: t('btc-context-path-up-empty'), downEmpty: t('btc-context-path-down-empty'),
    current: t('btc-context-current'), entry: t('btc-context-entry'), vsEntry: t('btc-context-current-vs-entry'),
    flow: t('btc-context-flow'), oi: t('btc-context-oi'), funding: t('btc-context-funding'),
    entryDetail: t('btc-context-entry-detail'), currentDetail: t('btc-context-current-detail'),
    text: s.innerText}; }"""
CHART = """() => { const c=document.querySelector('[data-testid=candle-chart]'); if(!c) return null;
  return {lines: c.dataset.contextLines||'', bars: c.dataset.visibleBars, span: c.dataset.priceSpan,
    tags: [...c.querySelectorAll('[data-testid=context-tags] span')].map(e => e.textContent)}; }"""
OVER = """() => ({doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  overlap: [...document.querySelectorAll('[data-testid$=-row]')].filter(r => {
    const kids=[...r.children].map(k=>{const g=document.createRange(); g.selectNodeContents(k); return g.getBoundingClientRect();}).filter(b=>b.width>0);
    for (let i=1;i<kids.length;i++) if (kids[i].left < kids[i-1].right - 0.5) return true;
    return r.scrollWidth > r.clientWidth + 0.5; }).length})"""

def page_for(b, port, w, h):
    ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2 if w == 390 else 1)
    pg = ctx.new_page()
    def on_req(r):
        R["requests"]["total"] += 1
        if r.method != "GET": R["requests"]["non_get"].append({"m": r.method, "u": r.url})
        if "/binance/" in r.url: R["requests"]["binance"] += 1
    pg.on("request", on_req)
    pg.route("**/*", lambda route: route.continue_() if route.request.url.startswith(f"http://127.0.0.1:{port}/") else route.abort())
    pg.goto(f"http://127.0.0.1:{port}/crypto-paper")
    pg.wait_for_selector("[data-testid=market-header]", timeout=60000)
    pg.wait_for_selector("[data-testid=candle-chart] canvas", timeout=60000)
    return ctx, pg

def wait_strip(pg, want_rows=True, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        s = pg.evaluate(STRIP)
        if s and (not want_rows or s["up"] or s["upEmpty"]): return s
        pg.wait_for_timeout(500)
    return pg.evaluate(STRIP)

def shot(pg, name, full=False):
    pg.screenshot(path=f"{OUT}/{name}.png", full_page=full)
    R["shots"][name] = {"strip": pg.evaluate(STRIP), "chart": pg.evaluate(CHART), "over": pg.evaluate(OVER)}
    print("shot", name, "lines", (R["shots"][name]["chart"] or {}).get("lines"), "over", R["shots"][name]["over"], flush=True)

def open_detail(pg):
    pg.evaluate("() => { const d=document.querySelector('[data-testid=btc-context-strip] details'); if (d) d.open = true; }")
    pg.wait_for_timeout(300)

def click_text(pg, text):
    pg.click(f"[data-testid=btc-context-strip] button:has-text('{text}')")
    pg.wait_for_timeout(1200)

def scroll_to_strip(pg):
    pg.evaluate("() => document.querySelector('[data-testid=btc-context-strip]').scrollIntoView({block:'end'})")
    pg.wait_for_timeout(300)

with sync_playwright() as p:
    b = p.chromium.launch()
    # 3. / 4. no position, nearest vs largest (FLAT rewrite of PAPER)
    ctx, pg = page_for(b, 4200, 1440, 900); wait_strip(pg); scroll_to_strip(pg)
    shot(pg, "1440-no-position-nearest")
    click_text(pg, "큰 순"); shot(pg, "1440-no-position-largest")
    click_text(pg, "가까운 순")
    # 7. hierarchy expanded + 12. R/S A/B
    open_detail(pg); scroll_to_strip(pg); shot(pg, "1440-wall-hierarchy-expanded")
    shot(pg, "1440-rs-annotation-B")
    click_text(pg, "숨김"); pg.evaluate("() => window.scrollTo(0,0)"); pg.wait_for_timeout(500); shot(pg, "1440-rs-hidden-A")
    click_text(pg, "참고선")
    for tf in ["15s", "10m", "1h", "4h"]:
        pg.click(f"[data-testid=timeframe-{tf}]")
        pg.wait_for_timeout(2500); pg.evaluate("() => window.scrollTo(0,0)"); shot(pg, f"tf-{tf}-1440")
    pg.click("[data-testid=timeframe-1m]"); pg.wait_for_timeout(1500)
    ctx.close()
    # 5. real PAPER SHORT (REAL mode, no store -> entry --)
    ctx, pg = page_for(b, 4100, 1440, 900); wait_strip(pg); scroll_to_strip(pg); shot(pg, "1440-short-paper-real")
    open_detail(pg); scroll_to_strip(pg); shot(pg, "1440-short-paper-real-expanded"); ctx.close()
    # 8. LIVE LONG 0.108@83913.1 simulated on the PAPER state, no recorder store -> entry unavailable
    ctx, pg = page_for(b, 4300, 1440, 900); wait_strip(pg); scroll_to_strip(pg); shot(pg, "1440-live-long-entry-unavailable")
    open_detail(pg); scroll_to_strip(pg); shot(pg, "1440-live-long-entry-unavailable-expanded"); ctx.close()
    # 6. LONG simulated with a fixture store (debug: --allow-fixture-entry), labelled 검증용
    ctx, pg = page_for(b, 4400, 1440, 900); wait_strip(pg); scroll_to_strip(pg); shot(pg, "1440-long-sim-fixture")
    open_detail(pg); scroll_to_strip(pg); shot(pg, "1440-long-sim-fixture-expanded"); ctx.close()
    # 10. 390 position (real PAPER SHORT)
    ctx, pg = page_for(b, 4100, 390, 844); wait_strip(pg); scroll_to_strip(pg); shot(pg, "390-position")
    open_detail(pg); scroll_to_strip(pg); shot(pg, "390-position-expanded"); shot(pg, "390-full", full=True); ctx.close()
    # 11. 1600 full
    ctx, pg = page_for(b, 4200, 1600, 1000); wait_strip(pg); open_detail(pg); shot(pg, "1600-full", full=True); ctx.close()
    # 9. no-wall fallback: proxy 3500 serves a snapshot with NONE sides (feeder variant)
    try:
        ctx, pg = page_for(b, 4500, 1440, 900); s = wait_strip(pg); scroll_to_strip(pg); shot(pg, "1440-no-wall-fallback"); ctx.close()
    except Exception as e:
        R["shots"]["1440-no-wall-fallback"] = {"error": str(e)}
    b.close()
json.dump(R, open(f"{OUT}/qa_shots.json", "w"), indent=1, ensure_ascii=False)
print("requests", R["requests"]["total"], "non_get", R["requests"]["non_get"], "binance", R["requests"]["binance"])
