"""Read-only projection of the daily new-entry and open-position caps.

RiskEngine stays the authority that rejects an order. This projection lets the
entry runtime stop before any market-data or strategy work once a cap is full,
and lets the API show why an approved symbol is not being evaluated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.orm import Session

from app.broker.sim import SimBroker
from app.execution.domain import IntentType, OrderSide
from app.repositories.risk import DailyRiskRepository
from app.risk.config import RiskConfig


class EntryCapacityReason(StrEnum):
    DAILY_ENTRY_CAP_REACHED = "DAILY_ENTRY_CAP_REACHED"
    OPEN_POSITION_CAP_REACHED = "OPEN_POSITION_CAP_REACHED"


@dataclass(frozen=True)
class EntryCapacity:
    entry_session_date: date
    entered_symbols: frozenset[str]
    open_symbols: frozenset[str]
    pending_entry_symbols: frozenset[str]
    max_new_entries: int
    max_open_positions: int

    @property
    def new_entries_used(self) -> int:
        return len(self.entered_symbols | self.pending_entry_symbols)

    @property
    def open_positions_used(self) -> int:
        return len(self.open_symbols | self.pending_entry_symbols)

    @property
    def blocked_reason(self) -> EntryCapacityReason | None:
        if self.new_entries_used >= self.max_new_entries:
            return EntryCapacityReason.DAILY_ENTRY_CAP_REACHED
        if self.open_positions_used >= self.max_open_positions:
            return EntryCapacityReason.OPEN_POSITION_CAP_REACHED
        return None

    def as_dict(self) -> dict[str, object]:
        reason = self.blocked_reason
        return {"entry_session_date": self.entry_session_date,
                "new_entries_used": self.new_entries_used, "max_new_entries": self.max_new_entries,
                "open_positions_used": self.open_positions_used,
                "max_open_positions": self.max_open_positions,
                "pending_entries": len(self.pending_entry_symbols),
                "blocked_reason": None if reason is None else reason.value}


def pending_entries(broker: SimBroker) -> dict[str, Decimal]:
    """Unfilled notional of base-entry BUY orders still open; pyramid adds are not new symbols."""
    pending: dict[str, Decimal] = {}
    for order in broker.get_open_orders():
        if order.side is OrderSide.BUY and order.intent_type == IntentType.BASE_ENTRY:
            pending[order.symbol] = pending.get(order.symbol, Decimal("0")) + (
                order.remaining_quantity * order.reference_price)
    return pending


def pending_entry_symbols(broker: SimBroker) -> frozenset[str]:
    return frozenset(pending_entries(broker))


def load_entry_capacity(session: Session, broker: SimBroker, entry_session_date: date,
                        config: RiskConfig | None = None) -> EntryCapacity:
    config = config or RiskConfig()
    daily = DailyRiskRepository(session).load(entry_session_date)
    return EntryCapacity(
        entry_session_date, daily.attempted_symbols,
        frozenset(position.symbol for position in broker.get_positions()),
        pending_entry_symbols(broker), config.max_new_symbols_per_day, config.max_open_positions,
    )
