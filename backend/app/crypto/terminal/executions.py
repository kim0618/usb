"""Manual terminal executions for the chart: one row per fill, classified by what it did.

Two sources, never mixed:

* PAPER - the symbol session's own ledger. Every fill is followed by the position event the
  engine wrote for it (`POSITION_OPEN`, `POSITION_INCREASE`, `POSITION_REDUCE`,
  `POSITION_CLOSE`), so the classification is the engine's, not re-derived here.
* LIVE - the account's audit mirror: the plan an order was built from (`LIVE_ORDER_INTENT`,
  which carries the position at plan time) and Binance's answer (`LIVE_ORDER_RESULT`, which
  carries the fill). A local file read; no signed Binance request is made.

  The fill price is not always in the answer. Binance's default MARKET response (`ACK`) has
  no `avgPrice`, no `cumQuote` and no `fills` - every one of this account's real results looks
  like that - so the price is resolved, in order, from the answer itself when it has one and
  otherwise from the account's `userTrades` already cached on disk by the performance panel
  (`manual_performance*.json`), matched by order id. Never from a mark or a current quote:
  an order whose fill price cannot be found is left off the chart, not drawn at a guess.

Read only. Nothing here writes a ledger, a tape or a mirror line.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

INITIAL_ENTRY = "INITIAL_ENTRY"
ADD_ENTRY = "ADD_ENTRY"
PARTIAL_CLOSE = "PARTIAL_CLOSE"
FULL_CLOSE = "FULL_CLOSE"
POSITION_FLIP = "POSITION_FLIP"

#: The request id prefix the PAPER order route gives the opening leg of a flip.
FLIP_OPEN_PREFIX = "flip-open-"


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def paper_executions(events: Iterable[dict[str, Any]], *, symbol: str,
                     account: str) -> list[dict[str, Any]]:
    """Fills of one PAPER run on one symbol, oldest first, numbered from 1."""
    fills: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for event in events:
        kind = event.get("event_type")
        request_id = event.get("request_id")
        if kind == "FILL" and request_id:
            fills[str(request_id)] = event
            continue
        if kind not in ("POSITION_OPEN", "POSITION_INCREASE", "POSITION_REDUCE", "POSITION_CLOSE"):
            continue
        fill = fills.pop(str(request_id), None) if request_id else None
        if kind in ("POSITION_OPEN", "POSITION_INCREASE"):
            signed = _decimal(event.get("signed_qty")) or Decimal(0)
            side = "LONG" if signed > 0 else "SHORT"
            if kind == "POSITION_OPEN":
                event_type = (POSITION_FLIP if str(request_id or "").startswith(FLIP_OPEN_PREFIX)
                              else INITIAL_ENTRY)
            else:
                event_type = ADD_ENTRY
            price = fill.get("fill_price") if fill else event.get("avg_entry")
            qty = fill.get("fill_qty") if fill else abs(signed)
        else:
            side = str(event.get("side") or "")
            event_type = PARTIAL_CLOSE if kind == "POSITION_REDUCE" else FULL_CLOSE
            price = event.get("exit_price")
            qty = event.get("closed_qty")
        if side not in ("LONG", "SHORT") or _decimal(price) is None or _decimal(qty) is None:
            continue
        rows.append({
            "seq": len(rows) + 1, "ts_ms": int(event.get("ts_ms") or 0), "mode": "PAPER",
            "symbol": symbol, "account": account, "side": side, "event_type": event_type,
            "price": str(price), "qty": str(qty),
            "leverage": None if event.get("leverage") is None else str(event.get("leverage")),
            "request_id": request_id, "reason": event.get("reason"),
        })
    return rows


def _mirror_lines(path: Path) -> Iterable[dict[str, Any]]:
    """Only the two kinds of line this needs, filtered before parsing: the mirror also holds
    every snapshot and stream event, and those are most of the file."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if ("LIVE_ORDER_INTENT" not in line and "LIVE_ORDER_RESULT" not in line
                    and "AUTO_EXIT_CLOSE_ONLY" not in line):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


#: Where a LIVE row's price came from, most direct first.
PRICE_AVG = "RESPONSE_AVG_PRICE"
PRICE_FILLS = "RESPONSE_FILLS"
PRICE_CUM_QUOTE = "RESPONSE_CUM_QUOTE"
PRICE_USER_TRADES = "USER_TRADES_CACHE"
#: Why a filled answer was still left off the chart.
DROP_NO_PRICE = "NO_PRICE_SOURCE"
DROP_TRADES_INCOMPLETE = "USER_TRADES_QTY_MISMATCH"


def _average(total: Decimal, qty: Decimal) -> Decimal:
    """A computed average, cut to 8 places so three fills do not print 28 digits on a marker."""
    text = format((total / qty).quantize(Decimal("1e-8")), "f").rstrip("0").rstrip(".")
    return Decimal(text)


def _positive(value: Any) -> Decimal | None:
    number = _decimal(value)
    return number if number is not None and number > 0 else None


def user_trades_by_order(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    """The cached `userTrades` rows of one symbol, grouped by Binance order id.

    Read only, from the file the performance panel already keeps; a missing or unreadable file
    is simply no trades. A trade id seen twice counts once."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    if path is None or not path.exists():
        return grouped
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return grouped
    seen: set[str] = set()
    for row in (raw.get("trades") or []) if isinstance(raw, dict) else []:
        if not isinstance(row, dict) or row.get("order_id") is None:
            continue
        trade_id = str(row.get("id"))
        if trade_id in seen:
            continue
        seen.add(trade_id)
        grouped.setdefault(str(row["order_id"]), []).append(row)
    return grouped


def resolve_fill_price(response: dict[str, Any], executed: Decimal,
                       trades: list[dict[str, Any]] | None) -> tuple[Decimal | None, str]:
    """The average fill price of one answered order and where it came from.

    Returns `(price, source)` or `(None, drop_reason)`. The order's own answer wins when it
    carries a price; the cached trades are used only when their quantity adds up to exactly
    what the answer says was executed, so a cache that has not caught up yet is not mistaken
    for a cheaper or dearer fill."""
    average = _positive(response.get("avgPrice"))
    if average is not None:
        return average, PRICE_AVG
    fills = response.get("fills") if isinstance(response.get("fills"), list) else []
    qty = sum((_positive(f.get("qty")) or Decimal(0) for f in fills if isinstance(f, dict)),
              Decimal(0))
    if qty > 0:
        quote = sum(((_positive(f.get("qty")) or Decimal(0)) * (_positive(f.get("price")) or Decimal(0))
                     for f in fills if isinstance(f, dict)), Decimal(0))
        if quote > 0:
            return _average(quote, qty), PRICE_FILLS
    cum_quote = _positive(response.get("cumQuote"))
    if cum_quote is not None:
        return _average(cum_quote, executed), PRICE_CUM_QUOTE
    if trades:
        qty = sum((_positive(t.get("qty")) or Decimal(0) for t in trades), Decimal(0))
        if qty != executed:
            return None, DROP_TRADES_INCOMPLETE
        quote = Decimal(0)
        for trade in trades:
            line = _positive(trade.get("quote_qty"))
            if line is None:
                line = (_positive(trade.get("qty")) or Decimal(0)) * (_positive(trade.get("price")) or Decimal(0))
            quote += line
        if quote > 0:
            return _average(quote, qty), PRICE_USER_TRADES
    return None, DROP_NO_PRICE


def live_execution_audit(lines: Iterable[dict[str, Any]], *, symbol: str, account: str | None,
                         trades: dict[str, list[dict[str, Any]]] | None = None
                         ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`(rows, dropped)`: the chart rows, and every filled answer on this symbol that did not
    become one, with the reason. See `live_executions`."""
    trades = trades or {}
    plans: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for line in lines:
        if account is not None and line.get("account") not in (None, account):
            continue
        kind = line.get("event_type")
        cid = line.get("client_order_id")
        if not cid:
            continue
        if kind == "LIVE_ORDER_INTENT" and line.get("stage") == "SUBMIT":
            plans[str(cid)] = line
            continue
        if kind == "LIVE_ORDER_SENT" and line.get("permission") == "AUTO_EXIT_CLOSE_ONLY":
            # The exit guard's full reduceOnly close has no intent line; its sent params are
            # the plan.
            params = line.get("params") if isinstance(line.get("params"), dict) else {}
            plans[str(cid)] = {"symbol": params.get("symbol"), "intent": "CLOSE",
                               "side": "LONG" if params.get("side") == "SELL" else "SHORT",
                               "position_qty_at_plan": params.get("quantity"), "leverage": None}
            continue
        if kind != "LIVE_ORDER_RESULT":
            continue
        plan = plans.pop(str(cid), None)
        response = line.get("response") if isinstance(line.get("response"), dict) else {}
        if plan is None or plan.get("symbol") != symbol:
            continue
        executed = _decimal(response.get("executedQty"))
        if executed is None or executed <= 0:
            continue
        price, source = resolve_fill_price(
            response, executed, trades.get(str(response.get("orderId"))))
        if price is None:
            dropped.append({"client_order_id": cid, "order_id": response.get("orderId"),
                            "reason": source})
            continue
        held = _decimal(plan.get("position_qty_at_plan")) or Decimal(0)
        if plan.get("intent") == "OPEN":
            event_type = INITIAL_ENTRY if held <= 0 else ADD_ENTRY
        else:
            event_type = FULL_CLOSE if executed >= held else PARTIAL_CLOSE
        ts = response.get("updateTime") or line.get("ts_ms") or 0
        rows.append({
            "seq": len(rows) + 1, "ts_ms": int(ts), "mode": "LIVE", "symbol": symbol,
            "account": line.get("account") or account, "side": plan.get("side"),
            "event_type": event_type, "price": str(price), "qty": str(executed),
            "leverage": None if plan.get("leverage") is None else str(plan.get("leverage")),
            "client_order_id": cid, "price_source": source,
        })
    return rows, dropped


def live_executions(lines: Iterable[dict[str, Any]], *, symbol: str, account: str | None,
                    trades: dict[str, list[dict[str, Any]]] | None = None
                    ) -> list[dict[str, Any]]:
    """Filled orders of one LIVE account on one symbol, oldest first, numbered from 1.

    The plan is the `stage="SUBMIT"` intent line (the one offered to the gate); the fill is the
    result line with the same client order id. A plan with no result was refused or never
    answered, and an answer with nothing executed filled nothing: neither is an execution. The
    price comes from `resolve_fill_price`; `trades` is the cached `userTrades` by order id.
    """
    return live_execution_audit(lines, symbol=symbol, account=account, trades=trades)[0]


def live_executions_from_file(path: Path, *, symbol: str, account: str | None,
                              trades_path: Path | None = None) -> list[dict[str, Any]]:
    return live_executions(_mirror_lines(path), symbol=symbol, account=account,
                           trades=user_trades_by_order(trades_path))


__all__ = ["ADD_ENTRY", "FULL_CLOSE", "INITIAL_ENTRY", "PARTIAL_CLOSE", "POSITION_FLIP",
           "live_executions", "live_executions_from_file", "paper_executions"]
