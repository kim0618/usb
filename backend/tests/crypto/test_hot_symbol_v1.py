"""Dynamic HOT Trading V1: BTC fixed, one persistent HOT symbol beside it.

What is proved here, each against the failure it would have:

* only the HOT service can admit a symbol, and a former HOT symbol is refused again once retired;
* the HOT slot survives a restart and changes only through a guarded scan;
* the lock is per symbol: BTC exposure never blocks, HOT exposure (position, resting order, order
  in flight, or an exposure source that cannot answer) always does, and a change during the scan
  keeps the old HOT symbol;
* on the paper engine, BTC and HOT positions coexist and an order or CLOSE on one never touches
  the other;
* on the Binance side, a HOT CLOSE carries the HOT symbol and its own quantity only, its filters
  and Safe MAX are its own, and LIVE orders on the HOT symbol stay off behind their switch;
* the Binance market feed parses its own symbol only and its history pages correctly;
* twenty replacements leave no feed, socket, session, cache page or LIVE runtime behind.

No test touches the network.
"""
from __future__ import annotations

import asyncio
import json
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.crypto import symbols as symbols_module
from app.crypto.hot_coins import lock as hot_lock
from app.crypto.hot_coins import market as hot_market
from app.crypto.hot_coins import scanner as hs
from app.crypto.hot_coins import slot as hot_slot_module
from app.crypto.hot_coins.lock import InFlightRegistry, SymbolExposure
from app.crypto.hot_coins.scanner import HotScanner
from app.crypto.hot_coins.service import HotCoinService
from app.crypto.hot_coins.slot import HotSlotController, SlotStore
from app.crypto.paper.account import SharedCash
from app.crypto.paper.book import BookSide, Quote
from app.crypto.paper.instrument import RiskTierTable
from app.crypto.symbols import SymbolNotSupported, admit_dynamic, resolve
from tests.crypto.conftest import RISK_LIMIT_PAYLOAD, make_config
from tests.crypto.test_hot_coins import NOW_MS, FixtureTransport, market

from app.crypto.terminal.session import PaperSession  # noqa: E402

HOT_A, HOT_B = "LUMIAUSDT", "KAIAUSDT"


@pytest.fixture(autouse=True)
def clean_dynamic(monkeypatch):
    monkeypatch.setattr(symbols_module, "_dynamic", set())


def exchange_row(symbol: str, *, tick: str = "0.0000100", step: str = "0.1",
                 market_max: str = "800000", notional: str = "5") -> dict[str, Any]:
    return {"symbol": symbol, "contractType": "PERPETUAL", "status": "TRADING",
            "quoteAsset": "USDT", "marginAsset": "USDT", "deliveryDate": hs.PERPETUAL_NO_DELIVERY_MS,
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": tick, "minPrice": "0.00001", "maxPrice": "20"},
                {"filterType": "LOT_SIZE", "stepSize": step, "minQty": step, "maxQty": "8000000"},
                {"filterType": "MARKET_LOT_SIZE", "stepSize": step, "minQty": step, "maxQty": market_max},
                {"filterType": "MIN_NOTIONAL", "notional": notional},
            ]}


def hot_risk_payload(symbol: str, max_leverage: str = "25.00") -> dict[str, Any]:
    payload = json.loads(json.dumps(RISK_LIMIT_PAYLOAD))
    for row in payload["result"]["list"]:
        row["symbol"] = symbol
    payload["result"]["list"][0]["maxLeverage"] = max_leverage
    return payload


# --------------------------------------------------------------------------- symbol admission

def test_only_admission_permits_a_symbol_and_retirement_refuses_it_again() -> None:
    with pytest.raises(SymbolNotSupported):
        resolve(HOT_A)
    assert admit_dynamic(HOT_A) == HOT_A
    assert resolve(HOT_A.lower()) == HOT_A
    assert symbols_module.permitted_symbols()[:3] == ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    assert symbols_module.permitted_symbols()[-1] == HOT_A
    symbols_module.retire_dynamic(HOT_A)
    with pytest.raises(SymbolNotSupported):
        resolve(HOT_A)


@pytest.mark.parametrize("bad", ["BTC/USDT", "LUMIA USDT", "LUMIABUSD", "USDT", "", "A" * 41 + "USDT", "../USDT"])
def test_admission_refuses_anything_that_is_not_a_usdt_symbol(bad) -> None:
    with pytest.raises(SymbolNotSupported):
        admit_dynamic(bad)


def test_a_chinese_named_symbol_is_admitted_as_named() -> None:
    assert admit_dynamic("龙虾USDT") == "龙虾USDT"
    assert resolve("龙虾USDT") == "龙虾USDT"


def test_eth_and_sol_stay_supported_by_the_backend() -> None:
    assert resolve("ETHUSDT") == "ETHUSDT" and resolve("SOLUSDT") == "SOLUSDT"
    assert not symbols_module.is_dynamic("ETHUSDT")


# --------------------------------------------------------------------------- slot + lock

class Exposures:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[Decimal | None, int | None, int | None]] = {}
        self.broken = False
        self.on_read = None
        self.reads: list[str] = []

    def exposure(self, symbol: str) -> SymbolExposure:
        self.reads.append(symbol)
        if self.on_read:
            self.on_read(symbol)
        if self.broken:
            raise TimeoutError("account read failed")
        pos, orders, flight = self.rows.get(symbol, (Decimal(0), 0, 0))
        return SymbolExposure(symbol, pos, orders, flight)


def scan_result(symbol: str) -> hs.ScanResult:
    spec = market(**{symbol: dict(r1h=40.0)})
    return HotScanner(FixtureTransport(spec), clock_ms=lambda: NOW_MS).scan()


def controller_at(tmp_path: Path, source: Exposures) -> HotSlotController:
    ctl = HotSlotController(sources=[source], clock_ms=lambda: NOW_MS)
    ctl.configure(tmp_path / "hot_slot.json")
    ctl.on_commit.append(admit_dynamic)
    return ctl


def test_first_scan_selects_and_the_slot_survives_a_restart(tmp_path) -> None:
    ctl = controller_at(tmp_path, Exposures())
    out = ctl.scan_and_replace(lambda: scan_result(HOT_A))
    assert out.code == hot_slot_module.HOT_SELECTED and out.slot.symbol == HOT_A
    assert resolve(HOT_A) == HOT_A
    reborn = HotSlotController()
    assert reborn.configure(tmp_path / "hot_slot.json").symbol == HOT_A
    assert reborn.current().metrics["one_hour_range_pct"] == pytest.approx(40.0)


def test_a_corrupt_slot_file_reads_as_no_hot(tmp_path) -> None:
    path = tmp_path / "hot_slot.json"
    path.write_text("{not json")
    assert SlotStore(path).load() is None


def test_btc_exposure_never_blocks_a_scan(tmp_path) -> None:
    source = Exposures()
    source.rows["BTCUSDT"] = (Decimal("0.08"), 3, 1)
    ctl = controller_at(tmp_path, source)
    ctl.scan_and_replace(lambda: scan_result(HOT_A))
    out = ctl.scan_and_replace(lambda: scan_result(HOT_B))
    assert out.code == hot_slot_module.HOT_REPLACED and out.slot.symbol == HOT_B
    assert "BTCUSDT" not in source.reads


@pytest.mark.parametrize("row, code", [
    ((Decimal("-12"), 0, 0), hot_lock.HOT_POSITION_OPEN),
    ((Decimal(0), 1, 0), hot_lock.HOT_OPEN_ORDER),
    ((Decimal(0), 0, 1), hot_lock.HOT_ORDER_IN_FLIGHT),
    ((None, 0, 0), hot_lock.HOT_STATE_UNKNOWN),
])
def test_hot_exposure_blocks_without_scanning(tmp_path, row, code) -> None:
    source = Exposures()
    ctl = controller_at(tmp_path, source)
    ctl.scan_and_replace(lambda: scan_result(HOT_A))
    source.rows[HOT_A] = row
    scans = []
    out = ctl.scan_and_replace(lambda: scans.append(1) or scan_result(HOT_B))
    assert out.code == code and scans == []
    assert ctl.current().symbol == HOT_A


def test_an_unreadable_source_blocks(tmp_path) -> None:
    source = Exposures()
    ctl = controller_at(tmp_path, source)
    ctl.scan_and_replace(lambda: scan_result(HOT_A))
    source.broken = True
    assert ctl.scan_and_replace(lambda: scan_result(HOT_B)).code == hot_lock.HOT_STATE_UNKNOWN
    assert ctl.lock().state == "BLOCKED"


def test_a_change_during_the_scan_keeps_the_old_hot_symbol(tmp_path) -> None:
    source = Exposures()
    ctl = controller_at(tmp_path, source)
    ctl.scan_and_replace(lambda: scan_result(HOT_A))
    retired = []
    ctl.on_retire.append(retired.append)

    def scan_while_an_order_lands():
        source.rows[HOT_A] = (Decimal("5"), 0, 0)
        return scan_result(HOT_B)

    out = ctl.scan_and_replace(scan_while_an_order_lands)
    assert out.code == hot_lock.HOT_STATE_CHANGED_DURING_SCAN
    assert ctl.current().symbol == HOT_A and SlotStore(tmp_path / "hot_slot.json").load().symbol == HOT_A
    assert retired == []
    with pytest.raises(SymbolNotSupported):
        resolve(HOT_B)        # the discarded candidate was never admitted


def test_the_replacing_flag_is_set_before_the_recheck(tmp_path) -> None:
    """An order that registers in flight after the re-check must see the flag (and refuse)."""
    source = Exposures()
    ctl = controller_at(tmp_path, source)
    ctl.scan_and_replace(lambda: scan_result(HOT_A))
    seen = []
    source.on_read = lambda symbol: seen.append(ctl.replacing)
    ctl.scan_and_replace(lambda: scan_result(HOT_B))
    assert seen == [None, HOT_A]          # before-check unflagged, re-check flagged
    assert ctl.replacing is None


def test_a_second_scan_while_one_runs_is_refused_not_queued(tmp_path) -> None:
    ctl = controller_at(tmp_path, Exposures())
    gate = threading.Event()
    results = []

    def slow():
        gate.wait(5)
        return scan_result(HOT_A)

    t = threading.Thread(target=lambda: results.append(ctl.scan_and_replace(slow)))
    t.start()
    for _ in range(100):
        if ctl.scanning:
            break
        threading.Event().wait(0.01)
    second = ctl.scan_and_replace(lambda: scan_result(HOT_B))
    gate.set()
    t.join(5)
    assert second.code == hot_slot_module.HOT_SCAN_IN_PROGRESS
    assert results[0].slot.symbol == HOT_A


def test_same_symbol_again_keeps_its_selection_time(tmp_path) -> None:
    clock = [NOW_MS]
    ctl = HotSlotController(sources=[Exposures()], clock_ms=lambda: clock[0])
    ctl.configure(tmp_path / "s.json")
    first = ctl.scan_and_replace(lambda: scan_result(HOT_A)).slot
    clock[0] += 60_000
    again = ctl.scan_and_replace(lambda: scan_result(HOT_A))
    assert again.code == hot_slot_module.HOT_UNCHANGED
    assert again.slot.selected_at_ms == first.selected_at_ms


def test_live_in_flight_with_unknown_outcome_holds_until_reconciled() -> None:
    reg = InFlightRegistry()
    with pytest.raises(TimeoutError):
        with reg.sending(HOT_A):
            raise TimeoutError
    assert reg.count(HOT_A) == 1
    reg.reconciled(HOT_A)
    assert reg.count(HOT_A) == 0
    with pytest.raises(ValueError):
        with reg.sending(HOT_A, unknown_on_error=False):
            raise ValueError
    assert reg.count(HOT_A) == 0       # paper: an exception means nothing was filled


# --------------------------------------------------------------------------- paper end to end

class FakeFeed:
    def __init__(self, symbol: str, bid: str, ask: str) -> None:
        self.symbol = symbol
        self.bid, self.ask = Decimal(bid), Decimal(ask)
        self.stopped = False
        self.started = True
        self.telemetry = type("T", (), {"connected": True, "last_message_ms": NOW_MS, "last_error": None})()

    def quote(self) -> Quote:
        mid = (self.bid + self.ask) / 2
        return Quote(ts_ms=NOW_MS, bids=BookSide.from_rows([[str(self.bid), "1000000"]], descending=True),
                     asks=BookSide.from_rows([[str(self.ask), "1000000"]], descending=False),
                     mark_price=mid, last_price=mid, index_price=mid)

    def chart(self, limit: int = 120) -> list:
        return []

    def view(self) -> dict:
        return {"symbol": self.symbol, "connected": True}

    def seed_klines(self, **_: Any) -> None:
        pass

    def start(self) -> None:
        pass

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def paper(monkeypatch, tmp_path):
    """The deployed app's paper runtime on a temp root: a real BTC session and real HOT sessions
    built from saved Binance/Bybit-shaped reference files, fed by fake quotes."""
    from app.crypto.terminal import api as terminal_api
    from app.crypto.terminal import hot_routes, server
    from app.crypto.terminal.server import app

    runtime = terminal_api.runtime
    config = make_config(leverage="10")
    tiers = RiskTierTable.from_payload(RISK_LIMIT_PAYLOAD, source="test", source_sha256="x")
    btc = PaperSession(config=config, tiers=tiers, root=tmp_path)
    btc.start(NOW_MS)
    cash = SharedCash(owner="BTCUSDT")
    cash.join("BTCUSDT", btc.engine.account)
    feeds = {"BTCUSDT": FakeFeed("BTCUSDT", "100000.0", "100000.1")}
    made: dict[str, FakeFeed] = {}
    for name, value in {"base_config": config, "root": tmp_path, "session": btc,
                        "sessions": {"BTCUSDT": btc}, "feeds": feeds, "cash": cash,
                        "retired": {}, "symbol_errors": {}, "named_symbols": {},
                        "_seeded": set(), "_seeds": {}, "c1_auto": None, "error": None}.items():
        monkeypatch.setattr(runtime, name, value)
    monkeypatch.setattr(server, "trade_feeds", {"BTCUSDT": server.trade_feed})

    ctl = hot_slot_module.controller
    monkeypatch.setattr(ctl, "_slot", None)
    monkeypatch.setattr(ctl, "_loaded", True)
    monkeypatch.setattr(ctl, "store", SlotStore(tmp_path / "hot_slot.json"))
    monkeypatch.setattr(ctl, "paper_in_flight", InFlightRegistry())
    monkeypatch.setattr(ctl, "replacing", None)

    def prepare_reference(symbol: str) -> None:
        hot_market.write_reference(tmp_path, symbol, exchange_row=exchange_row(symbol),
                                   funding_minutes=480, risk_payload=hot_risk_payload(symbol))
        made[symbol] = feeds.setdefault(symbol, FakeFeed(symbol, "0.12000", "0.12001"))

    def http(base: str, path: str, params: dict) -> Any:
        if path == "/v5/market/risk-limit":
            return hot_risk_payload(params["symbol"])
        return []

    monkeypatch.setattr(hot_market, "_http_get", http)

    def use_market(symbol: str) -> None:
        """Make the next scan's #1 `symbol`, with a fake feed waiting for it."""
        spec = market(**{symbol: dict(r1h=40.0)})
        svc = HotCoinService(lambda: HotScanner(FixtureTransport(spec), clock_ms=lambda: NOW_MS),
                             min_interval_s=0)
        monkeypatch.setattr(hot_routes, "service", svc)
        made[symbol] = feeds.setdefault(symbol, FakeFeed(symbol, "0.12000", "0.12001"))

    # The fixture scanner's exchangeInfo rows lack filters; the reference is written directly.
    original_prepare = hot_routes._prepare

    def prepare(symbol: str, metrics: Any) -> str | None:
        prepare_reference(symbol)
        return None

    monkeypatch.setattr(ctl, "prepare", prepare)
    yield TestClient(app), runtime, use_market, made
    ctl.prepare = original_prepare


def open_hot(client, runtime, symbol):
    """What the screen does when the HOT tab is pressed: read its state. The 1 Hz loop that
    starts a new session in production is not running in tests, so it is started here."""
    response = client.get(f"/api/crypto/state?symbol={symbol}")
    session = runtime.sessions.get(symbol)
    if session is not None and not session.is_started:
        session.start(NOW_MS)
    return response


def order(client, symbol, side, intent="OPEN", qty=None):
    body = {"side": side, "intent": intent, "symbol": symbol}
    if qty is not None:
        body["qty"] = qty
    return client.post("/api/crypto/order", json=body)


def test_no_hot_until_the_first_scan(paper) -> None:
    client, runtime, use_market, feeds = paper
    body = client.get("/api/crypto/hot").json()
    assert body["hot"] is None and body["lock"]["code"] == "NO_ACTIVE_HOT_SYMBOL"
    assert body["main"] == "BTCUSDT"


def test_scenarios_a_to_e_on_the_paper_engine(paper) -> None:
    client, runtime, use_market, feeds = paper
    # A. BTC position, no HOT yet: scanning is allowed.
    assert order(client, "BTCUSDT", "LONG", qty="0.010").status_code == 200
    btc_before = runtime.sessions["BTCUSDT"].engine.account.position.signed_qty
    use_market(HOT_A)
    first = client.post("/api/crypto/hot/scan")
    assert first.status_code == 200, first.text
    assert first.json()["hot"]["symbol"] == HOT_A and first.json()["status"] == "HOT_SELECTED"

    # B. HOT position opens beside BTC; BTC is untouched.
    state = open_hot(client, runtime, HOT_A)
    assert state.status_code == 200 and state.json()["symbol"] == HOT_A
    assert state.json()["instrument"]["qty_step"] == "0.1"
    opened = order(client, HOT_A, "LONG", qty="100.0")
    assert opened.status_code == 200, opened.text
    assert runtime.sessions[HOT_A].engine.account.position.signed_qty == Decimal("100.0")
    assert runtime.sessions["BTCUSDT"].engine.account.position.signed_qty == btc_before

    # C. HOT position: the scan is blocked, nothing is scanned or replaced.
    use_market(HOT_B)
    blocked = client.post("/api/crypto/hot/scan")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "HOT_POSITION_OPEN"
    assert client.get("/api/crypto/hot").json()["lock"]["state"] == "BLOCKED"

    # HOT CLOSE closes HOT only.
    closed = order(client, HOT_A, "LONG", intent="CLOSE", qty="100.0")
    assert closed.status_code == 200, closed.text
    assert runtime.sessions[HOT_A].engine.account.position.is_flat
    assert runtime.sessions["BTCUSDT"].engine.account.position.signed_qty == btc_before

    # D. Flat on the backend: the scan is allowed again.
    assert client.get("/api/crypto/hot").json()["lock"]["state"] == "ALLOWED"

    # E. A new scan replaces LUMIA with KAIA and releases LUMIA.
    replaced = client.post("/api/crypto/hot/scan")
    assert replaced.status_code == 200, replaced.text
    body = replaced.json()
    assert body["status"] == "HOT_REPLACED" and body["hot"]["symbol"] == HOT_B
    assert body["released"]["feed"] == "STOPPED" and body["released"]["session"] == "RETIRED"
    assert HOT_A not in runtime.feeds and HOT_A not in runtime.sessions
    assert client.get(f"/api/crypto/state?symbol={HOT_A}").status_code == 400
    # LUMIA traded, so its realised PnL and fees stay in the one wallet.
    assert runtime.cash.members.get(HOT_A) is runtime.retired[HOT_A].engine.account
    # BTC never moved.
    assert runtime.sessions["BTCUSDT"].engine.account.position.signed_qty == btc_before


def test_btc_close_never_touches_the_hot_position(paper) -> None:
    client, runtime, use_market, feeds = paper
    use_market(HOT_A)
    client.post("/api/crypto/hot/scan")
    open_hot(client, runtime, HOT_A)
    assert order(client, "BTCUSDT", "SHORT", qty="0.005").status_code == 200
    assert order(client, HOT_A, "LONG", qty="50.0").status_code == 200
    assert order(client, "BTCUSDT", "SHORT", intent="CLOSE", qty="0.005").status_code == 200
    assert runtime.sessions["BTCUSDT"].engine.account.position.is_flat
    assert runtime.sessions[HOT_A].engine.account.position.signed_qty == Decimal("50.0")
    # And the shared wallet is still one purse across both.
    assert runtime.sessions[HOT_A].engine.account.cash is runtime.cash


def test_an_order_during_a_replacement_is_refused(paper) -> None:
    client, runtime, use_market, feeds = paper
    use_market(HOT_A)
    client.post("/api/crypto/hot/scan")
    open_hot(client, runtime, HOT_A)
    hot_slot_module.controller.replacing = HOT_A
    try:
        refused = order(client, HOT_A, "LONG", qty="100.0")
    finally:
        hot_slot_module.controller.replacing = None
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "HOT_REPLACING"
    assert runtime.sessions[HOT_A].engine.account.position.is_flat
    assert hot_slot_module.controller.paper_in_flight.count(HOT_A) == 0


def test_the_hot_symbols_own_minimum_notional_applies(paper) -> None:
    client, runtime, use_market, feeds = paper
    use_market(HOT_A)
    client.post("/api/crypto/hot/scan")
    open_hot(client, runtime, HOT_A)
    refused = order(client, HOT_A, "LONG", qty="10.0")          # 10 x 0.12 = 1.2 USDT < 5
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "NOTIONAL_BELOW_MINIMUM"
    off_grid = order(client, HOT_A, "LONG", qty="100.05")       # step is 0.1
    assert off_grid.status_code in (400, 409)
    assert runtime.sessions[HOT_A].engine.account.position.is_flat


def test_hot_leverage_is_capped_by_its_own_margin_table(paper, tmp_path) -> None:
    client, runtime, use_market, feeds = paper
    hot_market.write_reference(tmp_path, "LOWLEVUSDT", exchange_row=exchange_row("LOWLEVUSDT"),
                               funding_minutes=240, risk_payload=hot_risk_payload("LOWLEVUSDT", "5.00"))
    from app.crypto.terminal.api import dynamic_run_config
    config, tiers = dynamic_run_config(runtime.base_config, "LOWLEVUSDT", tmp_path)
    assert config.leverage == Decimal(5) and config.instrument.max_leverage == Decimal("5.00")
    assert config.instrument.funding_interval_minutes == 240
    assert config.run_id.endswith("-LOWLEVUSDT")


def test_twenty_replacements_leave_nothing_behind(paper) -> None:
    client, runtime, use_market, feeds = paper
    from app.crypto.terminal import api as terminal_api
    from app.crypto.terminal import server
    stopped = []
    names = [f"HOT{i:02d}USDT" for i in range(21)]
    for name in names:
        use_market(name)
        assert client.post("/api/crypto/hot/scan").status_code == 200
        assert open_hot(client, runtime, name).status_code == 200
        assert client.get(f"/api/crypto/chart?symbol={name}").status_code == 200
    current = names[-1]
    assert set(runtime.feeds) == {"BTCUSDT", current}
    assert set(runtime.sessions) == {"BTCUSDT", current}
    assert runtime.retired == {}                     # none of them traded: dropped, not kept
    assert set(runtime.cash.members) == {"BTCUSDT", current}
    assert symbols_module.dynamic_symbols() == (current,)
    assert set(server.trade_feeds) <= {"BTCUSDT", current}
    assert terminal_api.hot_chart_history.cached_symbols() <= {current}
    assert all(feeds[n].stopped for n in names[:-1]) and not feeds[current].stopped
    for old in names[:-1]:
        assert client.get(f"/api/crypto/state?symbol={old}").status_code == 400


def test_a_restart_brings_back_the_hot_symbol_and_a_held_former_one(paper, tmp_path, monkeypatch) -> None:
    client, runtime, use_market, feeds = paper
    use_market(HOT_A)
    client.post("/api/crypto/hot/scan")
    open_hot(client, runtime, HOT_A)
    assert order(client, HOT_A, "LONG", qty="60.0").status_code == 200
    # Simulate a process restart: a fresh runtime over the same root.
    from app.crypto.terminal import api as terminal_api
    fresh = terminal_api.Runtime()
    monkeypatch.setattr(symbols_module, "_dynamic", set())
    fresh.base_config = runtime.base_config
    fresh.root = tmp_path
    fresh.compress_segments = False
    fresh.cash = SharedCash(owner="BTCUSDT")
    fresh.cash.join("BTCUSDT", runtime.sessions["BTCUSDT"].engine.account)
    fresh.feeds = {HOT_A: FakeFeed(HOT_A, "0.12", "0.12001")}
    fresh._join_dynamic_runs_with_history()
    assert HOT_A in fresh.sessions and HOT_A in symbols_module.dynamic_symbols()
    assert fresh.sessions[HOT_A].engine.account.position.signed_qty == Decimal("60.0")
    assert fresh.held_symbols() == [HOT_A]


# --------------------------------------------------------------------------- Binance (LIVE)

class FakeLiveClient:
    def __init__(self, rows=None, orders=None, fail=False) -> None:
        self.rows = rows or []
        self.orders = orders or []
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []

    def call(self, name: str, params: dict) -> Any:
        self.calls.append((name, dict(params)))
        if self.fail:
            raise RuntimeError("binance down")
        if name == "position_risk":
            return [r for r in self.rows if r["symbol"] == params.get("symbol")]
        if name == "open_orders":
            return [o for o in self.orders if o["symbol"] == params.get("symbol")]
        raise AssertionError(name)


@pytest.fixture
def live(monkeypatch):
    from app.crypto.terminal import live_routes
    rt = live_routes.live_runtime

    def install(client):
        monkeypatch.setattr(live_routes, "_shared_ready", lambda: client is not None)
        monkeypatch.setattr(rt, "client", client)
        monkeypatch.setattr(live_routes, "live_in_flight", InFlightRegistry())
        return live_routes
    return install


def test_live_exposure_reads_only_the_hot_symbol(live) -> None:
    client = FakeLiveClient(rows=[{"symbol": "BTCUSDT", "positionAmt": "-0.080"},
                                  {"symbol": HOT_A, "positionAmt": "0"}],
                            orders=[{"symbol": "BTCUSDT"}])
    lr = live(client)
    exposure = lr.live_exposure.exposure(HOT_A)
    assert (exposure.position_amt, exposure.open_orders, exposure.in_flight) == (0, 0, 0)
    assert {params["symbol"] for _, params in client.calls} == {HOT_A}


def test_live_exposure_counts_hot_position_and_orders(live) -> None:
    client = FakeLiveClient(rows=[{"symbol": HOT_A, "positionAmt": "-25"}], orders=[{"symbol": HOT_A}])
    lr = live(client)
    exposure = lr.live_exposure.exposure(HOT_A)
    assert exposure.position_amt == Decimal(25) and exposure.open_orders == 1


def test_live_exposure_failure_is_unknown_and_blocks(live) -> None:
    lr = live(FakeLiveClient(fail=True))
    decision = hot_lock.scan_permission(HOT_A, lr.live_exposure)
    assert decision.code == hot_lock.HOT_STATE_UNKNOWN


def test_no_key_means_no_live_exposure_rather_than_unknown(live, monkeypatch) -> None:
    lr = live(None)
    monkeypatch.setattr(lr.live_runtime, "config", None)
    exposure = lr.live_exposure.exposure(HOT_A)
    assert exposure.position_amt == 0 and exposure.open_orders == 0


def test_hot_live_orders_and_leverage_stay_off_behind_their_switch(live, monkeypatch) -> None:
    lr = live(FakeLiveClient())
    admit_dynamic(HOT_A)

    class Untouchable:
        def __getattr__(self, name):
            raise AssertionError(f"adapter.{name} touched while HOT LIVE orders are off")

    entry = type("E", (), {"symbol": HOT_A, "adapter": Untouchable()})()
    monkeypatch.setattr(lr, "_entry", lambda symbol: (entry, None))
    monkeypatch.delenv(lr.HOT_LIVE_ORDERS_ENV, raising=False)
    from app.crypto.terminal.server import app
    client = TestClient(app)
    for path, body in (("/api/crypto/binance/order", {"side": "LONG", "intent": "OPEN", "symbol": HOT_A, "qty": "1"}),
                       ("/api/crypto/binance/order", {"side": "LONG", "intent": "CLOSE", "symbol": HOT_A}),
                       ("/api/crypto/binance/leverage", {"leverage": "5", "symbol": HOT_A})):
        response = client.post(path, json=body)
        assert response.status_code == 409 and response.json()["error"]["code"] == "HOT_LIVE_ORDERS_DISABLED"
    assert lr._hot_live_refusal("BTCUSDT") is None


def _hot_specs(monkeypatch):
    from tests.crypto import test_crypto_multisymbol as ms
    monkeypatch.setitem(ms.SPECS, HOT_A, {"base": "LUMIA", "tick": "0.0000100", "step": "0.1",
                                          "min_qty": "0.1", "market_max": "800000", "notional": "5",
                                          "price": "0.1200000", "qty_precision": 1, "price_precision": 7})
    return ms


def test_live_hot_close_carries_only_the_hot_symbol_and_quantity(monkeypatch) -> None:
    ms = _hot_specs(monkeypatch)
    admit_dynamic(HOT_A)
    fake = ms.MultiSymbolBinance(positions={"BTCUSDT": ms.held("BTCUSDT", "LONG", "0.058")},
                                 extra_rows=[ms.held(HOT_A, "SHORT", "250.0")])
    client, config, fake = ms.make(fake, trading_enabled=True)
    hot = ms.adapter_for(HOT_A, client, config)
    plan = hot.plan_order("", ms.CLOSE, symbol=HOT_A)
    assert (plan.symbol, plan.qty, plan.reduce_only) == (HOT_A, Decimal("250.0"), True)
    btc = ms.adapter_for("BTCUSDT", client, config)
    btc_plan = btc.plan_order("", ms.CLOSE, symbol="BTCUSDT")
    assert (btc_plan.symbol, btc_plan.qty) == ("BTCUSDT", Decimal("0.058"))
    with pytest.raises(ms.OrderRefused):
        hot.plan_order("", ms.CLOSE, symbol="BTCUSDT")      # the HOT adapter refuses BTC


def test_live_hot_filters_and_safe_max_are_its_own(monkeypatch) -> None:
    ms = _hot_specs(monkeypatch)
    admit_dynamic(HOT_A)
    client, config, fake = ms.make(ms.MultiSymbolBinance(), trading_enabled=False)
    sizing = ms.adapter_for(HOT_A, client, config).get_sizing()
    row = sizing["sides"]["LONG"]
    assert sizing["available"] and row["instrument"]["qty_step"] == Decimal("0.1")
    assert row["instrument"]["min_notional"] == Decimal("5")
    assert row["max_qty"] > 0 and row["max_qty"] % Decimal("0.1") == 0
    assert all(params.get("symbol") in (None, HOT_A) for _, _, params in fake.calls)


def test_a_live_config_admits_the_hot_symbol_only_while_admitted() -> None:
    from app.crypto.live.credentials import load_config
    config = load_config({"BINANCE_API_KEY": "k" * 20, "BINANCE_API_SECRET": "s" * 20})
    with pytest.raises(SymbolNotSupported):
        config.for_symbol(HOT_A)
    admit_dynamic(HOT_A)
    assert config.for_symbol(HOT_A).symbol == HOT_A


# --------------------------------------------------------------------------- Binance market data

def test_the_binance_feed_parses_its_own_symbol_only() -> None:
    feed = hot_market.BinancePublicFeed(symbol=HOT_A)
    assert feed.quote() is None
    feed.handle({"stream": "x", "data": {"e": "depthUpdate", "s": HOT_A, "b": [["0.12", "100"]],
                                          "a": [["0.1201", "50"]]}}, 1)
    assert feed.quote() is None                      # no mark yet: a partial view is not a quote
    feed.handle({"data": {"e": "markPriceUpdate", "s": HOT_A, "E": 5, "p": "0.12005",
                          "i": "0.1200", "r": "0.0001", "T": 9}}, 2)
    feed.handle({"data": {"e": "kline", "s": HOT_A, "E": 6,
                          "k": {"t": 60_000, "o": "0.1", "h": "0.2", "l": "0.09", "c": "0.12", "v": "9", "x": False}}}, 3)
    quote = feed.quote()
    assert (quote.best_bid, quote.best_ask, quote.mark_price) == (Decimal("0.12"), Decimal("0.1201"), Decimal("0.12005"))
    assert quote.last_price == Decimal("0.12") and quote.funding_rate == Decimal("0.0001")
    feed.handle({"data": {"e": "depthUpdate", "s": "BTCUSDT", "b": [["1", "1"]], "a": [["2", "1"]]}}, 4)
    assert feed.quote().best_bid == Decimal("0.12") and feed.telemetry.malformed == 1
    feed.handle({"data": {"e": "kline", "s": HOT_A, "k": {"t": 120_000, "o": "1", "h": "1", "l": "1", "c": "1", "v": "1", "x": True}}}, 5)
    feed.handle({"data": {"e": "kline", "s": HOT_A, "k": {"t": 60_000, "o": "9", "h": "9", "l": "9", "c": "9", "v": "9", "x": True}}}, 6)
    assert [b["start_ms"] for b in feed.klines] == [60_000, 120_000]   # an older bar never rewinds the tail


def test_the_binance_stream_url_encodes_a_chinese_symbol() -> None:
    url = hot_market.stream_url("/public/stream", [hot_market.stream_name("龙虾USDT", "depth20@100ms")])
    assert url.startswith("wss://fstream.binance.com/public/stream?streams=")
    assert "%E9%BE%99%E8%99%BEusdt@depth20@100ms" in url


def test_the_book_and_market_streams_go_to_their_own_paths() -> None:
    feed = hot_market.BinancePublicFeed(symbol=HOT_A)
    assert feed._streams("/public") == ["lumiausdt@depth20@100ms"]
    assert feed._streams("/market") == ["lumiausdt@markPrice@1s", "lumiausdt@kline_1m"]


def test_binance_history_pages_backwards_and_folds_10m() -> None:
    calls = []

    def request(path, params):
        calls.append(dict(params))
        end = params.get("endTime", 3_000_000 * 60_000)
        limit = params["limit"]
        step = 300_000 if params["interval"] == "5m" else 60_000
        last = (end // step) * step if "endTime" in params else end
        return [[last - i * step, "1", "2", "0.5", "1.5", "10"] for i in range(limit)][::-1]

    history = hot_market.BinanceChartHistory(request=request, clock=lambda: 0.0)
    body = history.get("1m", 2500, symbol=HOT_A)
    assert len(body["bars"]) == 2500 and body["source"] == "BINANCE_PUBLIC_KLINE"
    assert len(calls) == 3 and all(c["symbol"] == HOT_A for c in calls)
    starts = [b["start_ms"] for b in body["bars"]]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)
    folded = history.get("10m", 10, symbol=HOT_A)
    assert all(b["start_ms"] % 600_000 == 0 for b in folded["bars"])
    assert history.forget(HOT_A) == 2 and history.cached_symbols() == set()


def test_instrument_spec_comes_from_binance_filters_and_refuses_a_missing_one() -> None:
    spec = hot_market.instrument_from_exchange_info(exchange_row(HOT_A), max_leverage=Decimal(25),
                                                    funding_minutes=240, source="t")
    assert (spec.tick_size, spec.qty_step, spec.min_order_qty, spec.max_mkt_order_qty,
            spec.min_notional_value) == (Decimal("0.0000100"), Decimal("0.1"), Decimal("0.1"),
                                         Decimal("800000"), Decimal("5"))
    row = exchange_row(HOT_A)
    row["filters"] = [f for f in row["filters"] if f["filterType"] != "PRICE_FILTER"]
    with pytest.raises(ValueError):
        hot_market.instrument_from_exchange_info(row, max_leverage=Decimal(25), funding_minutes=480, source="t")


def test_binance_brackets_convert_to_a_continuous_margin_table() -> None:
    from tests.crypto.binance_fixtures import LEVERAGE_BRACKET
    payload = hot_market.risk_payload_from_binance_brackets(HOT_A, LEVERAGE_BRACKET[0]["brackets"])
    table = RiskTierTable.from_payload(payload, source="t", source_sha256="x")
    assert table.verify_continuity() == []
    assert table.tiers[0].max_leverage == Decimal(150)
    assert table.maintenance_margin(Decimal(1000)) == Decimal("4.000")


def test_a_symbol_without_a_margin_table_gets_no_paper_run(tmp_path) -> None:
    def http(base, path, params):
        if path == "/v5/market/risk-limit":
            return {"retCode": 0, "result": {"list": []}}
        return []
    with pytest.raises(hot_market.MarginTableUnavailable):
        hot_market.fetch_reference(tmp_path, "龙虾USDT", exchange_row("龙虾USDT"), http=http)
    paths = hot_market.fetch_reference(
        tmp_path, "龙虾USDT", exchange_row("龙虾USDT"), http=http,
        bracket_provider=lambda s: [{"bracket": 1, "initialLeverage": 20, "notionalCap": 5000,
                                     "notionalFloor": 0, "maintMarginRatio": 0.01, "cum": 0}])
    spec, tiers = hot_market.load_reference(paths)
    assert spec.max_leverage == Decimal(20) and tiers.tiers[0].maintenance_margin_rate == Decimal("0.01")
