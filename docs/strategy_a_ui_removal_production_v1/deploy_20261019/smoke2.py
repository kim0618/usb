import json, re, sys
from playwright.sync_api import sync_playwright
BASE, OUT = sys.argv[1], sys.argv[2]
UP = {"https://usb.jptcalc.kr/api/": "http://127.0.0.1:18000/api/",
      BASE + "/crypto-api/": "http://127.0.0.1:18100/", BASE + "/btc-context-api/": "http://127.0.0.1:18014/",
      BASE + "/api/": "http://127.0.0.1:18000/api/"}
A_TERMS = ["Strategy A", "채택", "GPT 분석", "분석 세션", "진입 세션", "TOP8", "Mover", "ScannerRun", "종목 분석",
           "가상매매 시작", "후보 종목", "A+E", "기존 전략", "APPROVE / REJECT", "투자 승인"]
A_API = re.compile(r"/api/v1/(research|scanner|trading|shadow|strategy/evaluations)|/strategies/portfolio|STRATEGY_A")
ROUTES = ["/", "/dashboard", "/strategy-e", "/trading-h", "/strategy-h", "/strategy-compare", "/daily",
          "/strategy-history", "/runtime", "/settings", "/crypto-paper",
          "/trading", "/adoption", "/candidates", "/research", "/shadow"]
cache = {}; log = {"api": [], "blocked": [], "nonget": []}
def handle(route):
    req = route.request; url = req.url
    for pre, up in UP.items():
        if url.startswith(pre):
            if req.method != "GET":
                log["nonget"].append(f"{req.method} {url}"); return route.abort()
            if "binance" in url or "/live" in url and "crypto" in pre:
                log["blocked"].append(url); return route.abort()
            log["api"].append(url.split("?")[0])
            key = url
            if "crypto" in pre and key in cache:
                st, h, body = cache[key]
            else:
                r = route.fetch(url=up + url[len(pre):], timeout=60000)
                st, h, body = r.status, dict(r.headers), r.body()
                if "crypto" in pre: cache[key] = (st, h, body)
            h["access-control-allow-origin"] = "*"; h["access-control-allow-headers"] = "*"
            return route.fulfill(status=st, headers=h, body=body)
    if url.startswith(BASE) or url.startswith("data:"):
        if req.method not in ("GET", "HEAD"): log["nonget"].append(f"{req.method} {url}"); return route.abort()
        return route.continue_()
    log["blocked"].append(url); return route.abort()
res = {}
with sync_playwright() as p:
    b = p.chromium.launch(); ctx = b.new_context(viewport={"width": 1440, "height": 1100}); ctx.route("**/*", handle)
    for r in ROUTES:
        for k in log: log[k].clear()
        pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        resp = pg.goto(BASE + r, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(3000 if r in ROUTES[-5:] else 12000)
        body = pg.inner_text("body")
        q = lambda sel, js: pg.eval_on_selector_all(sel, js)
        res[r] = {"status": resp.status if resp else None, "final": pg.url.replace(BASE, ""), "is404": "404" in body[:600],
                  "a_terms": [t for t in A_TERMS if t in body], "nav": q("nav[aria-label='주요 메뉴'] a", "e=>e.map(x=>[x.textContent.trim(),x.getAttribute('href')])"),
                  "tabs": q("nav[aria-label='트레이딩 화면'] a", "e=>e.map(x=>x.getAttribute('href'))"),
                  "cards": q("[data-dashboard-card]", "e=>e.map(x=>x.getAttribute('data-strategy'))"),
                  "cols": q("th[data-column]", "e=>e.map(x=>x.dataset.column)"), "gates": q("[data-gate]", "e=>e.map(x=>x.dataset.gate)"),
                  "opts": q("[data-strategy-option]", "e=>e.map(x=>x.dataset.strategyOption)"),
                  "a_links": [l for l in q("a[href]", "e=>e.map(x=>x.getAttribute('href'))") if l in ("/trading","/adoption","/candidates","/research","/shadow")],
                  "a_buttons": [t for t in q("button", "e=>e.map(x=>x.textContent.trim())") if re.search(r"채택|거절|APPROVE|REJECT|GPT|프롬프트|분석 결과 입력|이 분석 사용", t)],
                  "a_api": sorted({a for a in log["api"] if A_API.search(a)}), "api_n": len(log["api"]),
                  "nonget": list(log["nonget"]), "errors": errs, "head": body[:900].replace("\n", " | ")}
        pg.screenshot(path=f"{OUT}/{r.strip('/') or 'root'}.png"); pg.close()
    b.close()
json.dump(res, open(f"{OUT}/results.json", "w"), ensure_ascii=False, indent=1)
for r, v in res.items():
    print(f"{r:18} {v['status']} 404={v['is404']} A_terms={v['a_terms']} A_links={v['a_links']} A_btn={v['a_buttons']} A_api={v['a_api']} api={v['api_n']} nonget={len(v['nonget'])} err={v['errors'][:1]}")
d = res["/dashboard"]; print("NAV", d["nav"]); print("TABS", res["/strategy-e"]["tabs"]); print("CARDS", d["cards"])
c = res["/strategy-compare"]; print("COMPARE cols", c["cols"], "gates", c["gates"], "opts", c["opts"]); print("DAILY cols", res["/daily"]["cols"])
