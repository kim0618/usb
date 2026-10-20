"""E's screens once the common producer runs E's official paper on the V2 book (first session
2026-10-19): the headline, the trades and the equity curve are the V2 official book only, the status
is the producer's session record, and the V1 / V0 books stay readable as history, never summed in."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, create_db_engine, get_db
from app.main import create_app
from app.services.simulation_runtime import clear_active_sim_broker
from app.strategy_e_max_rt import readback as RB

from tests.strategy_e_max.test_strategies_api import E, client, e_runtime, write_book, write_session  # noqa: F401

SESSION = "2026-10-19"
#: The 2026-10-19 fills as the V2 book recorded them (symbol, qty, entry, entry cost, exit).
FILLS = (("SLGN", "164", "38.8", "6.3632", "38.94"), ("CLF", "470", "14.135", "6.64345", "13.7555"),
         ("GLBE", "162", "40.95", "6.6339", "39.83"))


def _v2_official(run: Path) -> None:
    root = run / "paper_state_v2" / RB.OFFICIAL / E
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    row = {"equity_after": "9643.51445", "phase": "COMPLETE", "sim_cost": "19.64055",
           "sim_gross_pnl": "-336.8450", "sim_realized_pnl": "-356.48555"}
    (root / "book.json").write_text(json.dumps({
        "strategy_id": E, "initial_equity": "10000", "equity": "9643.51445", "realized_pnl": "-356.48555",
        "execution_version": "e_cost_v2_exact_0934_close", "accounting_version": "V2",
        "cost_contract_version": "STRATEGY_E_COST_V1:COST_10BP", "sessions": {SESSION: row}}))
    entries = {sym: {"status": "FILLED", "intent": {"quantity": qty, "notional": "0"}, "raw_market_price": px,
                     "fill_price": px, "fill_cost": cost, "commission": cost, "fill_at": f"{SESSION}T09:30:00-04:00", "weight": "2/3"}
               for sym, qty, px, cost, _ in FILLS}
    exits = {sym: {"status": "FILLED", "raw_market_price": out, "fill_price": out, "fill_cost": "0.0000", "commission": "0.0000",
                   "fill_at": f"{SESSION}T09:34:00-04:00", "exit_flag": "EXACT_0934_CLOSE"}
             for sym, _, _, _, out in FILLS}
    (root / "sessions" / f"{SESSION}.json").write_text(json.dumps({
        "strategy_id": E, "session": SESSION, "phase": "COMPLETE", "accounting_version": "V2",
        "execution_version": "e_cost_v2_exact_0934_close", "equity_at_open": "10000",
        "decision": {"selected": ["SLGN", "CLF", "GLBE"], "decided_at": f"{SESSION}T09:29:36-04:00",
                     "digest": "5fc48c85"},
        "entries": entries, "exits": exits,
        "summary": {"sim_realized_pnl": "-356.48555", "equity_after": "9643.51445"}}))


def _producer(root: Path, *, shadow: dict | None, producer: dict | None) -> None:
    day = root / SESSION
    day.mkdir(parents=True, exist_ok=True)
    (day / "producer.lock").touch()
    if shadow is not None:
        (day / "e_shadow.json").write_text(json.dumps(shadow))
    if producer is not None:
        (day / "producer.json").write_text(json.dumps(producer))


def _record(**over) -> dict:
    return {"session": SESSION, "strategy_id": E, "canonical_universe": 2551, "rvol_ready": 820, "rvol_missing": 0,
            "stale": 1, "h5_true": 22, "selected": ["SLGN", "CLF", "GLBE"],
            "paper_evidence_status": RB.OFFICIAL, "decision_digest": "5fc48c85", "runtime": "AE_COMMON_PRODUCER_V1",
            "execution_gate": {"authorized": True, "status": "AUTHORIZED", "reasons": []}} | over


PRODUCER = {"status": "COMPLETE", "e_paper": "ON_OFFICIAL_V2", "real_order_requests": 0,
            "e_finalization": {"T0": f"{SESSION}T09:25:01.226580-04:00", "T1": f"{SESSION}T09:29:34.596283-04:00",
                               "margin_to_0930_s": 25.4},
            "seal_0915": {"status": "SEALED"}, "finished_at": f"{SESSION}T09:36:12-04:00"}


@pytest.fixture
def producer_run(tmp_path, monkeypatch, e_runtime):
    root = tmp_path / "producer"
    root.mkdir()
    monkeypatch.setenv(RB.PRODUCER_RUN_ENV, str(root))
    run = e_runtime["run"]
    # the V1 period as production holds it: 25 trades, +190.39, last E-runner session 10/16
    write_book(run, RB.OFFICIAL, "2026-10-12", equity="10190.38592")
    write_book(run, RB.PROVISIONAL, "2026-10-16", equity="10000")
    write_session(run, "2026-10-16", paper_evidence_status=RB.PROVISIONAL)
    _v2_official(run)
    (run / RB.RUNTIME_MODE_FILE).write_text(json.dumps({"mode": RB.MODE_OFFICIAL, "since": "2026-10-19"}))
    return root


def test_without_the_producer_env_the_e_run_root_is_the_only_source(e_runtime, monkeypatch) -> None:
    monkeypatch.delenv(RB.PRODUCER_RUN_ENV, raising=False)
    write_session(e_runtime["run"], "2026-10-16")
    assert RB.producer_session_dirs() == [] and RB.session_record()["session"] == "2026-10-16"


def test_the_producer_record_is_the_newest_session(producer_run) -> None:
    _producer(producer_run, shadow={"status": "DECIDED", "session_record": _record()}, producer=PRODUCER)
    record = RB.session_record()
    assert record["session"] == SESSION and record["_source"] == "AE_COMMON_PRODUCER"
    assert record["producer"]["status"] == "COMPLETE" and record["producer"]["seal_0915"] == "SEALED"
    assert RB.session_record("2026-10-16")["session"] == "2026-10-16"      # older E-runner records stay


def test_active_accounting_needs_the_official_mode_and_the_v2_book(producer_run, e_runtime) -> None:
    assert RB.active_accounting() == RB.V2
    (e_runtime["run"] / RB.RUNTIME_MODE_FILE).write_text(json.dumps({"mode": RB.MODE_PAUSED}))
    assert RB.active_accounting() == RB.CURRENT


def test_account_headline_is_the_v2_official_book(client, producer_run) -> None:
    _producer(producer_run, shadow={"status": "DECIDED", "session_record": _record()}, producer=PRODUCER)
    account = client.get(f"/api/v1/strategies/{E}/account").json()
    assert (account["initial_equity"], account["current_equity"], account["total_pnl"]) == (
        "10000", "9643.51445", "-356.48555")
    assert account["today_pnl"] == "-356.48555" and account["closed_trades_today"] == 3
    assert account["evidence_status"] == RB.OFFICIAL and account["accounting_version"] == "V2"
    # the screens look the book up under evidence_status: it must be the V2 one, settled
    assert account["books"][RB.OFFICIAL]["equity"] == "9643.51445" and account["books"][RB.OFFICIAL]["sessions"] == 1
    assert account["books"]["V1_OFFICIAL_KIWOOM_PAPER"]["equity"] == "10190.38592"      # history, kept


def test_trades_are_the_v2_book_and_history_is_opt_in(client, producer_run) -> None:
    rows = client.get(f"/api/v1/strategies/{E}/trades").json()
    assert sorted(r["symbol"] for r in rows) == ["CLF", "GLBE", "SLGN"]
    assert {r["accounting_version"] for r in rows} == {"V2"} and {r["session"] for r in rows} == {SESSION}
    history = client.get(f"/api/v1/strategies/{E}/trades?history=true").json()
    assert {r["accounting_version"] for r in history} >= {"V2"} and len(history) >= len(rows)


def test_equity_official_curve_is_v2_and_v1_is_kept_apart(client, producer_run) -> None:
    body = client.get(f"/api/v1/strategies/{E}/equity").json()
    assert body["current_book"] == RB.OFFICIAL and body["baseline"] == "10000"
    assert body["points"] == [{"date": SESSION, "equity": "9643.51445"}]
    assert body["books"]["V1_" + RB.OFFICIAL]["points"][-1]["equity"] == "10190.38592"


def test_status_reads_the_producer_session(client, producer_run) -> None:
    _producer(producer_run, shadow={"status": "DECIDED", "session_record": _record()}, producer=PRODUCER)
    status = client.get(f"/api/v1/strategies/{E}/status").json()
    assert status["session"] == SESSION and status["evidence_status"] == RB.OFFICIAL
    assert status["detail"]["selected"] == ["SLGN", "CLF", "GLBE"] and status["detail"]["rvol_ready"] == 820
    assert status["detail"]["record_source"] == "AE_COMMON_PRODUCER"
    assert status["detail"]["producer"]["T1"].startswith(f"{SESSION}T09:29:34")


def test_a_gated_producer_session_shows_its_reason(client, producer_run) -> None:
    gate = {"authorized": False, "status": "DECISION_INCOMPLETE_UNIVERSE", "reasons": ["DECISION_INCOMPLETE_UNIVERSE"]}
    _producer(producer_run, shadow={"status": "DECIDED", "session_record": _record(execution_gate=gate, selected=[])},
              producer=PRODUCER)
    status = client.get(f"/api/v1/strategies/{E}/status").json()
    assert status["runtime_status"] == "DECISION_INCOMPLETE_UNIVERSE" and status["operational_status"] == "DATA_ERROR"


def test_a_producer_without_a_sealed_decision_is_no_decision_not_the_old_session(client, producer_run) -> None:
    _producer(producer_run, shadow={"status": "NO_DECISION", "reason": "UNIVERSE_NOT_AVAILABLE", "decision": None},
              producer=PRODUCER | {"status": "COMPLETE"})
    record = RB.session_record()
    assert record["session"] == SESSION and record["status"] == "NO_DECISION"
    assert record["reason"] == "UNIVERSE_NOT_AVAILABLE"


def test_a_running_producer_morning_is_reported_as_running(producer_run) -> None:
    _producer(producer_run, shadow=None, producer=None)
    record = RB.session_record()
    assert record["session"] == SESSION and record["status"] == "PRODUCER_RUNNING"


@pytest.fixture
def db_client(tmp_path):
    """The cards route also reads A's tables: an empty schema of its own, not the repo's database."""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'cards.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    clear_active_sim_broker()
    app = create_app()

    def override():
        with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_card_counts_the_v2_book_only(db_client, producer_run) -> None:
    _producer(producer_run, shadow={"status": "DECIDED", "session_record": _record()}, producer=PRODUCER)
    card = next(c for c in db_client.get("/api/v1/strategies/cards").json() if c["strategy_id"] == E)
    assert card["equity"] == "9643.51445" and card["initial_equity"] == "10000"
    assert card["official"]["trades"] == 3 and card["trades"] == 3
    assert card["official"]["accounting_versions"] == ["V2"]
    assert card["dashboard"]["last_run_at"] is not None
    assert card["last_trade_at"].startswith(SESSION)


def test_performance_e_sessions_are_the_producer_mornings(db_client, producer_run, monkeypatch) -> None:
    from app.strategies import official as OFF
    monkeypatch.setattr(OFF, "state", lambda: {"status": "STARTED", "official_paper_start": "2026-09-28",
                                              "paper_evaluation_version": "AE_PAPER_V1", "accounting_version": "V1"})
    _producer(producer_run, shadow={"status": "DECIDED", "session_record": _record()}, producer=PRODUCER)
    body = db_client.get("/api/v1/strategies/performance").json()
    assert body["e_sessions"] == [{"session": SESSION, "phase": "COMPLETE", "error": False}]
    assert body["books"][E] == "paper_state_v2/OFFICIAL_KIWOOM_PAPER"
    assert body["strategies"][E]["official"]["accounting_version"] == "V2"
