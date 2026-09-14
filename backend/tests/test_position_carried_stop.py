"""A carried position's stop is enforced on every completed regular bar.

The closing review decides whether a position is carried, and the end-of-day owner
promotes it to Day 2 and closes it at the Day 2 close. The minute driver used to
treat both carried phases as that owner's and skip them, so a carried position had
no stop from the 15:50 review through the Day 2 close. It now tests a carried
position's stop with the engine rule an open position meets, acts only on a breach,
and persists nothing otherwise: trailing, the high-water mark, the pyramid, Day 2
activation, and the Day 2 close all stay where they were.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.broker.domain import RejectionReason
from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession
from app.models.execution import ExecutionOrderRecord
from app.repositories.strategy import StrategyStateRepository
from app.risk.domain import AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot
from app.services.end_of_day_lifecycle import EndOfDayAction
from app.services.end_of_day_runtime import EndOfDayPositionRuntime
from app.services.position_lifecycle import (
    PositionAction, PositionLifecycleService, strategy_state_sink,
)
from app.services.position_management_runtime import PositionManagementRuntime
from app.services.simulation_runtime import clear_active_sim_broker
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.engine import StrategyReason
from app.strategy.lifecycle import (
    OvernightSuitability, StrategyBook, StrategyPhase, StrategyState,
)
from app.strategy.runner import StrategyLifecycleRunner

from backend.tests.test_position_lifecycle_hard_stop import (  # noqa: F401 - fixtures
    CASH, INITIAL_STOP, SYMBOL, activate, bar, count_submissions, enter, factory, flat,
    ownership, session_bars, stored_state,
)
from backend.tests.test_end_of_day_lifecycle import (
    CARRY_CLOSE, DAY2_OPEN, DAY2_REVIEW_AS_OF, EOD_FILL_AT, MARKET_CLOSE, Tape, carried_to_day2,
    carry_once, day2_tape, eod_tape, priced, service,
)

ET = ZoneInfo("America/New_York")
DAY2_STOP_AT = datetime(2026, 7, 6, 10, 0, tzinfo=ET)


def breach(at: datetime, *, high: float = 100.2):
    """A regular bar whose low crosses the 99 stop."""
    return bar(at, open_=100.0, high=high, low=98.5, close=99.0)


def after_breach(at: datetime):
    return bar(at, open_=98.8, high=99.0, low=98.5, close=98.9)


def day2_stop_tape():  # type: ignore[no-untyped-def]
    """Day 2 opened flat above the stop, breached at 10:00, then two bars to fill into."""
    tape = tuple(flat(DAY2_OPEN + timedelta(minutes=i), 104.0) for i in range(30))
    return tape + (breach(DAY2_STOP_AT), after_breach(DAY2_STOP_AT + timedelta(minutes=1)),
                   after_breach(DAY2_STOP_AT + timedelta(minutes=2)))


def sells(factory, status: str | None = None) -> list:  # type: ignore[no-untyped-def]
    with factory() as session:
        query = select(ExecutionOrderRecord).where(ExecutionOrderRecord.side == "SELL")
        if status is not None:
            query = query.where(ExecutionOrderRecord.status == status)
        return list(session.scalars(query))


# P-10. The open position's stop is unchanged ----------------------------------

def test_an_open_position_still_exits_on_its_stop(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    at = datetime(2026, 7, 2, 10, 0, tzinfo=ET)
    tape = session_bars(breach(at), after_breach(at + timedelta(minutes=2)))
    outcome = PositionLifecycleService(runtime).evaluate({SYMBOL: tape},
                                                        as_of=at + timedelta(minutes=1))[0]
    assert outcome.action is PositionAction.EXIT_FILLED
    assert outcome.reason == StrategyReason.INITIAL_STOP.value


# P-11, N. The rest of Day 1 after the carry ---------------------------------------

# A breach late enough that its next bar is the final one is covered, without handing
# the service a bar before it completes, in test_protective_exit_boundary.
@pytest.mark.parametrize("minute", [51, 55])
def test_a_carried_position_is_stopped_on_the_rest_of_day_one(factory, minute: int) -> None:
    runtime, _, _ = carry_once(factory)
    assert stored_state(factory).phase is StrategyPhase.OVERNIGHT_HELD
    stop_at = datetime(2026, 7, 2, 15, minute, tzinfo=ET)
    tape = eod_tape(fill=False) + (breach(stop_at), after_breach(stop_at + timedelta(minutes=2)))
    submitted = count_submissions(runtime.broker)

    outcome = PositionLifecycleService(runtime).evaluate({SYMBOL: tape},
                                                        as_of=stop_at + timedelta(minutes=1))[0]

    assert outcome.action is PositionAction.EXIT_FILLED
    assert outcome.reason == StrategyReason.INITIAL_STOP.value
    assert len(submitted) == 1
    assert outcome.order.filled_at == stop_at + timedelta(minutes=2) < MARKET_CLOSE
    assert runtime.broker.get_position(SYMBOL) is None
    assert stored_state(factory).phase is StrategyPhase.EXITED


@pytest.mark.asyncio
async def test_the_final_regular_bar_of_the_carry_day_is_still_tested(factory) -> None:
    """The 15:59 bar completes at the close, and the minute owner still reads it."""
    runtime, _, _ = carry_once(factory)
    tape = eod_tape(fill=False) + (breach(datetime(2026, 7, 2, 15, 59, tzinfo=ET)),)

    outcomes = await PositionManagementRuntime(runtime, lambda: Tape({SYMBOL: tape})).run_once(
        as_of=MARKET_CLOSE)

    # No bar of this session is left to fill it, so nothing is submitted ...
    assert [item.action for item in outcomes] == [PositionAction.EXIT_PENDING]
    assert outcomes[0].order is None and sells(factory) == []
    # ... but the breach is durable rather than dropped: the position is now leaving.
    stored = stored_state(factory)
    assert stored.phase is StrategyPhase.EXIT_SIGNALLED
    assert stored.phase_reason == StrategyReason.INITIAL_STOP.value


# P-14. A postmarket print is not a stop --------------------------------------------

@pytest.mark.asyncio
async def test_a_postmarket_print_below_the_stop_is_not_a_stop(factory) -> None:
    runtime, _, _ = carry_once(factory)
    before = stored_state(factory)
    submitted = count_submissions(runtime.broker)
    post_at = datetime(2026, 7, 2, 16, 5, tzinfo=ET)
    tape = eod_tape() + (bar(post_at, open_=99.0, high=99.2, low=97.0, close=98.0,
                             session=MarketSession.POSTMARKET),)

    outcome = PositionLifecycleService(runtime).evaluate(
        {SYMBOL: tape}, as_of=post_at + timedelta(minutes=1))[0]
    provider = Tape({SYMBOL: tape})
    owner = await PositionManagementRuntime(runtime, lambda: provider).run_once(
        as_of=post_at + timedelta(minutes=1))

    assert outcome.action is PositionAction.HOLD
    # Once the session closed, the owner's flush reads regular bars only, and every
    # one of them has already had its stop tested: nothing is decided on the print.
    assert [item.action for item in owner] == [PositionAction.SKIPPED]
    assert owner[0].reason == "already evaluated through this bar"
    assert provider.calls == [SYMBOL]
    assert submitted == []
    assert stored_state(factory) == replace(before, last_protected_bar_at=EOD_FILL_AT)


# P-12, P-13, L, P-16. Day 2 ---------------------------------------------------------

def test_day_two_is_stopped_on_a_regular_bar(factory) -> None:
    runtime, _, _ = carried_to_day2(factory)
    assert stored_state(factory).phase is StrategyPhase.DAY2_ACTIVE
    submitted = count_submissions(runtime.broker)

    outcome = PositionLifecycleService(runtime).evaluate(
        {SYMBOL: day2_stop_tape()}, as_of=DAY2_STOP_AT + timedelta(minutes=1))[0]

    assert outcome.action is PositionAction.EXIT_FILLED
    assert outcome.reason == StrategyReason.INITIAL_STOP.value
    assert len(submitted) == 1 and submitted[0].side.value == "SELL"
    assert outcome.order.filled_at == DAY2_STOP_AT + timedelta(minutes=2)
    assert runtime.broker.get_position(SYMBOL) is None
    assert stored_state(factory).phase is StrategyPhase.EXITED


def test_day_two_above_the_stop_changes_nothing_the_night_decided(factory) -> None:
    """A rally past +1R would trail an open position's stop and signal its add.

    Neither is extended to Day 2 here: the only thing tested is the stop, and the Day 2
    close is still the end-of-day owner's.
    """
    runtime, driver, _ = carried_to_day2(factory)
    before = stored_state(factory)
    rally = tuple(flat(DAY2_OPEN + timedelta(minutes=i), 106.0 + i * 0.1) for i in range(30))
    submitted = count_submissions(runtime.broker)

    outcome = PositionLifecycleService(runtime).evaluate(
        {SYMBOL: rally}, as_of=DAY2_OPEN + timedelta(minutes=30))[0]

    assert outcome.action is PositionAction.HOLD
    assert submitted == []
    assert stored_state(factory) == replace(before, last_protected_bar_at=rally[-1].timestamp)
    closing = driver.review({SYMBOL: day2_tape()}, as_of=DAY2_REVIEW_AS_OF)[0]
    assert closing.action is EndOfDayAction.EXIT_FILLED
    assert closing.reason == StrategyReason.DAY2_MAX_HOLD.value


def test_a_day_two_activated_late_still_tests_the_bar_before_its_activation(factory) -> None:
    """A restart mid-morning activates Day 2 after a bar has already completed.

    Activation stamps its own moment on the state. That stamp must not hide the bar
    that completed just before it: a carried stop is re-tested on every tick rather
    than skipped as already evaluated.
    """
    runtime, driver, _ = carry_once(factory)
    activated_at = DAY2_STOP_AT + timedelta(minutes=1, seconds=30)
    assert [item.action for item in driver.activate_day2(as_of=activated_at)] == [
        EndOfDayAction.DAY2_ACTIVATED]
    assert stored_state(factory).last_market_as_of == activated_at

    outcome = PositionLifecycleService(runtime).evaluate(
        {SYMBOL: day2_stop_tape()}, as_of=activated_at + timedelta(seconds=15))[0]

    assert outcome.action is PositionAction.EXIT_FILLED
    assert outcome.reason == StrategyReason.INITIAL_STOP.value
    assert runtime.broker.get_position(SYMBOL) is None


# M. The stop outranks every other decision, in every phase it is tested in -----------

@pytest.mark.parametrize("phase", ["POSITION_OPEN", "OVERNIGHT_HELD", "DAY2_ACTIVE"])
def test_the_stop_outranks_a_new_high_in_every_phase(factory, phase: str) -> None:
    """One bar both prints a new high far past +1R and breaches the stop: it exits."""
    if phase == "POSITION_OPEN":
        runtime = activate()
        enter(runtime, factory)
        at = datetime(2026, 7, 2, 10, 0, tzinfo=ET)
        lead = session_bars()
    elif phase == "OVERNIGHT_HELD":
        runtime, _, _ = carry_once(factory)
        at = datetime(2026, 7, 2, 15, 55, tzinfo=ET)
        lead = eod_tape(fill=False)
    else:
        runtime, _, _ = carried_to_day2(factory)
        at = DAY2_STOP_AT
        lead = tuple(flat(DAY2_OPEN + timedelta(minutes=i), 104.0) for i in range(30))
    assert stored_state(factory).phase.value == phase
    submitted = count_submissions(runtime.broker)
    tape = lead + (breach(at, high=115.0), after_breach(at + timedelta(minutes=2)))

    outcome = PositionLifecycleService(runtime).evaluate({SYMBOL: tape},
                                                        as_of=at + timedelta(minutes=1))[0]

    assert outcome.action is PositionAction.EXIT_FILLED
    assert [intent.side.value for intent in submitted] == ["SELL"]


# P-15. Early close ----------------------------------------------------------------

EARLY = date(2026, 11, 27)
EARLY_OPEN = datetime(2026, 11, 27, 9, 30, tzinfo=ET)


def early_session(*extra):  # type: ignore[no-untyped-def]
    base = [flat(EARLY_OPEN + timedelta(minutes=i), 100.0 + i * 0.05) for i in range(17)]
    base.append(bar(EARLY_OPEN + timedelta(minutes=17), open_=102.0, high=102.1, low=101.9,
                    close=102.0))
    return tuple(base) + extra


def test_an_early_close_carry_is_stopped_before_the_early_close(factory) -> None:
    runtime = activate()
    signal_at = EARLY_OPEN + timedelta(minutes=16)
    signalled = StrategyState(SYMBOL, EARLY, phase=StrategyPhase.ENTRY_SIGNALLED,
                              book=StrategyBook.ACTUAL, entry_price=Decimal("102"),
                              initial_stop=INITIAL_STOP, active_stop=INITIAL_STOP,
                              entry_trading_date=EARLY, holding_day_number=1)
    result = StrategyLifecycleRunner.for_active_runtime().execute_entry(
        state=signalled,
        decision=StrategyDecision(SYMBOL, DecisionType.ENTER, "ABOVE_VWAP_AND_OR_BREAK",
                                  signal_at, strategy_version="strategy_v0"),
        account=AccountSnapshot(CASH, CASH, Currency.USD, signal_at),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), signal_at),
        market_bars=early_session(), instrument_currency=Currency.USD, created_at=signal_at,
        actual_risk_state=DailyTradingState(EARLY), on_state=strategy_state_sink(signal_at))
    assert result.order is not None and runtime.broker.get_fills(result.order.id)
    with factory() as session:
        repository = StrategyStateRepository(session)
        repository.save(replace(repository.list_open(SYMBOL)[0],
                                overnight_suitability=OvernightSuitability.HIGH),
                        updated_at=signal_at)
        session.commit()

    close = MarketCalendar().regular_market_close(EARLY)
    review_at = datetime(2026, 11, 27, 12, 50, tzinfo=ET)
    assert close == datetime(2026, 11, 27, 13, 0, tzinfo=ET)
    assert service(runtime).review_at(EARLY) == review_at
    closing_bar = priced(datetime(2026, 11, 27, 12, 49, tzinfo=ET), CARRY_CLOSE)
    carried = service(runtime).review({SYMBOL: early_session(closing_bar)}, as_of=review_at)[0]
    assert carried.action is EndOfDayAction.CARRIED

    stop_at = datetime(2026, 11, 27, 12, 55, tzinfo=ET)
    tape = early_session(closing_bar, breach(stop_at), after_breach(stop_at + timedelta(minutes=2)))
    outcome = PositionLifecycleService(runtime).evaluate({SYMBOL: tape},
                                                        as_of=stop_at + timedelta(minutes=1))[0]

    assert outcome.action is PositionAction.EXIT_FILLED
    assert outcome.order.filled_at == stop_at + timedelta(minutes=2) < close
    assert runtime.broker.get_position(SYMBOL) is None


# P-17. Sold exactly once --------------------------------------------------------------

def test_a_stopped_carried_position_is_sold_exactly_once(factory) -> None:
    runtime, driver, _ = carried_to_day2(factory)
    minute_driver = PositionLifecycleService(runtime)
    tape = day2_stop_tape()
    assert minute_driver.evaluate({SYMBOL: tape}, as_of=DAY2_STOP_AT + timedelta(minutes=1))[0].exited
    submitted = count_submissions(runtime.broker)

    assert minute_driver.evaluate({SYMBOL: tape}, as_of=DAY2_STOP_AT + timedelta(minutes=1)) == ()
    assert minute_driver.evaluate({SYMBOL: tape}, as_of=DAY2_STOP_AT + timedelta(minutes=30)) == ()
    assert driver.review({SYMBOL: tape}, as_of=DAY2_REVIEW_AS_OF) == ()
    assert submitted == []
    assert len(sells(factory)) == 1


# P-18. A restart on Day 2 ---------------------------------------------------------

def test_a_restart_on_day_two_still_enforces_the_stop(factory) -> None:
    carried_to_day2(factory)
    clear_active_sim_broker()
    restarted = activate()

    outcome = PositionLifecycleService(restarted).evaluate(
        {SYMBOL: day2_stop_tape()}, as_of=DAY2_STOP_AT + timedelta(minutes=1))[0]

    assert outcome.action is PositionAction.EXIT_FILLED
    assert restarted.broker.get_position(SYMBOL) is None


# Q. Carry, Day 2 activation, and the stop through both runtimes ----------------------

@pytest.mark.asyncio
async def test_carry_day_two_activation_and_stop_through_both_runtimes(factory) -> None:
    runtime, _, _ = carry_once(factory)
    provider = Tape({SYMBOL: day2_stop_tape()})
    night = EndOfDayPositionRuntime(runtime, lambda: provider)
    minute_owner = PositionManagementRuntime(runtime, lambda: provider)

    activated = await night.run_once(as_of=DAY2_OPEN)
    assert [item.action for item in activated] == [EndOfDayAction.DAY2_ACTIVATED]
    actions = []
    for minute_after in (1, 2, 3):
        actions += [item.action for item in await minute_owner.run_once(
            as_of=DAY2_STOP_AT + timedelta(minutes=minute_after))]

    # The breach is read at 10:01; the next-bar rule settles it on the 10:02 bar.
    assert actions == [PositionAction.EXIT_UNFILLED, PositionAction.EXIT_UNFILLED,
                       PositionAction.EXIT_FILLED]
    assert runtime.broker.get_position(SYMBOL) is None
    assert len(sells(factory, "FILLED")) == 1
    assert stored_state(factory).phase is StrategyPhase.EXITED
