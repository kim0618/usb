"""Signed position and the single account authority.

Margin mode is ISOLATED: only the margin assigned to the position backs it, so a position can
liquidate while the wallet still holds free balance. Cross margin is out of D3 scope and the
difference is recorded in the run config rather than left to the reader to guess.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .instrument import RiskTierTable

SIDE_LONG = "LONG"
SIDE_SHORT = "SHORT"


@dataclass
class Position:
    """Signed quantity: LONG > 0, SHORT < 0, FLAT == 0 (contract S2)."""
    signed_qty: Decimal = Decimal(0)
    avg_entry: Decimal = Decimal(0)
    leverage: Decimal = Decimal(1)
    opened_ts_ms: int | None = None
    # Excursions in USDT, tracked while the position is open so the ledger can carry them at
    # close. Reading them back off the tape afterwards would work too, but then two places
    # would compute the same number and could disagree.
    max_adverse_excursion: Decimal = Decimal(0)
    max_favourable_excursion: Decimal = Decimal(0)
    mae_price: Decimal | None = None
    mfe_price: Decimal | None = None

    @property
    def is_flat(self) -> bool:
        return self.signed_qty == 0

    @property
    def side(self) -> str | None:
        if self.signed_qty > 0:
            return SIDE_LONG
        if self.signed_qty < 0:
            return SIDE_SHORT
        return None

    @property
    def sign(self) -> int:
        return 1 if self.signed_qty > 0 else (-1 if self.signed_qty < 0 else 0)

    @property
    def abs_qty(self) -> Decimal:
        return abs(self.signed_qty)

    @property
    def entry_notional(self) -> Decimal:
        return self.abs_qty * self.avg_entry

    @property
    def initial_margin(self) -> Decimal:
        if self.is_flat:
            return Decimal(0)
        return self.entry_notional / self.leverage

    def notional_at(self, price: Decimal) -> Decimal:
        return self.abs_qty * price

    def unrealized_pnl(self, mark_price: Decimal) -> Decimal:
        """Contract S3/P2: mark based, sign aware, never last price."""
        if self.is_flat:
            return Decimal(0)
        return (mark_price - self.avg_entry) * self.signed_qty

    def observe_excursion(self, mark_price: Decimal) -> None:
        """Record how far the open position ran against and for us, in mark terms."""
        if self.is_flat:
            return
        unrealized = self.unrealized_pnl(mark_price)
        if unrealized < self.max_adverse_excursion:
            self.max_adverse_excursion = unrealized
            self.mae_price = mark_price
        if unrealized > self.max_favourable_excursion:
            self.max_favourable_excursion = unrealized
            self.mfe_price = mark_price

    def reset_excursions(self) -> None:
        self.max_adverse_excursion = Decimal(0)
        self.max_favourable_excursion = Decimal(0)
        self.mae_price = None
        self.mfe_price = None


@dataclass
class Account:
    """`wallet_balance` is cash; `equity` adds the mark valuation of the open position.

    Invariants, checked by `assert_invariants` at every state change:
      A6  equity  = capital_base + (realized_pnl - realized_at_anchor)
                                 - (cash_charges - charges_at_anchor) + unrealized_pnl
      P5  equity  = available_balance + used_margin + unrealized_pnl

    The anchor is what makes a balance reset possible without losing history. `realized_pnl`,
    `cumulative_fees` and `cumulative_funding_paid` are never rewound: they stay the run's
    lifetime totals, so every analytic that folds the ledger keeps seeing all of it. A reset
    moves the anchor instead, which is the same statement as "spendable balance is measured
    from here on". With no reset the anchor sits at (starting_capital, 0, 0) and the arithmetic
    is exactly what it was before resets existed, byte for byte on replay.
    """
    starting_capital_usdt: Decimal
    realized_pnl: Decimal = Decimal(0)
    cumulative_fees: Decimal = Decimal(0)
    cumulative_funding_paid: Decimal = Decimal(0)
    position: Position = None  # type: ignore[assignment]
    # Capital anchor. `capital_base_usdt` defaults to the starting capital.
    capital_base_usdt: Decimal | None = None
    realized_at_anchor: Decimal = Decimal(0)
    charges_at_anchor: Decimal = Decimal(0)
    reset_count: int = 0
    last_reset_ts_ms: int | None = None

    def __post_init__(self) -> None:
        if self.position is None:
            self.position = Position()
        if self.capital_base_usdt is None:
            self.capital_base_usdt = self.starting_capital_usdt

    @property
    def cash_charges(self) -> Decimal:
        return self.cumulative_fees + self.cumulative_funding_paid

    @property
    def wallet_balance(self) -> Decimal:
        return (self.capital_base_usdt
                + (self.realized_pnl - self.realized_at_anchor)
                - (self.cash_charges - self.charges_at_anchor))

    def apply_capital_reset(self, target_usdt: Decimal, ts_ms: int) -> None:
        """Move the anchor to `target_usdt`. Nothing cumulative is touched."""
        self.capital_base_usdt = target_usdt
        self.realized_at_anchor = self.realized_pnl
        self.charges_at_anchor = self.cash_charges
        self.reset_count += 1
        self.last_reset_ts_ms = ts_ms

    @property
    def used_margin(self) -> Decimal:
        return self.position.initial_margin

    @property
    def available_balance(self) -> Decimal:
        return self.wallet_balance - self.used_margin

    def unrealized_pnl(self, mark_price: Decimal) -> Decimal:
        return self.position.unrealized_pnl(mark_price)

    def equity(self, mark_price: Decimal) -> Decimal:
        return self.wallet_balance + self.unrealized_pnl(mark_price)

    def maintenance_margin(self, tiers: RiskTierTable, mark_price: Decimal) -> Decimal:
        if self.position.is_flat:
            return Decimal(0)
        return tiers.maintenance_margin(self.position.notional_at(mark_price))

    def margin_ratio(self, tiers: RiskTierTable, mark_price: Decimal) -> Decimal | None:
        """Position equity over maintenance requirement. Below 1 means liquidate."""
        if self.position.is_flat:
            return None
        requirement = self.maintenance_margin(tiers, mark_price)
        if requirement <= 0:
            return None
        return (self.used_margin + self.unrealized_pnl(mark_price)) / requirement

    def is_liquidatable(self, tiers: RiskTierTable, mark_price: Decimal) -> bool:
        """Contract Q2: mark based, never last price."""
        if self.position.is_flat:
            return False
        return self.used_margin + self.unrealized_pnl(mark_price) <= self.maintenance_margin(tiers, mark_price)

    def liquidation_price(self, tiers: RiskTierTable, mark_price: Decimal) -> Decimal | None:
        """Mark price at which position equity equals the maintenance requirement.

        Solving `IM + (M - E) * Q = |Q| * M * mmRate - deduction` for M gives

            M = (E * Q - IM - deduction) / (Q * (1 - sign(Q) * mmRate))

        The tier is selected on the notional at the *current* mark. Bybit's own tier-selection
        basis for this calculation is not published on any public endpoint, so this choice is
        an explicit assumption rather than a measured fact.
        """
        position = self.position
        if position.is_flat:
            return None
        tier = tiers.tier_for_notional(position.notional_at(mark_price))
        denominator = position.signed_qty * (Decimal(1) - Decimal(position.sign) * tier.maintenance_margin_rate)
        if denominator == 0:
            return None
        price = (position.avg_entry * position.signed_qty - position.initial_margin - tier.mm_deduction) / denominator
        return price if price > 0 else Decimal(0)

    def assert_invariants(self, mark_price: Decimal, *, tolerance: Decimal = Decimal("0.000001")) -> None:
        unrealized = self.unrealized_pnl(mark_price)
        equity = self.equity(mark_price)
        a6 = (self.capital_base_usdt + (self.realized_pnl - self.realized_at_anchor)
              - (self.cash_charges - self.charges_at_anchor) + unrealized)
        p5 = self.available_balance + self.used_margin + unrealized
        if abs(equity - a6) > tolerance:
            raise AssertionError(f"A6 invariant broken: equity {equity} vs {a6}")
        if abs(equity - p5) > tolerance:
            raise AssertionError(f"P5 invariant broken: equity {equity} vs {p5}")

    def view(self, tiers: RiskTierTable, mark_price: Decimal) -> dict[str, Any]:
        position = self.position
        liq = self.liquidation_price(tiers, mark_price)
        ratio = self.margin_ratio(tiers, mark_price)
        return {
            "starting_capital_usdt": self.starting_capital_usdt,
            "capital_base_usdt": self.capital_base_usdt,
            "reset_count": self.reset_count,
            "last_reset_ts_ms": self.last_reset_ts_ms,
            "wallet_balance": self.wallet_balance,
            "available_balance": self.available_balance,
            "used_margin": self.used_margin,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl(mark_price),
            "equity": self.equity(mark_price),
            "cumulative_fees": self.cumulative_fees,
            "cumulative_funding_paid": self.cumulative_funding_paid,
            "position_side": position.side,
            "position_qty": position.abs_qty,
            "position_signed_qty": position.signed_qty,
            "avg_entry": position.avg_entry if not position.is_flat else None,
            "leverage": position.leverage if not position.is_flat else None,
            "entry_notional": position.entry_notional,
            "mark_notional": position.notional_at(mark_price),
            "maintenance_margin": self.maintenance_margin(tiers, mark_price),
            "margin_ratio": ratio,
            "liquidation_price": liq,
            "risk_tier": (tiers.tier_for_notional(position.notional_at(mark_price)).risk_id
                          if not position.is_flat else None),
        }
