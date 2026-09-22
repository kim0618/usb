"""Frozen, versioned Strategy V0 and shadow-variant policy."""

from dataclasses import dataclass
from datetime import time, timedelta
from decimal import Decimal
from enum import StrEnum


STRATEGY_VERSION = "strategy_v0"
SHADOW_VARIANT_VERSION = "shadow_variants_v0"


class TrailingMode(StrEnum):
    ATR = "ATR"
    STRUCTURE = "STRUCTURE"


@dataclass(frozen=True)
class StrategyConfig:
    version: str = STRATEGY_VERSION
    premarket_gap_min_pct: Decimal = Decimal("0.02")
    premarket_gap_max_pct: Decimal = Decimal("0.15")
    premarket_volume_ratio_min: Decimal = Decimal("0.05")
    opening_range_minutes: int = 15
    entry_deadline_et: time = time(10, 30)
    #: How far above the observed signal price the entry reference may be set, as a
    #: fraction (0.0005 is 0.05%), in the same units as the gap thresholds above. It is
    #: applied after the entry decision, so it never creates a signal the untoleranced
    #: rule would not create; it moves only the price Risk sizes at, and with it the
    #: ceiling that the unchanged execution cost model derives from that reference.
    #: A higher reference means a smaller quantity at a higher limit, never extra risk.
    #: At its default the strategy is byte-identical to the one that had no such field.
    entry_reference_tolerance_pct: Decimal = Decimal("0")
    require_price_above_vwap: bool = True
    require_opening_range_breakout: bool = True
    max_entry_attempts_per_symbol: int = 1
    atr_period: int = 14
    tight_atr_multiplier: Decimal = Decimal("1.0")
    normal_atr_multiplier: Decimal = Decimal("1.5")
    wide_atr_multiplier: Decimal = Decimal("2.0")
    trailing_activation_r: Decimal = Decimal("1")
    max_pyramid_adds: int = 1
    max_holding_trading_days: int = 2
    overnight_enabled: bool = True
    overnight_max_positions: int = 1
    closing_review_before_close: timedelta = timedelta(minutes=10)
    closing_strength_min: Decimal = Decimal("0.70")
    overnight_max_stop_distance_r: Decimal = Decimal("2")

    def __post_init__(self) -> None:
        if not (Decimal("0") < self.premarket_gap_min_pct < self.premarket_gap_max_pct):
            raise ValueError("invalid premarket gap thresholds")
        if self.premarket_volume_ratio_min < 0 or self.opening_range_minutes <= 0:
            raise ValueError("invalid volume/opening range configuration")
        if self.entry_reference_tolerance_pct < 0:
            raise ValueError("entry reference tolerance cannot be negative")
        if min(self.atr_period, self.max_entry_attempts_per_symbol,
               self.max_holding_trading_days, self.overnight_max_positions) < 1:
            raise ValueError("strategy count limits must be positive")
        # 0 disables pyramiding: the add condition in evaluate_position needs
        # add_count < max_pyramid_adds, which no position satisfies at 0, so no ADD
        # decision (and no add intent) is ever produced. 1 is the V1 behaviour.
        if self.max_pyramid_adds < 0:
            raise ValueError("max_pyramid_adds cannot be negative")


@dataclass(frozen=True)
class VariantConfig:
    variant: str
    allow_overnight: bool
    max_holding_days: int
    trailing_mode: TrailingMode
    trailing_atr_multiplier: Decimal | None
    is_control: bool = False
    version: str = SHADOW_VARIANT_VERSION


VARIANT_CONFIGS = {
    "A": VariantConfig("A", False, 1, TrailingMode.ATR, Decimal("1.5")),
    "B": VariantConfig("B", True, 2, TrailingMode.ATR, Decimal("1.0")),
    "C": VariantConfig("C", True, 2, TrailingMode.ATR, Decimal("1.5"), True),
    "D": VariantConfig("D", True, 2, TrailingMode.ATR, Decimal("2.0")),
    # E keeps the non-decreasing prior/initial structure stop and does not use ATR trailing.
    "E": VariantConfig("E", False, 1, TrailingMode.STRUCTURE, None),
}
