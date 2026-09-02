"""Broker-source-of-truth comparison without silent self-healing."""

from collections.abc import Sequence
from datetime import datetime

from app.broker.domain import SimOrder, SimPosition
from app.monitoring.domain import MismatchType, ReconciliationMismatch, ReconciliationResult
from app.strategy.lifecycle import StrategyPhase, StrategyState, TERMINAL_PHASES


POSITION_PHASES = {StrategyPhase.POSITION_OPEN, StrategyPhase.PYRAMID_ADDED,
                   StrategyPhase.OVERNIGHT_REVIEW, StrategyPhase.OVERNIGHT_HELD,
                   StrategyPhase.DAY2_ACTIVE, StrategyPhase.EXIT_SIGNALLED}


class ReconciliationService:
    def compare(self, *, strategy_states: Sequence[StrategyState],
                broker_positions: Sequence[SimPosition], open_orders: Sequence[SimOrder],
                checked_at: datetime) -> ReconciliationResult:
        internal = {s.symbol: s for s in strategy_states if s.book.value == "ACTUAL"}
        broker = {p.symbol: p for p in broker_positions if p.quantity > 0}
        mismatches: list[ReconciliationMismatch] = []
        for symbol, state in sorted(internal.items()):
            if state.phase in TERMINAL_PHASES and symbol in broker:
                mismatches.append(ReconciliationMismatch(MismatchType.TERMINAL_STATE_WITH_OPEN_POSITION,
                                                           symbol, f"{state.phase.value} has broker position"))
            elif state.phase in POSITION_PHASES and symbol not in broker:
                mismatches.append(ReconciliationMismatch(MismatchType.NONTERMINAL_STATE_WITHOUT_POSITION,
                                                           symbol, f"{state.phase.value} lacks broker position"))
        for symbol in sorted(set(broker) - set(internal)):
            mismatches.append(ReconciliationMismatch(MismatchType.BROKER_POSITION_WITHOUT_INTERNAL,
                                                       symbol, "broker position has no strategy state"))
        for order in sorted(open_orders, key=lambda item: item.id):
            state = internal.get(order.symbol)
            if state is None or state.phase in TERMINAL_PHASES:
                mismatches.append(ReconciliationMismatch(MismatchType.OPEN_ORDER_STATE_MISMATCH,
                                                           order.symbol, f"open order {order.id} has no active state"))
        return ReconciliationResult(not mismatches, tuple(mismatches), checked_at)
