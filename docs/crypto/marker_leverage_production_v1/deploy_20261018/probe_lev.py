import json, sys, time, re
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]; res = {}; blocked = []
SIM = {"armed": False, "leverage": None, "position": None}
def guard(route, req):
    url = req.url
    if req.method != "GET":
        blocked.append(f"{req.method} {url[:120]}"); return route.abort()
    if url.startswith("https://usb.jptcalc.kr/"):
        url = "http://127.0.0.1:18080/" + url[len("https://usb.jptcalc.kr/"):]
    elif not url.startswith("http://127.0.0.1:18080/"):
        blocked.append(f"EXT {url[:120]}"); return route.abort()
    sim = (SIM["armed"] or SIM["leverage"] or SIM["position"]) and "/api/crypto/binance/" in url
    if url == req.url and not sim: return route.continue_()
    r = route.fetch(url=url)
    if not sim: return route.fulfill(response=r)
    try: body = r.json()
    except Exception: return route.fulfill(response=r)
    if isinstance(body, dict):
        if "/binance/account" in url and body.get("gates"):
            if SIM["armed"]:
                body["gates"]["armed"] = True; body["gates"]["client_armed"] = True
            if SIM["leverage"] and body.get("symbol_config"):
                body["symbol_config"]["leverage"] = SIM["leverage"]
            if SIM["position"] and body.get("symbol") == SIM["position"]["symbol"]:
                p = dict(body.get("position") or {}); mark = (body.get("mark") or {}).get("mark_price") or "1"
                p.update({"side": "LONG", "qty": SIM["position"]["qty"], "signed_qty": SIM["position"]["qty"], "is_flat": False,
                          "entry_price": mark, "notional": str(float(mark) * float(SIM["position"]["qty"]))}); body["position"] = p
        if re.search(r"/binance/arm(\?|$)", url) and SIM["armed"]: body.update({"armed": True, "armed_by": "SESSION"})
        if "/binance/leverage" in url and SIM["leverage"] and "max_leverage" in body:
            cur = int(SIM["leverage"]); body["current"] = SIM["leverage"]
            body["current_valid"] = cur <= body["max_leverage"]
            body["current_invalid_message"] = None if body["current_valid"] else f"현재 심볼({body['symbol']})은 최대 {body['max_leverage']}x입니다. 레버리지를 다시 선택하세요."
        if SIM["position"] and "/binance/position" in url and "positions" not in url:
            return route.fulfill(status=404, content_type="application/json", body='{"error":{"code":"SIM","message":"sim"}}')
    return route.fulfill(status=r.status, headers={"content-type": "application/json"}, body=json.dumps(body))
def tid(page, t): return page.locator(f'[data-testid="{t}"]')
def en(page, sel):
    l = page.locator(sel); return {l.nth(i).get_attribute("data-testid"): l.nth(i).is_enabled() for i in range(l.count())}
def txt_of(page, t): return tid(page, t).first.inner_text()[:200] if tid(page, t).count() else None
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/home/tjd618/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell")
    def session(label, tab, armed=False, leverage=None, position=None):
        SIM.update({"armed": armed, "leverage": leverage, "position": position})
        ctx = b.new_context(viewport={"width": 1440, "height": 1100}); ctx.route("**/*", guard)
        page = ctx.new_page(); errs = []
        page.on("console", lambda m: errs.append(m.text[:160]) if m.type == "error" else None)
        page.goto("http://127.0.0.1:18080/crypto-paper", wait_until="domcontentloaded", timeout=90000)
        tid(page, "candle-chart").first.wait_for(timeout=90000); time.sleep(4)
        paper_markers = page.locator("[data-markers]").first.get_attribute("data-markers") if page.locator("[data-markers]").count() else None
        paper_lev = [page.locator('[data-testid^="paper-leverage-"]').nth(i).get_attribute("data-testid") for i in range(page.locator('[data-testid^="paper-leverage-"]').count())]
        tid(page, "source-BINANCE_LIVE").first.click(); tid(page, "live-order-ticket").first.wait_for(timeout=30000)
        tid(page, tab).first.click()
        t0 = time.time()
        try: page.locator('[data-testid^="live-leverage-"]:is(button)').first.wait_for(timeout=45000)
        except Exception: pass
        time.sleep(4); waited = round(time.time() - t0, 1)
        body = page.inner_text("body")
        r = {"header": txt_of(page, "market-header-symbol"), "trade_state": txt_of(page, "live-trade-state"),
             "leverage": en(page, '[data-testid^="live-leverage-"]:is(button)'),
             "leverage_max": txt_of(page, "live-leverage-max"), "leverage_invalid": txt_of(page, "live-leverage-invalid"),
             "order_leverage_invalid": txt_of(page, "live-order-leverage-invalid"),
             "long_short_close": en(page, '[data-testid="live-long"], [data-testid="live-short"], [data-testid="live-close"]'),
             "presets": en(page, '[data-testid^="live-preset-"]'),
             "half_full": en(page, '[data-testid$="half-close"]:is(button), [data-testid$="-full-close"]:is(button)'),
             "live_markers": page.locator("[data-markers]").first.get_attribute("data-markers") if page.locator("[data-markers]").count() else None,
             "paper_markers": paper_markers, "paper_leverage": paper_lev,
             "waited_s": waited, "bad": {w: w in body for w in ("조회만 가능", "HOT 심볼 LIVE")}, "console_errors": errs[:5]}
        page.screenshot(path=f"{OUT}/{label}.png", full_page=True)
        ctx.unroute_all(behavior="ignoreErrors"); ctx.close()
        json.dump(r, open(f"{OUT}/{label}.json", "w"), ensure_ascii=False, indent=1); return r
    res["A_real_HOT"] = session("A_real_hot", "symbol-tab-hot")
    res["A_real_BTC"] = session("A_real_btc", "symbol-tab-BTCUSDT")
    res["B_sim_invalid_50x_HOT"] = session("B_invalid_hot", "symbol-tab-hot", armed=True, leverage="50")
    res["B2_sim_invalid_50x_HOT_position"] = session("B2_invalid_hot_pos", "symbol-tab-hot", armed=True, leverage="50", position={"symbol": sys.argv[2], "qty": "1000"})
    res["C_sim_armed_valid_HOT"] = session("C_armed_hot", "symbol-tab-hot", armed=True)
    b.close()
res["blocked"] = blocked[:20]; res["blocked_n"] = len(blocked)
print(json.dumps(res, ensure_ascii=False, indent=1))
