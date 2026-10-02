"""C1x: the premium-normalization diagnostic, evaluated forward on live candles.

One per C1 signal, at most. It marks the moment the extreme discount C1 entered on has come
back to normal, and it is **not an exit**: nothing here produces an order, a close, a stop or an
Auto Exit setting, and the hypothetical numbers it records are labelled hypothetical throughout.

The rule is E2's, transcribed from `research/c1_exit/e2.py`:

    walking 1m bars from the entry bar e, on 5m evaluation bars only
    (a bar whose close lands on a 5m boundary), the S1 bucket must be B3 or higher
    on two consecutive evaluations. A false bar or a missing input resets the run to zero.
    The trigger bar is the second confirmation; the executable price is the next open.
    No trigger within 8 h means censored, and then the diagnostic simply never fires.

Deliberately a pure function of (signal, grid). The alternative - carrying a confirmation counter
across ticks - would have to be reconstructed after a restart and could drift from the contract;
recomputing from the candles cannot. Once a trigger has been written down the runtime stops
recomputing it, so no later data can move a timestamp that has already been published.
"""
from __future__ import annotations

from typing import Callable, Sequence

from .contract import (
    C1X_CONFIRMATIONS_REQUIRED, C1X_EVAL_CADENCE_MIN, C1X_MAX_HOLD_MIN, C1X_MIN_BUCKET,
    MICRO_BASE_FRAC, MINUTE_MS, TAKER_RATE,
)
from .features import NAN, bucket_index, is_nan
from .grid import Grid
from .models import C1xEvent, Signal, ShadowTrade
from .shadow import funding_paid

FIVE_MIN_MS = C1X_EVAL_CADENCE_MIN * MINUTE_MS
CutoffLookup = Callable[[Grid, int], "tuple[float, ...] | None"]


def is_evaluation_bar(bar_open_ms: int) -> bool:
    """E2 section 2: the 5m evaluation bars are the ones whose close is a 5m boundary.

    Transcribed from `e2.py`'s `eval5 = ((ts + MIN) % FIVE) == 0`. It is the bar's *close* that
    has to land on the boundary, because that is when the decision is taken.
    """
    return (bar_open_ms + MINUTE_MS) % FIVE_MIN_MS == 0


def evaluate(signal: Signal, grid: Grid, cutoffs: CutoffLookup,
             schedule: Sequence[tuple[int, float]], *,
             now_ms: int | None = None) -> C1xEvent:
    """The diagnostic's state for one signal, recomputed from the candles.

    Returns a NOT_TRIGGERED / CONFIRM_1 / TRIGGERED / EXPIRED_MAX_HOLD record. Which of the two
    unfinished states is reported is the run length at the last bar actually available, so the
    screen can show that a confirmation is pending rather than only the outcome.
    """
    event = C1xEvent(signal_id=signal.signal_id, status="NOT_TRIGGERED")
    entry_index = grid.index_of(signal.official_entry_at_ms)
    if entry_index < 0 or not grid.has_bar(entry_index):
        return event
    entry_price = grid.opens[entry_index]
    event.entry_at_ms = signal.official_entry_at_ms
    event.entry_price = entry_price

    run = 0
    last_seen = -1
    for offset in range(C1X_MAX_HOLD_MIN):
        index = entry_index + offset
        if index >= len(grid):
            break
        bar_ms = grid.ts(index)
        if not is_evaluation_bar(bar_ms):
            continue
        last_seen = index
        value = grid.s1[index]
        bucket = bucket_index(value, cutoffs(grid, index))
        # A missing input is not a passing bar and not a failing one either: E2 resets the run,
        # which is the same thing a false bar does, and never carries a stale premium forward.
        run = run + 1 if bucket >= C1X_MIN_BUCKET else 0
        if run >= C1X_CONFIRMATIONS_REQUIRED:
            return _trigger(event, signal, grid, schedule, index, value, bucket,
                            cutoffs(grid, index), entry_index, entry_price)
    # No trigger yet. Either the window is exhausted - censored, the diagnostic never fires - or
    # there are simply no more bars to look at.
    exhausted = entry_index + C1X_MAX_HOLD_MIN <= len(grid)
    event.confirmation_count = run
    event.status = "EXPIRED_MAX_HOLD" if exhausted else ("CONFIRM_1" if run else "NOT_TRIGGERED")
    event.censored_by_max_hold = exhausted
    if last_seen >= 0:
        event.observed_at_ms = grid.ts(last_seen) + MINUTE_MS
        event.premium_value = grid.s1[last_seen]
        edges = cutoffs(grid, last_seen)
        event.premium_boundary = edges[1] if edges else NAN
        event.premium_bucket = bucket_index(grid.s1[last_seen], edges)
    return event


def _trigger(event: C1xEvent, signal: Signal, grid: Grid,
             schedule: Sequence[tuple[int, float]], index: int, value: float, bucket: int,
             edges, entry_index: int, entry_price: float) -> C1xEvent:
    """Fill in the triggered record, including what an exit there would have returned.

    `hypothetical_` is in every name that carries a price or a return, because nothing was
    traded: the operator may have been holding, flat, or out hours earlier.
    """
    exit_index = index + 1
    event.status = "TRIGGERED"
    event.confirmation_count = C1X_CONFIRMATIONS_REQUIRED
    event.triggered_at_ms = grid.ts(index) + MINUTE_MS
    event.observed_at_ms = event.triggered_at_ms
    event.observed_price = grid.closes[index]
    event.premium_value = value
    event.premium_bucket = bucket
    # The boundary the premium had to clear: that day's 30th percentile, which is the B3 edge.
    event.premium_boundary = edges[1] if edges else NAN
    event.censored_by_max_hold = False

    if not grid.has_bar(exit_index):
        # The confirmation is in, but the bar that would price it has not opened yet.
        event.status = "TRIGGERED"
        return event
    event.executable_at_ms = grid.ts(exit_index)
    exit_price = grid.opens[exit_index]
    event.hypothetical_exit_price = exit_price
    event.holding_minutes = exit_index - entry_index
    if entry_price > 0:
        ratio = exit_price / entry_price
        funding = funding_paid(schedule, event.entry_at_ms, event.executable_at_ms)
        event.gross_if_exited = ratio - 1.0
        event.cost_if_exited = TAKER_RATE * (1.0 + ratio) + MICRO_BASE_FRAC + funding
        event.net_if_exited = event.gross_if_exited - event.cost_if_exited
        event.funding_if_exited = funding
    return event


def pair_with_e0(event: C1xEvent, trade: ShadowTrade | None) -> C1xEvent:
    """Attach the fixed 4 h benchmark and the paired difference, once the benchmark exists.

    Until the 4 h window closes the delta is `None` and `e0_status` says `PENDING`: a diagnostic
    compared against an unfinished benchmark would be a comparison with nothing.
    """
    if trade is None or trade.status != "SETTLED" or trade.net_return is None:
        event.e0_status = "PENDING"
        event.e0_net = None
        event.delta_net = None
        return event
    event.e0_status = "SETTLED"
    event.e0_net = trade.net_return
    event.delta_net = (None if event.net_if_exited is None
                       else event.net_if_exited - trade.net_return)
    return event
