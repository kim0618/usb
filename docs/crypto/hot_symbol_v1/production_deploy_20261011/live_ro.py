"""Post-deploy LIVE read-only smoke on the deployed runtime code. One run, no port.

All Binance traffic goes through ReadOnlyTransport (non-GET refused before the network, paced).
No LiveRuntime, no mirror, no stream, no listenKey. Fixture balances are applied only to the
pure sizing function's `available` argument; nothing is sent with them.
"""
import json
import os
import time
from collections import Counter
from decimal import Decimal

import httpx

from app.crypto import symbols as sym_mod
from app.crypto.hot_coins import market as hot_market
from app.crypto.live.account import AccountReader
from app.crypto.live.adapter import BinanceLiveAdapter
from app.crypto.live.credentials import load_config, load_credentials
from app.crypto.live.leverage import LeverageCapability
from app.crypto.live.orders import LiveOrderRouter
from app.crypto.live.rest import BinanceFuturesClient
from app.crypto.live.sizing import presets

LOG, BLOCKED = [], []


class ReadOnlyTransport(httpx.HTTPTransport):
    def handle_request(self, request):
        entry = {"m": request.method, "p": request.url.path, "signed": "signature=" in str(request.url.query)}
        if request.method != "GET":
            BLOCKED.append(entry)
            raise RuntimeError(f"READ-ONLY GUARD: refused {request.method} {request.url.path}")
        time.sleep(0.25)
        response = super().handle_request(request)
        entry["status"] = response.status_code
        LOG.append(entry)
        return response


def jd(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dict):
        return {k: jd(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jd(x) for x in v]
    return v


R = {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
probe = httpx.Client(transport=ReadOnlyTransport())
try:
    probe.post("https://fapi.binance.com/fapi/v1/order")
    R["guard_control"] = "NOT BLOCKED"
except RuntimeError as exc:
    R["guard_control"] = f"BLOCKED: {exc}"
probe.close()
BLOCKED.clear()
R["hot_live_orders_env"] = os.environ.get("BINANCE_LIVE_HOT_ORDERS", "<unset>")
assert R["hot_live_orders_env"] == "<unset>"

config = load_config()
client = BinanceFuturesClient(credentials=load_credentials(), base_url=config.base_url,
                              recv_window_ms=config.recv_window_ms, trading_enabled=False,
                              transport=ReadOnlyTransport())
client.sync_clock()


def btc():
    row = next(r for r in client.call("position_risk", {"symbol": "BTCUSDT"}) if r.get("symbol") == "BTCUSDT")
    return {k: row.get(k) for k in ("positionAmt", "entryPrice", "updateTime")}


R["btc_before"] = btc()
per = {}
for s in ("BTCUSDT", "CFXUSDT", "BATUSDT", "GTCUSDT", "STRKUSDT"):
    out = {}
    try:
        if s != "BTCUSDT":
            sym_mod.admit_dynamic(s)
        scoped = config.for_symbol(s)
        reader = AccountReader(client, scoped)
        router = LiveOrderRouter(reader=reader, client=client, config=scoped, mirror=None,
                                 capability=LeverageCapability())
        adapter = BinanceLiveAdapter(config=scoped, client=client, reader=reader, router=router, mirror=None)
        snap = adapter.snapshot(force=True)
        view = jd(snap.view())
        f = view.get("filters") or {}
        out["ready"] = view.get("ready")
        out["filters"] = {k: f.get(k) for k in ("price_tick", "price_precision", "quantity_precision", "qty_step",
                                                 "min_qty", "market_qty_step", "market_max_qty", "min_notional")}
        out["commission"] = {k: (view.get("commission") or {}).get(k) for k in ("maker", "taker")}
        out["configured_leverage"] = (view.get("symbol_config") or {}).get("leverage")
        br = snap.brackets
        m = getattr(br, "max_leverage", None) if br is not None else None
        out["bracket_max"] = jd(m() if callable(m) else m)
        out["brackets_error"] = view.get("brackets_error")
        p = view.get("position") or {}
        out["position"] = {k: p.get(k) for k in ("signed_qty", "entry_price", "is_flat")}
        out["open_orders"] = len(client.call("open_orders", {"symbol": s}))
        out["real_available"] = (view.get("balance") or {}).get("available_balance")
        real = adapter.get_sizing(force=False)
        out["real_safe_max"] = {side: {k: (real.get("sides", {}).get(side) or {}).get(k)
                                       for k in ("max_qty", "reject_code")} for side in ("LONG", "SHORT")}
        depth = client.call("depth", {"symbol": s, "limit": 20})
        lev = snap.symbol_config.leverage if snap.symbol_config else None
        fixtures = {}
        for bal in ("100", "1000"):
            row = presets(side="LONG", depth=depth, mark=snap.mark, commission=snap.commission,
                          filters=snap.filters, leverage=lev, brackets=snap.brackets,
                          available=Decimal(bal), ceiling=None, position=None)
            fixtures[bal] = jd({k: row.get(k) for k in ("max_qty", "leverage", "leverage_capped_by_bracket",
                                                         "configured_leverage", "reject_code")})
            mx = next((x for x in row.get("presets", []) if x.get("label") == "MAX"), None)
            if mx:
                fixtures[bal]["notional"] = jd(mx.get("notional"))
                fixtures[bal]["margin"] = jd(mx.get("required_margin"))
        out["fixture_safe_max_long"] = fixtures
        if s in ("GTCUSDT", "STRKUSDT"):
            hist = hot_market.BinanceChartHistory()
            out["chart_bars"] = {tf: len(hist.get(tf, 50, symbol=s)["bars"]) for tf in ("1m", "1h")}
            out["quote"] = {"bid": depth["bids"][0][0], "ask": depth["asks"][0][0], "mark": jd(snap.mark.mark_price)}
            try:
                body = hot_market._http_get(hot_market.BYBIT_REST, "/v5/market/risk-limit",
                                            {"category": "linear", "symbol": s})
                out["bybit_lists"] = bool(body.get("retCode") == 0 and body.get("result", {}).get("list"))
            except Exception as exc:
                out["bybit_lists"] = f"error {type(exc).__name__}"
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    per[s] = out
R["per_symbol"] = per
R["btc_after"] = btc()
R["btc_identity_unchanged"] = R["btc_before"] == R["btc_after"]
counts = Counter((e["m"], e["p"]) for e in LOG)
R["requests"] = {"total": len(LOG), "signed": sum(e["signed"] for e in LOG),
                 "non_get_sent": sum(e["m"] != "GET" for e in LOG), "blocked": BLOCKED,
                 "by_endpoint": {f"{m} {p}": n for (m, p), n in sorted(counts.items())},
                 "http_errors": [e for e in LOG if e.get("status", 200) >= 400]}
print(json.dumps(R, ensure_ascii=False, default=str))
