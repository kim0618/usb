"""What a LIVE market order would cost, priced on Binance's own book and the account's own rate.

The paper terminal answers this by sending the order to a clone of its engine. There is no clone
of Binance, so the same question is answered the only honest way available off-exchange: walk
the real order book for the size, charge the account's real taker rate, and say plainly that the
fill is an estimate until the exchange fills it.

Two rules keep this from drifting into fiction:

* **The book is walked, never averaged.** If the visible depth cannot cover the size, the side is
  refused with `NO_LIQUIDITY` rather than priced at the last level - a preview that quietly
  assumes liquidity it cannot see is worse than no preview.
* **No paper constant appears here.** The fee rate comes from `GET /fapi/v1/commissionRate`, the
  step and minimums from Binance's `exchangeInfo`, the mark from `GET /fapi/v1/premiumIndex`.
  The Bybit taker rate the paper run uses is not imported into this module at all.

The output keys match the paper preview's vocabulary (`entry_fill_price`, `entry_fee`,
`round_trip_cost`, `breakeven_*`...) so the existing panel can render either source without a
second translation table. The values are Binance's.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Sequence

from .filters import QuantityRejected, SymbolFilters
from .models import LONG, SHORT, CommissionRate, MarkPrice

#: Same wording as the paper preview: the breakeven assumes the book keeps its shape while the
#: price moves, which is an assumption and is labelled as one.
BREAKEVEN_BASIS = "EXIT_FILL_AT_ZERO_NET_BOOK_SHAPE_UNCHANGED"
FILL_BASIS = "ESTIMATED_FROM_VISIBLE_DEPTH_NOT_AN_EXCHANGE_QUOTE"


class NoLiquidity(RuntimeError):
    """The visible book cannot fill the size."""


def walk(levels: Sequence[Sequence[Any]], qty: Decimal) -> Decimal:
    """Volume-weighted fill price for `qty`, consuming levels in the order Binance sent them.

    `GET /fapi/v1/depth` returns bids descending and asks ascending, so the order as received is
    the order a market order would consume.
    """
    if qty <= 0:
        raise ValueError("qty must be positive")
    remaining = qty
    cost = Decimal(0)
    for level in levels:
        price = Decimal(str(level[0]))
        size = Decimal(str(level[1]))
        if size <= 0:
            continue
        take = size if size < remaining else remaining
        cost += price * take
        remaining -= take
        if remaining <= 0:
            return cost / qty
    raise NoLiquidity(f"visible depth covers {qty - remaining} of {qty}")


def _levels(depth: dict[str, Any], key: str) -> Iterable[Sequence[Any]]:
    rows = depth.get(key)
    if not isinstance(rows, list):
        raise ValueError(f"depth response carries no {key!r}")
    return rows


def round_trip(*, side: str, qty: Decimal, depth: dict[str, Any], mark: MarkPrice,
               commission: CommissionRate, filters: SymbolFilters,
               leverage: Decimal | None) -> dict[str, Any]:
    """Enter `qty` and close it immediately, priced on the book as it stands."""
    base: dict[str, Any] = {"side": side, "qty": qty, "feasible": False,
                            "mark_price": mark.mark_price, "source": "BINANCE_LIVE",
                            "fill_basis": FILL_BASIS}
    if side not in (LONG, SHORT):
        return {**base, "reject_stage": "INPUT", "reject_code": "UNKNOWN_SIDE",
                "reject_message": f"side must be LONG or SHORT, got {side!r}"}
    entry_side = "asks" if side == LONG else "bids"
    exit_side = "bids" if side == LONG else "asks"
    try:
        entry_levels = list(_levels(depth, entry_side))
        exit_levels = list(_levels(depth, exit_side))
        reference = Decimal(str(entry_levels[0][0])) if entry_levels else Decimal(0)
        filters.validate_market_qty(qty, reference_price=reference)
        entry_price = walk(entry_levels, qty)
        exit_price = walk(exit_levels, qty)
    except QuantityRejected as exc:
        return {**base, "reject_stage": "SIZE", "reject_code": exc.code, "reject_message": exc.message}
    except NoLiquidity as exc:
        return {**base, "reject_stage": "BOOK", "reject_code": "NO_LIQUIDITY", "reject_message": str(exc)}
    except (ValueError, IndexError) as exc:
        return {**base, "reject_stage": "BOOK", "reject_code": "NO_QUOTE", "reject_message": str(exc)}

    rate = commission.taker
    notional = entry_price * qty
    entry_fee = notional * rate
    exit_fee = exit_price * qty * rate
    gross = (exit_price - entry_price) * qty if side == LONG else (entry_price - exit_price) * qty
    mark_price = mark.mark_price
    entry_slippage = (entry_price - mark_price) * (qty if side == LONG else -qty)
    exit_slippage = (exit_price - mark_price) * (-qty if side == LONG else qty)

    if side == LONG:
        breakeven_exit = (entry_price * qty + entry_fee) / (qty * (Decimal(1) - rate))
        move = breakeven_exit - exit_price
        breakeven_mark = mark_price + move
    else:
        breakeven_exit = (entry_price * qty - entry_fee) / (qty * (Decimal(1) + rate))
        move = exit_price - breakeven_exit
        breakeven_mark = mark_price - move

    return {
        **base,
        "feasible": True,
        "notional": notional,
        "leverage": leverage,
        # Binance's own initial margin for a new position is notional / leverage; it is shown as
        # an estimate because the account's real requirement also depends on its risk tier,
        # which this preview does not read.
        "required_margin": (notional / leverage) if leverage and leverage > 0 else None,
        "entry_fill_price": entry_price,
        "entry_fee": entry_fee,
        "entry_slippage": entry_slippage,
        "entry_slippage_pnl": -entry_slippage,
        "exit_fill_price": exit_price,
        "exit_fee": exit_fee,
        "exit_slippage": exit_slippage,
        "exit_slippage_pnl": -exit_slippage,
        "round_trip_cost": entry_fee + exit_fee + entry_slippage + exit_slippage,
        "immediate_round_trip_net": gross - entry_fee - exit_fee,
        "breakeven_exit_fill_price": breakeven_exit,
        "breakeven_mark_price": breakeven_mark,
        "breakeven_move": move,
        "breakeven_move_pct": move / mark_price if mark_price else None,
        "breakeven_basis": BREAKEVEN_BASIS,
        "fee_rate": rate,
        "fee_source": "binance GET /fapi/v1/commissionRate",
    }


__all__ = ["NoLiquidity", "round_trip", "walk", "BREAKEVEN_BASIS", "FILL_BASIS"]
