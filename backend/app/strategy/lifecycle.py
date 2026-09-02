"""Strategy V0 lifecycle domain and validated state transitions."""

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from app.market.symbols import normalize_symbol
from app.strategy.config import STRATEGY_VERSION


class StrategyPhase(StrEnum):
    RESEARCH_READY = "RESEARCH_READY"
    HUMAN_REJECTED = "HUMAN_REJECTED"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    PREMARKET_REJECTED = "PREMARKET_REJECTED"
    PREMARKET_PASSED = "PREMARKET_PASSED"
    OPENING_RANGE_BUILDING = "OPENING_RANGE_BUILDING"
    WAITING_ENTRY = "WAITING_ENTRY"
    ENTRY_SIGNALLED = "ENTRY_SIGNALLED"
    POSITION_OPEN = "POSITION_OPEN"
    PYRAMID_ADDED = "PYRAMID_ADDED"
    OVERNIGHT_REVIEW = "OVERNIGHT_REVIEW"
    OVERNIGHT_HELD = "OVERNIGHT_HELD"
    DAY2_ACTIVE = "DAY2_ACTIVE"
    EXIT_SIGNALLED = "EXIT_SIGNALLED"
    EXITED = "EXITED"
    NO_TRADE = "NO_TRADE"


TERMINAL_PHASES = {StrategyPhase.HUMAN_REJECTED, StrategyPhase.PREMARKET_REJECTED,
                   StrategyPhase.EXITED, StrategyPhase.NO_TRADE}

_TRANSITIONS: dict[StrategyPhase, set[StrategyPhase]] = {
    StrategyPhase.RESEARCH_READY: {StrategyPhase.HUMAN_APPROVED, StrategyPhase.HUMAN_REJECTED,
                                   StrategyPhase.PREMARKET_PASSED, StrategyPhase.PREMARKET_REJECTED},
    StrategyPhase.HUMAN_APPROVED: {StrategyPhase.PREMARKET_PASSED, StrategyPhase.PREMARKET_REJECTED},
    StrategyPhase.PREMARKET_PASSED: {StrategyPhase.OPENING_RANGE_BUILDING},
    StrategyPhase.OPENING_RANGE_BUILDING: {StrategyPhase.WAITING_ENTRY, StrategyPhase.NO_TRADE},
    StrategyPhase.WAITING_ENTRY: {StrategyPhase.ENTRY_SIGNALLED, StrategyPhase.NO_TRADE},
    StrategyPhase.ENTRY_SIGNALLED: {StrategyPhase.POSITION_OPEN, StrategyPhase.NO_TRADE},
    StrategyPhase.POSITION_OPEN: {StrategyPhase.PYRAMID_ADDED, StrategyPhase.OVERNIGHT_REVIEW,
                                  StrategyPhase.EXIT_SIGNALLED},
    StrategyPhase.PYRAMID_ADDED: {StrategyPhase.OVERNIGHT_REVIEW, StrategyPhase.EXIT_SIGNALLED},
    StrategyPhase.OVERNIGHT_REVIEW: {StrategyPhase.OVERNIGHT_HELD, StrategyPhase.EXIT_SIGNALLED},
    StrategyPhase.OVERNIGHT_HELD: {StrategyPhase.DAY2_ACTIVE, StrategyPhase.EXIT_SIGNALLED},
    StrategyPhase.DAY2_ACTIVE: {StrategyPhase.EXIT_SIGNALLED},
    StrategyPhase.EXIT_SIGNALLED: {StrategyPhase.EXITED},
}


class StrategyBook(StrEnum):
    ACTUAL = "ACTUAL"
    SHADOW = "SHADOW"


class TrailingProfile(StrEnum):
    TIGHT = "TIGHT"
    NORMAL = "NORMAL"
    WIDE = "WIDE"
    UNKNOWN = "UNKNOWN"


class OvernightSuitability(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StrategyState:
    symbol: str
    trading_date: date
    phase: StrategyPhase = StrategyPhase.RESEARCH_READY
    scanner_candidate_id: int | None = None
    book: StrategyBook = StrategyBook.ACTUAL
    variant: str = "ACTUAL"
    entry_trading_date: date | None = None
    entry_price: Decimal | None = None
    initial_stop: Decimal | None = None
    active_stop: Decimal | None = None
    highest_price_since_entry: Decimal | None = None
    add_count: int = 0
    add_signal_issued: bool = False
    holding_day_number: int = 0
    overnight: bool = False
    last_market_as_of: datetime | None = None
    strategy_version: str = STRATEGY_VERSION
    trailing_profile: TrailingProfile = TrailingProfile.NORMAL
    overnight_suitability: OvernightSuitability = OvernightSuitability.UNKNOWN

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "phase", StrategyPhase(self.phase))
        object.__setattr__(self, "book", StrategyBook(self.book))
        variant = self.variant.strip().upper()
        if self.book is StrategyBook.ACTUAL:
            variant = "ACTUAL"
        elif variant not in {"A", "B", "C", "D", "E"}:
            raise ValueError("shadow strategy state requires variant A-E")
        object.__setattr__(self, "variant", variant)
        object.__setattr__(self, "trailing_profile", TrailingProfile(self.trailing_profile))
        object.__setattr__(self, "overnight_suitability", OvernightSuitability(self.overnight_suitability))
        for name in ("entry_price", "initial_stop", "active_stop", "highest_price_since_entry"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Decimal(value))
        if self.last_market_as_of is not None and (
            self.last_market_as_of.tzinfo is None or self.last_market_as_of.utcoffset() is None
        ):
            raise ValueError("last_market_as_of must be timezone-aware")
        if self.add_count < 0 or self.holding_day_number < 0:
            raise ValueError("strategy counters cannot be negative")

    def transition(self, phase: StrategyPhase, **changes: object) -> "StrategyState":
        phase = StrategyPhase(phase)
        if phase not in _TRANSITIONS.get(self.phase, set()):
            raise ValueError(f"invalid strategy transition: {self.phase} -> {phase}")
        return replace(self, phase=phase, **changes)
