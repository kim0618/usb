"""P1-G4B: an open position is reviewed before the close and carried, trimmed, or sold.

The entry here is the deterministic P1-F one (reference 102, stop 99, fill 102.153),
so a carry and a reduction can be read against a known starting point. A second,
tighter-stopped entry is used wherever a position has to be large enough for the
overnight stress cap to bite; nothing about it is arbitrary, because risk sizes it
from the same 1R the strategy always uses.

Every figure asserted about a sell comes from the broker or the durable row. This
module never re-derives a fill price, a PnL, or a stress limit: a test that
recomputes the formula only proves the formula equals itself.
"""

import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.broker.domain import OrderStatus, RejectionReason, TradeStatus
from app.execution.domain import IntentType, OrderSide
from app.integrations.kiwoom.client import KiwoomMarketDataClient
from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession, MinuteBar
from app.models.execution import ExecutionOrderRecord
from app.models.simulation import AccountDailyPerformanceRecord, SimulationPositionRecord, SimulationTradeRecord
from app.repositories.simulation import SimulationStateRepository
from app.repositories.strategy import StrategyStateRepository
from app.risk.config import RiskConfig
from app.risk.domain import (
    AccountSnapshot, Currency, DailyTradingState, OvernightAction, PortfolioSnapshot,
    PositionSnapshot,
)
from app.risk.engine import RiskEngine
from app.services.end_of_day_lifecycle import (
    EndOfDayAction, EndOfDayLifecycleService,
)
from app.services.end_of_day_runtime import (
    EndOfDayPositionRuntime, get_end_of_day_runtime, start_end_of_day_runtime,
    stop_end_of_day_runtime,
)
from app.main import create_app
from app.market import factory as market_factory
from app.services.position_lifecycle import PositionAction, PositionLifecycleService
from app.services.position_management_runtime import get_position_management_runtime
from app.services.simulation_runtime import clear_active_sim_broker, get_active_sim_broker
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.engine import StrategyReason, StrategyV0Engine
from app.strategy.lifecycle import (
    OvernightSuitability, StrategyBook, StrategyPhase, StrategyState, TrailingProfile,
)
from app.strategy.runner import StrategyLifecycleRunner

from backend.tests.test_position_lifecycle_hard_stop import (
    CASH,
    DAY,
    ENTRY_CASH,
    ENTRY_FILL,
    ENTRY_REFERENCE,
    INITIAL_STOP,
    MARKET_OPEN,
    QUANTITY,
    SIGNAL_AT,
    SYMBOL,
    activate,
    bar,
    count_submissions,
    counts,
    enter,
    factory,
    flat,
    ownership,
    open_session,
    session_bars,
    stored_state,
)

ET = ZoneInfo("America/New_York")

# The exchange's own close, less the configured ten-minute review window. No
# wall-clock time is written down in the implementation; these are what the
# calendar and the strategy config produce together.
MARKET_CLOSE = datetime(2026, 7, 2, 16, tzinfo=ET)
REVIEW_AS_OF = datetime(2026, 7, 2, 15, 50, tzinfo=ET)
CLOSING_BAR_AT = datetime(2026, 7, 2, 15, 49, tzinfo=ET)
EOD_FILL_AT = datetime(2026, 7, 2, 15, 51, tzinfo=ET)
LATE_BAR_AT = datetime(2026, 7, 2, 15, 55, tzinfo=ET)
LATE_AS_OF = datetime(2026, 7, 2, 15, 56, tzinfo=ET)
POSTMARKET_BAR_AT = datetime(2026, 7, 2, 16, 5, tzinfo=ET)

# 2026-07-04 falls on a Saturday, so Independence Day is observed on Friday the 3rd
# and the next XNYS session after entry day is Monday the 6th.
HOLIDAY_AS_OF = datetime(2026, 7, 3, 15, 50, tzinfo=ET)
SATURDAY_AS_OF = datetime(2026, 7, 4, 15, 50, tzinfo=ET)
DAY2 = date(2026, 7, 6)
DAY2_OPEN = datetime(2026, 7, 6, 9, 30, tzinfo=ET)
DAY2_REVIEW_AS_OF = datetime(2026, 7, 6, 15, 50, tzinfo=ET)
DAY2_CLOSING_BAR_AT = datetime(2026, 7, 6, 15, 49, tzinfo=ET)
DAY2_FILL_AT = datetime(2026, 7, 6, 15, 51, tzinfo=ET)

# 2026-11-27 is the day after Thanksgiving: XNYS closes at 13:00 ET.
EARLY_CLOSE_DAY = date(2026, 11, 27)

# A close strong enough to carry: above VWAP, at the session high, and inside the
# two-R stop distance the overnight policy allows.
CARRY_CLOSE = Decimal("104.5")

# A pyramided position has already trailed its stop up past 104, so the close that
# carries it has to clear that raised stop rather than the entry's.
PYRAMID_CLOSE = Decimal("106")

# The second entry: the same 1R, taken with a one-dollar stop, so risk sizes a
# position large enough for the twenty-percent gap stress to require a trim.
TIGHT_STOP = Decimal("101")
TIGHT_QUANTITY = Decimal("500")
TIGHT_ENTRY_CASH = Decimal("48872.50")
REDUCE_CLOSE = Decimal("103")


# Tape ----------------------------------------------------------------------

def priced(at: datetime, close: Decimal, *, symbol: str = SYMBOL,
           session: MarketSession = MarketSession.REGULAR) -> MinuteBar:
    """One bar whose high is its close, so a session high is exactly this price."""
    price = float(close)
    return MinuteBar(symbol=symbol, timestamp=at, open=price - 0.2, high=price,
                     low=price - 0.3, close=price, volume=20_000, session=session,
                     observed_at=at + timedelta(minutes=1),
                     available_at=at + timedelta(minutes=1))


def eod_tape(close: Decimal = CARRY_CLOSE, *, fill: bool = True, late: bool = False,
             postmarket: bool = False) -> tuple[MinuteBar, ...]:
    """The P1-F session, a closing bar the review reads, and what it may fill into."""
    tape = session_bars(priced(CLOSING_BAR_AT, close))
    if fill:
        tape += (priced(EOD_FILL_AT, close),)
    if late:
        tape += (priced(LATE_BAR_AT, close),)
    if postmarket:
        tape += (priced(POSTMARKET_BAR_AT, close, session=MarketSession.POSTMARKET),)
    return tape


def day2_tape(close: Decimal = CARRY_CLOSE, *, fill: bool = True) -> tuple[MinuteBar, ...]:
    """A second XNYS session, opened flat and closed on the same kind of bar."""
    tape = tuple(flat(DAY2_OPEN + timedelta(minutes=i), 104.0 + i * 0.05) for i in range(17))
    tape += (priced(DAY2_CLOSING_BAR_AT, close),)
    if fill:
        tape += (priced(DAY2_FILL_AT, close),)
    return tape


# Durable helpers -----------------------------------------------------------

def carriable(factory, *, suitability=OvernightSuitability.HIGH,
              active_stop: Decimal | None = None, **changes) -> StrategyState:
    """Give the open state the research and trailing figures a carry needs."""
    with factory() as session:
        repository = StrategyStateRepository(session)
        state = repository.list_open(SYMBOL)[0]
        updated = replace(state, overnight_suitability=suitability,
                          active_stop=active_stop or state.active_stop, **changes)
        repository.save(updated, updated_at=SIGNAL_AT)
        session.commit()
    return updated


def enter_tight(runtime, factory) -> StrategyState:
    """The same 1R with a one-dollar stop; risk alone decides the resulting size."""
    from app.services.position_lifecycle import strategy_state_sink

    signalled = StrategyState(SYMBOL, DAY, phase=StrategyPhase.ENTRY_SIGNALLED,
                              book=StrategyBook.ACTUAL, entry_price=ENTRY_REFERENCE,
                              initial_stop=TIGHT_STOP, active_stop=TIGHT_STOP,
                              entry_trading_date=DAY, holding_day_number=1)
    decision = StrategyDecision(SYMBOL, DecisionType.ENTER, "ABOVE_VWAP_AND_OR_BREAK",
                                SIGNAL_AT, strategy_version="strategy_v0")
    result = StrategyLifecycleRunner.for_active_runtime().execute_entry(
        state=signalled, decision=decision,
        account=AccountSnapshot(CASH, CASH, Currency.USD, SIGNAL_AT),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), SIGNAL_AT),
        market_bars=session_bars(), instrument_currency=Currency.USD, created_at=SIGNAL_AT,
        actual_risk_state=DailyTradingState(DAY), on_state=strategy_state_sink(SIGNAL_AT))
    assert result.order is not None and result.order.status is OrderStatus.FILLED
    assert runtime.broker.get_position(SYMBOL).quantity == TIGHT_QUANTITY
    assert runtime.broker.cash == TIGHT_ENTRY_CASH
    return result.state


def service(runtime, **kwargs) -> EndOfDayLifecycleService:
    return EndOfDayLifecycleService(runtime, **kwargs)


def durable_position(factory) -> SimulationPositionRecord:
    with factory() as session:
        return session.scalars(select(SimulationPositionRecord)).one()


def durable_trade(factory) -> SimulationTradeRecord:
    with factory() as session:
        return session.scalars(select(SimulationTradeRecord)).one()


def account_row(factory, account_id: int = 1):  # type: ignore[no-untyped-def]
    with factory() as session:
        return SimulationStateRepository(session).get_account_by_id(account_id)


def carry_once(factory):  # type: ignore[no-untyped-def]
    """Enter, make the position carriable, and let the review carry it whole."""
    runtime = activate()
    enter(runtime, factory)
    carriable(factory)
    driver = service(runtime)
    outcome = driver.review({SYMBOL: eod_tape()}, as_of=REVIEW_AS_OF)[0]
    assert outcome.action is EndOfDayAction.CARRIED
    return runtime, driver, outcome


def reduce_once(factory, *, tape=None, as_of=REVIEW_AS_OF):  # type: ignore[no-untyped-def]
    """Enter large, make it carriable, and let risk trim it to the stress cap."""
    runtime = activate()
    enter_tight(runtime, factory)
    carriable(factory)
    driver = service(runtime)
    outcome = driver.review({SYMBOL: eod_tape(REDUCE_CLOSE) if tape is None else tape},
                            as_of=as_of)[0]
    return runtime, driver, outcome


# 1-3. Review time comes from the exchange, never from a wall clock ----------

def test_normal_session_reviews_exactly_ten_minutes_before_the_close(factory) -> None:
    runtime = activate()
    driver = service(runtime)
    close = MarketCalendar().regular_market_close(DAY)
    assert close == MARKET_CLOSE
    assert driver.review_at(DAY) == REVIEW_AS_OF
    assert close - driver.review_at(DAY) == StrategyV0Engine().config.closing_review_before_close


def test_early_close_reviews_ten_minutes_before_the_early_close(factory) -> None:
    """No 15:50 is written down anywhere: an early close reviews early."""
    runtime = activate()
    driver = service(runtime)
    calendar = MarketCalendar()
    assert calendar.is_early_close(EARLY_CLOSE_DAY)
    assert calendar.regular_market_close(EARLY_CLOSE_DAY) == datetime(2026, 11, 27, 13, tzinfo=ET)
    assert driver.review_at(EARLY_CLOSE_DAY) == datetime(2026, 11, 27, 12, 50, tzinfo=ET)


@pytest.mark.parametrize("day", [date(2026, 7, 3), date(2026, 7, 4), date(2026, 7, 5)])
def test_a_holiday_or_weekend_has_no_review_time_at_all(factory, day) -> None:
    runtime = activate()
    assert service(runtime).review_at(day) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("as_of", [HOLIDAY_AS_OF, SATURDAY_AS_OF])
async def test_the_owner_reviews_nothing_on_a_holiday_or_a_weekend(factory, as_of) -> None:
    runtime = activate()
    enter(runtime, factory)
    carriable(factory)
    built: list[object] = []
    owner = EndOfDayPositionRuntime(runtime, lambda: built.append(object()) or built[-1])
    assert await owner.run_once(as_of=as_of) == ()
    # Nothing is acquired either: a closed exchange asks the provider for nothing.
    assert built == []
    assert stored_state(factory).phase is StrategyPhase.POSITION_OPEN


# 5-6. Carry ----------------------------------------------------------------

def test_a_carriable_position_is_held_whole_and_sells_nothing(factory) -> None:
    runtime, _, outcome = carry_once(factory)
    assert outcome.order is None
    assert outcome.reason == StrategyReason.OVERNIGHT_HOLD.value
    # SELL 0: cash, position, and account version are exactly what the entry left.
    assert runtime.broker.cash == ENTRY_CASH
    assert runtime.broker.get_position(SYMBOL).quantity == QUANTITY
    assert runtime.broker.get_trade(SYMBOL).status is TradeStatus.OPEN
    account = account_row(factory)
    assert account.cash == ENTRY_CASH and account.state_version == 1
    with factory() as session:
        assert counts(session) == {"orders": 1, "fills": 1, "positions": 1, "trades": 1}


def test_the_carry_is_durable_and_survives_a_restart(factory) -> None:
    runtime, _, _ = carry_once(factory)
    stored = stored_state(factory)
    assert stored.phase is StrategyPhase.OVERNIGHT_HELD and stored.overnight is True
    assert stored.holding_day_number == 1
    clear_active_sim_broker()
    restarted = activate()
    assert restarted.broker is not runtime.broker
    assert restarted.broker.get_position(SYMBOL).quantity == QUANTITY
    assert stored_state(factory) == stored


def test_a_position_research_never_cleared_for_the_night_is_sold(factory) -> None:
    """Suitability is the engine's own gate; the default state carries nothing."""
    runtime = activate()
    enter(runtime, factory)
    outcome = service(runtime).review({SYMBOL: eod_tape()}, as_of=REVIEW_AS_OF)[0]
    assert outcome.action is EndOfDayAction.EXIT_FILLED
    assert outcome.reason == StrategyReason.OVERNIGHT_REJECTED.value


# 7. The reduction has to be reachable at all --------------------------------

def test_a_position_over_the_stress_cap_is_trimmed_rather_than_abandoned(factory) -> None:
    """The gate is asked about a compliant size, so risk's own REDUCE is reachable.

    Asking the strategy gate about the whole position would answer a sizing
    question that belongs to risk: the position would be rejected outright and
    REDUCE_AND_HOLD could never happen. Both engines are read here directly, so the
    contract is stated in their own terms before any order exists.
    """
    runtime = activate()
    enter_tight(runtime, factory)
    state = carriable(factory)
    risk = RiskEngine()
    close = REDUCE_CLOSE
    equity = runtime.broker.cash + TIGHT_QUANTITY * close
    proposed = TIGHT_QUANTITY * close
    stress = risk.overnight_stress(equity, proposed, risk.config.overnight_stress_gap_pct)
    # The whole position breaches the cap ...
    assert stress.within_limit is False
    # ... and a compliant size nonetheless exists, which is what the gate is asked.
    assert Decimal("0") < stress.max_notional_by_stress < proposed
    snapshot = PositionSnapshot(SYMBOL, TIGHT_QUANTITY, ENTRY_FILL, close, Currency.USD,
                                initial_stop=TIGHT_STOP, active_stop=TIGHT_STOP,
                                base_notional_account_ccy=ENTRY_FILL * TIGHT_QUANTITY)
    outcome = risk.evaluate_overnight_notional(
        account=AccountSnapshot(equity, runtime.broker.cash, Currency.USD, REVIEW_AS_OF),
        portfolio=PortfolioSnapshot((snapshot,), snapshot.base_notional_account_ccy,
                                    Decimal("0"), REVIEW_AS_OF),
        proposed_notional=proposed)
    assert outcome.action is OvernightAction.REDUCE_AND_HOLD
    # Had the gate been handed the raw stress verdict, the review would say EXIT.
    rejected = StrategyV0Engine().closing_review(
        state=state, current_price=close, vwap=Decimal("100"), session_low=Decimal("99.9"),
        session_high=close, variant=__import__("app.services.position_lifecycle",
                                               fromlist=["ACTUAL_VARIANT"]).ACTUAL_VARIANT,
        stress_within_limit=False, overnight_position_available=True,
        has_new_negative_catalyst=False, as_of=REVIEW_AS_OF)
    assert rejected.decision is DecisionType.EXIT


# 8-11. The partial sell itself ---------------------------------------------

def test_the_review_sells_part_of_the_position_and_carries_the_rest(factory) -> None:
    runtime, _, outcome = reduce_once(factory)
    assert outcome.action is EndOfDayAction.REDUCED
    order = outcome.order
    assert order is not None and order.status is OrderStatus.FILLED
    assert order.side is OrderSide.SELL and order.intent_type == IntentType.EXIT.value
    assert order.fill_session is MarketSession.REGULAR
    assert order.filled_at == EOD_FILL_AT
    sold = order.filled_quantity
    assert Decimal("0") < sold < TIGHT_QUANTITY
    position = runtime.broker.get_position(SYMBOL)
    assert position.quantity == TIGHT_QUANTITY - sold
    # A sale does not re-price what is left: the average is the entry's own.
    assert position.average_price == ENTRY_FILL
    assert position.cost_basis == ENTRY_FILL * position.quantity
    assert position.realized_pnl > 0
    assert runtime.broker.cash > TIGHT_ENTRY_CASH


def test_the_trimmed_position_and_its_trade_are_durable_to_the_broker(factory) -> None:
    runtime, _, outcome = reduce_once(factory)
    broker_position = runtime.broker.get_position(SYMBOL)
    broker_trade = runtime.broker.get_trade(SYMBOL)
    row = durable_position(factory)
    assert (row.quantity, row.average_price, row.cost_basis, row.realized_pnl) == (
        broker_position.quantity, broker_position.average_price,
        broker_position.cost_basis, broker_position.realized_pnl)
    trade = durable_trade(factory)
    assert trade.status == TradeStatus.OPEN.value and trade.exit_time is None
    assert (trade.sold_quantity, trade.exit_notional, trade.average_exit_price) == (
        broker_trade._sold_quantity, broker_trade._exit_notional,
        broker_trade.average_exit_price)
    assert (trade.total_cost, trade.gross_pnl, trade.net_pnl, trade.gross_r, trade.net_r) == (
        broker_trade.total_cost, broker_trade.gross_pnl, broker_trade.net_pnl,
        broker_trade.gross_r, broker_trade.net_r)
    assert trade.sold_quantity == outcome.order.filled_quantity
    assert account_row(factory).cash == runtime.broker.cash


def test_the_reduced_state_matches_the_position_that_is_left(factory) -> None:
    runtime, _, _ = reduce_once(factory)
    stored = stored_state(factory)
    assert stored.phase is StrategyPhase.OVERNIGHT_HELD and stored.overnight is True
    assert stored.last_market_as_of == EOD_FILL_AT
    assert runtime.broker.get_position(SYMBOL).quantity == durable_position(factory).quantity


def test_a_restart_after_the_reduction_rebuilds_the_same_figures(factory) -> None:
    runtime, _, _ = reduce_once(factory)
    before = (runtime.broker.cash, runtime.broker.get_position(SYMBOL),
              runtime.broker.get_trade(SYMBOL))
    stored = stored_state(factory)
    clear_active_sim_broker()
    restarted = activate()
    assert restarted.broker is not runtime.broker
    assert restarted.broker.cash == before[0]
    assert restarted.broker.get_position(SYMBOL).quantity == before[1].quantity
    assert restarted.broker.get_position(SYMBOL).average_price == before[1].average_price
    assert restarted.broker.get_position(SYMBOL).cost_basis == before[1].cost_basis
    assert restarted.broker.get_position(SYMBOL).realized_pnl == before[1].realized_pnl
    assert restarted.broker.get_trade(SYMBOL)._sold_quantity == before[2]._sold_quantity
    assert restarted.broker.get_trade(SYMBOL)._exit_notional == before[2]._exit_notional
    assert restarted.broker.get_trade(SYMBOL).net_pnl == before[2].net_pnl
    assert stored_state(factory) == stored


# 17. Exactly once ----------------------------------------------------------

def test_a_second_tick_in_the_same_window_does_not_reduce_again(factory) -> None:
    """A reviewed position is no longer reviewable, so 15:51 sells nothing."""
    runtime, driver, first = reduce_once(factory)
    submitted = count_submissions(runtime.broker)
    after_first = (runtime.broker.cash, runtime.broker.get_position(SYMBOL).quantity)
    for minute in (51, 52, 59):
        again = driver.review({SYMBOL: eod_tape(REDUCE_CLOSE, late=True)},
                              as_of=datetime(2026, 7, 2, 15, minute, tzinfo=ET))[0]
        assert again.action is EndOfDayAction.SKIPPED
        assert "OVERNIGHT_HELD" in again.reason
    assert submitted == []
    assert (runtime.broker.cash, runtime.broker.get_position(SYMBOL).quantity) == after_first


def test_a_second_tick_after_a_carry_reviews_nothing(factory) -> None:
    runtime, driver, _ = carry_once(factory)
    submitted = count_submissions(runtime.broker)
    again = driver.review({SYMBOL: eod_tape()}, as_of=datetime(2026, 7, 2, 15, 55, tzinfo=ET))[0]
    assert again.action is EndOfDayAction.SKIPPED
    assert submitted == []
    assert runtime.broker.cash == ENTRY_CASH


# 12. Full exit -------------------------------------------------------------

def test_a_rejected_carry_closes_the_position_durably(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    outcome = service(runtime).review({SYMBOL: eod_tape()}, as_of=REVIEW_AS_OF)[0]
    assert outcome.action is EndOfDayAction.EXIT_FILLED
    assert outcome.order.filled_quantity == QUANTITY
    assert runtime.broker.get_positions() == ()
    trade = durable_trade(factory)
    assert trade.status == TradeStatus.CLOSED.value
    assert trade.exit_reason == StrategyReason.OVERNIGHT_REJECTED.value
    assert trade.exit_time == EOD_FILL_AT
    with factory() as session:
        assert session.scalars(select(SimulationPositionRecord)).all() == []
    assert stored_state(factory).phase is StrategyPhase.EXITED
    assert account_row(factory).cash == runtime.broker.cash


def test_after_a_full_exit_a_restart_finds_no_end_of_day_work(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    driver = service(runtime)
    assert driver.review({SYMBOL: eod_tape()}, as_of=REVIEW_AS_OF)[0].action is EndOfDayAction.EXIT_FILLED
    final_cash = runtime.broker.cash
    clear_active_sim_broker()
    restarted = activate()
    assert restarted.broker.cash == final_cash and restarted.broker.get_positions() == ()
    assert service(restarted).review({SYMBOL: eod_tape()}, as_of=LATE_AS_OF) == ()
    assert service(restarted).activate_day2(as_of=DAY2_OPEN) == ()


# 13-15. The fill window ----------------------------------------------------

def test_a_reduction_with_no_bar_left_stays_pending_and_moves_nothing(factory) -> None:
    runtime, _, outcome = reduce_once(factory, tape=eod_tape(REDUCE_CLOSE, fill=False))
    assert outcome.action is EndOfDayAction.REDUCTION_UNFILLED
    assert outcome.order.rejection_reason is RejectionReason.NO_NEXT_BAR
    assert runtime.broker.cash == TIGHT_ENTRY_CASH
    assert runtime.broker.get_position(SYMBOL).quantity == TIGHT_QUANTITY
    assert runtime.broker.get_trade(SYMBOL).status is TradeStatus.OPEN
    # The pending truth is durable: the carry was decided, its size was not.
    stored = stored_state(factory)
    assert stored.phase is StrategyPhase.OVERNIGHT_REVIEW
    assert stored.overnight is False
    assert stored.last_market_as_of == REVIEW_AS_OF


def test_a_pending_reduction_settles_on_a_later_bar_in_the_same_session(factory) -> None:
    """The retry is the same reduction, so it keeps the review's own signal time."""
    runtime, driver, first = reduce_once(factory, tape=eod_tape(REDUCE_CLOSE, fill=False))
    assert first.action is EndOfDayAction.REDUCTION_UNFILLED
    submitted = count_submissions(runtime.broker)
    retry = driver.review({SYMBOL: eod_tape(REDUCE_CLOSE, fill=False, late=True)},
                          as_of=LATE_AS_OF)[0]
    assert retry.action is EndOfDayAction.REDUCED
    # One retry, one sell: the bar that settles it is the first one after 15:50.
    assert len(submitted) == 1 and submitted[0].side is OrderSide.SELL
    assert retry.order.filled_at == LATE_BAR_AT
    assert runtime.broker.get_position(SYMBOL).quantity == TIGHT_QUANTITY - retry.order.filled_quantity
    assert stored_state(factory).phase is StrategyPhase.OVERNIGHT_HELD


def test_a_restart_before_the_pending_reduction_settles_it_exactly_once(factory) -> None:
    runtime, _, _ = reduce_once(factory, tape=eod_tape(REDUCE_CLOSE, fill=False))
    pending = stored_state(factory)
    clear_active_sim_broker()
    restarted = activate()
    assert restarted.broker.get_position(SYMBOL).quantity == TIGHT_QUANTITY
    assert stored_state(factory) == pending
    submitted = count_submissions(restarted.broker)
    retry = service(restarted).review({SYMBOL: eod_tape(REDUCE_CLOSE, fill=False, late=True)},
                                      as_of=LATE_AS_OF)[0]
    assert retry.action is EndOfDayAction.REDUCED
    assert len(submitted) == 1
    assert stored_state(factory).phase is StrategyPhase.OVERNIGHT_HELD


def test_a_reduction_never_fills_on_a_postmarket_print(factory) -> None:
    """An overnight trim settles where every other exit does: the regular session."""
    tape = eod_tape(REDUCE_CLOSE, fill=False, postmarket=True)
    assert tape[-1].session is MarketSession.POSTMARKET and tape[-1].timestamp > REVIEW_AS_OF
    runtime, _, outcome = reduce_once(factory, tape=tape)
    assert outcome.action is EndOfDayAction.REDUCTION_UNFILLED
    assert outcome.order.rejection_reason is RejectionReason.NO_NEXT_BAR
    assert runtime.broker.get_position(SYMBOL).quantity == TIGHT_QUANTITY


def test_a_pending_exit_is_retried_by_the_minute_driver_that_owns_that_phase(factory) -> None:
    """EXIT_SIGNALLED has one retry owner, and the closing review is not it.

    Two owners retrying one signal is how a position gets sold twice, so the
    closing review hands a signalled exit to the driver that already owns it. The
    cost is visible here: that driver re-derives the label from the stops, so a
    retried overnight exit closes the trade as a stop. It is a labelling loss on an
    unfilled exit, not a second sell.
    """
    runtime = activate()
    enter(runtime, factory)
    outcome = service(runtime).review({SYMBOL: eod_tape(fill=False)}, as_of=REVIEW_AS_OF)[0]
    assert outcome.action is EndOfDayAction.EXIT_UNFILLED
    assert outcome.order.rejection_reason is RejectionReason.NO_NEXT_BAR
    stored = stored_state(factory)
    assert stored.phase is StrategyPhase.EXIT_SIGNALLED
    assert runtime.broker.get_position(SYMBOL).quantity == QUANTITY
    # The closing review will not touch it again ...
    assert service(runtime).review({SYMBOL: eod_tape(fill=False, late=True)},
                                   as_of=LATE_AS_OF)[0].action is EndOfDayAction.SKIPPED
    # ... and the minute driver settles it on the next bar it has.
    retried = PositionLifecycleService(runtime).evaluate(
        {SYMBOL: eod_tape(fill=False, late=True)}, as_of=LATE_AS_OF)[0]
    assert retried.action is PositionAction.EXIT_FILLED
    assert runtime.broker.get_positions() == ()
    assert durable_trade(factory).exit_reason == StrategyReason.INITIAL_STOP.value


# 16-18. Day 2 --------------------------------------------------------------

def carried_to_day2(factory):  # type: ignore[no-untyped-def]
    runtime, driver, _ = carry_once(factory)
    activated = driver.activate_day2(as_of=DAY2_OPEN)
    assert [item.action for item in activated] == [EndOfDayAction.DAY2_ACTIVATED]
    return runtime, driver, activated[0]


def test_the_next_xnys_session_promotes_a_carried_position_to_day_two(factory) -> None:
    runtime, _, outcome = carried_to_day2(factory)
    assert MarketCalendar().next_trading_day(DAY) == DAY2
    stored = stored_state(factory)
    assert stored.phase is StrategyPhase.DAY2_ACTIVE
    assert stored.holding_day_number == 2 and stored.overnight is True
    assert stored.entry_trading_date == DAY
    # Nothing was bought or sold to advance a day.
    assert runtime.broker.cash == ENTRY_CASH
    assert runtime.broker.get_position(SYMBOL).quantity == QUANTITY


@pytest.mark.parametrize("as_of", [HOLIDAY_AS_OF, SATURDAY_AS_OF,
                                   datetime(2026, 7, 5, 12, tzinfo=ET)])
def test_a_closed_day_advances_no_holding_day(factory, as_of) -> None:
    runtime, driver, _ = carry_once(factory)
    assert driver.activate_day2(as_of=as_of) == ()
    stored = stored_state(factory)
    assert stored.phase is StrategyPhase.OVERNIGHT_HELD and stored.holding_day_number == 1


def test_day_two_is_activated_once_and_survives_a_restart(factory) -> None:
    runtime, driver, _ = carried_to_day2(factory)
    assert driver.activate_day2(as_of=DAY2_OPEN + timedelta(hours=1)) == ()
    stored = stored_state(factory)
    clear_active_sim_broker()
    restarted = activate()
    assert restarted.broker.get_position(SYMBOL).quantity == QUANTITY
    assert stored_state(factory) == stored
    assert service(restarted).activate_day2(as_of=DAY2_REVIEW_AS_OF) == ()
    assert stored_state(factory).holding_day_number == 2


def test_a_restart_on_day_two_activates_the_day_it_missed(factory) -> None:
    """The activation is not a moment that can be missed; it is a calendar fact."""
    runtime, _, _ = carry_once(factory)
    clear_active_sim_broker()
    restarted = activate()
    outcome = service(restarted).activate_day2(as_of=DAY2_REVIEW_AS_OF)[0]
    assert outcome.action is EndOfDayAction.DAY2_ACTIVATED
    assert stored_state(factory).holding_day_number == 2


def test_day_two_closes_the_position_rather_than_carrying_it_to_day_three(factory) -> None:
    runtime, driver, _ = carried_to_day2(factory)
    outcome = driver.review({SYMBOL: day2_tape()}, as_of=DAY2_REVIEW_AS_OF)[0]
    assert outcome.action is EndOfDayAction.EXIT_FILLED
    assert outcome.reason == StrategyReason.DAY2_MAX_HOLD.value
    assert runtime.broker.get_positions() == ()
    trade = durable_trade(factory)
    assert trade.status == TradeStatus.CLOSED.value
    assert trade.exit_reason == StrategyReason.DAY2_MAX_HOLD.value
    assert trade.exit_time == DAY2_FILL_AT
    assert stored_state(factory).phase is StrategyPhase.EXITED
    # Day 3 does not exist: there is nothing left to review or to advance.
    assert driver.review({SYMBOL: day2_tape()}, as_of=DAY2_REVIEW_AS_OF) == ()


def test_the_minute_driver_does_not_manage_a_position_the_night_owns(factory) -> None:
    """One position, two cadences, no overlap: OVERNIGHT_HELD is not a managed phase."""
    runtime, _, _ = carry_once(factory)
    submitted = count_submissions(runtime.broker)
    outcome = PositionLifecycleService(runtime).evaluate({SYMBOL: eod_tape()},
                                                        as_of=LATE_AS_OF)[0]
    assert outcome.action is PositionAction.SKIPPED
    assert "OVERNIGHT_HELD" in outcome.reason
    assert submitted == []
    assert runtime.broker.get_position(SYMBOL).quantity == QUANTITY


# 19. Everything the earlier stages built has to survive the night -----------

def test_a_pyramided_and_trailed_position_keeps_every_figure_through_the_night(factory) -> None:
    """G3 and G4A truth survives the closing review, the sell it produces, and a restart.

    The position that reaches the close here is the real one: a pyramid add the
    driver bought and a trailing stop it raised. At that size the overnight stress
    cap bites, so this is also the reduction path carrying an add's state through a
    partial sell.
    """
    from backend.tests.test_position_lifecycle_pyramid import ADD_AS_OF, add_tape

    runtime = activate()
    enter(runtime, factory)
    added = PositionLifecycleService(runtime).evaluate({SYMBOL: add_tape()}, as_of=ADD_AS_OF)[0]
    assert added.action is PositionAction.ADD_FILLED
    before = carriable(factory)
    assert before.add_count == 1 and before.add_signal_issued is True
    assert before.phase is StrategyPhase.PYRAMID_ADDED
    quantity = runtime.broker.get_position(SYMBOL).quantity
    assert quantity > QUANTITY
    planned_initial_risk = runtime.broker.get_trade(SYMBOL).planned_initial_risk
    assert before.active_stop > CARRY_CLOSE  # G3 raised it; the close must clear it

    tape = add_tape() + (priced(CLOSING_BAR_AT, PYRAMID_CLOSE), priced(EOD_FILL_AT, PYRAMID_CLOSE))
    outcome = service(runtime).review({SYMBOL: tape}, as_of=REVIEW_AS_OF)[0]
    assert outcome.action is EndOfDayAction.REDUCED

    def preserved(state) -> tuple:  # type: ignore[no-untyped-def]
        return (state.entry_price, state.initial_stop, state.active_stop,
                state.highest_price_since_entry, state.add_count, state.add_signal_issued,
                state.entry_trading_date, state.holding_day_number,
                state.trailing_profile, state.overnight_suitability)

    stored = stored_state(factory)
    assert preserved(stored) == preserved(before)
    assert stored.phase is StrategyPhase.OVERNIGHT_HELD and stored.overnight is True
    remaining = runtime.broker.get_position(SYMBOL).quantity
    assert Decimal("0") < remaining < quantity
    assert remaining == quantity - outcome.order.filled_quantity
    # An add's risk budget is the trade's for the trade's life; a trim is not a reset.
    assert runtime.broker.get_trade(SYMBOL).planned_initial_risk == planned_initial_risk
    clear_active_sim_broker()
    restarted = activate()
    assert restarted.broker.get_position(SYMBOL).quantity == remaining
    assert restarted.broker.get_trade(SYMBOL).planned_initial_risk == planned_initial_risk
    assert preserved(stored_state(factory)) == preserved(before)


def test_a_carry_leaves_a_raised_stop_and_its_high_water_mark_untouched(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    before = carriable(factory, active_stop=Decimal("102"),
                       highest_price_since_entry=Decimal("104.4"))
    outcome = service(runtime).review({SYMBOL: eod_tape()}, as_of=REVIEW_AS_OF)[0]
    assert outcome.action is EndOfDayAction.CARRIED
    stored = stored_state(factory)
    assert stored.active_stop == Decimal("102")
    assert stored.highest_price_since_entry == Decimal("104.4")
    assert stored.initial_stop == INITIAL_STOP and stored.entry_price == ENTRY_FILL
    clear_active_sim_broker()
    activate()
    assert stored_state(factory) == stored


def test_a_reduction_leaves_the_trade_risk_budget_and_stops_alone(factory) -> None:
    runtime = activate()
    enter_tight(runtime, factory)
    before = carriable(factory)
    planned_initial_risk = runtime.broker.get_trade(SYMBOL).planned_initial_risk
    outcome = service(runtime).review({SYMBOL: eod_tape(REDUCE_CLOSE)}, as_of=REVIEW_AS_OF)[0]
    assert outcome.action is EndOfDayAction.REDUCED
    stored = stored_state(factory)
    assert (stored.entry_price, stored.initial_stop, stored.active_stop) == (
        before.entry_price, before.initial_stop, before.active_stop)
    assert stored.highest_price_since_entry == before.highest_price_since_entry
    assert stored.add_count == before.add_count
    assert runtime.broker.get_trade(SYMBOL).planned_initial_risk == planned_initial_risk
    assert durable_trade(factory).planned_initial_risk == planned_initial_risk


# 22. Several symbols, one night --------------------------------------------

CARRY_SYMBOL, REDUCE_SYMBOL, EXIT_SYMBOL = "TSLA", "NVDA", "MSFT"
# These entries fill on a bar open rather than the P1-F close, so one R is smaller
# and the close that can still be carried sits nearer the stop.
MULTI_REDUCE_CLOSE = Decimal("102.8")


def symbol_session(symbol: str, close: Decimal, *, fill: bool = True) -> tuple[MinuteBar, ...]:
    """One symbol's own regular session: an entry tape and a closing bar."""
    tape = tuple(priced(MARKET_OPEN + timedelta(minutes=i), Decimal("100") + Decimal("0.05") * i,
                        symbol=symbol) for i in range(17))
    tape += (priced(datetime(2026, 7, 2, 9, 47, tzinfo=ET), Decimal("102"), symbol=symbol),
             priced(CLOSING_BAR_AT, close, symbol=symbol))
    return tape + ((priced(EOD_FILL_AT, close, symbol=symbol),) if fill else ())


def enter_symbol(symbol: str, stop: Decimal, *, suitability) -> StrategyState:  # type: ignore[no-untyped-def]
    from app.services.position_lifecycle import strategy_state_sink

    signalled = StrategyState(symbol, DAY, phase=StrategyPhase.ENTRY_SIGNALLED,
                              book=StrategyBook.ACTUAL, entry_price=ENTRY_REFERENCE,
                              initial_stop=stop, active_stop=stop, entry_trading_date=DAY,
                              holding_day_number=1, overnight_suitability=suitability)
    decision = StrategyDecision(symbol, DecisionType.ENTER, "ABOVE_VWAP_AND_OR_BREAK",
                                datetime(2026, 7, 2, 9, 46, tzinfo=ET),
                                strategy_version="strategy_v0")
    result = StrategyLifecycleRunner.for_active_runtime().execute_entry(
        state=signalled, decision=decision,
        account=AccountSnapshot(CASH, CASH, Currency.USD, SIGNAL_AT),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), SIGNAL_AT),
        market_bars=symbol_session(symbol, CARRY_CLOSE), instrument_currency=Currency.USD,
        created_at=SIGNAL_AT, actual_risk_state=DailyTradingState(DAY),
        on_state=strategy_state_sink(SIGNAL_AT))
    assert result.order is not None and result.order.status is OrderStatus.FILLED
    return result.state


def three_symbol_book(factory):  # type: ignore[no-untyped-def]
    runtime = activate()
    enter_symbol(CARRY_SYMBOL, INITIAL_STOP, suitability=OvernightSuitability.HIGH)
    enter_symbol(REDUCE_SYMBOL, TIGHT_STOP, suitability=OvernightSuitability.HIGH)
    enter_symbol(EXIT_SYMBOL, INITIAL_STOP, suitability=OvernightSuitability.UNKNOWN)
    assert len(runtime.broker.get_positions()) == 3
    return runtime


def test_each_symbol_reaches_its_own_outcome_without_disturbing_the_others(factory) -> None:
    """Carry, trim, and close in one review; the production slot limit is one, so
    this asks the risk engine for three slots. Only the independence is the subject
    here - the slot limit itself has its own test below."""
    runtime = three_symbol_book(factory)
    driver = service(runtime, risk_engine=RiskEngine(RiskConfig(max_overnight_positions=3)))
    tape = {CARRY_SYMBOL: symbol_session(CARRY_SYMBOL, CARRY_CLOSE),
            REDUCE_SYMBOL: symbol_session(REDUCE_SYMBOL, MULTI_REDUCE_CLOSE),
            EXIT_SYMBOL: symbol_session(EXIT_SYMBOL, CARRY_CLOSE)}
    outcomes = {item.symbol: item for item in driver.review(tape, as_of=REVIEW_AS_OF)}
    assert outcomes[CARRY_SYMBOL].action is EndOfDayAction.CARRIED
    assert outcomes[REDUCE_SYMBOL].action is EndOfDayAction.REDUCED
    assert outcomes[EXIT_SYMBOL].action is EndOfDayAction.EXIT_FILLED

    with factory() as session:
        repository = StrategyStateRepository(session)
        carried = repository.list_open(CARRY_SYMBOL)[0]
        reduced = repository.list_open(REDUCE_SYMBOL)[0]
        assert repository.list_open(EXIT_SYMBOL) == ()
    assert carried.phase is StrategyPhase.OVERNIGHT_HELD and carried.overnight is True
    assert reduced.phase is StrategyPhase.OVERNIGHT_HELD and reduced.overnight is True
    # The carried position is untouched; only the two that traded moved.
    assert runtime.broker.get_position(CARRY_SYMBOL).quantity == QUANTITY
    assert Decimal("0") < runtime.broker.get_position(REDUCE_SYMBOL).quantity < TIGHT_QUANTITY
    assert runtime.broker.get_position(EXIT_SYMBOL) is None
    assert runtime.broker.get_trade(EXIT_SYMBOL).status is TradeStatus.CLOSED
    assert runtime.broker.get_trade(CARRY_SYMBOL).status is TradeStatus.OPEN


def test_one_symbols_missing_market_data_does_not_decide_another_symbols_night(factory) -> None:
    runtime = three_symbol_book(factory)
    driver = service(runtime, risk_engine=RiskEngine(RiskConfig(max_overnight_positions=3)))
    # Only two symbols were acquired; the third is simply not offered for review.
    tape = {CARRY_SYMBOL: symbol_session(CARRY_SYMBOL, CARRY_CLOSE),
            EXIT_SYMBOL: symbol_session(EXIT_SYMBOL, CARRY_CLOSE)}
    outcomes = {item.symbol: item for item in
                driver.review(tape, as_of=REVIEW_AS_OF, symbols=frozenset(tape))}
    assert set(outcomes) == {CARRY_SYMBOL, EXIT_SYMBOL}
    assert outcomes[CARRY_SYMBOL].action is EndOfDayAction.CARRIED
    assert outcomes[EXIT_SYMBOL].action is EndOfDayAction.EXIT_FILLED
    assert runtime.broker.get_position(REDUCE_SYMBOL).quantity == TIGHT_QUANTITY
    with factory() as session:
        assert StrategyStateRepository(session).list_open(REDUCE_SYMBOL)[0].phase \
            is StrategyPhase.POSITION_OPEN


def test_the_overnight_slot_limit_is_counted_across_symbols_in_one_review(factory) -> None:
    """A book reviewed one symbol at a time still sees the slot already taken."""
    runtime = activate()
    enter_symbol(CARRY_SYMBOL, INITIAL_STOP, suitability=OvernightSuitability.HIGH)
    enter_symbol(EXIT_SYMBOL, INITIAL_STOP, suitability=OvernightSuitability.HIGH)
    assert RiskConfig().max_overnight_positions == 1
    tape = {CARRY_SYMBOL: symbol_session(CARRY_SYMBOL, CARRY_CLOSE),
            EXIT_SYMBOL: symbol_session(EXIT_SYMBOL, CARRY_CLOSE)}
    outcomes = {item.symbol: item for item in service(runtime).review(tape, as_of=REVIEW_AS_OF)}
    # MSFT is reviewed first and takes the only slot; TSLA then has none to take.
    assert outcomes[EXIT_SYMBOL].action is EndOfDayAction.CARRIED
    assert outcomes[CARRY_SYMBOL].action is EndOfDayAction.EXIT_FILLED
    assert outcomes[CARRY_SYMBOL].reason == StrategyReason.OVERNIGHT_REJECTED.value


# 25. Atomicity -------------------------------------------------------------

def test_a_failed_state_write_rolls_the_whole_reduction_back(factory, monkeypatch) -> None:
    """Order, fill, cash, position, trade, and state move together or not at all."""
    runtime = activate()
    enter_tight(runtime, factory)
    carriable(factory)
    with factory() as session:
        before_counts = counts(session)
    before = (runtime.broker.cash, runtime.broker.get_position(SYMBOL).quantity,
              runtime.broker.get_trade(SYMBOL).status, account_row(factory).state_version)

    def explode(self, state, *, updated_at):  # type: ignore[no-untyped-def]
        raise RuntimeError("strategy state write failed")

    monkeypatch.setattr(StrategyStateRepository, "save", explode)
    outcome = service(runtime).review({SYMBOL: eod_tape(REDUCE_CLOSE)}, as_of=REVIEW_AS_OF)[0]
    assert outcome.action is EndOfDayAction.SKIPPED
    assert "strategy state write failed" in outcome.reason
    # Broker memory is restored before the database rollback is even attempted.
    assert (runtime.broker.cash, runtime.broker.get_position(SYMBOL).quantity,
            runtime.broker.get_trade(SYMBOL).status) == before[:3]
    monkeypatch.undo()
    assert account_row(factory).state_version == before[3]
    with factory() as session:
        assert counts(session) == before_counts
    assert stored_state(factory).phase is StrategyPhase.POSITION_OPEN
    assert durable_position(factory).quantity == TIGHT_QUANTITY


def test_a_failing_symbol_does_not_block_the_rest_of_the_book(factory, monkeypatch) -> None:
    runtime = three_symbol_book(factory)
    original = StrategyStateRepository.save

    def selective(self, state, *, updated_at):  # type: ignore[no-untyped-def]
        if state.symbol == REDUCE_SYMBOL:
            raise RuntimeError("strategy state write failed")
        return original(self, state, updated_at=updated_at)

    monkeypatch.setattr(StrategyStateRepository, "save", selective)
    driver = service(runtime, risk_engine=RiskEngine(RiskConfig(max_overnight_positions=3)))
    tape = {symbol: symbol_session(symbol, close) for symbol, close in (
        (CARRY_SYMBOL, CARRY_CLOSE), (REDUCE_SYMBOL, MULTI_REDUCE_CLOSE),
        (EXIT_SYMBOL, CARRY_CLOSE))}
    outcomes = {item.symbol: item for item in driver.review(tape, as_of=REVIEW_AS_OF)}
    assert outcomes[REDUCE_SYMBOL].action is EndOfDayAction.SKIPPED
    assert outcomes[CARRY_SYMBOL].action is EndOfDayAction.CARRIED
    assert outcomes[EXIT_SYMBOL].action is EndOfDayAction.EXIT_FILLED
    assert runtime.broker.get_position(REDUCE_SYMBOL).quantity == TIGHT_QUANTITY


# 4-5, 21. The cadence owner ------------------------------------------------

class Tape:
    """A provider that hands back one symbol's session and counts what was asked."""

    def __init__(self, bars_by_symbol, *, failing=()) -> None:  # type: ignore[no-untyped-def]
        self.bars_by_symbol = bars_by_symbol
        self.failing = set(failing)
        self.calls: list[str] = []

    def get_minute_bars(self, symbols, start=None, end=None, session=None):  # type: ignore[no-untyped-def]
        symbol = symbols[0]
        self.calls.append(symbol)
        if symbol in self.failing:
            raise RuntimeError("market data unavailable")
        return list(self.bars_by_symbol.get(symbol, ()))


def owner(runtime, provider, *, built=None):  # type: ignore[no-untyped-def]
    def build():
        if built is not None:
            built.append(provider)
        return provider

    return EndOfDayPositionRuntime(runtime, build)


@pytest.mark.asyncio
async def test_the_owner_acquires_nothing_before_the_review_moment(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    carriable(factory)
    built: list[object] = []
    provider = Tape({SYMBOL: eod_tape()})
    assert await owner(runtime, provider, built=built).run_once(
        as_of=REVIEW_AS_OF - timedelta(minutes=1)) == ()
    assert built == [] and provider.calls == []
    assert stored_state(factory).phase is StrategyPhase.POSITION_OPEN


@pytest.mark.asyncio
async def test_the_owner_reviews_at_the_review_moment_and_only_once(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    carriable(factory)
    provider = Tape({SYMBOL: eod_tape()})
    eod_owner = owner(runtime, provider)
    first = await eod_owner.run_once(as_of=REVIEW_AS_OF)
    assert [item.action for item in first] == [EndOfDayAction.CARRIED]
    submitted = count_submissions(runtime.broker)
    for minute in (51, 52, 55, 59):
        again = await eod_owner.run_once(as_of=datetime(2026, 7, 2, 15, minute, tzinfo=ET))
        assert [item.action for item in again] == [EndOfDayAction.SKIPPED]
    assert submitted == []
    assert runtime.broker.cash == ENTRY_CASH


@pytest.mark.asyncio
async def test_the_owner_records_daily_performance_once_the_exchange_has_closed(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    carriable(factory)
    provider = Tape({SYMBOL: eod_tape()})
    assert await owner(runtime, provider).run_once(
        as_of=MARKET_CLOSE + timedelta(minutes=1)) == ()
    assert provider.calls == [SYMBOL]
    assert stored_state(factory).phase is StrategyPhase.POSITION_OPEN
    with factory() as session:
        snapshot = session.scalar(select(AccountDailyPerformanceRecord))
        assert snapshot is not None and snapshot.trading_date == date(2026, 7, 2)


@pytest.mark.asyncio
async def test_the_owner_activates_day_two_at_the_open_without_any_market_data(factory) -> None:
    runtime, _, _ = carry_once(factory)
    provider = Tape({})
    outcomes = await owner(runtime, provider).run_once(as_of=DAY2_OPEN)
    assert [item.action for item in outcomes] == [EndOfDayAction.DAY2_ACTIVATED]
    assert provider.calls == []
    assert stored_state(factory).holding_day_number == 2


@pytest.mark.asyncio
async def test_one_symbols_acquisition_failure_leaves_the_others_reviewed(factory) -> None:
    runtime = three_symbol_book(factory)
    provider = Tape({symbol: symbol_session(symbol, CARRY_CLOSE)
                     for symbol in (CARRY_SYMBOL, REDUCE_SYMBOL, EXIT_SYMBOL)},
                    failing=[REDUCE_SYMBOL])
    outcomes = await owner(runtime, provider).run_once(as_of=REVIEW_AS_OF)
    assert sorted(provider.calls) == sorted([CARRY_SYMBOL, EXIT_SYMBOL, REDUCE_SYMBOL])
    reviewed = {item.symbol: item.action for item in outcomes}
    assert set(reviewed) == {CARRY_SYMBOL, EXIT_SYMBOL}
    assert runtime.broker.get_position(REDUCE_SYMBOL).quantity == TIGHT_QUANTITY


@pytest.mark.asyncio
async def test_a_stopped_owner_starts_no_further_end_of_day_work(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    carriable(factory)
    provider = Tape({SYMBOL: eod_tape()})
    eod_owner = owner(runtime, provider)
    eod_owner.stop()
    assert await eod_owner.run_once(as_of=REVIEW_AS_OF) == ()
    assert provider.calls == []
    assert stored_state(factory).phase is StrategyPhase.POSITION_OPEN
    assert runtime.broker.get_position(SYMBOL).quantity == QUANTITY


@pytest.mark.asyncio
async def test_only_one_end_of_day_task_runs_in_a_process(factory) -> None:
    runtime = activate()
    provider = Tape({})
    try:
        first = start_end_of_day_runtime(runtime, lambda: provider)
        second = start_end_of_day_runtime(runtime, lambda: provider)
        assert first is second
        assert get_end_of_day_runtime() is not None
    finally:
        await stop_end_of_day_runtime()
    assert get_end_of_day_runtime() is None
    assert first.done()


@pytest.mark.asyncio
async def test_startup_runs_both_cadences_and_shutdown_releases_them(
    factory, monkeypatch, open_session,
) -> None:
    """Two owners, one broker: both start, and both are stopped before it is released."""
    runtime = activate()
    enter(runtime, factory)
    clear_active_sim_broker()

    class NoBars:
        def get_minute_bars(self, symbols, start=None, end=None, session=None):
            return []

    monkeypatch.setattr(market_factory, "build_kiwoom_provider", lambda *a, **k: NoBars())
    monkeypatch.setattr(KiwoomMarketDataClient, "request",
                        lambda *a, **k: pytest.fail("a cadence reached the real Kiwoom client"))
    app = create_app()
    async with app.router.lifespan_context(app):
        for _ in range(20):
            await asyncio.sleep(0)
        assert get_end_of_day_runtime() is not None
        assert get_position_management_runtime() is not None
    assert get_end_of_day_runtime() is None
    assert get_position_management_runtime() is None
    assert get_active_sim_broker() is None
    assert stored_state(factory).phase is StrategyPhase.POSITION_OPEN


# 26. Kiwoom stays a market-data adapter ------------------------------------

def test_the_night_places_every_order_on_the_simulation_broker_alone(factory, monkeypatch) -> None:
    monkeypatch.setattr(KiwoomMarketDataClient, "request",
                        lambda *a, **k: pytest.fail("the closing review reached Kiwoom"))
    runtime, _, outcome = reduce_once(factory)
    assert outcome.action is EndOfDayAction.REDUCED
    with factory() as session:
        orders = session.scalars(select(ExecutionOrderRecord)).all()
    # Every durable order this stage produced belongs to the simulation broker.
    assert [order.broker_type for order in orders] == ["SIM", "SIM"]
    assert all(order.id.startswith("SIM-") for order in orders)
    assert type(runtime.broker).__name__ == "SimBroker"
    # The adapter's own safety metric: no order path was ever attempted.
    client = KiwoomMarketDataClient.__new__(KiwoomMarketDataClient)
    client._request_counts = {"/api/dostk/chart": 3}
    assert client.order_request_count == 0


# 16A, 16D. Restart around the review itself --------------------------------

def test_a_restart_before_the_review_still_reviews_exactly_once(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    carriable(factory)
    clear_active_sim_broker()
    restarted = activate()
    assert restarted.broker is not runtime.broker
    driver = service(restarted)
    assert driver.review({SYMBOL: eod_tape()}, as_of=REVIEW_AS_OF)[0].action \
        is EndOfDayAction.CARRIED
    submitted = count_submissions(restarted.broker)
    assert driver.review({SYMBOL: eod_tape()}, as_of=LATE_AS_OF)[0].action \
        is EndOfDayAction.SKIPPED
    assert submitted == []
    assert stored_state(factory).phase is StrategyPhase.OVERNIGHT_HELD


def test_a_restart_with_a_pending_exit_keeps_one_retry_owner(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    assert service(runtime).review({SYMBOL: eod_tape(fill=False)},
                                   as_of=REVIEW_AS_OF)[0].action is EndOfDayAction.EXIT_UNFILLED
    pending = stored_state(factory)
    assert pending.phase is StrategyPhase.EXIT_SIGNALLED
    clear_active_sim_broker()
    restarted = activate()
    assert restarted.broker.get_position(SYMBOL).quantity == QUANTITY
    assert stored_state(factory) == pending
    submitted = count_submissions(restarted.broker)
    tape = eod_tape(fill=False, late=True)
    # The closing review still refuses it, and the minute driver still owns it.
    assert service(restarted).review({SYMBOL: tape}, as_of=LATE_AS_OF)[0].action \
        is EndOfDayAction.SKIPPED
    retried = PositionLifecycleService(restarted).evaluate({SYMBOL: tape}, as_of=LATE_AS_OF)[0]
    assert retried.action is PositionAction.EXIT_FILLED
    assert len(submitted) == 1
    assert restarted.broker.get_positions() == ()
    assert stored_state(factory).phase is StrategyPhase.EXITED
