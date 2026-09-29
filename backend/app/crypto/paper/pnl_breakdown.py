"""Where the money went: price PnL separated from what trading it cost.

Display and preview only. Nothing here changes account state, and nothing here is a second
accounting authority. Two kinds of figure come out of this module:

* **Confirmed** amounts are folded out of the ledger (FEE, FUNDING, FILL, POSITION_* events),
  the same events `analytics.build_trades` folds. A test holds the two folds to the same totals.
* **Expected close** amounts are read off a throwaway clone of the engine that has actually been
  sent the full CLOSE on the current quote, the way `sizing.probe` prices an entry. The close fee,
  the fill price and the wallet after the close are the clone's own numbers, so there is no fee
  rate, no book walk and no PnL expression written down here that could drift from the engine.

The one piece of arithmetic this module does own is a *split* of an amount the engine already
produced: the gap between mark-based unrealized PnL and the fill-based realized PnL of the
simulated close. That gap is the spread and book-walk cost of getting out (contract C1:
PRICE_EMBEDDED, already inside the fill price). It is reported so an operator can see it, and it
is never subtracted from a realized figure a second time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence

from .book import NoLiquidity
from .engine import OrderRejected, PaperEngine
from .ledger import EventType

PREVIEW_REASON = "PNL_PREVIEW"
ZERO = Decimal(0)


def _d(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else ZERO


@dataclass
class _Round:
    """Costs of one flat-to-flat round trip, accumulated in ledger order."""
    side: str | None = None
    gross_realized: Decimal = ZERO
    entry_fee: Decimal = ZERO
    exit_fee: Decimal = ZERO
    funding: Decimal = ZERO
    entry_slippage: Decimal = ZERO
    exit_slippage: Decimal = ZERO
    liquidated: bool = False
    exits: int = 0

    def view(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "gross_realized_pnl": self.gross_realized,
            "entry_fee": self.entry_fee,
            "exit_fee": self.exit_fee,
            "funding": self.funding,
            "entry_slippage": self.entry_slippage,
            "exit_slippage": self.exit_slippage,
            "slippage": self.entry_slippage + self.exit_slippage,
            # Effects on PnL, signed, so a display never has to negate a cost itself.
            "funding_pnl": -self.funding,
            "slippage_pnl": -(self.entry_slippage + self.exit_slippage),
            "net_realized_pnl": self.gross_realized - self.entry_fee - self.exit_fee - self.funding,
            "liquidated": self.liquidated,
            "exits": self.exits,
        }


@dataclass
class Folded:
    closed: list[dict[str, Any]] = field(default_factory=list)
    open: _Round | None = None


def fold(events: Sequence[dict[str, Any]]) -> Folded:
    """Split every round trip's fees into the entry and the exit side.

    A FEE event does not say which side of the trade it paid for, but the FILL that caused it does
    (`intent`), and both carry the same `request_id`. A forced liquidation has no FILL: its FEE is
    written with reason LIQUIDATION, and it is always an exit. Funding belongs to whatever position
    was open when it settled, which is the attribution `analytics.build_trades` uses as well.

    Slippage per fill is measured against the mark at that fill: `(fill - mark) * signed_delta`.
    Buying above mark or selling below it is a positive cost. A liquidation fills at the mark by
    contract (MARK_AT_TRIGGER), so its slippage is zero by construction, not by omission.
    """
    folded = Folded()
    intents: dict[str, str] = {}
    current = _Round()
    pending_entry_fee = ZERO
    pending_entry_slippage = ZERO
    is_open = False

    for event in events:
        kind = event["event_type"]
        if kind == EventType.FILL:
            request_id = event.get("request_id")
            intent = event.get("intent")
            if request_id is not None and intent is not None:
                intents[request_id] = intent
            cost = (_d(event["fill_price"]) - _d(event["mark_price"])) * _d(event["signed_delta"])
            if intent == "CLOSE":
                current.exit_slippage += cost
            elif is_open:
                current.entry_slippage += cost
            else:
                # The opening FILL is written before POSITION_OPEN, like its FEE.
                pending_entry_slippage += cost
        elif kind == EventType.FEE:
            amount = _d(event["amount"])
            exiting = (event.get("reason") == "LIQUIDATION"
                       or intents.get(event.get("request_id") or "") == "CLOSE")
            if exiting:
                current.exit_fee += amount
            elif is_open:
                current.entry_fee += amount
            else:
                pending_entry_fee += amount
        elif kind == EventType.POSITION_OPEN:
            current = _Round(side="LONG" if _d(event["signed_qty"]) > 0 else "SHORT",
                             entry_fee=pending_entry_fee, entry_slippage=pending_entry_slippage)
            pending_entry_fee = pending_entry_slippage = ZERO
            is_open = True
        elif kind == EventType.FUNDING and is_open:
            current.funding += _d(event["amount_paid"])
        elif kind == EventType.LIQUIDATION and is_open:
            current.liquidated = True
        elif kind in (EventType.POSITION_REDUCE, EventType.POSITION_CLOSE) and is_open:
            current.gross_realized += _d(event["gross_pnl"])
            current.exits += 1
            if kind == EventType.POSITION_CLOSE:
                row = current.view()
                row["index"] = len(folded.closed) + 1
                row["closed_ts_ms"] = int(event["ts_ms"])
                folded.closed.append(row)
                current = _Round()
                is_open = False
    folded.open = current if is_open else None
    return folded


def _segment(engine: PaperEngine) -> dict[str, Decimal]:
    """Since the last capital reset. The account's own anchors, subtraction only."""
    account = engine.account
    return {
        "realized_pnl": account.realized_pnl - account.realized_at_anchor,
        # The D4 invariant: current segment net PnL == wallet - capital base.
        "net_pnl": account.wallet_balance - account.capital_base_usdt,
    }


def expected_close(engine: PaperEngine) -> dict[str, Any]:
    """Send the full CLOSE to a clone on the current quote and report what it did.

    Imported lazily from `sizing` because that is where the clone is defined; one clone recipe,
    not two.
    """
    from .sizing import _clone

    position = engine.account.position
    quote = engine.quote
    clone = _clone(engine)
    try:
        clone.submit_order(ts_ms=quote.ts_ms, side=position.side, qty=position.abs_qty,
                           intent="CLOSE", request_id=f"preview-close-{quote.ts_ms}",
                           reason=PREVIEW_REASON)
    except (OrderRejected, NoLiquidity, ValueError) as exc:
        code = exc.code if isinstance(exc, OrderRejected) else (
            "NO_LIQUIDITY" if isinstance(exc, NoLiquidity) else "INVALID_ORDER")
        return {"feasible": False, "reject_code": code, "reject_message": str(exc)}

    events = clone.ledger.events
    fill = next(event for event in events if event["event_type"] == EventType.FILL)
    fee = next(event for event in events if event["event_type"] == EventType.FEE)
    close = next(event for event in events if event["event_type"] == EventType.POSITION_CLOSE)
    return {
        "feasible": True,
        "fill_price": _d(fill["fill_price"]),
        "reference_price": _d(fill["reference_price"]),
        "levels_consumed": fill["levels_consumed"],
        "fee": _d(fee["amount"]),
        "gross_pnl": _d(close["gross_pnl"]),
        "wallet_after": clone.account.wallet_balance,
    }


def open_position_preview(engine: PaperEngine, *,
                          events: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Everything the position panel needs, or `position_open: False`.

    `events` defaults to the engine's own ledger. A clone that was advanced to a newer quote has
    an empty in-memory ledger of its own, so its caller passes the live ledger followed by
    whatever the clone recorded (a funding settlement, say) to keep the confirmed costs whole.
    """
    quote = engine.quote
    account = engine.account
    position = account.position
    if quote is None or position.is_flat:
        return {"position_open": False}

    mark = quote.mark_price
    signed_qty = position.signed_qty
    unrealized = account.unrealized_pnl(mark)
    accrued = fold(engine.ledger.events if events is None else events).open or _Round()
    segment = _segment(engine)
    close = expected_close(engine)

    preview: dict[str, Any] = {
        "position_open": True,
        "side": position.side,
        "qty": position.abs_qty,
        "leverage": position.leverage,
        "quote_ts_ms": quote.ts_ms,
        "mark_price": mark,
        # Price PnL on the valuation the whole account uses (contract S3/P2).
        "unrealized_pnl": unrealized,
        # As a fraction of the margin backing the position (isolated), for the headline figure.
        "unrealized_pct_of_margin": (unrealized / account.used_margin
                                     if account.used_margin > 0 else None),
        # Already charged, from the ledger.
        "entry_fee": accrued.entry_fee,
        "partial_exit_fee": accrued.exit_fee,
        "partial_realized_pnl": accrued.gross_realized,
        "funding": accrued.funding,
        "funding_pnl": -accrued.funding,
        "entry_slippage": accrued.entry_slippage,
        # Current segment, from the account's anchors.
        "segment_realized_pnl": segment["realized_pnl"],
        "segment_net_pnl": segment["net_pnl"],
        "close_feasible": close["feasible"],
        "close_reject_code": close.get("reject_code"),
        "close_reject_message": close.get("reject_message"),
        "expected_close_fill_price": None,
        "expected_close_reference_price": None,
        "expected_close_fee": None,
        "expected_close_slippage": None,
        "expected_close_slippage_pnl": None,
        "expected_close_spread_cost": None,
        "expected_close_depth_cost": None,
        "expected_segment_net_if_closed": None,
        "expected_position_net_if_closed": None,
    }
    if not close["feasible"]:
        return preview

    # Split of a gap the engine produced: mark-valued PnL minus the fill-valued PnL of the close.
    slippage = unrealized - close["gross_pnl"]
    spread_cost = (mark - close["reference_price"]) * signed_qty
    preview.update({
        "expected_close_fill_price": close["fill_price"],
        "expected_close_reference_price": close["reference_price"],
        "expected_close_levels_consumed": close["levels_consumed"],
        "expected_close_fee": close["fee"],
        "expected_close_slippage": slippage,
        "expected_close_slippage_pnl": -slippage,
        "expected_close_spread_cost": spread_cost,
        "expected_close_depth_cost": slippage - spread_cost,
        # The clone's wallet after the close, measured from the segment's capital line. This is
        # the engine's answer; the formula in the UI brief is the identity it satisfies.
        "expected_segment_net_if_closed": close["wallet_after"] - account.capital_base_usdt,
        # The same question for this position alone, from its own confirmed costs.
        "expected_position_net_if_closed": (accrued.gross_realized + close["gross_pnl"]
                                            - accrued.entry_fee - accrued.exit_fee
                                            - close["fee"] - accrued.funding),
    })
    return preview


def closed_trade_breakdowns(events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per closed round trip, indexed like `analytics.build_trades`."""
    return fold(events).closed


# Money fields a display may want in KRW. Prices and quantities are deliberately absent.
KRW_FIELDS = (
    # Pre-trade round trip (trade_preview). A price move per coin is not money and is absent.
    "entry_slippage", "exit_slippage", "entry_slippage_pnl", "exit_slippage_pnl",
    "round_trip_cost", "immediate_round_trip_net", "notional", "required_margin",
    "unrealized_pnl", "entry_fee", "partial_exit_fee", "partial_realized_pnl", "funding",
    "funding_pnl", "segment_net_pnl", "expected_close_fee", "expected_close_slippage",
    "expected_close_slippage_pnl", "expected_segment_net_if_closed",
    "expected_position_net_if_closed", "gross_realized_pnl", "exit_fee", "slippage",
    "slippage_pnl", "net_realized_pnl",
)


def to_krw(row: dict[str, Any], krw_per_usdt: Decimal) -> dict[str, Any]:
    """The run's fixed FX rate applied to the money fields present. None stays None."""
    return {key: (row[key] * krw_per_usdt if row[key] is not None else None)
            for key in KRW_FIELDS if key in row}
