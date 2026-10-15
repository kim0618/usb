"""Manual terminal executions for the chart: one row per fill, classified by what it did.

Two sources, never mixed:

* PAPER - the symbol session's own ledger. Every fill is followed by the position event the
  engine wrote for it (`POSITION_OPEN`, `POSITION_INCREASE`, `POSITION_REDUCE`,
  `POSITION_CLOSE`), so the classification is the engine's, not re-derived here.
* LIVE - the account's audit mirror: the plan an order was built from (`LIVE_ORDER_INTENT`,
  which carries the position at plan time) and Binance's answer (`LIVE_ORDER_RESULT`, which
  carries the fill). A local file read; no signed Binance request is made.

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


def live_executions(lines: Iterable[dict[str, Any]], *, symbol: str,
                    account: str | None) -> list[dict[str, Any]]:
    """Filled orders of one LIVE account on one symbol, oldest first, numbered from 1.

    The plan is the `stage="SUBMIT"` intent line (the one offered to the gate); the fill is the
    result line with the same client order id. A plan with no result was refused or never
    answered, and an answer with nothing executed filled nothing: neither is an execution.
    """
    plans: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
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
        price = _decimal(response.get("avgPrice"))
        if executed is None or executed <= 0 or price is None or price <= 0:
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
            "client_order_id": cid,
        })
    return rows


def live_executions_from_file(path: Path, *, symbol: str,
                              account: str | None) -> list[dict[str, Any]]:
    return live_executions(_mirror_lines(path), symbol=symbol, account=account)


__all__ = ["ADD_ENTRY", "FULL_CLOSE", "INITIAL_ENTRY", "PARTIAL_CLOSE", "POSITION_FLIP",
           "live_executions", "live_executions_from_file", "paper_executions"]
