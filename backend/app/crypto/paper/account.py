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


class SharedCash:
    """One wallet behind several single-position accounts.

    A multi-symbol terminal is several engines, each still owning exactly one `Position`. What
    they must not own severally is the money: a real Binance futures wallet is one purse, and
    margin posted on BTCUSDT is margin ETHUSDT cannot also spend. Without this, the paper screen
    is *looser* than the live one - it accepts sizes the exchange would refuse, which is the
    dangerous direction for a rehearsal.

    What is shared and what is not:

    * shared - the capital base, the cash (every member's realised PnL and charges), and the
      margin in use, so `available_balance` is the same figure on every symbol's screen;
    * per symbol - the position, its fills, its liquidation, and its own `realized_pnl`,
      `cumulative_fees` and `cumulative_funding_paid`, so each instrument's analytics still say
      what that instrument did.

    Attaching one changes the account's arithmetic. Not attaching one changes nothing at all:
    every method below falls back to the single-account behaviour exactly as it was, which is
    why the existing single-symbol run, its ledger and its tests are untouched.
    """

    def __init__(self, owner: str) -> None:
        #: Which member's capital base is the purse's. There is no separate seed to keep in
        #: sync: the owner is the symbol whose run already holds the money, so attaching a
        #: purse carries the existing balance across by construction and a later balance reset
        #: on that symbol moves the wallet instead of leaving the purse on a stale figure.
        self.owner = owner
        self._members: dict[str, "Account"] = {}

    @property
    def capital_base_usdt(self) -> Decimal:
        owner = self._members.get(self.owner)
        return owner.capital_base_usdt if owner is not None else Decimal(0)

    def join(self, symbol: str, account: "Account") -> None:
        """Attach an account. Its cash is the purse's from here on.

        A member that is not the owner contributes only its realised PnL and its charges: its
        own `capital_base_usdt` is deliberately ignored, so adding a second and third symbol to
        a running wallet does not conjure two more starting balances.
        """
        self._members[symbol] = account
        account.cash = self

    @property
    def members(self) -> dict[str, "Account"]:
        return dict(self._members)

    @property
    def realized_delta(self) -> Decimal:
        return sum((a.realized_pnl - a.realized_at_anchor for a in self._members.values()),
                   Decimal(0))

    def apply_capital_reset(self, target_usdt: Decimal, ts_ms: int) -> None:
        """Re-anchor the whole wallet, not one symbol's share of it.

        A reset means "spendable balance is measured from here on", and on a shared purse that
        sentence is about the purse. Re-anchoring only the symbol the button was pressed on
        would leave the other members' accumulated deltas still counted in the wallet, so the
        balance would not land on the target.
        """
        for symbol, account in self._members.items():
            account.apply_capital_reset(target_usdt if symbol == self.owner else Decimal(0),
                                        ts_ms)

    @property
    def charges_delta(self) -> Decimal:
        return sum((a.cash_charges - a.charges_at_anchor for a in self._members.values()),
                   Decimal(0))

    @property
    def wallet_balance(self) -> Decimal:
        """The one cash figure. Same shape as a single account's, summed over the members."""
        return self.capital_base_usdt + self.realized_delta - self.charges_delta

    @property
    def used_margin(self) -> Decimal:
        """Margin every member has posted. This is the whole point of the object."""
        return sum((a.position.initial_margin for a in self._members.values()), Decimal(0))

    @property
    def available_balance(self) -> Decimal:
        return self.wallet_balance - self.used_margin

    def unrealized_pnl(self, marks: dict[str, Decimal]) -> Decimal:
        """Needs a mark per symbol: one price cannot value three instruments."""
        return sum((account.position.unrealized_pnl(marks[symbol])
                    for symbol, account in self._members.items() if symbol in marks),
                   Decimal(0))

    def equity(self, marks: dict[str, Decimal]) -> Decimal:
        return self.wallet_balance + self.unrealized_pnl(marks)

    def assert_invariants(self, marks: dict[str, Decimal],
                          *, tolerance: Decimal = Decimal("0.000001")) -> None:
        """A6 and P5, restated for the purse.

        Per symbol they cannot both hold once the wallet is shared: P5 reads
        `equity = available + used_margin + unrealized`, and with one wallet behind three
        screens the left side is the purse's while `used_margin` on the right is one symbol's.
        The statement is true of the whole purse, which is where it is checked.
        """
        unrealized = self.unrealized_pnl(marks)
        equity = self.wallet_balance + unrealized
        a6 = self.capital_base_usdt + self.realized_delta - self.charges_delta + unrealized
        p5 = self.available_balance + self.used_margin + unrealized
        if abs(equity - a6) > tolerance:
            raise AssertionError(f"A6 invariant broken on shared cash: {equity} vs {a6}")
        if abs(equity - p5) > tolerance:
            raise AssertionError(f"P5 invariant broken on shared cash: {equity} vs {p5}")

    def view(self) -> dict[str, Any]:
        return {"shared": True, "symbols": sorted(self._members),
                "capital_base_usdt": self.capital_base_usdt,
                "wallet_balance": self.wallet_balance,
                "used_margin": self.used_margin,
                "available_balance": self.available_balance,
                "used_margin_by_symbol": {symbol: account.position.initial_margin
                                          for symbol, account in self._members.items()}}


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
    #: The shared wallet this account draws from, or None when it owns its cash alone.
    #: `None` is the single-symbol run and every line below then behaves exactly as before.
    cash: "SharedCash | None" = None

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
        """Cash. The shared purse's when there is one, this account's own otherwise."""
        if self.cash is not None:
            return self.cash.wallet_balance
        return (self.capital_base_usdt
                + (self.realized_pnl - self.realized_at_anchor)
                - (self.cash_charges - self.charges_at_anchor))

    @property
    def own_wallet_balance(self) -> Decimal:
        """What this symbol alone would hold. Kept for per-instrument analytics, which still
        have to be able to say what this instrument did rather than what the purse holds."""
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
        """This symbol's own posted margin. Per symbol even on a shared purse: it is what backs
        *this* position, and the liquidation arithmetic below is about this position."""
        return self.position.initial_margin

    @property
    def available_balance(self) -> Decimal:
        """What may still be committed, which is a property of the wallet and not of a symbol.

        On a shared purse every symbol's posted margin is subtracted, so a BTCUSDT position
        shrinks what ETHUSDT may open - the behaviour a real Binance wallet has and the one the
        paper screen has to copy if it is to be a rehearsal rather than a looser game.
        """
        if self.cash is not None:
            return self.cash.available_balance
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
        """A6 and P5 for an account that owns its cash.

        On a shared purse neither can be stated per symbol - the wallet on the left belongs to
        three screens while `used_margin` on the right belongs to one - so the check moves to
        `SharedCash.assert_invariants`, which needs a mark per symbol and is called by the
        runtime that holds all three. Skipping it here is therefore not a weakening: it is the
        same statement, made where it is true.
        """
        if self.cash is not None:
            return
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
            # Present only on a shared purse, so a reader can see that the available balance is
            # the wallet's rather than this symbol's and where the rest of the margin went.
            "cash": self.cash.view() if self.cash is not None else None,
            "own_wallet_balance": self.own_wallet_balance if self.cash is not None else None,
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
