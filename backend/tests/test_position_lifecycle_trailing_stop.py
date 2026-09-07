"""P1-G3: Strategy V0 trailing state drives the durable position lifecycle."""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.broker.domain import OrderStatus, RejectionReason, TradeStatus
from app.models.simulation import SimulationTradeRecord
from app.repositories.strategy import StrategyStateRepository
from app.services.position_lifecycle import PositionAction, PositionLifecycleService
from app.services.position_management_runtime import PositionManagementRuntime
from app.services.simulation_runtime import clear_active_sim_broker
from app.strategy.config import VARIANT_CONFIGS
from app.strategy.domain import DecisionType
from app.strategy.engine import StrategyReason, StrategyV0Engine
from app.strategy.lifecycle import StrategyPhase, StrategyState, TrailingProfile

from backend.tests.test_position_lifecycle_hard_stop import (
    DAY,
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
    durable_shape,
    enter,
    factory,
    ownership,
    session_bars,
    stored_state,
)


ONE_R = ENTRY_FILL - INITIAL_STOP
ACTIVATION_PRICE = ENTRY_FILL + ONE_R
ACTIVATION_BAR_AT = datetime(2026, 7, 2, 10, 0, tzinfo=MARKET_OPEN.tzinfo)
ACTIVATION_AS_OF = ACTIVATION_BAR_AT + timedelta(minutes=1)
BREACH_BAR_AT = ACTIVATION_BAR_AT + timedelta(minutes=1)
BREACH_AS_OF = BREACH_BAR_AT + timedelta(minutes=1)
# The breach bar is completed/evaluated at 10:02.  Conservative execution is
# strictly after that signal time, so 10:03 is the first eligible fill bar.
EXIT_BAR_AT = ACTIVATION_BAR_AT + timedelta(minutes=3)


def trailing_tape(*, high: Decimal = ACTIVATION_PRICE,
                  breach: bool = False, include_exit: bool = False):
    """The known P1-F tape plus a +1R high and optional later stop/fill bars."""
    activation = bar(
        ACTIVATION_BAR_AT,
        open_=float(high - Decimal("0.4")),
        high=float(high),
        low=float(high - Decimal("0.8")),
        close=float(high - Decimal("0.2")),
    )
    result = session_bars(activation)
    if breach:
        # The exact raised stop depends on PIT ATR, so derive only test input from
        # the authority engine; the lifecycle assertions still read persisted truth.
        state = replace(
            StrategyState(
                SYMBOL, DAY, phase=StrategyPhase.POSITION_OPEN,
                entry_trading_date=DAY, entry_price=ENTRY_FILL,
                initial_stop=INITIAL_STOP, active_stop=INITIAL_STOP,
                highest_price_since_entry=ENTRY_FILL, holding_day_number=1,
                trailing_profile=TrailingProfile.NORMAL,
            ),
            last_market_as_of=ENTRY_FILL_AT,
        )
        raised = StrategyV0Engine().evaluate_position(
            state=state, bars=result, market_open=MARKET_OPEN,
            as_of=ACTIVATION_AS_OF, current_price=high - Decimal("0.2"),
            average_price=ENTRY_FILL, variant=VARIANT_CONFIGS["C"],
        ).state.active_stop
        assert raised is not None and raised > INITIAL_STOP
        result += (bar(BREACH_BAR_AT, open_=float(raised + Decimal("0.2")),
                       high=float(raised + Decimal("0.4")), low=float(raised),
                       close=float(raised + Decimal("0.1"))),)
    if include_exit:
        result += (bar(EXIT_BAR_AT, open_=104.0, high=104.2, low=103.8, close=104.0),)
    return result


def activate_trailing(runtime, factory):
    driver = PositionLifecycleService(runtime)
    outcome = driver.evaluate({SYMBOL: trailing_tape()}, as_of=ACTIVATION_AS_OF)[0]
    assert outcome.action in {PositionAction.HOLD, PositionAction.ADD_UNFILLED}
    state = stored_state(factory)
    assert state.highest_price_since_entry == ACTIVATION_PRICE
    assert state.active_stop > INITIAL_STOP
    return state


def test_actual_fill_is_the_exact_one_r_authority_and_stop_is_monotonic(factory) -> None:
    assert ONE_R == Decimal("3.153")
    assert ACTIVATION_PRICE == Decimal("105.306")
    base = StrategyState(
        SYMBOL, DAY, phase=StrategyPhase.POSITION_OPEN,
        entry_trading_date=DAY, entry_price=ENTRY_FILL, initial_stop=INITIAL_STOP,
        active_stop=INITIAL_STOP, highest_price_since_entry=ENTRY_FILL,
        holding_day_number=1, add_signal_issued=True,
    )
    engine = StrategyV0Engine()
    below = trailing_tape(high=ACTIVATION_PRICE - Decimal("0.001"))
    first = engine.evaluate_position(
        state=base, bars=below, market_open=MARKET_OPEN, as_of=ACTIVATION_AS_OF,
        current_price=ACTIVATION_PRICE - Decimal("0.2"), average_price=ENTRY_FILL,
        variant=VARIANT_CONFIGS["C"],
    )
    assert first.state.active_stop == INITIAL_STOP

    exact = engine.evaluate_position(
        state=base, bars=trailing_tape(), market_open=MARKET_OPEN,
        as_of=ACTIVATION_AS_OF, current_price=ACTIVATION_PRICE - Decimal("0.2"),
        average_price=ENTRY_FILL, variant=VARIANT_CONFIGS["C"],
    )
    assert exact.atr is not None
    assert exact.state.active_stop == ACTIVATION_PRICE - Decimal("1.5") * exact.atr
    assert exact.state.highest_price_since_entry == ACTIVATION_PRICE

    lower_high_bar = bar(
        BREACH_BAR_AT, open_=105.0, high=105.1,
        low=float(exact.state.active_stop + Decimal("0.01")), close=104.9,
    )
    lower = engine.evaluate_position(
        state=exact.state, bars=trailing_tape() + (lower_high_bar,),
        market_open=MARKET_OPEN, as_of=BREACH_AS_OF,
        current_price=Decimal("104.9"), average_price=ENTRY_FILL,
        variant=VARIANT_CONFIGS["C"],
    )
    assert lower.state.highest_price_since_entry == ACTIVATION_PRICE
    assert lower.state.active_stop >= exact.state.active_stop


def test_trailing_update_is_durable_and_same_bar_is_idempotent(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    submissions = count_submissions(runtime.broker)
    raised = activate_trailing(runtime, factory)
    assert submissions == []

    restarted = PositionLifecycleService(runtime).evaluate(
        {SYMBOL: trailing_tape()}, as_of=ACTIVATION_AS_OF
    )[0]
    assert restarted.action is PositionAction.SKIPPED
    assert restarted.reason == "already evaluated through this bar"
    assert stored_state(factory).active_stop == raised.active_stop
    assert submissions == []


@pytest.mark.asyncio
async def test_runtime_tick_automatically_persists_the_trailing_update(factory) -> None:
    runtime = activate()
    enter(runtime, factory)

    class Provider:
        def get_minute_bars(self, symbols, start=None, end=None, session=None):
            assert symbols == [SYMBOL]
            return trailing_tape()

    owner = PositionManagementRuntime(runtime, Provider)
    outcomes = await owner.run_once(as_of=ACTIVATION_AS_OF)
    assert outcomes[0].action in {PositionAction.HOLD, PositionAction.ADD_UNFILLED}
    state = stored_state(factory)
    assert state.highest_price_since_entry == ACTIVATION_PRICE
    assert state.active_stop > INITIAL_STOP
    with factory() as session:
        assert counts(session) == {"orders": 1, "fills": 1, "positions": 1, "trades": 1}


@pytest.mark.parametrize("restart_before_activation,restart_after_activation", [
    (True, False), (False, True),
])
def test_restart_before_or_after_activation_closes_on_the_raised_stop(
    factory, restart_before_activation, restart_after_activation,
) -> None:
    runtime = activate()
    enter(runtime, factory)
    if restart_before_activation:
        clear_active_sim_broker()
        runtime = activate()
    raised = activate_trailing(runtime, factory)
    if restart_after_activation:
        clear_active_sim_broker()
        runtime = activate()
        restored = stored_state(factory)
        assert restored.active_stop == raised.active_stop
        assert restored.highest_price_since_entry == raised.highest_price_since_entry

    submissions = count_submissions(runtime.broker)
    outcome = PositionLifecycleService(runtime).evaluate(
        {SYMBOL: trailing_tape(breach=True, include_exit=True)}, as_of=BREACH_AS_OF
    )[0]
    assert outcome.action is PositionAction.EXIT_FILLED
    assert outcome.reason == StrategyReason.TRAILING_STOP.value
    assert outcome.order.filled_at == EXIT_BAR_AT
    assert len(submissions) == 1
    assert runtime.broker.get_position(SYMBOL) is None
    assert runtime.broker.get_trade(SYMBOL).status is TradeStatus.CLOSED
    assert stored_state(factory).phase is StrategyPhase.EXITED
    with factory() as session:
        trade = session.scalars(select(SimulationTradeRecord)).one()
        assert trade.status == "CLOSED" and trade.exit_reason == "TRAILING_STOP"
        assert counts(session) == {"orders": 2, "fills": 2, "positions": 0, "trades": 1}


def test_trailing_no_next_bar_retries_once_without_losing_the_raised_stop(factory) -> None:
    runtime = activate()
    enter(runtime, factory)
    raised = activate_trailing(runtime, factory)
    driver = PositionLifecycleService(runtime)
    submissions = count_submissions(runtime.broker)

    first = driver.evaluate(
        {SYMBOL: trailing_tape(breach=True)}, as_of=BREACH_AS_OF
    )[0]
    assert first.action is PositionAction.EXIT_UNFILLED
    assert first.order.status is OrderStatus.REJECTED
    assert first.order.rejection_reason is RejectionReason.NO_NEXT_BAR
    signalled = stored_state(factory)
    assert signalled.phase is StrategyPhase.EXIT_SIGNALLED
    assert signalled.active_stop == raised.active_stop
    assert signalled.highest_price_since_entry == raised.highest_price_since_entry
    assert runtime.broker.get_position(SYMBOL).quantity == QUANTITY

    retry = driver.evaluate(
        {SYMBOL: trailing_tape(breach=True, include_exit=True)},
        as_of=EXIT_BAR_AT + timedelta(minutes=1),
    )[0]
    assert retry.action is PositionAction.EXIT_FILLED
    assert retry.reason == "TRAILING_STOP"
    assert retry.order.filled_at == EXIT_BAR_AT
    assert len(submissions) == 2
    assert driver.evaluate({}, as_of=EXIT_BAR_AT + timedelta(minutes=2)) == ()


def test_trailing_exit_state_failure_rolls_back_sell_and_broker(factory, monkeypatch) -> None:
    runtime = activate()
    enter(runtime, factory)
    raised = activate_trailing(runtime, factory)
    original = StrategyStateRepository.save

    def fail_on_exit(self, state, *, updated_at):
        if state.phase is StrategyPhase.EXITED:
            raise RuntimeError("state write failed")
        return original(self, state, updated_at=updated_at)

    monkeypatch.setattr(StrategyStateRepository, "save", fail_on_exit)
    with pytest.raises(RuntimeError, match="state write failed"):
        PositionLifecycleService(runtime).evaluate(
            {SYMBOL: trailing_tape(breach=True, include_exit=True)}, as_of=BREACH_AS_OF
        )

    assert runtime.broker.get_position(SYMBOL).quantity == QUANTITY
    assert runtime.broker.get_trade(SYMBOL).status is TradeStatus.OPEN
    state = stored_state(factory)
    assert state.phase is StrategyPhase.POSITION_OPEN
    assert state.active_stop == raised.active_stop
    with factory() as session:
        assert counts(session) == {"orders": 1, "fills": 1, "positions": 1, "trades": 1}
