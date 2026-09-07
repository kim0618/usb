"""The production caller that carries an open position to a durable exit.

Everything this needs already existed: the engine decides, the risk engine sizes
the sell, the runner submits, and the execution service owns the transaction.
What was missing was something to call them once a bar completes, so this is a
driver rather than a second copy of any of that logic - no stop is re-tested and
no price is recomputed here.

Scope is deliberately one lifecycle: an open position hits its stop and is closed
in full. A pyramid signal is reported and left unexecuted, and the overnight and
closing-review paths stay where they are, because each needs its own contract.

Single process by construction. The runtime holder is process-local, so two
processes running this would each see the same open position and each sell it;
the runtime this binds to is the one the backend activated at startup.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
import logging

from sqlalchemy.orm import Session

from app.broker.domain import SimOrder, SimPosition
from app.market.calendar import MarketCalendar
from app.market.domain import MinuteBar
from app.repositories.strategy import StrategyStateRepository
from app.risk.domain import Currency, PositionSnapshot
from app.risk.engine import RiskEngine
from app.services.simulation_runtime import SimulationRuntimeContext
from app.strategy.config import VARIANT_CONFIGS, VariantConfig
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.engine import StrategyV0Engine, stop_reason
from app.strategy.indicators import available_regular_bars
from app.strategy.lifecycle import StrategyPhase, StrategyState
from app.strategy.runner import StrategyLifecycleRunner

logger = logging.getLogger(__name__)

# The actual book takes its trailing multiplier from the state's trailing profile,
# so a variant contributes only the trailing mode. C is the control variant and the
# only one whose overnight, holding-day, and ATR settings match StrategyConfig's own
# defaults, which makes it the derived choice rather than a preference. G3 owns the
# final actual-book trailing contract; nothing here depends on the multiplier.
ACTUAL_VARIANT = VARIANT_CONFIGS["C"]

# Phases this stage manages. An open position in any other phase belongs to a
# lifecycle that is not wired yet, and is reported rather than quietly handled.
MANAGED_PHASES = frozenset({StrategyPhase.POSITION_OPEN, StrategyPhase.PYRAMID_ADDED,
                            StrategyPhase.EXIT_SIGNALLED})


def strategy_state_sink(updated_at: datetime):  # type: ignore[no-untyped-def]
    """A sink that writes the state a fill produced on the execution's session.

    Shared by entry and exit so both commit their phase with the fill itself.
    """
    def persist(session: Session, state: StrategyState) -> None:
        StrategyStateRepository(session).save(state, updated_at=updated_at)

    return persist


class PositionAction(StrEnum):
    HOLD = "HOLD"
    EXIT_FILLED = "EXIT_FILLED"
    EXIT_UNFILLED = "EXIT_UNFILLED"
    ADD_DEFERRED = "ADD_DEFERRED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class PositionOutcome:
    """What one evaluation did, including the reason it did nothing."""

    symbol: str
    action: PositionAction
    reason: str
    state: StrategyState | None = None
    order: SimOrder | None = None

    @property
    def exited(self) -> bool:
        return self.action is PositionAction.EXIT_FILLED


class PositionLifecycleService:
    def __init__(self, runtime: SimulationRuntimeContext, *,
                 engine: StrategyV0Engine | None = None,
                 risk_engine: RiskEngine | None = None,
                 calendar: MarketCalendar | None = None,
                 variant: VariantConfig = ACTUAL_VARIANT) -> None:
        if not runtime.durable:
            raise ValueError("position management requires a durable runtime")
        self.runtime = runtime
        self.engine = engine or StrategyV0Engine()
        self.calendar = calendar or MarketCalendar()
        self.variant = variant
        self.runner = StrategyLifecycleRunner(runtime.broker, runtime=runtime,
                                              risk_engine=risk_engine, calendar=self.calendar)

    @property
    def broker(self):  # type: ignore[no-untyped-def]
        return self.runtime.broker

    def evaluate(self, bars_by_symbol: Mapping[str, Sequence[MinuteBar]], *,
                 as_of: datetime,
                 symbols: frozenset[str] | None = None) -> tuple[PositionOutcome, ...]:
        """Evaluate every open position the active broker holds.

        The broker is the authority on what is open: reading holdings from the
        database instead would create a second control loop that could disagree
        with the broker actually submitting the orders.
        """
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return tuple(
            self._evaluate_one(position, tuple(bars_by_symbol.get(position.symbol, ())),
                               as_of=as_of)
            for position in self.broker.get_positions()
            if symbols is None or position.symbol in symbols
        )

    def _evaluate_one(self, position: SimPosition, bars: tuple[MinuteBar, ...], *,
                      as_of: datetime) -> PositionOutcome:
        symbol = position.symbol
        with self.runtime.session_factory() as session:
            states = StrategyStateRepository(session).list_open(symbol)
        if not states:
            # No reconstruction: a stop this process never recorded is a stop it
            # cannot honestly enforce.
            return self._skip(symbol, "no open strategy state")
        if len(states) > 1:
            return self._skip(symbol, f"{len(states)} open strategy states")
        state = states[0]
        if state.phase not in MANAGED_PHASES:
            return self._skip(symbol, f"phase {state.phase.value} is not managed yet")
        if None in (state.entry_price, state.initial_stop, state.active_stop):
            # Both the engine and the stop reason divide on these; an incomplete
            # row is refused rather than defaulted into a stop nobody set.
            return self._skip(symbol, "strategy state has no entry or stop")
        market_open = self.calendar.regular_market_open(
            as_of.astimezone(self.calendar.timezone).date())
        if market_open is None:
            return self._skip(symbol, "not a trading session")

        if state.phase is StrategyPhase.EXIT_SIGNALLED:
            # Already committed to leaving. Re-running the engine here would both
            # re-decide a settled question and attempt an EXIT_SIGNALLED to
            # EXIT_SIGNALLED transition the state machine rejects.
            return self._exit(state, position, bars, as_of=as_of, reason=stop_reason(state).value)
        visible = available_regular_bars(bars, as_of)
        if not visible:
            return self._skip(symbol, "no completed regular bar")
        if (state.last_market_as_of is not None
                and state.last_market_as_of >= visible[-1].available_at):
            return self._skip(symbol, "already evaluated through this bar")

        evaluated = self.engine.evaluate_position(
            state=state, bars=bars, market_open=market_open, as_of=as_of,
            current_price=Decimal(str(visible[-1].close)), average_price=position.average_price,
            variant=self.variant)
        if evaluated.decision.decision is DecisionType.EXIT:
            return self._exit(evaluated.state, position, bars, as_of=as_of,
                              reason=evaluated.decision.reason_code)
        self._save(evaluated.state, updated_at=as_of)
        if evaluated.decision.decision is DecisionType.ADD:
            # The durable add path exists; deciding how much to add is a separate
            # contract, so the signal is surfaced instead of being dropped.
            return PositionOutcome(symbol, PositionAction.ADD_DEFERRED,
                                   evaluated.decision.reason_code, evaluated.state)
        return PositionOutcome(symbol, PositionAction.HOLD, evaluated.decision.reason_code,
                               evaluated.state)

    def _exit(self, state: StrategyState, position: SimPosition, bars: tuple[MinuteBar, ...], *,
              as_of: datetime, reason: str) -> PositionOutcome:
        """Sell the whole position through the durable execution transaction."""
        visible = available_regular_bars(bars, as_of)
        if not visible:
            self._save(state, updated_at=as_of)
            return PositionOutcome(state.symbol, PositionAction.EXIT_UNFILLED,
                                   "no completed regular bar", state)
        # A retry is still the exit that was signalled, so it keeps the signal's
        # own time. Re-dating it to now would push the next-bar rule past every
        # bar that has since completed, and the exit could never settle.
        market_as_of = state.last_market_as_of or as_of
        decision = StrategyDecision(state.symbol, DecisionType.EXIT, reason, market_as_of,
                                    strategy_version=state.strategy_version)
        snapshot = PositionSnapshot(
            state.symbol, position.quantity, position.average_price,
            Decimal(str(visible[-1].close)), Currency(self.broker.currency),
            initial_stop=state.initial_stop, add_count=state.add_count,
            base_notional_account_ccy=position.cost_basis, overnight=state.overnight)
        # Quantity is left to the exit intent, which sells the snapshot's whole
        # position; a partial exit is a different contract.
        result = self.runner.execute_exit(state=state, decision=decision, position=snapshot,
                                          market_bars=bars, created_at=as_of,
                                          on_state=strategy_state_sink(as_of))
        assert result.order is not None
        if not self.broker.get_fills(result.order.id):
            logger.warning("EXIT UNFILLED: %s %s", state.symbol, result.order.rejection_reason)
            return PositionOutcome(state.symbol, PositionAction.EXIT_UNFILLED,
                                   str(result.order.rejection_reason), result.state, result.order)
        logger.info("EXIT FILLED: %s %s qty=%s", state.symbol, reason, result.order.filled_quantity)
        return PositionOutcome(state.symbol, PositionAction.EXIT_FILLED, reason,
                               result.state, result.order)

    def _save(self, state: StrategyState, *, updated_at: datetime) -> None:
        """Persist a state no execution accompanied, in its own transaction."""
        with self.runtime.session_factory() as session:
            try:
                StrategyStateRepository(session).save(state, updated_at=updated_at)
                session.commit()
            except Exception:
                session.rollback()
                raise

    @staticmethod
    def _skip(symbol: str, reason: str) -> PositionOutcome:
        logger.info("POSITION SKIPPED: %s (%s)", symbol, reason)
        return PositionOutcome(symbol, PositionAction.SKIPPED, reason)
