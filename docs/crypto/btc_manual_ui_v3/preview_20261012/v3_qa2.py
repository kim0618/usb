"""BTC Manual Trading UI V3 preview QA on production data, through the GET-only proxies.
3100: production LIVE account (real LONG) and PAPER (flat) | 3200: LIVE with a simulated SHORT | 3300: simulated LONG."""
import json, sys, time
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]
TFS = ["15s", "1m", "10m", "1h", "4h", "1d"]
BANNED = ["HOLD", "EXIT", "TAKE PROFIT", "STOP", "TP1", "TP2", "익절", "손절 ", "추천", "강화", "약화",
          "CANCEL", "CONSUMED", "STALE", "UNKNOWN", "STRUCTURE", "LIQUIDITY", "Q1", "Q2", "Q3"]
R = {"widths": {}, "modes": {}, "timeframes": {}, "interaction": {}, "reconnect": {}, "hot": {}, "requests": {}}
reqs = []
CHART = """() => { const c=document.querySelector('[data-testid=candle-chart]'); if(!c) return null;
  const r=c.getBoundingClientRect(); return {lines:c.dataset.contextLines||'', bars:c.dataset.visibleBars,
  span:c.dataset.priceSpan, y:r.y+window.scrollY, h:r.height,
  tags:[...c.querySelectorAll('[data-testid=context-tags] span')].map(e=>e.textContent+'@'+e.style.top)}; }"""
TICKET = """() => { const t=[...document.querySelectorAll('[data-testid=order-ticket],[data-testid=live-order-ticket]')].find(e=>e.offsetParent!==null);
  if(!t) return null; const r=t.getBoundingClientRect(); return {x:r.x, y:r.y+window.scrollY, w:r.width, h:r.height}; }"""
OVER = """() => ({doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  overlap: [...document.querySelectorAll('[data-testid$=-row]')].filter(r => {
    const kids=[...r.children].map(k=>{const g=document.createRange(); g.selectNodeContents(k); return g.getBoundingClientRect();}).filter(b=>b.width>0);
    for (let i=1;i<kids.length;i++) if (kids[i].left < kids[i-1].right - 0.5) return true;
    return r.scrollWidth > r.clientWidth + 0.5; }).length,
  strip: (()=>{const s=document.querySelector('[data-testid=btc-context-strip]'); if(!s) return null;
    return Math.max(0, s.scrollWidth - s.clientWidth);})()})"""
STRIP = "() => { const s=document.querySelector('[data-testid=btc-context-strip]'); return s ? {mode:s.dataset.mode, text:s.innerText} : null; }"

def page_for(b, port, w, h, tag, live=False, block_ctx=False):
    ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=2 if w == 390 else 1)
    pg = ctx.new_page()
    pg.on("request", lambda r: reqs.append({"tag": tag, "m": r.method, "u": r.url, "t": time.time()}))
    def guard(route):
        u = route.request.url
        if not u.startswith(f"http://127.0.0.1:{port}/"): return route.abort()
        if block_ctx and "/btc-context-api/" in u: return route.abort()
        return route.continue_()
    pg.route("**/*", guard)
    pg.goto(f"http://127.0.0.1:{port}/crypto-paper")
    pg.wait_for_selector("[data-testid=market-header]", timeout=40000)
    if live:
        pg.click("[data-testid=source-BINANCE_LIVE]")
        pg.wait_for_selector("[data-testid=live-market-header], [data-testid=candle-chart]", timeout=40000)
    pg.wait_for_selector("[data-testid=candle-chart] canvas", timeout=40000)
    return ctx, pg

def chart(pg, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        c = pg.evaluate(CHART)
        if c and c.get("span"): return c
        pg.wait_for_timeout(300)
    return pg.evaluate(CHART) or {"lines": None, "tags": [], "span": None, "bars": None, "y": 0, "h": 0}

def wait_strip(pg, mode=None, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        s = pg.evaluate(STRIP)
        if s and "매도벽1" in s["text"] and (mode is None or s["mode"] == mode): return s
        pg.wait_for_timeout(500)
    return pg.evaluate(STRIP)

def tf(pg, frame):
    pg.click(f"[data-testid=timeframe-{frame}]"); pg.wait_for_timeout(2500)

def shot_view(pg, name):
    """Chart section and the panel under it, one image."""
    a = pg.locator("section[aria-label='시세 차트']").first.bounding_box()
    s = pg.locator("[data-testid=btc-context-strip]").first.bounding_box()
    if not a or not s: return pg.screenshot(path=f"{OUT}/{name}.png")
    x0, y0 = min(a["x"], s["x"]), min(a["y"], s["y"])
    x1, y1 = max(a["x"] + a["width"], s["x"] + s["width"]), max(a["y"] + a["height"], s["y"] + s["height"])
    pg.screenshot(path=f"{OUT}/{name}.png", full_page=True, clip={"x": x0, "y": y0 + 0, "width": x1 - x0, "height": y1 - y0})

def banned(pg):
    body = pg.evaluate("() => (document.querySelector('[data-testid=btc-context-strip]')||{}).innerText||''")
    return {k: body.count(k) for k in BANNED if body.count(k)}

with sync_playwright() as p:
    b = p.chromium.launch()
    # ---- modes at 1440
    for name, port, live, mode in (("no-position", 3400, False, "MARKET"), ("short-paper-real", 3100, False, "POSITION"),
                                   ("short-sim", 3200, False, "POSITION"), ("long-sim", 3300, False, "POSITION")):
        c, pg = page_for(b, port, 1440, 900, f"mode-{name}", live=live)
        s = wait_strip(pg, mode); pg.wait_for_timeout(14000); s = pg.evaluate(STRIP)
        shot_view(pg, f"1440-{name}")
        R["modes"][name] = {"strip": s, "chart": pg.evaluate(CHART), "banned": banned(pg), "overflow": pg.evaluate(OVER)}
        if name == "short-sim":
            pg.locator("[data-testid=btc-context-strip]").screenshot(path=f"{OUT}/1440-wall-hierarchy-compact.png")
            pg.click("[data-testid=btc-context-strip] summary"); pg.wait_for_timeout(400)
            pg.locator("[data-testid=btc-context-strip]").screenshot(path=f"{OUT}/1440-wall-hierarchy-expanded.png")
            R["modes"][name]["expanded"] = pg.evaluate(STRIP)
            R["modes"][name]["overflow_open"] = pg.evaluate(OVER)
            pg.click("[data-testid=btc-context-strip] summary")
            pg.click("[data-testid=btc-context-selection] >> text=큰 순"); pg.wait_for_timeout(1500)
            R["modes"][name]["size_selection"] = {"strip": pg.evaluate(STRIP), "chart": pg.evaluate(CHART)}
            pg.locator("[data-testid=btc-context-strip]").screenshot(path=f"{OUT}/1440-selection-size.png")
            pg.click("[data-testid=btc-context-selection] >> text=가까운 순"); pg.wait_for_timeout(800)
        c.close()
    # ---- widths: SHORT sim, context vs context blocked
    for w, h in ((1600, 1000), (1440, 900), (390, 844)):
        c1, a = page_for(b, 3200, w, h, f"ctx-{w}")
        c2, z = page_for(b, 3200, w, h, f"bare-{w}", block_ctx=True)
        a.wait_for_timeout(1000)
        wait_strip(a, "POSITION"); a.wait_for_timeout(10000)
        samples = []
        for _ in range(4):
            ca, cz = chart(a), chart(z)
            samples.append([ca["span"], cz["span"], ca["bars"], cz["bars"]]); a.wait_for_timeout(1500)
        ta, tz = a.evaluate(TICKET), z.evaluate(TICKET)
        R["widths"][w] = {"span_samples": samples, "ticket_shift": None if not (ta and tz) else {k: round(ta[k]-tz[k], 1) for k in ta},
                          "overflow": a.evaluate(OVER), "overflow_bare": z.evaluate(OVER), "banned": banned(a),
                          "chart_y_shift": round(ca["y"] - cz["y"], 1), "chart_h_shift": round(ca["h"] - cz["h"], 1)}
        if w == 1600: a.screenshot(path=f"{OUT}/1600-full-page.png", full_page=True)
        if w == 390:
            a.locator("[data-testid=btc-context-strip]").scroll_into_view_if_needed()
            a.locator("[data-testid=btc-context-strip]").screenshot(path=f"{OUT}/390-position-mode.png")
            a.screenshot(path=f"{OUT}/390-full-page.png", full_page=True)
            a.click("[data-testid=btc-context-strip] summary"); a.wait_for_timeout(300)
            a.locator("[data-testid=btc-context-strip]").screenshot(path=f"{OUT}/390-position-mode-expanded.png")
            R["widths"][w]["overflow_open"] = a.evaluate(OVER)
        if w == 1440:
            for frame in TFS:
                tf(a, frame); tf(z, frame); a.wait_for_timeout(1500)
                ca, cz = chart(a), chart(z)
                R["timeframes"][frame] = {"lines": ca["lines"], "tags": ca["tags"], "same_span": ca["span"] == cz["span"],
                                          "same_bars": ca["bars"] == cz["bars"], "span": [ca["span"], cz["span"]]}
                shot_view(a, f"tf-{frame}-1440")
            tf(a, "1m"); tf(z, "1m")
            box = a.locator("[data-testid=candle-chart]").bounding_box()
            cx, cy = box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5
            before = a.evaluate(CHART)
            a.mouse.move(cx, cy); a.mouse.wheel(0, -600); a.wait_for_timeout(800); zin = a.evaluate(CHART)
            a.mouse.wheel(0, 1200); a.wait_for_timeout(800); zout = a.evaluate(CHART)
            a.mouse.move(cx, cy); a.mouse.down(); a.mouse.move(cx + box["width"] * 0.5, cy, steps=12); a.mouse.up()
            a.wait_for_timeout(1000); pan = a.evaluate(CHART)
            shot_view(a, "interaction-pan-1440")
            R["interaction"] = {"before": before, "zoom_in": zin, "zoom_out": zout, "pan": pan}
            a.route("**/btc-context-api/**", lambda route: route.abort())
            end = time.time() + 30
            while time.time() < end and (a.evaluate(CHART) or {}).get("lines"): a.wait_for_timeout(500)
            down = {"chart": a.evaluate(CHART), "strip": a.evaluate(STRIP)}
            shot_view(a, "reconnect-down-1440")
            a.unroute("**/btc-context-api/**")
            back = wait_strip(a, "POSITION", 30); a.wait_for_timeout(1500)
            R["reconnect"] = {"down": down, "back": {"strip": back, "chart": a.evaluate(CHART)}}
            a.click("[data-testid=symbol-tab-hot]")
            a.wait_for_function("() => document.querySelector('[data-testid=symbol-tab-hot]').getAttribute('aria-selected')==='true'", timeout=15000)
            t0 = time.time(); a.wait_for_timeout(10000); t1 = time.time()
            R["hot"] = {"chart": a.evaluate(CHART), "strip": a.evaluate(STRIP),
                        "ctx_requests": sum(1 for r in reqs if r["tag"] == "ctx-1440" and "/btc-context-api/" in r["u"] and t0 <= r["t"] <= t1)}
            shot_view(a, "hot-1440") if a.evaluate(STRIP) else a.locator("section[aria-label='시세 차트']").first.screenshot(path=f"{OUT}/hot-1440.png")
            a.click("[data-testid=symbol-tab-BTCUSDT]"); R["hot"]["back"] = wait_strip(a, None, 30)
        c1.close(); c2.close()
    c, pg = page_for(b, 3400, 1440, 900, "hot-paper", live=False)
    wait_strip(pg, "MARKET")
    pg.click("[data-testid=symbol-tab-hot]")
    pg.wait_for_function("() => document.querySelector('[data-testid=symbol-tab-hot]').getAttribute('aria-selected')==='true'", timeout=15000)
    t0 = time.time(); pg.wait_for_timeout(10000); t1 = time.time()
    R["hot_paper"] = {"chart": pg.evaluate(CHART), "strip": pg.evaluate(STRIP),
                      "ctx_requests": sum(1 for r in reqs if r["tag"] == "hot-paper" and "/btc-context-api/" in r["u"] and t0 <= r["t"] <= t1),
                      "header": (pg.evaluate("() => (document.querySelector('[data-testid=market-header]')||{}).innerText||''") or "")[:60]}
    pg.locator("section[aria-label='시세 차트']").first.screenshot(path=f"{OUT}/hot-paper-1440.png")
    c.close()
    b.close()
nonget = [r for r in reqs if r["m"] not in ("GET", "HEAD")]
R["requests"] = {"total": len(reqs), "non_get": nonget, "ctx_methods": sorted({r["m"] for r in reqs if "/btc-context-api/" in r["u"]})}
json.dump(R, open(f"{OUT}/v3_qa.json", "w"), indent=1, default=str, ensure_ascii=False)
print("done", len(reqs))
