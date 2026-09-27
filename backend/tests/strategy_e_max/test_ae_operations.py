"""A/E operations layer: registry lifecycles, canonical ledger, performance, portfolio and paper gate.

The E fixture is the real 2026-09-25 OFFICIAL session (COR, MEI, MGM) with its recorded fills, so
the reconciliation test pins the exact book figure the server wrote (-123.15418280).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.broker.sim import SimBroker
from app.core.database import Base, create_db_engine, get_db
from app.main import create_app
from app.models.simulation import AccountDailyPerformanceRecord, SimulationAccountRecord, SimulationTradeRecord
from app.services.simulation_runtime import SimulationRuntimeContext, clear_active_sim_broker, set_active_runtime
from app.strategies import ledger as L
from app.strategies import official as OFF
from app.strategies import paper_gate as GATE
from app.strategies import performance as PERF
from app.strategies import registry as REG
from app.strategy_e_max_rt import readback as RB

A, E = REG.STRATEGY_A, REG.STRATEGY_E_MAX_V1
SESSION = "2026-09-25"

# (symbol, qty, entry raw, entry fill, entry cost, exit raw, exit fill, exit cost, weight rank)
REAL_0925 = (
    ("COR", "21", "308.95", "309.413425", "16.219875", "307.945", "307.4830825", "16.1671125"),
    ("MEI", "445", "14.78", "14.80217", "16.44275", "14.858", "14.835713", "16.529525"),
    ("MGM", "197", "33.88", "33.93082", "16.68590", "33.9881", "33.93711785", "16.73913925"),
)
RECORDED_0925 = "-123.15418280"


def e_state(session: str = SESSION, rows=REAL_0925, phase: str = "COMPLETE", realized: str = RECORDED_0925,
            view10: str = "0.001476747536998298852619489533") -> dict:
    entries, exits = {}, {}
    for symbol, qty, e_raw, e_fill, e_cost, x_raw, x_fill, x_cost in rows:
        entries[symbol] = {"key": f"{E}|{session}|{symbol}|ENTRY", "status": "FILLED", "weight": "2/3",
                           "intent": {"quantity": qty}, "signal_at": f"{session}T09:29:26-04:00",
                           "fill_at": f"{session}T09:30:00-04:00", "raw_market_price": e_raw,
                           "fill_price": e_fill, "fill_cost": e_cost}
        exits[symbol] = {"key": f"{E}|{session}|{symbol}|EXIT", "status": "FILLED",
                         "fill_at": f"{session}T09:35:00-04:00", "raw_market_price": x_raw,
                         "fill_price": x_fill, "fill_cost": x_cost, "exit_flag": "ON_TIME_0935_OPEN"}
    return {"strategy_id": E, "session": session, "phase": phase, "equity_at_open": "10000",
            "decision": {"digest": "5285e23d69abd1a4" + "0" * 48, "selected": ["MGM", "MEI", "COR"],
                         "decided_at": f"{session}T09:29:26-04:00"},
            "entries": entries, "exits": exits,
            "summary": {"sim_realized_pnl": realized, "equity_after": str(Decimal(10000) + Decimal(realized)),
                        "fixed_bp_session_return_views": {"net_10bp": view10}}}


def write_e(run: Path, status: str, states: list[dict], *, records: dict[str, str] | None = None) -> None:
    root = run / "paper_state" / status / E
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    sessions, equity = {}, Decimal(10000)
    for state in states:
        (root / "sessions" / f"{state['session']}.json").write_text(json.dumps(state), encoding="utf-8")
        if state["phase"] == "COMPLETE":
            equity += Decimal(state["summary"]["sim_realized_pnl"])
            sessions[state["session"]] = {"sim_realized_pnl": state["summary"]["sim_realized_pnl"],
                                          "equity_after": str(equity), "phase": "COMPLETE"}
    (root / "book.json").write_text(json.dumps({
        "strategy_id": E, "initial_equity": "10000", "equity": str(equity),
        "realized_pnl": str(equity - 10000), "sessions": sessions}), encoding="utf-8")
    for session, grade in (records or {}).items():
        (run / session).mkdir(parents=True, exist_ok=True)
        (run / session / "session_record.json").write_text(json.dumps(
            {"session": session, "strategy_id": E, "paper_evidence_status": grade}), encoding="utf-8")


@pytest.fixture
def e_runtime(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setenv(RB.PAPER_RUN_ENV, str(run))
    monkeypatch.setenv(RB.RVOL_ENV, str(tmp_path / "rvol"))
    monkeypatch.setenv(RB.UNIVERSE_ENV, str(tmp_path / "universe"))
    return run


def a_row(uid: str, symbol: str, gross: str, cost: str, net: str, *, status: str = "CLOSED",
          entry: str = "2026-09-21T13:47:00+00:00", exit_: str | None = "2026-09-21T16:36:00+00:00",
          qty: str = "1") -> SimpleNamespace:
    return SimpleNamespace(trade_uid=uid, symbol=symbol, gross_pnl=gross, total_cost=cost, net_pnl=net,
                           status=status, entry_time=datetime.fromisoformat(entry),
                           exit_time=None if exit_ is None else datetime.fromisoformat(exit_),
                           average_entry_price="100", average_exit_price=None if exit_ is None else "101",
                           total_quantity=qty, exit_reason="TRAILING_STOP" if exit_ else None)


def a_record(*args, **kwargs) -> dict:
    return L.a_trade_record(a_row(*args, **kwargs), strategy_id=A, strategy_version="V0", account_id=1)


# -- registry ------------------------------------------------------------------------------------------

def test_only_a_and_e_are_active_and_b_c_d_are_closed_research() -> None:
    assert [m.strategy_id for m in REG.enabled()] == [A, E]
    by_id = {m.strategy_id: m for m in REG.REGISTRY}
    for closed in (REG.STRATEGY_B, REG.STRATEGY_C, REG.STRATEGY_D):
        meta = by_id[closed]
        assert meta.enabled is False and meta.research_lifecycle == "CLOSED" and meta.lifecycle == "RETIRED"
        assert meta.closeout and (Path(__file__).resolve().parents[3] / meta.closeout).exists()
    for active in (A, E):
        assert by_id[active].research_lifecycle == "PASSED_TO_PAPER" and by_id[active].lifecycle == "PAPER"


def test_e_keeps_its_internal_id_and_only_the_display_name_is_e() -> None:
    meta = REG.get(E)
    assert meta.strategy_id == "STRATEGY_E_MAX_V1"            # books and runtime files are keyed by this
    assert (meta.display_name, meta.short_name, meta.variant_label) == ("Strategy E", "E", "E-MAX V1")
    assert REG.get(REG.STRATEGY_B).display_name == "Strategy B"   # historical B stays B


# -- ledger ---------------------------------------------------------------------------------------------

def test_a_record_detects_the_pre_contract_double_deduction() -> None:
    v0 = a_record("T1", "INTC", "31.4180791447855963273659535", "4.411140406682749889620954699",
                  "27.00693873810284643774499880")
    assert v0["accounting"] == L.ACCOUNTING_V0 and v0["net_pnl"] == "27.00693873810284643774499880"
    v1 = a_record("T2", "INTC", "31.418", "4.411", "29.0")      # only commission/fx charged
    assert v1["accounting"] == L.ACCOUNTING_V1
    assert v0["strategy_id"] == A and v0["book"] == "PAPER_DB" and v0["mode"] == "PAPER"
    assert v0["mfe"] is None and "mfe" in v0["missing"] and v0["holding_seconds"] == 10140


def test_open_a_trade_has_no_pnl_yet() -> None:
    row = a_record("T3", "NVDA", "0", "1.2", "-1.2", status="OPEN", exit_=None)
    assert row["net_pnl"] is None and row["gross_pnl"] is None and row["accounting"] is None


def test_e_rows_reproduce_the_real_0925_book_exactly() -> None:
    out = L.e_trade_records(e_state(), strategy_id=E, strategy_version="E-MAX V1", book=RB.OFFICIAL,
                            book_session={"sim_realized_pnl": RECORDED_0925})
    assert out["reconciliation"]["status"] == "RECONCILED"
    assert out["reconciliation"]["accounting"] == L.ACCOUNTING_V0
    total = sum(Decimal(t["net_pnl"]) for t in out["trades"])
    assert total == Decimal(RECORDED_0925)
    cor = next(t for t in out["trades"] if t["symbol"] == "COR")
    assert cor["gross_pnl"] == "-40.5371925" and cor["net_pnl"] == "-72.9241800"
    assert cor["provenance"]["decision_digest"].startswith("5285e23d")
    assert cor["provenance"]["h5_signal_id"] == "5285e23d69abd1a4:COR" and cor["provenance"]["selection_rank"] == 3
    assert cor["holding_seconds"] == 300 and cor["book"] == RB.OFFICIAL


def test_e_rows_that_do_not_reconcile_publish_no_per_trade_net() -> None:
    out = L.e_trade_records(e_state(), strategy_id=E, strategy_version="E-MAX V1", book=RB.OFFICIAL,
                            book_session={"sim_realized_pnl": "-63.88"})
    assert out["reconciliation"]["status"] == "UNRECONCILED"
    assert all(t["net_pnl"] is None for t in out["trades"])


# -- performance ----------------------------------------------------------------------------------------

def book_e(tmp_states=None) -> PERF.Book:
    out = L.e_trade_records(e_state(), strategy_id=E, strategy_version="E-MAX V1", book=RB.OFFICIAL,
                            book_session={"sim_realized_pnl": RECORDED_0925})
    return PERF.Book(E, Decimal(10000), out["trades"],
                     [PERF.DailyPoint(SESSION, Decimal(RECORDED_0925), Decimal("9876.84581720"))],
                     ["2026-09-24", SESSION])


def book_a() -> PERF.Book:
    trades = [a_record("T1", "META", "28", "4", "24"), a_record("T2", "AMD", "26", "4", "22"),
              a_record("T3", "INTC", "-10", "4", "-14")]
    daily = [PERF.DailyPoint("2026-09-24", Decimal(0), Decimal(1000)),
             PERF.DailyPoint("2026-09-25", Decimal(32), Decimal(1032))]
    return PERF.Book(A, Decimal(1000), trades, daily, ["2026-09-24", SESSION])


def test_strategy_metrics_follow_their_definitions() -> None:
    m = PERF.strategy_metrics(book_a())
    assert m["trades"] == 3 and m["net_pnl"] == "32.0000" and m["gross_pnl"] == "44.0000" and m["costs"] == "12.0000"
    assert m["win_rate"] == "0.666667" and m["pf"] == "3.285714" and m["expectancy"] == "10.6667"
    assert m["return"] == "0.032000" and m["mdd"] == "0.000000"
    assert m["avg_mfe"] is None and m["na"]["avg_mfe"] == "NOT_RECORDED_BY_LEDGER"


def test_no_sample_is_na_not_zero() -> None:
    m = PERF.strategy_metrics(PERF.Book(E, Decimal(10000), [], [], []))
    assert m["trades"] == 0 and m["net_pnl"] is None and m["pf"] is None and m["win_rate"] is None
    assert m["na"]["net_pnl"] == "NO_CLOSED_TRADE" and m["na"]["mdd"] == "NO_DAILY_SERIES"


def test_pf_without_a_losing_trade_says_why() -> None:
    book = PERF.Book(A, Decimal(1000), [a_record("T1", "META", "28", "4", "24")], [], [])
    m = PERF.strategy_metrics(book)
    assert m["pf"] is None and m["na"]["pf"] == "NO_LOSING_TRADE"


def test_combined_column_is_the_sum_of_the_two_books() -> None:
    a, e = book_a(), book_e()
    combined = PERF.combined_metrics([a, e])
    assert combined["trades"] == 6
    assert Decimal(combined["net_pnl"]) == Decimal(PERF.strategy_metrics(a)["net_pnl"]) + Decimal(
        PERF.strategy_metrics(e)["net_pnl"])
    assert combined["initial_equity"] == "11000.0000"
    assert combined["accounting_mix"] == [L.ACCOUNTING_V0]


def test_same_symbol_in_both_books_stays_attributed_and_the_portfolio_adds_it() -> None:
    a = PERF.Book(A, Decimal(1000), [], [], [], [{"symbol": "NVDA", "quantity": "100", "cost_basis": "18000"}])
    e = PERF.Book(E, Decimal(1000), [], [], [], [{"symbol": "NVDA", "quantity": "50", "cost_basis": "9000"}])
    out = PERF.exposure([a, e])
    nvda = out["per_symbol"][0]
    assert nvda["quantity"] == "150" and nvda["cost_basis"] == "27000.0000"
    assert nvda["by_strategy"] == {A: {"quantity": "100", "cost_basis": "18000.0000"},
                                   E: {"quantity": "50", "cost_basis": "9000.0000"}}
    assert out["per_strategy"][A]["open_positions"] == 1 and out["per_strategy"][E]["open_positions"] == 1


def test_portfolio_baseline_blends_only_the_common_window() -> None:
    a, e = book_a(), book_e()
    a.daily.insert(0, PERF.DailyPoint("2026-09-23", Decimal(0), Decimal(1000)))
    a.operating_sessions.insert(0, "2026-09-23")
    out = PERF.portfolio_baseline(a, e)
    assert out["mode"] == "SIMULATION_BASELINE_ONLY" and out["allocation_rule"] == "NOT_IN_USE"
    assert out["window"] == {"start": "2026-09-24", "end": SESSION} and out["days"] == 2
    day = out["equity_curve"][-1]
    expected = Decimal("0.5") * Decimal(32) / Decimal(1000) + Decimal("0.5") * Decimal(RECORDED_0925) / Decimal(10000)
    assert Decimal(day["combined"]) == round(expected, 6)
    assert out["correlation"] is None and out["na"]["correlation"].startswith("NEEDS_")
    assert out["na"]["sector_overlap"] == "SECTOR_NOT_RECORDED_IN_LEDGER"


def test_portfolio_overlaps_count_days_and_symbols() -> None:
    days = [f"2026-10-{d:02d}" for d in range(1, 8)]
    a_pnl = [10, -5, 3, -2, 4, -1, 6]
    e_pnl = [-20, -10, 30, 5, -5, -15, 25]
    def book(sid, pnl, symbol):
        equity, daily = Decimal(1000), []
        for day, value in zip(days, pnl):
            equity += value
            daily.append(PERF.DailyPoint(day, Decimal(value), equity))
        return PERF.Book(sid, Decimal(1000), [{"symbol": symbol, "session": days[0], "status": "CLOSED",
                                               "net_pnl": "1"}], daily, days)
    out = PERF.portfolio_baseline(book(A, a_pnl, "NVDA"), book(E, e_pnl, "NVDA"))
    assert out["days"] == 7 and out["correlation"] is not None
    assert out["losing_day_overlap"]["both"] == 2                # 10-02 and 10-06
    assert out["winning_day_overlap"]["both"] == 2               # 10-03 and 10-07
    assert out["symbol_overlap"]["symbols"] == ["NVDA"]
    assert out["same_session_symbol_collisions"] == ["2026-10-01:NVDA"]


# -- paper gate -----------------------------------------------------------------------------------------

def metrics(**over) -> dict:
    base = {"operating_sessions": 80, "trades": 80, "net_pnl": "100", "expectancy": "1.25", "pf": "1.5",
            "mdd": "-0.05", "accounting_mix": [L.ACCOUNTING_V1], "na": {}}
    return base | over


def test_gate_contract_is_frozen_by_checksum(monkeypatch, tmp_path) -> None:
    body = GATE.contract()
    assert body["declaration"]["contract_id"] == "AE_PAPER_EVALUATION_GATE_V1"
    tampered = tmp_path / "gate.json"
    data = json.loads(GATE.CONTRACT.read_text(encoding="utf-8"))
    data["strategies"][E]["pf_min"] = 0.9
    tampered.write_text(json.dumps(data), encoding="utf-8")
    GATE.contract.cache_clear()
    monkeypatch.setattr(GATE, "CONTRACT", tampered)
    with pytest.raises(GATE.ContractError):
        GATE.contract()
    GATE.contract.cache_clear()


def test_gate_passes_only_with_the_sample_and_every_condition() -> None:
    GATE.contract.cache_clear()
    assert GATE.evaluate(E, metrics(), error_sessions=2, divergence=Decimal("-0.001"))["verdict"] == "PASS"
    small = GATE.evaluate(E, metrics(trades=10), error_sessions=0, divergence=Decimal(0))
    assert small["verdict"] == "INCONCLUSIVE" and "SAMPLE_NOT_REACHED" in small["reasons"]
    pending = GATE.evaluate(A, metrics(), error_sessions=0, divergence=None)
    assert pending["verdict"] == "INCONCLUSIVE" and pending["pending"] == ["divergence"]


def test_v0_pnl_failure_is_not_final_but_v1_failure_is() -> None:
    losing = dict(net_pnl="-50", expectancy="-0.6", pf="0.8")
    v0 = GATE.evaluate(E, metrics(accounting_mix=[L.ACCOUNTING_V0], **losing), error_sessions=0,
                       divergence=Decimal(0))
    assert v0["verdict"] == "INCONCLUSIVE" and "ACCOUNTING_V0_OVERCHARGED" in v0["reasons"] and not v0["final"]
    v1 = GATE.evaluate(E, metrics(**losing), error_sessions=0, divergence=Decimal(0))
    assert v1["verdict"] == "FAIL" and v1["final"]


def test_mixed_accounting_is_never_judged() -> None:
    out = GATE.evaluate(A, metrics(accounting_mix=[L.ACCOUNTING_V0, L.ACCOUNTING_V1]), error_sessions=0,
                        divergence=Decimal(0))
    assert out["verdict"] == "INCONCLUSIVE" and "ACCOUNTING_MIXED" in out["reasons"]


def test_an_unreliable_runtime_fails_before_the_sample() -> None:
    out = GATE.evaluate(E, metrics(operating_sessions=25, trades=10, accounting_mix=[L.ACCOUNTING_V0]),
                        error_sessions=8, divergence=None)
    assert out["verdict"] == "FAIL" and "operational_error_rate" in out["failed"] and out["final"]


# -- API ------------------------------------------------------------------------------------------------
# Two layers, as on the server after deployment: LEGACY V0 rows (A's 09-25 trades, E's real 09-25
# session under paper_state/) and OFFICIAL V1 rows from the official start (2026-09-29).

START = "2026-09-29"
V1_COR = (("COR", "21", "308.95", "308.95", "6.487950", "307.945", "307.945", "0"),)
V1_COR_NET = Decimal(21) * (Decimal("307.945") - Decimal("308.95")) - Decimal("6.487950")     # -27.59295


def e_state_v1(session: str) -> dict:
    state = e_state(session, rows=V1_COR, realized=str(V1_COR_NET),
                    view10=str(Decimal(2) * (Decimal("307.945") / Decimal("308.95") - 1 - Decimal("0.001"))))
    for bucket in ("entries", "exits"):
        for leg in state[bucket].values():
            leg["commission"] = leg["fill_cost"]
    state |= {"accounting_version": "V1", "cost_contract_version": "STRATEGY_E_COST_V1:COST_10BP",
              "execution_cost_bp": "10", "execution_version": "e_cost_v1_round_trip"}
    return state


def a_trade(account_id, uid, symbol, day, gross, cost, net, now):
    entry = datetime.fromisoformat(f"{day}T13:47:00+00:00")
    return SimulationTradeRecord(
        account_id=account_id, trade_uid=uid, symbol=symbol, entry_time=entry,
        exit_time=entry.replace(hour=16, minute=36), initial_quantity=Decimal(1), total_quantity=Decimal(1),
        average_entry_price=Decimal(100), average_exit_price=Decimal(128), gross_pnl=Decimal(gross),
        net_pnl=Decimal(net), planned_initial_risk=Decimal(5), gross_r=Decimal(0), net_r=Decimal(0),
        total_cost=Decimal(cost), exit_reason="TRAILING_STOP", status="CLOSED", created_at=now, updated_at=now)


@pytest.fixture
def api(tmp_path, e_runtime, monkeypatch):
    monkeypatch.setenv(OFF.START_ENV, START)
    engine = create_db_engine(f"sqlite:///{tmp_path / 'api.sqlite3'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 9, 29, 20, tzinfo=timezone.utc)
    with sessions() as db:
        account = SimulationAccountRecord(account_key="operator", broker_type="SIM", base_currency="USD",
                                          initial_cash=Decimal(1000), cash=Decimal("1048"), created_at=now,
                                          updated_at=now)
        db.add(account)
        db.flush()
        # legacy V0: net = gross - total_cost
        db.add(a_trade(account.id, "T-META", "META", "2026-09-25", "28", "4", "24", now))
        db.add(a_trade(account.id, "T-AMD", "AMD", "2026-09-25", "4", "4", "0", now))
        # official V1: net = gross - commission only (10 of total_cost 25)
        db.add(a_trade(account.id, "T-COR", "COR", START, "28", "10", "24", now))
        for day, opening, pnl, equity in (("2026-09-25", "1000", "24", "1024"), (START, "1024", "24", "1048")):
            db.add(AccountDailyPerformanceRecord(
                account_id=account.id, trading_date=date.fromisoformat(day), opening_equity=Decimal(opening),
                closing_equity=Decimal(equity), cash=Decimal(equity), position_market_value=Decimal(0),
                daily_pnl=Decimal(pnl), daily_return=Decimal(pnl) / Decimal(opening), created_at=now, updated_at=now))
        db.commit()
        account_id = account.id
    set_active_runtime(SimulationRuntimeContext(SimBroker(Decimal("1048")), account_id, sessions))
    # legacy V0 E tree, exactly as the server holds it
    write_e(e_runtime, RB.OFFICIAL, [e_state("2026-09-24", rows=(), phase="ERROR", realized="0"), e_state()],
            records={"2026-09-23": RB.PROVISIONAL, "2026-09-24": RB.OFFICIAL, SESSION: RB.OFFICIAL, START: RB.OFFICIAL})
    write_e(e_runtime, RB.PROVISIONAL, [e_state("2026-09-23", realized="5",
                                                 rows=(("WMT", "1", "1", "1", "0", "1", "6", "0"),))])
    # official V1 E tree
    write_e_root(e_runtime, "paper_state_v1", RB.OFFICIAL, [e_state_v1(START)])
    app = create_app()

    async def override():
        with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = override
    yield app
    clear_active_sim_broker()


def write_e_root(run: Path, root_name: str, status: str, states: list[dict]) -> None:
    root = run / root_name / status / E
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    sessions, equity = {}, Decimal(10000)
    for state in states:
        (root / "sessions" / f"{state['session']}.json").write_text(json.dumps(state), encoding="utf-8")
        equity += Decimal(state["summary"]["sim_realized_pnl"])
        sessions[state["session"]] = {"sim_realized_pnl": state["summary"]["sim_realized_pnl"],
                                      "equity_after": str(equity), "phase": "COMPLETE"}
    (root / "book.json").write_text(json.dumps({
        "strategy_id": E, "initial_equity": "10000", "equity": str(equity), "realized_pnl": str(equity - 10000),
        "sessions": sessions, "accounting_version": "V1", "execution_version": "e_cost_v1_round_trip"}),
        encoding="utf-8")


async def get(app, path: str):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(path)
        assert response.status_code == 200, (path, response.text)
        return response.json()


@pytest.mark.asyncio
async def test_a_trades_come_from_the_paper_ledger(api) -> None:
    rows = await get(api, f"/api/v1/strategies/{A}/trades")
    assert {r["symbol"] for r in rows} == {"META", "AMD", "COR"}
    assert all(r["strategy_id"] == A and r["source"] == "PAPER_DB" for r in rows)
    by = {r["symbol"]: r for r in rows}
    assert by["META"]["accounting_version"] == "V0" and by["META"]["evaluation"] == "LEGACY"
    assert by["META"]["recorded_net_pnl"] == "24" and by["META"]["recomputed_v1_net_pnl"] == "26.4"
    assert by["COR"]["accounting_version"] == "V1" and by["COR"]["evaluation"] == "OFFICIAL"


@pytest.mark.asyncio
async def test_ledger_keeps_same_symbol_rows_in_their_own_books(api) -> None:
    rows = await get(api, "/api/v1/strategies/ledger")
    cor = sorted((r["strategy_id"], r["book"], r["accounting_version"], r["session"])
                 for r in rows if r["symbol"] == "COR")
    assert cor == [(A, "PAPER_DB", "V1", START), (E, RB.OFFICIAL, "V0", SESSION), (E, RB.OFFICIAL, "V1", START)]
    wmt = [r for r in rows if r["symbol"] == "WMT"]
    assert wmt and wmt[0]["book"] == RB.PROVISIONAL              # listed, tagged, never folded into OFFICIAL


@pytest.mark.asyncio
async def test_cards_split_official_and_legacy(api) -> None:
    cards = await get(api, "/api/v1/strategies/cards")
    assert [c["strategy_id"] for c in cards] == [A, E]
    assert [c["display_name"] for c in cards] == ["Strategy A", "Strategy E"]
    by_id = {c["strategy_id"]: c for c in cards}
    assert by_id[A]["official"]["trades"] == 1 and by_id[A]["legacy"]["trades"] == 2
    assert by_id[E]["official"]["trades"] == 1 and by_id[E]["legacy"]["trades"] == 3
    assert by_id[E]["legacy"]["net_pnl"] == RECORDED_0925 and by_id[E]["legacy"]["accounting_versions"] == ["V0"]
    assert by_id[A]["paper_clock"]["official_paper_start"] == START
    for card in cards:
        assert {"equity", "open_positions", "today_realized_pnl", "today_unrealized_pnl", "net_pnl", "trades",
                "last_signal_at", "last_trade_at", "lifecycle", "operational_status", "official", "legacy"} <= set(card)


@pytest.mark.asyncio
async def test_official_metrics_are_v1_only_and_legacy_stays_apart(api) -> None:
    body = await get(api, "/api/v1/strategies/performance")
    a, e = body["strategies"][A], body["strategies"][E]
    assert a["official"]["trades"] == 1 and a["official"]["net_pnl"] == "24.0000"
    assert a["official"]["accounting_mix"] == [L.ACCOUNTING_V1]
    assert a["legacy"]["trades"] == 2 and a["legacy"]["accounting_mix"] == [L.ACCOUNTING_V0]
    assert e["official"]["net_pnl"] == f"{V1_COR_NET.quantize(Decimal('0.0001'))}"
    assert e["legacy"]["net_pnl"] == "-123.1542" and e["legacy"]["trades"] == 3
    # official ledger total == official account equity change (A: 1024 -> 1048; E: book delta)
    assert Decimal(a["official"]["net_pnl"]) == Decimal(a["official"]["equity_change"]) == Decimal(24)
    assert a["official"]["ledger_vs_equity_gap"] == "0.0000" and e["official"]["ledger_vs_equity_gap"] == "0.0000"
    combined = body["combined"]
    assert combined["trades"] == 2 and combined["accounting_mix"] == [L.ACCOUNTING_V1]
    assert Decimal(combined["net_pnl"]) == Decimal(a["official"]["net_pnl"]) + Decimal(e["official"]["net_pnl"])
    assert Decimal(combined["equity_change"]) == (Decimal(a["official"]["equity_change"])
                                                  + Decimal(e["official"]["equity_change"]))
    assert body["legacy_combined"]["trades"] == 5
    assert body["e_sessions"] == [{"session": START, "phase": "COMPLETE", "error": False}]
    assert body["gate"][A]["verdict"] == body["gate"][E]["verdict"] == "INCONCLUSIVE"
    assert body["gate"][E]["accounting"] == [L.ACCOUNTING_V1]
    assert body["paper_clock"]["status"] == "STARTED"
    assert body["excluded"][RB.PROVISIONAL]["trades"] == 1


@pytest.mark.asyncio
async def test_a_v0_row_inside_the_official_window_blocks_the_gate(api, monkeypatch) -> None:
    monkeypatch.setenv(OFF.START_ENV, "2026-09-25")          # pull the V0 rows into the official window
    body = await get(api, "/api/v1/strategies/performance")
    assert set(body["strategies"][A]["official"]["accounting_mix"]) == {L.ACCOUNTING_V0, L.ACCOUNTING_V1}
    assert "ACCOUNTING_MIXED" in body["gate"][A]["reasons"]
    assert body["gate"][A]["verdict"] == "INCONCLUSIVE"


@pytest.mark.asyncio
async def test_before_the_start_nothing_is_official(api, monkeypatch) -> None:
    monkeypatch.delenv(OFF.START_ENV)
    body = await get(api, "/api/v1/strategies/performance")
    for sid in (A, E):
        assert body["strategies"][sid]["official"]["trades"] == 0
        assert body["strategies"][sid]["official"]["net_pnl"] is None
    assert body["combined"]["trades"] == 0 and body["paper_clock"]["status"] == "NOT_STARTED"
    assert body["strategies"][A]["legacy"]["trades"] == 2        # V0 only; the V1 COR row is PRE_OFFICIAL


@pytest.mark.asyncio
async def test_portfolio_is_official_by_default_and_never_mixes_books(api) -> None:
    official = await get(api, "/api/v1/strategies/portfolio")
    assert official["book"] == "official" and official["accounting_version"] == "V1"
    assert official["baseline"]["mode"] == "SIMULATION_BASELINE_ONLY"
    assert official["baseline"]["risk_budget"] == {A: "0.5", E: "0.5"}
    assert official["baseline"]["window"] == {"start": START, "end": START}
    assert official["baseline"]["symbol_overlap"]["symbols"] == ["COR"]
    assert official["combined"]["trades"] == 2
    legacy = await get(api, "/api/v1/strategies/portfolio?book=legacy")
    assert legacy["accounting_version"] == "V0" and legacy["combined"]["trades"] == 5
    assert legacy["baseline"]["window"]["end"] <= SESSION           # V0 dates only


@pytest.mark.asyncio
async def test_closed_strategies_have_no_operating_data(api) -> None:
    for closed in (REG.STRATEGY_B, REG.STRATEGY_C, REG.STRATEGY_D):
        status = await get(api, f"/api/v1/strategies/{closed}/status")
        assert status["enabled"] is False and status["research_lifecycle"] == "CLOSED"
        assert status["runtime_status"] == "NOT_RUNNING"
        assert await get(api, f"/api/v1/strategies/{closed}/trades") == []
