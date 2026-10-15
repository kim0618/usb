"""C1 is gone from the operating terminal (CRYPTO MINIMAL RUNTIME CLEANUP V2).

The research package `app.crypto.c1` stays in the tree for replay. What was removed is every path
from it, and from the old PAPER C1 AUTO flag, into the running terminal. These tests pin what the
removal promised: no C1 route, no C1 entry, and an existing position - including one an earlier
AUTO opened, under a run whose `c1_auto_state.json` still says enabled - survives a restart with
its quantity, entry and ledger unchanged and closes by hand.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.crypto.terminal import api as terminal_api
from tests.crypto.conftest import RISK_LIMIT_PAYLOAD

BACKEND = Path(__file__).resolve().parents[2]

LEGACY_AUTO_STATE = {"active_benchmark_at": None, "active_signal_id": None,
                     "active_trade_id": None, "disabled_at": None, "enabled": True,
                     "enabled_at": 1_500, "last_seen_signal_at": None,
                     "last_seen_signal_id": None, "leverage": "10", "source": "PAPER_C1_AUTO"}


def book(ts: int, bid: str = "100000.0", ask: str = "100000.1") -> dict:
    return {"topic": "orderbook.50.BTCUSDT", "type": "snapshot", "ts": ts,
            "data": {"u": ts, "seq": ts, "b": [[bid, "10"]], "a": [[ask, "10"]]}}


def ticker(ts: int, mark: str = "100000.0") -> dict:
    return {"topic": "tickers.BTCUSDT", "type": "snapshot", "ts": ts,
            "data": {"symbol": "BTCUSDT", "markPrice": mark, "indexPrice": mark,
                     "lastPrice": mark, "fundingRate": "0.0001",
                     "nextFundingTime": str(8 * 3_600_000), "openInterest": "60000"}}


@pytest.fixture
def paper_env(tmp_path: Path, monkeypatch) -> Path:
    risk = tmp_path / "risk.json"
    risk.write_text(json.dumps(RISK_LIMIT_PAYLOAD))
    config_path = tmp_path / "run_config.json"
    config_path.write_text(json.dumps({
        "run_id": "c1-removal", "starting_capital_krw": "1000000", "fx_krw_per_usdt": "1000",
        "fx_source": "test", "fx_asof_utc": "2026-09-23T00:00:00Z", "fee_version": "test-v1",
        "fee_taker_rate": "0.0006", "fee_maker_rate": "0.0002", "fee_source": "test",
        "fee_effective_date": "2026-01-01", "slippage_model": "NONE", "slippage_bps": "0",
        "leverage": "10", "risk_limit_path": str(risk)}))
    monkeypatch.setenv(terminal_api.CONFIG_ENV, str(config_path))
    monkeypatch.setenv("CRYPTO_PAPER_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(terminal_api.BybitPublicFeed, "seed_klines", lambda self, **kw: None)
    monkeypatch.setattr(terminal_api.BybitPublicFeed, "start", lambda self: None)
    monkeypatch.setattr(terminal_api.Runtime, "start", lambda self: None)
    return tmp_path / "runs"


@contextmanager
def booted(monkeypatch, ts: int) -> Iterator[TestClient]:
    """One process lifetime: a fresh runtime over the same run root, primed with one tick."""
    monkeypatch.setattr(terminal_api, "runtime", terminal_api.Runtime())
    with TestClient(terminal_api.create_app()) as client:
        runtime = terminal_api.runtime
        runtime.feed.handle(book(ts), ts + 1)
        runtime.feed.handle(ticker(ts), ts + 1)
        if not runtime.session.is_started:
            runtime.session.start(ts - 1)
        runtime.session.observe(runtime.feed.quote(), force=True)
        yield client


def ledger_digest(run_dir: Path) -> str:
    return hashlib.sha256((run_dir / "ledger.jsonl").read_bytes()).hexdigest()


def test_no_c1_route_is_registered() -> None:
    paths = {route.path for route in terminal_api.create_app().routes}
    assert not [path for path in paths if "c1" in path.lower()]
    from app.crypto.terminal import server
    assert not [route.path for route in server.app.routes if "c1" in route.path.lower()]


def test_the_terminal_imports_nothing_from_the_c1_package_or_the_auto_controller() -> None:
    offenders = []
    for folder in ("terminal", "paper", "live", "hot_coins"):
        for path in (BACKEND / "app" / "crypto" / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in ("crypto.c1", "..c1", "c1_routes", "c1_auto", "C1AutoController"):
                if needle in text:
                    offenders.append(f"{path.relative_to(BACKEND)}: {needle}")
    assert offenders == []
    assert not (BACKEND / "app" / "crypto" / "terminal" / "c1_routes.py").exists()
    assert not (BACKEND / "app" / "crypto" / "paper" / "c1_auto.py").exists()
    assert not hasattr(terminal_api.runtime, "c1_auto")


def test_state_reports_a_manual_account_with_no_c1_fields(paper_env: Path, monkeypatch) -> None:
    with booted(monkeypatch, 1_000) as client:
        state = client.get("/api/crypto/state").json()
        assert state["paper_source"] == "PAPER_MANUAL"
        assert "c1_auto" not in state and "c1_available" not in state
        assert client.get("/api/crypto/paper/c1-auto").status_code == 404
        assert client.post("/api/crypto/paper/c1-auto", json={"enabled": True}).status_code == 404
        assert client.get("/api/crypto/c1/state").status_code == 404


def test_an_auto_opened_position_survives_a_restart_and_closes_by_hand(
        paper_env: Path, monkeypatch) -> None:
    # Lifetime 1: a position opened with the old AUTO origin, under a state file that says
    # AUTO is enabled - the shape the production run had before the removal.
    with booted(monkeypatch, 1_000) as client:
        opened = client.post("/api/crypto/order", json={
            "side": "LONG", "intent": "OPEN", "qty": "0.010", "reason": "PAPER_C1_AUTO"})
        assert opened.status_code == 200
        run_dir = terminal_api.runtime.session.run_dir
        before = client.get("/api/crypto/state").json()["account"]
        assert before["position_side"] == "LONG" and before["position_qty"] == "0.010"
    (run_dir / "c1_auto_state.json").write_text(json.dumps(LEGACY_AUTO_STATE, sort_keys=True))
    legacy_bytes = (run_dir / "c1_auto_state.json").read_bytes()
    digest = ledger_digest(run_dir)
    events_before = [json.loads(line) for line in
                     (run_dir / "ledger.jsonl").read_text().splitlines()]

    # Lifetime 2: the build without C1. Nothing in it may touch the position on its own.
    with booted(monkeypatch, 2_000) as client:
        account = client.get("/api/crypto/state").json()["account"]
        for key in ("position_side", "position_qty", "avg_entry", "leverage", "realized_pnl",
                    "wallet_balance"):
            assert account.get(key) == before.get(key), key
        assert ledger_digest(run_dir) == digest
        # The 1 Hz loop body, run by hand for a while: no entry, no exit, no new ledger line.
        runtime = terminal_api.runtime
        for ts in range(2_000, 30_000, 250):
            runtime.feed.handle(ticker(ts), ts + 1)
            runtime.session.observe(runtime.feed.quote())
        assert ledger_digest(run_dir) == digest
        assert (run_dir / "c1_auto_state.json").read_bytes() == legacy_bytes

        # Manual control is the operator's again: leverage locked only by the open position,
        # never by an AUTO flag, and the close goes through the ordinary path.
        locked = client.post("/api/crypto/leverage", json={"leverage": "5"})
        assert locked.json()["error"]["code"] == "LEVERAGE_LOCKED_WHILE_OPEN"
        closed = client.post("/api/crypto/order",
                             json={"side": "LONG", "intent": "CLOSE", "qty": "0.010"})
        assert closed.status_code == 200
        assert client.get("/api/crypto/state").json()["account"]["position_side"] is None

    events_after = [json.loads(line) for line in
                    (run_dir / "ledger.jsonl").read_text().splitlines()]
    assert events_after[:len(events_before)] == events_before
    closes = [row for row in events_after[len(events_before):]
              if row["event_type"] == "POSITION_CLOSE"]
    assert len(closes) == 1
    opens = [row for row in events_after if row["event_type"] == "POSITION_OPEN"]
    assert [row["reason"] for row in opens] == ["PAPER_C1_AUTO"]


def test_a_manual_open_is_allowed_under_a_legacy_enabled_auto_file(
        paper_env: Path, monkeypatch) -> None:
    with booted(monkeypatch, 1_000):
        run_dir = terminal_api.runtime.session.run_dir
    (run_dir / "c1_auto_state.json").write_text(json.dumps(LEGACY_AUTO_STATE))
    with booted(monkeypatch, 2_000) as client:
        opened = client.post("/api/crypto/order",
                             json={"side": "SHORT", "intent": "OPEN", "qty": "0.010"})
        assert opened.status_code == 200
        assert client.get("/api/crypto/state").json()["account"]["position_side"] == "SHORT"
