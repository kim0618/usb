"""H-V2 PAPER EXECUTION V1: APPROVE -> pending entry -> position -> exits, under the frozen contract.

Every test uses a temporary forward store (``STRATEGY_H_FORWARD_DIR``) or drives the engine directly
on synthetic bars. No test reads the real launch snapshot, Strategy A's paper database or Strategy
E's runtime files, and no price used here is a real forward price of a cohort issuer.
"""

from __future__ import annotations

import json
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

import pytest

from app.execution.config import ExecutionConfig
from app.execution.costs import buy_effective_price
from app.strategies import registry as REG
from app.strategies.h_forward import contract as FC
from app.strategies.h_forward import prices as PR
from app.strategies.h_forward import research_queue as RQ
from app.strategies.h_forward import store as ST
from app.strategies.h_forward import transitions as TR
from app.strategies.h_forward import views as VW
from app.strategies.h_paper import contract as PC
from app.strategies.h_paper import engine as EN
from app.strategies.h_paper import service as HP
from app.strategies.h_paper import store as PS

H = REG.STRATEGY_H_V2
D5 = FC.required_d5_contract()
LAUNCH_D7 = "2026-10-04T08:34:30+00:00"
PAPER_AT = "2026-10-13T12:00:00+00:00"          # 08:00 ET, before the 10-13 open
SESSIONS = ("2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20",
            "2026-10-21", "2026-10-22")
CFG = ExecutionConfig()


# -- fixtures ---------------------------------------------------------------------------------------

def ledger_row(ticker: str, decision: str, at: str, *, previous: str | None = None, cause: str = "LAUNCH",
               tp1: float | None = 12.0, tp2: float | None = 15.0, bear: float | None = 8.0,
               d5: str = D5, version: str | None = None) -> dict:
    return {"strategy_id": H, "ticker": ticker, "thesis_version": version or f"T:{ticker}:{at}",
            "decision_time": at, "decision": decision, "previous_decision": previous, "cause": cause,
            "tp1": tp1, "tp2": tp2, "bear": bear, "bear_refusal": None if bear is not None else "NEGATIVE_IMPLIED_EQUITY",
            "valuation_confidence": "MEDIUM", "d5_contract": d5}


def bar(o, h, l, c):
    return PR.Bar(session="", close=c, high=h, low=l, volume=1000.0, open=o)


def flat(price=10.0):
    return bar(price, price, price, price)


def run_engine(ledger, prices, *, sessions=SESSIONS, at=PAPER_AT):
    """prices: {session: {ticker: Bar}}; a ticker absent from a session has no bar."""
    launch = {"launched_at": at, "initial_equity": "10000"}
    engine = EN.Engine(launch, ledger, [s for s in sessions if s in prices], lambda s: prices.get(s, {}))
    return engine.run()


def kinds(result, ticker=None):
    return [e["event"] for e in result.events if ticker is None or e.get("ticker") == ticker]


def first(result, kind, ticker=None):
    return next(e for e in result.events if e["event"] == kind and (ticker is None or e.get("ticker") == ticker))


def expected_qty(equity: Decimal, raw: Decimal) -> Decimal:
    return (equity * PC.slot_fraction() / buy_effective_price(raw, CFG)).to_integral_value(rounding=ROUND_DOWN)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(ST.ROOT_ENV, str(tmp_path / "d7"))
    return tmp_path / "d7"


def seed(decisions: dict[str, str]) -> None:
    snaps = [{"record": "LAUNCH", "strategy_id": H, "ticker": t, "cik": f"000000000{i}",
              "security_id": f"BBG{t}", "d5_contract": D5, "decision": d, "thesis_version": f"T1:{t}",
              "cohort_tag": FC.INITIAL_COHORT, "launch_baseline_session": "2026-10-02",
              "decision_session": "2026-09-16", "launch_timestamp": LAUNCH_D7,
              "valuation": {"tp1": 12.0, "tp2": 15.0, "bear": 8.0}}
             for i, (t, d) in enumerate(sorted(decisions.items()))]
    ST.append_snapshots(snaps)
    ST.append_ledger([ledger_row(t, d, LAUNCH_D7, version=f"T1:{t}") | {"cohort_tag": FC.INITIAL_COHORT}
                      for t, d in sorted(decisions.items())])


def store_bars(session: str, rows: dict[str, tuple]) -> None:
    PR.write_session(session, {t: {"open": o, "high": h, "low": l, "close": c, "volume": 1}
                               for t, (o, h, l, c) in rows.items()}, fetched_at="2026-10-23T05:00:00+00:00")


# -- the contract -----------------------------------------------------------------------------------

def test_contract_is_frozen_and_states_the_v1_rules():
    state = PC.state()
    assert state["contract_id"] == "H_V2_PAPER_EXECUTION_V1"
    assert PC.max_open_positions() == 3 and PC.slot_fraction() * 3 < 1 + Decimal("1e-20")
    assert PC.initial_equity() == Decimal("10000")
    assert PC.required_d5_contract() == "D5_D2R_V1"
    body = PC.contract()
    assert body["exits"]["fixed_percentage_stop"] is False and body["exits"]["atr_stop"] is False
    assert body["launch"]["historical_backfill"] is False
    assert body["research_budget"]["production_daily_budget_usd"] is None


# -- entry ------------------------------------------------------------------------------------------

def test_watch_never_enters():
    ledger = [ledger_row("AAA", "WATCH", LAUNCH_D7),
              ledger_row("AAA", "WATCH", "2026-10-13T20:30:00+00:00", previous="WATCH", cause="MATERIAL_REFRESH")]
    result = run_engine(ledger, {s: {"AAA": flat()} for s in SESSIONS})
    assert "BUY" not in kinds(result) and "PENDING_ENTRY" not in kinds(result)
    assert first(result, "SIGNAL_NO_ACTION")["paper_action"] == "NONE"


def test_reject_never_enters():
    ledger = [ledger_row("BBB", "REJECT", LAUNCH_D7),
              ledger_row("BBB", "WATCH", "2026-10-13T20:30:00+00:00", previous="REJECT", cause="MATERIAL_REFRESH")]
    result = run_engine(ledger, {s: {"BBB": flat()} for s in SESSIONS})
    assert "BUY" not in kinds(result)


def test_approve_creates_pending_entry_before_its_session_is_stored():
    ledger = [ledger_row("AAA", "APPROVE", "2026-10-14T21:00:00+00:00", previous="WATCH", cause="MATERIAL_REFRESH")]
    result = run_engine(ledger, {s: {"AAA": flat()} for s in SESSIONS[:2]})
    pending = first(result, "PENDING_ENTRY")
    assert pending["entry_effective_session"] == "2026-10-15"
    assert result.pending == [{"signal_key": pending["signal_key"], "ticker": "AAA",
                               "entry_effective_session": "2026-10-15", "state": "PENDING_ENTRY"}]
    assert "BUY" not in kinds(result)


def test_entry_fills_at_the_next_session_open_never_the_same_session():
    # Decided at 11:00 ET on 10-13, after that session's open: the fill is the 10-14 open.
    ledger = [ledger_row("AAA", "APPROVE", "2026-10-13T15:00:00+00:00", previous="WATCH", cause="MATERIAL_REFRESH")]
    prices = {"2026-10-13": {"AAA": bar(9.0, 9.0, 9.0, 9.0)}, "2026-10-14": {"AAA": bar(10.0, 10.5, 9.9, 10.2)},
              "2026-10-15": {"AAA": flat(10.2)}}
    result = run_engine(ledger, prices)
    buy = first(result, "BUY")
    assert buy["session"] == "2026-10-14" and Decimal(buy["raw_price"]) == Decimal("10")
    assert Decimal(buy["fill_price"]) == Decimal("10") * (1 + Decimal("15") / 10000)
    assert Decimal(buy["quantity"]) == expected_qty(Decimal("10000"), Decimal("10"))
    assert buy["decision_effective_session"] == "2026-10-14"


def test_future_price_leakage_is_rejected_by_the_invariants(store):
    seed({"AAA": "WATCH"})
    row = ledger_row("AAA", "APPROVE", "2026-10-14T15:00:00+00:00", previous="WATCH", cause="MATERIAL_REFRESH")
    ST.append_ledger([row])
    key = EN.signal_key(row)
    forged = [{"event": "PENDING_ENTRY", "event_id": f"{key}|PENDING_ENTRY", "signal_key": key, "ticker": "AAA",
               "decision_time": row["decision_time"]},
              {"event": "BUY", "event_id": "x|BUY", "signal_key": key, "ticker": "AAA", "session": "2026-10-14"}]
    problems = HP.invariant_problems(forged)
    assert any("future-price leakage" in p for p in problems)


def test_max_three_positions_and_slot_exhaustion_waits_then_enters_when_a_slot_frees():
    at = "2026-10-13T20:30:00+00:00"
    ledger = [ledger_row(t, "APPROVE", at, previous="WATCH", cause="MATERIAL_REFRESH") for t in ("AAA", "BBB", "CCC", "DDD")]
    ledger.append(ledger_row("AAA", "WATCH", "2026-10-15T21:00:00+00:00", previous="APPROVE", cause="MATERIAL_REFRESH"))
    prices = {s: {t: flat() for t in ("AAA", "BBB", "CCC", "DDD")} for s in SESSIONS}
    result = run_engine(ledger, prices)
    buys = [e for e in result.events if e["event"] == "BUY"]
    assert [b["ticker"] for b in buys[:3]] == ["AAA", "BBB", "CCC"]
    assert all(b["session"] == "2026-10-14" for b in buys[:3])
    waiting = first(result, "APPROVE_WAITING_SLOT", "DDD")
    assert waiting["session"] == "2026-10-14" and waiting["open_positions"] == 3
    # AAA's downgrade closes it at the 10-16 open, which frees the slot DDD enters at the same open.
    assert first(result, "SELL_DECISION_DOWNGRADE", "AAA")["session"] == "2026-10-16"
    assert buys[3]["ticker"] == "DDD" and buys[3]["session"] == "2026-10-16"
    assert max(e["open_positions"] for e in result.events if e["event"] == "NAV") == 3


def test_duplicate_approve_never_pyramids():
    at = "2026-10-13T20:30:00+00:00"
    row = ledger_row("AAA", "APPROVE", at, previous="WATCH", cause="MATERIAL_REFRESH")
    later = ledger_row("AAA", "APPROVE", "2026-10-15T21:00:00+00:00", previous="APPROVE", cause="MATERIAL_REFRESH")
    result = run_engine([row, dict(row), later], {s: {"AAA": flat()} for s in SESSIONS})
    assert kinds(result).count("BUY") == 1
    assert "APPROVE_DUPLICATE_IGNORED" in kinds(result)
    assert "APPROVE_IGNORED_ALREADY_OPEN" in kinds(result)


def test_writer_refuses_approve_to_approve_and_non_evidential_causes(store):
    seed({"AAA": "WATCH"})
    common = dict(thesis_version="T2:AAA", decision_time="2026-10-13T20:30:00+00:00", effective_session="2026-10-13",
                  d5_contract=D5, valuation={"tp1": 12, "tp2": 15, "bear": 8}, checksums={}, evidence={})
    with pytest.raises(TR.TransitionRefused):
        TR.record("AAA", decision="APPROVE", cause="PRICE", **common)
    TR.record("AAA", decision="APPROVE", cause=TR.MATERIAL_REFRESH, **common)
    with pytest.raises(TR.TransitionRefused):
        TR.record("AAA", decision="APPROVE", cause=TR.MATERIAL_REFRESH,
                  **(common | {"thesis_version": "T3:AAA", "decision_time": "2026-10-14T20:30:00+00:00"}))


# -- exits ------------------------------------------------------------------------------------------

def _entered(prices_after: dict, *, tp1=12.0, tp2=15.0, bear: float | None = 8.0, extra=()):
    ledger = [ledger_row("AAA", "APPROVE", "2026-10-13T20:30:00+00:00", previous="WATCH", cause="MATERIAL_REFRESH",
                         tp1=tp1, tp2=tp2, bear=bear), *extra]
    prices = {"2026-10-13": {"AAA": flat(9.5)}, "2026-10-14": {"AAA": flat(10.0)}} | prices_after
    return run_engine(ledger, prices)


def test_tp1_sells_half_and_keeps_half():
    result = _entered({"2026-10-15": {"AAA": bar(10.5, 12.5, 10.4, 12.2)}, "2026-10-16": {"AAA": flat(12.2)}})
    buy, sell = first(result, "BUY"), first(result, "SELL_TP1")
    half = (Decimal(buy["quantity"]) / 2).to_integral_value(rounding=ROUND_DOWN)
    assert sell["exit_reason"] == "TP1" and Decimal(sell["raw_price"]) == Decimal("12") and Decimal(sell["quantity"]) == half
    assert sell["position_status"] == "PARTIAL"
    assert Decimal(sell["remaining_shares"]) == Decimal(buy["quantity"]) - half
    assert result.positions[0]["status"] == "PARTIAL" and result.positions[0]["tp1_done"] is True


def test_tp2_closes_the_rest():
    result = _entered({"2026-10-15": {"AAA": bar(10.5, 12.5, 10.4, 12.2)},
                       "2026-10-16": {"AAA": bar(12.5, 15.2, 12.4, 15.0)}})
    tp2 = first(result, "SELL_TP2")
    assert Decimal(tp2["raw_price"]) == Decimal("15") and Decimal(tp2["remaining_shares"]) == 0 and tp2["position_status"] == "CLOSED"
    assert result.positions == []


def test_a_gap_open_above_tp_fills_at_the_open():
    result = _entered({"2026-10-15": {"AAA": bar(16.0, 16.5, 15.8, 16.2)}})
    assert Decimal(first(result, "SELL_TP1")["raw_price"]) == Decimal("16")
    assert Decimal(first(result, "SELL_TP2")["raw_price"]) == Decimal("16")


def test_approve_to_watch_closes_everything_at_the_next_open():
    down = ledger_row("AAA", "WATCH", "2026-10-15T21:00:00+00:00", previous="APPROVE", cause="MATERIAL_REFRESH")
    result = _entered({"2026-10-15": {"AAA": flat(10.5)}, "2026-10-16": {"AAA": bar(10.8, 11, 10.7, 10.9)}}, extra=[down])
    sell = first(result, "SELL_DECISION_DOWNGRADE")
    assert sell["exit_reason"] == "DECISION_DOWNGRADE_WATCH" and sell["session"] == "2026-10-16"
    assert Decimal(sell["raw_price"]) == Decimal("10.8") and sell["position_status"] == "CLOSED"


def test_approve_to_reject_closes_everything():
    down = ledger_row("AAA", "REJECT", "2026-10-15T21:00:00+00:00", previous="APPROVE", cause="MATERIAL_REFRESH")
    result = _entered({"2026-10-15": {"AAA": flat(10.5)}, "2026-10-16": {"AAA": flat(10.6)}}, extra=[down])
    assert first(result, "SELL_DECISION_DOWNGRADE")["exit_reason"] == "DECISION_DOWNGRADE_REJECT"


def test_thesis_invalidation_closes_everything():
    inval = ledger_row("AAA", "WATCH", "2026-10-15T21:00:00+00:00", previous="APPROVE", cause="THESIS_INVALIDATED")
    result = _entered({"2026-10-15": {"AAA": flat(10.5)}, "2026-10-16": {"AAA": flat(10.6)}}, extra=[inval])
    sell = first(result, "SELL_INVALIDATION")
    assert sell["exit_reason"] == "THESIS_INVALIDATED" and sell["position_status"] == "CLOSED"


def test_bear_breach_closes_at_the_bear():
    result = _entered({"2026-10-15": {"AAA": bar(9.0, 9.1, 7.5, 7.8)}})
    sell = first(result, "SELL_BEAR")
    assert Decimal(sell["raw_price"]) == Decimal("8") and sell["exit_reason"] == "BEAR_BREACH"


def test_bear_and_tp_in_one_bar_take_the_worst_case():
    result = _entered({"2026-10-15": {"AAA": bar(10.0, 12.5, 7.9, 10.0)}})
    assert "SELL_TP1" not in kinds(result)
    assert first(result, "SELL_BEAR")["trigger"].endswith("AMBIGUOUS_WORST_CASE")


def test_bear_not_available_does_not_invent_a_stop():
    result = _entered({"2026-10-15": {"AAA": bar(5.0, 5.0, 1.0, 1.2)}, "2026-10-16": {"AAA": flat(1.0)}}, bear=None)
    buy = first(result, "BUY")
    assert buy["bear"] is None and buy["bear_rule"] == EN.NOT_EVALUATED
    assert not [k for k in kinds(result) if k.startswith("SELL")]
    assert result.positions[0]["bear_rule"] == EN.NOT_EVALUATED


def test_tp1_absent_means_no_tp1_exit():
    result = _entered({"2026-10-15": {"AAA": bar(10.5, 13.0, 10.4, 12.8)}}, tp1=None)
    assert "SELL_TP1" not in kinds(result)


def test_entry_session_bar_is_not_used_for_exits():
    ledger = [ledger_row("AAA", "APPROVE", "2026-10-13T20:30:00+00:00", previous="WATCH", cause="MATERIAL_REFRESH")]
    result = run_engine(ledger, {"2026-10-13": {"AAA": flat(9.5)}, "2026-10-14": {"AAA": bar(10.0, 16.0, 7.0, 10.0)}})
    assert not [k for k in kinds(result) if k.startswith("SELL")]


# -- money --------------------------------------------------------------------------------------------

def test_nav_arithmetic_and_realized_unrealized_pnl():
    result = _entered({"2026-10-15": {"AAA": bar(10.5, 12.5, 10.4, 12.2)}, "2026-10-16": {"AAA": flat(13.0)}})
    buy, sell = first(result, "BUY"), first(result, "SELL_TP1")
    qty, half = Decimal(buy["quantity"]), Decimal(sell["quantity"])
    entry_fill = Decimal("10") * (1 + Decimal("15") / 10000)
    exit_fill = Decimal("12") * (1 - Decimal("15") / 10000)
    commission = Decimal("10") * qty * Decimal("10") / 10000 + Decimal("12") * half * Decimal("10") / 10000
    cash = Decimal("10000") - entry_fill * qty - Decimal("10") * qty * Decimal("10") / 10000 \
        + exit_fill * half - Decimal("12") * half * Decimal("10") / 10000
    nav = [e for e in result.events if e["event"] == "NAV"][-1]
    assert Decimal(nav["cash"]) == cash
    assert Decimal(nav["equity"]) == Decimal(nav["cash"]) + Decimal(nav["positions_value"])
    assert Decimal(nav["positions_value"]) == (qty - half) * Decimal("13")
    realized = (exit_fill - entry_fill) * half - commission
    unrealized = (Decimal("13") - entry_fill) * (qty - half)
    assert Decimal(nav["equity"]) - Decimal("10000") == realized + unrealized
    for e in result.events:
        if e["event"] == "NAV":
            assert Decimal(e["cash"]) + Decimal(e["positions_value"]) == Decimal(e["equity"])


def test_a_closed_trade_reconciles_cash_to_net_pnl():
    result = _entered({"2026-10-15": {"AAA": bar(10.5, 12.5, 10.4, 12.2)},
                       "2026-10-16": {"AAA": bar(12.5, 15.2, 12.4, 15.0)}})
    tp2 = first(result, "SELL_TP2")
    assert Decimal(tp2["cash_after"]) - Decimal("10000") == Decimal(tp2["realized_net_pnl"])


# -- launch, backfill, integrity ---------------------------------------------------------------------

def test_d5_d2r_v1_is_required():
    row = ledger_row("AAA", "APPROVE", "2026-10-13T20:30:00+00:00", previous="WATCH", cause="MATERIAL_REFRESH", d5="D5_D2_V1")
    result = run_engine([row], {s: {"AAA": flat()} for s in SESSIONS})
    assert "BUY" not in kinds(result) and "APPROVE_REFUSED_D5_CONTRACT" in kinds(result)


def test_writer_refuses_a_superseded_d5_contract(store):
    seed({"AAA": "WATCH"})
    with pytest.raises(TR.TransitionRefused):
        TR.record("AAA", decision="APPROVE", cause=TR.MATERIAL_REFRESH, thesis_version="T2:AAA",
                  decision_time="2026-10-13T20:30:00+00:00", effective_session="2026-10-13",
                  d5_contract="D5_D2_V1", valuation={}, checksums={}, evidence={})


def test_historical_watch_and_pre_launch_approve_are_not_backfilled(store):
    seed({"AAA": "WATCH", "BBB": "WATCH", "CCC": "REJECT"})
    # An APPROVE recorded before the paper launch is the shadow's past, never a paper trade.
    TR.record("AAA", decision="APPROVE", cause=TR.MATERIAL_REFRESH, thesis_version="T2:AAA",
              decision_time="2026-10-12T20:30:00+00:00", effective_session="2026-10-12", d5_contract=D5,
              valuation={"tp1": 12, "tp2": 15, "bear": 8}, checksums={}, evidence={})
    HP.launch(at=PAPER_AT)
    for s in SESSIONS[:4]:
        store_bars(s, {"AAA": (10, 10, 10, 10), "BBB": (10, 10, 10, 10), "CCC": (10, 10, 10, 10), "SPY": (1, 1, 1, 1)})
    status = HP.run()
    assert status["paper_run"] == "OK" and status["problems"] == []
    assert not [e for e in PS.events() if e["event"] not in ("NAV",)]
    account = VW.account()
    assert account["initial_equity"] == "10000" and account["current_equity"] == "10000"
    assert account["cash"] == "10000" and account["open_positions"] == 0 and account["trades"] == 0
    assert VW.positions() == [] and VW.trades() == []


def test_launch_is_written_once_and_only_after_the_shadow(store):
    assert HP.launch(at=PAPER_AT)["reason"] == "FORWARD_SHADOW_NOT_LAUNCHED"
    seed({"AAA": "WATCH"})
    first_launch = HP.launch(at=PAPER_AT)
    assert first_launch["launched"] is True
    assert first_launch["launch"]["decision_counts_at_launch"] == {"APPROVE": 0, "WATCH": 1, "REJECT": 0}
    again = HP.launch(at="2026-10-20T12:00:00+00:00")
    assert again["launched"] is False and PS.launch()["launched_at"] == PAPER_AT


def test_end_to_end_store_run_is_append_only_and_detects_tampering(store):
    seed({"AAA": "WATCH"})
    HP.launch(at=PAPER_AT)
    TR.record("AAA", decision="APPROVE", cause=TR.MATERIAL_REFRESH, thesis_version="T2:AAA",
              decision_time="2026-10-13T20:30:00+00:00", effective_session="2026-10-13", d5_contract=D5,
              valuation={"tp1": 12, "tp2": 15, "bear": 8, "confidence": "MEDIUM"}, checksums={}, evidence={})
    store_bars("2026-10-13", {"AAA": (9, 9, 9, 9), "SPY": (1, 1, 1, 1)})
    assert HP.run()["pending"][0]["entry_effective_session"] == "2026-10-14"
    store_bars("2026-10-14", {"AAA": (10, 10.2, 9.8, 10), "SPY": (1, 1, 1, 1)})
    first_run = HP.run()
    assert first_run["open_positions"] == 1
    count = len(PS.events())
    assert HP.run()["appended"] == 0 and len(PS.events()) == count          # idempotent
    store_bars("2026-10-15", {"AAA": (10.5, 12.5, 10.4, 12.2), "SPY": (1, 1, 1, 1)})
    store_bars("2026-10-16", {"AAA": (12.5, 15.5, 12.4, 15.0), "SPY": (1, 1, 1, 1)})
    HP.run()
    trades = VW.trades()
    assert len(trades) == 1 and trades[0]["status"] == "CLOSED" and trades[0]["exit_reason"] == "TP2"
    assert trades[0]["trade_id"].startswith("STRATEGY_H_V2|2026-10-14|AAA") and trades[0]["strategy_id"] == H
    assert [e["trade_action"] for e in VW.fills()] == ["SELL_TP2", "SELL_TP1", "BUY"]
    assert HP.verify()["verdict"] == "PASS"
    card = VW.card()
    assert card["trades"] == 1 and card["initial_equity"] == "10000" and card["paper"]["mode"] == PC.SHADOW_PLUS_PAPER
    # Tamper with one stored event: verify fails and run appends nothing.
    path = PS.path(PS.EVENTS)
    lines = path.read_text().splitlines()
    row = json.loads(lines[1])
    row["raw_price"] = "1"
    lines[1] = json.dumps(row, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    assert HP.verify()["verdict"] == "FAIL"
    store_bars("2026-10-19", {"AAA": (15, 15, 15, 15), "SPY": (1, 1, 1, 1)})
    failed = HP.run()
    assert failed["paper_run"] == "PAPER_INTEGRITY_FAILURE" and failed["appended"] == 0


def test_a_gap_in_the_price_store_stalls_processing_rather_than_skipping_a_session():
    ledger = [ledger_row("AAA", "APPROVE", "2026-10-13T20:30:00+00:00", previous="WATCH", cause="MATERIAL_REFRESH")]
    prices = {"2026-10-13": {"AAA": flat()}, "2026-10-14": {"AAA": flat()}, "2026-10-16": {"AAA": bar(8, 8, 1, 2)}}
    result = run_engine(ledger, prices)
    assert result.stalled_at == "2026-10-15" and result.processed_through == "2026-10-14"
    assert "SELL_BEAR" not in kinds(result)


def test_a_missing_open_keeps_the_entry_pending_and_never_uses_a_close():
    ledger = [ledger_row("AAA", "APPROVE", "2026-10-13T20:30:00+00:00", previous="WATCH", cause="MATERIAL_REFRESH")]
    no_open = PR.Bar(session="", close=10.0, high=10.0, low=10.0, volume=1.0, open=None)
    result = run_engine(ledger, {"2026-10-13": {"AAA": flat(9.5)}, "2026-10-14": {"AAA": no_open},
                                "2026-10-15": {"AAA": flat(11.0)}})
    assert "ENTRY_PRICE_MISSING" in kinds(result)
    buy = first(result, "BUY")
    assert buy["session"] == "2026-10-15" and Decimal(buy["raw_price"]) == Decimal("11") and buy["execution_delay_sessions"] == 1


def test_downgrade_before_fill_cancels_the_pending_entry():
    ledger = [ledger_row("AAA", "APPROVE", "2026-10-13T20:30:00+00:00", previous="WATCH", cause="MATERIAL_REFRESH"),
              ledger_row("AAA", "WATCH", "2026-10-13T22:00:00+00:00", previous="APPROVE", cause="MATERIAL_REFRESH")]
    result = run_engine(ledger, {s: {"AAA": flat()} for s in SESSIONS})
    assert "BUY" not in kinds(result) and "APPROVE_CANCELLED_DECISION_CHANGED" in kinds(result)


# -- isolation ----------------------------------------------------------------------------------------

def test_h_paper_writes_only_under_its_own_store(store, tmp_path):
    seed({"AAA": "WATCH"})
    HP.launch(at=PAPER_AT)
    TR.record("AAA", decision="APPROVE", cause=TR.MATERIAL_REFRESH, thesis_version="T2:AAA",
              decision_time="2026-10-13T20:30:00+00:00", effective_session="2026-10-13", d5_contract=D5,
              valuation={"tp1": 12, "tp2": 15, "bear": 8}, checksums={}, evidence={})
    for s in SESSIONS[:3]:
        store_bars(s, {"AAA": (10, 10, 10, 10), "SPY": (1, 1, 1, 1)})
    HP.run()
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written and all(str(p).startswith(str(store)) for p in written)
    assert all(t["strategy_id"] == H and t["trade_id"].startswith("STRATEGY_H_V2|") for t in VW.trades())


def test_same_ticker_in_a_and_e_is_isolated_from_h(store):
    """H's trade identity carries its strategy id; an A or E row for the same ticker cannot collide."""
    from app.strategies import ledger as L

    class Row:
        trade_uid, gross_pnl, net_pnl, total_cost, status = "A-1", "1", "1", "0", "CLOSED"
        symbol, entry_time, exit_time = "AAA", None, None
        average_entry_price = average_exit_price = total_quantity = "1"
        exit_reason = "TP"
    a = L.a_trade_record(Row(), strategy_id=REG.STRATEGY_A, strategy_version="V0", account_id=1)
    result = _entered({"2026-10-15": {"AAA": flat(10.5)}})
    h_ids = {e["position_id"] for e in result.events if e["event"] == "BUY"}
    assert a["trade_id"] not in h_ids and all(i.startswith(H) for i in h_ids)


# -- research queue -----------------------------------------------------------------------------------

def test_research_queue_drops_a_filing_the_thesis_already_read(store):
    seed({"AEYE": "REJECT", "FG": "WATCH"})
    queue = {"generated_at": "2026-10-12T05:10:56+00:00", "issuers": [
        {"ticker": "AEYE", "cik": "1", "state": "REFRESH_DUE",
         "new_material_filings": [{"form": "8-K", "filingDate": "2026-09-18", "accession": "A-OLD"}]},
        {"ticker": "FG", "cik": "2", "state": "REFRESH_DUE",
         "new_material_filings": [{"form": "8-K", "filingDate": "2026-10-05", "accession": "F-NEW"}]}]}
    ST.path("refresh_queue.json").write_text(json.dumps(queue))
    index = {"issuers": {"AEYE": {"chunked_accessions": ["A-OLD"], "thesis_version": "T1:AEYE"},
                         "FG": {"chunked_accessions": ["F-OLD"], "thesis_version": "T1:FG"}}}
    body = RQ.build(caches={}, index=index)
    by = {r["ticker"]: r for r in body["rows"]}
    assert by["AEYE"]["research_status"] == RQ.EVIDENCE_ALREADY_IN_THESIS and by["AEYE"]["d3_needed"] is False
    assert by["FG"]["research_status"] == RQ.QUEUED and by["FG"]["d3_needed"] is True
    assert body["queued"] == ["FG"] and body["model_calls_scheduled"] == 0
    assert body["intake_state"] == RQ.INTAKE_NOT_WIRED


def test_frozen_evidence_index_shows_the_aeye_8k_was_read_and_fg_vrrm_were_not():
    index = RQ.evidence_index()["issuers"]
    assert "0001104659-26-108940" in index["AEYE"]["chunked_accessions"]
    assert "0001934850-26-000095" not in index["FG"]["chunked_accessions"]
    assert "0001193125-26-412497" not in index["VRRM"]["chunked_accessions"]
