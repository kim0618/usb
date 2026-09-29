"""Pre-trade cost preview and the fast live view. Display only; no account state changes.

Both answers come from throwaway clones of the engine, the recipe `sizing` already uses:

* **Round trip** - a clone is sent the entry, a second clone of *that* is sent the full CLOSE on
  the same book. Entry fill, exit fill, both fees and the net are read off those clones, so the
  cost of trading a size is what the engine would charge, not a fee rate times a guess.
* **Live** - a clone is advanced to the newest feed quote with `apply_market`, exactly as the
  real engine will be on its next observation, and the position preview is taken from it. The
  real engine and the input tape are never touched, so the screen can refresh faster than the
  1 Hz tape without the tape growing.

The breakeven is the one derived figure, and it is derived from engine outputs: the exit fill
at which the round trip nets zero, given the entry fill and entry fee the clone produced and the
run's own taker rate (`config.fees`, the single fee authority). It assumes the book keeps its
shape (spread and depth) while the price moves, and says so in `breakeven_basis`.
"""
from __future__ import annotations

from decimal import Decimal
from itertools import chain
from typing import Any

from .account import SIDE_LONG, SIDE_SHORT
from .book import NoLiquidity, Quote
from .engine import OrderRejected, PaperEngine
from .ledger import EventType
from .pnl_breakdown import open_position_preview

PREVIEW_REASON = "ORDER_PREVIEW"
BREAKEVEN_BASIS = "EXIT_FILL_AT_ZERO_NET_BOOK_SHAPE_UNCHANGED"


def _classify(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, OrderRejected):
        return exc.code, str(exc)
    if isinstance(exc, NoLiquidity):
        return "NO_LIQUIDITY", str(exc)
    return "INVALID_ORDER", str(exc)


def advanced(engine: PaperEngine, quote: Quote | None) -> PaperEngine:
    """A clone moved onto `quote` the way the real engine will be on its next observation.

    An older or equal quote is not applied: the clone then simply mirrors the engine.
    """
    from .sizing import _clone

    clone = _clone(engine)
    if quote is not None and (engine.quote is None or quote.ts_ms > engine.quote.ts_ms):
        clone.apply_market(quote)
    return clone


def _first(events: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return next(event for event in events if event["event_type"] == kind)


def round_trip(engine: PaperEngine, side: str, qty: Decimal) -> dict[str, Any]:
    """Enter `qty` on `side` and close it at once, on clones, on the engine's current quote."""
    from .sizing import _clone, max_entry

    quote = engine.quote
    base: dict[str, Any] = {"side": side, "qty": qty, "feasible": False}
    if quote is None:
        return {**base, "reject_stage": "QUOTE", "reject_code": "NO_QUOTE",
                "reject_message": "no market quote has been observed yet"}
    base.update({"quote_ts_ms": quote.ts_ms, "mark_price": quote.mark_price,
                 "best_bid": quote.best_bid, "best_ask": quote.best_ask,
                 "leverage": engine.leverage})
    if side not in (SIDE_LONG, SIDE_SHORT):
        return {**base, "reject_stage": "INPUT", "reject_code": "UNKNOWN_SIDE",
                "reject_message": f"side must be LONG or SHORT, got {side!r}"}
    if not engine.account.position.is_flat:
        # Adding to a position blends the entry price, so "enter and close at once" would no
        # longer describe the cost of this order alone. The preview answers for a flat account.
        return {**base, "reject_stage": "INPUT", "reject_code": "POSITION_OPEN",
                "reject_message": "pre-trade preview is for a flat account"}

    def refused(stage: str, exc: Exception) -> dict[str, Any]:
        code, message = _classify(exc)
        safe = max_entry(engine, side)
        return {**base, "reject_stage": stage, "reject_code": code, "reject_message": message,
                "safe_max_qty": safe.qty if safe.feasible else Decimal(0)}

    entered = _clone(engine)
    try:
        entered.submit_order(ts_ms=quote.ts_ms, side=side, qty=qty, intent="OPEN",
                             request_id=f"preview-open-{quote.ts_ms}", reason=PREVIEW_REASON)
    except (OrderRejected, NoLiquidity, ValueError) as exc:
        return refused("ENTRY", exc)
    entry_fill = _first(entered.ledger.events, EventType.FILL)
    entry_fee = Decimal(str(_first(entered.ledger.events, EventType.FEE)["amount"]))
    required_margin = entered.account.used_margin

    closed = _clone(entered)
    held = entered.account.position.abs_qty
    try:
        closed.submit_order(ts_ms=quote.ts_ms, side=side, qty=held, intent="CLOSE",
                            request_id=f"preview-close-{quote.ts_ms}", reason=PREVIEW_REASON)
    except (OrderRejected, NoLiquidity, ValueError) as exc:
        return refused("EXIT", exc)
    exit_fill = _first(closed.ledger.events, EventType.FILL)
    exit_fee = Decimal(str(_first(closed.ledger.events, EventType.FEE)["amount"]))
    gross = Decimal(str(_first(closed.ledger.events, EventType.POSITION_CLOSE)["gross_pnl"]))

    mark = quote.mark_price
    entry_price = Decimal(str(entry_fill["fill_price"]))
    exit_price = Decimal(str(exit_fill["fill_price"]))
    # Cost positive: paying above mark on the way in, receiving below it on the way out.
    entry_slippage = (entry_price - mark) * Decimal(str(entry_fill["signed_delta"]))
    exit_slippage = (exit_price - mark) * Decimal(str(exit_fill["signed_delta"]))
    net = gross - entry_fee - exit_fee

    rate = engine.config.fees.rate_for("TAKER")
    if side == SIDE_LONG:
        breakeven_exit = (entry_price * held + entry_fee) / (held * (Decimal(1) - rate))
        move = breakeven_exit - exit_price
        breakeven_mark = mark + move
    else:
        breakeven_exit = (entry_price * held - entry_fee) / (held * (Decimal(1) + rate))
        move = exit_price - breakeven_exit
        breakeven_mark = mark - move

    return {
        **base,
        "feasible": True,
        "qty": held,
        "notional": Decimal(str(entry_fill["notional"])),
        "required_margin": required_margin,
        "entry_fill_price": entry_price,
        "entry_fee": entry_fee,
        "entry_slippage": entry_slippage,
        "exit_fill_price": exit_price,
        "exit_fee": exit_fee,
        "exit_slippage": exit_slippage,
        # Effects on PnL, signed, so a display never negates a cost itself.
        "entry_slippage_pnl": -entry_slippage,
        "exit_slippage_pnl": -exit_slippage,
        # Everything standing between the mark and money kept, for an instant round trip. By
        # construction it is minus the immediate net: the two slippages are exactly the gross.
        "round_trip_cost": entry_fee + exit_fee + entry_slippage + exit_slippage,
        "immediate_round_trip_net": net,
        "breakeven_exit_fill_price": breakeven_exit,
        "breakeven_mark_price": breakeven_mark,
        "breakeven_move": move,
        "breakeven_move_pct": move / mark,
        "breakeven_basis": BREAKEVEN_BASIS,
        "fee_rate": rate,
    }


def live_position(engine: PaperEngine, quote: Quote | None) -> dict[str, Any]:
    """The position preview on the newest feed quote, without recording anything."""
    clone = advanced(engine, quote)
    return open_position_preview(clone, events=list(chain(engine.ledger.events, clone.ledger.events)))
