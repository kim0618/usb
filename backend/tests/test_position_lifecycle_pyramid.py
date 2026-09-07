"""P1-G4A: a confirmed pyramid add is sized by risk and bought durably.

The tape continues the deterministic P1-F entry (reference 102, stop 99, fill
102.153), so +1R is an exact number rather than an approximation. Nothing here
recomputes a fill price, a commission, or a blended average: those come from the
broker and the durable rows. Only the risk engine's own quantity and risk
figures are asserted against the contract, because that is the one authority
this stage adds.
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.broker.domain import OrderStatus, RejectionReason, TradeStatus
from app.execution.domain import IntentType, OrderSide
from app.integrations.kiwoom.client import KiwoomMarketDataClient
from app.models.simulation import SimulationPositionRecord, SimulationTradeRecord
from app.repositories.risk import DailyRiskRepository
from app.repositories.strategy import StrategyStateRepository
from app.risk.config import RiskConfig
from app.risk.domain import (
    AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot, PositionSnapshot,
    RiskRejectionReason,
)
from app.risk.engine import RiskEngine
from app.services.position_lifecycle import PositionAction, PositionLifecycleService
from app.services.position_management_runtime import PositionManagementRuntime
from app.services.simulation_runtime import clear_active_sim_broker
from app.services.strategy import StrategyLifecycleService
from app.strategy.config import VARIANT_CONFIGS
from app.strategy.domain import DecisionType, StrategyDecision, TradingEligibility
from app.strategy.engine import StrategyReason, StrategyV0Engine
from app.strategy.lifecycle import StrategyPhase, StrategyState

from backend.tests.test_position_lifecycle_hard_stop import (
    CASH,
    DAY,
    ENTRY_CASH,
    ENTRY_FILL,
    ENTRY_FILL_AT,
    INITIAL_STOP,
    MARKET_OPEN,
    QUANTITY,
    SYMBOL,
    activate,
    bar,
    count_submissions,
    counts,
    enter,
    factory,
    ownership,
    session_bars,
    stored_state,
)

ONE_R = ENTRY_FILL - INITIAL_STOP
# The pyramid trigger is a +1R close, so this price is the boundary itself.
TRIGGER_CLOSE = ENTRY_FILL + ONE_R
ADD_BAR_AT = datetime(2026, 7, 2, 10, 0, tzinfo=MARKET_OPEN.tzinfo)
ADD_AS_OF = ADD_BAR_AT + timedelta(minutes=1)
ADD_FILL_AT = ADD_BAR_AT + timedelta(minutes=2)
LATER_BAR_AT = ADD_BAR_AT + timedelta(minutes=3)
LATER_AS_OF = LATER_BAR_AT + timedelta(minutes=1)
LATER_FILL_AT = ADD_BAR_AT + timedelta(minutes=5)
NOW = MARKET_OPEN


def signal_bar(close: Decimal) -> tuple:
    """One bar whose close is the price the engine will judge the add on."""
    return (bar(ADD_BAR_AT, open_=float(close - Decimal("0.4")), high=float(close),
                low=float(close - Decimal("0.8")), close=float(close)),)


def add_tape(*, close: Decimal = TRIGGER_CLOSE, fill: bool = True,
             retry_fill: bool = False, stop_after: bool = False) -> tuple:
    """The P1-F tape, a +1R signal bar, and optionally something to fill into."""
    tape = session_bars(*signal_bar(close))
    if fill:
        tape += (bar(ADD_FILL_AT, open_=105.4, high=105.9, low=105.2, close=105.6),)
    if retry_fill:
        tape += (bar(LATER_BAR_AT, open_=105.5, high=105.9, low=105.3, close=105.7),
                 bar(LATER_FILL_AT, open_=105.6, high=106.0, low=105.4, close=105.8))
    if stop_after:
        tape += (bar(LATER_BAR_AT + timedelta(minutes=4), open_=99.5, high=99.6, low=90.0, close=95.0),
                 bar(LATER_BAR_AT + timedelta(minutes=6), open_=94.0, high=94.5, low=93.5, close=94.2))
    return tape


def durable_position(factory, account_id: int = 1) -> SimulationPositionRecord:
    with factory() as session:
        return session.scalars(select(SimulationPositionRecord)).one()


def durable_trade(factory) -> SimulationTradeRecord:
    with factory() as session:
        return session.scalars(select(SimulationTradeRecord)).one()


def add_once(factory):  # type: ignore[no-untyped-def]
    """Enter, then let the driver buy the one add this tape confirms."""
    runtime = activate()
    enter(runtime, factory)
    driver = PositionLifecycleService(runtime)
    outcome = driver.evaluate({SYMBOL: add_tape()}, as_of=ADD_AS_OF)[0]
    assert outcome.action is PositionAction.ADD_FILLED
    return runtime, driver, outcome


# Risk contract -------------------------------------------------------------
# These read the pure engine, which is the only thing that decides how much an
# add may buy. The durable tests below never restate any of these numbers.

def snapshot(*, quantity="100", average="100", current="110", stop="105",
             base="10000", add_count=0) -> PositionSnapshot:
    return PositionSnapshot(SYMBOL, Decimal(quantity), Decimal(average), Decimal(current),
                            Currency.USD, initial_stop=Decimal("95"), active_stop=Decimal(stop),
                            add_count=add_count, base_notional_account_ccy=Decimal(base))


def evaluate_add(position: PositionSnapshot, *, equity="1000000", cash="1000000",
                 planned_initial_risk="500", requested=None,
                 engine: RiskEngine | None = None, daily=None):  # type: ignore[no-untyped-def]
    return (engine or RiskEngine()).evaluate_pyramid_add(
        decision=StrategyDecision(SYMBOL, DecisionType.ADD, "PYRAMID_CONFIRMATION", NOW,
                                  strategy_version="strategy_v0"),
        eligibility=TradingEligibility(True),
        account=AccountSnapshot(Decimal(equity), Decimal(cash), Currency.USD, NOW),
        portfolio=PortfolioSnapshot((position,), position.base_notional_account_ccy,
                                    Decimal("0"), NOW),
        daily_state=daily or DailyTradingState(DAY),
        planned_initial_risk=Decimal(planned_initial_risk), requested_notional_account_ccy=requested,
        created_at=NOW)


def test_risk_owns_the_add_quantity_without_any_requested_notional() -> None:
    """No caller has to invent a size: with nothing requested the engine sizes it."""
    result = evaluate_add(snapshot())
    assert result.approved and result.order_intent is not None
    intent, metrics = result.order_intent, result.metrics
    assert intent.side is OrderSide.BUY and intent.intent_type is IntentType.PYRAMID_ADD
    # The position already risks 100 * (100 - 105) = -500 to its raised stop, so a
    # 500 budget leaves 1000 of headroom over a 5-wide add distance: 200 shares.
    assert metrics.current_stop_risk == Decimal("-500")
    assert metrics.risk_budget == Decimal("500")
    assert metrics.final_quantity == Decimal("200")
    assert intent.quantity == Decimal("200")
    assert intent.account_notional == Decimal("22000")
    assert intent.reference_price == Decimal("110") and intent.initial_stop == Decimal("105")


def test_the_add_never_pushes_total_stop_risk_past_the_original_planned_risk() -> None:
    """Contract D, read straight off the metrics the approval was made on."""
    for stop, budget in (("105", "500"), ("101", "500"), ("108", "250")):
        result = evaluate_add(snapshot(stop=stop), planned_initial_risk=budget)
        assert result.approved, stop
        metrics = result.metrics
        assert metrics.post_add_stop_risk == metrics.current_stop_risk + metrics.planned_risk
        assert metrics.post_add_stop_risk <= Decimal(budget)


def test_the_incremental_stop_risk_cap_binds_before_any_notional_cap() -> None:
    """A notional cap alone cannot approve: the stop-risk cap is what bites here."""
    tight = evaluate_add(snapshot(stop="100"), planned_initial_risk="200")
    assert tight.approved
    metrics = tight.metrics
    # Existing leg risks nothing to a breakeven stop, so the whole 200 is available
    # over a 10-wide add distance: 20 shares, 2200 notional.
    assert metrics.current_stop_risk == Decimal("0")
    assert metrics.final_quantity == Decimal("20")
    assert metrics.final_notional_account_ccy == Decimal("2200")
    assert metrics.risk_capped_notional_account_ccy == Decimal("2200")
    assert metrics.final_notional_account_ccy < metrics.pyramid_capacity_remaining
    assert metrics.final_notional_account_ccy < metrics.symbol_capacity_remaining


def test_a_position_already_at_its_planned_risk_gets_no_add() -> None:
    """Price rose but the stop did not, so there is no room to add anything."""
    result = evaluate_add(snapshot(stop="99"), planned_initial_risk="50")
    assert result.rejection_reason is RiskRejectionReason.PYRAMID_RISK_BUDGET_EXHAUSTED
    assert evaluate_add(snapshot(), planned_initial_risk="0").rejection_reason \
        is RiskRejectionReason.PYRAMID_RISK_BUDGET_EXHAUSTED


def test_an_add_without_a_stop_below_the_price_is_not_sized_at_all() -> None:
    unstopped = PositionSnapshot(SYMBOL, Decimal("100"), Decimal("100"), Decimal("110"), Currency.USD)
    assert evaluate_add(unstopped).rejection_reason is RiskRejectionReason.INVALID_STOP_PRICE
    above = snapshot(stop="111")
    assert evaluate_add(above).rejection_reason is RiskRejectionReason.INVALID_STOP_PRICE


def test_the_pyramid_reserve_still_caps_an_add_the_risk_budget_would_allow() -> None:
    """The 20% reserve is unchanged; a generous budget does not get past it."""
    result = evaluate_add(snapshot(base="10000"), equity="100000", cash="100000",
                          planned_initial_risk="100000")
    assert result.approved
    assert result.metrics.pyramid_capacity_remaining == Decimal("20000.00")
    assert result.metrics.final_notional_account_ccy == Decimal("20000.00")
    assert result.metrics.risk_capped_notional_account_ccy > Decimal("20000.00")


def test_available_cash_caps_an_add_the_reserve_would_allow() -> None:
    result = evaluate_add(snapshot(base="10000"), equity="100000", cash="1500",
                          planned_initial_risk="100000")
    assert result.approved and result.metrics.final_notional_account_ccy == Decimal("1500")


def test_symbol_exposure_caps_an_add_before_the_reserve_does() -> None:
    """60% of equity is already in this symbol, so only the remainder can be added."""
    result = evaluate_add(snapshot(base="59000"), equity="100000", cash="100000",
                          planned_initial_risk="100000")
    assert result.approved
    assert result.metrics.symbol_capacity_remaining == Decimal("1000.00")
    assert result.metrics.final_notional_account_ccy == Decimal("1000.00")


def test_a_requested_notional_can_only_ask_for_less_than_the_risk_cap() -> None:
    smaller = evaluate_add(snapshot(stop="100"), planned_initial_risk="200", requested=Decimal("1000"))
    assert smaller.metrics.final_notional_account_ccy == Decimal("1000")
    larger = evaluate_add(snapshot(stop="100"), planned_initial_risk="200", requested=Decimal("99999"))
    assert larger.metrics.final_notional_account_ccy == Decimal("2200")


def test_averaging_down_and_the_single_add_limit_are_refused_by_risk() -> None:
    losing = snapshot(current="99")
    assert evaluate_add(losing).rejection_reason is RiskRejectionReason.POSITION_NOT_PROFITABLE
    flat = snapshot(current="100")
    assert evaluate_add(flat).rejection_reason is RiskRejectionReason.POSITION_NOT_PROFITABLE
    assert evaluate_add(snapshot(add_count=1)).rejection_reason is RiskRejectionReason.PYRAMID_LIMIT
    reserved = DailyTradingState(DAY, add_counts={SYMBOL: 1})
    assert evaluate_add(snapshot(), daily=reserved).rejection_reason is RiskRejectionReason.PYRAMID_LIMIT
    assert RiskConfig().max_pyramid_adds == 1


# Strategy trigger ----------------------------------------------------------

def open_state(**changes) -> StrategyState:  # type: ignore[no-untyped-def]
    base = StrategyState(SYMBOL, DAY, phase=StrategyPhase.POSITION_OPEN, entry_trading_date=DAY,
                         entry_price=ENTRY_FILL, initial_stop=INITIAL_STOP, active_stop=INITIAL_STOP,
                         highest_price_since_entry=ENTRY_FILL, holding_day_number=1,
                         last_market_as_of=ENTRY_FILL_AT)
    return base if not changes else type(base)(**{**base.__dict__, **changes})


def decide(close: Decimal, *, state: StrategyState | None = None):  # type: ignore[no-untyped-def]
    return StrategyV0Engine().evaluate_position(
        state=state or open_state(), bars=add_tape(close=close, fill=False),
        market_open=MARKET_OPEN, as_of=ADD_AS_OF, current_price=close,
        average_price=ENTRY_FILL, variant=VARIANT_CONFIGS["C"])


def test_the_add_signal_is_issued_exactly_at_one_r_and_not_below_it() -> None:
    exact = decide(TRIGGER_CLOSE)
    assert exact.decision.decision is DecisionType.ADD
    assert exact.decision.reason_code == StrategyReason.PYRAMID_CONFIRMATION.value
    assert exact.state.add_signal_issued and exact.state.add_count == 0
    below = decide(TRIGGER_CLOSE - Decimal("0.001"))
    assert below.decision.decision is DecisionType.HOLD
    assert not below.state.add_signal_issued


def test_no_add_signal_while_the_price_is_at_or_below_the_average_price() -> None:
    """Averaging down is refused by the strategy before risk is ever consulted."""
    for average in (TRIGGER_CLOSE, TRIGGER_CLOSE + Decimal("1")):
        evaluated = StrategyV0Engine().evaluate_position(
            state=open_state(), bars=add_tape(fill=False), market_open=MARKET_OPEN,
            as_of=ADD_AS_OF, current_price=TRIGGER_CLOSE, average_price=average,
            variant=VARIANT_CONFIGS["C"])
        assert evaluated.decision.decision is DecisionType.HOLD
        assert not evaluated.state.add_signal_issued


def test_no_second_add_signal_after_one_is_issued_or_filled() -> None:
    already = decide(TRIGGER_CLOSE + Decimal("1"), state=open_state(add_signal_issued=True))
    assert already.decision.decision is DecisionType.HOLD
    filled = decide(TRIGGER_CLOSE + Decimal("1"),
                    state=open_state(add_count=1, add_signal_issued=True,
                                     phase=StrategyPhase.PYRAMID_ADDED))
    assert filled.decision.decision is DecisionType.HOLD
    with pytest.raises(ValueError, match="one pyramid add"):
        StrategyLifecycleService.mark_add_filled(
            open_state(add_count=1, add_signal_issued=True), market_as_of=ADD_FILL_AT)


# Durable add ---------------------------------------------------------------

def test_the_add_fills_once_and_moves_cash_position_trade_and_state(factory) -> None:
    runtime, _driver, outcome = add_once(factory)
    order, broker = outcome.order, runtime.broker
    assert order.status is OrderStatus.FILLED and order.side is OrderSide.BUY
    assert order.intent_type == IntentType.PYRAMID_ADD.value
    assert order.filled_at == ADD_FILL_AT and order.rejection_reason is None
    fills = broker.get_fills(order.id)
    assert len(fills) == 1 and fills[0].quantity == order.filled_quantity

    position, trade = broker.get_position(SYMBOL), broker.get_trade(SYMBOL)
    assert position.quantity == QUANTITY + order.filled_quantity
    assert broker.cash < ENTRY_CASH
    assert trade.status is TradeStatus.OPEN
    assert trade.total_quantity == position.quantity
    assert trade.initial_quantity == QUANTITY

    row = durable_position(factory)
    assert row.quantity == position.quantity and row.average_price == position.average_price
    assert row.cost_basis == position.cost_basis
    stored = durable_trade(factory)
    assert stored.status == "OPEN" and stored.total_quantity == trade.total_quantity
    assert stored.average_entry_price == trade.average_entry_price
    assert stored.entry_notional == trade._entry_notional
    assert stored.total_cost == trade.total_cost
    with factory() as session:
        assert counts(session) == {"orders": 2, "fills": 2, "positions": 1, "trades": 1}
        from app.repositories.simulation import SimulationStateRepository
        account = SimulationStateRepository(session).get_account_by_id(1)
        assert account.cash == broker.cash and account.state_version == 2

    state = stored_state(factory)
    assert state.phase is StrategyPhase.PYRAMID_ADDED
    assert state.add_count == 1 and state.add_signal_issued
    assert state.last_market_as_of == ADD_FILL_AT


def test_the_add_does_not_rewrite_the_trade_s_planned_initial_risk(factory) -> None:
    """One R is the trade's own, so gross_r and net_r keep one denominator."""
    runtime = activate()
    entered_state = enter(runtime, factory)
    assert entered_state.phase is StrategyPhase.POSITION_OPEN
    original = runtime.broker.get_trade(SYMBOL).planned_initial_risk
    before = durable_trade(factory).planned_initial_risk
    assert before == original

    outcome = PositionLifecycleService(runtime).evaluate({SYMBOL: add_tape()}, as_of=ADD_AS_OF)[0]
    assert outcome.action is PositionAction.ADD_FILLED
    trade = runtime.broker.get_trade(SYMBOL)
    assert trade.planned_initial_risk == original
    assert durable_trade(factory).planned_initial_risk == original
    assert trade.net_r == trade.net_pnl / original


def test_the_driver_asks_risk_for_the_size_and_submits_exactly_one_buy(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    submissions = count_submissions(runtime.broker)
    outcome = PositionLifecycleService(runtime).evaluate({SYMBOL: add_tape()}, as_of=ADD_AS_OF)[0]
    assert outcome.action is PositionAction.ADD_FILLED
    assert len(submissions) == 1
    intent = submissions[0]
    assert intent.intent_type is IntentType.PYRAMID_ADD
    # The reference price is the completed bar the signal was judged on, and the
    # stop is the one the state actually enforces at that moment.
    assert intent.reference_price == TRIGGER_CLOSE
    assert intent.initial_stop == stored_state(factory).active_stop
    # The buy carries its own planned risk, and it cannot exceed the budget the
    # trade started with; a base entry's add used to carry a hard-coded zero.
    assert intent.risk_amount > 0
    assert intent.risk_amount <= runtime.broker.get_trade(SYMBOL).planned_initial_risk


def test_the_add_state_and_the_fill_commit_together(factory, monkeypatch) -> None:
    """A failing state write takes the buy, the cash, and the position with it."""
    runtime = activate()
    enter(runtime, factory)
    original = StrategyStateRepository.save

    def fail_on_add(self, state, *, updated_at):
        if state.phase is StrategyPhase.PYRAMID_ADDED:
            raise RuntimeError("state write failed")
        return original(self, state, updated_at=updated_at)

    monkeypatch.setattr(StrategyStateRepository, "save", fail_on_add)
    with pytest.raises(RuntimeError, match="state write failed"):
        PositionLifecycleService(runtime).evaluate({SYMBOL: add_tape()}, as_of=ADD_AS_OF)

    assert runtime.broker.cash == ENTRY_CASH
    assert runtime.broker.get_position(SYMBOL).quantity == QUANTITY
    assert runtime.broker.get_trade(SYMBOL).total_quantity == QUANTITY
    with factory() as session:
        assert counts(session) == {"orders": 1, "fills": 1, "positions": 1, "trades": 1}
    state = stored_state(factory)
    assert state.phase is StrategyPhase.POSITION_OPEN and state.add_count == 0


def test_no_next_bar_leaves_everything_untouched_but_keeps_the_signal(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    outcome = PositionLifecycleService(runtime).evaluate(
        {SYMBOL: add_tape(fill=False)}, as_of=ADD_AS_OF)[0]

    assert outcome.action is PositionAction.ADD_UNFILLED
    assert outcome.order.status is OrderStatus.REJECTED
    assert outcome.order.rejection_reason is RejectionReason.NO_NEXT_BAR
    assert runtime.broker.cash == ENTRY_CASH
    assert runtime.broker.get_position(SYMBOL).quantity == QUANTITY
    assert runtime.broker.get_trade(SYMBOL).total_quantity == QUANTITY
    with factory() as session:
        assert counts(session) == {"orders": 2, "fills": 1, "positions": 1, "trades": 1}
    state = stored_state(factory)
    assert state.phase is StrategyPhase.POSITION_OPEN
    assert state.add_signal_issued and state.add_count == 0


def test_an_unfilled_add_is_retried_on_a_later_bar_and_fills_once(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    driver = PositionLifecycleService(runtime)
    first = driver.evaluate({SYMBOL: add_tape(fill=False)}, as_of=ADD_AS_OF)[0]
    assert first.action is PositionAction.ADD_UNFILLED
    submissions = count_submissions(runtime.broker)

    retry = driver.evaluate(
        {SYMBOL: add_tape(fill=False, retry_fill=True)}, as_of=LATER_AS_OF)[0]
    assert retry.action is PositionAction.ADD_FILLED
    assert retry.reason == StrategyReason.PYRAMID_CONFIRMATION.value
    # The retry is still the add that was signalled at 10:01, so it fills on the
    # first bar after that signal rather than on the first bar after the retry.
    assert retry.order.filled_at == LATER_BAR_AT
    assert len(submissions) == 1
    assert stored_state(factory).add_count == 1

    # The add is spent: a later bar neither re-signals nor buys again.
    after = driver.evaluate(
        {SYMBOL: add_tape(fill=False, retry_fill=True)},
        as_of=LATER_FILL_AT + timedelta(minutes=1))[0]
    assert after.action is PositionAction.HOLD
    assert len(submissions) == 1
    assert stored_state(factory).add_count == 1


def test_a_stop_on_the_same_bar_outranks_an_outstanding_add(factory) -> None:
    """An add still owed never buys into a bar that breaches the stop."""
    runtime = activate()
    enter(runtime, factory)
    driver = PositionLifecycleService(runtime)
    assert driver.evaluate({SYMBOL: add_tape(fill=False)}, as_of=ADD_AS_OF)[0].action \
        is PositionAction.ADD_UNFILLED
    breach = add_tape(fill=False) + (
        bar(LATER_BAR_AT, open_=100.0, high=100.2, low=98.5, close=99.0),
        bar(LATER_FILL_AT, open_=98.8, high=99.0, low=98.5, close=98.9))
    outcome = driver.evaluate({SYMBOL: breach}, as_of=LATER_AS_OF)[0]
    assert outcome.action is PositionAction.EXIT_FILLED
    assert runtime.broker.get_position(SYMBOL) is None
    assert stored_state(factory).add_count == 0


# Restart -------------------------------------------------------------------

def test_restart_before_any_add_signal_keeps_the_position_addable(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    clear_active_sim_broker()

    restarted = activate()
    state = stored_state(factory)
    assert state.add_count == 0 and not state.add_signal_issued
    outcome = PositionLifecycleService(restarted).evaluate({SYMBOL: add_tape()}, as_of=ADD_AS_OF)[0]
    assert outcome.action is PositionAction.ADD_FILLED
    assert stored_state(factory).add_count == 1


def test_restart_with_an_issued_but_unfilled_add_keeps_the_signal_and_retries(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    assert PositionLifecycleService(runtime).evaluate(
        {SYMBOL: add_tape(fill=False)}, as_of=ADD_AS_OF)[0].action is PositionAction.ADD_UNFILLED
    clear_active_sim_broker()

    restarted = activate()
    assert restarted.broker.cash == ENTRY_CASH
    assert restarted.broker.get_position(SYMBOL).quantity == QUANTITY
    survived = stored_state(factory)
    assert survived.add_signal_issued and survived.add_count == 0
    assert survived.phase is StrategyPhase.POSITION_OPEN

    outcome = PositionLifecycleService(restarted).evaluate(
        {SYMBOL: add_tape(fill=False, retry_fill=True)}, as_of=LATER_AS_OF)[0]
    assert outcome.action is PositionAction.ADD_FILLED
    assert stored_state(factory).add_count == 1


def test_restart_after_a_fill_restores_the_totals_and_blocks_a_second_add(factory) -> None:
    runtime, _driver, _outcome = add_once(factory)
    quantity = runtime.broker.get_position(SYMBOL).quantity
    average = runtime.broker.get_position(SYMBOL).average_price
    cost_basis = runtime.broker.get_position(SYMBOL).cost_basis
    trade = runtime.broker.get_trade(SYMBOL)
    cash = runtime.broker.cash
    clear_active_sim_broker()

    restarted = activate()
    position = restarted.broker.get_position(SYMBOL)
    assert restarted.broker.cash == cash
    assert (position.quantity, position.average_price, position.cost_basis) \
        == (quantity, average, cost_basis)
    reloaded = restarted.broker.get_trade(SYMBOL)
    assert reloaded.total_quantity == trade.total_quantity
    assert reloaded.initial_quantity == trade.initial_quantity
    assert reloaded.average_entry_price == trade.average_entry_price
    assert reloaded.planned_initial_risk == trade.planned_initial_risk
    assert reloaded.total_cost == trade.total_cost

    state = stored_state(factory)
    assert state.phase is StrategyPhase.PYRAMID_ADDED and state.add_count == 1
    submissions = count_submissions(restarted.broker)
    later = PositionLifecycleService(restarted).evaluate(
        {SYMBOL: add_tape(retry_fill=True)}, as_of=LATER_AS_OF)[0]
    assert later.action is PositionAction.HOLD
    assert submissions == []
    assert stored_state(factory).add_count == 1
    with factory() as session:
        assert counts(session) == {"orders": 2, "fills": 2, "positions": 1, "trades": 1}


def test_a_stop_after_an_add_still_closes_the_whole_position(factory) -> None:
    """The hard stop is unchanged by pyramiding: it sells everything that is held."""
    runtime, driver, _outcome = add_once(factory)
    quantity = runtime.broker.get_position(SYMBOL).quantity
    outcome = driver.evaluate({SYMBOL: add_tape(stop_after=True)}, as_of=LATER_AS_OF + timedelta(minutes=4))[0]
    assert outcome.action is PositionAction.EXIT_FILLED
    assert outcome.order.filled_quantity == quantity
    assert runtime.broker.get_position(SYMBOL) is None
    trade = runtime.broker.get_trade(SYMBOL)
    assert trade.status is TradeStatus.CLOSED
    assert trade.total_quantity == quantity
    assert stored_state(factory).phase is StrategyPhase.EXITED


# Reservations and safety ---------------------------------------------------

def test_the_durable_add_path_commits_no_daily_risk_reservation(factory) -> None:
    """Nothing is reserved ahead of the broker, so nothing can go stale behind it."""
    _runtime, _driver, _outcome = add_once(factory)
    with factory() as session:
        loaded = DailyRiskRepository(session).load(DAY)
    assert loaded.add_counts == {} and loaded.pyramid_notional_reserved == Decimal("0")


def test_a_reservation_taken_for_an_add_that_never_filled_is_released(tmp_path) -> None:
    """The RiskService seam commits before the broker, so it must give back."""
    from sqlalchemy.orm import Session

    from app.core.database import Base, create_db_engine
    from app.services.risk import RiskService

    db_engine = create_db_engine(f"sqlite:///{tmp_path / 'risk.sqlite3'}")
    Base.metadata.create_all(db_engine)
    with Session(db_engine) as session:
        repository = DailyRiskRepository(session)
        repository.reserve_entry(trading_date=DAY, symbol=SYMBOL, issued_at=NOW,
                                 planned_risk=Decimal("500"), base_notional=Decimal("10000"),
                                 risk_version="risk_v0", strategy_version="strategy_v0")
        session.commit()
        service = RiskService(RiskEngine(), repository)
        approved = service.reserve_pyramid_add(
            trading_date=DAY,
            decision=StrategyDecision(SYMBOL, DecisionType.ADD, "PYRAMID_CONFIRMATION", NOW,
                                      strategy_version="strategy_v0"),
            eligibility=TradingEligibility(True),
            account=AccountSnapshot(Decimal("1000000"), Decimal("1000000"), Currency.USD, NOW),
            portfolio=PortfolioSnapshot((snapshot(),), Decimal("10000"), Decimal("0"), NOW),
            planned_initial_risk=Decimal("500"), created_at=NOW)
        assert approved.approved
        held = repository.load(DAY)
        assert held.add_counts == {SYMBOL: 1}
        # While the reservation stands the symbol is at its limit, which is exactly
        # why an add that never filled cannot be allowed to keep it.
        assert RiskEngine().evaluate_pyramid_add(
            decision=StrategyDecision(SYMBOL, DecisionType.ADD, "PYRAMID_CONFIRMATION", NOW,
                                      strategy_version="strategy_v0"),
            eligibility=TradingEligibility(True),
            account=AccountSnapshot(Decimal("1000000"), Decimal("1000000"), Currency.USD, NOW),
            portfolio=PortfolioSnapshot((snapshot(),), Decimal("10000"), Decimal("0"), NOW),
            daily_state=held, planned_initial_risk=Decimal("500"),
            created_at=NOW).rejection_reason is RiskRejectionReason.PYRAMID_LIMIT

        service.release_pyramid_add(
            trading_date=DAY, symbol=SYMBOL,
            amount=approved.metrics.final_notional_account_ccy, updated_at=NOW)
        released = repository.load(DAY)
        assert released.add_counts == {SYMBOL: 0}
        assert released.pyramid_notional_reserved == Decimal("0")
    db_engine.dispose()


@pytest.mark.asyncio
async def test_a_runtime_tick_buys_the_add_without_any_kiwoom_ordering(factory, monkeypatch) -> None:
    runtime = activate()
    enter(runtime, factory)
    monkeypatch.setattr(KiwoomMarketDataClient, "request",
                        lambda *a, **k: pytest.fail("the add path reached the Kiwoom client"))

    class Provider:
        def get_minute_bars(self, symbols, start=None, end=None, session=None):
            assert symbols == [SYMBOL]
            return add_tape()

    owner = PositionManagementRuntime(runtime, Provider)
    # A tick only ever sees completed bars, so the bar an add fills on cannot be
    # visible at the tick that signals it; the next tick is what settles it.
    first = await owner.run_once(as_of=ADD_AS_OF)
    assert first[0].action is PositionAction.ADD_UNFILLED
    assert first[0].order.rejection_reason is RejectionReason.NO_NEXT_BAR
    settled = await owner.run_once(as_of=ADD_FILL_AT + timedelta(minutes=1))
    assert settled[0].action is PositionAction.ADD_FILLED
    assert settled[0].order.filled_at == ADD_FILL_AT
    assert stored_state(factory).add_count == 1
    with factory() as session:
        assert counts(session) == {"orders": 3, "fills": 2, "positions": 1, "trades": 1}
