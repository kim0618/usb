"""HOT Coin Finder V0: ranking contract, scan robustness, single flight, the route, the lock
contract and the structural read-only guarantees. No test touches the network."""
from __future__ import annotations

import ast
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import httpx
import pytest
from fastapi.testclient import TestClient

from app.crypto.hot_coins import lock as hot_lock
from app.crypto.hot_coins import scanner as hs
from app.crypto.hot_coins import service as hot_service
from app.crypto.hot_coins.lock import (
    ALLOWED, BLOCKED, InFlightRegistry, SymbolExposure, guarded_scan, scan_permission,
)
from app.crypto.hot_coins.scanner import HotScanner, HttpTransport, ScanFailed
from app.crypto.hot_coins.service import HotCoinService
from app.crypto.terminal import hot_routes
from app.crypto.terminal.server import app

NOW_MS = 1_791_700_000_000 - (1_791_700_000_000 % 60_000) + 30_000  # mid-minute
APP = Path(__file__).resolve().parents[2] / "app"


# --------------------------------------------------------------------------- fixture market

def market(**overrides: Any) -> dict[str, dict[str, Any]]:
    """Seven liquid movers with distinct 1H ranges, plus BTC moving the most of all."""
    base = {
        "BTCUSDT": dict(r1h=30.0),
        "AAAUSDT": dict(r1h=12.0),
        "BBBUSDT": dict(r1h=9.0),
        "CCCUSDT": dict(r1h=15.0),
        "DDDUSDT": dict(r1h=4.0),
        "EEEUSDT": dict(r1h=7.0),
        "FFFUSDT": dict(r1h=3.0),
        "GGGUSDT": dict(r1h=11.0),
    }
    for name, spec in overrides.items():
        if spec is None:
            base.pop(name, None)
        else:
            base[name] = {**base.get(name, {}), **spec}
    full = {}
    for name, spec in base.items():
        full[name] = {"r1h": 5.0, "r24h": 20.0, "qv24h": 100e6, "qv1h": 5e6, "spread_bps": 2.0,
                      "book_usdt": 1e6, "status": "TRADING", "contractType": "PERPETUAL",
                      "quoteAsset": "USDT", "deliveryDate": hs.PERPETUAL_NO_DELIVERY_MS,
                      "change": +1.0, "batch_spread_bps": None, "level_step": 0.001, **spec}
    return full


class FixtureTransport:
    def __init__(self, spec: dict[str, dict[str, Any]], *, fail: set[tuple[str, str]] | None = None,
                 fail_batch: bool = False, used_weight: int = 50) -> None:
        self.spec = spec
        self.fail = fail or set()
        self.fail_batch = fail_batch
        self.used_weight = used_weight
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._lock = threading.Lock()

    def get(self, path: str, params: Mapping[str, Any] | None = None):
        params = dict(params or {})
        with self._lock:
            self.calls.append((path, params))
        headers = {"x-mbx-used-weight-1m": str(self.used_weight)}
        symbol = params.get("symbol")
        if (path, symbol) in self.fail:
            raise httpx.ConnectError("fixture failure")
        if path == "/fapi/v1/exchangeInfo":
            if self.fail_batch:
                raise httpx.ConnectError("down")
            return {"symbols": [
                {"symbol": n, "status": s["status"], "contractType": s["contractType"],
                 "quoteAsset": s["quoteAsset"], "deliveryDate": s["deliveryDate"]}
                for n, s in self.spec.items()]}, headers
        if path == "/fapi/v1/ticker/24hr":
            out = []
            for n, s in self.spec.items():
                open_ = 100.0
                out.append({"symbol": n, "openPrice": str(open_), "highPrice": str(open_ * (1 + s["r24h"] / 100)),
                            "lowPrice": str(open_), "lastPrice": "101.5", "quoteVolume": str(s["qv24h"])})
            return out, headers
        if path == "/fapi/v1/ticker/bookTicker":
            out = []
            for n, s in self.spec.items():
                bps = s["batch_spread_bps"] if s["batch_spread_bps"] is not None else s["spread_bps"]
                half = 100.0 * bps / 2e4
                out.append({"symbol": n, "bidPrice": str(100 - half), "askPrice": str(100 + half)})
            return out, headers
        s = self.spec[symbol]
        if path == "/fapi/v1/klines":
            bars = []
            start = NOW_MS - (NOW_MS % 60_000) - 61 * 60_000
            for i in range(62):
                o = start + i * 60_000
                high = 100.0 * (1 + s["r1h"] / 100) if i == 30 else 100.0
                close = 100.0 * (1 + s["change"] / 100) if i >= 60 else 100.0
                bars.append([o, "100", str(high), "100", str(close), "1", o + 59_999,
                             str(s["qv1h"] / 60) if i >= 1 else "0"])
            # bar 61 is the open minute (closes after NOW_MS) and must be ignored
            bars[-1][2] = "1000"
            return bars, headers
        if path == "/fapi/v1/depth":
            half = 100.0 * s["spread_bps"] / 2e4
            levels = 50
            qty = s["book_usdt"] / levels / 100.0
            step = s["level_step"]
            bids = [[str(100 - half - i * step), str(qty)] for i in range(levels)]
            asks = [[str(100 + half + i * step), str(qty)] for i in range(levels)]
            return {"bids": bids, "asks": asks}, headers
        raise AssertionError(f"unexpected path {path}")


def scan(spec=None, **kwargs) -> hs.ScanResult:
    transport = FixtureTransport(spec or market(), **kwargs)
    return HotScanner(transport, clock_ms=lambda: NOW_MS).scan()


# --------------------------------------------------------------------------- ranking contract

def test_scan_returns_top5_ranked_by_one_hour_range_only() -> None:
    result = scan()
    assert [c.symbol for c in result.top] == ["CCCUSDT", "AAAUSDT", "GGGUSDT", "BBBUSDT", "EEEUSDT"]
    assert [c.rank for c in result.top] == [1, 2, 3, 4, 5]
    assert result.top[0].one_hour_range_pct == pytest.approx(15.0)
    # the open minute's 1000 high was ignored
    assert all(c.one_hour_range_pct < 20 for c in result.top)


def test_24h_movement_does_not_enter_the_rank() -> None:
    spec = market(DDDUSDT=dict(r24h=300.0), CCCUSDT=dict(r24h=1.0))
    assert [c.symbol for c in scan(spec).top][0] == "CCCUSDT"
    assert "DDDUSDT" not in [c.symbol for c in scan(spec).top]


def test_candidate_fields() -> None:
    top = scan(market(CCCUSDT=dict(change=-2.0))).top[0]
    body = top.as_dict()
    for key in ("symbol", "last_price", "one_hour_range_pct", "twenty_four_hour_range_pct",
                "one_hour_quote_volume", "twenty_four_hour_quote_volume", "spread_bps",
                "estimated_round_trip_slippage_bps_1000", "direction", "scanned_at_ms"):
        assert key in body
    assert body["direction"] == "DOWN"
    assert body["scanned_at_ms"] == NOW_MS
    assert body["one_hour_quote_volume"] == pytest.approx(5e6)
    assert body["twenty_four_hour_range_pct"] == pytest.approx(20.0)


def test_btc_is_excluded_even_when_it_moves_most() -> None:
    result = scan()
    assert "BTCUSDT" not in [c.symbol for c in result.top]
    assert result.rejections["BTCUSDT"] == hs.EXCLUDED_MAIN_SYMBOL


@pytest.mark.parametrize("override, reason", [
    (dict(status="SETTLING"), hs.NOT_TRADING),
    (dict(contractType="TRADIFI_PERPETUAL"), hs.NOT_PERPETUAL),
    (dict(contractType="CURRENT_QUARTER"), hs.NOT_PERPETUAL),
    (dict(quoteAsset="USDC"), hs.NOT_USDT_QUOTED),
    (dict(deliveryDate=1_800_000_000_000), hs.DELIVERY_SCHEDULED),
    (dict(qv24h=19_999_999.0), hs.LOW_24H_VOLUME),
    (dict(qv1h=499_000.0), hs.LOW_1H_VOLUME),
    (dict(spread_bps=10.5), hs.WIDE_SPREAD),
    (dict(batch_spread_bps=12.0), hs.WIDE_SPREAD),
    (dict(book_usdt=900.0), hs.HIGH_SLIPPAGE),
    (dict(book_usdt=5_000.0, level_step=0.04), hs.HIGH_SLIPPAGE),
])
def test_each_gate_removes_the_leader(override, reason) -> None:
    result = scan(market(CCCUSDT=override))
    assert "CCCUSDT" not in [c.symbol for c in result.top]
    assert result.rejections["CCCUSDT"] == reason
    assert [c.symbol for c in result.top][0] == "AAAUSDT"


def test_gate_boundaries_are_inclusive() -> None:
    result = scan(market(CCCUSDT=dict(qv24h=20e6, qv1h=0.5e6, spread_bps=9.99)))
    assert result.top[0].symbol == "CCCUSDT"


def test_slippage_is_round_trip_at_1000_usdt() -> None:
    book = {"bids": [["99.99", "5"], ["99.90", "100"]], "asks": [["100.01", "5"], ["100.10", "100"]]}
    spread, rt = hs.book_quality(book)
    assert spread == pytest.approx(2.0, abs=1e-6)
    # 500 USDT at the touch, 500 one level deeper, each side
    assert rt > spread and rt < 20
    assert hs.walk_book([(100.0, 1.0)], 1_000, 100.0) is None  # book too thin: exhausted


def test_unicode_symbol_is_scanned_and_encoded() -> None:
    spec = market(**{"龙虾USDT": dict(r1h=20.0)})
    result = scan(spec)
    assert result.top[0].symbol == "龙虾USDT"

    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json=[], headers={"x-mbx-used-weight-1m": "3"})

    transport = HttpTransport(transport=httpx.MockTransport(handler))
    transport.get("/fapi/v1/klines", {"symbol": "龙虾USDT", "interval": "1m", "limit": 62})
    raw = seen[0].raw_path.decode("ascii")
    assert "symbol=%E9%BE%99%E8%99%BEUSDT" in raw
    assert seen[0].params["symbol"] == "龙虾USDT"


# --------------------------------------------------------------------------- robustness / cost

def test_one_symbol_failing_does_not_fail_the_scan() -> None:
    result = scan(fail={("/fapi/v1/klines", "CCCUSDT"), ("/fapi/v1/depth", "AAAUSDT")})
    assert result.rejections["CCCUSDT"] == hs.DATA_UNAVAILABLE
    assert result.rejections["AAAUSDT"] == hs.DATA_UNAVAILABLE
    assert [c.symbol for c in result.top] == ["GGGUSDT", "BBBUSDT", "EEEUSDT", "DDDUSDT", "FFFUSDT"]
    assert result.api.failed == 2


def test_whole_market_outage_fails_the_scan() -> None:
    with pytest.raises(ScanFailed) as caught:
        scan(fail_batch=True)
    assert caught.value.code == "HOT_SCAN_UPSTREAM"


def test_rate_budget_guard_aborts() -> None:
    with pytest.raises(ScanFailed) as caught:
        scan(used_weight=hs.USED_WEIGHT_ABORT)
    assert caught.value.code == "HOT_SCAN_RATE_BUDGET"


def test_nothing_eligible_is_a_failure_not_an_empty_result() -> None:
    spec = {n: dict(qv24h=1.0) for n in market() if n != "BTCUSDT"}
    with pytest.raises(ScanFailed) as caught:
        scan(market(**spec))
    assert caught.value.code == "HOT_SCAN_EMPTY"


def test_cost_is_batch_plus_one_kline_per_survivor_plus_capped_books() -> None:
    names = {f"S{i:03d}USDT": dict(r1h=1.0 + i / 10) for i in range(300)}
    thin = {f"T{i:03d}USDT": dict(qv24h=1e6) for i in range(200)}
    transport = FixtureTransport(market(**names, **thin))
    result = HotScanner(transport, clock_ms=lambda: NOW_MS).scan()
    by = result.api.by_endpoint
    assert by["/fapi/v1/ticker/24hr"] == 1 and by["/fapi/v1/ticker/bookTicker"] == 1
    # klines only for the 307 that passed the batch prefilter, never for the 200 thin ones
    assert by["/fapi/v1/klines"] == 307
    assert by["/fapi/v1/depth"] <= hs.DEPTH_MAX_CALLS
    assert not any(p == "/fapi/v1/klines" and q["symbol"].startswith("T") for p, q in transport.calls)
    assert result.api.weight == 1 + 40 + 5 + 307 + 2 * by["/fapi/v1/depth"]


def test_exchange_info_is_cached_between_scans() -> None:
    transport = FixtureTransport(market())
    scanner = HotScanner(transport, clock_ms=lambda: NOW_MS)
    scanner.scan()
    second = scanner.scan()
    assert "/fapi/v1/exchangeInfo" not in second.api.by_endpoint


def test_scanner_uses_only_public_market_get_endpoints() -> None:
    transport = FixtureTransport(market())
    HotScanner(transport, clock_ms=lambda: NOW_MS).scan()
    assert {p for p, _ in transport.calls} <= set(hs.ENDPOINT_WEIGHT)


# --------------------------------------------------------------------------- service

class CountingScanner:
    def __init__(self, gate: threading.Event | None = None, fail: bool = False) -> None:
        self.calls = 0
        self.gate = gate
        self.fail = fail

    def scan(self) -> hs.ScanResult:
        self.calls += 1
        if self.gate:
            self.gate.wait(5)
        if self.fail:
            raise ScanFailed("HOT_SCAN_UPSTREAM", "down")
        return scan()


def test_concurrent_requests_cost_one_scan() -> None:
    gate = threading.Event()
    counting = CountingScanner(gate)
    svc = HotCoinService(lambda: counting)
    answers: list[Any] = []
    threads = [threading.Thread(target=lambda: answers.append(svc.scan())) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join(5)
    assert counting.calls == 1
    assert len(answers) == 4
    assert sum(a.coalesced for a in answers) == 3
    assert len({id(a.result) for a in answers}) == 1


def test_repeat_within_floor_returns_cached_and_failure_keeps_last() -> None:
    clock = [0.0]
    counting = CountingScanner()
    svc = HotCoinService(lambda: counting, min_interval_s=10, clock=lambda: clock[0])
    first = svc.scan()
    clock[0] = 5
    again = svc.scan()
    assert again.cached and again.result is first.result and counting.calls == 1
    clock[0] = 20
    counting.fail = True
    with pytest.raises(ScanFailed):
        svc.scan()
    clock[0] = 21
    counting.fail = False
    fresh = svc.scan()
    assert not fresh.cached and counting.calls == 3


# --------------------------------------------------------------------------- route

@pytest.fixture
def route_service(monkeypatch):
    def install(scanner):
        svc = HotCoinService(lambda: scanner, min_interval_s=0)
        monkeypatch.setattr(hot_routes, "service", svc)
        return svc
    return install


def test_route_returns_top5_and_contract(route_service) -> None:
    route_service(HotScanner(FixtureTransport(market()), clock_ms=lambda: NOW_MS))
    response = TestClient(app).get("/api/crypto/hot-coins")
    assert response.status_code == 200
    body = response.json()
    assert [row["symbol"] for row in body["top"]] == ["CCCUSDT", "AAAUSDT", "GGGUSDT", "BBBUSDT", "EEEUSDT"]
    assert body["lock"]["state"] == ALLOWED and body["lock"]["code"] == "NO_ACTIVE_HOT_SYMBOL"
    assert body["contract"]["rank"] == "one_hour_range_pct desc"
    assert body["api"]["weight"] > 0 and "duration_ms" in body["api"]
    assert body["scanned_at"].endswith("Z")
    assert "rejections" not in body
    debug = TestClient(app).get("/api/crypto/hot-coins?debug=1").json()
    assert debug["rejections"]["BTCUSDT"] == "EXCLUDED_MAIN_SYMBOL"


def test_route_failure_is_an_error_body(route_service) -> None:
    route_service(HotScanner(FixtureTransport(market(), fail_batch=True), clock_ms=lambda: NOW_MS))
    response = TestClient(app).get("/api/crypto/hot-coins")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "HOT_SCAN_UPSTREAM"


def test_the_scan_route_is_get_only() -> None:
    methods = {m for r in app.routes if getattr(r, "path", "") == "/api/crypto/hot-coins"
               for m in getattr(r, "methods", set())}
    assert methods == {"GET"}
    assert TestClient(app).post("/api/crypto/hot-coins").status_code == 405


# --------------------------------------------------------------------------- lock contract

class Account:
    """A fake account holding exposures on several symbols."""

    def __init__(self, positions=None, orders=None, registry: InFlightRegistry | None = None,
                 broken: bool = False) -> None:
        self.positions = positions or {}
        self.orders = orders or {}
        self.registry = registry or InFlightRegistry()
        self.broken = broken
        self.reads: list[str] = []

    def exposure(self, symbol: str) -> SymbolExposure:
        self.reads.append(symbol)
        if self.broken:
            raise TimeoutError("account read failed")
        return SymbolExposure(symbol, Decimal(self.positions.get(symbol, "0")),
                              self.orders.get(symbol, 0), self.registry.count(symbol))


def test_btc_position_does_not_block_scan() -> None:
    account = Account(positions={"BTCUSDT": "0.08"})
    assert scan_permission(None, account).allowed
    assert scan_permission("MAGICUSDT", account).allowed
    assert account.reads == ["MAGICUSDT"]  # BTC is never consulted


def test_btc_open_order_does_not_block_scan() -> None:
    account = Account(orders={"BTCUSDT": 2})
    assert scan_permission("MAGICUSDT", account).code == hot_lock.HOT_SYMBOL_FLAT
    assert scan_permission("BTCUSDT", account).allowed  # BTC can never be the locked HOT


def test_current_hot_position_blocks_scan() -> None:
    decision = scan_permission("MAGICUSDT", Account(positions={"MAGICUSDT": "-12"}))
    assert decision.state == BLOCKED and decision.code == hot_lock.HOT_POSITION_OPEN


def test_current_hot_open_order_blocks_scan() -> None:
    decision = scan_permission("MAGICUSDT", Account(orders={"MAGICUSDT": 1}))
    assert decision.state == BLOCKED and decision.code == hot_lock.HOT_OPEN_ORDER


def test_current_hot_in_flight_blocks_scan_until_answered() -> None:
    registry = InFlightRegistry()
    account = Account(registry=registry)
    with registry.sending("MAGICUSDT"):
        assert scan_permission("MAGICUSDT", account).code == hot_lock.HOT_ORDER_IN_FLIGHT
        assert scan_permission("KAIAUSDT", account).allowed
    assert scan_permission("MAGICUSDT", account).allowed


def test_in_flight_with_unknown_outcome_stays_locked_until_reconciled() -> None:
    registry = InFlightRegistry()
    account = Account(registry=registry)
    with pytest.raises(TimeoutError):
        with registry.sending("MAGICUSDT"):
            raise TimeoutError("no answer")
    assert scan_permission("MAGICUSDT", account).code == hot_lock.HOT_ORDER_IN_FLIGHT
    registry.reconciled("MAGICUSDT")
    assert scan_permission("MAGICUSDT", account).allowed


def test_unknown_state_blocks() -> None:
    assert scan_permission("MAGICUSDT", Account(broken=True)).code == hot_lock.HOT_STATE_UNKNOWN
    assert scan_permission("MAGICUSDT", None).code == hot_lock.HOT_STATE_UNKNOWN

    class Partial:
        def exposure(self, symbol):
            return SymbolExposure(symbol, Decimal("0"), None, 0)
    assert scan_permission("MAGICUSDT", Partial()).code == hot_lock.HOT_STATE_UNKNOWN


def test_race_rule_keeps_previous_hot_when_state_changes_during_scan() -> None:
    account = Account()

    def scan_while_an_order_lands():
        account.orders["MAGICUSDT"] = 1
        return "KAIAUSDT"

    outcome = guarded_scan("MAGICUSDT", account, scan_while_an_order_lands)
    assert not outcome.applied and outcome.result is None
    assert outcome.decision.code == hot_lock.HOT_STATE_CHANGED_DURING_SCAN


def test_race_rule_applies_when_flat_at_both_ends_and_skips_scan_when_blocked() -> None:
    assert guarded_scan("MAGICUSDT", Account(), lambda: "KAIAUSDT").result == "KAIAUSDT"
    called = []
    blocked = guarded_scan("MAGICUSDT", Account(positions={"MAGICUSDT": "1"}),
                           lambda: called.append(1))
    assert not blocked.applied and called == []


def test_v0_server_holds_no_hot_symbol_and_no_account_source() -> None:
    assert hot_service.CURRENT_HOT_SYMBOL is None
    assert hot_service.EXPOSURE_SOURCE is None


# --------------------------------------------------------------------------- structure

HOT_FILES = sorted((APP / "crypto" / "hot_coins").glob("*.py")) + [
    APP / "crypto" / "terminal" / "hot_routes.py"]


def _imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append("." * node.level + (node.module or ""))
    return names


def test_hot_code_imports_no_account_order_or_paper_engine_module() -> None:
    for path in HOT_FILES:
        for name in _imports(path):
            parts = name.replace(".", " ").split()
            assert "live" not in parts, (path, name)
            assert "live_routes" not in parts, (path, name)
            assert not ("paper" in parts and "engine" in parts), (path, name)
            assert "c1" not in parts, (path, name)


def test_hot_code_names_no_mutating_endpoint() -> None:
    """The HOT package and its routes reach Binance's public market data only. The one write
    they own is the HOT slot itself (`POST /api/crypto/hot/scan`); account reads and writes live
    in `live_routes`, which registers its exposure source from outside."""
    forbidden = ("/fapi/v1/order", "/fapi/v1/batchOrders", "/fapi/v1/leverage", "/fapi/v1/marginType",
                 "/fapi/v1/listenKey", "/fapi/v2/account", "/fapi/v3/account", "positionRisk",
                 "openOrders", "signature=", "hmac", "X-MBX-APIKEY", ".put(", ".delete(")
    for path in HOT_FILES:
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, (path, needle)
        assert ".post(" not in text.replace('@app.post("/api/crypto/hot/scan")', ""), path


def test_hot_routes_registers_get_and_the_one_scan_post() -> None:
    tree = ast.parse((APP / "crypto" / "terminal" / "hot_routes.py").read_text(encoding="utf-8"))
    routes = [(d.func.attr, d.args[0].value) for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
              for d in n.decorator_list if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)]
    assert sorted(routes) == [("get", "/api/crypto/hot"), ("get", "/api/crypto/hot-coins"),
                              ("post", "/api/crypto/hot/scan")]
