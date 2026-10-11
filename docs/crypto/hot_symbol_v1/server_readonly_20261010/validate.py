"""Dynamic HOT V1 - production LIVE read-only validation. Runs once, no port, no server.

Every Binance request goes through ReadOnlyTransport: anything but GET is refused before it
leaves the process and recorded. No LiveRuntime (no audit mirror append, no user-data stream,
no listenKey), no file under the production runtime is opened for writing; scratch files live
in WORK only.
"""
import asyncio, json, os, sys, time, traceback
from collections import Counter
from decimal import Decimal
from pathlib import Path

import httpx

WORK = Path(sys.argv[1])
sys.path.insert(0, str(WORK / "backend"))

from app.crypto import symbols as sym_mod                                  # noqa: E402
from app.crypto.hot_coins import lock as hot_lock                          # noqa: E402
from app.crypto.hot_coins import market as hot_market                      # noqa: E402
from app.crypto.hot_coins.lock import SymbolExposure, scan_permission      # noqa: E402
from app.crypto.hot_coins.scanner import HotScanner, HttpTransport         # noqa: E402
from app.crypto.live.account import AccountReader                          # noqa: E402
from app.crypto.live.adapter import BinanceLiveAdapter                     # noqa: E402
from app.crypto.live.credentials import load_config, load_credentials      # noqa: E402
from app.crypto.live.leverage import LeverageCapability                    # noqa: E402
from app.crypto.live.orders import LiveOrderRouter                         # noqa: E402
from app.crypto.live.rest import BinanceFuturesClient                      # noqa: E402

LOG: list[dict] = []
BLOCKED: list[dict] = []


class ReadOnlyTransport(httpx.HTTPTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        entry = {"m": request.method, "p": request.url.path, "signed": "signature=" in str(request.url.query)}
        if request.method != "GET":
            BLOCKED.append(entry)
            raise RuntimeError(f"READ-ONLY GUARD: refused {request.method} {request.url.path}")
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
    if hasattr(v, "__dict__") and not isinstance(v, type):
        return jd({k: x for k, x in vars(v).items() if not k.startswith("_")})
    return v


R: dict = {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

# Control: the guard must refuse a write before it reaches the network.
_probe = httpx.Client(transport=ReadOnlyTransport())
try:
    _probe.post("https://fapi.binance.com/fapi/v1/order")
    R["guard_control"] = "NOT BLOCKED"
except RuntimeError as exc:
    R["guard_control"] = f"BLOCKED: {exc}"
finally:
    _probe.close()
R["guard_control_entries"] = list(BLOCKED)
BLOCKED.clear()
assert os.environ.get("BINANCE_LIVE_HOT_ORDERS", "off").lower() in ("", "off", "0", "false"), "HOT orders must be OFF"
R["hot_live_orders_env"] = os.environ.get("BINANCE_LIVE_HOT_ORDERS", "<unset>")

config = load_config()
client = BinanceFuturesClient(credentials=load_credentials(), base_url=config.base_url,
                              recv_window_ms=config.recv_window_ms, trading_enabled=False,
                              transport=ReadOnlyTransport())
client.sync_clock()
R["client"] = {"trading_enabled": client.trading_enabled, "config_symbols": list(config.symbols),
               "config_trading_enabled": config.trading_enabled}

# ---------------------------------------------------------------- candidates (public scan)
scanner = HotScanner(HttpTransport())
scan = scanner.scan()
eligible = [c.symbol for c in scan.top] + [k for k, v in scan.rejections.items() if v == "NOT_EVALUATED"]
R["scan"] = {"top": [c.symbol for c in scan.top], "api": scan.api.as_dict(), "eligible": len(eligible)}


def bybit_lists(symbol):
    try:
        body = hot_market._http_get(hot_market.BYBIT_REST, "/v5/market/risk-limit", {"category": "linear", "symbol": symbol})
        return bool(body.get("retCode") == 0 and body.get("result", {}).get("list"))
    except Exception:
        return False


hot1 = scan.top[0].symbol
common = next(s for s in [c.symbol for c in scan.top] + eligible if s != hot1 and bybit_lists(s))
binance_only = next((s for s in eligible if not bybit_lists(s)), None)
binance_only_source = "HOT-eligible"
if binance_only is None:
    for s in ("龙虾USDT", "GTCUSDT", "FETUSDT", "PUMPUSDT"):
        if scanner.symbol_row(s) and not bybit_lists(s):
            binance_only, binance_only_source = s, "known Binance-only (not HOT-eligible now)"
            break
CANDS = [("HOT#1", hot1), ("COMMON", common), ("BINANCE_ONLY", binance_only)]
R["candidates"] = {label: s for label, s in CANDS}
R["binance_only_source"] = binance_only_source

# ---------------------------------------------------------------- BTC before
def safe(fn):
    try:
        return fn()
    except Exception as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}


def btc_position():
    rows = client.call("position_risk", {"symbol": "BTCUSDT"})
    row = next(r for r in rows if r.get("symbol") == "BTCUSDT")
    return {k: row.get(k) for k in ("positionAmt", "entryPrice", "updateTime", "isolatedMargin")}

R["btc_before"] = safe(btc_position)
R["btc_open_orders_before"] = safe(lambda: len(client.call("open_orders", {"symbol": "BTCUSDT"})))


class LiveExposureRO:
    """The same two reads as live_routes.LiveExposure, minus the in-flight registry (no order
    path exists in this process, so in-flight is 0 by construction)."""
    def exposure(self, symbol):
        rows = client.call("position_risk", {"symbol": symbol})
        orders = client.call("open_orders", {"symbol": symbol})
        amount = sum((abs(Decimal(str(r.get("positionAmt") or "0"))) for r in rows if r.get("symbol") == symbol), Decimal(0))
        return SymbolExposure(symbol, amount, len(orders), 0)


live_exposure = LiveExposureRO()

# ---------------------------------------------------------------- per symbol
WORKREF = WORK / "paper_ref"
per = {}
btc_adapter = None
for label, s in [("BTC", "BTCUSDT")] + CANDS:
    out = {"symbol": s}
    try:
        if s != "BTCUSDT":
            sym_mod.admit_dynamic(s)
        scoped = config.for_symbol(s)
        reader = AccountReader(client, scoped)
        router = LiveOrderRouter(reader=reader, client=client, config=scoped, mirror=None, capability=LeverageCapability())
        adapter = BinanceLiveAdapter(config=scoped, client=client, reader=reader, router=router, mirror=None)
        if s == "BTCUSDT":
            btc_adapter = adapter
        snap = adapter.snapshot(force=True)
        view = jd(snap.view())
        out["ready"] = view.get("ready")
        out["blockers"] = view.get("blockers")
        f = view.get("filters") or {}
        out["filters"] = {k: f.get(k) for k in ("symbol", "status", "contract_type", "price_tick", "price_precision",
                                                 "quantity_precision", "qty_step", "min_qty", "max_qty", "market_qty_step",
                                                 "market_min_qty", "market_max_qty", "min_notional", "max_notional")}
        out["commission"] = view.get("commission")
        sc = view.get("symbol_config") or {}
        out["symbol_config"] = {k: sc.get(k) for k in ("symbol", "leverage", "margin_type", "max_notional_value")} if sc else None
        br = client.call("leverage_bracket", {"symbol": s})
        rows = br if isinstance(br, list) else [br]
        brackets = next((r.get("brackets") for r in rows if r.get("symbol") == s), [])
        out["brackets"] = {"count": len(brackets), "max_initial_leverage": max((b.get("initialLeverage", 0) for b in brackets), default=None),
                           "first": {k: brackets[0].get(k) for k in ("initialLeverage", "notionalCap", "maintMarginRatio", "cum")} if brackets else None}
        p = view.get("position") or {}
        out["position"] = {k: p.get(k) for k in ("symbol", "side", "qty", "signed_qty", "entry_price", "is_flat")}
        out["balance"] = {k: (view.get("balance") or {}).get(k) for k in ("asset", "available_balance")}
        out["open_orders"] = len(client.call("open_orders", {"symbol": s}))
        sizing = adapter.get_sizing()
        out["sizing"] = jd({side: {k: v for k, v in (sizing.get("sides", {}).get(side) or {}).items()
                                    if k in ("max_qty", "max_feasible", "reject_code", "available_balance", "leverage",
                                             "vwap", "expected_entry_vwap", "fee", "taker_rate", "instrument", "notional", "slippage_bps")}
                                for side in ("LONG", "SHORT")})
        out["sizing_available"] = sizing.get("available")
        if s != "BTCUSDT":
            exp = live_exposure.exposure(s)
            out["exposure"] = jd(vars(exp))
            out["lock_live_only"] = scan_permission(s, live_exposure).as_dict()
            # Paper margin table, the way the HOT service builds it on this server (bracket fallback)
            row = scanner.symbol_row(s)
            def provider(symbol):
                return brackets if symbol == s else None
            try:
                paths = hot_market.fetch_reference(WORKREF, s, row, bracket_provider=provider)
                spec, tiers = hot_market.load_reference(paths)
                out["paper_reference"] = {"ok": True, "margin_source": json.loads(paths.risk_limit.read_text()).get("retMsg"),
                                          "tiers": len(tiers.tiers), "max_leverage": str(spec.max_leverage),
                                          "tick": str(spec.tick_size), "step": str(spec.qty_step), "min_notional": str(spec.min_notional_value),
                                          "funding_min": spec.funding_interval_minutes}
            except Exception as exc:
                out["paper_reference"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["trace"] = traceback.format_exc()[-800:]
    per[label] = out
R["per_symbol"] = per

# ---------------------------------------------------------------- public market data per candidate
async def market(s):
    feed = hot_market.BinancePublicFeed(symbol=s)
    feed.seed_klines()
    feed.start()
    got = None
    for _ in range(60):
        await asyncio.sleep(0.25)
        q = feed.quote()
        if q is not None and feed.ticker.get("lastPrice"):
            got = q
            break
    hist = hot_market.BinanceChartHistory()
    h = {tf: len(hist.get(tf, 200, symbol=s)["bars"]) for tf in ("1m", "10m", "1h", "4h", "1d")}
    view = feed.view()
    await feed.stop()
    return {"quote": None if got is None else {"bid": str(got.best_bid), "ask": str(got.best_ask), "mark": str(got.mark_price),
                                                "last": str(got.last_price), "funding": str(got.funding_rate)},
            "seeded_1m": len(feed.klines), "history": h, "connected_then_stopped": view["connects"] >= 2 and not feed.running}

async def all_markets():
    return {label: await market(s) for label, s in CANDS}
R["market"] = asyncio.run(all_markets())

# ---------------------------------------------------------------- lock semantics on real data + fixtures
class Fixed:
    def __init__(self, rows): self.rows = rows
    def exposure(self, symbol):
        p, o, f = self.rows.get(symbol, (Decimal(0), 0, 0))
        return SymbolExposure(symbol, p, o, f)

class Broken:
    def exposure(self, symbol): raise TimeoutError("simulated read failure")

btc_amt = Decimal(str(R["btc_before"].get("positionAmt", "0")))
R["lock"] = {
    "real_hot1": scan_permission(hot1, live_exposure).as_dict(),  # a failed read reads BLOCKED/UNKNOWN
    "btc_never_consulted": scan_permission("BTCUSDT", live_exposure).as_dict(),
    "fixture_btc_position_only": scan_permission(hot1, Fixed({"BTCUSDT": (abs(btc_amt), 1, 0)})).code,
    "fixture_hot_position": scan_permission(hot1, Fixed({hot1: (Decimal("12"), 0, 0)})).code,
    "fixture_hot_open_order": scan_permission(hot1, Fixed({hot1: (Decimal(0), 1, 0)})).code,
    "fixture_hot_in_flight": scan_permission(hot1, Fixed({hot1: (Decimal(0), 0, 1)})).code,
    "fixture_unknown": scan_permission(hot1, Broken()).code,
}

# ---------------------------------------------------------------- BTC after + request summary
R["btc_after"] = safe(btc_position)
R["btc_open_orders_after"] = safe(lambda: len(client.call("open_orders", {"symbol": "BTCUSDT"})))
R["btc_identity_unchanged"] = R["btc_before"] == R["btc_after"]
counts = Counter((e["m"], e["p"]) for e in LOG)
R["requests"] = {"total": len(LOG), "signed": sum(e["signed"] for e in LOG), "non_get_sent": sum(e["m"] != "GET" for e in LOG),
                 "blocked_non_get": BLOCKED, "by_endpoint": {f"{m} {p}": n for (m, p), n in sorted(counts.items())},
                 "http_errors": [e for e in LOG if e.get("status", 200) >= 400]}
R["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
print(json.dumps(R, ensure_ascii=False, default=str))
