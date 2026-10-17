import json, sys, time, re, copy
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]; HOT = sys.argv[2]; res = {}; blocked = []; patched = {"account": 0, "arm": 0, "position": 0}
SIM = {"armed": False, "position": False}
def fake_position(p, mark):
    p = dict(p or {}); q = "1000"
    p.update({"symbol": HOT, "side": "LONG", "qty": q, "signed_qty": q, "is_flat": False,
              "entry_price": str(mark), "notional": str(round(float(mark) * 1000, 4)), "unrealized_pnl": "0", "leverage": "20"})
    return p
def guard(route, req):
    url = req.url
    if req.method != "GET":
        blocked.append(f"{req.method} {url[:120]}"); return route.abort()
    if url.startswith("https://usb.jptcalc.kr/"):
        url = "http://127.0.0.1:18080/" + url[len("https://usb.jptcalc.kr/"):]
    elif not url.startswith("http://127.0.0.1:18080/"):
        blocked.append(f"EXT {url[:120]}"); return route.abort()
    sim = SIM["armed"] and "/api/crypto/binance/" in url
    if url == req.url and not sim: return route.continue_()
    if SIM["position"] and "/api/crypto/binance/position" in url and "positions" not in url:
        patched["position"] += 1
        return route.fulfill(status=404, content_type="application/json", body='{"error":{"code":"SIM","message":"sim"}}')
    r = route.fetch(url=url)
    if not sim: return route.fulfill(response=r)
    try: body = r.json()
    except Exception: return route.fulfill(response=r)
    if "/binance/account" in url and isinstance(body, dict) and body.get("gates"):
        g = body["gates"]; g["armed"] = True; g["client_armed"] = True
        if isinstance(g.get("arm_session"), dict): g["arm_session"].update({"armed": True, "armed_by": "SESSION"})
        if SIM["position"] and body.get("symbol") == HOT:
            mark = (body.get("mark") or {}).get("mark_price") or "0.067"
            body["position"] = fake_position(body.get("position"), mark)
        patched["account"] += 1
    if re.search(r"/binance/arm(\?|$)", url) and isinstance(body, dict):
        body.update({"armed": True, "armed_by": "SESSION"}); patched["arm"] += 1
    return route.fulfill(status=r.status, headers={"content-type": "application/json"}, body=json.dumps(body))
def tid(page, t): return page.locator(f'[data-testid="{t}"]')
def en(page, sel):
    l = page.locator(sel); return {l.nth(i).get_attribute("data-testid"): l.nth(i).is_enabled() for i in range(l.count())}
BAD = ["조회만 가능", "HOT 심볼 LIVE", "HOT_LIVE_ORDERS_DISABLED", "BINANCE_LIVE_HOT_ORDERS"]
def read_live(page, label):
    time.sleep(8); txt = page.inner_text("body")
    st = tid(page, "live-trade-state")
    detail = None
    if st.count():
        if st.first.get_attribute("aria-expanded") != "true": st.first.click(); time.sleep(0.5)
        d = tid(page, "live-trade-detail"); detail = d.first.inner_text()[:300] if d.count() else None
    r = {"header": [page.locator('[data-testid="market-header-symbol"], [data-testid="live-market-header"]').nth(i).inner_text()[:50] for i in range(page.locator('[data-testid="market-header-symbol"], [data-testid="live-market-header"]').count())],
         "hot_tab_selected": tid(page, "symbol-tab-hot").first.get_attribute("aria-selected") if tid(page, "symbol-tab-hot").count() else None,
         "trade_state": st.first.inner_text() if st.count() else None, "trade_detail": detail,
         "bad_text": {w: w in txt for w in BAD},
         "long_short_close": en(page, '[data-testid="live-long"], [data-testid="live-short"], [data-testid="live-close"]'),
         "presets": en(page, '[data-testid^="live-preset-"]'),
         "leverage": en(page, '[data-testid^="live-leverage-"]:is(button)'),
         "half_full": en(page, '[data-testid$="half-close"]:is(button), [data-testid$="-full-close"]:is(button), [data-testid="live-card-close"]'),
         "close_groups": [page.locator('[data-testid$="close-buttons"]').nth(i).inner_text()[:120] for i in range(page.locator('[data-testid$="close-buttons"]').count())],
         "order_locked": tid(page, "live-order-locked").count(), "order_activate": tid(page, "live-order-activate").count(),
         "leverage_locked_msg": tid(page, "live-leverage-locked").count(),
         "holding": tid(page, "live-ticket-holding").first.inner_text()[:80] if tid(page, "live-ticket-holding").count() else None}
    page.screenshot(path=f"{OUT}/{label}.png", full_page=True)
    return r
with sync_playwright() as p:
    b = p.chromium.launch(executable_path='/home/tjd618/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell')
    def session(label, armed, position, symbol_tab):
        SIM["armed"], SIM["position"] = armed, position
        ctx = b.new_context(viewport={"width": 1440, "height": 1100}); ctx.route("**/*", guard)
        page = ctx.new_page(); errs = []
        page.on("console", lambda m: errs.append(m.text[:160]) if m.type == "error" else None)
        page.goto("http://127.0.0.1:18080/crypto-paper", wait_until="domcontentloaded", timeout=90000)
        tid(page, "candle-chart").first.wait_for(timeout=90000); time.sleep(4)
        tid(page, "source-BINANCE_LIVE").first.click()
        tid(page, "live-order-ticket").first.wait_for(timeout=30000)
        t = tid(page, symbol_tab); t.first.click()
        r = read_live(page, label); r["console_errors"] = errs[:6]
        ctx.unroute_all(behavior="ignoreErrors"); ctx.close()
        json.dump(r, open(f"{OUT}/{label}.json", "w"), ensure_ascii=False, indent=1); return r
    ONLY = sys.argv[3] if len(sys.argv) > 3 else "all"
    if ONLY in ("all", "A"):
        res["A_real_disarmed_HOT"] = session("A_hot_disarmed", False, False, "symbol-tab-hot")
        res["A_real_disarmed_BTC"] = session("A_btc_disarmed", False, False, "symbol-tab-BTCUSDT")
    if ONLY in ("all", "B"):
        res["B1_sim_armed_HOT_flat"] = session("B1_hot_armed", True, False, "symbol-tab-hot")
        res["B2_sim_armed_HOT_position"] = session("B2_hot_armed_pos", True, True, "symbol-tab-hot")
    if ONLY in ("all", "C"):
        res["C_sim_armed_BTCUSDT"] = session("C_BTCUSDT_armed", True, False, "symbol-tab-BTCUSDT")
    b.close()
res["patched"] = patched; res["blocked"] = blocked[:20]; res["blocked_n"] = len(blocked)
print(json.dumps(res, ensure_ascii=False, indent=1))
