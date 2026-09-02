"""Frozen Stage 6 shadow metadata; strategy behavior arrives in Stage 7."""

from dataclasses import dataclass
from enum import StrEnum


SHADOW_VARIANT_VERSION = "shadow_variants_v0"
MIN_ELIGIBLE_TRADES = 300
MIN_TRADING_DAYS = 60


class ShadowVariant(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"

    @property
    def is_control(self) -> bool:
        return self is ShadowVariant.C


class ShadowStatus(StrEnum):
    NO_TRADE = "NO_TRADE"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNFILLED = "UNFILLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ShadowResult:
    symbol: str
    variant: ShadowVariant
    variant_version: str = SHADOW_VARIANT_VERSION
    status: ShadowStatus = ShadowStatus.NO_TRADE
    premarket_passed: bool | None = None
    opening_passed: bool | None = None
    entry_signalled: bool | None = None
    entry_filled: bool | None = None
    no_trade_reason: str | None = None
    exit_reason: str | None = None
    gate_reached: str | None = None
    holding_days: int = 0


def evaluation_eligible(eligible_trades: int, trading_days: int) -> bool:
    return eligible_trades >= MIN_ELIGIBLE_TRADES and trading_days >= MIN_TRADING_DAYS
