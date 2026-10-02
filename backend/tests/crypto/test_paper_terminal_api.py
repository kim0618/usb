"""Terminal API and feed. The app is exercised without any network: the feed is fed by hand."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.crypto.paper.instrument import RiskTierTable
from app.crypto.terminal import api as terminal_api
from app.crypto.terminal.feed import BybitPublicFeed
from app.crypto.paper.ledger import InputTape
from app.crypto.terminal.session import PaperSession
from tests.crypto.conftest import RISK_LIMIT_PAYLOAD, make_config

D = Decimal


def snapshot_message(bid: str = "100000.0", ask: str = "100000.1") -> dict:
    return {"topic": "orderbook.50.BTCUSDT", "type": "snapshot", "ts": 1_000,
            "data": {"u": 1, "seq": 1, "b": [[bid, "10"]], "a": [[ask, "10"]]}}


def ticker_snapshot(mark: str = "100000.0", ts: int = 1_000) -> dict:
    return {"topic": "tickers.BTCUSDT", "type": "snapshot", "ts": ts,
            "data": {"symbol": "BTCUSDT", "markPrice": mark, "indexPrice": mark,
                     "lastPrice": mark, "fundingRate": "0.0001",
                     "nextFundingTime": str(8 * 3_600_000), "openInterest": "60000"}}


# ------------------------------------------------------------------ feed

def test_the_feed_has_no_quote_until_both_a_book_and_a_mark_have_arrived() -> None:
    feed = BybitPublicFeed()
    assert feed.quote() is None
    feed.handle(snapshot_message(), 1_001)
    assert feed.quote() is None  # a book without a mark is not a quote
    feed.handle(ticker_snapshot(), 1_001)
    quote = feed.quote()
    assert quote is not None and quote.best_bid == D("100000.0") and quote.best_ask == D("100000.1")
    assert quote.mark_price == D("100000.0")


def test_a_book_gap_asks_for_a_resync_instead_of_serving_a_stalled_book() -> None:
    feed = BybitPublicFeed()
    feed.handle(snapshot_message(), 1_001)
    feed.handle(ticker_snapshot(), 1_001)
    feed.handle({"topic": "orderbook.50.BTCUSDT", "type": "delta", "ts": 1_100,
                 "data": {"u": 99, "seq": 99, "b": [], "a": []}}, 1_101)
    assert feed.telemetry.book_gaps == 1
    assert feed._needs_resync is True
    assert feed.book.ready is False


def test_a_disconnect_clears_the_book_and_the_ticker_so_nothing_stale_is_quoted() -> None:
    feed = BybitPublicFeed()
    feed.handle(snapshot_message(), 1_001)
    feed.handle(ticker_snapshot(), 1_001)
    assert feed.quote() is not None
    feed.on_disconnect()
    assert feed.quote() is None
    assert feed.telemetry.connected is False


def test_ticker_deltas_merge_into_the_snapshot_rather_than_blanking_fields() -> None:
    feed = BybitPublicFeed()
    feed.handle(ticker_snapshot(), 1_001)
    feed.handle({"topic": "tickers.BTCUSDT", "type": "delta", "ts": 2_000,
                 "data": {"markPrice": "100500.0"}}, 2_001)
    assert feed.ticker["markPrice"] == "100500.0"
    assert feed.ticker["fundingRate"] == "0.0001"  # survived the delta


def test_only_the_latest_bar_is_replaced_so_the_chart_does_not_grow_a_duplicate() -> None:
    feed = BybitPublicFeed()
    feed.handle({"topic": "kline.1.BTCUSDT", "ts": 1_000, "data": [
        {"start": 60_000, "open": "1", "high": "2", "low": "1", "close": "2", "volume": "1",
         "confirm": False}]}, 1_001)
    feed.handle({"topic": "kline.1.BTCUSDT", "ts": 1_500, "data": [
        {"start": 60_000, "open": "1", "high": "3", "low": "1", "close": "3", "volume": "2",
         "confirm": True}]}, 1_501)
    assert len(feed.klines) == 1
    assert feed.klines[0]["close"] == "3" and feed.klines[0]["confirmed"] is True


# ------------------------------------------------------------------ session

def test_market_observations_are_throttled_to_one_a_second_but_a_command_forces_one(tmp_path) -> None:
    from app.crypto.paper.book import BookSide, Quote
    tiers = RiskTierTable.from_payload(RISK_LIMIT_PAYLOAD, source="t", source_sha256="h")
    session = PaperSession(config=make_config(), tiers=tiers, root=tmp_path)  # type: ignore[arg-type]

    def quote(ts: int) -> Quote:
        return Quote(ts_ms=ts, bids=BookSide.from_rows([["100000.0", "10"]], descending=True),
                     asks=BookSide.from_rows([["100000.1", "10"]], descending=False),
                     mark_price=D("100000.0"))

    session.start(1_000)
    assert session.observe(quote(1_000)) is True
    assert session.observe(quote(1_200)) is False   # inside the 1 s window
    assert session.observe(quote(2_200)) is True
    assert session.observe(quote(2_300), force=True) is True
    # Read back from the file: the live tape is write-through and keeps no copy in memory.
    written = InputTape.read(session.run_dir / "input.jsonl").records
    kinds = [record["kind"] for record in written]
    # 1_000 and 2_200 pass the throttle, 1_200 is inside the window, 2_300 is forced.
    assert kinds.count("MARKET") == 3 and kinds.count("COMMAND") == 1
    assert [record["payload"]["ts_ms"] for record in written if record["kind"] == "MARKET"] == [
        1_000, 2_200, 2_300]


def test_the_session_writes_its_config_and_both_tapes_to_the_run_directory(tmp_path) -> None:
    tiers = RiskTierTable.from_payload(RISK_LIMIT_PAYLOAD, source="t", source_sha256="h")
    session = PaperSession(config=make_config(), tiers=tiers, root=tmp_path)  # type: ignore[arg-type]
    session.start(1_000)
    written = json.loads((session.run_dir / "run_config.json").read_text())
    assert written["fees"]["version"] == "test-fees-v1"
    assert (session.run_dir / "input.jsonl").exists()
    assert (session.run_dir / "ledger.jsonl").exists()


# ------------------------------------------------------------------ config loading

def test_a_run_config_missing_a_fee_or_fx_field_refuses_to_load(tmp_path: Path) -> None:
    complete = {
        "run_id": "r", "starting_capital_krw": "1000000", "fx_krw_per_usdt": "1300",
        "fx_source": "s", "fx_asof_utc": "t", "fee_version": "v", "fee_taker_rate": "0.00055",
        "fee_maker_rate": "0.0002", "fee_source": "s", "fee_effective_date": "d",
        "slippage_model": "NONE", "slippage_bps": "0", "leverage": "10",
        "risk_limit_path": str(tmp_path / "risk.json"),
    }
    (tmp_path / "risk.json").write_text(json.dumps(RISK_LIMIT_PAYLOAD))
    for field in ("fee_taker_rate", "fee_source", "fee_effective_date", "fx_krw_per_usdt", "fx_source"):
        broken = dict(complete)
        broken[field] = ""
        path = tmp_path / f"{field}.json"
        path.write_text(json.dumps(broken))
        with pytest.raises(terminal_api.ConfigMissing) as missing:
            terminal_api.load_run_config(path)
        assert field in str(missing.value)
    path = tmp_path / "ok.json"
    path.write_text(json.dumps(complete))
    config, tiers = terminal_api.load_run_config(path)
    assert config.fees.taker_rate == D("0.00055") and len(tiers.tiers) == 3


# ------------------------------------------------------------------ HTTP

@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """A live-shaped app with the network replaced: no websocket, no REST seed."""
    risk = tmp_path / "risk.json"
    risk.write_text(json.dumps(RISK_LIMIT_PAYLOAD))
    config_path = tmp_path / "run_config.json"
    config_path.write_text(json.dumps({
        "run_id": "api-test", "starting_capital_krw": "1000000", "fx_krw_per_usdt": "1000",
        "fx_source": "test", "fx_asof_utc": "2026-09-23T00:00:00Z", "fee_version": "test-v1",
        "fee_taker_rate": "0.0006", "fee_maker_rate": "0.0002", "fee_source": "test",
        "fee_effective_date": "2026-01-01", "slippage_model": "NONE", "slippage_bps": "0",
        "leverage": "10", "risk_limit_path": str(risk)}))
    monkeypatch.setenv(terminal_api.CONFIG_ENV, str(config_path))
    monkeypatch.setenv("CRYPTO_PAPER_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(terminal_api.BybitPublicFeed, "seed_klines", lambda self, **kw: None)
    monkeypatch.setattr(terminal_api.BybitPublicFeed, "start", lambda self: None)
    monkeypatch.setattr(terminal_api.Runtime, "start", lambda self: None)
    with TestClient(terminal_api.create_app()) as test_client:
        terminal_api.runtime.feed.handle(snapshot_message(), 1_001)
        terminal_api.runtime.feed.handle(ticker_snapshot(), 1_001)
        terminal_api.runtime.session.start(1_000)
        # The 1 Hz observation loop is not running in tests, so take the first tick by hand.
        terminal_api.runtime.session.observe(terminal_api.runtime.feed.quote(), force=True)
        yield test_client


def test_state_reports_the_quote_the_account_and_the_fee_provenance(client: TestClient) -> None:
    body = client.get("/api/crypto/state").json()
    assert body["state"]["mode"] == "MANUAL"
    assert body["state"]["auto_available"] is False
    assert body["quote"]["best_bid"] == "100000.0" and body["quote"]["best_ask"] == "100000.1"
    assert body["fees"]["basis"] == "ASSUMED_PUBLIC_NON_VIP"
    assert body["fx"]["source"] == "test"
    assert body["account"]["position_side"] is None
    assert Decimal(body["krw"]["equity"]) == D("1000000")


def test_paper_c1_auto_is_outside_the_read_only_signal_namespace(client: TestClient) -> None:
    paths = {route.path for route in terminal_api.create_app().routes}
    assert "/api/crypto/paper/c1-auto" in paths
    assert "/api/crypto/c1-auto" not in paths


def test_reading_paper_c1_auto_state_never_creates_an_order(client: TestClient) -> None:
    controller = terminal_api.runtime.c1_auto
    assert controller is not None
    before = list(controller.session.engine.ledger.events)
    response = client.get("/api/crypto/paper/c1-auto")
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert controller.session.engine.ledger.events == before


def test_c1_auto_toggle_keeps_state_endpoints_on_the_same_paper_account(
        client: TestClient, monkeypatch) -> None:
    from app.crypto.terminal import c1_routes

    opened = client.post("/api/crypto/order",
                         json={"side": "LONG", "intent": "OPEN", "qty": "0.010"})
    assert opened.status_code == 200
    before_state = client.get("/api/crypto/state").json()
    before_ledger = client.get("/api/crypto/ledger").json()
    monkeypatch.setattr(c1_routes, "c1_runtime", object())

    enabled = client.post("/api/crypto/paper/c1-auto", json={"enabled": True})
    during_state = client.get("/api/crypto/state").json()
    during_ledger = client.get("/api/crypto/ledger").json()
    disabled = client.post("/api/crypto/paper/c1-auto", json={"enabled": False})
    after_state = client.get("/api/crypto/state").json()
    after_ledger = client.get("/api/crypto/ledger").json()

    assert enabled.status_code == 200 and disabled.status_code == 200
    for state in (during_state, after_state):
        assert state["run_id"] == before_state["run_id"]
        assert state["account"] == before_state["account"]
        assert state["krw"] == before_state["krw"]
    assert during_ledger == before_ledger == after_ledger


def test_a_manual_long_then_close_moves_the_account_through_the_api(client: TestClient) -> None:
    opened = client.post("/api/crypto/order",
                         json={"side": "LONG", "intent": "OPEN", "qty": "0.010"})
    assert opened.status_code == 200
    state = client.get("/api/crypto/state").json()
    assert state["account"]["position_side"] == "LONG"
    assert state["account"]["avg_entry"] == "100000.1"
    assert state["account"]["liquidation_price"] is not None

    terminal_api.runtime.feed.handle(ticker_snapshot("100500.0", ts=2_000), 2_001)
    terminal_api.runtime.feed.handle(
        {"topic": "orderbook.50.BTCUSDT", "type": "snapshot", "ts": 2_000,
         "data": {"u": 5, "seq": 5, "b": [["100500.0", "10"]], "a": [["100500.1", "10"]]}}, 2_001)
    closed = client.post("/api/crypto/order",
                         json={"side": "LONG", "intent": "CLOSE", "qty": "0.010"})
    assert closed.status_code == 200
    final = client.get("/api/crypto/state").json()
    assert final["account"]["position_side"] is None
    assert Decimal(final["account"]["realized_pnl"]) > 0


def test_sizing_by_notional_floors_to_the_quantity_grid(client: TestClient) -> None:
    response = client.post("/api/crypto/order",
                           json={"side": "LONG", "intent": "OPEN", "notional_usdt": "1234"})
    assert response.status_code == 200
    state = client.get("/api/crypto/state").json()
    # 1234 / 100000.1 = 0.01233..., floored to the 0.001 grid.
    assert state["account"]["position_qty"] == "0.012"


def test_a_rejected_order_returns_its_engine_code_and_leaves_the_account_alone(client: TestClient) -> None:
    response = client.post("/api/crypto/order",
                           json={"side": "LONG", "intent": "OPEN", "qty": "0.0015"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "QTY_OFF_GRID"
    assert client.get("/api/crypto/state").json()["account"]["position_side"] is None


def test_auto_is_refused_by_the_api_with_the_not_ready_code(client: TestClient) -> None:
    response = client.post("/api/crypto/mode", json={"action": "AUTO_ON"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AUTO_NOT_READY"


def test_emergency_needs_confirmation_and_then_flattens_the_book(client: TestClient) -> None:
    client.post("/api/crypto/order", json={"side": "SHORT", "intent": "OPEN", "qty": "0.010"})
    unconfirmed = client.post("/api/crypto/mode", json={"action": "EMERGENCY_ON"})
    assert unconfirmed.status_code == 409
    assert unconfirmed.json()["error"]["code"] == "CONFIRMATION_REQUIRED"
    confirmed = client.post("/api/crypto/mode", json={"action": "EMERGENCY_ON", "confirmed": True})
    assert confirmed.status_code == 200
    state = client.get("/api/crypto/state").json()
    assert state["state"]["mode"] == "EMERGENCY"
    assert state["account"]["position_side"] is None
    assert state["state"]["can_open_new_position"] is False


def test_the_emergency_close_button_works_without_changing_mode(client: TestClient) -> None:
    client.post("/api/crypto/order", json={"side": "LONG", "intent": "OPEN", "qty": "0.010"})
    response = client.post("/api/crypto/emergency-close")
    assert response.status_code == 200
    state = client.get("/api/crypto/state").json()
    assert state["account"]["position_side"] is None
    assert state["state"]["mode"] == "MANUAL"


def test_leverage_changes_while_flat_and_is_locked_while_open(client: TestClient) -> None:
    assert client.post("/api/crypto/leverage", json={"leverage": "25"}).status_code == 200
    assert client.get("/api/crypto/state").json()["leverage"] == "25"
    client.post("/api/crypto/order", json={"side": "LONG", "intent": "OPEN", "qty": "0.010"})
    locked = client.post("/api/crypto/leverage", json={"leverage": "5"})
    assert locked.status_code == 409
    assert locked.json()["error"]["code"] == "LEVERAGE_LOCKED_WHILE_OPEN"
    off_grid = client.post("/api/crypto/leverage", json={"leverage": "10.005"})
    assert off_grid.status_code in (400, 409)


def test_the_ledger_endpoint_returns_newest_first_with_a_total(client: TestClient) -> None:
    client.post("/api/crypto/order", json={"side": "LONG", "intent": "OPEN", "qty": "0.010"})
    body = client.get("/api/crypto/ledger?limit=5").json()
    assert body["total"] >= 5
    assert body["events"][0]["seq"] > body["events"][-1]["seq"]


def test_an_order_without_a_live_quote_is_refused_rather_than_filled(client: TestClient) -> None:
    terminal_api.runtime.feed.on_disconnect()
    response = client.post("/api/crypto/order",
                           json={"side": "LONG", "intent": "OPEN", "qty": "0.010"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_QUOTE"
