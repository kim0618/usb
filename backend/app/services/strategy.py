"""Transaction owner for strategy state; the engine remains persistence-free."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.repositories.strategy import StrategyStateRepository
from app.strategy.lifecycle import StrategyBook, StrategyState
from app.strategy.lifecycle import StrategyPhase
from app.strategy.engine import GateResult


class StrategyStateService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = StrategyStateRepository(session)

    def load(self, symbol: str, trading_date: date, *, book: StrategyBook = StrategyBook.ACTUAL,
             variant: str = "ACTUAL") -> StrategyState | None:
        return self.repository.load(symbol, trading_date, book=book, variant=variant)

    def save(self, state: StrategyState, *, updated_at: datetime) -> StrategyState:
        try:
            self.repository.save(state, updated_at=updated_at)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return state


class StrategyLifecycleService:
    """Pure orchestration helpers for gate and fill acknowledgements."""

    @staticmethod
    def apply_human_gate(state: StrategyState, *, approved: bool, shadow_mode: bool) -> StrategyState:
        if shadow_mode:
            return state
        return state.transition(StrategyPhase.HUMAN_APPROVED if approved else StrategyPhase.HUMAN_REJECTED)

    @staticmethod
    def apply_premarket_gate(state: StrategyState, gate: GateResult) -> StrategyState:
        phase = StrategyPhase.PREMARKET_PASSED if gate.passed else StrategyPhase.PREMARKET_REJECTED
        # A rejection is terminal, so the reason it carries is the only record of why
        # this symbol never reached an entry decision.
        passed = state.transition(phase, phase_reason=None if gate.passed else gate.reason.value)
        return passed.transition(StrategyPhase.OPENING_RANGE_BUILDING) if gate.passed else passed

    @staticmethod
    def mark_entry_filled(state: StrategyState, *, fill_price: Decimal,
                          market_as_of: datetime) -> StrategyState:
        if state.phase is not StrategyPhase.ENTRY_SIGNALLED:
            raise ValueError("entry fill requires ENTRY_SIGNALLED")
        return state.transition(StrategyPhase.POSITION_OPEN, entry_price=Decimal(fill_price),
                                highest_price_since_entry=Decimal(fill_price),
                                last_market_as_of=market_as_of)

    @staticmethod
    def mark_add_filled(state: StrategyState, *, market_as_of: datetime) -> StrategyState:
        if state.phase not in {StrategyPhase.POSITION_OPEN, StrategyPhase.PYRAMID_ADDED}:
            raise ValueError("add fill requires an open position")
        if state.add_count >= 1:
            raise ValueError("V1 permits one pyramid add")
        if state.phase is StrategyPhase.POSITION_OPEN:
            return state.transition(StrategyPhase.PYRAMID_ADDED, add_count=1,
                                    add_signal_issued=True, last_market_as_of=market_as_of)
        return replace(state, add_count=1, add_signal_issued=True, last_market_as_of=market_as_of)

    @staticmethod
    def mark_exit_filled(state: StrategyState, *, market_as_of: datetime,
                         remaining_quantity: Decimal) -> StrategyState:
        """Reconcile an execution fill with broker quantity truth."""
        if state.phase is not StrategyPhase.EXIT_SIGNALLED:
            raise ValueError("exit fill requires EXIT_SIGNALLED")
        if remaining_quantity < 0:
            raise ValueError("remaining quantity cannot be negative")
        if remaining_quantity > 0:
            return replace(state, last_market_as_of=market_as_of)
        return state.transition(StrategyPhase.EXITED, last_market_as_of=market_as_of)
