"""BTC Wall UI Compact V1 local preview QA (debug only, not production code).

Stack: cached production GET responses (cproxy.js, GET only, upstream blocked) + the real
btc-context API replaying ONE cached production collector snapshot (replay_feeder.py).
The 5-minute pressure needs minute history, so the browser route below time-shifts each /snapshot
answer by one minute per request (flow scripted from the real 75/25 reading). Everything else in
the answer is the API's own output.
usage: qa_shots.py OUT_DIR   (5100 REAL paper = real SHORT, 5200 FLAT, 5300 FLAT + no-wall collector)"""
import json, sys, time
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]
R = {"shots": {}, "requests": {"total": 0, "non_get": [], "binance": 0}}
FLOWS = [70, 55, 30, 62, 80, 51]          # buy % per scripted minute (5 completed + 1 current)

STRIP = """() => { const s=document.querySelector('[data-testid=btc-context-strip]'); if(!s) return null;
  const t = id => s.querySelector(`[data-testid=${id}]`)?.textContent ?? null;
  return {mode: s.dataset.mode, up: t('btc-context-path-up'), down: t('btc-context-path-down'),
    upValue: t('btc-context-path-up-value'), downValue: t('btc-context-path-down-value'),
    pressure: t('btc-context-pressure'), dots: t('btc-context-pressure-dots'),
    oi: t('btc-context-oi'), funding: t('btc-context-funding'),
    current: t('btc-context-current'), entry: t('btc-context-entry'), vsEntry: t('btc-context-current-vs-entry'),
    detailOpen: s.querySelector('details')?.open, height: Math.round(s.getBoundingClientRect().height),
    text: s.innerText}; }"""
CHART = """() => { const c=document.querySelector('[data-testid=candle-chart]'); if(!c) return null;
  const tags=[...c.querySelectorAll('[data-testid=context-tags] span')];
  const rects=tags.map(e=>e.getBoundingClientRect());
  let overlap=0; for (let i=0;i<rects.length;i++) for (let j=i+1;j<rects.length;j++) {
    const a=rects[i], b=rects[j];
    if (a.left<b.right && b.left<a.right && a.top<b.bottom && b.top<a.bottom) overlap++; }
  const cr=c.getBoundingClientRect();
  return {lines: c.dataset.contextLines||'', bars: c.dataset.visibleBars, span: c.dataset.priceSpan,
    box: [Math.round(cr.width), Math.round(cr.height)],
    tags: tags.map(e => ({text: e.textContent, order: e.dataset.order, color: getComputedStyle(e).color})),
    tagOverlap: overlap}; }"""
GEOM = """() => { const t=[...document.querySelectorAll('[data-testid=order-ticket]')].find(e => e.getBoundingClientRect().width > 0); const r=t&&t.getBoundingClientRect();
  return {ticket: r ? [Math.round(r.left+scrollX), Math.round(r.top+scrollY), Math.round(r.width), Math.round(r.height)] : null,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth}; }"""

def install_history(pg, base_port):
    state = {"n": 0}
    t0 = (int(time.time()) // 60) * 60 * 1000 + 30_000
    def handle(route):
        resp = route.fetch()
        try:
            body = resp.json()
        except Exception:
            return route.fulfill(response=resp)
        i = min(state["n"], len(FLOWS) - 1)
        state["n"] += 1
        body["server_time_ms"] = t0 + i * 60_000
        buy = FLOWS[i]
        body["flow"] = {**body["flow"], "live": True, "buy_pct": buy, "sell_pct": 100 - buy}
        route.fulfill(response=resp, json=body)
    pg.route("**/btc-context-api/snapshot", handle)
    return state

def page_for(b, port, w, h, history=True):
    ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2 if w == 390 else 1)
    pg = ctx.new_page()
    def on_req(r):
        R["requests"]["total"] += 1
        if r.method != "GET": R["requests"]["non_get"].append({"m": r.method, "u": r.url})
        if "/binance/" in r.url: R["requests"]["binance"] += 1
    pg.on("request", on_req)
    pg.route("**/*", lambda route: route.fallback() if route.request.url.startswith(f"http://127.0.0.1:{port}/") else route.abort())
    if history: install_history(pg, port)
    pg.goto(f"http://127.0.0.1:{port}/crypto-paper")
    pg.wait_for_selector("[data-testid=market-header]", timeout=60000)
    pg.wait_for_selector("[data-testid=candle-chart] canvas", timeout=60000)
    return ctx, pg

def wait_pressure(pg, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        s = pg.evaluate(STRIP)
        if s and s["pressure"] and "매수" in s["pressure"] and "/5분" not in s["pressure"]: return s
        pg.wait_for_timeout(1000)
    return pg.evaluate(STRIP)

def settle(pg, ms=1500): pg.wait_for_timeout(ms)

def scroll_to_strip(pg):
    pg.evaluate("() => document.querySelector('[data-testid=btc-context-strip]').scrollIntoView({block:'end'})")
    pg.wait_for_timeout(400)

def shot(pg, name, full=False, extra=None):
    pg.screenshot(path=f"{OUT}/{name}.png", full_page=full)
    R["shots"][name] = {"strip": pg.evaluate(STRIP), "chart": pg.evaluate(CHART), "geom": pg.evaluate(GEOM)}
    if extra: R["shots"][name].update(extra)
    d = R["shots"][name]
    print("shot", name, "tags", [t["text"] for t in (d["chart"] or {}).get("tags", [])], "overlap", (d["chart"] or {}).get("tagOverlap"),
          "geom", d["geom"], flush=True)

def set_details(pg, open_):
    pg.evaluate("(o) => { const d=document.querySelector('[data-testid=btc-context-strip] details'); if (d) d.open = o; }", open_)
    pg.wait_for_timeout(300)

def click_text(pg, text):
    pg.click(f"[data-testid=btc-context-strip] button:has-text('{text}')"); pg.wait_for_timeout(1000)

def movement(pg):
    """ticket and chart geometry with the strip shown vs display:none, details closed vs open, sort NEAR vs SIZE"""
    out = {}
    base = pg.evaluate(GEOM)["ticket"]; c0 = pg.evaluate(CHART)
    set_details(pg, True); a = pg.evaluate(GEOM)["ticket"]; c1 = pg.evaluate(CHART)
    set_details(pg, False)
    click_text(pg, "큰 순"); b = pg.evaluate(GEOM)["ticket"]; c2 = pg.evaluate(CHART)
    click_text(pg, "가까운 순")
    pg.evaluate("() => { document.querySelector('[data-testid=btc-context-strip]').style.display='none'; }")
    pg.wait_for_timeout(300); n = pg.evaluate(GEOM)["ticket"]; c3 = pg.evaluate(CHART)
    pg.evaluate("() => { document.querySelector('[data-testid=btc-context-strip]').style.display=''; }")
    pg.wait_for_timeout(300)
    d = lambda x: [x[i] - base[i] for i in range(4)]
    out["ticket_delta_details_open"] = d(a); out["ticket_delta_size_sort"] = d(b); out["ticket_delta_strip_hidden"] = d(n)
    out["chart_box"] = [c["box"] for c in (c0, c1, c2, c3)]
    out["chart_bars"] = [c["bars"] for c in (c0, c1, c2, c3)]
    out["chart_span"] = [c["span"] for c in (c0, c1, c2, c3)]
    return out

with sync_playwright() as p:
    b = p.chromium.launch()
    # 1-3, 1440, no position (FLAT)
    ctx, pg = page_for(b, 5200, 1440, 1000); wait_pressure(pg); settle(pg); scroll_to_strip(pg)
    shot(pg, "1-1440-compact")
    R["movement_1440"] = movement(pg)
    set_details(pg, True); scroll_to_strip(pg); shot(pg, "2-1440-detail-open")
    set_details(pg, False)
    click_text(pg, "큰 순"); scroll_to_strip(pg); shot(pg, "3-1440-size-sort")
    set_details(pg, True); scroll_to_strip(pg); shot(pg, "3b-1440-size-sort-detail")
    click_text(pg, "가까운 순"); set_details(pg, False)
    # R/S option kept in the detail: annotation as grey dotted
    ctx.close()
    # 4 phone
    ctx, pg = page_for(b, 5200, 390, 844); wait_pressure(pg); settle(pg)
    shot(pg, "4-390-compact", full=True)
    R["movement_390"] = movement(pg)
    set_details(pg, True); shot(pg, "4b-390-detail-open", full=True)
    ctx.close()
    # 5 wide
    ctx, pg = page_for(b, 5200, 1600, 1000); wait_pressure(pg); settle(pg)
    shot(pg, "5-1600-full", full=True)
    R["movement_1600"] = movement(pg)
    ctx.close()
    # 6 position (real paper SHORT)
    ctx, pg = page_for(b, 5100, 1440, 1000); wait_pressure(pg); settle(pg); scroll_to_strip(pg)
    shot(pg, "6-1440-position")
    set_details(pg, True); scroll_to_strip(pg); shot(pg, "6b-1440-position-detail")
    ctx.close()
    ctx, pg = page_for(b, 5100, 390, 844); wait_pressure(pg); settle(pg)
    shot(pg, "6c-390-position", full=True)
    ctx.close()
    # 7 no wall
    ctx, pg = page_for(b, 5300, 1440, 1000); wait_pressure(pg); settle(pg); scroll_to_strip(pg)
    shot(pg, "7-1440-no-wall")
    ctx.close()
    # 8 untouched real API: no history yet (collecting)
    ctx, pg = page_for(b, 5200, 1440, 1000, history=False); settle(pg, 8000); scroll_to_strip(pg)
    shot(pg, "8-1440-collecting-real-clock")
    ctx.close()
    b.close()
json.dump(R, open(f"{OUT}/qa_shots.json", "w"), ensure_ascii=False, indent=1)
print("requests", R["requests"])
