"""Production QA for the wall + flow horizon UI (2026-10-12). The deployed artifact (same BUILD_ID)
served locally behind a GET-only caching proxy: /crypto-api is fetched from production 8100 at most
once per URL (/binance refused), /btc-context-api goes live to production 8014 (GET only, unsigned).
usage: qa_prod.py OUT_DIR PORT"""
import json, sys, time
from playwright.sync_api import sync_playwright
OUT, PORT = sys.argv[1], int(sys.argv[2])
R = {"shots": {}, "requests": {"total": 0, "non_get": [], "binance": 0, "btcctx": 0}, "compare": [], "hot": {}, "reload": {}}
LAST = {"snap": None}
ROWS = """() => { const s=document.querySelector('[data-testid=btc-context-strip]'); if(!s) return null;
  return {rows: ['10m','1h','4h','1d'].map(k=>{const r=s.querySelector(`[data-testid=flow-horizon-${k}]`);
    return {k, text:r.querySelector(`[data-testid=flow-horizon-${k}-text]`).textContent, lean:r.dataset.lean, buy:r.dataset.buy, sell:r.dataset.sell}}),
    selInHeader: !!s.querySelector('[data-testid=btc-context-selection]') && !s.querySelector('[data-testid=btc-context-detail] [data-testid=btc-context-selection]'),
    advisor: !!document.querySelector('[data-testid^=entry-analysis]'), pressure: !!s.querySelector('[data-testid^=btc-context-pressure]'),
    open: s.querySelector('details')?.open, text: s.innerText}; }"""
CHART = """() => { const c=document.querySelector('[data-testid=candle-chart]'); if(!c) return null;
  const tags=[...c.querySelectorAll('[data-testid=context-tags] span')]; const rc=tags.map(e=>e.getBoundingClientRect());
  let ov=0; for(let i=0;i<rc.length;i++)for(let j=i+1;j<rc.length;j++){const a=rc[i],b=rc[j]; if(a.left<b.right&&b.left<a.right&&a.top<b.bottom&&b.top<a.bottom)ov++}
  const cr=c.getBoundingClientRect(); return {box:[Math.round(cr.width),Math.round(cr.height)], bars:c.dataset.visibleBars, span:c.dataset.priceSpan,
  lines:c.dataset.contextLines, tags:tags.map(e=>({t:e.textContent,o:e.dataset.order,c:getComputedStyle(e).color})), tagOverlap:ov}; }"""
GEOM = """() => { const t=[...document.querySelectorAll('[data-testid=order-ticket]')].find(e=>e.getBoundingClientRect().width>0);
  const r=t&&t.getBoundingClientRect(); return {ticket:r?[Math.round(r.left+scrollX),Math.round(r.top+scrollY),Math.round(r.width),Math.round(r.height)]:null,
  overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth, bodyOverflow:document.body.scrollWidth-document.body.clientWidth}; }"""

def expected(snap):
    out = []
    fh = (snap or {}).get("flow_horizons") or {}
    for k in ("10m", "1h", "4h", "1d"):
        h = (fh.get("horizons") or {}).get(k) or {}
        if not fh.get("live") or h.get("lean") is None: out.append("--"); continue
        out.append(f"매수 {h['buy_pct']}%" if h["lean"] == "BUY" else f"매도 {h['sell_pct']}%" if h["lean"] == "SELL" else "중립")
    return out

def page_for(b, w, h):
    ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2 if w == 390 else 1)
    pg = ctx.new_page()
    def on_req(r):
        R["requests"]["total"] += 1
        if r.method != "GET": R["requests"]["non_get"].append({"m": r.method, "u": r.url})
        if "/binance/" in r.url: R["requests"]["binance"] += 1
        if "/btc-context-api/" in r.url: R["requests"]["btcctx"] += 1
    def on_resp(r):
        if r.url.endswith("/btc-context-api/snapshot") and r.status == 200:
            try: LAST["snap"] = r.json()
            except Exception: pass
    pg.on("request", on_req); pg.on("response", on_resp)
    pg.route("**/*", lambda route: route.fallback() if route.request.url.startswith(f"http://127.0.0.1:{PORT}/") else route.abort())
    t0 = time.time(); pg.goto(f"http://127.0.0.1:{PORT}/crypto-paper")
    pg.wait_for_selector("[data-testid=market-header]", timeout=90000); pg.wait_for_selector("[data-testid=candle-chart] canvas", timeout=90000)
    return ctx, pg, t0

def until_values(pg, t0, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        s = pg.evaluate(ROWS)
        if s and all(r["text"] != "--" for r in s["rows"]): return round((time.time() - t0) * 1000)
        pg.wait_for_timeout(150)
    return None

def compare(pg, name):
    for _ in range(20):
        s = pg.evaluate(ROWS); exp = expected(LAST["snap"]); ui = [r["text"] for r in s["rows"]]
        if ui == exp: break
        pg.wait_for_timeout(300)
    R["compare"].append({"at": name, "ui": ui, "api": exp, "equal": ui == exp,
                         "api_raw": {k: {x: v.get(x) for x in ("buy_notional", "sell_notional", "buy_pct", "sell_pct", "lean", "coverage_ratio")}
                                     for k, v in ((LAST["snap"] or {}).get("flow_horizons") or {}).get("horizons", {}).items()}})
    print("compare", name, ui, exp, ui == exp, flush=True)

def scroll(pg):
    pg.evaluate("() => document.querySelector('[data-testid=btc-context-strip]')?.scrollIntoView({block:'end'})"); pg.wait_for_timeout(300)
def details(pg, o):
    pg.evaluate("(o)=>{const d=document.querySelector('[data-testid=btc-context-strip] details'); if(d) d.open=o}", o); pg.wait_for_timeout(300)
def shot(pg, name, full=False):
    pg.screenshot(path=f"{OUT}/{name}.png", full_page=full)
    R["shots"][name] = {"strip": pg.evaluate(ROWS), "chart": pg.evaluate(CHART), "geom": pg.evaluate(GEOM)}
    g = R["shots"][name]; print("shot", name, g["geom"], "tags", [t["t"] for t in g["chart"]["tags"]], "ov", g["chart"]["tagOverlap"], flush=True)
def layout(pg):
    base = pg.evaluate(GEOM); c0 = pg.evaluate(CHART)
    details(pg, True); a = pg.evaluate(GEOM); c1 = pg.evaluate(CHART); details(pg, False)
    pg.evaluate("()=>{document.querySelector('[data-testid=btc-context-strip]').style.display='none'}"); pg.wait_for_timeout(300)
    n = pg.evaluate(GEOM); c2 = pg.evaluate(CHART)
    pg.evaluate("()=>{document.querySelector('[data-testid=btc-context-strip]').style.display=''}"); pg.wait_for_timeout(300)
    d = lambda x: [x["ticket"][i] - base["ticket"][i] for i in range(4)]
    return {"ticket_delta_details_open": d(a), "ticket_delta_strip_hidden": d(n), "overflow": [base["overflow"], a["overflow"], base["bodyOverflow"]],
            "chart": [[c["box"], c["bars"], c["span"]] for c in (c0, c1, c2)]}

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx, pg, t0 = page_for(b, 1440, 1000)
    R["reload"]["first_ms_from_goto"] = until_values(pg, t0)
    compare(pg, "1440-first")
    t1 = time.time(); pg.reload(); pg.wait_for_selector("[data-testid=candle-chart] canvas", timeout=90000)
    R["reload"]["reload_ms_from_reload"] = until_values(pg, t1)
    compare(pg, "1440-after-reload")
    scroll(pg); shot(pg, "1-1440-production"); R["layout_1440"] = layout(pg)
    details(pg, True); scroll(pg); shot(pg, "2-1440-production-details"); details(pg, False)
    # HOT isolation: 15 s on HOT, then back
    before = R["requests"]["btcctx"]
    pg.click("[data-testid=symbol-tab-hot]"); pg.wait_for_timeout(1500)
    R["hot"]["strip_present"] = pg.evaluate("()=>!!document.querySelector('[data-testid=btc-context-strip]')")
    mid = R["requests"]["btcctx"]; pg.wait_for_timeout(15000)
    R["hot"]["btcctx_requests_during_15s"] = R["requests"]["btcctx"] - mid
    R["hot"]["chart_context_lines"] = pg.evaluate("()=>document.querySelector('[data-testid=candle-chart]')?.dataset.contextLines||''")
    shot(pg, "3-1440-hot-tab")
    pg.click("[data-testid=symbol-tab-BTCUSDT]"); pg.wait_for_timeout(500)
    R["hot"]["back_on_btc_ms"] = until_values(pg, time.time())
    ctx.close()
    for w, h, name in ((390, 844, "4-390-production"), (1600, 1000, "5-1600-production")):
        ctx, pg, t0 = page_for(b, w, h); until_values(pg, t0); compare(pg, name)
        shot(pg, name, full=True); R[f"layout_{w}"] = layout(pg)
        if w == 390: details(pg, True); shot(pg, "4b-390-production-details", full=True)
        ctx.close()
    b.close()
json.dump(R, open(f"{OUT}/qa_prod.json", "w"), ensure_ascii=False, indent=1)
print("reload", R["reload"]); print("hot", R["hot"]); print("requests", R["requests"])
for k in ("layout_1440", "layout_390", "layout_1600"): print(k, R[k])
