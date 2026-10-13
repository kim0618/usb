"""Rebuilding the manual trades from the paper ledger.

The ledger is the authority, not the terminal's display. A trade is reconstructed by walking the
events in sequence and attributing every cash charge to the position that was open when it was
charged, because `POSITION_CLOSE.net_pnl` is `gross - exit fee` and leaves out the entry fee and
all funding.

`ACCOUNT_RESET` is the other thing that has to be handled honestly. Money added by a reset is not
profit, so lifetime and current-segment performance are computed separately and never mixed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

LONG = "LONG"
SHORT = "SHORT"

OPEN = "POSITION_OPEN"
INCREASE = "POSITION_INCREASE"
CLOSE = "POSITION_CLOSE"
REDUCE = "POSITION_REDUCE"
FEE = "FEE"
FUNDING = "FUNDING"
RESET = "ACCOUNT_RESET"
RUN_START = "RUN_START"
LIQUIDATION = "LIQUIDATION"
LEVERAGE_CHANGE = "LEVERAGE_CHANGE"


def utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dec(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


@dataclass
class ManualTrade:
    """One round trip, from the open that started it to the close that flattened it."""

    index: int
    side: str
    opened_ts_ms: int
    closed_ts_ms: int
    hold_ms: int
    leverage: float
    qty: float
    entry_price: float
    exit_price: float
    gross_pnl: float
    entry_fee: float
    exit_fee: float
    fees: float
    funding: float
    net_pnl: float
    entry_notional: float
    gross_bp: float
    net_bp: float
    mae: float | None
    mfe: float | None
    mae_price: float | None
    mfe_price: float | None
    liquidated: bool
    segment: int                 # 0 = before the first reset, 1 = after it, and so on
    equity_before: float
    equity_after: float
    increases: int = 0

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0

    def as_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["opened_utc"] = utc(self.opened_ts_ms)
        out["closed_utc"] = utc(self.closed_ts_ms)
        out["hold_minutes"] = self.hold_ms / 60_000
        return out


@dataclass
class Reconstruction:
    trades: list[ManualTrade] = field(default_factory=list)
    resets: list[dict[str, Any]] = field(default_factory=list)
    open_position: dict[str, Any] | None = None
    run_start_ms: int | None = None
    liquidations: int = 0
    rejected_orders: int = 0
    leverage_changes: int = 0
    unattributed_fees: float = 0.0
    unattributed_funding: float = 0.0
    config: dict[str, Any] | None = None

    def segment_trades(self, segment: int) -> list[ManualTrade]:
        return [t for t in self.trades if t.segment == segment]

    @property
    def current_segment(self) -> int:
        return len(self.resets)


def load_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def reconstruct(events: list[dict[str, Any]]) -> Reconstruction:
    """Walk the ledger once, attributing every charge to the position it belonged to."""
    out = Reconstruction()
    segment = 0
    index = 0

    # State of the position currently being built, or None while flat.
    pending: dict[str, Any] | None = None
    # The engine writes the FEE for a fill *before* the POSITION_OPEN it belongs to, so a fee
    # charged while flat is the entry fee of the position about to open. Holding it here is what
    # keeps entry fees from vanishing into an "unattributed" bucket.
    pending_entry_fees = 0.0

    for event in events:
        kind = event["event_type"]

        if kind == RUN_START:
            out.run_start_ms = int(event["ts_ms"])
            out.config = event.get("config")
            continue

        if kind == RESET:
            segment += 1
            out.resets.append({
                "ts_ms": int(event["ts_ms"]), "utc": utc(int(event["ts_ms"])),
                "segment_opened": segment, "reason": event.get("reason"),
                "before_equity": float(_dec(event.get("before_equity"))),
                "after_equity": float(_dec(event.get("after_equity"))),
                "before_equity_krw": float(_dec(event.get("before_equity_krw"))),
                "after_equity_krw": float(_dec(event.get("after_equity_krw"))),
                "preserved_realized_pnl": float(_dec(event.get("preserved_realized_pnl"))),
                "preserved_fees": float(_dec(event.get("preserved_fees"))),
                "preserved_funding": float(_dec(event.get("preserved_funding"))),
                "topped_up": float(_dec(event.get("after_equity"))
                                   - _dec(event.get("before_equity")))})
            continue

        if kind == "ORDER_REJECTED":
            out.rejected_orders += 1
            continue

        if kind == LEVERAGE_CHANGE:
            out.leverage_changes += 1
            continue

        if kind == LIQUIDATION:
            out.liquidations += 1
            if pending is not None:
                pending["liquidated"] = True
            continue

        if kind == OPEN:
            pending = {
                "entry_fee": pending_entry_fees,
                "opened_ts_ms": int(event["ts_ms"]),
                "leverage": float(_dec(event.get("leverage"))),
                "signed_qty": float(_dec(event.get("signed_qty"))),
                "entry_price": float(_dec(event.get("avg_entry"))),
                "equity_before": float(_dec(event.get("equity"))),
                "fees": pending_entry_fees, "funding": 0.0, "increases": 0,
                "liquidated": False, "segment": segment,
            }
            pending_entry_fees = 0.0
            continue

        if kind == INCREASE:
            if pending is not None:
                pending["increases"] += 1
                pending["signed_qty"] = float(_dec(event.get("signed_qty")))
                pending["entry_price"] = float(_dec(event.get("avg_entry")))
            continue

        if kind == FEE:
            amount = float(_dec(event.get("amount")))
            if pending is not None:
                pending["fees"] += amount
            else:
                pending_entry_fees += amount
            continue

        if kind == FUNDING:
            amount = float(_dec(event.get("amount_paid")))
            if pending is not None:
                pending["funding"] += amount
            else:
                out.unattributed_funding += amount
            continue

        if kind in (CLOSE, REDUCE):
            if pending is None:
                continue
            exit_fee = float(_dec(event.get("fee")))
            # The close's own FEE event has already been added to `pending["fees"]`; the close
            # event repeats the same number, so the difference is what the entry and any
            # increases cost.
            fees = pending["fees"]
            entry_fee = max(fees - exit_fee, 0.0)
            gross = float(_dec(event.get("gross_pnl")))
            funding = pending["funding"]
            net = gross - fees - funding
            qty = float(_dec(event.get("closed_qty")))
            entry_price = float(_dec(event.get("entry_avg"))) or pending["entry_price"]
            notional = qty * entry_price

            if kind == CLOSE:
                index += 1
                out.trades.append(ManualTrade(
                    index=index,
                    side=event.get("side") or (LONG if pending["signed_qty"] > 0 else SHORT),
                    opened_ts_ms=int(event.get("opened_ts_ms") or pending["opened_ts_ms"]),
                    closed_ts_ms=int(event["ts_ms"]),
                    hold_ms=int(event.get("hold_ms") or 0),
                    leverage=float(_dec(event.get("leverage")) or pending["leverage"]),
                    qty=qty, entry_price=entry_price,
                    exit_price=float(_dec(event.get("exit_price"))),
                    gross_pnl=gross, entry_fee=entry_fee, exit_fee=exit_fee, fees=fees,
                    funding=funding, net_pnl=net, entry_notional=notional,
                    gross_bp=(gross / notional * 1e4) if notional else 0.0,
                    net_bp=(net / notional * 1e4) if notional else 0.0,
                    mae=_opt(event.get("max_adverse_excursion")),
                    mfe=_opt(event.get("max_favourable_excursion")),
                    mae_price=_opt(event.get("mae_mark_price")),
                    mfe_price=_opt(event.get("mfe_mark_price")),
                    liquidated=pending["liquidated"], segment=pending["segment"],
                    equity_before=pending["equity_before"],
                    equity_after=float(_dec(event.get("equity"))),
                    increases=pending["increases"]))
                pending = None
            else:
                pending["fees"] = 0.0
                pending["funding"] = 0.0
            continue

    out.unattributed_fees += pending_entry_fees
    if pending is not None:
        out.open_position = {
            "opened_ts_ms": pending["opened_ts_ms"], "opened_utc": utc(pending["opened_ts_ms"]),
            "side": LONG if pending["signed_qty"] > 0 else SHORT,
            "signed_qty": pending["signed_qty"], "entry_price": pending["entry_price"],
            "leverage": pending["leverage"], "segment": pending["segment"],
            "fees_so_far": pending["fees"], "funding_so_far": pending["funding"],
            "note": "still open at the snapshot; excluded from realised performance"}
    return out


def _opt(value: Any) -> float | None:
    return None if value is None else float(_dec(value))
