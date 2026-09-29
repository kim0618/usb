"""How large an entry the account can actually take, answered by the engine itself.

There is no margin formula in this file, and there must never be one. The affordable size is
not something you can invert in closed form anyway: a market order walks the book, so the fill
price - and therefore the margin and the fee - depend on the quantity being sized. Any closed
form would have to assume a single price and would quietly overstate MAX in exactly the thin
book where overstating it is most expensive.

So instead of deriving, this probes. It copies the account onto a throwaway engine whose ledger
has no file behind it, submits the order there, and reports what the real engine would have
done. Every number it returns - fill price, fee, reserved margin, liquidation price - is read
off that settled clone, so the panel cannot drift from the engine's own arithmetic.

The clone shares `config` and `tiers` with the live engine. Both are read-only value objects,
and sharing them is deliberate: copying them would create a second copy of the fee schedule and
the risk tiers, which is the very duplication this module exists to avoid.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .account import SIDE_LONG, SIDE_SHORT
from .book import NoLiquidity
from .engine import OrderRejected, PaperEngine
from .ledger import Ledger

PROBE_REASON = "SIZING_PROBE"
DEFAULT_FRACTIONS = (Decimal("0.25"), Decimal("0.50"), Decimal("0.75"), Decimal("1.00"))


@dataclass(frozen=True)
class SizeQuote:
    """One candidate entry, priced by the engine, and checked that it can be closed again.

    `feasible` means both halves passed. A size you can get into but not out of is not a size
    the screen should offer: the book is one-sided often enough that an entry filled on a deep
    ask can face a bid that cannot absorb it a second later.
    """
    label: str
    fraction: Decimal
    qty: Decimal
    feasible: bool
    fill_price: Decimal | None = None
    notional: Decimal | None = None
    fee: Decimal | None = None
    reserved_margin: Decimal | None = None
    required_total: Decimal | None = None
    liquidation_price: Decimal | None = None
    margin_ratio: Decimal | None = None
    risk_tier: int | None = None
    resulting_qty: Decimal | None = None
    resulting_avg_entry: Decimal | None = None
    available_after: Decimal | None = None
    reject_code: str | None = None
    reject_message: str | None = None
    # The exit half, tested against the same snapshot the entry was priced on.
    entry_feasible: bool = False
    exit_feasible: bool = False
    exit_fill_price: Decimal | None = None
    exit_reject_code: str | None = None
    exit_reject_message: str | None = None
    # Provenance of the snapshot this was priced on, so a preview can be told from a fill.
    quote_ts_ms: int | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    mark_price: Decimal | None = None
    bid_depth: Decimal | None = None
    ask_depth: Decimal | None = None

    def view(self) -> dict[str, Any]:
        return {
            "label": self.label, "fraction": self.fraction, "qty": self.qty,
            "feasible": self.feasible, "fill_price": self.fill_price, "notional": self.notional,
            "fee": self.fee, "reserved_margin": self.reserved_margin,
            "required_total": self.required_total, "liquidation_price": self.liquidation_price,
            "margin_ratio": self.margin_ratio, "risk_tier": self.risk_tier,
            "resulting_qty": self.resulting_qty, "resulting_avg_entry": self.resulting_avg_entry,
            "available_after": self.available_after,
            "reject_code": self.reject_code, "reject_message": self.reject_message,
            "entry_feasible": self.entry_feasible, "exit_feasible": self.exit_feasible,
            "exit_fill_price": self.exit_fill_price,
            "exit_reject_code": self.exit_reject_code,
            "exit_reject_message": self.exit_reject_message,
            "quote_ts_ms": self.quote_ts_ms, "best_bid": self.best_bid,
            "best_ask": self.best_ask, "mark_price": self.mark_price,
            "bid_depth": self.bid_depth, "ask_depth": self.ask_depth,
        }


def _clone(engine: PaperEngine) -> PaperEngine:
    """A throwaway engine holding a copy of the mutable state and nothing durable.

    `Ledger()` with no path is in-memory only, the same construction recovery uses to replay a
    tape without writing. The live ledger is never copied: it can hold tens of thousands of
    events, and a probe has no business carrying them around.
    """
    clone = PaperEngine(engine.config, engine.tiers, ledger=Ledger())
    clone.account = copy.deepcopy(engine.account)
    clone.state = copy.deepcopy(engine.state)
    clone.leverage = engine.leverage
    clone.quote = engine.quote
    clone.last_market_ts_ms = engine.last_market_ts_ms
    clone.liquidation_count = engine.liquidation_count
    clone.funding_grid_mismatches = engine.funding_grid_mismatches
    clone._started = True
    return clone


def floor_to_step(qty: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise ValueError("qty step must be positive")
    return (qty / step).to_integral_value(rounding="ROUND_DOWN") * step


def _snapshot(quote: Any) -> dict[str, Any]:
    """Which book this size was priced on, so a preview can never be mistaken for a fill."""
    return {"quote_ts_ms": quote.ts_ms, "best_bid": quote.best_bid, "best_ask": quote.best_ask,
            "mark_price": quote.mark_price, "bid_depth": quote.bids.total_qty,
            "ask_depth": quote.asks.total_qty}


def _classify(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, OrderRejected):
        return exc.code, str(exc)
    if isinstance(exc, NoLiquidity):
        return "NO_LIQUIDITY", str(exc)
    return "INVALID_ORDER", str(exc)


def probe(engine: PaperEngine, side: str, qty: Decimal, *, label: str = "",
          fraction: Decimal = Decimal(0), require_exit: bool = True) -> SizeQuote:
    """Price one candidate entry on a clone, then try to close it on the same snapshot.

    The exit half is the point. A market order consumes the opposite side of the book, and the
    two sides are routinely lopsided: an entry that fills comfortably on a deep ask can face a
    bid that cannot absorb it at all. Sizing that only asked "can I get in" handed the operator
    positions the engine would then refuse to close, which is exactly what happened in
    production at 1.082 BTC against 0.157 BTC of bid.

    Closing is tested against the *same* quote, not a later one. That is deliberately the
    friendliest case: if it fails even here, it will not pass a second later either.
    """
    if side not in (SIDE_LONG, SIDE_SHORT):
        raise ValueError(f"unknown side {side}")
    quote = engine.quote
    if quote is None:
        return SizeQuote(label=label, fraction=fraction, qty=qty, feasible=False,
                         reject_code="NO_QUOTE", reject_message="no market quote has been observed yet")
    if qty <= 0:
        return SizeQuote(label=label, fraction=fraction, qty=qty, feasible=False,
                         reject_code="QTY_NOT_POSITIVE", reject_message="quantity must be positive",
                         **_snapshot(quote))

    before_margin = engine.account.used_margin
    before_fees = engine.account.cumulative_fees
    clone = _clone(engine)
    try:
        clone.submit_order(ts_ms=quote.ts_ms, side=side, qty=qty, intent="OPEN",
                           request_id=f"probe-{quote.ts_ms}", reason=PROBE_REASON)
    except (OrderRejected, NoLiquidity, ValueError) as exc:
        code, message = _classify(exc)
        return SizeQuote(label=label, fraction=fraction, qty=qty, feasible=False,
                         reject_code=code, reject_message=message, **_snapshot(quote))

    fill = next((event for event in clone.ledger.events if event["event_type"] == "FILL"), None)
    account = clone.account.view(clone.tiers, quote.mark_price)
    fee = clone.account.cumulative_fees - before_fees
    reserved = clone.account.used_margin - before_margin

    exit_ok, exit_price, exit_code, exit_message = True, None, None, None
    if require_exit:
        # Same clone, same quote: the position now exists, so this is the real close path.
        held = clone.account.position.abs_qty
        exit_clone = _clone(clone)
        try:
            exit_clone.submit_order(ts_ms=quote.ts_ms, side=side, qty=held, intent="CLOSE",
                                    request_id=f"probe-exit-{quote.ts_ms}", reason=PROBE_REASON)
            closing = [event for event in exit_clone.ledger.events
                       if event["event_type"] == "FILL"]
            exit_price = Decimal(closing[-1]["fill_price"]) if closing else None
        except (OrderRejected, NoLiquidity, ValueError) as exc:
            exit_ok = False
            exit_code, exit_message = _classify(exc)

    return SizeQuote(
        label=label, fraction=fraction, qty=qty, feasible=exit_ok, entry_feasible=True,
        exit_feasible=exit_ok, exit_fill_price=exit_price, exit_reject_code=exit_code,
        exit_reject_message=exit_message,
        reject_code=None if exit_ok else exit_code,
        reject_message=None if exit_ok else exit_message,
        fill_price=Decimal(fill["fill_price"]) if fill else None,
        notional=Decimal(fill["notional"]) if fill else None,
        fee=fee, reserved_margin=reserved, required_total=reserved + fee,
        liquidation_price=account["liquidation_price"], margin_ratio=account["margin_ratio"],
        risk_tier=account["risk_tier"], resulting_qty=account["position_qty"],
        resulting_avg_entry=account["avg_entry"], available_after=account["available_balance"],
        **_snapshot(quote))


def max_entry(engine: PaperEngine, side: str) -> SizeQuote:
    """The largest grid quantity that can be both opened and closed on this snapshot.

    Not "the largest order that fills". That was the old meaning and it produced positions the
    engine would then refuse to close, because the exit consumes the other side of the book and
    the two sides are not the same size.

    Binary search is still sound: every reason either half is refused gets worse with size.
    Margin, notional against the risk limit, entry depth and exit depth all move one way, so
    their conjunction does too. The search never has to model *why* a size failed, only that
    it did.
    """
    spec = engine.config.instrument
    step = spec.qty_step
    smallest = probe(engine, side, spec.min_order_qty, label="MAX", fraction=Decimal(1))
    if not smallest.feasible:
        # Nothing is affordable. Carry the engine's own reason rather than inventing one.
        return SizeQuote(label="MAX", fraction=Decimal(1), qty=Decimal(0), feasible=False,
                         reject_code=smallest.reject_code, reject_message=smallest.reject_message)

    low = spec.min_order_qty
    high = floor_to_step(spec.max_mkt_order_qty, step)
    best = smallest
    while low < high:
        # Bias upward so the loop cannot stall on `low` when the two are one step apart.
        middle = floor_to_step(low + (high - low) / 2 + step, step)
        if middle > high:
            middle = high
        candidate = probe(engine, side, middle, label="MAX", fraction=Decimal(1))
        if candidate.feasible:
            best, low = candidate, middle
        else:
            high = middle - step
    return best


def presets(engine: PaperEngine, side: str,
            fractions: tuple[Decimal, ...] = DEFAULT_FRACTIONS) -> dict[str, Any]:
    """MAX plus each requested fraction of it, every one priced by its own probe.

    A fraction is applied to the quantity and then re-probed rather than having its cost scaled
    down from MAX: half the size is not half the fill price once the book is being walked, and
    the panel should show what would actually happen. The re-probe is two-sided as well, so a
    smaller preset is not assumed safe just because it is smaller than a safe MAX.
    """
    spec = engine.config.instrument
    ceiling = max_entry(engine, side)
    labels = {Decimal("0.25"): "25%", Decimal("0.50"): "HALF", Decimal("0.75"): "75%",
              Decimal("1.00"): "MAX"}
    quotes: list[SizeQuote] = []
    for fraction in fractions:
        label = labels.get(fraction, f"{fraction * 100:.0f}%")
        if not ceiling.feasible:
            quotes.append(SizeQuote(label=label, fraction=fraction, qty=Decimal(0), feasible=False,
                                    reject_code=ceiling.reject_code,
                                    reject_message=ceiling.reject_message))
            continue
        qty = ceiling.qty if fraction == 1 else floor_to_step(ceiling.qty * fraction, spec.qty_step)
        quotes.append(probe(engine, side, qty, label=label, fraction=fraction))

    quote = engine.quote
    return {
        "side": side,
        "leverage": engine.leverage,
        "max_qty": ceiling.qty if ceiling.feasible else Decimal(0),
        "max_feasible": ceiling.feasible,
        "reject_code": ceiling.reject_code,
        "reject_message": ceiling.reject_message,
        # What MAX now means, carried with the answer so a reader does not have to remember.
        "max_definition": "ENTRY_AND_IMMEDIATE_EXIT_ON_THIS_SNAPSHOT",
        "quote_ts_ms": quote.ts_ms if quote is not None else None,
        "best_bid": quote.best_bid if quote is not None else None,
        "best_ask": quote.best_ask if quote is not None else None,
        "mark_price": quote.mark_price if quote is not None else None,
        "entry_depth": (quote.asks.total_qty if side == SIDE_LONG else quote.bids.total_qty)
                       if quote is not None else None,
        "exit_depth": (quote.bids.total_qty if side == SIDE_LONG else quote.asks.total_qty)
                      if quote is not None else None,
        "instrument": {
            "qty_step": spec.qty_step, "min_order_qty": spec.min_order_qty,
            "min_notional_value": spec.min_notional_value,
            "max_mkt_order_qty": spec.max_mkt_order_qty,
        },
        "presets": [quote.view() for quote in quotes],
    }
