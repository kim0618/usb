"""Versioned V1 risk policy."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RiskConfig:
    version: str = "risk_v0"
    risk_per_trade_pct: Decimal = Decimal("0.005")
    max_daily_risk_units: Decimal = Decimal("2")
    base_capacity_pct: Decimal = Decimal("0.80")
    pyramid_reserve_pct: Decimal = Decimal("0.20")
    max_symbol_exposure_pct: Decimal = Decimal("0.60")
    max_new_symbols_per_day: int = 2
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
        if min(self.max_new_symbols_per_day, self.max_pyramid_adds, self.max_overnight_positions) < 1:
            raise ValueError("risk count limits must be positive")
