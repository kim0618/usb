"""Execution costs and the fill model for a Strategy B backtest.

Two things live here that the research layer deliberately does not know about: what a fill
costs, and which price a signal turns into. B-F0 8.3 fixes the *shape* of the fill model;
the numbers behind the costs are declared in B-E0, which is why nothing in this module has a
default. A caller that has not decided its fee and slippage cannot accidentally run at zero.
"""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from app.strategy_b.models import MomentumBar

BPS = 10_000.0


class FillScenario(StrEnum):
    """Which price a crossed trigger becomes."""

    SIGNAL_BAR = "SIGNAL_BAR"
    """B-F0 8.3: the trigger itself, or the bar's open when the bar gapped past it."""
    NEXT_BAR_OPEN = "NEXT_BAR_OPEN"
    """The conservative sensitivity run: the next actual bar's open, whatever it is."""


@dataclass(frozen=True, slots=True)
class CostModel:
    """Slippage moves the price against the trade; the fee is cash on both sides.

    Both are per side and in basis points. There is no default: B-E0 declares them, and a run
    records the values it used in its identity, so two runs with different costs can never be
    compared by accident.
    """

    fee_bps_per_side: float
    slippage_bps_per_side: float

    def __post_init__(self) -> None:
        for name in ("fee_bps_per_side", "slippage_bps_per_side"):
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")

    def buy_price(self, raw: float) -> float:
        return raw * (1 + self.slippage_bps_per_side / BPS)

    def sell_price(self, raw: float) -> float:
        return raw * (1 - self.slippage_bps_per_side / BPS)

    def fee(self, price: float, shares: int) -> float:
        return price * shares * self.fee_bps_per_side / BPS


def signal_fill(trigger_price: float, bar: MomentumBar, scenario: FillScenario,
                next_bar: MomentumBar | None = None) -> float | None:
    """The raw fill price for a trigger crossed on ``bar``, before costs.

    ``None`` means no fill: the bar never reached the trigger, or the conservative scenario
    has no next bar to open at (a silent tape to the end of the session does not fill).
    """
    if scenario is FillScenario.NEXT_BAR_OPEN:
        return None if next_bar is None else next_bar.open
    if bar.high < trigger_price:
        return None
    return bar.open if bar.open > trigger_price else trigger_price
