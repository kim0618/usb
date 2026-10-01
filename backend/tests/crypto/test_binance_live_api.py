"""The `/api/crypto/binance/*` routes, and the proof that adding them changed nothing on the
paper side.

The app under test is the deployed one (`terminal.server:app`, which imports `live_routes`), and
it is driven without its lifespan so no paper run, no Bybit socket and no Binance socket is
started. Every Binance response comes from the mock transport.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.crypto.live.adapter import BinanceLiveAdapter
from app.crypto.live.mirror import LiveEvent, LiveMirror
from app.crypto.terminal import live_routes
from app.crypto.terminal.server import app
from tests.crypto.binance_fixtures import (FakeBinance, POSITION_RISK_FLAT, POSITION_RISK_LONG,
                                           make_client, make_config)

D = Decimal


@pytest.fixture
def no_key(monkeypatch) -> TestClient:
    for name in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "BINANCE_LIVE_TRADING_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(live_routes.live_runtime, "adapter", None)
    monkeypatch.setattr(live_routes.live_runtime, "config", None)
    monkeypatch.setattr(live_routes.live_runtime, "error", None)
    return TestClient(app)


@pytest.fixture
def live(monkeypatch, tmp_path: Path):
    """An app whose LIVE adapter is wired to the fake exchange. Trading stays disabled."""
    fake = FakeBinance()
    fake.position_rows = POSITION_RISK_FLAT
    client, fake = make_client(fake, trading_enabled=False)
    config = make_config(trading_enabled=False)
    mirror = LiveMirror(path=tmp_path / "live" / "events.jsonl",
                        account_fingerprint=config.fingerprint)
    adapter = BinanceLiveAdapter(config=config, client=client, mirror=mirror)
    monkeypatch.setattr(live_routes.live_runtime, "adapter", adapter)
    monkeypatch.setattr(live_routes.live_runtime, "config", config)
    monkeypatch.setattr(live_routes.live_runtime, "error", None)
    return TestClient(app), adapter, fake, mirror


# ------------------------------------------------------------------ runtime construction

def test_the_live_runtime_syncs_the_clock_before_the_first_account_read(monkeypatch, tmp_path: Path) -> None:
    """The offset has to be measured while the adapter is being built. Measuring it only after a
    signed read has already failed means the operator sees a CLOCK_SKEW panel on a machine whose
    key, IP and account are all fine - which is exactly what a fast local clock produced."""
    fake = FakeBinance()
    fake.position_rows = POSITION_RISK_FLAT
    monkeypatch.setenv("BINANCE_API_KEY", "test-api-key-0123456789")
    monkeypatch.setenv("BINANCE_API_SECRET", "test-api-secret-abcdef")
    monkeypatch.setenv("BINANCE_LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("CRYPTO_LIVE_USER_STREAM", "off")
    monkeypatch.setenv(live_routes.LIVE_ROOT_ENV, str(tmp_path / "live"))

    real = live_routes.BinanceFuturesClient

    def with_fake_transport(**kwargs):
        return real(transport=fake.transport(), **kwargs)

    monkeypatch.setattr(live_routes, "BinanceFuturesClient", with_fake_transport)
    runtime = live_routes.LiveRuntime()
    adapter = runtime.build()

    assert adapter is not None
    assert fake.count("/fapi/v1/time") == 1
    assert runtime.error is None
    # The offset landed on the client the adapter will use, not on a throwaway: the fake's
    # server time is a fixed past instant, so a synced client carries a large negative offset.
    assert adapter.client.telemetry.clock_offset_ms != 0
    assert adapter.client.telemetry.trade_requests == 0
    runtime.shutdown()


def test_a_clock_sync_that_fails_leaves_the_route_serving_rather_than_dead(monkeypatch, tmp_path: Path) -> None:
    fake = FakeBinance()
    fake.position_rows = POSITION_RISK_FLAT
    fake.fail("GET", "/fapi/v1/time", 503, None, "Service unavailable.")
    monkeypatch.setenv("BINANCE_API_KEY", "test-api-key-0123456789")
    monkeypatch.setenv("BINANCE_API_SECRET", "test-api-secret-abcdef")
    monkeypatch.setenv("CRYPTO_LIVE_USER_STREAM", "off")
    monkeypatch.setenv(live_routes.LIVE_ROOT_ENV, str(tmp_path / "live"))

    real = live_routes.BinanceFuturesClient
    monkeypatch.setattr(live_routes, "BinanceFuturesClient",
                        lambda **kwargs: real(transport=fake.transport(), **kwargs))
    runtime = live_routes.LiveRuntime()
    adapter = runtime.build()

    assert adapter is not None
    assert runtime.error is not None and "clock sync failed" in runtime.error
    runtime.shutdown()


# ------------------------------------------------------------------ without a key

def test_status_without_a_key_tells_the_ui_to_stay_on_paper(no_key: TestClient) -> None:
    body = no_key.get("/api/crypto/binance/status").json()
    assert body["available"] is False and body["ready"] is False
    assert body["blockers"][0]["code"] == "CREDENTIALS_MISSING"
    assert body["gates"]["armed"] is False
    assert body["config"]["credentials_present"] is False


def test_the_status_route_publishes_the_endpoint_registry_for_provenance(no_key: TestClient) -> None:
    rows = no_key.get("/api/crypto/binance/status").json()["endpoints"]
    paths = {row["path"] for row in rows}
    assert "/fapi/v3/positionRisk" in paths and "/fapi/v1/ticker/bookTicker" in paths
    assert all(row["doc"].startswith("https://developers.binance.com/") for row in rows)


def test_account_without_a_key_is_unavailable_rather_than_empty(no_key: TestClient) -> None:
    response = no_key.get("/api/crypto/binance/account")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LIVE_UNAVAILABLE"


# ------------------------------------------------------------------ with a key

def test_the_account_route_serves_binances_own_figures(live) -> None:
    client, _, _, _ = live
    body = client.get("/api/crypto/binance/account").json()
    assert body["ready"] is True and body["source"] == "BINANCE_LIVE"
    assert body["balance"]["wallet_balance"] == "1000.00000000"
    assert body["balance"]["available_balance"] == "875.00000000"
    assert body["symbol_config"]["leverage"] == "10"
    assert body["symbol_config"]["margin_type"] == "CROSSED"
    assert body["position_mode"]["mode"] == "ONE_WAY"
    assert body["mark"]["mark_price"] == "83500.00000000"
    assert body["gates"]["armed"] is False


def test_the_account_route_says_which_rate_converted_its_won_figures(live) -> None:
    client, _, _, _ = live
    body = client.get("/api/crypto/binance/account").json()
    # No paper run is configured in this app, so there is no rate and no won figure is invented.
    assert body["krw_per_usdt"] is None and body["krw"] is None
    assert "USDT" in body["krw_note"]


def test_the_preview_route_prices_both_sides_on_binances_book(live) -> None:
    client, _, _, _ = live
    body = client.get("/api/crypto/binance/preview", params={"qty": "0.002"}).json()
    assert set(body["sides"]) == {"LONG", "SHORT"}
    long_side = body["sides"]["LONG"]
    assert long_side["feasible"] is True
    assert long_side["fee_rate"] == "0.000400"
    assert long_side["source"] == "BINANCE_LIVE"
    assert Decimal(long_side["entry_fill_price"]) >= D("83500.10")


def test_the_preview_route_needs_a_size(live) -> None:
    client, _, _, _ = live
    response = client.get("/api/crypto/binance/preview")
    assert response.status_code == 400 and response.json()["error"]["code"] == "QTY_REQUIRED"


def test_fills_and_funding_come_from_binance_and_say_so(live) -> None:
    client, _, _, _ = live
    fills = client.get("/api/crypto/binance/fills").json()
    assert fills["fills"][0]["commission"] == "0.50100000"
    assert fills["authority"] == "binance GET /fapi/v1/userTrades"
    funding = client.get("/api/crypto/binance/funding").json()
    assert funding["funding"][0]["income_type"] == "FUNDING_FEE"
    assert "FUNDING_FEE" in funding["authority"]


def test_an_order_is_refused_with_the_plan_attached_and_nothing_is_sent(live) -> None:
    client, _, fake, mirror = live
    response = client.post("/api/crypto/binance/order",
                           json={"side": "LONG", "intent": "OPEN", "qty": "0.002"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LIVE_TRADING_DISABLED"
    assert fake.count("/fapi/v1/order") == 0
    kinds = [event["event_type"] for event in mirror.events]
    assert LiveEvent.ORDER_INTENT in kinds and LiveEvent.ORDER_SENT not in kinds


def test_a_reverse_is_refused_before_the_trading_gate_is_even_reached(live) -> None:
    client, adapter, fake, _ = live
    fake.position_rows = POSITION_RISK_LONG
    adapter.resync()
    response = client.post("/api/crypto/binance/order",
                           json={"side": "SHORT", "intent": "OPEN", "qty": "0.002"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVERSE_NOT_ALLOWED"


def test_a_leverage_change_is_refused_by_the_same_flag(live) -> None:
    client, _, fake, _ = live
    response = client.post("/api/crypto/binance/leverage", json={"leverage": "5"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LIVE_TRADING_DISABLED"
    assert fake.count("/fapi/v1/leverage") == 0


def test_a_non_integer_leverage_is_rejected_before_anything_is_attempted(live) -> None:
    client, _, fake, _ = live
    response = client.post("/api/crypto/binance/leverage", json={"leverage": "2.5"})
    assert response.status_code == 400
    assert fake.count("/fapi/v1/leverage") == 0


def test_the_events_route_serves_the_audit_mirror_and_labels_it_as_one(live) -> None:
    client, _, _, _ = live
    client.post("/api/crypto/binance/order", json={"side": "LONG", "intent": "OPEN", "qty": "0.002"})
    body = client.get("/api/crypto/binance/events").json()
    assert body["total"] >= 1
    assert "never replayed" in body["role"]


def test_no_live_response_carries_a_credential(live) -> None:
    client, _, _, _ = live
    for path in ("/api/crypto/binance/status", "/api/crypto/binance/account",
                 "/api/crypto/binance/fills", "/api/crypto/binance/events"):
        text = json.dumps(client.get(path).json())
        assert "test-api-key-0123456789" not in text
        assert "test-api-secret-abcdef" not in text


# ------------------------------------------------------------------ paper regression

def test_the_paper_routes_are_untouched_by_the_live_registration(no_key: TestClient) -> None:
    """The LIVE module registers new paths and rebinds nothing. `/api/crypto/live` still means
    the paper position's fast poll, and the paper routes still answer from the paper runtime."""
    paths = {route.path for route in app.routes}
    for path in ("/api/crypto/state", "/api/crypto/order", "/api/crypto/live",
                 "/api/crypto/order-preview", "/api/crypto/sizing", "/api/crypto/reset"):
        assert path in paths
    # No paper run is configured here, so the paper routes answer with their own 503 - the LIVE
    # import did not replace the handler.
    body = no_key.get("/api/crypto/state").json()
    assert body["error"]["code"] == "RUN_NOT_CONFIGURED"


def test_a_live_route_reads_exactly_one_thing_from_the_paper_session(live, monkeypatch) -> None:
    """The KRW rate, and nothing else.

    A recording stand-in allows `config` (the fixed rate lives there) and fails on every other
    attribute, so a future edit that reads a paper balance, an engine or the ledger from a LIVE
    handler fails here instead of quietly coupling the two accounts.
    """
    client, _, _, _ = live
    touched: list[str] = []

    class FixedRate:
        """Just enough of a paper run config to carry the display rate."""
        fx = type("Fx", (), {"krw_per_usdt": Decimal("1400")})()

    class OnlyConfig:
        config = FixedRate()

        def __getattr__(self, name: str):
            touched.append(name)
            raise AssertionError(f"a LIVE route touched the paper session ({name})")

    monkeypatch.setattr(live_routes.runtime, "session", None)
    for path in ("/api/crypto/binance/status", "/api/crypto/binance/account",
                 "/api/crypto/binance/preview?qty=0.002", "/api/crypto/binance/fills",
                 "/api/crypto/binance/events"):
        assert client.get(path).status_code == 200
    monkeypatch.setattr(live_routes.runtime, "session", OnlyConfig())
    body = client.get("/api/crypto/binance/account").json()
    assert body["krw_per_usdt"] == "1400"
    assert touched == [], f"LIVE handlers reached for {touched} on the paper session"


def test_position_card_krw_uses_the_same_fixed_rate_as_account_summary(live, monkeypatch) -> None:
    client, adapter, _fake, _mirror = live
    monkeypatch.setattr(adapter, "get_position_card", lambda: {
        "open": True, "unrealized_pnl": Decimal("9.56"),
        "net_if_closed": Decimal("8.88"), "net_complete": True})
    session = type("Session", (), {"config": type("Config", (), {
        "fx": type("Fx", (), {"krw_per_usdt": Decimal("1400")})()})()})()
    monkeypatch.setattr(live_routes.runtime, "session", session)
    body = client.get("/api/crypto/binance/position").json()
    assert body["krw_per_usdt"] == "1400"
    assert body["krw"] == {"unrealized_pnl": "13384.00", "net_if_closed": "12432.00"}
