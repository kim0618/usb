"""Versioned V1 risk policy."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RiskConfig:
    version: str = "risk_v1"
    risk_per_trade_pct: Decimal = Decimal("0.005")
    # Daily planned initial risk, counted in the entry session's fixed 1R (see RiskEngine).
    max_daily_risk_units: Decimal = Decimal("3")
    base_capacity_pct: Decimal = Decimal("0.80")
    pyramid_reserve_pct: Decimal = Decimal("0.20")
    max_symbol_exposure_pct: Decimal = Decimal("0.60")
    # New symbols entered per entry session; an exit does not free a slot.
    max_new_symbols_per_day: int = 3
    # Concurrently held symbols, pending base entries included.
    max_open_positions: int = 3
    max_pyramid_adds: int = 1
    overnight_stress_gap_pct: Decimal = Decimal("0.20")
    max_overnight_stress_loss_pct: Decimal = Decimal("0.05")
    max_overnight_positions: int = 1

    def __post_init__(self) -> None:
        percentages = (
            self.risk_per_trade_pct, self.base_capacity_pct, self.pyramid_reserve_pct,
            self.max_symbol_exposure_pct, self.overnight_stress_gap_pct,
            self.max_overnight_stress_loss_pct,
        )
        if any(value <= 0 or value > 1 for value in percentages):
            raise ValueError("risk percentages must be in (0, 1]")
        if self.base_capacity_pct + self.pyramid_reserve_pct != Decimal("1"):
            raise ValueError("base capacity and pyramid reserve must total 1")
        if self.max_daily_risk_units <= 0:
            raise ValueError("max_daily_risk_units must be positive")
        if min(self.max_new_symbols_per_day, self.max_open_positions,
               self.max_overnight_positions) < 1:
            raise ValueError("risk count limits must be positive")
        # 0 means no pyramid add is ever approved (evaluate_pyramid_add rejects with
        # PYRAMID_LIMIT). The 80/20 base/reserve split is unchanged by it: an unused
        # reserve is not handed to base entries.
        if self.max_pyramid_adds < 0:
            raise ValueError("max_pyramid_adds cannot be negative")
