"""One session end to end: gate, setup, signal, fill, exit and the account that books it."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.backtest.strategy_b.costs import CostModel, FillScenario
from app.backtest.strategy_b.engine import SymbolSession, run_session
from app.backtest.strategy_b.portfolio import Portfolio
from app.strategy_b.config import StrategyBConfig
from app.strategy_b.exits import ExitReason
from app.strategy_b.models import CandidateState, SetupType
from app.strategy_b.session import AggregationScope
from tests.strategy_b.fixtures import (
    D, SYMBOL, boundaries, et, flat_volume_profile, in_scope, make_bar, make_tape,
    past_session_dates,
)

CONFIG = StrategyBConfig()
COSTS = CostModel(fee_bps_per_side=1.5, slippage_bps_per_side=5.0)


def breakout_session(symbol: str = SYMBOL, *, breakout_high: float = 10.50,
                     breakout_close: float = 10.45, tail=()):
    """Flat at 10.00, a +3% minute at 09:40, a shallow hold, then a break of the high."""
    bars = [make_bar(9, 30 + k, 10.00, 3000.0, open_=10.00, high=10.00, low=10.00)
            for k in range(10)]
    bars.append(make_bar(9, 40, 10.30, 40_000.0, open_=10.01, high=10.35, low=10.00))
    bars += [make_bar(9, 41 + k, 10.25, 8_000.0, open_=10.26, high=10.30, low=10.20)
             for k in range(3)]
    bars.append(make_bar(9, 44, breakout_close, 20_000.0, open_=10.30, high=breakout_high,
                         low=10.28))
    return make_tape([*bars, *tail], symbol=symbol)


def symbol_session(symbol: str = SYMBOL, **overrides) -> SymbolSession:
    history = tuple(flat_volume_profile(day, 100.0) for day in past_session_dates(20))
    tape = overrides.pop("tape", None) or breakout_session(symbol)
    return SymbolSession(tape=tape, scope=in_scope(), rvol_history=history, **overrides)


def portfolio(equity: float = 100_000.0, risk=None) -> Portfolio:
    return Portfolio(equity=equity, costs=COSTS, risk=risk or CONFIG.risk)


def run(items, *, account=None, scenario=FillScenario.SIGNAL_BAR, config=CONFIG):
    account = account or portfolio()
    report = run_session(items, config=config, portfolio=account, scenario=scenario,
                         boundaries=boundaries())
    return report, account


# ---- the intended path ----------------------------------------------------------------------

def test_one_session_produces_one_trade_from_gate_to_time_stop() -> None:
    report, account = run([symbol_session()])

    assert report.candidates_opened == 1
    assert report.entries == 1
    assert len(report.trades) == 1

    trade = report.trades[0]
    assert (trade.symbol, trade.setup) == (SYMBOL, SetupType.HOD_BREAKOUT)
    assert trade.entry_bar_timestamp == et(9, 44)          # the bar that crossed the trigger
    assert trade.entered_at == et(9, 46)                   # signalled 09:45, filled one tick later
    assert trade.entry_price == pytest.approx(10.35 * 1.001 * 1.0005)  # trigger plus slippage
    assert trade.initial_stop == 10.20
    assert [leg.reason for leg in trade.legs] == [ExitReason.TIME_STOP]
    assert trade.legs[0].at == et(10, 16)                  # entry + 30 wall-clock minutes
    assert trade.is_closed and trade.realized_r > 0
    assert trade.fees > 0 and trade.net_pnl < trade.gross_pnl
    assert account.equity > 100_000.0
    assert not account.open_positions


def test_the_candidate_lifecycle_is_recorded_even_when_it_ends_in_a_trade() -> None:
    report, _ = run([symbol_session()])
    entered = [c for c in report.finished_candidates if c.state is CandidateState.ENTERED]
    assert len(entered) == 1
    candidate = entered[0]
    assert candidate.detected_at == et(9, 41)              # the +3% bar becomes visible
    assert candidate.setup_ready_at == et(9, 44)           # three held bars after the high
    assert candidate.signal_at == et(9, 45)
    assert candidate.signal_bar_timestamp == et(9, 44)
    assert candidate.entered_at == et(9, 46)


def test_the_conservative_scenario_fills_at_the_next_bar_open() -> None:
    """Same tape, two execution assumptions: the conservative one pays the next open."""
    tail = [make_bar(9, 45, 10.40, 5_000.0, open_=10.42, high=10.45, low=10.38)]
    items = [symbol_session(tape=breakout_session(tail=tail))]
    strict, _ = run(items, scenario=FillScenario.SIGNAL_BAR)
    loose, _ = run(items, scenario=FillScenario.NEXT_BAR_OPEN)
    assert strict.trades[0].entry_price == pytest.approx(10.35 * 1.001 * 1.0005)
    assert loose.trades[0].entry_price == pytest.approx(10.42 * 1.0005)
    assert strict.trades[0].entry_price < loose.trades[0].entry_price


def test_a_breakout_that_ran_away_before_the_fill_is_not_chased() -> None:
    """09:45 closes 2.3% above the trigger, past the 1% drift tolerance, so nothing is bought."""
    tail = [make_bar(9, 45, 10.60, 5_000.0, open_=10.55, high=10.65, low=10.50)]
    report, account = run([symbol_session(tape=breakout_session(tail=tail))])
    assert report.entries == 0 and not account.trades
    assert report.refusals.get("PRICE_DRIFT", 0) == 1


# ---- account limits ---------------------------------------------------------------------------

def test_the_open_position_limit_refuses_the_fourth_symbol() -> None:
    risk = replace(CONFIG.risk, max_open_positions=2)
    items = [symbol_session(symbol) for symbol in ("AAAA", "BBBB", "CCCC", "DDDD")]
    report, account = run(items, account=portfolio(risk=risk))
    assert report.entries == 2
    assert report.refusals.get("MAX_POSITIONS", 0) == 2
    assert len(report.trades) == 2


def test_a_symbol_enters_at_most_once_a_day() -> None:
    """The trade closes long before the session does; the symbol still does not come back."""
    report, _ = run([symbol_session()])
    assert report.entries == 1
    assert report.candidates_opened == 1


def test_an_account_too_small_to_buy_a_share_takes_no_trade() -> None:
    report, account = run([symbol_session()], account=portfolio(equity=50.0))
    assert report.entries == 0
    assert report.refusals.get("SIZE_ZERO", 0) == 1
    assert account.equity == 50.0 and not account.trades


# ---- refusals the research layer owns ---------------------------------------------------------

def test_a_symbol_out_of_scope_never_becomes_a_candidate() -> None:
    report, _ = run([replace(symbol_session(), scope=None)])
    assert report.entries == 0
    assert report.refusals.get("INELIGIBLE", 0) == 1


def test_a_quiet_session_produces_nothing_and_costs_no_snapshots() -> None:
    flat = make_tape([make_bar(9, 30 + k, 10.00, 500.0, open_=10.00, high=10.00, low=10.00)
                      for k in range(30)])
    report, account = run([symbol_session(tape=flat)])
    assert (report.prefiltered_minutes, report.candidates_opened, report.entries) == (0, 0, 0)
    assert account.equity == 100_000.0


def test_the_session_refuses_inputs_that_do_not_belong_to_it() -> None:
    other_day = date(2026, 3, 11)
    wrong = make_tape([make_bar(9, 30, 10.0, 1000.0, day=other_day)], day=other_day)
    with pytest.raises(ValueError):
        run([symbol_session(tape=wrong)])
    with pytest.raises(ValueError):
        run([symbol_session(), symbol_session()])
