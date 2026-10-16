import json, sys, time
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]; res = {}; blocked = []
def guard(route, req):
    if req.method != "GET" or not req.url.startswith("http://127.0.0.1:18080/"):
        blocked.append(f"{req.method} {req.url[:120]}"); return route.abort()
    return route.continue_()
def tid(page, t): return page.locator(f'[data-testid="{t}"]')
def texts(txt, words): return {w: (w in txt) for w in words}
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 1100})
    ctx.route("**/*", guard)
    page = ctx.new_page(); errors = []
    page.on("console", lambda m: errors.append(m.text[:200]) if m.type == "error" else None)
    page.goto("http://127.0.0.1:18080/crypto-paper", wait_until="domcontentloaded", timeout=90000)
    tid(page, "candle-chart").first.wait_for(timeout=90000)
    page.wait_for_function("() => document.body.innerText.includes('0.064')", timeout=60000)
    time.sleep(12)
    txt = page.inner_text("body")
    def enabled(sel):
        l = page.locator(sel); return [(l.nth(i).get_attribute("data-testid"), l.nth(i).is_enabled()) for i in range(l.count())]
    res["paper"] = {
        "symbol_header": tid(page, "market-header-symbol").first.inner_text() if tid(page, "market-header-symbol").count() else None,
        "hot_scan_button": tid(page, "hot-scan-button").count(),
        "hot_list_text": texts(txt, ["HOT 코인 탐색 결과", "HOT 탐색 결과 보기", "아직 HOT이 없습니다"]),
        "presets": enabled('[data-testid^="preset-"]:is(button)'),
        "long_short": enabled('[data-testid="long-button"], [data-testid="short-button"]'),
        "amount_lines": texts(txt, ["선택 수량", "내가 거는 금액", "이번 주문 규모"]),
        "fee_preview_detail": tid(page, "order-preview-detail").count() + tid(page, "order-preview-detail-toggle").count(),
        "fee_text": texts(txt, ["예상 진입 비용", "왕복 총비용", "강제 청산가", "비용 상세"]),
        "leverage_buttons": [page.locator('[data-testid^="paper-leverage-"]').nth(i).get_attribute("data-testid") for i in range(page.locator('[data-testid^="paper-leverage-"]').count())],
        "half_close": [(page.locator('[data-testid$="half-close"]').nth(i).get_attribute("data-testid"), page.locator('[data-testid$="half-close"]').nth(i).inner_text()[:80], page.locator('[data-testid$="half-close"]').nth(i).is_enabled()) for i in range(page.locator('[data-testid$="half-close"]').count())],
        "close_buttons_group": [page.locator('[data-testid$="close-buttons"]').nth(i).inner_text()[:120] for i in range(page.locator('[data-testid$="close-buttons"]').count())],
        "half_in_order_ticket": tid(page, "order-ticket").first.inner_text().count("HALF CLOSE") if tid(page, "order-ticket").count() else None,
        "analytics": {t: tid(page, t).count() for t in ("performance-panel", "trade-history", "ledger-table", "run-footer")},
        "analytics_text": texts(txt, ["이 세션 성과", "거래내역", "주문체결원장", "런 설정 근거"]),
        "wall_text": texts(txt, ["상단벽1", "하단벽1"]), "c1": page.locator('[data-testid*="c1"]').count(),
        "avg_entry_text": "평균진입" in txt, "position": ("SHORT" in txt, "0.064" in txt, "83,612.9" in txt),
    }
    page.screenshot(path=OUT + "/paper.png")
    live = tid(page, "source-BINANCE_LIVE")
    res["live_button"] = live.count() and live.is_enabled()
    if res["live_button"]:
        t0 = time.time(); live.click()
        try: tid(page, "live-order-ticket").first.wait_for(timeout=20000)
        except Exception as e: res["live_wait_error"] = str(e)[:120]
        time.sleep(6); txt = page.inner_text("body")
        res["live"] = {
            "symbol_header": [page.locator('[data-testid="market-header-symbol"], [data-testid="live-market-header"]').nth(i).inner_text()[:60] for i in range(page.locator('[data-testid="market-header-symbol"], [data-testid="live-market-header"]').count())],
            "trade_state": tid(page, "live-trade-state").first.inner_text()[:80] if tid(page, "live-trade-state").count() else None,
            "presets": enabled('[data-testid^="live-preset-"]'),
            "leverage_options": tid(page, "live-leverage-options").first.inner_text()[:120] if tid(page, "live-leverage-options").count() else None,
            "amount_lines": texts(txt, ["선택 수량", "내가 거는 금액", "이번 주문 규모"]),
            "ttl_text": texts(txt, ["제한 시간", "15분", "직접 해제할 때까지"]),
            "account_cards": tid(page, "live-account-cards").count() + tid(page, "live-account-compact").count(),
            "paper_balance_leak": "10,138" in txt or "10,233" in txt,
            "half_close": page.locator('[data-testid^="live-"][data-testid$="half-close"]').count(),
            "wall_text": texts(txt, ["상단벽1", "하단벽1"]), "c1": page.locator('[data-testid*="c1"]').count(),
            "seconds_on_live": round(time.time() - t0, 1)}
        page.screenshot(path=OUT + "/live.png")
        tid(page, "source-PAPER").click(); time.sleep(1)
    res["console_errors"] = errors[:10]; res["blocked"] = blocked[:20]; res["blocked_n"] = len(blocked)
    b.close()
print(json.dumps(res, ensure_ascii=False, indent=1))
