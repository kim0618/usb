"""The stop is tested on every completed regular bar, whatever the process missed.

Each open strategy state records the start of the last regular bar its stop was
tested on. A tick replays every completed regular bar after that cursor, oldest
first, each exactly as an on-time tick would have judged it, so a stalled tick, a
restart, a close or an open slept through never lets a breach go unseen, and the
exit sells where an on-time tick would have sold it.

Ticks land JIT after each minute boundary, the way the production loop wakes.
"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import logging

import pytest
from sqlalchemy import select

from app.broker.domain import OrderStatus
from app.core.exceptions import MarketDataError
from app.execution.domain import OrderSide
from app.market.calendar import MarketCalendar
from app.models.execution import ExecutionOrderRecord
from app.repositories.strategy import StrategyStateRepository
from app.risk.domain import (
    AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot, PositionSnapshot,
)
from app.services.end_of_day_lifecycle import EndOfDayAction
from app.services.end_of_day_runtime import EndOfDayPositionRuntime
from app.services.position_lifecycle import (
    MANDATORY_EXIT_REASONS, PROTECTIVE_EXIT_REASONS, PositionAction, PositionLifecycleService,
    is_mandatory_exit, strategy_state_sink,
)
from app.services.position_management_runtime import PositionManagementRuntime
from app.services.simulation_runtime import clear_active_sim_broker
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.engine import StrategyReason
from app.strategy.lifecycle import StrategyBook, StrategyPhase, StrategyState
from app.strategy.runner import StrategyLifecycleRunner

from backend.tests.test_position_lifecycle_hard_stop import (  # noqa: F401 - fixtures
    CASH, INITIAL_STOP, QUANTITY, SYMBOL, activate, bar, enter, factory, flat, ownership,
    session_bars, stored_state,
)
from backend.tests.test_end_of_day_lifecycle import (
    CARRY_CLOSE, DAY2_REVIEW_AS_OF, MARKET_CLOSE, REDUCE_CLOSE, TIGHT_QUANTITY, carriable,
    carried_to_day2, carry_once, eod_tape, priced, reduce_once, service,
)
from backend.tests.test_protective_exit_boundary import (
    D1, D2, D3, EARLY, JIT, Feed, actions, at, breach, carry_early, flats, gap_open,
    open_state, sell_rows,
)
from tests.test_position_exchange_authority import (  # noqa: F401 - fixture
    AS_OF as AUTHORITY_AS_OF, Clock, RoutingClient, candidate_row, kiwoom_factory, ledger,
    open_position, session_tape,
)

D4 = date(2026, 7, 8)


def whole_day(day: date, price: float):  # type: ignore[no-untyped-def]
    """Every regular minute of a full session, flat at ``price``."""
    return tuple(flat(at(day, 9, 30) + timedelta(minutes=i), price) for i in range(390))


def morning(*extra):  # type: ignore[no-untyped-def]
    """The entry session through 10:01, flat above the 99 stop and below any add."""
    return (session_bars() + flats(D1, 9, range(48, 60), 101.0) + flats(D1, 10, [0, 1], 101.0)
            + tuple(extra))


def sold(runtime) -> list:  # type: ignore[no-untyped-def]
    return [fill for fill in runtime.broker.get_fills() if fill.side is OrderSide.SELL]


def bought(runtime) -> list:  # type: ignore[no-untyped-def]
    return [fill for fill in runtime.broker.get_fills() if fill.side is OrderSide.BUY]


def buy_rows(factory) -> list:  # type: ignore[no-untyped-def]
    with factory() as session:
        return list(session.scalars(select(ExecutionOrderRecord).where(
            ExecutionOrderRecord.side == "BUY")))


def counted(runtime):  # type: ignore[no-untyped-def]
    """A lifecycle whose engine records the moment of every bar it is asked to judge."""
    lifecycle = PositionLifecycleService(runtime)
    judged: list = []
    original = lifecycle.engine.evaluate_position

    def evaluate_position(**kwargs):  # type: ignore[no-untyped-def]
        judged.append(kwargs["as_of"])
        return original(**kwargs)

    lifecycle.engine.evaluate_position = evaluate_position  # type: ignore[method-assign]
    return lifecycle, judged


def save_state(factory, state: StrategyState) -> None:  # type: ignore[no-untyped-def]
    with factory() as session:
        StrategyStateRepository(session).save(state, updated_at=at(D1, 9, 30))
        session.commit()


def second_open_state(factory, symbol: str = SYMBOL) -> None:  # type: ignore[no-untyped-def]
    """A contradiction: a second open row for a symbol one position belongs to."""
    with factory() as session:
        repository = StrategyStateRepository(session)
        state = repository.list_open(symbol)[0]
        repository.save(replace(state, trading_date=state.trading_date - timedelta(days=1)),
                        updated_at=at(D1, 9, 30))
        session.commit()


class FlakyFeed(Feed):
    """A Kiwoom-style outage for every read whose window ends inside [down_from, down_until)."""

    def __init__(self, bars, down_from, down_until) -> None:  # type: ignore[no-untyped-def]
        super().__init__(bars)
        self.down = (down_from, down_until)

    def get_minute_bars(self, symbols, start=None, end=None, session=None):  # type: ignore[no-untyped-def]
        if end is not None and self.down[0] <= end < self.down[1]:
            self.calls += 1
            raise MarketDataError("KIWOOM_TIMEOUT", "simulated outage")
        return super().get_minute_bars(symbols, start, end, session)


MISSED = pytest.mark.parametrize("missed", [(), (1,), (1, 2)],
                                 ids=["on-time", "one-missed", "two-missed"])


def ticks(first: int, count: int, missed: tuple[int, ...]) -> list[int]:
    """``count`` consecutive minutes from ``first``, less the ones the process missed."""
    return [first + offset for offset in range(count) if offset not in missed]


# P-1 to P-4. A breach on a bar no tick reached ------------------------------------

@pytest.mark.asyncio
@MISSED
async def test_an_open_position_breach_on_a_missed_bar_sells_as_on_time(factory, missed) -> None:
    runtime = activate()
    enter(runtime, factory)
    feed = Feed(morning(breach(at(D1, 10, 2)), *flats(D1, 10, [3, 4, 5, 6], 101.0)))
    owner = PositionManagementRuntime(runtime, lambda: feed)
    for minute_ in ticks(2, 4, missed):
        await owner.run_once(as_of=at(D1, 10, minute_) + JIT)

    # 10:02 breached and 10:03 recovered; whichever ticks ran, the sell is the one an
    # on-time tick makes: the first bar after the breach completed, at its open.
    assert [(fill.filled_at, fill.raw_market_price) for fill in sold(runtime)] == [
        (at(D1, 10, 4), Decimal("101"))]
    assert runtime.broker.get_trade(SYMBOL).exit_reason == StrategyReason.INITIAL_STOP.value
    assert runtime.broker.get_position(SYMBOL) is None
    assert stored_state(factory).phase is StrategyPhase.EXITED


@pytest.mark.asyncio
async def test_the_breach_is_anchored_at_its_own_bar_and_later_bars_never_reach_the_stop(
        factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    # Two breaching bars in a row: only the first one decides, and the replay stops.
    feed = Feed(morning(breach(at(D1, 10, 2)), breach(at(D1, 10, 3)),
                        *flats(D1, 10, [4, 5], 101.0)))
    owner = PositionManagementRuntime(runtime, lambda: feed)
    await owner.run_once(as_of=at(D1, 10, 2) + JIT)
    assert stored_state(factory).last_protected_bar_at == at(D1, 10, 1)   # durable cursor

    assert await actions(owner, at(D1, 10, 4) + JIT) == [PositionAction.EXIT_UNFILLED]
    signalled = open_state(factory)
    assert signalled.phase is StrategyPhase.EXIT_SIGNALLED
    assert signalled.phase_reason == StrategyReason.INITIAL_STOP.value
    assert signalled.last_protected_bar_at == at(D1, 10, 2)
    assert signalled.last_market_as_of == at(D1, 10, 3)


@pytest.mark.asyncio
async def test_a_tape_delivered_newest_first_is_still_judged_oldest_first(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    tape = morning(breach(at(D1, 10, 2)), breach(at(D1, 10, 3)), *flats(D1, 10, [4, 5], 101.0))
    feed = Feed(tuple(reversed(tape)))
    owner = PositionManagementRuntime(runtime, lambda: feed)
    await owner.run_once(as_of=at(D1, 10, 2) + JIT)
    await owner.run_once(as_of=at(D1, 10, 4) + JIT)

    signalled = open_state(factory)
    assert signalled.last_protected_bar_at == at(D1, 10, 2)
    assert signalled.last_market_as_of == at(D1, 10, 3)


@pytest.mark.asyncio
@MISSED
async def test_a_carried_position_breach_on_a_missed_bar_sells_as_on_time(factory, missed) -> None:
    runtime, _, _ = carry_once(factory)
    assert open_state(factory).phase is StrategyPhase.OVERNIGHT_HELD
    feed = Feed(eod_tape() + flats(D1, 15, [52], 104.5) + (breach(at(D1, 15, 53)),)
                + flats(D1, 15, range(54, 60), 104.5))
    owner = PositionManagementRuntime(runtime, lambda: feed)
    for minute_ in ticks(53, 4, missed):
        await owner.run_once(as_of=at(D1, 15, minute_) + JIT)

    assert [(fill.filled_at, fill.raw_market_price) for fill in sold(runtime)] == [
        (at(D1, 15, 55), Decimal("104.5"))]
    assert runtime.broker.get_position(SYMBOL) is None


@pytest.mark.asyncio
@MISSED
async def test_a_day_two_breach_on_a_missed_bar_sells_as_on_time(factory, missed) -> None:
    runtime, _, _ = carried_to_day2(factory)
    assert open_state(factory).phase is StrategyPhase.DAY2_ACTIVE
    feed = Feed(flats(D2, 9, range(30, 60), 104.0) + flats(D2, 10, [0, 1], 104.0)
                + (breach(at(D2, 10, 2)),) + flats(D2, 10, [3, 4, 5, 6], 104.0))
    owner = PositionManagementRuntime(runtime, lambda: feed)
    for minute_ in ticks(2, 4, missed):
        await owner.run_once(as_of=at(D2, 10, minute_) + JIT)

    assert [(fill.filled_at, fill.raw_market_price) for fill in sold(runtime)] == [
        (at(D2, 10, 4), Decimal("104"))]
    assert runtime.broker.get_position(SYMBOL) is None


@pytest.mark.asyncio
@MISSED
@pytest.mark.parametrize("night", [False, True], ids=["minute-only", "with-night"])
async def test_an_overnight_review_breach_on_a_missed_bar_exits_in_full(factory, missed,
                                                                       night) -> None:
    runtime, _, reviewed = reduce_once(factory, tape=eod_tape(REDUCE_CLOSE, fill=False))
    assert reviewed.action is EndOfDayAction.REDUCTION_UNFILLED
    feed = Feed(eod_tape(REDUCE_CLOSE, fill=False) + flats(D1, 15, [50], 103.0)
                + (bar(at(D1, 15, 51), open_=103.0, high=103.1, low=100.5, close=103.0),)
                + flats(D1, 15, range(52, 60), 103.0))
    owners = [PositionManagementRuntime(runtime, lambda: feed)]
    if night:
        owners.insert(0, EndOfDayPositionRuntime(runtime, lambda: feed))
    for minute_ in ticks(51, 4, missed):
        for owner in owners:
            await owner.run_once(as_of=at(D1, 15, minute_) + JIT)

    # The whole position leaves on the stop; no reduction ever sold a part of it.
    assert [(fill.quantity, fill.filled_at) for fill in sold(runtime)] == [
        (TIGHT_QUANTITY, at(D1, 15, 53))]
    assert runtime.broker.get_trade(SYMBOL).exit_reason == StrategyReason.INITIAL_STOP.value


# P-5, P-6. The last bars before a close ---------------------------------------------

@pytest.mark.asyncio
async def test_a_breach_before_the_final_bar_is_found_by_the_flush(factory) -> None:
    runtime, _, _ = carry_once(factory)
    feed = Feed(eod_tape() + flats(D1, 15, range(52, 57), 104.5) + (breach(at(D1, 15, 57)),)
                + flats(D1, 15, [58, 59], 104.5))
    owner = PositionManagementRuntime(runtime, lambda: feed)
    for minute_ in range(52, 58):
        await owner.run_once(as_of=at(D1, 15, minute_) + JIT)
    assert stored_state(factory).last_protected_bar_at == at(D1, 15, 56)

    # 15:58 and 15:59 recovered and the 15:58 and 15:59 ticks were lost: the flush
    # still tests 15:57 first, and sells on 15:59 exactly as an on-time tick would.
    assert await actions(owner, MARKET_CLOSE + JIT) == [PositionAction.EXIT_FILLED]
    assert [fill.filled_at for fill in sold(runtime)] == [at(D1, 15, 59)]


@pytest.mark.asyncio
async def test_an_early_close_breach_before_the_final_bar_is_found_by_the_flush(factory) -> None:
    runtime = activate()
    tape = carry_early(runtime, factory)
    feed = Feed(tape + flats(EARLY, 12, range(50, 57), 104.5) + (breach(at(EARLY, 12, 57)),)
                + flats(EARLY, 12, [58, 59], 104.5))
    owner = PositionManagementRuntime(runtime, lambda: feed)
    for minute_ in range(51, 58):
        await owner.run_once(as_of=at(EARLY, 12, minute_) + JIT)

    assert await actions(owner, at(EARLY, 13, 0) + JIT) == [PositionAction.EXIT_FILLED]
    assert [fill.filled_at for fill in sold(runtime)] == [at(EARLY, 12, 59)]


# P-7, P-9. A restart resumes from the durable cursor --------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("breached", [True, False])
async def test_a_restart_replays_exactly_the_bars_the_last_process_left(factory, breached) -> None:
    runtime = activate()
    enter(runtime, factory)
    second = breach(at(D1, 10, 2)) if breached else flat(at(D1, 10, 2), 101.0)
    feed = Feed(morning(second, *flats(D1, 10, [3, 4, 5], 101.0)))
    await PositionManagementRuntime(runtime, lambda: feed).run_once(as_of=at(D1, 10, 2) + JIT)
    assert stored_state(factory).last_protected_bar_at == at(D1, 10, 1)

    clear_active_sim_broker()                      # down from 10:02 to 10:05
    restarted = activate()
    lifecycle, judged = counted(restarted)
    owner = PositionManagementRuntime(restarted, lambda: feed, lifecycle=lifecycle)
    outcome = await actions(owner, at(D1, 10, 5) + JIT)

    if breached:
        assert outcome == [PositionAction.EXIT_FILLED]
        assert judged == [at(D1, 10, 3)]           # 10:02 decided; 10:03-10:04 never judged
        assert [fill.filled_at for fill in sold(restarted)] == [at(D1, 10, 4)]
    else:
        assert outcome == [PositionAction.HOLD]
        assert judged == [at(D1, 10, 3), at(D1, 10, 4), at(D1, 10, 5)]
        assert stored_state(factory).last_protected_bar_at == at(D1, 10, 4)
        assert restarted.broker.get_position(SYMBOL).quantity == QUANTITY


@pytest.mark.asyncio
async def test_a_catch_up_is_logged_once_with_its_cursor_and_breach(factory, caplog,
                                                                    monkeypatch) -> None:
    # An in-process alembic run elsewhere in the suite disables existing loggers;
    # the production process configures logging once and never does.
    monkeypatch.setattr(logging.getLogger("app.services.position_lifecycle"), "disabled", False)
    runtime = activate()
    enter(runtime, factory)
    feed = Feed(morning(breach(at(D1, 10, 2)), *flats(D1, 10, [3, 4, 5], 101.0)))
    owner = PositionManagementRuntime(runtime, lambda: feed)
    await owner.run_once(as_of=at(D1, 10, 2) + JIT)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.services.position_lifecycle"):
        await owner.run_once(as_of=at(D1, 10, 5) + JIT)

    lines = [record.getMessage() for record in caplog.records if "CATCH-UP" in record.getMessage()]
    assert len(lines) == 1
    assert f"cursor={at(D1, 10, 1).isoformat()}" in lines[0]
    assert "bars=1" in lines[0] and f"through={at(D1, 10, 2).isoformat()}" in lines[0]
    assert "breach=INITIAL_STOP" in lines[0]
    # An on-time tick that judges one bar and holds writes nothing at INFO.
    caplog.clear()
    held = Feed(morning(*flats(D1, 10, [2, 3], 101.0)))
    clear_active_sim_broker()
    quiet = PositionManagementRuntime(activate(), lambda: held)
    with caplog.at_level(logging.INFO, logger="app.services.position_lifecycle"):
        await quiet.run_once(as_of=at(D1, 10, 5) + JIT)
    assert not [record for record in caplog.records if "CATCH-UP" in record.getMessage()]


# P-10. The same bars twice ------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_same_bars_twice_sell_nothing_twice(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    tape = morning(breach(at(D1, 10, 2)), *flats(D1, 10, [3, 4, 5], 101.0))
    feed = Feed(tape + tape)                       # every bar published twice
    owner = PositionManagementRuntime(runtime, lambda: feed)
    for minute_ in (2, 5, 5, 6):
        await owner.run_once(as_of=at(D1, 10, minute_) + JIT)

    assert len(sold(runtime)) == 1
    assert [row.status for row in sell_rows(factory)] == ["FILLED"]
    assert runtime.broker.get_position(SYMBOL) is None


@pytest.mark.asyncio
async def test_two_different_bars_for_one_minute_are_never_guessed_between(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    feed = Feed(morning(flat(at(D1, 10, 2), 101.0), breach(at(D1, 10, 2)),
                        *flats(D1, 10, [3, 4], 101.0)))
    owner = PositionManagementRuntime(runtime, lambda: feed)
    outcome = (await owner.run_once(as_of=at(D1, 10, 4) + JIT))[0]

    assert outcome.action is PositionAction.SKIPPED
    assert "conflicting" in outcome.reason
    assert sell_rows(factory) == [] and runtime.broker.get_position(SYMBOL) is not None


# Q. Downtime across the close, the night, and the next open ------------------------

RESTARTS = pytest.mark.parametrize("restart_at", [
    at(D1, 16, 30), at(D2, 8, 0), at(D2, 10, 0) + JIT], ids=["16:30", "D2-08:00", "D2-10:00"])


def downtime_tape(breached: bool):  # type: ignore[no-untyped-def]
    fifty_seven = breach(at(D1, 15, 57)) if breached else flat(at(D1, 15, 57), 104.5)
    return (eod_tape() + flats(D1, 15, range(52, 57), 104.5) + (fifty_seven,)
            + flats(D1, 15, [58, 59], 104.5) + whole_day(D2, 104.5))


async def down_at_1555(factory, feed):  # type: ignore[no-untyped-def]
    runtime, _, _ = carry_once(factory)
    owner = PositionManagementRuntime(runtime, lambda: feed)
    for minute_ in range(52, 56):
        await owner.run_once(as_of=at(D1, 15, minute_) + JIT)
    assert stored_state(factory).last_protected_bar_at == at(D1, 15, 54)
    clear_active_sim_broker()
    return activate()


@pytest.mark.asyncio
@RESTARTS
async def test_a_breach_during_downtime_is_found_and_sold_where_on_time_would(
        factory, restart_at) -> None:
    feed = Feed(downtime_tape(breached=True))
    restarted = await down_at_1555(factory, feed)
    night = EndOfDayPositionRuntime(restarted, lambda: feed)
    minute_owner = PositionManagementRuntime(restarted, lambda: feed)

    await night.run_once(as_of=restart_at)         # the night owner may wake first
    await minute_owner.run_once(as_of=restart_at)
    await night.run_once(as_of=at(D2, 9, 31) + JIT)
    await minute_owner.run_once(as_of=at(D2, 9, 31) + JIT)

    # 15:57 breached while the process was down; wherever it restarted, the sell is
    # the one an on-time tick would have made: D1's 15:59 bar, at its open.
    assert [(fill.filled_at, fill.raw_market_price) for fill in sold(restarted)] == [
        (at(D1, 15, 59), Decimal("104.5"))]
    assert restarted.broker.get_trade(SYMBOL).exit_reason == StrategyReason.INITIAL_STOP.value
    assert [row.status for row in sell_rows(factory)] == ["FILLED"]
    assert restarted.broker.get_position(SYMBOL) is None


@pytest.mark.asyncio
@RESTARTS
async def test_downtime_without_a_breach_tests_every_missed_bar_once(factory, restart_at) -> None:
    feed = Feed(downtime_tape(breached=False))
    restarted = await down_at_1555(factory, feed)
    lifecycle, judged = counted(restarted)
    night = EndOfDayPositionRuntime(restarted, lambda: feed)
    minute_owner = PositionManagementRuntime(restarted, lambda: feed, lifecycle=lifecycle)

    await night.run_once(as_of=restart_at)
    assert await actions(minute_owner, restart_at) == [PositionAction.HOLD]

    stored = stored_state(factory)
    if restart_at.date() == D1 or restart_at.hour < 9:
        # The closed session, 15:55 through its final bar, each judged once.
        assert judged == [at(D1, 15, 55) + timedelta(minutes=k) for k in range(1, 6)]
        assert stored.last_protected_bar_at == at(D1, 15, 59)
        assert stored.phase is StrategyPhase.OVERNIGHT_HELD
    else:
        # D1's remaining bars first, then D2's, oldest to newest, each once.
        assert judged[:5] == [at(D1, 15, 55) + timedelta(minutes=k) for k in range(1, 6)]
        assert judged[5:] == [at(D2, 9, 31) + timedelta(minutes=i) for i in range(30)]
        assert stored.last_protected_bar_at == at(D2, 9, 59)
        assert stored.phase is StrategyPhase.DAY2_ACTIVE
    assert restarted.broker.get_position(SYMBOL) is not None and sell_rows(factory) == []


@pytest.mark.asyncio
async def test_a_final_bar_already_flushed_is_not_judged_again_after_a_restart(factory) -> None:
    runtime, _, _ = carry_once(factory)
    feed = Feed(downtime_tape(breached=False))
    owner = PositionManagementRuntime(runtime, lambda: feed)
    await owner.run_once(as_of=MARKET_CLOSE + JIT)
    assert stored_state(factory).last_protected_bar_at == at(D1, 15, 59)

    clear_active_sim_broker()
    restarted = activate()
    lifecycle, judged = counted(restarted)
    again = PositionManagementRuntime(restarted, lambda: feed, lifecycle=lifecycle)
    outcome = await again.run_once(as_of=at(D2, 8, 0))

    assert [item.reason for item in outcome] == ["already evaluated through this bar"]
    assert judged == []


# R. API and evaluation cost -----------------------------------------------------------

@pytest.mark.asyncio
async def test_one_read_per_tick_and_only_new_bars_are_judged(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    feed = Feed(morning(*flats(D1, 10, range(2, 31), 101.0)))
    lifecycle, judged = counted(runtime)
    owner = PositionManagementRuntime(runtime, lambda: feed, lifecycle=lifecycle)

    per_tick = []
    for minute_ in [0, 1, 2, 3, 4, 5, 9, 10]:     # 10:06 through 10:08 were missed
        before_reads, before_judged = feed.calls, len(judged)
        await owner.run_once(as_of=at(D1, 10, minute_) + JIT)
        per_tick.append((feed.calls - before_reads, len(judged) - before_judged))

    # The first tick catches up the morning since the fill; every on-time tick after
    # it judges exactly the one bar that completed; a late tick judges the ones missed.
    assert per_tick[0] == (1, 13)
    assert per_tick[1:6] == [(1, 1)] * 5
    assert per_tick[6] == (1, 4)
    assert per_tick[7] == (1, 1)


# N. Which exits may cross a close ----------------------------------------------------

def test_the_mandatory_exits_are_the_protective_family_and_a_refused_carry() -> None:
    assert PROTECTIVE_EXIT_REASONS == {StrategyReason.INITIAL_STOP.value,
                                       StrategyReason.TRAILING_STOP.value,
                                       StrategyReason.DAY2_MAX_HOLD.value}
    assert MANDATORY_EXIT_REASONS == PROTECTIVE_EXIT_REASONS | {
        StrategyReason.OVERNIGHT_REJECTED.value}


def leaving(factory, reason: str | None) -> StrategyState:  # type: ignore[no-untyped-def]
    """A carried position signalled out after the last bar of D1 could fill it."""
    state = open_state(factory)
    signalled = state.transition(StrategyPhase.EXIT_SIGNALLED, phase_reason=reason,
                                 last_market_as_of=at(D1, 15, 59) + timedelta(seconds=30))
    save_state(factory, signalled)
    return signalled


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", sorted(MANDATORY_EXIT_REASONS))
async def test_a_mandatory_exit_left_by_the_close_sells_on_the_next_open(factory, reason) -> None:
    runtime, _, _ = carry_once(factory)
    assert is_mandatory_exit(leaving(factory, reason))
    feed = Feed(eod_tape() + flats(D1, 15, range(52, 60), 104.5) + gap_open(D2, 97.0))
    owner = PositionManagementRuntime(runtime, lambda: feed)

    assert await actions(owner, MARKET_CLOSE + JIT) == [PositionAction.EXIT_PENDING]
    assert await actions(owner, at(D2, 9, 31) + JIT) == [PositionAction.EXIT_FILLED]
    assert [fill.filled_at for fill in sold(runtime)] == [at(D2, 9, 30)]
    assert [row.status for row in sell_rows(factory)] == ["FILLED"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [None, StrategyReason.DAY2_NEGATIVE_CATALYST.value,
                                    StrategyReason.PYRAMID_CONFIRMATION.value],
                         ids=["reasonless", "catalyst", "ordinary"])
async def test_an_ordinary_or_reasonless_exit_never_fills_after_its_session(factory, reason) -> None:
    runtime, _, _ = carry_once(factory)
    assert not is_mandatory_exit(leaving(factory, reason))
    feed = Feed(eod_tape() + flats(D1, 15, range(52, 60), 104.5) + gap_open(D2, 97.0, count=10))
    owner = PositionManagementRuntime(runtime, lambda: feed)
    for minute_ in range(1, 10):
        await owner.run_once(as_of=at(D2, 9, 30) + timedelta(minutes=minute_) + JIT)

    # A signal is never read as mandatory for want of a reason: it keeps its session.
    assert sold(runtime) == []
    assert runtime.broker.get_position(SYMBOL).quantity == QUANTITY
    assert {row.status for row in sell_rows(factory)} <= {"REJECTED"}


def test_an_entry_signalled_in_the_final_minute_never_fills_in_the_next_session(factory) -> None:
    runtime = activate()
    signal_at = at(D1, 15, 59) + timedelta(seconds=30)
    state = StrategyState(SYMBOL, D1, phase=StrategyPhase.ENTRY_SIGNALLED, book=StrategyBook.ACTUAL,
                          entry_price=Decimal("102"), initial_stop=INITIAL_STOP,
                          active_stop=INITIAL_STOP, entry_trading_date=D1, holding_day_number=1)
    result = StrategyLifecycleRunner.for_active_runtime().execute_entry(
        state=state,
        decision=StrategyDecision(SYMBOL, DecisionType.ENTER, "ABOVE_VWAP_AND_OR_BREAK",
                                  signal_at, strategy_version="strategy_v0"),
        account=AccountSnapshot(CASH, CASH, Currency.USD, signal_at),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), signal_at),
        market_bars=gap_open(D2, 102.0), instrument_currency=Currency.USD, created_at=signal_at,
        actual_risk_state=DailyTradingState(D1), on_state=strategy_state_sink(signal_at))

    assert result.order is not None and result.order.status is OrderStatus.REJECTED
    assert runtime.broker.get_position(SYMBOL) is None


@pytest.mark.asyncio
async def test_an_add_signalled_on_day_one_is_never_bought_on_day_two(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    state = open_state(factory)
    save_state(factory, replace(state, add_signal_issued=True, last_market_as_of=at(D1, 15, 40),
                                last_protected_bar_at=at(D1, 15, 39)))
    feed = Feed(whole_day(D2, 106.0))              # D1's closing review was lost
    owner = PositionManagementRuntime(runtime, lambda: feed)
    seen = []
    for minute_ in range(31, 41):
        seen += await actions(owner, at(D2, 9, minute_) + JIT)

    # Day 2 belongs to the night: the add is not even attempted, let alone bought.
    assert not {PositionAction.ADD_FILLED, PositionAction.ADD_UNFILLED} & set(seen)
    assert len(bought(runtime)) == 1               # the entry, and nothing else
    assert [row.status for row in buy_rows(factory)] == ["FILLED"]


# O. Two open states for one symbol: every owner refuses to pick one ----------------

@pytest.mark.asyncio
async def test_the_minute_driver_refuses_two_open_states(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    second_open_state(factory)
    feed = Feed(morning(breach(at(D1, 10, 2)), *flats(D1, 10, [3, 4], 101.0)))
    outcome = (await PositionManagementRuntime(runtime, lambda: feed).run_once(
        as_of=at(D1, 10, 4) + JIT))[0]

    assert outcome.action is PositionAction.SKIPPED and "2 open strategy states" in outcome.reason
    assert sell_rows(factory) == []


def test_the_read_window_refuses_to_pick_between_two_open_states(factory) -> None:
    """Two open rows are not one cursor: the read falls back to the session's own open."""
    runtime = activate()
    enter(runtime, factory)                 # never protected: its cursor is the D1 fill
    second_open_state(factory)
    window = MarketCalendar().session(D2)

    assert PositionLifecycleService(runtime).fetch_start(SYMBOL, window) == window.market_open


@pytest.mark.asyncio
async def test_the_final_flush_refuses_two_open_states(factory) -> None:
    runtime, _, _ = carry_once(factory)
    second_open_state(factory)
    feed = Feed(eod_tape() + flats(D1, 15, range(52, 59), 104.5) + (breach(at(D1, 15, 59)),))
    outcome = await PositionManagementRuntime(runtime, lambda: feed).run_once(
        as_of=MARKET_CLOSE + JIT)

    assert [item.action for item in outcome] == [PositionAction.SKIPPED]
    assert "2 open strategy states" in outcome[0].reason
    assert sell_rows(factory) == []


def test_the_night_owner_refuses_two_open_states(factory) -> None:
    runtime, driver, _ = carry_once(factory)
    second_open_state(factory)
    reviewed = driver.review({SYMBOL: eod_tape()}, as_of=at(D1, 15, 51))[0]

    assert reviewed.action is EndOfDayAction.SKIPPED
    assert reviewed.reason == "no single open strategy state"
    assert driver.activate_day2(as_of=at(D2, 9, 31)) == ()
    # Past every holding deadline, a contradiction is still refused, never resolved.
    assert driver.enforce_holding_limit(as_of=at(D4, 9, 0)) == ()
    assert driver.overdue_reductions(as_of=at(D2, 9, 31)) == frozenset()
    assert sell_rows(factory) == []


@pytest.mark.asyncio
async def test_two_open_states_leave_the_exchange_authority_ambiguous(ledger) -> None:
    runtime, factory = ledger
    open_position(runtime, "ORCL", candidate_row(factory, "ORCL", "NYSE"))
    second_open_state(factory, "ORCL")
    client = RoutingClient({"ORCL": "NY"}, {"ORCL": session_tape("ORCL")})
    clock = Clock(AUTHORITY_AS_OF)

    outcome = await PositionManagementRuntime(runtime, kiwoom_factory(client, clock),
                                              clock=clock).run_once(as_of=AUTHORITY_AS_OF)

    assert [item.action for item in outcome] == [PositionAction.PROTECTION_UNAVAILABLE]
    assert "EXCHANGE_AUTHORITY_MISSING" in outcome[0].reason
    assert client.queries == []                    # no venue was guessed


# J. Exits that are not stops, across the close ----------------------------------------

@pytest.mark.asyncio
async def test_a_carry_refused_in_the_final_minute_sells_on_the_next_open(factory) -> None:
    runtime = activate()
    enter(runtime, factory)                        # UNKNOWN suitability: the carry is refused
    tape = (session_bars() + flats(D1, 15, range(40, 60), 101.0)
            + gap_open(D2, 90.0, count=10))
    feed = FlakyFeed(tape, at(D1, 15, 50), at(D1, 15, 59))   # the review is late
    minute_owner = PositionManagementRuntime(runtime, lambda: feed)
    night = EndOfDayPositionRuntime(runtime, lambda: feed)
    for minute_ in range(50, 60):
        for owner in (night, minute_owner):
            await owner.run_once(as_of=at(D1, 15, minute_) + JIT)
    assert open_state(factory).phase_reason == StrategyReason.OVERNIGHT_REJECTED.value
    await minute_owner.run_once(as_of=MARKET_CLOSE + JIT)
    for minute_ in (30, 31):
        for owner in (night, minute_owner):
            await owner.run_once(as_of=at(D2, 9, minute_) + JIT)

    assert [(fill.filled_at, fill.raw_market_price) for fill in sold(runtime)] == [
        (at(D2, 9, 30), Decimal("90"))]
    assert runtime.broker.get_position(SYMBOL) is None
    # Only the in-session next-bar waits were rejected; nothing waited across the close.
    assert len(sell_rows(factory, "REJECTED")) <= 2


@pytest.mark.asyncio
async def test_a_carry_risk_refused_outright_is_mandatory_across_the_close(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    state = open_state(factory)
    at_review = at(D1, 15, 59) + timedelta(seconds=30)
    position = runtime.broker.get_position(SYMBOL)
    snapshot = PositionSnapshot(SYMBOL, position.quantity, position.average_price,
                                Decimal("101"), Currency.USD, initial_stop=state.initial_stop,
                                active_stop=state.active_stop,
                                base_notional_account_ccy=position.cost_basis)
    carried = PositionSnapshot("OTHER", Decimal("1"), Decimal("10"), Decimal("10"),
                               Currency.USD, initial_stop=Decimal("9"),
                               base_notional_account_ccy=Decimal("10"), overnight=True)
    result = StrategyLifecycleRunner.for_active_runtime().apply_overnight_risk(
        state=state,
        decision=StrategyDecision(SYMBOL, DecisionType.OVERNIGHT_HOLD, "OVERNIGHT_HOLD",
                                  at_review, strategy_version="strategy_v0"),
        account=AccountSnapshot(CASH, runtime.broker.cash, Currency.USD, at_review),
        portfolio=PortfolioSnapshot((snapshot, carried), position.cost_basis, Decimal("0"),
                                    at_review),
        position=snapshot, market_bars=session_bars(), created_at=at_review,
        on_state=strategy_state_sink(at_review))

    # The overnight slot is taken, so risk refuses the carry and the whole position
    # must leave - recorded as the mandatory exit it is, not as a reasonless one.
    leaving_state = open_state(factory)
    assert leaving_state.phase is StrategyPhase.EXIT_SIGNALLED
    assert leaving_state.phase_reason == StrategyReason.OVERNIGHT_REJECTED.value
    assert result.order is not None and result.order.status is OrderStatus.REJECTED

    feed = Feed(gap_open(D2, 95.0))
    assert await actions(PositionManagementRuntime(runtime, lambda: feed),
                         at(D2, 9, 31) + JIT) == [PositionAction.EXIT_FILLED]
    assert [fill.filled_at for fill in sold(runtime)] == [at(D2, 9, 30)]


@pytest.mark.asyncio
async def test_a_reduction_decided_in_the_final_minute_settles_on_the_next_open(factory) -> None:
    runtime, _, reviewed = reduce_once(factory, tape=eod_tape(REDUCE_CLOSE, fill=False),
                                       as_of=at(D1, 15, 59) + JIT)
    assert reviewed.action is EndOfDayAction.REDUCTION_UNFILLED
    feed = Feed(eod_tape(REDUCE_CLOSE, fill=False) + flats(D1, 15, range(50, 60), 103.0)
                + whole_day(D2, 103.0) + whole_day(D3, 103.0))
    minute_owner = PositionManagementRuntime(runtime, lambda: feed)
    night = EndOfDayPositionRuntime(runtime, lambda: feed)
    await minute_owner.run_once(as_of=MARKET_CLOSE + JIT)
    for minute_ in (30, 31, 32):
        for owner in (night, minute_owner):
            await owner.run_once(as_of=at(D2, 9, minute_) + JIT)

    trimmed = sold(runtime)
    assert [fill.filled_at for fill in trimmed] == [at(D2, 9, 30)]
    assert Decimal("0") < runtime.broker.get_position(SYMBOL).quantity < TIGHT_QUANTITY
    assert open_state(factory).phase is StrategyPhase.DAY2_ACTIVE

    for minute_ in (50, 51, 52):
        for owner in (night, minute_owner):
            await owner.run_once(as_of=at(D2, 15, minute_) + JIT)
    assert runtime.broker.get_position(SYMBOL) is None
    assert runtime.broker.get_trade(SYMBOL).exit_reason == StrategyReason.DAY2_MAX_HOLD.value


# K / M. Day 2 is the last day, whatever the process missed --------------------------

def test_a_day_one_whose_review_was_lost_is_closed_at_the_day_two_review(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    carriable(factory)
    driver = service(runtime)
    assert driver.activate_day2(as_of=at(D2, 9, 31)) == ()   # never carried, so never activated
    tape = (tuple(flat(at(D2, 9, 30) + timedelta(minutes=i), 100.0 + i * 0.05) for i in range(17))
            + (priced(at(D2, 15, 49), CARRY_CLOSE), priced(at(D2, 15, 51), CARRY_CLOSE)))

    closed = driver.review({SYMBOL: tape}, as_of=DAY2_REVIEW_AS_OF)[0]

    # Held since D1, today is holding day 2 on the exchange calendar: it may not carry.
    assert closed.action is EndOfDayAction.EXIT_FILLED
    assert closed.reason == StrategyReason.DAY2_MAX_HOLD.value
    assert runtime.broker.get_position(SYMBOL) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("woke_at", [at(D2, 16, 1), at(D3, 8, 0)], ids=["D2-after-close", "D3-premarket"])
async def test_a_day_two_close_the_review_never_reached_sells_on_day_three_first_bar(
        factory, woke_at) -> None:
    runtime, _, _ = carried_to_day2(factory)
    feed = Feed(whole_day(D2, 104.0) + gap_open(D3, 103.0))
    night = EndOfDayPositionRuntime(runtime, lambda: feed)

    signalled = await night.run_once(as_of=woke_at)
    assert [(item.action, item.reason) for item in signalled] == [
        (EndOfDayAction.MAX_HOLD_SIGNALLED, StrategyReason.DAY2_MAX_HOLD.value)]
    assert await actions(PositionManagementRuntime(runtime, lambda: feed),
                         at(D3, 9, 31) + JIT) == [PositionAction.EXIT_FILLED]
    assert [fill.filled_at for fill in sold(runtime)] == [at(D3, 9, 30)]
    assert runtime.broker.get_position(SYMBOL) is None


@pytest.mark.asyncio
async def test_a_day_two_review_starved_of_data_still_closes_on_day_two(factory) -> None:
    runtime, _, _ = carried_to_day2(factory)
    feed = FlakyFeed(whole_day(D2, 104.0), at(D2, 15, 50), at(D2, 15, 52))
    night = EndOfDayPositionRuntime(runtime, lambda: feed)
    minute_owner = PositionManagementRuntime(runtime, lambda: feed)

    outcome = await night.run_once(as_of=at(D2, 15, 50) + JIT)
    assert EndOfDayAction.MAX_HOLD_SIGNALLED in [item.action for item in outcome]
    await minute_owner.run_once(as_of=at(D2, 15, 51) + JIT)
    assert await actions(minute_owner, at(D2, 15, 52) + JIT) == [PositionAction.EXIT_FILLED]
    assert [fill.filled_at for fill in sold(runtime)] == [at(D2, 15, 51)]
