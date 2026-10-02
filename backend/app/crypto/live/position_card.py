"""What the held LIVE position has cost so far, and what closing it right now would net.

The paper terminal answers this from its own ledger, which knows every fill it ever made. A
Binance position has no such ledger here, and it may not even have been opened from this
screen, so the position's history is reconstructed from the exchange's own record: `userTrades`
walked backwards until the position is flat again.

That walk is the only honest source for three things the card needs:

* **when the position was opened** - `positionRisk.updateTime` is the last *change*, not the
  open, so a position added to an hour ago would report an hour it has not been held;
* **what it has cost in commission** - every fill inside the window, opening and partial
  closing alike, at the commission Binance actually charged;
* **what has already been realised inside it** - partial closes, which Binance reports per fill.

Funding is read separately, from `income`, scoped to the same window.

The close leg is priced the way the preview panel prices one: walk the opposite side of the real
book for the held quantity. If the visible depth cannot cover it, the net is refused rather than
estimated, because a number produced by pretending the liquidity is there is worse than none.

Nothing here sends anything, and nothing here is recomputed from price and quantity when Binance
published the figure itself: `realizedPnl` and `commission` are taken as given.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

from .filters import SymbolFilters
from .models import LONG, SHORT, CommissionRate, IncomeRow, LivePosition, UserTrade
from .preview import NoLiquidity, walk

#: What the net figure means, carried with it so a reader never has to guess.
NET_BASIS = "CLOSE_ENTIRE_POSITION_AT_MARKET_ON_THIS_BOOK_NOW"

#: Where "포지션 규모" came from. `positionRisk.notional` is Binance's own valuation of the
#: position at its mark and is signed by direction; the screen wants the size of the exposure,
#: so it is reported as an absolute value and labelled with this.
EXPOSURE_FROM_NOTIONAL = "BINANCE_POSITION_RISK_NOTIONAL_ABS"
#: Only when Binance sent no `notional` on the row. `positionAmt x markPrice` is then the same
#: quantity computed from two fields of the same response rather than from a price this package
#: chose, and the difference is visible on screen through this label.
EXPOSURE_FROM_QTY_AND_MARK = "ABS_POSITION_AMT_TIMES_BINANCE_MARK"

#: `positionRisk.initialMargin` is the margin Binance holds against the position. Measured
#: against two leverages on the real account (1x: 83.2149 on a notional of 83.2149; 20x:
#: 339.2448 on 6784.8965), so it tracks the setting rather than being a constant - which is why
#: it is reported instead of `notional / leverage`, a division that ignores the maintenance tier
#: and disagrees with the exchange exactly when the position is close to trouble.
MARGIN_FROM_POSITION_RISK = "BINANCE_POSITION_RISK_INITIAL_MARGIN"

#: How the open time was established, so the screen can say `-` rather than guess.
OPEN_FROM_TRADES = "BINANCE_USER_TRADES_WALKBACK"
OPEN_UNKNOWN = "UNKNOWN"


def opening(trades: Sequence[UserTrade], position: LivePosition) -> dict[str, Any]:
    """Walk `userTrades` newest-first until the position is flat, and report that window.

    The walk stops the moment the running signed quantity reaches zero going backwards: that
    fill is the one that opened the position currently held. A window that never closes - the
    fetched page does not reach back far enough - returns `UNKNOWN` rather than the oldest fill
    it happened to see, because a truncated page would otherwise be reported as the open.
    """
    if position.is_flat:
        return {"opened_at_ms": None, "source": OPEN_UNKNOWN, "commission_paid": Decimal(0),
                "realized_since_open": Decimal(0), "fills": 0}
    ordered = sorted(trades, key=lambda trade: trade.time_ms, reverse=True)
    running = position.signed_qty
    commission = Decimal(0)
    realized = Decimal(0)
    opened_at_ms: int | None = None
    fills = 0
    for trade in ordered:
        signed = trade.qty if trade.buyer else -trade.qty
        fills += 1
        commission += trade.commission
        realized += trade.realized_pnl
        before = running - signed
        if (before == 0) or (before * running < 0):
            # This fill took the account off flat (or through it); it opened what is held now.
            opened_at_ms = trade.time_ms
            break
        running = before
    if opened_at_ms is None:
        return {"opened_at_ms": None, "source": OPEN_UNKNOWN, "commission_paid": Decimal(0),
                "realized_since_open": Decimal(0), "fills": 0}
    return {"opened_at_ms": opened_at_ms, "source": OPEN_FROM_TRADES,
            "commission_paid": commission, "realized_since_open": realized, "fills": fills}


def funding_since(rows: Sequence[IncomeRow], since_ms: int | None) -> Decimal:
    """Net funding cash flow while this position has been held.

    Binance's `income` is signed the way the wallet moved: negative when funding was paid,
    positive when it was received. The sum is carried with that sign and is *added* to the net,
    never subtracted - inverting it would turn every funding payment into a gain.
    """
    if since_ms is None:
        return Decimal(0)
    return sum((row.income for row in rows if row.time_ms >= since_ms), Decimal(0))


def close_now(*, position: LivePosition, depth: dict[str, Any], commission: CommissionRate,
              filters: SymbolFilters) -> dict[str, Any]:
    """Price closing the whole position at market on the book as it stands."""
    base: dict[str, Any] = {"feasible": False, "qty": position.qty, "basis": NET_BASIS}
    if position.is_flat or position.side is None:
        return {**base, "reject_code": "NO_POSITION", "reject_message": "청산할 포지션이 없습니다."}
    exit_key = "bids" if position.side == LONG else "asks"
    levels = depth.get(exit_key)
    if not isinstance(levels, list) or not levels:
        return {**base, "reject_code": "NO_QUOTE", "reject_message": "Binance 호가를 읽지 못했습니다."}
    try:
        exit_price = walk(levels, position.qty)
    except NoLiquidity as exc:
        return {**base, "reject_code": "NO_LIQUIDITY", "reject_message": str(exc)}
    except (ValueError, IndexError) as exc:
        return {**base, "reject_code": "NO_QUOTE", "reject_message": str(exc)}

    entry = position.entry_price
    if entry is None:
        return {**base, "reject_code": "NO_ENTRY_PRICE",
                "reject_message": "Binance가 진입가를 보고하지 않았습니다."}
    exit_fee = exit_price * position.qty * commission.taker
    gross = ((exit_price - entry) * position.qty if position.side == LONG
             else (entry - exit_price) * position.qty)
    return {**base, "feasible": True, "exit_fill_price": exit_price, "exit_fee": exit_fee,
            "gross_pnl": gross, "fee_rate": commission.taker,
            "reference_price": Decimal(str(levels[0][0])),
            "fee_source": "binance GET /fapi/v1/commissionRate",
            "reject_code": None, "reject_message": None}


def exposure(position: LivePosition) -> dict[str, Any]:
    """How much of the market this position is actually holding, as a positive figure.

    Binance publishes it, so it is read rather than derived: `notional` on the `positionRisk`
    row. The absolute value is taken because a SHORT's notional is negative and "포지션 규모" is
    a size, not a direction - the direction is already the LONG/SHORT badge beside it. The only
    arithmetic fallback is used when the field is absent, and it says so.
    """
    if position.is_flat:
        return {"exposure": None, "exposure_basis": None}
    if position.notional is not None:
        return {"exposure": abs(position.notional), "exposure_basis": EXPOSURE_FROM_NOTIONAL}
    if position.mark_price is not None:
        return {"exposure": position.qty * position.mark_price,
                "exposure_basis": EXPOSURE_FROM_QTY_AND_MARK}
    return {"exposure": None, "exposure_basis": None}


def card(*, position: LivePosition, trades: Sequence[UserTrade], funding_rows: Sequence[IncomeRow],
         depth: dict[str, Any], commission: CommissionRate, filters: SymbolFilters,
         leverage: Decimal | None) -> dict[str, Any]:
    """Everything the position card shows, assembled from Binance's own answers."""
    if position.is_flat:
        return {"open": False}
    window = opening(trades, position)
    funding = funding_since(funding_rows, window["opened_at_ms"])
    close = close_now(position=position, depth=depth, commission=commission, filters=filters)
    net = None
    if close["feasible"]:
        # The same shape the paper card's figure has: what this position will have been worth,
        # start to finish, if it is closed on this book. Commission already charged and the
        # close's own fee are costs and come off; funding is already signed by Binance and is
        # added as it stands.
        net = (window["realized_since_open"] + close["gross_pnl"]
               - window["commission_paid"] - close["exit_fee"] + funding)
    return {
        "open": True,
        "side": position.side,
        "qty": position.qty,
        "leverage": leverage,
        "entry_price": position.entry_price,
        "break_even_price": position.break_even_price,
        "mark_price": position.mark_price,
        # Binance reports an unopened or cross-margin liquidation as 0; the screen shows "-"
        # rather than a price of zero, and that decision is made here, once.
        "liquidation_price": (position.liquidation_price
                              if position.liquidation_price and position.liquidation_price > 0
                              else None),
        "unrealized_pnl": position.unrealized_pnl,
        "notional": position.notional,
        **exposure(position),
        "initial_margin": position.initial_margin,
        "maint_margin": position.maint_margin,
        "margin_basis": MARGIN_FROM_POSITION_RISK,
        "opened_at_ms": window["opened_at_ms"],
        "opened_source": window["source"],
        "commission_paid": (window["commission_paid"]
                            if window["opened_at_ms"] is not None else None),
        "realized_since_open": (window["realized_since_open"]
                                if window["opened_at_ms"] is not None else None),
        "funding_income": funding if window["opened_at_ms"] is not None else None,
        "close": close,
        "net_if_closed": net,
        "net_basis": NET_BASIS,
        "net_complete": bool(close["feasible"] and window["opened_at_ms"] is not None),
    }


__all__ = ["card", "close_now", "exposure", "opening", "funding_since", "NET_BASIS",
           "OPEN_FROM_TRADES", "OPEN_UNKNOWN", "EXPOSURE_FROM_NOTIONAL",
           "EXPOSURE_FROM_QTY_AND_MARK", "MARGIN_FROM_POSITION_RISK"]
