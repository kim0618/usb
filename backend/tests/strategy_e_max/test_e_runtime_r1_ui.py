"""E-RUNTIME-R1 screens: a paused or validating runtime never reads COMPLETE, the headline is the
active official book (never the PROVISIONAL one), the V1 period carries its labels, and the RVOL
bootstrap reads COMPLETE / COMPLETE_WITH_EXCEPTIONS / INCOMPLETE instead of an endless RUNNING."""

from __future__ import annotations

import json
from pathlib import Path

from app.strategy_e_max_rt import readback as RB

from tests.strategy_e_max.test_strategies_api import E, client, e_runtime, write_book, write_session  # noqa: F401


def _mode(run: Path, mode: str) -> None:
    (run / RB.RUNTIME_MODE_FILE).write_text(json.dumps({"mode": mode, "since": "2026-10-16"}))


def _v2_book(run: Path, status: str, session: str, equity: str = "10050") -> None:
    root = run / "paper_state_v2" / status / E
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    (root / "book.json").write_text(json.dumps({
        "strategy_id": E, "initial_equity": "10000", "equity": equity, "realized_pnl": "50",
        "execution_version": "e_cost_v2_exact_0934_close", "accounting_version": "V2",
        "sessions": {session: {"sim_realized_pnl": "50", "equity_after": equity, "phase": "COMPLETE"}}}))
    (root / "sessions" / f"{session}.json").write_text(json.dumps({
        "strategy_id": E, "session": session, "phase": "COMPLETE", "entries": {}, "exits": {},
        "summary": {"sim_realized_pnl": "50", "equity_after": equity}}))


def _broken_v1(run: Path) -> None:
    write_book(run, RB.OFFICIAL, "2026-10-12", equity="10190.38592")
    write_book(run, RB.PROVISIONAL, "2026-10-15", equity="10000")
    write_session(run, "2026-10-15", paper_evidence_status=RB.PROVISIONAL, stale=2549)


def test_without_the_mode_file_nothing_changes(client, e_runtime) -> None:
    _broken_v1(e_runtime["run"])
    account = client.get(f"/api/v1/strategies/{E}/account").json()
    assert account["current_equity"] == "10000"                 # the pre-R1 behaviour, unchanged


def test_paused_runtime_is_never_complete_and_headline_is_not_provisional(client, e_runtime) -> None:
    _broken_v1(e_runtime["run"])
    _mode(e_runtime["run"], RB.MODE_PAUSED)
    status = client.get(f"/api/v1/strategies/{E}/status").json()
    assert status["runtime_status"] == "RUNTIME_PAUSED_FOR_REPAIR" and status["operational_status"] == "PAUSED"
    account = client.get(f"/api/v1/strategies/{E}/account").json()
    assert account["current_equity"] == "10190.38592"            # V1 official, no longer hidden
    assert account["evidence_status"] == "LEGACY_RUNTIME_V1"
    assert "LEGACY" in account["headline_book"]
    assert account["books"]["V1_PROVISIONAL_RVOL_BOOTSTRAP"]["label"] == "INVALID_RUNTIME_EVIDENCE"


def test_shadow_validation_is_labelled_and_not_performance(client, e_runtime) -> None:
    _broken_v1(e_runtime["run"])
    _v2_book(e_runtime["run"], RB.SHADOW, "2026-10-19")
    _mode(e_runtime["run"], RB.MODE_SHADOW)
    status = client.get(f"/api/v1/strategies/{E}/status").json()
    assert status["runtime_status"] == "RUNTIME_SHADOW_VALIDATION"
    account = client.get(f"/api/v1/strategies/{E}/account").json()
    assert account["current_equity"] == "10190.38592"            # shadow equity is never the headline
    assert account["books"]["V2_SHADOW_VALIDATION"]["label"] == "SHADOW_NOT_PERFORMANCE"


def test_official_v2_book_is_the_headline_once_running(client, e_runtime) -> None:
    _broken_v1(e_runtime["run"])
    _v2_book(e_runtime["run"], RB.OFFICIAL, "2026-10-20", equity="10050")
    write_session(e_runtime["run"], "2026-10-20", paper_evidence_status=RB.OFFICIAL, stale=1,
                  execution_gate={"authorized": True, "status": "AUTHORIZED", "reasons": []})
    _mode(e_runtime["run"], RB.MODE_OFFICIAL)
    status = client.get(f"/api/v1/strategies/{E}/status").json()
    assert status["runtime_status"] == "OFFICIAL_PAPER_RUNNING"
    account = client.get(f"/api/v1/strategies/{E}/account").json()
    assert account["current_equity"] == "10050" and account["evidence_status"] == RB.OFFICIAL
    assert account["initial_equity"] == "10000"                  # V1's 10190.39 is not carried over


def test_a_blocked_official_session_shows_its_reason_not_complete(client, e_runtime) -> None:
    _v2_book(e_runtime["run"], RB.OFFICIAL, "2026-10-20")
    write_session(e_runtime["run"], "2026-10-21", paper_evidence_status=RB.OFFICIAL, stale=900,
                  execution_gate={"authorized": False, "status": "DECISION_INCOMPLETE_UNIVERSE",
                                  "reasons": ["DECISION_INCOMPLETE_UNIVERSE"]})
    _mode(e_runtime["run"], RB.MODE_OFFICIAL)
    status = client.get(f"/api/v1/strategies/{E}/status").json()
    assert status["runtime_status"] == "DECISION_INCOMPLETE_UNIVERSE"
    assert status["operational_status"] == "DATA_ERROR"
    assert status["detail"]["no_decision_reason"] == "DECISION_INCOMPLETE_UNIVERSE"


def test_v1_trades_carry_their_evaluation_labels(client, e_runtime) -> None:
    run = e_runtime["run"]
    trade_entry = {"S": {"status": "FILLED", "intent": {"quantity": "10", "notional": "100"},
                         "raw_market_price": "10", "fill_price": "10", "fill_cost": "0.1",
                         "fill_at": "2026-09-29T09:30:00-04:00", "weight": "2/3"}}
    trade_exit = {"S": {"status": "FILLED", "raw_market_price": "11", "fill_price": "11", "fill_cost": "0",
                        "fill_at": "2026-09-29T09:35:00-04:00"}}
    write_book(run, RB.OFFICIAL, "2026-09-29", entries=trade_entry, exits=trade_exit)
    _mode(run, RB.MODE_PAUSED)
    rows = client.get(f"/api/v1/strategies/{E}/trades").json()
    labelled = [r for r in rows if r.get("session") == "2026-09-29"]
    assert labelled and all(r["evaluation_label"] == "LEGACY_RUNTIME_EXIT_PROXY" for r in labelled)
    assert RB.evidence_label("2026-10-07") == "INVALID_RUNTIME_EVIDENCE"
    assert RB.evidence_label("2026-10-20", RB.V2) is None


def test_bootstrap_reads_complete_states_not_an_endless_running(e_runtime) -> None:
    passes = e_runtime["rvol"] / "passes"
    passes.mkdir()
    _mode(e_runtime["run"], RB.MODE_PAUSED)

    def cov(ready, exhausted):
        (passes / "coverage_latest.json").write_text(json.dumps(
            {"symbols": 2552, "fully_ready": ready, "history_exhausted": exhausted, "window": 20, "minimum": 5}))
        return RB.bootstrap()

    assert cov(2552, 0)["status"] == "COMPLETE"
    assert cov(2545, 7)["status"] == "COMPLETE_WITH_EXCEPTIONS"
    body = cov(2544, 7)
    assert body["status"] == "INCOMPLETE" and body["pending"] == 1
