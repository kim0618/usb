"""The /api/crypto/sizing route.

It is registered from `terminal/server.py` onto the module-level app that `api.py` builds, so
these tests drive `server.app` rather than a fresh `create_app()`: that is exactly the object
the deployed service runs.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.crypto.terminal import api as terminal_api
from app.crypto.terminal import server as terminal_server
from tests.crypto.conftest import RISK_LIMIT_PAYLOAD
from tests.crypto.test_paper_terminal_api import snapshot_message, ticker_snapshot

D = Decimal


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    risk = tmp_path / "risk.json"
    risk.write_text(json.dumps(RISK_LIMIT_PAYLOAD))
    config_path = tmp_path / "run_config.json"
    config_path.write_text(json.dumps({
        "run_id": "sizing-test", "starting_capital_krw": "1000000", "fx_krw_per_usdt": "1000",
        "fx_source": "test", "fx_asof_utc": "2026-09-23T00:00:00Z", "fee_version": "test-v1",
        "fee_taker_rate": "0.0006", "fee_maker_rate": "0.0002", "fee_source": "test",
        "fee_effective_date": "2026-01-01", "slippage_model": "NONE", "slippage_bps": "0",
        "leverage": "10", "risk_limit_path": str(risk)}))
    monkeypatch.setenv(terminal_api.CONFIG_ENV, str(config_path))
    monkeypatch.setenv("CRYPTO_PAPER_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(terminal_api.BybitPublicFeed, "seed_klines", lambda self, **kw: None)
    monkeypatch.setattr(terminal_api.BybitPublicFeed, "start", lambda self: None)
    monkeypatch.setattr(terminal_api.Runtime, "start", lambda self: None)
    with TestClient(terminal_server.app) as test_client:
        terminal_api.runtime.feed.handle(snapshot_message(), 1_001)
        terminal_api.runtime.feed.handle(ticker_snapshot(), 1_001)
        terminal_api.runtime.session.start(1_000)
        terminal_api.runtime.session.observe(terminal_api.runtime.feed.quote(), force=True)
        yield test_client


def test_the_d3_routes_still_exist_alongside_the_new_one() -> None:
    paths = {route.path for route in terminal_server.app.routes if hasattr(route, "path")}
    # Registering from a second module must extend the app, never replace what D3 put there.
    assert {"/health", "/api/crypto/state", "/api/crypto/order", "/api/crypto/sizing"} <= paths


def test_sizing_returns_both_sides_by_default(client: TestClient) -> None:
    body = client.get("/api/crypto/sizing").json()
    assert set(body["sides"]) == {"LONG", "SHORT"}
    assert body["run_id"] == "sizing-test"
    assert body["leverage"] == "10"


def test_each_side_carries_four_presets_in_order(client: TestClient) -> None:
    body = client.get("/api/crypto/sizing").json()
    labels = [row["label"] for row in body["sides"]["LONG"]["presets"]]
    assert labels == ["25%", "HALF", "75%", "MAX"]


def test_a_single_side_can_be_requested(client: TestClient) -> None:
    body = client.get("/api/crypto/sizing?side=SHORT").json()
    assert set(body["sides"]) == {"SHORT"}


def test_an_unknown_side_is_refused(client: TestClient) -> None:
    response = client.get("/api/crypto/sizing?side=SIDEWAYS")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNKNOWN_SIDE"


def test_every_number_arrives_as_a_decimal_string(client: TestClient) -> None:
    body = client.get("/api/crypto/sizing").json()
    biggest = next(row for row in body["sides"]["LONG"]["presets"] if row["label"] == "MAX")
    # Money and quantities must not become floats on the way out.
    for key in ("qty", "fill_price", "notional", "fee", "reserved_margin", "required_total"):
        assert isinstance(biggest[key], str), key


def test_the_max_preset_is_an_order_the_api_actually_accepts(client: TestClient) -> None:
    body = client.get("/api/crypto/sizing").json()
    biggest = next(row for row in body["sides"]["LONG"]["presets"] if row["label"] == "MAX")
    assert biggest["feasible"]

    placed = client.post("/api/crypto/order",
                         json={"side": "LONG", "intent": "OPEN", "qty": biggest["qty"]})
    assert placed.status_code == 200, placed.json()
    assert placed.json()["state"]["account"]["position_qty"] == biggest["qty"]


def test_one_step_beyond_max_is_refused_by_the_order_endpoint(client: TestClient) -> None:
    body = client.get("/api/crypto/sizing").json()
    side = body["sides"]["LONG"]
    step = Decimal(side["instrument"]["qty_step"])
    biggest = next(row for row in side["presets"] if row["label"] == "MAX")

    refused = client.post("/api/crypto/order", json={
        "side": "LONG", "intent": "OPEN", "qty": str(Decimal(biggest["qty"]) + step)})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "INSUFFICIENT_MARGIN"


def test_asking_for_sizing_does_not_move_the_account(client: TestClient) -> None:
    before = client.get("/api/crypto/state").json()
    for _ in range(3):
        client.get("/api/crypto/sizing")
    after = client.get("/api/crypto/state").json()
    assert after["account"] == before["account"]
    assert after["ledger_event_count"] == before["ledger_event_count"]


def test_sizing_after_an_entry_reflects_the_margin_already_committed(client: TestClient) -> None:
    first = client.get("/api/crypto/sizing").json()
    ceiling_before = Decimal(first["sides"]["LONG"]["max_qty"])

    client.post("/api/crypto/order", json={"side": "LONG", "intent": "OPEN", "qty": "0.010"})

    second = client.get("/api/crypto/sizing").json()
    assert Decimal(second["sides"]["LONG"]["max_qty"]) < ceiling_before
    # And the opposite side is now unavailable, with the engine's reason.
    assert second["sides"]["SHORT"]["max_feasible"] is False
    assert second["sides"]["SHORT"]["reject_code"] == "REVERSE_NOT_ALLOWED"


def test_sizing_tracks_a_leverage_change(client: TestClient) -> None:
    at_ten = Decimal(client.get("/api/crypto/sizing").json()["sides"]["LONG"]["max_qty"])
    client.post("/api/crypto/leverage", json={"leverage": "20"})
    at_twenty = Decimal(client.get("/api/crypto/sizing").json()["sides"]["LONG"]["max_qty"])
    assert at_twenty > at_ten


def test_sizing_is_unavailable_rather_than_wrong_when_the_run_is_not_configured(
        client: TestClient, monkeypatch) -> None:
    # A terminal with no run must say so. Returning a size computed from a default account would
    # be the one failure mode worth more than all the others: a number that looks tradable.
    monkeypatch.setattr(terminal_api.runtime, "session", None)
    monkeypatch.setattr(terminal_api.runtime, "error", "run config not found")
    response = client.get("/api/crypto/sizing")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RUN_NOT_CONFIGURED"


# ------------------------------------------------------------------ order-time guard (D4.1)

def test_an_order_that_could_not_be_closed_is_refused_at_submit(client: TestClient) -> None:
    """A preset priced seconds ago is not a promise. The size is re-checked on the tick the
    order is actually judged against, so a stale one cannot be forced through."""
    # A book with plenty of ask and almost no bid: the entry would fill, the exit would not.
    terminal_api.runtime.feed.handle({
        "topic": "orderbook.50.BTCUSDT", "type": "snapshot", "ts": 2_000,
        "data": {"u": 2, "seq": 2, "b": [["100000.0", "0.002"]], "a": [["100000.1", "50"]]}},
        2_000)
    terminal_api.runtime.feed.handle(ticker_snapshot(ts=2_000), 2_000)

    response = client.post("/api/crypto/order",
                           json={"side": "LONG", "intent": "OPEN", "qty": "0.010"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "NO_LIQUIDITY"
    # And it tells the operator what would have been safe.
    assert "안전 최대" in response.json()["error"]["message"]
    assert client.get("/api/crypto/state").json()["account"]["position_side"] is None


def test_a_close_is_never_blocked_by_the_entry_guard(client: TestClient) -> None:
    assert client.post("/api/crypto/order",
                       json={"side": "LONG", "intent": "OPEN", "qty": "0.010"}).status_code == 200
    # Thin the bid after the position exists; closing must still be attempted, not pre-refused.
    terminal_api.runtime.feed.handle({
        "topic": "orderbook.50.BTCUSDT", "type": "snapshot", "ts": 3_000,
        "data": {"u": 3, "seq": 3, "b": [["100000.0", "9"]], "a": [["100000.1", "0.001"]]}},
        3_000)
    terminal_api.runtime.feed.handle(ticker_snapshot(ts=3_000), 3_000)
    closed = client.post("/api/crypto/order",
                         json={"side": "LONG", "intent": "CLOSE", "qty": "0.010"})
    assert closed.status_code == 200
    assert closed.json()["state"]["account"]["position_side"] is None


def test_the_sizing_payload_carries_the_snapshot_it_was_priced_on(client: TestClient) -> None:
    body = client.get("/api/crypto/sizing").json()
    side = body["sides"]["LONG"]
    assert side["max_definition"] == "ENTRY_AND_IMMEDIATE_EXIT_ON_THIS_SNAPSHOT"
    for key in ("quote_ts_ms", "best_bid", "best_ask", "mark_price", "entry_depth", "exit_depth"):
        assert side[key] is not None, key
    biggest = next(row for row in side["presets"] if row["label"] == "MAX")
    assert biggest["entry_feasible"] is True and biggest["exit_feasible"] is True
    # The preview fill is the ask, never the mark.
    assert biggest["fill_price"] == side["best_ask"]
    assert biggest["exit_fill_price"] == side["best_bid"]


def test_max_from_the_api_opens_and_closes_through_the_api(client: TestClient) -> None:
    biggest = next(row for row in client.get("/api/crypto/sizing").json()["sides"]["LONG"]["presets"]
                   if row["label"] == "MAX")
    assert biggest["feasible"]
    opened = client.post("/api/crypto/order",
                         json={"side": "LONG", "intent": "OPEN", "qty": biggest["qty"]})
    assert opened.status_code == 200, opened.json()
    closed = client.post("/api/crypto/order",
                         json={"side": "LONG", "intent": "CLOSE", "qty": biggest["qty"]})
    assert closed.status_code == 200, closed.json()
    assert closed.json()["state"]["account"]["position_side"] is None
