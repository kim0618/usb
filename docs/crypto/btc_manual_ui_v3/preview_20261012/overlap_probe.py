import sys, re
from playwright.sync_api import sync_playwright
src = open(sys.argv[1]).read()
OVER = re.search(r'OVER = """(.*?)"""', src, re.S).group(1)
with sync_playwright() as p:
    b = p.chromium.launch(); out = {}
    for w in (390, 1440):
        c = b.new_context(viewport={"width": w, "height": 844}, device_scale_factor=2 if w == 390 else 1); pg = c.new_page()
        pg.route("**/*", lambda r: r.continue_() if r.request.url.startswith("http://127.0.0.1:3200/") else r.abort())
        pg.goto("http://127.0.0.1:3200/crypto-paper"); pg.wait_for_selector("[data-testid=btc-context-path-up-row]", timeout=40000)
        pg.wait_for_timeout(1500); out[w] = pg.evaluate(OVER); c.close()
    b.close(); print(out)
