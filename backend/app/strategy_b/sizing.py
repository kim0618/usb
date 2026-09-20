"""Strategy B position sizing: B-F0 section 8.4, and nothing else.

Risk first, then the concentration cap. The stop distance is what a share can lose, so the
share count comes from the risk budget; the position-value cap then only ever lowers it.
Money in, money out - this module holds no account state and never decides whether the
portfolio may take the trade (that is the engine's `MAX_POSITIONS` / `DAILY_LOSS_LIMIT`).
"""

from dataclasses import dataclass
from enum import StrEnum
from math import floor, isfinite

from app.strategy_b.config import RiskConfig


class SizeRefusal(StrEnum):
    SIZE_ZERO = "SIZE_ZERO"
    """The risk budget or the concentration cap buys less than one share."""


@dataclass(frozen=True, slots=True)
class SizeDecision:
    shares: int
    risk_budget: float
    """Currency the trade may lose if the stop fills exactly: equity x risk_per_trade_pct."""
    risk_per_share: float
    position_value: float
    capped_by_position_limit: bool
    refusal: SizeRefusal | None

    def __post_init__(self) -> None:
        if (self.shares == 0) != (self.refusal is not None):
            raise ValueError("a refusal is exactly a zero-share decision")


def position_size(equity: float, entry_price: float, initial_stop: float,
                  config: RiskConfig) -> SizeDecision:
    """Shares to buy at ``entry_price`` with the stop at ``initial_stop``.

    ``equity`` and the prices must be in the same currency; B-F0 leaves the FX handling of a
    KRW account trading USD equities to the engine, which converts before calling this.
    """
    for name, value in (("equity", equity), ("entry_price", entry_price),
                        ("initial_stop", initial_stop)):
        if not isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    risk_per_share = entry_price - initial_stop
    if risk_per_share <= 0:
        raise ValueError("the stop must sit below the entry price")

    risk_budget = equity * config.risk_per_trade_pct / 100
    shares = floor(risk_budget / risk_per_share)
    cap = floor(equity * config.max_position_pct / 100 / entry_price)
    capped = cap < shares
    shares = min(shares, cap)
    return SizeDecision(
        shares=shares, risk_budget=risk_budget, risk_per_share=risk_per_share,
        position_value=shares * entry_price, capped_by_position_limit=capped,
        refusal=SizeRefusal.SIZE_ZERO if shares == 0 else None)
