"""Control for the first-load price span: two pages without context and two with, side by side."""
import json, time
from playwright.sync_api import sync_playwright
URL = "http://127.0.0.1:3100/crypto-paper"
CH = """() => { const c=document.querySelector('[data-testid=candle-chart]'); return c ? {span:c.dataset.priceSpan,
  bars:c.dataset.visibleBars, to:c.dataset.rangeTo, last:c.dataset.lastIndex, lines:c.dataset.contextLines||''} : null; }"""
out = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for w, h in ((1440, 900), (390, 844), (1600, 1000)):
        pages = []
        for name, block in (("bareA", True), ("bareB", True), ("ctxA", False), ("ctxB", False)):
            ctx = b.new_context(viewport={"width": w, "height": h})
            pg = ctx.new_page()
            def guard(route, block=block):
                u = route.request.url
                if not u.startswith("http://127.0.0.1:3100/") or (block and "/btc-context-api/" in u): return route.abort()
                return route.continue_()
            pg.route("**/*", guard)
            pg.goto(URL)
            pages.append((name, ctx, pg))
        for _, _, pg in pages: pg.wait_for_selector("[data-testid=candle-chart] canvas", timeout=40000)
        time.sleep(12)
        for k in range(3):
            row = {"w": w, "k": k, **{name: pg.evaluate(CH) for name, _, pg in pages}}
            out.append(row); time.sleep(2)
        for _, ctx, _ in pages: ctx.close()
    b.close()
for r in out:
    print(r["w"], r["k"], " | ".join(f'{n}: span {r[n]["span"]} bars {r[n]["bars"]} to {r[n]["to"]}/{r[n]["last"]}' for n in ("bareA","bareB","ctxA","ctxB")))
json.dump(out, open("span_probe.json", "w"), indent=1)
