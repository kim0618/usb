import json, sys, time
from playwright.sync_api import sync_playwright
S = sys.argv[1]
URL = "http://127.0.0.1:3100/crypto-paper"
STALE = json.load(open("/tmp/claude-1000/-home-tjd618/3967451c-25c7-451e-98a5-327234cf09a9/scratchpad/fe/frontend/components/__fixtures__/btc-context-snapshot.json"))["stale"]
VIEWPORTS = [(1600, 1000), (1440, 900), (390, 844)]
results = {"runs": [], "scenarios": {}}
requests = []

JS_TICKET = """() => { const t=[...document.querySelectorAll('[data-testid=order-ticket]')].find(e=>e.offsetParent!==null);
  if(!t) return null; const r=t.getBoundingClientRect(); return {x:r.x, y:r.y+window.scrollY, w:r.width, h:r.height}; }"""
JS_STRIP = """() => { const s=document.querySelector('[data-testid=btc-context-strip]'); if(!s) return null;
  const r=s.getBoundingClientRect(); return {x:r.x, y:r.y+window.scrollY, w:r.width, h:r.height, text:s.innerText,
  live:[...s.querySelectorAll('[data-testid^=btc-context-]')].map(e=>e.dataset.testid+':'+e.dataset.live)}; }"""
JS_OVERFLOW = """() => ({doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  body: document.body.scrollWidth - document.body.clientWidth,
  strip: (()=>{const s=document.querySelector('[data-testid=btc-context-strip]'); if(!s) return null;
    return [...s.querySelectorAll('*')].reduce((m,e)=>Math.max(m,e.scrollWidth-e.clientWidth>0 && getComputedStyle(e).overflow!=='hidden' && !e.classList.contains('truncate')?e.scrollWidth-e.clientWidth:0),0);})(),
  truncated: (()=>{const s=document.querySelector('[data-testid=btc-context-strip]'); if(!s) return null;
    return [...s.querySelectorAll('.truncate')].filter(e=>e.scrollWidth>e.clientWidth).map(e=>e.innerText);})()})"""

external = []
BANNED = ["Q1", "Q2", "Q3", "LONG candidate", "SHORT candidate", "research", "sample", "p-value", "confidence"]
def hook(page, tag):
    page.on("request", lambda r: requests.append({"tag": tag, "method": r.method, "url": r.url, "t": time.time()}))
    def guard(route):
        if route.request.url.startswith("http://127.0.0.1:3100/"):
            return route.continue_()
        external.append({"tag": tag, "method": route.request.method, "url": route.request.url}); route.abort()
    page.route("**/*", guard)

def wait_live_strip(page, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        s = page.evaluate(JS_STRIP)
        if s and "BUY --" not in s["text"] and "R --" not in s["text"] and "OI --" not in s["text"]:
            return s
        page.wait_for_timeout(1000)
    return page.evaluate(JS_STRIP)

with sync_playwright() as p:
    browser = p.chromium.launch()
    for w, h in VIEWPORTS:
        ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
        page = ctx.new_page(); tag = f"main-{w}"; hook(page, tag)
        page.goto(URL); page.wait_for_selector("[data-testid=market-header]", timeout=30000)
        strip = wait_live_strip(page)
        page.wait_for_timeout(500)
        ticket_with = page.evaluate(JS_TICKET)
        over = page.evaluate(JS_OVERFLOW)
        page.screenshot(path=f"{S}/shots/btc-{w}.png", full_page=True)
        page.locator("[data-testid=btc-context-strip]").screenshot(path=f"{S}/shots/strip-{w}.png")
        # Same page load: remove the strip from layout and measure the ticket again.
        page.evaluate("() => { document.querySelector('[data-testid=btc-context-strip]').style.display='none'; }")
        page.wait_for_timeout(200)
        ticket_without = page.evaluate(JS_TICKET)
        page.evaluate("() => { document.querySelector('[data-testid=btc-context-strip]').style.display=''; }")
        # HOT tab: strip gone, and no strip request while there.
        body_text = page.evaluate("() => document.body.innerText")
        banned_strip = {w: strip["text"].count(w) for w in BANNED} if strip else None
        banned_body = {w: body_text.count(w) for w in BANNED}
        long_enabled_main = page.evaluate("() => [...document.querySelectorAll('[data-testid=long-button]')].filter(e=>e.offsetParent!==null).map(e=>!e.disabled)")
        page.click("[data-testid=symbol-tab-hot]")
        page.wait_for_function("() => document.querySelector('[data-testid=symbol-tab-hot]').getAttribute('aria-selected')==='true'", timeout=15000)
        t0 = time.time(); page.wait_for_timeout(500)
        hot_strip_count = page.locator("[data-testid=btc-context-strip]").count()
        page.wait_for_timeout(7000)
        hot_polls = sum(1 for r in requests if r["tag"] == tag and r["url"].endswith("/snapshot") and r["t"] > t0 + 0.5)
        hot_over = page.evaluate(JS_OVERFLOW)
        page.screenshot(path=f"{S}/shots/hot-{w}.png", full_page=False)
        page.click("[data-testid=symbol-tab-BTCUSDT]")
        page.wait_for_selector("[data-testid=btc-context-strip]", timeout=15000)
        back = wait_live_strip(page, 20)
        results["runs"].append({"width": w, "strip": strip, "ticket_with": ticket_with, "ticket_without": ticket_without,
            "ticket_shift_px": None if not (ticket_with and ticket_without) else {k: round(ticket_with[k]-ticket_without[k], 2) for k in ("x","y","w","h")},
            "overflow": over, "hot_strip_count": hot_strip_count, "hot_snapshot_polls_7s": hot_polls,
            "hot_overflow": hot_over, "back_on_btc": back and back["text"],
            "banned_strip": banned_strip, "banned_body": banned_body, "long_enabled": long_enabled_main,
            "hot_btc_ctx_requests_7s": sum(1 for r in requests if r["tag"] == tag and "/btc-context-api/" in r["url"] and r["t"] > t0 + 0.5)})
        ctx.close()
    # API unavailable from the first request.
    for name, handler in (("api_down", lambda route: route.abort()),
                          ("api_stale", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(STALE))),
                          ("api_500", lambda route: route.fulfill(status=500, body="boom"))):
        for w, h in ((1440, 900), (390, 844)):
            ctx = browser.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page(); tag = f"{name}-{w}"; hook(page, tag)
            page.route("**/snapshot", handler)
            page.goto(URL); page.wait_for_selector("[data-testid=market-header]", timeout=30000)
            page.wait_for_selector("[data-testid=btc-context-strip]", timeout=15000)
            page.wait_for_timeout(6000)
            s = page.evaluate(JS_STRIP)
            long_enabled = page.evaluate("() => [...document.querySelectorAll('[data-testid=long-button]')].filter(e=>e.offsetParent!==null).map(e=>!e.disabled)")
            alerts = page.evaluate("() => [...document.querySelectorAll('[role=alert]')].map(e => ({id: e.id, tag: e.tagName, in_strip: !!e.closest('[data-testid=btc-context-strip]'), text: e.innerText}))")
            page.locator("[data-testid=btc-context-strip]").screenshot(path=f"{S}/shots/strip-{name}-{w}.png")
            results["scenarios"][f"{name}-{w}"] = {"strip": s, "long_enabled": long_enabled, "alerts": alerts,
                                                   "overflow": page.evaluate(JS_OVERFLOW), "ticket": page.evaluate(JS_TICKET)}
            ctx.close()
    browser.close()
results["external_blocked"] = external
results["requests"] = {"total": len(requests),
    "by_method": {m: sum(1 for r in requests if r["method"] == m) for m in sorted({r["method"] for r in requests})},
    "non_get": [r for r in requests if r["method"] not in ("GET", "HEAD")],
    "snapshot_methods": sorted({r["method"] for r in requests if r["url"].endswith("/snapshot")}),
    "snapshot_count": sum(1 for r in requests if r["url"].endswith("/snapshot"))}
json.dump(results, open(f"{S}/qa_results.json", "w"), indent=1, default=str)
print(json.dumps(results, indent=1, default=str)[:6000])
