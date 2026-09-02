"""Broker-independent strategy decision domain."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.market.symbols import normalize_symbol


class DecisionType(StrEnum):
    NO_TRADE = "NO_TRADE"
    ENTER = "ENTER"
    HOLD = "HOLD"
    ADD = "ADD"
    EXIT = "EXIT"
    OVERNIGHT_HOLD = "OVERNIGHT_HOLD"


@dataclass(frozen=True)
class StrategyContext:
    symbol: str
    market_as_of: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        _require_aware(self.market_as_of, "market_as_of")


@dataclass(frozen=True)
class StrategyDecision:
    symbol: str
    decision: DecisionType
    reason_code: str
    market_as_of: datetime
    strategy_version: str = "strategy_foundation_v0"
    created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "decision", DecisionType(self.decision))
        _require_aware(self.market_as_of, "market_as_of")
        if self.created_at is not None:
            _require_aware(self.created_at, "created_at")
        if not self.reason_code.strip() or not self.strategy_version.strip():
            raise ValueError("reason_code and strategy_version must be non-blank")


@dataclass(frozen=True)
class TradingEligibility:
    human_approved: bool
    safe_mode: bool = False
    book: str = "ACTUAL"

    def __post_init__(self) -> None:
        normalized = self.book.strip().upper()
        if normalized not in {"ACTUAL", "SHADOW"}:
            raise ValueError("book must be ACTUAL or SHADOW")
        object.__setattr__(self, "book", normalized)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
