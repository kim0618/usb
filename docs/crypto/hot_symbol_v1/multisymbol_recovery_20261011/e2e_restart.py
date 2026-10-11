"""PAPER E2E with a process restart in the middle (real local terminal, public data, no key).

BTC position -> HOT select -> HOT position -> restart backend -> both recovered ->
scan blocked (API + screen) -> HOT close -> scan re-enabled -> restart again -> still flat.
"""
import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

from playwright.sync_api import sync_playwright

Q = os.path.dirname(os.path.abspath(__file__))
RUN, ROOT = sys.argv[1], sys.argv[2]          # run_config path, paper root
API, URL = "http://127.0.0.1:8199", "http://127.0.0.1:3199/crypto-paper"
BACKEND = os.path.expanduser("~/usb-hot-coin-finder-r0/backend")
PY = "/home/tjd618/usb/.venv/bin/python"
R: dict = {}
proc = None


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, method=method, data=data,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def start_backend(tag):
    global proc
    env = dict(os.environ, CRYPTO_PAPER_RUN_CONFIG=RUN, CRYPTO_PAPER_ROOT=ROOT,
               BINANCE_LIVE_ROOT=f"{Q}/live")
    env.pop("BINANCE_LIVE_HOT_ORDERS", None)
    proc = subprocess.Popen([PY, "-m", "uvicorn", "app.crypto.terminal.server:app", "--host",
                             "127.0.0.1", "--port", "8199"], cwd=BACKEND, env=env,
                            stdout=open(f"{Q}/e2e_backend_{tag}.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            if api("/health")[1].get("configured") is not None:
                break
        except Exception:
            pass
        time.sleep(1)
    return api("/health")[1]


def stop_backend():
    proc.terminate()
    proc.wait(timeout=20)


def state(symbol):
    return api(f"/api/crypto/state?symbol={urllib.parse.quote(symbol)}")


def account(symbol):
    s, b = state(symbol)
    a = (b or {}).get("account") or {}
    return s, {k: a.get(k) for k in ("position_side", "position_qty", "avg_entry", "realized_pnl",
                                     "cumulative_fees", "wallet_balance", "available_balance")}


def warm(symbol):
    for _ in range(30):
        s, b = state(symbol)
        if s == 200 and (b.get("quote") or {}).get("best_ask"):
            return b
        time.sleep(1)
    raise RuntimeError(f"no quote for {symbol}")


def order(symbol, side, qty, intent="OPEN"):
    warm(symbol)
    return api("/api/crypto/order", "POST", {"side": side, "intent": intent, "qty": qty,
                                             "symbol": symbol})


try:
    R["boot1"] = start_backend("1")
    warm("BTCUSDT")
    R["btc_open"] = order("BTCUSDT", "LONG", "0.002")[0]
    R["scan1"] = api("/api/crypto/hot/scan", "POST")[0]
    hot = api("/api/crypto/hot")[1]["hot"]["symbol"]
    R["hot"] = hot
    b = warm(hot)
    price, step = Decimal(b["quote"]["best_ask"]), Decimal(b["instrument"]["qty_step"])
    qty = str((Decimal(20) / price / step).to_integral_value(rounding="ROUND_CEILING") * step)
    R["hot_open"] = order(hot, "LONG", qty)[0]
    before = {"BTCUSDT": account("BTCUSDT")[1], hot: account(hot)[1]}
    R["before_restart"] = before
    R["lock_before"] = api("/api/crypto/hot")[1]["lock"]["code"]
    stop_backend()

    R["boot2"] = start_backend("2")
    after = {"BTCUSDT": account("BTCUSDT"), hot: account(hot)}
    R["after_restart"] = {k: v[1] for k, v in after.items()}
    R["after_status"] = {k: v[0] for k, v in after.items()}
    position_fields = ("position_side", "position_qty", "avg_entry", "realized_pnl", "cumulative_fees")
    R["positions_identical"] = all(
        {f: before[k][f] for f in position_fields} == {f: after[k][1][f] for f in position_fields}
        for k in before)
    R["wallet_identical"] = (before["BTCUSDT"]["wallet_balance"] == after["BTCUSDT"][1]["wallet_balance"]
                             and before[hot]["wallet_balance"] == after[hot][1]["wallet_balance"])
    R["lock_after_restart"] = api("/api/crypto/hot")[1]["lock"]["code"]
    R["scan_after_restart"] = api("/api/crypto/hot/scan", "POST")[0]

    chrome = glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-1223/chrome-linux*/chrome"))[0]
    os.environ["LD_LIBRARY_PATH"] = os.path.expanduser("~/.local/emulib")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(URL)
        page.wait_for_selector('[data-testid="market-header"]', timeout=40000)
        page.locator('[data-testid="symbol-tab-hot"]').locator("visible=true").first.click()
        page.wait_for_function("() => { const b = document.querySelector('[data-testid=hot-scan-button]'); return b && b.disabled; }", timeout=15000)
        page.wait_for_timeout(2500)
        btn = page.locator('[data-testid="hot-scan-button"]').locator("visible=true").first
        R["screen_after_restart"] = {"scan_disabled": btn.is_disabled(), "title": btn.get_attribute("title"),
                                     "position_side": page.locator('[data-testid="position-side"]').locator("visible=true").first.inner_text()}
        page.screenshot(path=f"{Q}/e2e_after_restart_hot.png")
        browser.close()

    R["hot_close"] = order(hot, "LONG", qty, intent="CLOSE")[0]
    R["lock_after_close"] = api("/api/crypto/hot")[1]["lock"]["code"]
    final = {"BTCUSDT": account("BTCUSDT")[1], hot: account(hot)[1]}
    stop_backend()
    R["boot3"] = start_backend("3")
    again = {"BTCUSDT": account("BTCUSDT")[1], hot: account(hot)[1]}
    R["third_boot_identical"] = again == final
    R["lock_third_boot"] = api("/api/crypto/hot")[1]["lock"]["code"]
    R["sources"] = open(f"{Q}/e2e_backend_3.log").read().count("WALLET_REPLAY")
finally:
    if proc and proc.poll() is None:
        stop_backend()
print(json.dumps(R, indent=1, ensure_ascii=False))
