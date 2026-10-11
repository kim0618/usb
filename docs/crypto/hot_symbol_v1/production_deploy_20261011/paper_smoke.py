"""Production PAPER smoke for Dynamic HOT V1. Runs on the server against 127.0.0.1:8100.

Only two write paths exist in this script, both PAPER: POST /api/crypto/hot/scan and
POST /api/crypto/order. Anything else that is not a GET is refused before it is sent.
Phases:
  open   A..C: scan from BTC, HOT quote/chart/preview, PAPER LONG -> lock -> CLOSE -> SHORT (kept)
  check  state of BTC + HOT (used before and after the restart)
  close  E: CLOSE the HOT SHORT -> flat -> scan allowed
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

B = "http://127.0.0.1:8100"
ALLOWED_WRITES = {"/api/crypto/hot/scan", "/api/crypto/order"}
SENT: list[tuple[str, str, int]] = []


def call(method, path, body=None):
    bare = path.split("?")[0]
    if method != "GET" and bare not in ALLOWED_WRITES:
        raise SystemExit(f"REFUSED {method} {bare}")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data=data, method=method,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            SENT.append((method, bare, r.status))
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        SENT.append((method, bare, e.code))
        raw = e.read()
        return e.code, (json.loads(raw) if raw else {})


def q(symbol):
    return urllib.parse.quote(symbol)


def acct(symbol):
    s, b = call("GET", f"/api/crypto/state?symbol={q(symbol)}")
    a = (b or {}).get("account") or {}
    keep = ("position_side", "position_qty", "avg_entry", "realized_pnl", "cumulative_fees",
            "cumulative_funding_paid", "wallet_balance", "available_balance", "used_margin")
    return s, {k: a.get(k) for k in keep}, b


def warm(symbol):
    for _ in range(40):
        s, a, b = acct(symbol)
        if s == 200 and ((b.get("quote") or {}).get("best_ask")):
            return b
        time.sleep(1)
    raise SystemExit(f"no quote for {symbol}")


def live_btc():
    s, b = call("GET", "/api/crypto/binance/positions")
    return [{k: p.get(k) for k in ("signed_qty", "entry_price", "update_time_ms")}
            for p in b.get("positions", []) if p.get("symbol") == "BTCUSDT"]


phase = sys.argv[1]
R: dict = {"phase": phase, "live_btc_before": live_btc()}
if phase == "open":
    s, hot0 = call("GET", "/api/crypto/hot")
    R["A_before"] = {"hot": hot0.get("hot"), "lock": hot0["lock"]["code"]}
    R["A_btc_paper"] = acct("BTCUSDT")[1]
    s, scan = call("POST", "/api/crypto/hot/scan")
    R["A_scan"] = {"status": s, "code": scan.get("code") or (scan.get("error") or {}).get("code")}
    s, hot = call("GET", "/api/crypto/hot")
    sym = hot["hot"]["symbol"]
    R["hot"] = sym
    R["A_hot_metrics"] = {k: hot["hot"]["metrics"].get(k) for k in
                          ("one_hour_range_pct", "one_hour_change_pct", "last_price",
                           "estimated_round_trip_slippage_bps_1000", "spread_bps")}
    R["A_top"] = [t.get("symbol") for t in hot["hot"].get("top", [])]
    R["A_paper"] = hot.get("paper")
    b = warm(sym)
    R["B_quote"] = {k: b["quote"].get(k) for k in ("best_bid", "best_ask", "mark_price")}
    R["B_feed"] = {k: (b.get("feed") or {}).get(k) for k in ("source", "symbol", "connected")}
    R["B_instrument"] = b.get("instrument")
    s, ch = call("GET", f"/api/crypto/chart-history?symbol={q(sym)}&timeframe=1m&limit=5")
    R["B_chart"] = {"status": s, "bars": len(ch.get("bars") or ch.get("candles") or ch.get("klines") or [])}
    price = Decimal(b["quote"]["best_ask"])
    step = Decimal(b["instrument"]["qty_step"])
    floor = max(Decimal(str(b["instrument"].get("min_notional_value") or 5)), Decimal(12))
    qty = (floor / price / step).to_integral_value(rounding="ROUND_CEILING") * step
    R["qty"] = str(qty)
    s, pv = call("GET", f"/api/crypto/order-preview?long_qty={qty}&short_qty={qty}&symbol={q(sym)}")
    R["B_preview"] = {"status": s, "sides": {k: {f: v.get(f) for f in ("feasible", "notional", "required_margin", "entry_fee", "breakeven_mark_price", "reject_code")}
                                              for k, v in (pv.get("sides") or {}).items()}}
    s, o = call("POST", "/api/crypto/order", {"side": "LONG", "intent": "OPEN", "qty": str(qty), "symbol": sym})
    R["C_long"] = {"status": s, "err": (o.get("error") or {}).get("code")}
    R["C_long_pos"] = acct(sym)[1]
    R["D_lock_long"] = call("GET", "/api/crypto/hot")[1]["lock"]["code"]
    R["D_scan_while_long"] = call("POST", "/api/crypto/hot/scan")[0]
    s, o = call("POST", "/api/crypto/order", {"side": "LONG", "intent": "CLOSE", "qty": str(qty), "symbol": sym})
    R["C_long_close"] = {"status": s, "err": (o.get("error") or {}).get("code")}
    R["C_after_long_close"] = acct(sym)[1]
    warm(sym)
    s, o = call("POST", "/api/crypto/order", {"side": "SHORT", "intent": "OPEN", "qty": str(qty), "symbol": sym})
    R["C_short"] = {"status": s, "err": (o.get("error") or {}).get("code")}
    R["C_short_pos"] = acct(sym)[1]
    R["D_lock_short"] = call("GET", "/api/crypto/hot")[1]["lock"]["code"]
    R["btc_paper_after"] = acct("BTCUSDT")[1]
elif phase == "check":
    sym = sys.argv[2]
    R["btc"] = acct("BTCUSDT")[1]
    R["hot_acct"] = acct(sym)[1]
    s, hot = call("GET", "/api/crypto/hot")
    R["hot"] = (hot.get("hot") or {}).get("symbol")
    R["lock"] = hot["lock"]["code"]
    R["held"] = hot.get("held")
    s, st = call("GET", f"/api/crypto/state?symbol={q(sym)}")
    R["hot_recovery_source"] = ((st.get("recovery") or {}).get("source"))
    s, st = call("GET", "/api/crypto/state?symbol=BTCUSDT")
    R["btc_recovery_source"] = ((st.get("recovery") or {}).get("source"))
    R["btc_recovery_ckpt"] = ((st.get("recovery") or {}).get("checkpoint_segment"))
    R["c1"] = {k: call("GET", "/api/crypto/paper/c1-auto")[1].get(k) for k in ("enabled", "enabled_at")}
elif phase == "close":
    sym = sys.argv[2]
    _, a, _ = acct(sym)
    warm(sym)
    s, o = call("POST", "/api/crypto/order", {"side": a["position_side"] or "SHORT", "intent": "CLOSE",
                                              "qty": a["position_qty"], "symbol": sym})
    R["E_close"] = {"status": s, "err": (o.get("error") or {}).get("code")}
    R["E_after"] = acct(sym)[1]
    R["E_lock"] = call("GET", "/api/crypto/hot")[1]["lock"]["code"]
    R["btc"] = acct("BTCUSDT")[1]
R["live_btc_after"] = live_btc()
R["non_get_sent"] = sorted({(m, p, s) for m, p, s in SENT if m != "GET"})
print(json.dumps(R, ensure_ascii=False, indent=1))
