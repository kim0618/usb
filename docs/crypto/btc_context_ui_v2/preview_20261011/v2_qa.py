"""BTC Context UI V2 preview QA against production data (GET-only proxy on 3100)."""
import json, sys, time
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]
URL = "http://127.0.0.1:3100/crypto-paper"
BANNED = ["Q1", "Q2", "Q3", "LONG candidate", "SHORT candidate", "research", "sample", "p-value",
          "confidence", "STALE", "UNKNOWN", "STRUCTURE", "LIQUIDITY", "DERIVATIVES"]
TFS = ["15s", "1m", "10m", "1h", "4h", "1d"]
R = {"widths": {}, "timeframes": {}, "interaction": {}, "reconnect": {}, "hot": {}, "requests": {}}
reqs = []

CHART = """() => { const c=document.querySelector('[data-testid=candle-chart]'); if(!c) return null;
  const r=c.getBoundingClientRect(); return {lines:c.dataset.contextLines||'', bars:c.dataset.visibleBars,
  span:c.dataset.priceSpan, spacing:c.dataset.barSpacing, profile:c.dataset.profile, y:r.y+window.scrollY, h:r.height,
  tags:[...c.querySelectorAll('[data-testid=context-tags] span')].map(e=>e.textContent+'@'+e.style.top)}; }"""
TICKET = """() => { const t=[...document.querySelectorAll('[data-testid=order-ticket]')].find(e=>e.offsetParent!==null);
  if(!t) return null; const r=t.getBoundingClientRect(); return {x:r.x, y:r.y+window.scrollY, w:r.width, h:r.height}; }"""
OVER = """() => ({doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  body: document.body.scrollWidth - document.body.clientWidth,
  strip: (()=>{const s=document.querySelector('[data-testid=btc-context-strip]'); if(!s) return null;
    return Math.max(0, s.scrollWidth - s.clientWidth);})()})"""
STRIP = "() => { const s=document.querySelector('[data-testid=btc-context-strip]'); return s ? s.innerText : null; }"
OFFNOTE = "() => { const s=document.querySelector('[data-testid=offscreen-overlays]'); return s ? s.innerText : null; }"

def page_for(browser, w, h, tag, block_ctx=False):
    ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=2 if w == 390 else 1)
    pg = ctx.new_page()
    pg.on("request", lambda r: reqs.append({"tag": tag, "m": r.method, "u": r.url, "t": time.time()}))
    def guard(route):
        u = route.request.url
        if not u.startswith("http://127.0.0.1:3100/"): return route.abort()
        if block_ctx and "/btc-context-api/" in u: return route.abort()
        return route.continue_()
    pg.route("**/*", guard)
    pg.goto(URL); pg.wait_for_selector("[data-testid=market-header]", timeout=40000)
    pg.wait_for_selector("[data-testid=candle-chart] canvas", timeout=40000)
    return ctx, pg

def wait_lines(pg, want=True, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        c = pg.evaluate(CHART)
        if c and (bool(c["lines"]) == want): return c
        pg.wait_for_timeout(500)
    return pg.evaluate(CHART)

def tf(pg, frame):
    pg.click(f"[data-testid=timeframe-{frame}]")
    pg.wait_for_timeout(2500)

def shot_chart(pg, name):
    pg.locator("section[aria-label='시세 차트']").first.screenshot(path=f"{OUT}/shots/{name}.png")

with sync_playwright() as p:
    b = p.chromium.launch()
    # ---- widths: context vs no-context side by side
    for w, h in ((1600, 1000), (1440, 900), (390, 844)):
        c1, a = page_for(b, w, h, f"ctx-{w}")
        c2, z = page_for(b, w, h, f"bare-{w}", block_ctx=True)
        wait_lines(a, True); a.wait_for_timeout(12000)
        # Measure both at the same moment, five times: the two pages load history independently.
        samples = []
        for _ in range(5):
            ca, cz = a.evaluate(CHART), z.evaluate(CHART)
            samples.append([ca["span"], cz["span"], ca["bars"], cz["bars"]])
            a.wait_for_timeout(2000)
        ta, tz = a.evaluate(TICKET), z.evaluate(TICKET)
        body = a.evaluate("() => document.body.innerText")
        R["widths"][w] = {"ctx_chart": ca, "bare_chart": cz, "span_samples": samples, "ticket_ctx": ta, "ticket_bare": tz,
            "ticket_shift": None if not (ta and tz) else {k: round(ta[k]-tz[k], 1) for k in ta},
            "overflow": a.evaluate(OVER), "strip": a.evaluate(STRIP), "offscreen_note": a.evaluate(OFFNOTE),
            "banned": {k: body.count(k) for k in BANNED if body.count(k)}}
        shot_chart(a, f"chart-{w}")
        a.screenshot(path=f"{OUT}/shots/page-{w}.png", full_page=(w == 390))
        if w == 390:
            a.locator("[data-testid=btc-context-strip]").screenshot(path=f"{OUT}/shots/strip-390.png")
            a.click("[data-testid=btc-context-strip] summary"); a.wait_for_timeout(300)
            a.locator("[data-testid=btc-context-strip]").screenshot(path=f"{OUT}/shots/strip-390-open.png")
            R["widths"][w]["strip_open"] = a.evaluate(STRIP)
            R["widths"][w]["overflow_open"] = a.evaluate(OVER)
        if w == 1440:
            a.locator("[data-testid=btc-context-strip]").screenshot(path=f"{OUT}/shots/strip-1440.png")
            # ---- timeframes, compared with the bare page at the same timeframe
            for frame in TFS:
                tf(a, frame); tf(z, frame)
                la = wait_lines(a, True, 15)
                ca, cz = a.evaluate(CHART), z.evaluate(CHART)
                R["timeframes"][frame] = {"ctx": ca, "bare": cz, "offscreen_note": a.evaluate(OFFNOTE),
                                          "same_span": ca["span"] == cz["span"], "same_bars": ca["bars"] == cz["bars"]}
                shot_chart(a, f"tf-{frame}-1440")
            # ---- interaction on 1m: zoom, pan, history load
            tf(a, "1m"); tf(z, "1m")
            box = a.locator("[data-testid=candle-chart]").bounding_box()
            cx, cy = box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5
            before = a.evaluate(CHART)
            a.mouse.move(cx, cy); a.mouse.wheel(0, -600); a.wait_for_timeout(800)
            zoom_in = a.evaluate(CHART)
            a.mouse.wheel(0, 1200); a.wait_for_timeout(800)
            zoom_out = a.evaluate(CHART)
            a.mouse.move(cx, cy); a.mouse.down(); a.mouse.move(cx + box["width"] * 0.6, cy, steps=12); a.mouse.up()
            a.wait_for_timeout(800)
            pan = a.evaluate(CHART)
            count0 = a.evaluate("() => document.querySelectorAll('[data-testid=candle-chart]').length")
            hist_reqs0 = sum(1 for r in reqs if r["tag"] == "ctx-1440" and "chart-history" in r["u"])
            for _ in range(8):
                a.mouse.move(cx - box["width"] * 0.3, cy); a.mouse.down()
                a.mouse.move(cx + box["width"] * 0.45, cy, steps=10); a.mouse.up(); a.wait_for_timeout(700)
            a.wait_for_timeout(2500)
            hist = a.evaluate(CHART)
            hist_reqs = sum(1 for r in reqs if r["tag"] == "ctx-1440" and "chart-history" in r["u"]) - hist_reqs0
            shot_chart(a, "interaction-after-history-1440")
            R["interaction"] = {"before": before, "zoom_in": zoom_in, "zoom_out": zoom_out, "pan": pan,
                                "after_history": hist, "history_requests": hist_reqs}
            # ---- reconnect: API gone, then back
            tf(a, "1m")
            a.route("**/btc-context-api/**", lambda route: route.abort())
            gone = wait_lines(a, False, 30)
            strip_gone = a.evaluate(STRIP)
            shot_chart(a, "reconnect-down-1440")
            a.unroute("**/btc-context-api/**")
            back = wait_lines(a, True, 30)
            R["reconnect"] = {"down": gone, "strip_down": strip_gone, "back": back}
            # ---- HOT isolation
            a.click("[data-testid=symbol-tab-hot]")
            a.wait_for_function("() => document.querySelector('[data-testid=symbol-tab-hot]').getAttribute('aria-selected')==='true'", timeout=15000)
            t0 = time.time(); a.wait_for_timeout(12000); t1 = time.time()
            hc = a.evaluate(CHART)
            R["hot"] = {"chart": hc, "strip": a.evaluate(STRIP),
                        "ctx_requests": sum(1 for r in reqs if r["tag"] == "ctx-1440" and "/btc-context-api/" in r["u"] and t0 <= r["t"] <= t1),
                        "header": (a.evaluate("() => (document.querySelector('[data-testid=market-header]')||{}).innerText||''") or "")[:60],
                        "ticket": a.evaluate(TICKET)}
            shot_chart(a, "hot-1440")
            a.click("[data-testid=symbol-tab-BTCUSDT]")
            R["hot"]["back"] = wait_lines(a, True, 30)
        c1.close(); c2.close()
    b.close()
nonget = [r for r in reqs if r["m"] not in ("GET", "HEAD")]
R["requests"] = {"total": len(reqs), "non_get": nonget,
                 "ctx_methods": sorted({r["m"] for r in reqs if "/btc-context-api/" in r["u"]})}
json.dump(R, open(f"{OUT}/v2_qa.json", "w"), indent=1, default=str)
print(json.dumps(R, indent=1, default=str)[:12000])
