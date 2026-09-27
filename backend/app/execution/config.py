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


@dataclass(frozen=True)
class RoundTripCostConfig(ExecutionConfig):
    """A strategy's frozen round-trip cost contract, applied the way the contract states it.

    Some research contracts (Strategy E's ``TOTAL_ROUND_TRIP_SUBTRACTION_V1``) price friction as one
    total, notional-proportional deduction per complete trade, with no split between the legs and
    no price adjustment. This config reproduces that and nothing else: both legs fill at the raw
    price (spread and slippage 0), and the whole round-trip total is charged once, as a cash charge
    on the entry fill, so ``net = gross - round_trip_cost_bps x entry notional``.

    It is a subclass rather than new fields on ``ExecutionConfig`` so that A's ``execution_v0``
    instances, and every config fingerprint built from them, are unchanged.
    """

    round_trip_cost_bps: Decimal = Decimal("0")
    cost_contract: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        for name in ("default_spread_bps", "default_slippage_bps", "commission_bps", "fx_cost_bps"):
            if getattr(self, name) != 0:
                raise ValueError(f"{name} must be 0 under a round-trip cost contract; the contract is the only cost")
        if self.round_trip_cost_bps < 0:
            raise ValueError("round_trip_cost_bps cannot be negative")
        if not self.cost_contract:
            raise ValueError("a round-trip cost config names the contract it implements")
