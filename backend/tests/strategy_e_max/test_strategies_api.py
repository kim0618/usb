"""The multi-strategy read API: registry, A regression, E file adapter, isolation and empty states."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.strategies import registry as REG
from app.strategy_e_max_rt import readback as RB

ET = ZoneInfo("America/New_York")
E = REG.STRATEGY_E_MAX_V1
A = REG.STRATEGY_A


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def e_runtime(tmp_path, monkeypatch):
    """A fake E runtime tree; every accessor reads these paths through the environment."""
    run = tmp_path / "run"
    rvol = tmp_path / "rvol"
    universe = tmp_path / "universe"
    for path in (run, rvol, universe):
        path.mkdir(parents=True)
    monkeypatch.setenv(RB.PAPER_RUN_ENV, str(run))
    monkeypatch.setenv(RB.RVOL_ENV, str(rvol))
    monkeypatch.setenv(RB.UNIVERSE_ENV, str(universe))
    return {"run": run, "rvol": rvol, "universe": universe}


def write_session(run: Path, session: str, **over) -> None:
    body = {"session": session, "strategy_id": E, "canonical_universe": 2561, "feature_complete": 2309,
            "sparse_no_premarket": 251, "market_data_unavailable": 1, "stale": 0,
            "rvol_history_insufficient": 826, "h5_true": 0, "h5_false": 27, "h5_unknown": 827,
            "breadth_denominator_eligible_rows": 854, "rvol_ready": 28, "rvol_missing": 826,
            "rvol_threshold_frozen": 3.0, "rvol_window": 20, "rvol_minimum": 5, "selected": [],
            "paper_evidence_status": RB.PROVISIONAL, "bootstrap_complete": False,
            "decision_digest": "d" * 64} | over
    (run / session).mkdir(parents=True, exist_ok=True)
    (run / session / "session_record.json").write_text(json.dumps(body), encoding="utf-8")


def write_book(run: Path, status: str, session: str, *, equity="10120.00", entries=None, exits=None) -> None:
    root = run / "paper_state_v1" / status / E
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    (root / "book.json").write_text(json.dumps({
        "strategy_id": E, "initial_equity": "10000", "equity": equity, "realized_pnl": "120.00",
        "sessions": {session: {"sim_realized_pnl": "120.00", "equity_after": equity, "phase": "COMPLETE"}},
        "live_margin_approved": False}), encoding="utf-8")
    (root / "sessions" / f"{session}.json").write_text(json.dumps({
        "strategy_id": E, "session": session, "phase": "COMPLETE",
        "entries": entries if entries is not None else {},
        "exits": exits if exits is not None else {},
        "summary": {"sim_realized_pnl": "120.00", "equity_after": equity}}), encoding="utf-8")


# -- registry --------------------------------------------------------------------------------------

def test_registry_lists_a_and_e_and_keeps_b_inactive(client) -> None:
    rows = client.get("/api/v1/strategies").json()
    by_id = {row["strategy_id"]: row for row in rows}
    assert {A, E} <= set(by_id)
    assert by_id[A]["enabled"] and by_id[E]["enabled"]
    assert by_id[REG.STRATEGY_B]["enabled"] is False              # kept, not deleted
    for row in rows:
        assert {"display_name", "version", "mode", "market_data_source", "runtime_status"} <= set(row)


def test_api_schema_is_the_same_six_questions_for_every_strategy(client) -> None:
    for strategy_id in (A, E):
        status = client.get(f"/api/v1/strategies/{strategy_id}/status").json()
        account = client.get(f"/api/v1/strategies/{strategy_id}/account").json()
        assert {"strategy_id", "runtime_status", "paper_status", "evidence_status", "detail"} <= set(status)
        assert {"currency", "initial_equity", "current_equity", "today_pnl", "total_pnl",
                "open_positions", "closed_trades_today", "source"} <= set(account)
        assert isinstance(client.get(f"/api/v1/strategies/{strategy_id}/positions").json(), list)
        assert isinstance(client.get(f"/api/v1/strategies/{strategy_id}/trades").json(), list)
        assert "points" in client.get(f"/api/v1/strategies/{strategy_id}/equity").json()


def test_unknown_strategy_is_answered_not_crashed(client) -> None:
    body = client.get("/api/v1/strategies/NOPE/status").json()
    assert body["available"] is False and body["state"] == "UNKNOWN_STRATEGY"


# -- Strategy A regression --------------------------------------------------------------------------

def test_strategy_a_endpoints_are_untouched(client) -> None:
    for path in ("/api/v1/dashboard", "/api/v1/trading", "/api/v1/runtime", "/api/v1/trading/daily-performance"):
        assert client.get(path).status_code == 200, path
    trading = client.get("/api/v1/trading").json()
    assert {"broker_mode", "availability", "strategy_states"} <= set(trading)


def test_strategy_a_account_reads_the_paper_db_not_e_files(client, e_runtime) -> None:
    write_session(e_runtime["run"], "2026-09-23")
    account = client.get(f"/api/v1/strategies/{A}/account").json()
    assert account["source"] == "PAPER_DB" and account["evidence_status"] is None


# -- Strategy E file adapter -------------------------------------------------------------------------

def test_e_status_without_any_runtime_file_is_graceful(client, e_runtime) -> None:
    body = client.get(f"/api/v1/strategies/{E}/status").json()
    assert body["runtime_status"] == "NO_SESSION_YET" and body["paper_status"] == "NO_SESSION_YET"
    assert body["detail"]["bootstrap"]["available"] is False
    assert body["detail"]["universe"]["status"] == "UNIVERSE_NOT_AVAILABLE"
    assert client.get(f"/api/v1/strategies/{E}/positions").json() == []


def test_e_status_reports_the_session_record_counts(client, e_runtime) -> None:
    write_session(e_runtime["run"], "2026-09-23")
    detail = client.get(f"/api/v1/strategies/{E}/status").json()["detail"]
    assert detail["canonical_universe"] == 2561 and detail["rvol_ready"] == 28
    assert detail["rvol_missing"] == 826 and detail["market_data_unavailable"] == 1
    assert detail["h5_unknown"] == 827 and detail["rvol_threshold"] == 3.0
    assert detail["market_data_source"] == "KIWOOM"


def test_provisional_and_official_books_are_reported_apart(client, e_runtime) -> None:
    run = e_runtime["run"]
    write_session(run, "2026-09-23")
    write_book(run, RB.PROVISIONAL, "2026-09-23", equity="10120.00")
    account = client.get(f"/api/v1/strategies/{E}/account").json()
    assert account["evidence_status"] == RB.PROVISIONAL
    assert account["books"][RB.PROVISIONAL]["sessions"] == 1
    assert account["books"][RB.OFFICIAL]["present"] is False
    equity = client.get(f"/api/v1/strategies/{E}/equity").json()
    assert equity["current_book"] == RB.PROVISIONAL
    assert equity["books"][RB.OFFICIAL]["points"] == []
    assert len(equity["points"]) == 1                              # only the current book's own history


def test_official_grade_switches_the_book_without_carrying_provisional_over(client, e_runtime) -> None:
    run = e_runtime["run"]
    write_session(run, "2026-09-23")
    write_book(run, RB.PROVISIONAL, "2026-09-23", equity="10120.00")
    write_session(run, "2026-09-24", paper_evidence_status=RB.OFFICIAL, bootstrap_complete=True,
                  rvol_missing=0, rvol_ready=2561)
    write_book(run, RB.OFFICIAL, "2026-09-24", equity="10050.00")
    account = client.get(f"/api/v1/strategies/{E}/account").json()
    assert account["evidence_status"] == RB.OFFICIAL
    assert account["current_equity"] == "10050.00"                 # not 10120 + anything
    equity = client.get(f"/api/v1/strategies/{E}/equity").json()
    assert [p["date"] for p in equity["points"]] == ["2026-09-24"]
    assert [p["date"] for p in equity["books"][RB.PROVISIONAL]["points"]] == ["2026-09-23"]


def test_e_empty_state_is_a_reason_not_a_zero(client, e_runtime) -> None:
    write_session(e_runtime["run"], "2026-09-23")
    account = client.get(f"/api/v1/strategies/{E}/account").json()
    assert account["current_equity"] is None and account["today_pnl"] is None
    assert "부트스트랩" in account["empty_reason"]


def test_same_symbol_is_attributed_to_each_strategy(client, e_runtime) -> None:
    run = e_runtime["run"]
    write_session(run, "2026-09-23")
    write_book(run, RB.PROVISIONAL, "2026-09-23", entries={"NVDA": {
        "status": "FILLED", "fill_price": "181.20", "fill_at": "2026-09-23T09:30:00-04:00",
        "intent": {"quantity": "4", "notional": "724.80"}}})
    positions = client.get(f"/api/v1/strategies/{E}/positions").json()
    assert [p["symbol"] for p in positions] == ["NVDA"]
    assert positions[0]["strategy_id"] == E and positions[0]["evidence_status"] == RB.PROVISIONAL
    a_positions = client.get(f"/api/v1/strategies/{A}/positions").json()
    assert all(p["strategy_id"] == A for p in a_positions)         # never mixed into E's list


def test_bootstrap_progress_comes_from_the_collector_report(client, e_runtime) -> None:
    (e_runtime["rvol"] / "passes").mkdir()
    (e_runtime["rvol"] / "passes" / "coverage_latest.json").write_text(json.dumps({
        "symbols": 2561, "window": 20, "minimum": 5, "fully_ready": 28, "partially_ready": 0,
        "zero_history": 2533, "history_exhausted": 0, "at": "2026-09-23T02:15:45+00:00"}), encoding="utf-8")
    bootstrap = client.get(f"/api/v1/strategies/{E}/status").json()["detail"]["bootstrap"]
    assert bootstrap["status"] == "RUNNING" and bootstrap["ready"] == 28 and bootstrap["symbols"] == 2561
    assert bootstrap["percent"] == 1.1
    assert bootstrap["estimated_completion"] is None               # the UI must not invent one


def test_stale_universe_is_reported_as_no_decision(client, e_runtime) -> None:
    from app.market.calendar import MarketCalendar
    today = datetime.now(timezone.utc).astimezone(ET).date()
    stale = MarketCalendar("America/New_York").previous_trading_day(today)
    (e_runtime["universe"] / f"universe_{stale.isoformat()}.json").write_text(json.dumps({
        "format": "e-canonical-universe-v2", "target_session": stale.isoformat(),
        "asof_session": "2026-01-02", "symbols": ["AAPL"], "symbol_count": 1, "source": "MASSIVE",
        "digest": "x" * 64}), encoding="utf-8")
    universe = client.get(f"/api/v1/strategies/{E}/status").json()["detail"]["universe"]
    assert universe["status"] == "UNIVERSE_NOT_AVAILABLE"
    assert "not" in universe["identity"] or "D-1" in universe["identity"]


def test_a_and_e_accounts_are_never_summed(client, e_runtime) -> None:
    write_session(e_runtime["run"], "2026-09-23")
    write_book(e_runtime["run"], RB.PROVISIONAL, "2026-09-23")
    a = client.get(f"/api/v1/strategies/{A}/account").json()
    e = client.get(f"/api/v1/strategies/{E}/account").json()
    assert a["source"] != e["source"]
    assert a["strategy_id"] == A and e["strategy_id"] == E
    assert "합산하지 않는다" in e["separate_books"]


def test_bootstrap_from_the_store_alone_never_claims_completion(client, e_runtime) -> None:
    """Before the first pass report exists the store knows the walked symbols, not the universe."""
    import sqlite3
    store = e_runtime["rvol"] / "kiwoom_premarket.sqlite3"
    connection = sqlite3.connect(store)
    connection.executescript(
        "CREATE TABLE kiwoom_collection_state (symbol TEXT PRIMARY KEY, staged_count INTEGER,"
        " oldest_session TEXT, history_exhausted INTEGER, last_pass_at TEXT, last_error TEXT);"
        "INSERT INTO kiwoom_collection_state VALUES ('AAPL',20,'2026-08-24',0,'x',NULL),"
        " ('MSFT',20,'2026-08-24',0,'x',NULL);")
    connection.commit()
    connection.close()
    (e_runtime["universe"] / "universe_2026-09-23.json").write_text(json.dumps({
        "format": "e-canonical-universe-v2", "target_session": "2026-09-23", "asof_session": "2026-09-22",
        "symbols": ["AAPL"], "symbol_count": 2561, "source": "MASSIVE"}), encoding="utf-8")
    bootstrap = client.get(f"/api/v1/strategies/{E}/status").json()["detail"]["bootstrap"]
    assert bootstrap["status"] == "RUNNING"                    # 2 walked symbols are not 2,561
    assert bootstrap["symbols"] == 2561 and bootstrap["ready"] == 2
    assert bootstrap["source"] == "store"
