"""Where a LIVE position would be liquidated, estimated before the order is sent.

After a fill the screen shows Binance's own `positionRisk.liquidationPrice` and nothing here
replaces it. Before the fill there is no such figure, so this module solves Binance's published
USDs-M liquidation formula (single-asset mode, one-way) for the position the order would leave:

    LP = (M + cum - side * Q * EP) / (Q * mmr - side * Q)

which, written per side, is

    LONG   LP = (Q * EP - M - cum) / (Q * (1 - mmr))
    SHORT  LP = (Q * EP + M + cum) / (Q * (1 + mmr))

`Q` and `EP` are the resulting position's size and average entry (held plus the order, the order
priced at its walked fill). `mmr` and `cum` are the maintenance ratio and amount of the bracket
the position sits in *at the liquidation price* - maintenance margin is charged on the mark
notional, so that is the band that applies when the mark gets there. `M` is the money standing
behind the position:

* ISOLATED: the position's isolated wallet. A held position contributes
  `isolatedMargin - unRealizedProfit` (its wallet, without the PnL the formula accounts for
  through `EP`); the order adds its initial margin, `notional / leverage`.
* CROSSED: the cross wallet, less the maintenance margin of every *other* position, plus their
  unrealised PnL. Those other positions are held still; when they move, so does this figure.

It is an estimate and is labelled as one. The entry fee, funding and any later fill at a different
price move Binance's figure away from it; the fee alone is of the order of the taker rate in
price terms. Anything the formula needs and the account did not supply returns no price and a
reason, never a price computed on an assumed zero.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .brackets import Bracket, LeverageBrackets
from .models import LONG, SHORT, AccountBalance, LivePosition

ISOLATED = "ISOLATED"
CROSSED = "CROSSED"

BASIS = "ESTIMATE_BINANCE_FORMULA_BEFORE_FEES_AND_FUNDING"

#: Why there is no estimate. The screen shows "-" for every one of these.
NO_BRACKETS = "LIQ_BRACKETS_UNKNOWN"
NO_MAINT_AMOUNT = "LIQ_BRACKET_CUM_MISSING"
MARGIN_TYPE_UNKNOWN = "LIQ_MARGIN_TYPE_UNKNOWN"
REVERSE = "LIQ_REVERSE_SIDE"
WALLET_UNKNOWN = "LIQ_WALLET_UNKNOWN"
NO_SOLUTION = "LIQ_NO_BRACKET_SOLUTION"


def _refused(code: str) -> dict[str, Any]:
    return {"liquidation_price": None, "liquidation_distance_pct": None,
            "liquidation_basis": BASIS, "liquidation_refused": code}


def solve(*, side: str, qty: Decimal, entry_price: Decimal, margin: Decimal,
          band: Bracket) -> Decimal | None:
    """The formula for one bracket. None when the band carries no maintenance amount."""
    if band.maint_amount is None or qty <= 0:
        return None
    mmr, cum = band.maint_margin_ratio, band.maint_amount
    if side == LONG:
        price = (qty * entry_price - margin - cum) / (qty * (Decimal(1) - mmr))
    else:
        price = (qty * entry_price + margin + cum) / (qty * (Decimal(1) + mmr))
    return price if price > 0 else Decimal(0)


def solve_on_table(*, side: str, qty: Decimal, entry_price: Decimal, margin: Decimal,
                   brackets: LeverageBrackets) -> tuple[Decimal | None, Bracket | None]:
    """The price whose own notional lands in the bracket it was solved with.

    Each band is tried in turn and the first self-consistent answer is kept. A LONG that cannot
    be liquidated above zero (margin covers the whole position) answers 0 in the first band.
    """
    for band in brackets.brackets:
        price = solve(side=side, qty=qty, entry_price=entry_price, margin=margin, band=band)
        if price is None:
            return None, band
        notional = qty * price
        if band.floor <= notional <= band.cap:
            return price, band
    return None, None


def estimate(*, side: str, qty: Decimal, fill_price: Decimal, leverage: Decimal | None,
             margin_type: str | None, brackets: LeverageBrackets | None,
             position: LivePosition | None, balance: AccountBalance | None,
             mark_price: Decimal) -> dict[str, Any]:
    """The estimate for opening `qty` on `side` at `fill_price`, with the reason when there is none."""
    if brackets is None:
        return _refused(NO_BRACKETS)
    if not leverage or leverage <= 0:
        return _refused(NO_BRACKETS)
    held = position if position is not None and not position.is_flat else None
    if held is not None and held.side != side:
        # The other side reduces the held position; there is no new position to liquidate.
        return _refused(REVERSE)

    held_qty = held.qty if held is not None else Decimal(0)
    held_entry = held.entry_price if held is not None else None
    if held is not None and held_entry is None:
        return _refused(WALLET_UNKNOWN)
    total_qty = held_qty + qty
    avg_entry = ((held_qty * held_entry if held_entry is not None else Decimal(0))
                 + qty * fill_price) / total_qty

    kind = (margin_type or "").upper()
    if kind == ISOLATED:
        held_wallet = Decimal(0)
        if held is not None:
            if held.isolated_margin is None:
                return _refused(WALLET_UNKNOWN)
            held_wallet = held.isolated_margin - held.unrealized_pnl
        margin = held_wallet + qty * fill_price / leverage
    elif kind == CROSSED:
        if balance is None or balance.cross_wallet_balance is None:
            return _refused(WALLET_UNKNOWN)
        own_maint = (held.maint_margin or Decimal(0)) if held is not None else Decimal(0)
        own_pnl = held.unrealized_pnl if held is not None else Decimal(0)
        others_maint = balance.maint_margin - own_maint
        others_pnl = balance.unrealized_pnl - own_pnl
        margin = balance.cross_wallet_balance - others_maint + others_pnl
    else:
        return _refused(MARGIN_TYPE_UNKNOWN)

    price, band = solve_on_table(side=side, qty=total_qty, entry_price=avg_entry,
                                 margin=margin, brackets=brackets)
    if price is None:
        return _refused(NO_MAINT_AMOUNT if band is not None else NO_SOLUTION)
    distance = (price - mark_price) / mark_price if mark_price else None
    return {"liquidation_price": price, "liquidation_distance_pct": distance,
            "liquidation_basis": BASIS, "liquidation_refused": None,
            "liquidation_margin_type": kind, "liquidation_bracket": band.bracket if band else None,
            "liquidation_position_qty": total_qty, "liquidation_avg_entry": avg_entry,
            "liquidation_margin": margin}


__all__ = ["BASIS", "CROSSED", "ISOLATED", "estimate", "solve", "solve_on_table"]
