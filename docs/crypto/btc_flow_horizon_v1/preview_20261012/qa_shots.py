"""BTC Wall UI Flow Horizon V1 local preview QA (debug only).
Ports: 5200 REAL (cached terminal data, real btc-context API + real flow horizon worker over public
klines), 5401 mixed / 5402 all-buy / 5403 all-sell / 5404 one-missing = SYNTHETIC horizons (the
node wrapper replaces `flow_horizons` only), 5501 synthetic mixed + real SHORT position, 5300 no-wall.
usage: qa_shots.py OUT_DIR"""
import json, sys, time
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]
R = {"shots": {}, "requests": {"total": 0, "non_get": [], "binance": 0}, "immediate": {}}
STRIP = """() => { const s=document.querySelector('[data-testid=btc-context-strip]'); if(!s) return null;
  const rows=['10m','1h','4h','1d'].map(k=>{const r=s.querySelector(`[data-testid=flow-horizon-${k}]`); if(!r) return null;
    const b=r.querySelector('[data-testid=flow-bar-buy]'), l=r.querySelector('[data-testid=flow-bar-sell]');
    return {k, text:r.querySelector(`[data-testid=flow-horizon-${k}-text]`).textContent, lean:r.dataset.lean,
      buyW:b?b.style.width:null, sellW:l?l.style.width:null}});
  const d=s.querySelector('details');
  const outside=(()=>{const c=s.cloneNode(true); c.querySelector('[data-testid=btc-context-detail]')?.remove(); return c.innerText})();
  return {mode:s.dataset.mode, placement:s.dataset.sortPlacement, rows, open:d?.open, h:Math.round(s.getBoundingClientRect().height),
    outside, selInDetail: !!s.querySelector('[data-testid=btc-context-detail] [data-testid=btc-context-selection]'),
    selInHeader: !!s.querySelector('[data-testid=btc-context-selection]') && !s.querySelector('[data-testid=btc-context-detail] [data-testid=btc-context-selection]')}; }"""
CHART = """() => { const c=document.querySelector('[data-testid=candle-chart]'); if(!c) return null;
  const tags=[...c.querySelectorAll('[data-testid=context-tags] span')]; const rects=tags.map(e=>e.getBoundingClientRect());
  let ov=0; for(let i=0;i<rects.length;i++)for(let j=i+1;j<rects.length;j++){const a=rects[i],b=rects[j];
    if(a.left<b.right&&b.left<a.right&&a.top<b.bottom&&b.top<a.bottom)ov++}
  const cr=c.getBoundingClientRect();
  return {box:[Math.round(cr.width),Math.round(cr.height)], bars:c.dataset.visibleBars, span:c.dataset.priceSpan,
    tags:tags.map(e=>e.textContent), tagOverlap:ov}; }"""
GEOM = """() => { const t=[...document.querySelectorAll('[data-testid=order-ticket]')].find(e=>e.getBoundingClientRect().width>0);
  const r=t&&t.getBoundingClientRect(); return {ticket:r?[Math.round(r.left+scrollX),Math.round(r.top+scrollY),Math.round(r.width),Math.round(r.height)]:null,
  overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth}; }"""

def page_for(b, port, w, h, query=""):
    ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2 if w == 390 else 1)
    pg = ctx.new_page()
    def on_req(r):
        R["requests"]["total"] += 1
        if r.method != "GET": R["requests"]["non_get"].append({"m": r.method, "u": r.url})
        if "/binance/" in r.url: R["requests"]["binance"] += 1
    pg.on("request", on_req)
    pg.route("**/*", lambda route: route.fallback() if route.request.url.startswith(f"http://127.0.0.1:{port}/") else route.abort())
    pg.goto(f"http://127.0.0.1:{port}/crypto-paper{query}")
    pg.wait_for_selector("[data-testid=market-header]", timeout=60000)
    pg.wait_for_selector("[data-testid=candle-chart] canvas", timeout=60000)
    return ctx, pg

def ready(pg, timeout=40):
    end = time.time() + timeout; t0 = time.time()
    while time.time() < end:
        s = pg.evaluate(STRIP)
        if s and any(r and r["text"] != "--" for r in s["rows"]): return round((time.time() - t0) * 1000)
        pg.wait_for_timeout(200)
    return None

def scroll_strip(pg):
    pg.evaluate("() => document.querySelector('[data-testid=btc-context-strip]').scrollIntoView({block:'end'})"); pg.wait_for_timeout(400)
def details(pg, o):
    pg.evaluate("(o)=>{const d=document.querySelector('[data-testid=btc-context-strip] details'); if(d) d.open=o}", o); pg.wait_for_timeout(300)
def shot(pg, name, full=False, note=None):
    pg.screenshot(path=f"{OUT}/{name}.png", full_page=full)
    R["shots"][name] = {"strip": pg.evaluate(STRIP), "chart": pg.evaluate(CHART), "geom": pg.evaluate(GEOM), "note": note}
    d = R["shots"][name]; print("shot", name, [r["text"] if r else None for r in d["strip"]["rows"]],
        "geom", d["geom"], "tagOverlap", d["chart"]["tagOverlap"], flush=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    # 1 real, immediate on load; reload twice to prove nothing is warmed up in the browser
    ctx, pg = page_for(b, 5200, 1440, 1000)
    R["immediate"]["first_load_ms_until_values"] = ready(pg)
    pg.reload(); pg.wait_for_selector("[data-testid=candle-chart] canvas", timeout=60000)
    R["immediate"]["reload_ms_until_values"] = ready(pg)
    scroll_strip(pg); shot(pg, "1-1440-flow-horizons-real", note="REAL: cached terminal + real API + public klines worker")
    # movement
    base = pg.evaluate(GEOM)["ticket"]; c0 = pg.evaluate(CHART)
    details(pg, True); a = pg.evaluate(GEOM)["ticket"]; c1 = pg.evaluate(CHART); details(pg, False)
    for tf in ["15s", "1h", "4h", "1d", "1m"]:
        pg.click(f"[data-testid=timeframe-{tf}]"); pg.wait_for_timeout(700)
        R["immediate"][f"rows_on_{tf}"] = [r["text"] for r in pg.evaluate(STRIP)["rows"]]
    R["movement_1440"] = {"ticket_delta_details": [a[i]-base[i] for i in range(4)], "chart_box": [c0["box"], c1["box"]],
                          "bars": [c0["bars"], c1["bars"]]}
    scroll_strip(pg); details(pg, True); scroll_strip(pg); shot(pg, "7-1440-details-expanded-A", note="REAL; sort switch beside the title")
    ctx.close()
    # B placement
    ctx, pg = page_for(b, 5200, 1440, 1000, "?sort=detail"); ready(pg); scroll_strip(pg)
    shot(pg, "7b-1440-sort-in-detail-closed-B", note="REAL; sort switch inside the detail")
    details(pg, True); scroll_strip(pg); shot(pg, "7c-1440-sort-in-detail-open-B", note="REAL")
    ctx.close()
    # synthetic variants
    for port, name, note in [(5401, "2-1440-mixed-direction-synthetic", "SYNTHETIC horizons: 10m 70 buy, 1h 61 buy, 4h 58 sell, 1d 52 buy"),
                             (5402, "3-1440-all-buy-lean-synthetic", "SYNTHETIC horizons"),
                             (5403, "4-1440-all-sell-lean-synthetic", "SYNTHETIC horizons"),
                             (5404, "8-1440-one-horizon-missing-synthetic", "SYNTHETIC: 4h withheld (coverage 99.7%)")]:
        ctx, pg = page_for(b, port, 1440, 1000); ready(pg); scroll_strip(pg); shot(pg, name, note=note); ctx.close()
    ctx, pg = page_for(b, 5200, 390, 844); ready(pg); shot(pg, "5-390-real", full=True, note="REAL")
    d0 = pg.evaluate(GEOM)["ticket"]; details(pg, True); d1 = pg.evaluate(GEOM)["ticket"]
    R["movement_390"] = {"ticket_delta_details": [d1[i]-d0[i] for i in range(4)]}
    shot(pg, "5b-390-details-open", full=True, note="REAL"); ctx.close()
    ctx, pg = page_for(b, 5401, 390, 844); ready(pg); shot(pg, "5c-390-mixed-synthetic", full=True, note="SYNTHETIC"); ctx.close()
    ctx, pg = page_for(b, 5200, 1600, 1000); ready(pg); shot(pg, "6-1600-full-real", full=True, note="REAL")
    R["movement_1600"] = {"overflow": pg.evaluate(GEOM)["overflow"]}; ctx.close()
    ctx, pg = page_for(b, 5501, 1440, 1000); ready(pg); scroll_strip(pg); shot(pg, "9-1440-position-synthetic", note="real paper SHORT + SYNTHETIC horizons"); ctx.close()
    ctx, pg = page_for(b, 5300, 1440, 1000); ready(pg); scroll_strip(pg); shot(pg, "10-1440-no-wall-real", note="REAL, no-wall collector snapshot"); ctx.close()
    b.close()
json.dump(R, open(f"{OUT}/qa_shots.json", "w"), ensure_ascii=False, indent=1)
print("immediate", R["immediate"]); print("movement", R.get("movement_1440"), R.get("movement_390"), R.get("movement_1600")); print("requests", R["requests"])
