"""Versioned, conservative simulation execution assumptions."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ExecutionConfig:
    version: str = "execution_v0"
    default_spread_bps: Decimal = Decimal("10")
    default_slippage_bps: Decimal = Decimal("5")
    commission_bps: Decimal = Decimal("10")
    fx_cost_bps: Decimal = Decimal("0")
    fill_delay_bars: int = 1
    partial_fill_enabled: bool = False
    partial_fill_ratio: Decimal = Decimal("0.5")
    ambiguous_bar_policy: str = "WORST_CASE"

    def __post_init__(self) -> None:
        for name in ("default_spread_bps", "default_slippage_bps", "commission_bps", "fx_cost_bps"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.fill_delay_bars < 1:
            raise ValueError("fill_delay_bars must be at least one")
        if not Decimal("0") < self.partial_fill_ratio <= Decimal("1"):
            raise ValueError("partial_fill_ratio must be in (0, 1]")
        if self.ambiguous_bar_policy != "WORST_CASE":
            raise ValueError("execution_v0 supports only WORST_CASE")
