"""Dynamic HOT V1 browser QA against the real local terminal (PAPER, public data, no key)."""
import glob, json, math, os, time, urllib.error, urllib.parse, urllib.request
from decimal import Decimal
from playwright.sync_api import sync_playwright

Q = "<scratch>/v1qa"
SHOTS = f"{Q}/ui2"; os.makedirs(SHOTS, exist_ok=True)
URL = "http://127.0.0.1:3199/crypto-paper"
API = "http://127.0.0.1:8199"
chrome = glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux*/chrome"))[0]
os.environ["LD_LIBRARY_PATH"] = os.path.expanduser("~/.local/emulib")
R = {}


def api(path):
    with urllib.request.urlopen(API + path, timeout=20) as r:
        return json.loads(r.read())


def position(symbol):
    s = api(f"/api/crypto/state?symbol={urllib.parse.quote(symbol)}")
    acct = s.get("account") or {}
    return acct.get("position_side"), acct.get("position_qty") or acct.get("qty")


def visible(page, testid):
    return page.locator(f'[data-testid="{testid}"]').locator("visible=true").first


def box(page, testid):
    b = visible(page, testid).bounding_box()
    return None if b is None else [round(b["x"]), round(b["y"]), round(b["width"]), round(b["height"])]


def overflow(page):
    return page.evaluate("() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]")


def wait_feed_live(page, timeout=30000):
    page.wait_for_function("() => !document.querySelector('[data-testid=stale-entry-block]')", timeout=timeout)


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
    # ------------------------------------------------------------------ 1440: full scenario
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = ctx.new_page()
    reqs = []
    page.on("request", lambda r: reqs.append({"m": r.method, "u": r.url, "t": time.time()}) if "8199" in r.url else None)
    page.goto(URL)
    page.wait_for_selector('[data-testid="market-header"]', timeout=40000)
    page.wait_for_function("() => !document.querySelector('[data-testid=hot-scan-button]').disabled", timeout=20000)
    hot = api("/api/crypto/hot")
    hot_sym = hot["hot"]["symbol"] if hot["hot"] else None
    R["start"] = {"hot": hot_sym, "tabs": page.locator('[role=tab]').evaluate_all("e => e.map(x => [x.dataset.testid, x.dataset.symbol, x.innerText.replace(/\\n/g,' ')])"),
                  "selected": page.locator('[role=tab][aria-selected=true]').get_attribute("data-testid"),
                  "ticket": box(page, "order-ticket"), "overflow": overflow(page)}
    page.screenshot(path=f"{SHOTS}/1440_0_start.png")

    # A. BTC position (QTY 0.002) while HOT exists and is flat
    wait_feed_live(page)
    visible(page, "order-ticket").locator("#crypto-size").fill("0.002")
    visible(page, "long-button").click()
    page.wait_for_function("() => document.querySelector('[data-testid=position-side]') && document.querySelector('[data-testid=position-side]').innerText.includes('LONG')", timeout=15000)
    btc_pos = api("/api/crypto/state?symbol=BTCUSDT")["account"]
    R["A_btc"] = {"side": btc_pos.get("position_side"), "qty": btc_pos.get("position_qty"), "hot_lock": api("/api/crypto/hot")["lock"]["code"]}

    # B0. the seeded previous HOT (BAT) is replaced by the real #1 through the button
    old_hot = hot_sym
    R["A_lock_before_scan"] = {"server": api("/api/crypto/hot")["lock"]["code"],
                               "button_enabled": not visible(page, "hot-scan-button").is_disabled()}
    visible(page, "hot-scan-button").click()
    page.wait_for_function("() => !document.querySelector('[data-testid=hot-scan-button]').disabled", timeout=40000)
    page.wait_for_timeout(500)
    hot_sym = api("/api/crypto/hot")["hot"]["symbol"]
    t_switch = time.time()
    page.wait_for_timeout(4000)
    try:
        status_old = urllib.request.urlopen(API + f"/api/crypto/state?symbol={old_hot}").status
    except urllib.error.HTTPError as e:
        status_old = e.code
    R["B0_replacement"] = {"old": old_hot, "new": hot_sym, "replaced": hot_sym != old_hot,
                           "old_state_status": status_old,
                           "old_in_permitted": old_hot in api("/api/crypto/hot")["permitted"],
                           "browser_requests_for_old_after_switch": sum(1 for r in reqs if r["t"] > t_switch and f"symbol={old_hot}" in r["u"])}

    # B. select HOT tab, chart from Binance, open a HOT position (~20 USDT)
    visible(page, "symbol-tab-hot").click()
    page.wait_for_function(f"() => document.querySelector('[data-testid=symbol-tab-hot]').getAttribute('aria-selected') === 'true'")
    page.wait_for_function(f"() => {{ const h = document.querySelector('[data-testid=market-header]'); return h && h.innerText.includes('{hot_sym[:-4]}'); }}", timeout=30000)
    wait_feed_live(page)
    state = api(f"/api/crypto/state?symbol={urllib.parse.quote(hot_sym)}")
    price = Decimal(state["quote"]["best_ask"]); step = Decimal(state["instrument"]["qty_step"])
    qty = (Decimal(20) / price / step).to_integral_value(rounding="ROUND_CEILING") * step
    page.wait_for_timeout(1500)
    R["B_chart"] = {"canvas": page.locator("canvas").count(), "header": visible(page, "market-header").inner_text()[:80],
                    "feed_source": state["feed"].get("source"), "instrument": state["instrument"]}
    page.screenshot(path=f"{SHOTS}/1440_1_hot_selected.png")
    visible(page, "order-ticket").locator("#crypto-size").fill(str(qty))
    visible(page, "long-button").click()
    page.wait_for_function("() => document.querySelector('[data-testid=position-side]') && document.querySelector('[data-testid=position-side]').innerText.includes('LONG')", timeout=15000)
    hot_acct = api(f"/api/crypto/state?symbol={urllib.parse.quote(hot_sym)}")["account"]
    btc_after = api("/api/crypto/state?symbol=BTCUSDT")["account"]
    R["B_positions"] = {"hot": [hot_acct.get("position_side"), hot_acct.get("position_qty")], "order_qty": str(qty),
                        "btc_unchanged": [btc_after.get("position_side"), btc_after.get("position_qty")] == [R["A_btc"]["side"], R["A_btc"]["qty"]]}

    # C. HOT position -> scan blocked (server lock, picked up by the poll)
    t_open = time.time()
    page.wait_for_function("() => { const b = document.querySelector('[data-testid=hot-scan-button]'); return b.disabled && (b.title || '').includes('포지션'); }", timeout=12000)
    blocked_after = round(time.time() - t_open, 1)
    R["C_blocked"] = {"blocked_after_s": blocked_after, "button_disabled": visible(page, "hot-scan-button").is_disabled(),
                      "title": visible(page, "hot-scan-button").get_attribute("title"),
                      "server_lock": api("/api/crypto/hot")["lock"]["code"],
                      "direct_post": None}
    req = urllib.request.Request(API + "/api/crypto/hot/scan", method="POST")
    try:
        urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as e:
        R["C_blocked"]["direct_post"] = [e.code, json.loads(e.read())["error"]["code"]]
    page.screenshot(path=f"{SHOTS}/1440_2_hot_position_blocked.png")

    # D. CLOSE HOT -> backend flat -> button back on
    visible(page, "close-button").click()
    page.wait_for_function("() => !document.querySelector('[data-testid=position-side]') || !document.querySelector('[data-testid=position-side]').innerText.includes('LONG')", timeout=15000)
    t0 = time.time()
    page.wait_for_function("() => { const b = document.querySelector('[data-testid=hot-scan-button]'); return !b.disabled && !(b.title || '').includes('포지션'); }", timeout=12000)
    hot_after_close = api(f"/api/crypto/state?symbol={urllib.parse.quote(hot_sym)}")["account"]
    btc_after2 = api("/api/crypto/state?symbol=BTCUSDT")["account"]
    R["D_closed"] = {"hot_flat": hot_after_close.get("position_side") in (None, "FLAT"), "button_reenabled_after_s": round(time.time() - t0, 1),
                     "btc_unchanged": [btc_after2.get("position_side"), btc_after2.get("position_qty")] == [R["A_btc"]["side"], R["A_btc"]["qty"]],
                     "server_lock": api("/api/crypto/hot")["lock"]["code"]}

    # E. new scan (real market decides whether #1 changed)
    mark = len(reqs)
    visible(page, "hot-scan-button").dblclick()
    page.wait_for_function("() => !document.querySelector('[data-testid=hot-scan-button]').disabled", timeout=40000)
    page.wait_for_timeout(500)
    after = api("/api/crypto/hot")
    new_sym = after["hot"]["symbol"]
    R["E_rescan"] = {"posts": sum(1 for r in reqs[mark:] if r["m"] == "POST" and "/hot/scan" in r["u"]),
                     "previous": hot_sym, "now": new_sym,
                     "selected": page.locator('[role=tab][aria-selected=true]').get_attribute("data-symbol"),
                     "top": [t["symbol"] for t in after["hot"]["top"]]}
    page.screenshot(path=f"{SHOTS}/1440_3_after_rescan.png")
    if new_sym != hot_sym:
        t_switch = time.time()
        page.wait_for_timeout(4000)
        old = [r for r in reqs if r["t"] > t_switch + 1 and f"symbol={urllib.parse.quote(hot_sym)}" in r["u"]]
        R["E_rescan"]["old_symbol_requests_after_switch"] = len(old)
        R["E_rescan"]["old_state_status"] = urllib.request.urlopen(urllib.request.Request(API + f"/api/crypto/hot")).status
    # reload keeps HOT and the selection
    page.reload(); page.wait_for_selector('[data-testid="market-header"]', timeout=40000)
    page.wait_for_function("() => !document.querySelector('[data-testid=hot-scan-button]').disabled", timeout=20000)
    R["reload"] = {"hot_tab": visible(page, "symbol-tab-hot").get_attribute("data-symbol"),
                   "selected": page.locator('[role=tab][aria-selected=true]').get_attribute("data-symbol")}
    R["requests_1440"] = {"total": len(reqs), "methods": sorted({r["m"] for r in reqs}),
                          "non_get": sorted({(r["m"], urllib.parse.urlparse(r["u"]).path) for r in reqs if r["m"] not in ("GET", "OPTIONS")}),
                          "binance_live_non_get": sum(1 for r in reqs if r["m"] not in ("GET", "OPTIONS") and "/binance/" in r["u"])}
    ctx.close()

    # ------------------------------------------------------------------ 390 / 1600: layout
    for width in (390, 1440, 1600):
        ctx = browser.new_context(viewport={"width": width, "height": 900})
        page = ctx.new_page()
        page.goto(URL); page.wait_for_selector('[data-testid="market-header"]', timeout=40000)
        page.wait_for_function("() => !document.querySelector('[data-testid=hot-scan-button]').disabled", timeout=20000)
        page.wait_for_timeout(1500)
        r = {"tabs": page.locator('[role=tab]').evaluate_all("e => e.map(x => { const b = x.getBoundingClientRect(); return [x.dataset.testid, x.dataset.symbol, Math.round(b.x), Math.round(b.width), x.scrollWidth <= x.clientWidth + 1]; })"),
             "strip": page.evaluate("() => { const t = document.querySelector('[data-testid=symbol-tabs]'); return [t.scrollWidth, t.clientWidth]; }"),
             "button": box(page, "hot-scan-button"), "ticket": box(page, "order-ticket"), "overflow": overflow(page)}
        page.screenshot(path=f"{SHOTS}/{width}_0_tabs.png")
        visible(page, "symbol-tab-hot").click()
        page.wait_for_function("() => document.querySelector('[data-testid=symbol-tab-hot]').getAttribute('aria-selected') === 'true'")
        page.wait_for_timeout(2500)
        r["hot_selected_overflow"] = overflow(page)
        r["pressed_tabs"] = page.locator('[role=tab][aria-selected=true]').evaluate_all("e => e.map(x => x.dataset.symbol)")
        r["mark_text"] = visible(page, "mark-price").inner_text()
        r["hot_ticket"] = box(page, "order-ticket")
        page.screenshot(path=f"{SHOTS}/{width}_1_hot_tab.png")
        visible(page, "hot-show-button").click()
        page.wait_for_selector('[data-testid="hot-coins-panel"]')
        r["panel"] = box(page, "hot-coins-panel"); r["panel_overflow"] = overflow(page)
        page.screenshot(path=f"{SHOTS}/{width}_2_panel.png")
        page.reload(); page.wait_for_selector('[data-testid="market-header"]', timeout=40000)
        page.wait_for_function("() => !document.querySelector('[data-testid=hot-scan-button]').disabled", timeout=20000)
        page.wait_for_timeout(1500)
        r["pressed_after_reload"] = page.locator('[role=tab][aria-selected=true]').evaluate_all("e => e.map(x => x.dataset.symbol)")
        R[f"layout_{width}"] = r
        ctx.close()
    browser.close()

json.dump(R, open(f"{SHOTS}/report.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(R, ensure_ascii=False, indent=1))
