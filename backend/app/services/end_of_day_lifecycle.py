"""The production caller that reviews an open position before the close.

Everything this needs already existed. ``StrategyV0Engine.closing_review`` decides
whether a position is fit to be carried, ``RiskEngine.evaluate_overnight_notional``
decides how much of it may be, ``StrategyLifecycleRunner`` submits whatever sell
that produces, and ``ExecutionService`` owns the transaction. What was missing was
something to call them once a day, so this is a driver rather than a second copy of
any of that logic - no eligibility rule is restated and no stress limit is
recomputed here.

Two engines, one order
----------------------
The strategy gate takes ``stress_within_limit`` as an input, so asking it about the
whole position would have answered a sizing question that belongs to risk: a
position over the overnight stress cap would be rejected outright and the risk
engine's own REDUCE_AND_HOLD could never be reached. The gate is therefore asked
about the largest stress-compliant size that exists - ``max_notional_by_stress``
capped by what is actually held - and the risk engine is then left to hold, trim,
or refuse it. Neither engine's rules change; only the order of the two questions
makes the reduction reachable.

Exactly once, without a new column
----------------------------------
The phase machine already says what has happened to a position today. A reviewed
position is OVERNIGHT_HELD (carried), EXIT_SIGNALLED (leaving), or EXITED, and none
of those is reviewable, so a second tick in the same closing window cannot review or
reduce a position twice. OVERNIGHT_REVIEW is the one in-between state: it says the
carry was already decided and only its size is still settling, so a later bar
re-sizes that same reduction instead of deciding the day again. Nothing here reads a
clock to work out whether it has run.

Single process by construction, like the minute driver it sits beside: the runtime
holder is process-local, and the runtime this binds to is the one the backend
activated at startup.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
import logging

from app.broker.domain import SimOrder, SimPosition
from app.market.calendar import MarketCalendar
from app.market.domain import MinuteBar
from app.repositories.strategy import StrategyStateRepository
from app.risk.domain import AccountSnapshot, Currency, PortfolioSnapshot, PositionSnapshot
from app.risk.engine import RiskEngine
from app.services.position_lifecycle import ACTUAL_VARIANT, strategy_state_sink
from app.services.simulation_runtime import SimulationRuntimeContext
from app.strategy.config import VariantConfig
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.engine import StrategyReason, StrategyV0Engine
from app.strategy.indicators import available_regular_bars, session_vwap
from app.strategy.lifecycle import StrategyPhase, StrategyState
from app.strategy.runner import StrategyLifecycleRunner

logger = logging.getLogger(__name__)

# Phases a closing review may still decide. Everything else has either already been
# reviewed today (OVERNIGHT_HELD), already committed to leaving (EXIT_SIGNALLED,
# owned by the minute driver's retry), or finished (EXITED).
REVIEWABLE_PHASES = frozenset({StrategyPhase.POSITION_OPEN, StrategyPhase.PYRAMID_ADDED,
                               StrategyPhase.DAY2_ACTIVE})


class EndOfDayAction(StrEnum):
    CARRIED = "CARRIED"
    REDUCED = "REDUCED"
    REDUCTION_UNFILLED = "REDUCTION_UNFILLED"
    EXIT_FILLED = "EXIT_FILLED"
    EXIT_UNFILLED = "EXIT_UNFILLED"
    DAY2_ACTIVATED = "DAY2_ACTIVATED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class EndOfDayOutcome:
    """What one closing review did, including the reason it did nothing."""

    symbol: str
    action: EndOfDayAction
    reason: str
    state: StrategyState | None = None
    order: SimOrder | None = None

    @property
    def carried(self) -> bool:
        return self.action in {EndOfDayAction.CARRIED, EndOfDayAction.REDUCED}


class EndOfDayLifecycleService:
    """Review, carry, trim, or close each open position once per trading day."""

    def __init__(self, runtime: SimulationRuntimeContext, *,
                 engine: StrategyV0Engine | None = None,
                 risk_engine: RiskEngine | None = None,
                 calendar: MarketCalendar | None = None,
                 variant: VariantConfig = ACTUAL_VARIANT) -> None:
        if not runtime.durable:
            raise ValueError("end-of-day management requires a durable runtime")
        self.runtime = runtime
        self.engine = engine or StrategyV0Engine()
        self.risk = risk_engine or RiskEngine()
        self.calendar = calendar or MarketCalendar()
        self.variant = variant
        self.runner = StrategyLifecycleRunner(runtime.broker, runtime=runtime,
                                              risk_engine=self.risk, calendar=self.calendar)

    @property
    def broker(self):  # type: ignore[no-untyped-def]
        return self.runtime.broker

    def review_at(self, day: date) -> datetime | None:
        """The configured closing review moment for an actual XNYS session.

        Derived from the exchange's own close, so an early close reviews early and a
        holiday or weekend has no review time at all. No wall-clock time is written
        down anywhere in this stage.
        """
        close = self.calendar.regular_market_close(day)
        return None if close is None else close - self.engine.config.closing_review_before_close

    # Day 2 --------------------------------------------------------------

    def activate_day2(self, *, as_of: datetime) -> tuple[EndOfDayOutcome, ...]:
        """Promote yesterday's carried positions to Day 2 on the next XNYS session.

        The calendar decides what Day 2 is, so a weekend or a holiday advances
        nothing; the runner's own primitive refuses any day but the second.
        """
        _require_aware(as_of)
        current_day = as_of.astimezone(self.calendar.timezone).date()
        outcomes: list[EndOfDayOutcome] = []
        for position in self.broker.get_positions():
            state = self._single_state(position.symbol)
            if state is None or state.phase is not StrategyPhase.OVERNIGHT_HELD:
                continue
            if state.entry_trading_date is None:
                outcomes.append(_skip(position.symbol, "carried state has no entry trading date"))
                continue
            if not self.calendar.is_trading_day(current_day):
                continue
            holding_day = self.calendar.holding_day_number(state.entry_trading_date, current_day)
            if holding_day <= state.holding_day_number:
                continue
            if holding_day != 2:
                # Day 3 is not a state this strategy has; inventing one here would
                # be a policy, not an activation.
                outcomes.append(_skip(position.symbol, f"holding day {holding_day} is beyond Day 2"))
                continue
            try:
                activated = self.runner.activate_day2(state, as_of=as_of)
                self._save(activated, updated_at=as_of)
            except Exception as error:
                logger.exception("DAY2 ACTIVATION FAILED: %s", position.symbol)
                outcomes.append(_skip(position.symbol, f"day 2 activation failed: {error}"))
                continue
            logger.info("DAY2 ACTIVATED: %s holding_day=%s", position.symbol,
                        activated.holding_day_number)
            outcomes.append(EndOfDayOutcome(position.symbol, EndOfDayAction.DAY2_ACTIVATED,
                                            "DAY2_ACTIVE", activated))
        return tuple(outcomes)

    # Closing review -----------------------------------------------------

    def review(self, bars_by_symbol: Mapping[str, Sequence[MinuteBar]], *, as_of: datetime,
               symbols: frozenset[str] | None = None) -> tuple[EndOfDayOutcome, ...]:
        """Review every open position the active broker holds.

        The broker is the authority on what is open, exactly as it is for the minute
        driver: reading holdings from the database instead would create a second
        control loop that could disagree with the broker submitting the orders.
        """
        _require_aware(as_of)
        marks = self._marks(bars_by_symbol, as_of=as_of)
        states: dict[str, StrategyState | None] = {
            position.symbol: self._single_state(position.symbol)
            for position in self.broker.get_positions()}
        outcomes: list[EndOfDayOutcome] = []
        for position in self.broker.get_positions():
            if symbols is not None and position.symbol not in symbols:
                continue
            try:
                outcome = self._review_one(
                    position, tuple(bars_by_symbol.get(position.symbol, ())),
                    as_of=as_of, marks=marks, states=states)
                if outcome.state is not None:
                    # The overnight slot limit counts positions, so the symbol
                    # reviewed next has to see what this one just decided. Reading
                    # the row back would cost a query per symbol to learn what the
                    # outcome already carries.
                    states[position.symbol] = outcome.state
                outcomes.append(outcome)
            except Exception as error:
                # One symbol's failure decides one symbol's night. The execution
                # transaction has already restored the broker and rolled the
                # database back, so the rest of the book is reviewed on truth.
                logger.exception("EOD REVIEW FAILED: %s", position.symbol)
                outcomes.append(_skip(position.symbol, f"review failed: {error}"))
        return tuple(outcomes)

    def _review_one(self, position: SimPosition, bars: tuple[MinuteBar, ...], *,
                    as_of: datetime, marks: Mapping[str, Decimal],
                    states: Mapping[str, StrategyState | None]) -> EndOfDayOutcome:
        symbol = position.symbol
        state = states.get(symbol)
        if state is None:
            # No reconstruction: a carry decision this process never recorded is one
            # it cannot honestly make on the position's behalf.
            return _skip(symbol, "no single open strategy state")
        if None in (state.entry_price, state.initial_stop, state.active_stop):
            return _skip(symbol, "strategy state has no entry or stop")
        market_open = self.calendar.regular_market_open(
            as_of.astimezone(self.calendar.timezone).date())
        if market_open is None:
            return _skip(symbol, "not a trading session")
        if state.phase is StrategyPhase.OVERNIGHT_REVIEW:
            # The carry was decided; only its size is still settling.
            return self._settle_reduction(state, position, bars, as_of=as_of, marks=marks,
                                          states=states)
        if state.phase not in REVIEWABLE_PHASES:
            return _skip(symbol, f"phase {state.phase.value} is not reviewed at the close")
        visible = available_regular_bars(bars, as_of)
        if not visible:
            return _skip(symbol, "no completed regular bar")
        current_price = Decimal(str(visible[-1].close))
        account = self._account(marks, as_of)
        if account.equity <= 0:
            return _skip(symbol, "account equity is not positive")
        portfolio = self._portfolio(marks, states, as_of=as_of)
        # The gate is asked about the largest stress-compliant size that exists, not
        # about the whole position: the stress cap is a sizing limit, and sizing is
        # the risk engine's to apply below.
        proposed = position.quantity * current_price
        stress = self.risk.overnight_stress(account.equity, proposed,
                                            self.risk.config.overnight_stress_gap_pct)
        carriable = min(proposed, stress.max_notional_by_stress)
        decision = self.engine.closing_review(
            state=state, current_price=current_price,
            vwap=session_vwap(bars, market_open, as_of), bars=bars, market_open=market_open,
            variant=self.variant, stress_within_limit=carriable > 0,
            overnight_position_available=self.risk.can_add_overnight_position(portfolio),
            # No intraday catalyst feed exists in this system; research runs before
            # the open, and inventing one here would be a new strategy input.
            has_new_negative_catalyst=False, as_of=as_of)
        # The review's own moment is the signal time a retry must keep, so it is
        # stamped before anything is submitted.
        signalled = replace(state, last_market_as_of=as_of)
        if decision.decision is DecisionType.EXIT:
            return self._exit(signalled, position, bars, current_price=current_price,
                              as_of=as_of, reason=decision.reason_code)
        return self._carry(signalled, position, bars, decision=decision, account=account,
                           portfolio=portfolio, current_price=current_price, as_of=as_of)

    def _settle_reduction(self, state: StrategyState, position: SimPosition,
                          bars: tuple[MinuteBar, ...], *, as_of: datetime,
                          marks: Mapping[str, Decimal],
                          states: Mapping[str, StrategyState | None]) -> EndOfDayOutcome:
        """Retry a reduction the broker had no bar to fill; never review again.

        The retry is still the reduction that was decided, so it keeps that
        decision's own time. Re-dating it to now would push the next-bar rule past
        every bar that has completed since, and the sell could never settle.
        """
        visible = available_regular_bars(bars, as_of)
        if not visible:
            return _skip(position.symbol, "no completed regular bar for a pending reduction")
        account = self._account(marks, as_of)
        decision = StrategyDecision(state.symbol, DecisionType.OVERNIGHT_HOLD,
                                    StrategyReason.OVERNIGHT_HOLD.value,
                                    state.last_market_as_of or as_of,
                                    strategy_version=state.strategy_version)
        return self._carry(state, position, bars, decision=decision, account=account,
                           portfolio=self._portfolio(marks, states, as_of=as_of),
                           current_price=Decimal(str(visible[-1].close)), as_of=as_of)

    def _carry(self, state: StrategyState, position: SimPosition, bars: tuple[MinuteBar, ...], *,
               decision: StrategyDecision, account: AccountSnapshot, portfolio: PortfolioSnapshot,
               current_price: Decimal, as_of: datetime) -> EndOfDayOutcome:
        """Hand an approved carry to risk, which holds it, trims it, or refuses it."""
        snapshot = self._snapshot(state, position, current_price)
        result = self.runner.apply_overnight_risk(
            state=state, decision=decision, account=account, portfolio=portfolio,
            position=snapshot, market_bars=bars, created_at=as_of,
            on_state=strategy_state_sink(as_of))
        if result.order is None:
            # Nothing was submitted, so nothing joined an execution transaction; the
            # carry is state alone and this owns its commit.
            self._save(result.state, updated_at=as_of)
            logger.info("OVERNIGHT CARRIED: %s qty=%s", state.symbol, position.quantity)
            return EndOfDayOutcome(state.symbol, EndOfDayAction.CARRIED,
                                   decision.reason_code, result.state)
        if not self.broker.get_fills(result.order.id):
            logger.warning("OVERNIGHT REDUCTION UNFILLED: %s %s", state.symbol,
                           result.order.rejection_reason)
            return EndOfDayOutcome(state.symbol, EndOfDayAction.REDUCTION_UNFILLED,
                                   str(result.order.rejection_reason), result.state, result.order)
        if result.state.phase in {StrategyPhase.EXIT_SIGNALLED, StrategyPhase.EXITED}:
            # Risk refused the carry outright; the strategy gate already answers this
            # question above, so reaching here at all is defence rather than a path.
            logger.info("OVERNIGHT EXIT FILLED: %s qty=%s", state.symbol,
                        result.order.filled_quantity)
            return EndOfDayOutcome(state.symbol, EndOfDayAction.EXIT_FILLED,
                                   decision.reason_code, result.state, result.order)
        logger.info("OVERNIGHT REDUCED: %s sold=%s", state.symbol, result.order.filled_quantity)
        return EndOfDayOutcome(state.symbol, EndOfDayAction.REDUCED, "OVERNIGHT_REDUCTION",
                               result.state, result.order)

    def _exit(self, state: StrategyState, position: SimPosition, bars: tuple[MinuteBar, ...], *,
              current_price: Decimal, as_of: datetime, reason: str) -> EndOfDayOutcome:
        """Sell the whole position through the durable execution transaction."""
        decision = StrategyDecision(state.symbol, DecisionType.EXIT, reason,
                                    state.last_market_as_of or as_of,
                                    strategy_version=state.strategy_version)
        result = self.runner.execute_exit(
            state=state, decision=decision, position=self._snapshot(state, position, current_price),
            market_bars=bars, created_at=as_of, on_state=strategy_state_sink(as_of))
        assert result.order is not None
        if not self.broker.get_fills(result.order.id):
            logger.warning("EOD EXIT UNFILLED: %s %s", state.symbol, result.order.rejection_reason)
            return EndOfDayOutcome(state.symbol, EndOfDayAction.EXIT_UNFILLED,
                                   str(result.order.rejection_reason), result.state, result.order)
        logger.info("EOD EXIT FILLED: %s %s qty=%s", state.symbol, reason,
                    result.order.filled_quantity)
        return EndOfDayOutcome(state.symbol, EndOfDayAction.EXIT_FILLED, reason,
                               result.state, result.order)

    # Snapshots ----------------------------------------------------------

    def _marks(self, bars_by_symbol: Mapping[str, Sequence[MinuteBar]], *,
               as_of: datetime) -> dict[str, Decimal]:
        """Last completed regular close per symbol; nothing is marked without one."""
        marks: dict[str, Decimal] = {}
        for symbol, bars in bars_by_symbol.items():
            visible = available_regular_bars(tuple(bars), as_of)
            if visible:
                marks[symbol] = Decimal(str(visible[-1].close))
        return marks

    def _account(self, marks: Mapping[str, Decimal], as_of: datetime) -> AccountSnapshot:
        """Equity from the broker's own valuation, marked on completed bars.

        A position this tick could not acquire a bar for is valued at the price the
        broker recorded paying for it. That is a stale mark rather than an invented
        one, it only ever contributes to the equity another symbol is measured
        against - a symbol with no completed bar of its own is never reviewed here -
        and the alternative is letting one failed acquisition leave the whole book
        unreviewed overnight.
        """
        valuations: dict[str, Decimal] = {}
        stale: list[str] = []
        for position in self.broker.get_positions():
            mark = marks.get(position.symbol)
            if mark is None:
                stale.append(position.symbol)
                mark = position.average_price
            valuations[position.symbol] = mark
        if stale:
            logger.warning("EOD EQUITY MARKED STALE: %s", ",".join(sorted(stale)))
        state = self.broker.account_snapshot(valuations, as_of)
        return AccountSnapshot(state.equity, state.cash, Currency(self.broker.currency), as_of)

    def _snapshot(self, state: StrategyState, position: SimPosition,
                  current_price: Decimal) -> PositionSnapshot:
        return PositionSnapshot(
            state.symbol, position.quantity, position.average_price, current_price,
            Currency(self.broker.currency), initial_stop=state.initial_stop,
            active_stop=state.active_stop, add_count=state.add_count,
            base_notional_account_ccy=position.cost_basis, overnight=state.overnight)

    def _portfolio(self, marks: Mapping[str, Decimal],
                   states: Mapping[str, StrategyState | None], *,
                   as_of: datetime) -> PortfolioSnapshot:
        """Every open position, each carrying the overnight flag its state records.

        The overnight slot limit counts positions, so a book reviewed one symbol at a
        time still has to see the ones already carried tonight.
        """
        snapshots = []
        for position in self.broker.get_positions():
            state = states.get(position.symbol)
            snapshots.append(PositionSnapshot(
                position.symbol, position.quantity, position.average_price,
                marks.get(position.symbol, position.average_price),
                Currency(self.broker.currency),
                initial_stop=None if state is None else state.initial_stop,
                active_stop=None if state is None else state.active_stop,
                add_count=0 if state is None else state.add_count,
                base_notional_account_ccy=position.cost_basis,
                overnight=False if state is None else state.overnight))
        exposure = sum((item.cost_basis for item in self.broker.get_positions()), Decimal("0"))
        return PortfolioSnapshot(tuple(snapshots), exposure, Decimal("0"), as_of)

    # Persistence --------------------------------------------------------

    def _single_state(self, symbol: str) -> StrategyState | None:
        with self.runtime.session_factory() as session:
            states = StrategyStateRepository(session).list_open(symbol)
        if len(states) != 1:
            # More than one open row is a contradiction the caller refuses rather
            # than silently resolves, exactly as the minute driver does.
            return None
        return states[0]

    def _save(self, state: StrategyState, *, updated_at: datetime) -> None:
        """Persist a state no execution accompanied, in its own transaction."""
        with self.runtime.session_factory() as session:
            try:
                StrategyStateRepository(session).save(state, updated_at=updated_at)
                session.commit()
            except Exception:
                session.rollback()
                raise


def _skip(symbol: str, reason: str) -> EndOfDayOutcome:
    logger.info("EOD SKIPPED: %s (%s)", symbol, reason)
    return EndOfDayOutcome(symbol, EndOfDayAction.SKIPPED, reason)


def _require_aware(as_of: datetime) -> None:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
