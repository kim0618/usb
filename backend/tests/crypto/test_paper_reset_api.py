"""POST /api/crypto/reset, driven through the app the deployed service runs."""
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
        "run_id": "reset-test", "starting_capital_krw": "1000000", "fx_krw_per_usdt": "1000",
        "fx_source": "test", "fx_asof_utc": "2026-09-24T00:00:00Z", "fee_version": "test-v1",
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


def trade(client: TestClient) -> None:
    assert client.post("/api/crypto/order",
                       json={"side": "LONG", "intent": "OPEN", "qty": "0.010"}).status_code == 200
    assert client.post("/api/crypto/order",
                       json={"side": "LONG", "intent": "CLOSE", "qty": "0.010"}).status_code == 200


def test_reset_defaults_to_ten_million_won(client: TestClient) -> None:
    trade(client)
    response = client.post("/api/crypto/reset", json={})
    assert response.status_code == 200, response.json()
    account = response.json()["state"]["account"]
    assert D(account["equity"]) == D("10000")          # 10,000,000 KRW at 1000 KRW/USDT
    assert D(account["available_balance"]) == D("10000")
    assert account["position_side"] is None


def test_reset_reports_the_krw_figure_the_operator_asked_for(client: TestClient) -> None:
    client.post("/api/crypto/reset", json={})
    body = client.get("/api/crypto/state").json()
    assert D(body["krw"]["equity"]) == D("10000000")


def test_an_explicit_target_is_honoured(client: TestClient) -> None:
    response = client.post("/api/crypto/reset", json={"target_krw": "5000000"})
    assert response.status_code == 200
    assert D(response.json()["state"]["account"]["equity"]) == D("5000")


def test_reset_is_refused_while_a_position_is_open(client: TestClient) -> None:
    assert client.post("/api/crypto/order",
                       json={"side": "LONG", "intent": "OPEN", "qty": "0.010"}).status_code == 200
    response = client.post("/api/crypto/reset", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RESET_BLOCKED_OPEN_POSITION"


def test_a_refused_reset_leaves_no_trace_in_the_ledger(client: TestClient) -> None:
    client.post("/api/crypto/order", json={"side": "LONG", "intent": "OPEN", "qty": "0.010"})
    before = client.get("/api/crypto/ledger?limit=500").json()
    client.post("/api/crypto/reset", json={})
    after = client.get("/api/crypto/ledger?limit=500").json()
    # A rejected leverage change writes nothing either; a rejected reset behaves the same.
    assert after["total"] == before["total"]


@pytest.mark.parametrize("target", ["0", "-100", "abc"])
def test_a_bad_target_is_refused(client: TestClient, target: str) -> None:
    response = client.post("/api/crypto/reset", json={"target_krw": target})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "RESET_TARGET_INVALID"


def test_the_reset_shows_up_in_the_ledger_in_time_order(client: TestClient) -> None:
    trade(client)
    client.post("/api/crypto/reset", json={})
    events = client.get("/api/crypto/ledger?limit=500").json()["events"]
    assert events[0]["event_type"] == "ACCOUNT_RESET"       # newest first
    assert D(events[0]["after_equity_krw"]) == D("10000000")
    # And the trade that preceded it is still there.
    assert any(event["event_type"] == "POSITION_CLOSE" for event in events)


def test_history_and_performance_survive_the_reset(client: TestClient) -> None:
    trade(client)
    before = client.get("/api/crypto/performance").json()
    before_trades = client.get("/api/crypto/trades").json()["total"]

    client.post("/api/crypto/reset", json={})

    after = client.get("/api/crypto/performance").json()
    assert after["trades"] == before["trades"] == 1
    assert client.get("/api/crypto/trades").json()["total"] == before_trades
    assert after["fees"] == before["fees"]
    assert after["gross_pnl"] == before["gross_pnl"]
    assert after["capital_resets"] == 1
    assert len(after["segments"]) == 2


def test_sizing_grows_to_match_the_new_balance(client: TestClient) -> None:
    before = D(client.get("/api/crypto/sizing").json()["sides"]["LONG"]["max_qty"])
    client.post("/api/crypto/reset", json={})
    after = D(client.get("/api/crypto/sizing").json()["sides"]["LONG"]["max_qty"])
    # Ten times the capital buys roughly ten times the position; the point is that the next
    # read is computed fresh rather than served from anything cached.
    assert after > before


def test_resetting_twice_is_allowed(client: TestClient) -> None:
    assert client.post("/api/crypto/reset", json={}).status_code == 200
    second = client.post("/api/crypto/reset", json={"target_krw": "2000000"})
    assert second.status_code == 200
    assert D(second.json()["state"]["account"]["equity"]) == D("2000")
    assert second.json()["state"]["account"]["reset_count"] == 2


def test_the_reset_survives_a_restart(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    trade(client)
    client.post("/api/crypto/reset", json={})
    before = client.get("/api/crypto/state").json()

    # Same run directory, fresh runtime: this is what systemctl restart does.
    terminal_api.runtime.build(Path(str(tmp_path / "run_config.json")), tmp_path / "runs")
    restored = terminal_api.runtime.session
    restored.observe(terminal_api.runtime.feed.quote(), force=True)

    assert restored.engine.account.wallet_balance == D(before["account"]["wallet_balance"])
    assert restored.engine.account.reset_count == 1
    assert restored.engine.account.realized_pnl == D(before["account"]["realized_pnl"])
    assert restored.recovery is not None and restored.recovery.restored is True
