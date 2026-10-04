"""Band depth and imbalance, with coverage that refuses to guess.

The rule this module exists to enforce: **a quantity we could not observe is null, never zero.**
Zero is a real answer (a band with nothing resting in it) and it has to stay distinguishable from
"the book does not reach that far" and from "there is no book right now". So every side of every
band carries one of three coverage states:

* `COMPLETE` - the book is synchronized, fresh, and the entire side-band lies inside the interval
  the snapshot established. The canonical `qty`/`notional` are the answer.
* `PARTIAL` - the book is synchronized and fresh, but the band runs past the snapshot's outer
  bound. Canonical values are null; `observed_qty`/`observed_notional` give the lower bound that
  was actually seen, which is useful and is labelled as a lower bound.
* `UNKNOWN` - no usable book: unsynchronized, stale, or no valid mid. Everything is null.

With a `limit=1000` snapshot this is not a corner case. Measured 2026-10-04, the snapshot's outer
bounds sat at -0.1507% and +0.1499% of mid, so the +-0.1% band was COMPLETE and the +-0.25%,
+-0.5% and +-1% bands were PARTIAL. Reporting three PARTIAL bands is the honest result at this
depth, and it is the finding that decides what Liquidity Map V1 can ask for.

Imbalance is computed only when **both** sides of that band are COMPLETE, because a ratio built
from one complete and one truncated side is not an imbalance, it is an artefact of where the
snapshot ended. A zero denominator gives null rather than zero, for the same reason as above.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .contract import (BANDS, COMPLETE, DEPTH_STALE_MS, PARTIAL, RATIO_DECIMALS, UNKNOWN)
from .envelope import decimal_out, ratio_out
from . import book as B


def _side_totals(levels: dict[Decimal, Decimal], low: Decimal, high: Decimal) -> tuple[Decimal, Decimal, int]:
    """Quantity, notional and level count for the prices inside `[low, high]`."""
    qty = Decimal(0)
    notional = Decimal(0)
    count = 0
    for price, size in levels.items():
        if low <= price <= high:
            qty += size
            notional += price * size
            count += 1
    return qty, notional, count


def _side_view(coverage: str, qty: Decimal, notional: Decimal, levels: int,
               low: Decimal, high: Decimal) -> dict[str, Any]:
    """One side of one band. Canonical values exist only when coverage is COMPLETE."""
    canonical = coverage == COMPLETE
    observed = coverage in (COMPLETE, PARTIAL)
    return {
        "coverage": coverage,
        "qty": decimal_out(qty) if canonical else None,
        "notional": decimal_out(notional) if canonical else None,
        "observed_qty": decimal_out(qty) if observed else None,
        "observed_notional": decimal_out(notional) if observed else None,
        "observed_is_lower_bound": coverage == PARTIAL,
        "levels": levels if observed else None,
        "price_low": decimal_out(low) if observed else None,
        "price_high": decimal_out(high) if observed else None,
    }


def _unknown_side() -> dict[str, Any]:
    return {
        "coverage": UNKNOWN, "qty": None, "notional": None, "observed_qty": None,
        "observed_notional": None, "observed_is_lower_bound": False, "levels": None,
        "price_low": None, "price_high": None,
    }


def band_view(depth: B.DepthBook, *, at_ns: int, stale_ms: int = DEPTH_STALE_MS) -> dict[str, Any]:
    """The whole `derived.book` payload: state, mid, source ids, ages and every band."""
    age_ms = depth.age_ms(at_ns)
    mid = depth.mid()
    fresh = depth.state == B.SYNCED and age_ms is not None and age_ms <= stale_ms
    usable = fresh and mid is not None

    state = depth.state
    if depth.state == B.SYNCED and not fresh:
        state = B.STALE
    elif depth.state == B.SYNCED and mid is None:
        # Synchronized and fresh, but the best bid/ask no longer sit inside the known interval.
        state = B.CROSSED_BOOK

    bands: list[dict[str, Any]] = []
    for label, fraction in BANDS:
        if not usable:
            bands.append({"band_pct": label, "bid": _unknown_side(), "ask": _unknown_side(),
                          "imbalance_btc": None, "imbalance_usdt": None})
            continue
        assert mid is not None and depth.known_low is not None and depth.known_high is not None
        bid_low, bid_high = mid * (Decimal(1) - fraction), mid
        ask_low, ask_high = mid, mid * (Decimal(1) + fraction)

        bid_complete = bid_low >= depth.known_low
        ask_complete = ask_high <= depth.known_high
        # Only the part of the band we can see is summed, even when reporting a lower bound.
        bid_qty, bid_notional, bid_levels = _side_totals(
            depth.bids, max(bid_low, depth.known_low), min(bid_high, depth.known_high))
        ask_qty, ask_notional, ask_levels = _side_totals(
            depth.asks, max(ask_low, depth.known_low), min(ask_high, depth.known_high))

        bid = _side_view(COMPLETE if bid_complete else PARTIAL, bid_qty, bid_notional,
                         bid_levels, bid_low, bid_high)
        ask = _side_view(COMPLETE if ask_complete else PARTIAL, ask_qty, ask_notional,
                         ask_levels, ask_low, ask_high)
        both_complete = bid_complete and ask_complete
        bands.append({
            "band_pct": label,
            "bid": bid,
            "ask": ask,
            "imbalance_btc": ratio_out(bid_qty - ask_qty, bid_qty + ask_qty, RATIO_DECIMALS)
            if both_complete else None,
            "imbalance_usdt": ratio_out(bid_notional - ask_notional, bid_notional + ask_notional,
                                        RATIO_DECIMALS) if both_complete else None,
        })

    lag_ms = depth.lag_ms()
    return {
        "state": state,
        "generation": depth.generation,
        "mid": decimal_out(mid),
        "best_bid": decimal_out(depth.best_bid()),
        "best_ask": decimal_out(depth.best_ask()),
        "source_u": depth.last_update_id,
        "snapshot_update_id": depth.snapshot_update_id,
        "known_low": decimal_out(depth.known_low),
        "known_high": decimal_out(depth.known_high),
        "age_ms": age_ms,
        "fresh": fresh,
        "event_ms": depth.last_event_ms,
        "receive_ms": depth.last_receive_ms,
        "lag_ms": lag_ms,
        "lag_state": lag_state(lag_ms),
        "levels": depth.level_count(),
        "last_invalidation": depth.last_invalidation,
        "bands": bands,
    }


def lag_state(lag_ms: int | None) -> str:
    """Exchange lag is a quality signal, and an implausible one is UNKNOWN rather than a number."""
    from .contract import CLOCK_SKEW_TOLERANCE_MS, LAG_UNKNOWN_MS
    if lag_ms is None:
        return UNKNOWN
    if lag_ms > LAG_UNKNOWN_MS or lag_ms < -CLOCK_SKEW_TOLERANCE_MS:
        return UNKNOWN
    return COMPLETE


__all__ = ["band_view", "lag_state"]
