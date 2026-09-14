"""A protective exit is enforced across the close, and the closing review cannot outrun it.

A session's final regular bar completes at the close itself, so no in-session tick
ever reads it; the minute owner flushes it once the session is over. A stop that
breaches too late for any bar of its session to fill it stays durably signalled and
sells on the first regular bar of the next XNYS session - at that bar's open, with
execution costs, never at the stop price and never on the previous session's window.

From the closing review on, the end-of-day owner decides the night and the minute
driver keeps only the stop, and the review tests that same stop before it reduces or
carries anything: whichever owner reaches the 15:50 bar first, the night is the same.

Ticks land JIT after each minute boundary, the way the production loop wakes.
"""

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core import database
from app.core.config import get_settings
from app.execution.domain import OrderSide
from app.models.execution import ExecutionOrderRecord
from app.repositories.simulation import SimulationStateRepository
from app.repositories.strategy import StrategyStateRepository
from app.risk.domain import AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot
from app.services.end_of_day_lifecycle import EndOfDayAction
from app.services.end_of_day_runtime import EndOfDayPositionRuntime
from app.services.entry_management_runtime import EntryAction, EntryManagementRuntime
from app.services.position_lifecycle import (
    PositionAction, PositionLifecycleService, strategy_state_sink,
)
from app.services.position_management_runtime import (
    FINAL_FLUSH_ATTEMPTS, PositionManagementRuntime,
)
from app.services.simulation_runtime import clear_active_sim_broker
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.engine import StrategyReason
from app.strategy.lifecycle import OvernightSuitability, StrategyBook, StrategyPhase, StrategyState
from app.strategy.runner import StrategyLifecycleRunner

from backend.tests.test_position_lifecycle_hard_stop import (  # noqa: F401 - fixtures
    CASH, INITIAL_STOP, MARKET_OPEN, SYMBOL, activate, bar, factory, flat, ownership,
    session_bars, stored_state,
)
from backend.tests.test_end_of_day_lifecycle import (
    CARRY_CLOSE, DAY2_REVIEW_AS_OF, MARKET_CLOSE, REDUCE_CLOSE, TIGHT_QUANTITY, carriable,
    carried_to_day2, carry_once, day2_tape, enter_tight, eod_tape, priced, reduce_once, service,
)
from backend.tests.test_simulation_persistence_schema import REVISION, _migrated_engine
from tests.test_entry_management_runtime import seed_session
from tests.test_position_exchange_authority import (  # noqa: F401 - fixture
    ANALYSIS_DAY, CODES, OPEN, Clock, RoutingClient, daily, entry_tape, filled_sells,
    kiwoom_factory, ledger, minute, restart, steady,
)

ET = ZoneInfo("America/New_York")
JIT = timedelta(milliseconds=50)
D1 = date(2026, 7, 2)
D2 = date(2026, 7, 6)  # 2026-07-03 is the Independence Day holiday
D3 = date(2026, 7, 7)


def at(day: date, hour: int, minute_: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute_, tzinfo=ET)


class Feed:
    """A provider that honours the query window, as Kiwoom does, and counts requests."""

    def __init__(self, bars) -> None:  # type: ignore[no-untyped-def]
        self.bars = tuple(bars)
        self.calls = 0

    def get_minute_bars(self, symbols, start=None, end=None, session=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        return [item for item in self.bars if item.symbol == symbols[0]
                and (start is None or item.timestamp >= start)
                and (end is None or item.timestamp <= end)
                and (session is None or item.session is session)]


def breach(when: datetime):  # type: ignore[no-untyped-def]
    """A regular bar whose low crosses the 99 stop."""
    return bar(when, open_=100.0, high=100.2, low=98.5, close=99.0)


def flats(day: date, hour: int, minutes, price: float):  # type: ignore[no-untyped-def]
    return tuple(flat(at(day, hour, m), price) for m in minutes)


def gap_open(day: date, open_: float = 90.0, count: int = 6):  # type: ignore[no-untyped-def]
    """A session that opens at ``open_``, below every stop here, and stays there."""
    first = bar(at(day, 9, 30), open_=open_, high=open_ + 1, low=open_ - 1, close=open_ + 0.5)
    return (first,) + tuple(flat(at(day, 9, 30) + timedelta(minutes=i), open_)
                            for i in range(1, count))


def open_state(factory) -> StrategyState | None:  # type: ignore[no-untyped-def]
    with factory() as session:
        states = StrategyStateRepository(session).list_open(SYMBOL)
    return states[0] if states else None


def sell_rows(factory, status: str | None = None) -> list:  # type: ignore[no-untyped-def]
    with factory() as session:
        query = select(ExecutionOrderRecord).where(ExecutionOrderRecord.side == "SELL")
        if status is not None:
            query = query.where(ExecutionOrderRecord.status == status)
        return list(session.scalars(query))


async def actions(owner, when: datetime) -> list[PositionAction]:  # type: ignore[no-untyped-def]
    return [item.action for item in await owner.run_once(as_of=when)]


def only_sell(runtime):  # type: ignore[no-untyped-def]
    sells = [fill for fill in runtime.broker.get_fills() if fill.side is OrderSide.SELL]
    assert len(sells) == 1
    return sells[0]


# A, D, E, F, G. The final regular bar, and the next session's first bar ------------

@pytest.mark.asyncio
async def test_a_final_bar_breach_is_read_after_the_close_and_sold_at_the_next_open(factory) -> None:
    runtime, _, _ = carry_once(factory)
    feed = Feed(eod_tape() + flats(D1, 15, range(52, 59), 104.5) + (breach(at(D1, 15, 59)),)
                + gap_open(D2))
    minute_owner = PositionManagementRuntime(runtime, lambda: feed)
    night = EndOfDayPositionRuntime(runtime, lambda: feed)

    # 15:59 completes at 16:00; the tick lands after it, outside the session.
    assert await actions(minute_owner, MARKET_CLOSE + JIT) == [PositionAction.EXIT_PENDING]
    pending = open_state(factory)
    assert pending.phase is StrategyPhase.EXIT_SIGNALLED
    assert pending.phase_reason == StrategyReason.INITIAL_STOP.value
    assert pending.last_market_as_of == MARKET_CLOSE
    assert sell_rows(factory) == []

    # One flush per session: a later tick neither re-reads nor re-decides it.
    requests = feed.calls
    assert await minute_owner.run_once(as_of=MARKET_CLOSE + timedelta(minutes=1) + JIT) == ()
    assert feed.calls == requests

    # The next session: it is leaving, not carried, so Day 2 is not activated.
    assert await night.run_once(as_of=at(D2, 9, 30) + JIT) == ()
    assert await actions(minute_owner, at(D2, 9, 30) + JIT) == [PositionAction.EXIT_PENDING]
    filled = await minute_owner.run_once(as_of=at(D2, 9, 31) + JIT)

    assert [item.action for item in filled] == [PositionAction.EXIT_FILLED]
    fill = only_sell(runtime)
    # The first regular bar of the next session, at its open, costs applied adversely.
    assert fill.filled_at == at(D2, 9, 30)
    assert fill.raw_market_price == Decimal("90")
    assert fill.fill_price < Decimal("90")
    assert runtime.broker.get_position(SYMBOL) is None
    assert stored_state(factory).phase is StrategyPhase.EXITED
    # Exactly one sell was ever recorded, and it filled; no rejection was left behind.
    assert [row.status for row in sell_rows(factory)] == ["FILLED"]
    assert await minute_owner.run_once(as_of=at(D2, 9, 32) + JIT) == ()


# C, G. A late breach: the ordinary next-bar wait, then no deadlock --------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(("breach_minute", "filled_at", "waits"), [
    (57, at(D1, 15, 59), 2),   # its next bar is the final one: sold at the flush
    (58, at(D2, 9, 30), 0),    # no bar of D1 is left: sold at D2's first bar
])
async def test_a_late_breach_never_deadlocks(factory, breach_minute: int, filled_at: datetime,
                                             waits: int) -> None:
    runtime, _, _ = carry_once(factory)
    feed = Feed(eod_tape() + flats(D1, 15, range(52, breach_minute), 104.5)
                + (breach(at(D1, 15, breach_minute)),)
                + flats(D1, 15, range(breach_minute + 1, 60), 98.9) + gap_open(D2, 97.0))
    minute_owner = PositionManagementRuntime(runtime, lambda: feed)

    for later in range(breach_minute + 1, 61):
        await minute_owner.run_once(as_of=at(D1, 15, 0) + timedelta(minutes=later) + JIT)
    for tick in range(0, 4):
        await minute_owner.run_once(as_of=at(D2, 9, 30) + timedelta(minutes=tick) + JIT)

    assert runtime.broker.get_position(SYMBOL) is None
    assert only_sell(runtime).filled_at == filled_at
    assert open_state(factory) is None
    assert len(sell_rows(factory, "FILLED")) == 1
    # Rejections only ever come from the in-session next-bar wait, never from waiting
    # across the close.
    assert len(sell_rows(factory, "REJECTED")) == waits


@pytest.mark.asyncio
async def test_a_pending_protective_exit_waits_without_rejections_until_a_bar_prints(factory) -> None:
    runtime, _, _ = carry_once(factory)
    feed = Feed(eod_tape() + flats(D1, 15, range(52, 59), 104.5) + (breach(at(D1, 15, 59)),)
                + gap_open(D3, 95.0))   # D2 prints nothing at all for this symbol
    minute_owner = PositionManagementRuntime(runtime, lambda: feed)

    await minute_owner.run_once(as_of=MARKET_CLOSE + JIT)
    waited = []
    for tick in range(0, 60):
        waited += await actions(minute_owner, at(D2, 9, 30) + timedelta(minutes=tick) + JIT)

    assert set(waited) == {PositionAction.EXIT_PENDING}
    assert sell_rows(factory) == []
    assert runtime.broker.get_position(SYMBOL) is not None

    assert await actions(minute_owner, at(D3, 9, 31) + JIT) == [PositionAction.EXIT_FILLED]
    assert only_sell(runtime).filled_at == at(D3, 9, 30)
    assert [row.status for row in sell_rows(factory)] == ["FILLED"]


@pytest.mark.asyncio
async def test_the_flush_waits_a_bounded_number_of_times_for_an_unpublished_final_bar(factory) -> None:
    runtime, _, _ = carry_once(factory)
    before = open_state(factory)
    feed = Feed(eod_tape() + flats(D1, 15, range(52, 59), 104.5))   # no 15:59 bar
    minute_owner = PositionManagementRuntime(runtime, lambda: feed)

    seen = [await actions(minute_owner, MARKET_CLOSE + timedelta(minutes=later) + JIT)
            for later in range(0, FINAL_FLUSH_ATTEMPTS + 5)]

    # The first flush tests the bars no tick reached; later ones find nothing new,
    # and once the bound is spent nothing is read or decided at all.
    assert seen[0] == [PositionAction.HOLD]
    assert all(item == [PositionAction.SKIPPED] for item in seen[1:FINAL_FLUSH_ATTEMPTS])
    assert all(item == [] for item in seen[FINAL_FLUSH_ATTEMPTS:])

    assert feed.calls == FINAL_FLUSH_ATTEMPTS
    # Every bar that was published had its stop tested once; nothing else moved.
    assert open_state(factory) == replace(before, last_protected_bar_at=at(D1, 15, 58))
    assert sell_rows(factory) == []


# B, I, M. The early close -----------------------------------------------------------

EARLY = date(2026, 11, 27)
EARLY_NEXT = date(2026, 11, 30)


def carry_early(runtime, factory):  # type: ignore[no-untyped-def]
    """Enter on the 13:00 close day and let the 12:50 review carry it whole."""
    early_open = at(EARLY, 9, 30)
    base = tuple(flat(early_open + timedelta(minutes=i), 100.0 + i * 0.05) for i in range(17))
    base += (bar(early_open + timedelta(minutes=17), open_=102.0, high=102.1, low=101.9,
                 close=102.0),)
    signal_at = early_open + timedelta(minutes=16)
    signalled = StrategyState(SYMBOL, EARLY, phase=StrategyPhase.ENTRY_SIGNALLED,
                              book=StrategyBook.ACTUAL, entry_price=Decimal("102"),
                              initial_stop=INITIAL_STOP, active_stop=INITIAL_STOP,
                              entry_trading_date=EARLY, holding_day_number=1)
    StrategyLifecycleRunner.for_active_runtime().execute_entry(
        state=signalled,
        decision=StrategyDecision(SYMBOL, DecisionType.ENTER, "ABOVE_VWAP_AND_OR_BREAK",
                                  signal_at, strategy_version="strategy_v0"),
        account=AccountSnapshot(CASH, CASH, Currency.USD, signal_at),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), signal_at),
        market_bars=base, instrument_currency=Currency.USD, created_at=signal_at,
        actual_risk_state=DailyTradingState(EARLY), on_state=strategy_state_sink(signal_at))
    with factory() as session:
        repository = StrategyStateRepository(session)
        repository.save(replace(repository.list_open(SYMBOL)[0],
                                overnight_suitability=OvernightSuitability.HIGH),
                        updated_at=signal_at)
        session.commit()
    closing = priced(at(EARLY, 12, 49), CARRY_CLOSE)
    carried = service(runtime).review({SYMBOL: base + (closing,)}, as_of=at(EARLY, 12, 50))[0]
    assert carried.action is EndOfDayAction.CARRIED
    return base + (closing,)


@pytest.mark.asyncio
@pytest.mark.parametrize("breach_minute", [58, 59])
async def test_an_early_close_final_bars_are_protected(factory, breach_minute: int) -> None:
    runtime = activate()
    tape = carry_early(runtime, factory)
    feed = Feed(tape + flats(EARLY, 12, range(50, breach_minute), 104.5)
                + (breach(at(EARLY, 12, breach_minute)),)
                + flats(EARLY, 12, range(breach_minute + 1, 60), 98.9) + gap_open(EARLY_NEXT))
    minute_owner = PositionManagementRuntime(runtime, lambda: feed)

    seen = []
    for later in range(breach_minute + 1, 61):   # through the 13:00 close
        seen += await actions(minute_owner, at(EARLY, 12, 0) + timedelta(minutes=later) + JIT)
    assert seen[-1] is PositionAction.EXIT_PENDING
    assert open_state(factory).phase is StrategyPhase.EXIT_SIGNALLED
    assert sell_rows(factory) == []

    assert await actions(minute_owner, at(EARLY_NEXT, 9, 31) + JIT) == [PositionAction.EXIT_FILLED]
    assert only_sell(runtime).filled_at == at(EARLY_NEXT, 9, 30)


# N. A restart while the exit is pending, and a restart before the flush --------------

@pytest.mark.asyncio
async def test_a_restart_while_a_protective_exit_is_pending_still_sells_once(factory) -> None:
    runtime, _, _ = carry_once(factory)
    feed = Feed(eod_tape() + flats(D1, 15, range(52, 58), 104.5) + (breach(at(D1, 15, 58)),)
                + flats(D1, 15, [59], 98.9) + gap_open(D2))
    await PositionManagementRuntime(runtime, lambda: feed).run_once(as_of=at(D1, 15, 59) + JIT)
    assert open_state(factory).phase is StrategyPhase.EXIT_SIGNALLED

    clear_active_sim_broker()
    restarted = activate()
    owner = PositionManagementRuntime(restarted, lambda: feed)
    assert await actions(owner, at(D2, 9, 31) + JIT) == [PositionAction.EXIT_FILLED]

    assert restarted.broker.get_position(SYMBOL) is None
    assert only_sell(restarted).filled_at == at(D2, 9, 30)
    assert [row.status for row in sell_rows(factory)] == ["FILLED"]


@pytest.mark.asyncio
async def test_a_backend_started_before_the_open_flushes_the_session_it_missed(factory) -> None:
    runtime, _, _ = carry_once(factory)
    feed = Feed(eod_tape() + flats(D1, 15, range(52, 59), 104.5) + (breach(at(D1, 15, 59)),)
                + gap_open(D2))
    clear_active_sim_broker()   # the process was down across the close
    restarted = activate()
    owner = PositionManagementRuntime(restarted, lambda: feed)

    assert await actions(owner, at(D2, 8, 0)) == [PositionAction.EXIT_PENDING]
    assert await actions(owner, at(D2, 9, 31) + JIT) == [PositionAction.EXIT_FILLED]
    assert only_sell(restarted).filled_at == at(D2, 9, 30)


# H. A breach while a reduction is still settling ------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["position", "end_of_day"])
async def test_a_stop_during_the_overnight_review_exits_in_full_before_any_reduction(
        factory, first: str) -> None:
    runtime, _, reviewed = reduce_once(factory, tape=eod_tape(REDUCE_CLOSE, fill=False))
    assert reviewed.action is EndOfDayAction.REDUCTION_UNFILLED
    assert open_state(factory).phase is StrategyPhase.OVERNIGHT_REVIEW
    feed = Feed(eod_tape(REDUCE_CLOSE, fill=False)
                + (bar(at(D1, 15, 50), open_=103.0, high=103.1, low=100.5, close=103.0),)
                + flats(D1, 15, range(51, 60), 103.0))
    minute_owner = PositionManagementRuntime(runtime, lambda: feed)
    night = EndOfDayPositionRuntime(runtime, lambda: feed)
    owners = (minute_owner, night) if first == "position" else (night, minute_owner)

    for later in (51, 52, 53):
        for owner in owners:
            await owner.run_once(as_of=at(D1, 15, later) + JIT)

    assert runtime.broker.get_position(SYMBOL) is None
    fill = only_sell(runtime)
    assert fill.quantity == TIGHT_QUANTITY and fill.filled_at == at(D1, 15, 52)
    assert runtime.broker.get_trade(SYMBOL).exit_reason == StrategyReason.INITIAL_STOP.value
    assert stored_state(factory).phase is StrategyPhase.EXITED


def test_the_minute_driver_alone_protects_a_position_whose_reduction_is_settling(factory) -> None:
    """No end-of-day tick is needed: the stop of a pending reduction is the driver's too."""
    runtime, _, _ = reduce_once(factory, tape=eod_tape(REDUCE_CLOSE, fill=False))
    tape = (eod_tape(REDUCE_CLOSE, fill=False)
            + (bar(at(D1, 15, 50), open_=103.0, high=103.1, low=100.5, close=103.0),))

    outcome = PositionLifecycleService(runtime).evaluate(
        {SYMBOL: tape}, as_of=at(D1, 15, 51) + JIT)[0]

    assert outcome.action is PositionAction.EXIT_UNFILLED   # the ordinary next-bar wait
    signalled = open_state(factory)
    assert signalled.phase is StrategyPhase.EXIT_SIGNALLED
    assert signalled.phase_reason == StrategyReason.INITIAL_STOP.value
    assert runtime.broker.get_position(SYMBOL).quantity == TIGHT_QUANTITY


# I, J. 15:50: the same night whichever owner reaches the bar first -------------------

def new_book(tmp_path, monkeypatch, name: str):  # type: ignore[no-untyped-def]
    """A fresh durable account, exactly as the shared ``factory`` fixture builds one."""
    engine = _migrated_engine(tmp_path / f"{name}.sqlite3", monkeypatch, REVISION)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setenv("RUNTIME_PROFILE", "real_market_operator")
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "kiwoom")
    get_settings.cache_clear()
    with sessions() as session:
        SimulationStateRepository(session).create_account(
            broker_type="SIM", account_key="operator", base_currency="USD",
            initial_cash=CASH, cash=CASH, created_at=MARKET_OPEN)
        session.commit()
    return sessions, engine


async def closing_minute(sessions, *, first: str, breached: bool) -> dict:  # type: ignore[no-untyped-def]
    runtime = activate()
    enter_tight(runtime, sessions)      # stop 101, sized large enough to be trimmed
    carriable(sessions)
    closing = bar(at(D1, 15, 49), open_=102.9, high=103.0,
                  low=100.5 if breached else 102.7, close=103.0)
    feed = Feed(session_bars() + (closing,) + flats(D1, 15, range(50, 60), 103.0))
    minute_owner = PositionManagementRuntime(runtime, lambda: feed)
    night = EndOfDayPositionRuntime(runtime, lambda: feed)
    owners = (minute_owner, night) if first == "position" else (night, minute_owner)
    for later in range(50, 60):
        for owner in owners:
            await owner.run_once(as_of=at(D1, 15, later) + JIT)
    position = runtime.broker.get_position(SYMBOL)
    trade = runtime.broker.get_trade(SYMBOL)
    result = {
        "quantity": None if position is None else position.quantity,
        "fills": [(fill.side, fill.quantity, fill.filled_at, fill.fill_price)
                  for fill in runtime.broker.get_fills()],
        "exit_reason": trade.exit_reason,
        "state": stored_state(sessions),
        "orders": sorted((row.side, row.status) for row in sell_rows(sessions)),
    }
    clear_active_sim_broker()
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("breached", [True, False])
async def test_the_closing_minute_does_not_depend_on_which_owner_wakes_first(
        tmp_path, monkeypatch, breached: bool) -> None:
    results = {}
    for first in ("position", "end_of_day"):
        sessions, engine = new_book(tmp_path, monkeypatch, f"{first}-{breached}")
        try:
            results[first] = await closing_minute(sessions, first=first, breached=breached)
        finally:
            engine.dispose()

    assert results["position"] == results["end_of_day"]
    outcome = results["position"]
    if breached:
        # The stop outranks the night: the whole position leaves on the next bar.
        assert outcome["quantity"] is None
        assert outcome["exit_reason"] == StrategyReason.INITIAL_STOP.value
        assert outcome["state"].phase is StrategyPhase.EXITED
        sells = [fill for fill in outcome["fills"] if fill[0] is OrderSide.SELL]
        assert [(fill[1], fill[2]) for fill in sells] == [(TIGHT_QUANTITY, at(D1, 15, 51))]
    else:
        # No stop: the review trims to the stress cap and carries the rest, as before.
        assert outcome["state"].phase is StrategyPhase.OVERNIGHT_HELD
        assert Decimal("0") < outcome["quantity"] < TIGHT_QUANTITY
        assert outcome["state"].active_stop == Decimal("101")


# L. Day 2 max hold: Day 3 holds nothing ----------------------------------------------

def test_day_two_max_hold_leaves_nothing_for_day_three(factory) -> None:
    runtime, driver, _ = carried_to_day2(factory)
    closed = driver.review({SYMBOL: day2_tape()}, as_of=DAY2_REVIEW_AS_OF)[0]
    assert closed.action is EndOfDayAction.EXIT_FILLED
    assert closed.reason == StrategyReason.DAY2_MAX_HOLD.value
    assert runtime.broker.get_positions() == ()
    assert PositionLifecycleService(runtime).evaluate(
        {SYMBOL: gap_open(D3)}, as_of=at(D3, 9, 31) + JIT) == ()


def test_an_unfilled_day_two_close_sells_on_the_first_bar_of_day_three(factory) -> None:
    """The mandatory Day 2 close is protective: it can never wait out Day 3."""
    runtime, driver, _ = carried_to_day2(factory)
    unfilled = driver.review({SYMBOL: day2_tape(fill=False)}, as_of=DAY2_REVIEW_AS_OF)[0]
    assert unfilled.action is EndOfDayAction.EXIT_UNFILLED
    assert open_state(factory).phase_reason == StrategyReason.DAY2_MAX_HOLD.value

    minute_driver = PositionLifecycleService(runtime)
    assert [item.action for item in minute_driver.evaluate(
        {SYMBOL: ()}, as_of=at(D3, 9, 30) + JIT)] == [PositionAction.EXIT_PENDING]
    sold = minute_driver.evaluate({SYMBOL: gap_open(D3, 103.0)}, as_of=at(D3, 9, 31) + JIT)

    assert [item.action for item in sold] == [PositionAction.EXIT_FILLED]
    assert only_sell(runtime).filled_at == at(D3, 9, 30)
    assert runtime.broker.get_positions() == ()
    assert len(sell_rows(factory, "FILLED")) == 1


# O, P. Every venue: entry, carry, final-bar stop, and the next-session sell ----------

@pytest.mark.asyncio
@pytest.mark.parametrize("exchange", ["NYSE", "AMEX", "NASDAQ"])
async def test_a_final_bar_protective_exit_reads_and_sells_on_the_trades_venue(
        ledger, exchange: str) -> None:
    runtime, factory = ledger
    code = CODES[exchange]
    entry_day, next_day = OPEN.date(), date(2024, 6, 20)   # 2024-06-19 was Juneteenth
    with factory() as session:
        seed_session(session, ANALYSIS_DAY, ("ORCL",), {"ORCL": exchange})
    day_one = entry_tape("ORCL", *steady("ORCL", 104.0, OPEN + timedelta(minutes=18),
                                         until=time(15, 58)))
    day_one.append(minute("ORCL", at(entry_day, 15, 59), 99.0, low=98.0))
    day_two = [minute("ORCL", at(next_day, 9, 30), 95.0), minute("ORCL", at(next_day, 9, 31), 95.2)]
    client = RoutingClient({"ORCL": code}, {"ORCL": day_one + day_two}, {"ORCL": daily("ORCL")})
    clock = Clock(OPEN)
    shared = kiwoom_factory(client, clock)
    entry = EntryManagementRuntime(runtime, shared, clock=clock)
    night = EndOfDayPositionRuntime(runtime, shared, clock=clock)
    minute_owner = PositionManagementRuntime(runtime, shared, clock=clock)

    for when in (OPEN + timedelta(minutes=16) + JIT, OPEN + timedelta(minutes=18) + JIT):
        clock.now = when
        entered = await entry.run_once(as_of=when)
    assert [item.action for item in entered] == [EntryAction.FILLED]
    clock.now = at(entry_day, 15, 50) + JIT
    assert [item.action for item in await night.run_once(as_of=clock.now)] == [
        EndOfDayAction.CARRIED]
    clock.now = at(entry_day, 16, 0) + JIT
    assert await actions(minute_owner, clock.now) == [PositionAction.EXIT_PENDING]

    restarted = restart(runtime, factory)
    fresh = kiwoom_factory(client, clock)
    clock.now = at(next_day, 9, 31) + JIT
    sold = await PositionManagementRuntime(restarted, fresh, clock=clock).run_once(as_of=clock.now)

    assert [item.action for item in sold] == [PositionAction.EXIT_FILLED]
    assert sold[0].order.filled_at == at(next_day, 9, 30)
    assert restarted.broker.get_position("ORCL") is None
    assert len(filled_sells(factory)) == 1
    assert set(client.exchanges("ORCL")) == {code}
    assert RoutingClient.order_request_count == 0
