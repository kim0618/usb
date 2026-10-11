import time, json
from playwright.sync_api import sync_playwright
out=[]
with sync_playwright() as p:
    b=p.chromium.launch()
    for w,h in ((1440,900),(390,844)):
        ctx=b.new_context(viewport={"width":w,"height":h}); pg=ctx.new_page(); reqs=[]
        pg.on("request", lambda r: reqs.append((time.time(), r.method, r.url)))
        pg.route("**/*", lambda route: route.continue_() if route.request.url.startswith("http://127.0.0.1:3100/") else route.abort())
        pg.goto("http://127.0.0.1:3100/crypto-paper"); pg.wait_for_selector("[data-testid=btc-context-strip]", timeout=30000); pg.wait_for_timeout(6000)
        pre=sum(1 for t,m,u in reqs if "/btc-context-api/" in u)
        pg.click("[data-testid=symbol-tab-hot]")
        pg.wait_for_function("() => document.querySelector('[data-testid=symbol-tab-hot]').getAttribute('aria-selected')==='true'", timeout=15000)
        t0=time.time(); pg.wait_for_timeout(15000); t1=time.time()
        panel=pg.locator("[data-testid=btc-context-strip]").count()
        chart=pg.locator("canvas").count(); ticket=pg.locator("[data-testid=order-ticket]").count()
        hot_hdr=pg.evaluate("() => (document.querySelector('[data-testid=market-header]')||{}).innerText||''")
        during=[(round(t-t0,2),u) for t,m,u in reqs if t0<=t<=t1 and "/btc-context-api/" in u]
        pg.click("[data-testid=symbol-tab-BTCUSDT]"); pg.wait_for_selector("[data-testid=btc-context-strip]", timeout=15000); t2=time.time(); pg.wait_for_timeout(6000)
        after=sum(1 for t,m,u in reqs if t>=t2-1 and "/btc-context-api/" in u)
        out.append({"w":w,"pre_btc_requests":pre,"hot_window_s":round(t1-t0,1),"hot_btc_ctx_requests":len(during),"during":during,"hot_panel":panel,"hot_canvas":chart,"hot_ticket":ticket,"hot_header":hot_hdr[:80].replace("\n"," "),"after_return_requests":after,"non_get":[x for x in reqs if x[1]!="GET"]})
        ctx.close()
    b.close()
print(json.dumps(out,indent=1))
json.dump(out,open("hot_probe.json","w"),indent=1)
