"""Exit management and position sizing (B-F0 sections 8.4 and 9)."""

from dataclasses import replace

import pytest

from app.strategy_b.config import StrategyBConfig
from app.strategy_b.errors import InvalidTransition, PointInTimeViolation
from app.strategy_b.exits import ExitReason, advance_position, open_position
from app.strategy_b.models import SetupType
from app.strategy_b.session import AggregationScope
from app.strategy_b.sizing import SizeRefusal, position_size
from tests.strategy_b.fixtures import at, breakout_bars, et, make_bar, make_tape

CONFIG = StrategyBConfig()
SCOPE = AggregationScope.EXTENDED_DAY
ENTRY = 11.011
STOP = 10.75
R = ENTRY - STOP                      # 0.261
TARGET = ENTRY + 2 * R                # 11.533


def position(shares: int = 100, *, entry_minute: int = 50):
    """Filled from the 09:50 bar, recorded at 09:51 (the engine answers one tick later)."""
    return open_position("BTEST", SetupType.HOD_BREAKOUT, entry_price=ENTRY,
                         entered_at=et(*at(entry_minute - 40 + 1)), shares=shares,
                         initial_stop=STOP, entry_bar_timestamp=et(9, entry_minute))


def tape_after(*bars):
    """The setup tape, a breakout bar at 09:50, then whatever the test adds."""
    entry_bar = make_bar(9, 50, 11.05, 6000.0, open_=10.92, high=11.10, low=10.90)
    return make_tape([*breakout_bars(), entry_bar, *bars])


def advance(pos, tape, as_of):
    return advance_position(pos, tape, as_of, config=CONFIG.exit, scope=SCOPE)


# ---- stops -------------------------------------------------------------------------------

def test_a_stop_fills_at_the_stop_and_a_gap_fills_worse() -> None:
    tape = tape_after(make_bar(9, 51, 10.80, 3000.0, open_=11.00, high=11.02, low=10.70))
    update = advance(position(), tape, et(9, 52))
    assert [(e.reason, e.price, e.shares) for e in update.events] == [
        (ExitReason.HARD_STOP, STOP, 100)]
    assert not update.position.is_open

    gapped = tape_after(make_bar(9, 51, 10.55, 3000.0, open_=10.60, high=10.65, low=10.50))
    update = advance(position(), gapped, et(9, 52))
    assert update.events[0].price == 10.60  # the open, because the bar never traded at the stop


def test_the_entry_bar_itself_is_checked_against_the_stop() -> None:
    """The breakout bar that runs back through the stop is a loss, not a cancelled setup."""
    entry_bar = make_bar(9, 50, 10.70, 9000.0, open_=10.92, high=11.10, low=10.68)
    tape = make_tape([*breakout_bars(), entry_bar])
    update = advance(position(), tape, et(9, 51))
    assert [(e.reason, e.at, e.price) for e in update.events] == [
        (ExitReason.HARD_STOP, et(9, 50), STOP)]


def test_a_bar_that_reaches_the_target_and_the_stop_is_booked_as_the_stop() -> None:
    both = make_bar(9, 51, 11.20, 8000.0, open_=11.05, high=TARGET + 0.05, low=10.70)
    update = advance(position(), tape_after(both), et(9, 52))
    assert [e.reason for e in update.events] == [ExitReason.HARD_STOP]


# ---- partial and trail --------------------------------------------------------------------

def test_the_partial_sells_half_at_the_target_and_lifts_the_stop_to_breakeven() -> None:
    runner = make_bar(9, 51, 11.50, 9000.0, open_=11.06, high=TARGET + 0.02, low=11.00)
    update = advance(position(), tape_after(runner), et(9, 52))
    event = update.events[0]
    assert (event.reason, event.shares, event.closes_position) == (
        ExitReason.PARTIAL_TRAIL, 50, False)
    assert event.price == pytest.approx(TARGET)
    assert update.position.shares == 50
    assert update.position.stop == ENTRY and update.position.partial_taken


def test_after_the_partial_the_stop_trails_the_previous_bar_low() -> None:
    bars = [make_bar(9, 51, 11.50, 9000.0, open_=11.06, high=TARGET + 0.02, low=11.05),
            make_bar(9, 52, 11.60, 4000.0, open_=11.50, high=11.65, low=11.45),
            make_bar(9, 53, 11.40, 4000.0, open_=11.58, high=11.60, low=11.30)]
    update = advance(position(), tape_after(*bars), et(9, 54))
    # 09:52 lifts nothing (11.05 < breakeven 11.011 is false, so the stop becomes 11.05),
    # 09:53 trails to the 09:52 low 11.45 and that bar trades down to 11.30.
    assert [(e.reason, e.price, e.shares) for e in update.events] == [
        (ExitReason.PARTIAL_TRAIL, TARGET, 50), (ExitReason.TRAILING_STOP, 11.45, 50)]
    assert not update.position.is_open


def test_a_position_too_small_to_split_takes_the_whole_target() -> None:
    runner = make_bar(9, 51, 11.52, 9000.0, open_=11.06, high=TARGET + 0.05, low=11.00)
    update = advance(position(1), tape_after(runner), et(9, 52))
    assert [(e.reason, e.shares, e.closes_position) for e in update.events] == [
        (ExitReason.PARTIAL_TRAIL, 1, True)]
    assert not update.position.is_open


# ---- deadlines ----------------------------------------------------------------------------

def test_the_time_stop_fires_on_the_clock_at_the_last_close_before_it() -> None:
    bars = [make_bar(10, 19, 11.20, 1000.0, open_=11.18, high=11.25, low=11.10),
            make_bar(10, 25, 11.30, 1000.0, open_=11.22, high=11.35, low=11.20)]
    update = advance(position(), tape_after(*bars), et(10, 30))
    # entered_at 09:51 + 30 wall-clock minutes = 10:21; the last bar before it closed at 11.20.
    assert [(e.reason, e.at, e.price) for e in update.events] == [
        (ExitReason.TIME_STOP, et(10, 21), 11.20)]


def test_a_silent_tape_does_not_postpone_the_time_stop() -> None:
    update = advance(position(), tape_after(), et(10, 30))
    assert [(e.reason, e.at) for e in update.events] == [(ExitReason.TIME_STOP, et(10, 21))]
    assert update.events[0].price == 11.05  # the 09:50 close, the last print before the deadline


def test_the_partial_releases_the_time_stop_and_the_trail_takes_over() -> None:
    runner = make_bar(9, 51, 11.50, 9000.0, open_=11.06, high=TARGET + 0.02, low=11.00)
    quiet = [make_bar(10, 25, 11.55, 500.0, open_=11.52, high=11.60, low=11.50)]
    update = advance(position(), tape_after(runner, *quiet), et(10, 30))
    assert [e.reason for e in update.events] == [ExitReason.PARTIAL_TRAIL]
    assert update.position.is_open and update.position.shares == 50


def test_nothing_is_held_overnight() -> None:
    late = [make_bar(15, 40, 12.00, 2000.0, open_=11.90, high=12.10, low=11.80),
            make_bar(15, 54, 12.20, 2000.0, open_=12.10, high=12.25, low=12.05),
            make_bar(15, 57, 12.40, 2000.0, open_=12.30, high=12.45, low=12.25)]
    runner = make_bar(9, 51, 11.50, 9000.0, open_=11.06, high=TARGET + 0.02, low=11.00)
    update = advance(position(), tape_after(runner, *late), et(15, 58))
    closing = update.events[-1]
    assert (closing.reason, closing.at, closing.price) == (ExitReason.EOD_EXIT, et(15, 55), 12.20)
    assert not update.position.is_open


def test_the_end_of_day_outranks_the_time_stop() -> None:
    late_entry = open_position("BTEST", SetupType.HOD_BREAKOUT, entry_price=ENTRY,
                               entered_at=et(15, 50), shares=100, initial_stop=STOP,
                               entry_bar_timestamp=et(15, 49))
    tape = make_tape([*breakout_bars(),
                      make_bar(15, 49, 11.20, 3000.0, open_=11.00, high=11.30, low=11.00),
                      make_bar(15, 54, 11.25, 3000.0, open_=11.20, high=11.30, low=11.15)])
    update = advance_position(late_entry, tape, et(16, 25), config=CONFIG.exit, scope=SCOPE)
    assert [(e.reason, e.at) for e in update.events] == [(ExitReason.EOD_EXIT, et(15, 55))]


# ---- refusals and determinism ---------------------------------------------------------------

def test_a_closed_position_cannot_be_advanced_and_time_cannot_go_backwards() -> None:
    tape = tape_after(make_bar(9, 51, 10.80, 3000.0, open_=11.00, high=11.02, low=10.70))
    closed = advance(position(), tape, et(9, 52)).position
    with pytest.raises(InvalidTransition):
        advance(closed, tape, et(9, 53))
    with pytest.raises(PointInTimeViolation):
        advance(position(), tape, et(9, 49))
    with pytest.raises(PointInTimeViolation):
        open_position("BTEST", SetupType.HOD_BREAKOUT, entry_price=ENTRY, entered_at=et(9, 50),
                      shares=10, initial_stop=STOP, entry_bar_timestamp=et(9, 51))
    with pytest.raises(ValueError):
        replace(position(), stop=STOP - 0.01)


def test_the_same_tape_always_produces_the_same_events() -> None:
    bars = [make_bar(9, 51, 11.50, 9000.0, open_=11.06, high=TARGET + 0.02, low=11.05),
            make_bar(9, 52, 11.40, 4000.0, open_=11.50, high=11.55, low=11.00)]
    tape = tape_after(*bars)
    assert advance(position(), tape, et(9, 53)) == advance(position(), tape, et(9, 53))


def test_tick_by_tick_matches_one_catch_up_call() -> None:
    """A skipped tick may not change what happened: the scan is per bar, not per call."""
    bars = [make_bar(9, 51, 11.50, 9000.0, open_=11.06, high=TARGET + 0.02, low=11.05),
            make_bar(9, 52, 11.60, 4000.0, open_=11.50, high=11.65, low=11.45),
            make_bar(9, 53, 11.40, 4000.0, open_=11.58, high=11.60, low=11.30)]
    tape = tape_after(*bars)
    stepwise, events = position(), []
    for minute in (51, 52, 53, 54):
        update = advance(stepwise, tape, et(9, minute))
        events += list(update.events)
        stepwise = update.position
        if not stepwise.is_open:
            break
    at_once = advance(position(), tape, et(9, 54))
    assert events == list(at_once.events)


# ---- sizing ---------------------------------------------------------------------------------

def test_size_comes_from_the_risk_budget_and_is_capped_by_the_position_limit() -> None:
    wide = position_size(100_000.0, 11.011, 8.0, CONFIG.risk)
    assert wide.shares == 166  # 500 risk / 3.011 per share, under the 20% cap
    assert not wide.capped_by_position_limit

    tight = position_size(100_000.0, 11.011, STOP, CONFIG.risk)
    assert tight.capped_by_position_limit
    assert tight.shares == 1816  # 20,000 cap / 11.011, below the 1915 the risk budget allows
    assert tight.position_value <= 100_000.0 * CONFIG.risk.max_position_pct / 100


def test_a_budget_that_buys_no_share_refuses_instead_of_rounding_up() -> None:
    small = position_size(100.0, 11.011, 8.0, CONFIG.risk)
    assert (small.shares, small.refusal) == (0, SizeRefusal.SIZE_ZERO)
    for bad in ((0.0, 11.0, 8.0), (100.0, -1.0, 8.0), (100.0, 11.0, 0.0)):
        with pytest.raises(ValueError):
            position_size(*bad, CONFIG.risk)
    with pytest.raises(ValueError):
        position_size(100_000.0, 11.0, 11.0, CONFIG.risk)
