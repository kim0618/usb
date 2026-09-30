"""How large a LIVE entry this account can actually place, answered on the server.

The paper terminal answers the same question by submitting the order to a throwaway copy of its
engine (`paper/sizing.py`). There is no clone of Binance, so this module does the nearest honest
thing: it takes one candidate size at a time and puts it through every rule that would actually
refuse it, in the order the order router refuses them, using only figures Binance supplied.

The rules, and where each number comes from:

* **Reverse** - `positionRisk`. An open position on the other side means no size is available at
  all; `OrderRouter._open_plan` refuses it and sizing must not offer what the router will reject.
* **Local ceiling** - `BINANCE_LIVE_MAX_QTY`. Checked before the exchange's own filters, the same
  order the router checks it, so the operator is told which limit they hit.
* **Exchange filters** - `exchangeInfo`: LOT_SIZE / MARKET_LOT_SIZE stepSize and minQty, and
  MIN_NOTIONAL against the reference price.
* **Book depth, both ways** - `GET /fapi/v1/depth`, walked by `preview.round_trip`. Entry and
  exit are both required, because a size that fills on a deep ask can face a bid that cannot
  absorb it; the paper engine learned that in production at 1.082 BTC against 0.157 BTC of bid.
* **Margin** - `assets[USDT].availableBalance` against the entry's own required margin and fee,
  both taken from the preview rather than recomputed here.

There is no closed-form maximum in this file and there must not be one. A market order walks the
book, so the fill price - and therefore the margin and the fee - depend on the size being sized.
Any formula would have to assume one price and would overstate MAX in exactly the thin book
where overstating it is most expensive. The search is a binary search over the step grid, which
is sound because every reason a size is refused gets worse with size: ceiling, notional, depth
and margin all move one way, so their conjunction does too.

Nothing here sends anything. Every call is a read.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .filters import SymbolFilters
from .models import LONG, SHORT, CommissionRate, LivePosition, MarkPrice
from .preview import round_trip

#: The quick-size ladder the panel offers, as fractions of MAX.
DEFAULT_FRACTIONS = (Decimal("0.25"), Decimal("0.50"), Decimal("0.75"), Decimal("1.00"))

FRACTION_LABELS = {Decimal("0.25"): "25%", Decimal("0.50"): "HALF",
                   Decimal("0.75"): "75%", Decimal("1.00"): "MAX"}

#: What MAX means here, carried with the answer so a reader never has to guess. Same definition
#: the paper terminal settled on.
MAX_DEFINITION = "ENTRY_AND_IMMEDIATE_EXIT_ON_THIS_SNAPSHOT_WITHIN_LOCAL_CEILING"

REVERSE_NOT_ALLOWED = "REVERSE_NOT_ALLOWED"
QTY_ABOVE_LOCAL_MAXIMUM = "QTY_ABOVE_LOCAL_MAXIMUM"
INSUFFICIENT_MARGIN = "INSUFFICIENT_MARGIN"
MARGIN_UNKNOWN = "MARGIN_UNKNOWN"


def floor_to_step(qty: Decimal, step: Decimal) -> Decimal:
    """Always down. A fraction of MAX that rounded up would be a size MAX already refused."""
    if step <= 0:
        raise ValueError("qty step must be positive")
    return (qty / step).to_integral_value(rounding="ROUND_DOWN") * step


def ceil_to_step(qty: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise ValueError("qty step must be positive")
    return (qty / step).to_integral_value(rounding="ROUND_CEILING") * step


def reference_price(depth: dict[str, Any], side: str) -> Decimal:
    """Top of the side a market entry would consume, the same price `round_trip` measures the
    notional at."""
    rows = depth.get("asks" if side == LONG else "bids")
    if not isinstance(rows, list) or not rows:
        return Decimal(0)
    return Decimal(str(rows[0][0]))


def smallest_qty(filters: SymbolFilters, reference: Decimal) -> Decimal:
    """The smallest grid size that clears every *lower* bound.

    This exists because MIN_NOTIONAL points the other way from every other rule. Quantity
    minimums and the notional minimum refuse sizes for being too small, so feasibility is not
    monotonic at the bottom of the range and a search that started at `minQty` would conclude
    "nothing fits" on a symbol whose minimum order is simply worth less than MIN_NOTIONAL. The
    search therefore starts here, above both floors, where feasibility is monotonic again.
    """
    floor_qty = max(filters.market_min_qty, filters.min_qty)
    if reference > 0 and filters.min_notional > 0:
        floor_qty = max(floor_qty, ceil_to_step(filters.min_notional / reference,
                                                filters.market_qty_step))
    return ceil_to_step(floor_qty, filters.market_qty_step)


def _refused(qty: Decimal, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"qty": qty, "feasible": False, "reject_code": code, "reject_message": message, **extra}


def check(*, side: str, qty: Decimal, depth: dict[str, Any], mark: MarkPrice,
          commission: CommissionRate, filters: SymbolFilters, leverage: Decimal | None,
          available: Decimal | None, ceiling: Decimal | None,
          position: LivePosition | None) -> dict[str, Any]:
    """Everything that must hold for `qty` to be placeable right now, in the router's order."""
    if qty <= 0:
        return _refused(qty, "QTY_NOT_POSITIVE", "수량은 0보다 커야 합니다.")
    if position is not None and not position.is_flat and position.side != side:
        return _refused(qty, REVERSE_NOT_ALLOWED,
                        f"{position.side} 포지션 {position.qty}이 열려 있습니다. 먼저 청산하세요.")
    if ceiling is not None and qty > ceiling:
        return _refused(qty, QTY_ABOVE_LOCAL_MAXIMUM,
                        f"수량 {qty}이 이 프로세스의 상한 {ceiling}을 넘습니다 (BINANCE_LIVE_MAX_QTY).")

    # Filters and both sides of the book, priced by the module the preview panel already uses.
    priced = round_trip(side=side, qty=qty, depth=depth, mark=mark, commission=commission,
                        filters=filters, leverage=leverage)
    if not priced.get("feasible"):
        return _refused(qty, str(priced.get("reject_code") or "NOT_FEASIBLE"),
                        str(priced.get("reject_message") or "이 수량은 주문할 수 없습니다."),
                        reject_stage=priced.get("reject_stage"))

    required_margin = priced.get("required_margin")
    entry_fee = priced.get("entry_fee")
    if required_margin is None:
        # No leverage means no margin figure, and guessing one would be the arithmetic this
        # module exists to avoid. The size is not offered.
        return _refused(qty, MARGIN_UNKNOWN, "레버리지를 읽지 못해 필요 증거금을 알 수 없습니다.")
    required_total = required_margin + (entry_fee or Decimal(0))
    if available is None:
        return _refused(qty, MARGIN_UNKNOWN, "주문가능 잔고를 읽지 못했습니다.")
    if required_total > available:
        return _refused(qty, INSUFFICIENT_MARGIN,
                        f"필요 {required_total} USDT가 주문가능 {available} USDT를 넘습니다.",
                        required_total=required_total)
    return {**priced, "qty": qty, "feasible": True, "required_total": required_total,
            "available_after": available - required_total}


def max_open(*, side: str, depth: dict[str, Any], mark: MarkPrice, commission: CommissionRate,
             filters: SymbolFilters, leverage: Decimal | None, available: Decimal | None,
             ceiling: Decimal | None, position: LivePosition | None) -> dict[str, Any]:
    """The largest grid quantity that passes every rule above.

    Binary search, biased upward so it cannot stall when the bounds are one step apart. The
    search never models *why* a size failed, only that it did.
    """
    step = filters.market_qty_step
    floor_qty = smallest_qty(filters, reference_price(depth, side))
    probe = lambda size: check(side=side, qty=size, depth=depth, mark=mark, commission=commission,
                               filters=filters, leverage=leverage, available=available,
                               ceiling=ceiling, position=position)
    smallest = probe(floor_qty)
    if not smallest.get("feasible"):
        # Not even the exchange minimum fits. Carry the real reason rather than inventing one.
        return {**_refused(Decimal(0), str(smallest.get("reject_code")),
                           str(smallest.get("reject_message"))), "label": "MAX",
                "fraction": Decimal(1)}

    high = floor_to_step(min(filters.market_max_qty, ceiling) if ceiling is not None
                         else filters.market_max_qty, step)
    low, best = floor_qty, smallest
    while low < high:
        middle = floor_to_step(low + (high - low) / 2 + step, step)
        if middle > high:
            middle = high
        candidate = probe(middle)
        if candidate.get("feasible"):
            best, low = candidate, middle
        else:
            high = middle - step
    return {**best, "label": "MAX", "fraction": Decimal(1)}


def presets(*, side: str, depth: dict[str, Any], mark: MarkPrice, commission: CommissionRate,
            filters: SymbolFilters, leverage: Decimal | None, available: Decimal | None,
            ceiling: Decimal | None, position: LivePosition | None,
            fractions: tuple[Decimal, ...] = DEFAULT_FRACTIONS) -> dict[str, Any]:
    """MAX, plus each fraction of it, every one re-checked rather than scaled down.

    A fraction is applied to the quantity and then put through `check` again, because half the
    size is not half the fill price once the book is being walked. The result is floored to the
    step: rounding up would offer a size the ceiling or the book has already refused.
    """
    step = filters.market_qty_step
    ceiling_quote = max_open(side=side, depth=depth, mark=mark, commission=commission,
                             filters=filters, leverage=leverage, available=available,
                             ceiling=ceiling, position=position)
    rows: list[dict[str, Any]] = []
    for fraction in fractions:
        label = FRACTION_LABELS.get(fraction, f"{fraction * 100:.0f}%")
        if not ceiling_quote.get("feasible"):
            rows.append({**_refused(Decimal(0), str(ceiling_quote.get("reject_code")),
                                    str(ceiling_quote.get("reject_message"))),
                         "label": label, "fraction": fraction})
            continue
        size = (ceiling_quote["qty"] if fraction == 1
                else floor_to_step(ceiling_quote["qty"] * fraction, step))
        row = check(side=side, qty=size, depth=depth, mark=mark, commission=commission,
                    filters=filters, leverage=leverage, available=available,
                    ceiling=ceiling, position=position)
        rows.append({**row, "label": label, "fraction": fraction})

    return {
        "side": side,
        "leverage": leverage,
        "available_balance": available,
        "local_max_qty": ceiling,
        "max_qty": ceiling_quote["qty"] if ceiling_quote.get("feasible") else Decimal(0),
        "max_feasible": bool(ceiling_quote.get("feasible")),
        "reject_code": ceiling_quote.get("reject_code"),
        "reject_message": ceiling_quote.get("reject_message"),
        "max_definition": MAX_DEFINITION,
        "instrument": {"qty_step": step,
                       "min_qty": max(filters.market_min_qty, filters.min_qty),
                       "smallest_orderable_qty": smallest_qty(filters, reference_price(depth, side)),
                       "min_notional": filters.min_notional,
                       "market_max_qty": filters.market_max_qty},
        "presets": rows,
    }


__all__ = ["check", "max_open", "presets", "floor_to_step", "ceil_to_step", "smallest_qty",
           "reference_price", "DEFAULT_FRACTIONS", "FRACTION_LABELS", "MAX_DEFINITION"]
