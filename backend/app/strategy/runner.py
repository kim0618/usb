"""Minimal common Strategy/Risk/SimBroker lifecycle orchestration.

This is intentionally a one-candidate lifecycle coordinator, not a backtester.
It owns signal-to-fill reconciliation while Strategy, Risk, and Broker remain
separate sources of truth.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.broker.domain import OrderStatus, SimOrder
from app.broker.sim import SimBroker
from app.execution.domain import IntentType
from app.market.calendar import MarketCalendar
from app.market.domain import MinuteBar
from app.risk.domain import (
    AccountSnapshot, Currency, DailyTradingState, OvernightAction,
    PortfolioSnapshot, PositionSnapshot, RiskEvaluation,
)
from app.risk.engine import RiskEngine
from app.services.execution import ExecutionService
from app.services.risk import RiskService
from app.services.simulation_runtime import SimulationRuntimeContext, get_active_runtime
from app.services.strategy import StrategyLifecycleService
from app.strategy.domain import DecisionType, StrategyDecision, TradingEligibility
from app.strategy.lifecycle import StrategyBook, StrategyPhase, StrategyState
from app.strategy.session_policy import SessionPolicy


@dataclass(frozen=True)
class LifecycleExecution:
    state: StrategyState
    risk: RiskEvaluation | None
    order: SimOrder | None


class StrategyLifecycleRunner:
    """Common actual/shadow execution path with book-specific risk state."""

    def __init__(self, broker: SimBroker, *, risk_engine: RiskEngine | None = None,
                 calendar: MarketCalendar | None = None,
                 actual_risk_service: RiskService | None = None,
                 runtime: SimulationRuntimeContext | None = None) -> None:
        if runtime is not None and runtime.broker is not broker:
            # A runner executing against a broker the runtime does not own would
            # persist one broker's figures under another broker's account.
            raise ValueError("runtime context owns a different broker")
        self.broker = broker
        self.runtime = runtime
        self.execution = ExecutionService(broker)
        self.risk = risk_engine or RiskEngine()
        self.calendar = calendar or MarketCalendar()
        self.session_policy = SessionPolicy(self.calendar)
        self.actual_risk_service = actual_risk_service
        self._shadow_risk: dict[tuple[object, str], DailyTradingState] = {}

    @classmethod
    def for_active_runtime(cls, **kwargs) -> "StrategyLifecycleRunner":
        """Bind to the broker this process already activated, never a new one."""
        runtime = get_active_runtime()
        if runtime is None:
            raise RuntimeError("no active simulation runtime")
        return cls(runtime.broker, runtime=runtime, **kwargs)

    def _submit(self, intent, market_bars, *, created_at: datetime,
                on_persist: Callable[[Session, SimOrder], None] | None = None) -> SimOrder:
        """The single broker submission behind one lifecycle execution event.

        Every execution path funnels through here so the broker is asked to fill
        exactly once. A durable runtime records that same submission inside the
        execution service's own transaction; a runner without a durable account
        (shadow variants, replay smoke) executes without persistence as before.

        ``on_persist`` joins the caller's own write to that transaction. Without a
        durable runtime there is no transaction to join, so asking for one is an
        error rather than a silently dropped write.
        """
        runtime = self.runtime
        if runtime is None or not runtime.durable:
            if on_persist is not None:
                raise RuntimeError("durable state persistence requires a durable runtime")
            return self.execution.execute(intent, market_bars)
        with runtime.session_factory() as session:
            durable = ExecutionService(self.broker, session, account_id=runtime.account_id)
            return durable.execute_and_persist(intent, market_bars, updated_at=created_at,
                                               on_persist=on_persist)

    def risk_state(self, state: StrategyState, actual_state: DailyTradingState | None = None) -> DailyTradingState:
        if state.book is StrategyBook.ACTUAL:
            if actual_state is None:
                raise ValueError("actual lifecycle requires persistent daily risk state")
            return actual_state
        key = (state.trading_date, state.variant)
        return self._shadow_risk.setdefault(key, DailyTradingState(state.trading_date))

    def execute_entry(self, *, state: StrategyState, decision: StrategyDecision,
                      account: AccountSnapshot, portfolio: PortfolioSnapshot,
                      market_bars: tuple[MinuteBar, ...], instrument_currency: Currency,
                      created_at: datetime, actual_risk_state: DailyTradingState | None = None,
                      on_state: Callable[[Session, StrategyState], None] | None = None) -> LifecycleExecution:
        if not self.session_policy.permissions_at(decision.market_as_of).new_entry:
            return LifecycleExecution(state, None, None)
        eligibility = TradingEligibility(False if state.book is StrategyBook.SHADOW else True,
                                         book=state.book.value)
        if state.book is StrategyBook.ACTUAL and self.actual_risk_service is not None:
            evaluation = self.actual_risk_service.prepare_base_entry(
                trading_date=state.trading_date, decision=decision, eligibility=eligibility,
                account=account, portfolio=portfolio, entry_price=state.entry_price or Decimal("0"),
                stop_price=state.initial_stop or Decimal("0"), instrument_currency=instrument_currency,
                created_at=created_at)
            daily = actual_risk_state or DailyTradingState(state.trading_date)
        else:
            daily = self.risk_state(state, actual_risk_state)
            evaluation = self.risk.evaluate_base_entry(
                decision=decision, eligibility=eligibility, account=account, portfolio=portfolio,
                daily_state=daily, entry_price=state.entry_price or Decimal("0"),
                stop_price=state.initial_stop or Decimal("0"), instrument_currency=instrument_currency,
                created_at=created_at,
            )
        if not evaluation.approved or evaluation.order_intent is None:
            return LifecycleExecution(state, evaluation, None)
        fill_bars = self.session_policy.regular_fill_bars(market_bars, as_of=decision.market_as_of)
        settled: list[StrategyState] = []
        order = self._submit(evaluation.order_intent, fill_bars, created_at=created_at,
                             on_persist=self._settler(state, self._entry_state, settled, on_state))
        filled = self.broker.get_fills(order.id)
        if not filled:
            return LifecycleExecution(state, evaluation, order)
        next_state = settled[0] if settled else self._entry_state(state, order)
        if state.book is StrategyBook.SHADOW and evaluation.metrics is not None:
            self._shadow_risk[(state.trading_date, state.variant)] = DailyTradingState(
                state.trading_date, daily.attempted_symbols | {state.symbol},
                daily.planned_risk_reserved + evaluation.metrics.planned_risk,
                daily.base_notional_reserved + evaluation.metrics.final_notional_account_ccy,
                daily.pyramid_notional_reserved, dict(daily.add_counts))
        return LifecycleExecution(next_state, evaluation, order)

    def execute_add(self, *, state: StrategyState, decision: StrategyDecision,
                    account: AccountSnapshot, portfolio: PortfolioSnapshot,
                    planned_initial_risk: Decimal, market_bars: tuple[MinuteBar, ...],
                    created_at: datetime, requested_notional: Decimal | None = None,
                    actual_risk_state: DailyTradingState | None = None,
                    on_state: Callable[[Session, StrategyState], None] | None = None) -> LifecycleExecution:
        """Submit one pyramid add; RiskEngine, not this runner, decides its size.

        ``requested_notional`` stays optional because there is no second opinion
        to offer: with nothing requested the risk engine sizes the add from the
        stop-risk budget it already owns.

        ``on_state`` joins the add's state - add_count and the PYRAMID_ADDED
        phase - to the fill's own transaction, so the two can never disagree.
        """
        if not self.session_policy.permissions_at(decision.market_as_of).pyramid:
            return LifecycleExecution(state, None, None)
        eligibility = TradingEligibility(False if state.book is StrategyBook.SHADOW else True,
                                         book=state.book.value)
        reserved = False
        if state.book is StrategyBook.ACTUAL and self.actual_risk_service is not None:
            evaluation = self.actual_risk_service.reserve_pyramid_add(
                trading_date=state.trading_date, decision=decision, eligibility=eligibility,
                account=account, portfolio=portfolio, planned_initial_risk=planned_initial_risk,
                requested_notional_account_ccy=requested_notional, created_at=created_at)
            reserved = evaluation.approved
            daily = actual_risk_state or DailyTradingState(state.trading_date)
        else:
            daily = self.risk_state(state, actual_risk_state)
            evaluation = self.risk.evaluate_pyramid_add(
                decision=decision, eligibility=eligibility, account=account, portfolio=portfolio,
                daily_state=daily, planned_initial_risk=planned_initial_risk,
                requested_notional_account_ccy=requested_notional, created_at=created_at,
            )
        if not evaluation.approved or evaluation.order_intent is None:
            return LifecycleExecution(state, evaluation, None)
        fill_bars = self.session_policy.regular_fill_bars(market_bars, as_of=decision.market_as_of)
        settled: list[StrategyState] = []
        try:
            order = self._submit(evaluation.order_intent, fill_bars, created_at=created_at,
                                 on_persist=self._settler(state, self._add_state, settled, on_state))
        except BaseException:
            self._release(reserved, state, evaluation, created_at)
            raise
        fills = self.broker.get_fills(order.id)
        if not fills:
            # The signal itself is not spent by a rejection: the state returned is
            # the one that came in, so add_count and the phase are untouched.
            self._release(reserved, state, evaluation, created_at)
            return LifecycleExecution(state, evaluation, order)
        next_state = settled[0] if settled else self._add_state(state, order)
        if state.book is StrategyBook.SHADOW and evaluation.metrics is not None:
            self._shadow_risk[(state.trading_date, state.variant)] = replace(
                daily, pyramid_notional_reserved=daily.pyramid_notional_reserved
                + evaluation.metrics.final_notional_account_ccy,
                add_counts={**daily.add_counts, state.symbol: 1})
        return LifecycleExecution(next_state, evaluation, order)

    def _release(self, reserved: bool, state: StrategyState, evaluation: RiskEvaluation,
                 created_at: datetime) -> None:
        if not reserved or self.actual_risk_service is None or evaluation.metrics is None:
            return
        self.actual_risk_service.release_pyramid_add(
            trading_date=state.trading_date, symbol=state.symbol,
            amount=evaluation.metrics.final_notional_account_ccy, updated_at=created_at)

    def execute_exit(self, *, state: StrategyState, decision: StrategyDecision,
                     position: PositionSnapshot, market_bars: tuple[MinuteBar, ...],
                     created_at: datetime, quantity: Decimal | None = None,
                     on_state: Callable[[Session, StrategyState], None] | None = None) -> LifecycleExecution:
        if state.phase is not StrategyPhase.EXIT_SIGNALLED:
            state = state.transition(StrategyPhase.EXIT_SIGNALLED)
        intent = self.risk.build_exit_intent(decision=decision, position=position,
                                             created_at=created_at, quantity=quantity)
        # An exit fills in the same regular session an entry would, so a stop does
        # not settle on a thin premarket or postmarket print.
        fill_bars = self.session_policy.regular_fill_bars(market_bars, as_of=decision.market_as_of)
        settled: list[StrategyState] = []
        order = self._submit(intent, fill_bars, created_at=created_at,
                             on_persist=self._settler(state, self._exit_state, settled, on_state))
        if not self.broker.get_fills(order.id):
            return LifecycleExecution(state, None, order)
        return LifecycleExecution(settled[0] if settled else self._exit_state(state, order), None, order)

    def _settler(self, state: StrategyState, resolve, settled: list[StrategyState],
                 on_state: Callable[[Session, StrategyState], None] | None):
        """Commit the phase a fill produces with the fill itself.

        Without this the two would be separate transactions, and a crash between
        them would leave a sold position still recorded as open.
        """
        if on_state is None:
            return None

        def persist(session: Session, order: SimOrder) -> None:
            resolved = resolve(state, order)
            settled.append(resolved)
            on_state(session, resolved)

        return persist

    def _entry_state(self, state: StrategyState, order: SimOrder) -> StrategyState:
        fills = self.broker.get_fills(order.id)
        if not fills:
            return state
        position = self.broker.get_position(state.symbol)
        assert position is not None
        return StrategyLifecycleService.mark_entry_filled(
            state, fill_price=position.average_price, market_as_of=fills[-1].filled_at)

    def _add_state(self, state: StrategyState, order: SimOrder) -> StrategyState:
        fills = self.broker.get_fills(order.id)
        if not fills:
            # A rejected add leaves the signal standing and the count at zero, so
            # a later bar can retry the same add without it counting twice.
            return state
        return StrategyLifecycleService.mark_add_filled(state, market_as_of=fills[-1].filled_at)

    def _exit_state(self, state: StrategyState, order: SimOrder) -> StrategyState:
        fills = self.broker.get_fills(order.id)
        if not fills:
            # A rejected exit stays signalled: the position is still open and the
            # next bar must be allowed to retry it.
            return state
        broker_position = self.broker.get_position(state.symbol)
        remaining = Decimal("0") if broker_position is None else broker_position.quantity
        return StrategyLifecycleService.mark_exit_filled(
            state, market_as_of=fills[-1].filled_at, remaining_quantity=remaining)

    def apply_overnight_risk(self, *, state: StrategyState, decision: StrategyDecision,
                             account: AccountSnapshot, portfolio: PortfolioSnapshot,
                             position: PositionSnapshot, market_bars: tuple[MinuteBar, ...],
                             created_at: datetime,
                             on_state: Callable[[Session, StrategyState], None] | None = None) -> LifecycleExecution:
        """Size a carry the risk engine has to approve; it holds, trims, or leaves.

        ``on_state`` joins the phase a reduction produces - OVERNIGHT_HELD, or the
        still-pending review a rejection leaves - to the fill's own transaction, so
        a sold quantity and the state that describes it can never disagree.
        """
        review = state if state.phase is StrategyPhase.OVERNIGHT_REVIEW else state.transition(StrategyPhase.OVERNIGHT_REVIEW)
        outcome = self.risk.evaluate_overnight_notional(
            account=account, portfolio=portfolio,
            proposed_notional=position.quantity * position.current_price)
        if outcome.action is OvernightAction.HOLD_FULL:
            return LifecycleExecution(review.transition(StrategyPhase.OVERNIGHT_HELD, overnight=True), None, None)
        if outcome.action is OvernightAction.EXIT_ALL:
            exit_decision = replace(decision, decision=DecisionType.EXIT)
            return self.execute_exit(state=review, decision=exit_decision, position=position,
                                     market_bars=market_bars, created_at=created_at, on_state=on_state)
        reduction_quantity = outcome.reduce_notional / position.current_price
        exit_decision = replace(decision, decision=DecisionType.EXIT, reason_code="OVERNIGHT_REDUCTION")
        intent = self.risk.build_exit_intent(decision=exit_decision, position=position,
                                             quantity=reduction_quantity, created_at=created_at)
        # A reduction is a sell like every other sell this runner submits, so it
        # settles inside the signal's own regular session. Handing the broker the
        # raw tape would let an overnight trim fill on a premarket or postmarket
        # print that no other exit is allowed to use.
        fill_bars = self.session_policy.regular_fill_bars(market_bars, as_of=exit_decision.market_as_of)
        settled: list[StrategyState] = []
        order = self._submit(intent, fill_bars, created_at=created_at,
                             on_persist=self._settler(review, self._reduction_state, settled, on_state))
        return LifecycleExecution(settled[0] if settled else self._reduction_state(review, order),
                                  None, order)

    def _reduction_state(self, state: StrategyState, order: SimOrder) -> StrategyState:
        """A filled reduction closes the review; an unfilled one leaves it pending.

        The review phase is the whole retry contract: it says the carry was already
        decided and only its size is still settling, so a later bar re-sizes the
        same reduction instead of reviewing the position a second time.
        """
        fills = self.broker.get_fills(order.id)
        if not fills:
            return state
        return state.transition(StrategyPhase.OVERNIGHT_HELD, overnight=True,
                                last_market_as_of=fills[-1].filled_at)

    def activate_day2(self, state: StrategyState, *, as_of: datetime) -> StrategyState:
        if state.entry_trading_date is None:
            raise ValueError("entry trading date is required")
        current_day = as_of.astimezone(self.calendar.timezone).date()
        holding_day = self.calendar.holding_day_number(state.entry_trading_date, current_day)
        if holding_day != 2:
            raise ValueError("V1 permits activation only on trading Day 2")
        return state.transition(StrategyPhase.DAY2_ACTIVE, holding_day_number=2,
                                last_market_as_of=as_of)
