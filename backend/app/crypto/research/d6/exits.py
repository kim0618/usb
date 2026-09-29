"""Exit intent for a position that is handed in, not held.

The evaluator answers one question: given this position and this price, does the contract say to
be out? It computes no PnL, touches no ledger and produces no close order.

Two rules exist, in this order:

  X2 volatility stop   mark at or below the stop price
  X1 time exit         240 minutes held

The order matters. When a single bar touches the stop and also runs out the clock, the contract's
`same_bar_rule` says the stop wins, because a 1m bar does not say whether the low came before or
after the close, and the unfavourable reading is the honest one.

An emergency stop does *not* force an exit. The contract keeps holdings on the X1/X2 rules and
stops new entries instead, so `emergency_stop` is a filter concern, not an exit concern.
"""
from __future__ import annotations

import math

from .contract import Contract
from .model import EXIT_INTENT, ExitEvaluation, HOLD_POSITION, VirtualPosition

TIME_EXIT = "X1_TIME_EXIT"
VOLATILITY_STOP = "X2_VOLATILITY_STOP"
HOLDING = "HOLDING"
MARK_UNKNOWN = "MARK_UNKNOWN"


def evaluate(contract: Contract, position: VirtualPosition, *, now_ms: int,
             mark: float | None = None, mark_low: float | None = None) -> ExitEvaluation:
    """`mark_low` is the bar's lowest mark when replaying bars; `mark` alone is the realtime tick."""
    held_ms = now_ms - position.entry_ts_ms
    held_minutes = held_ms / 60_000
    max_hold_ms = contract.max_hold_min * 60_000
    detail = {"stop_price": position.stop_price, "mark": mark, "mark_low": mark_low,
              "max_hold_min": contract.max_hold_min}

    worst = None
    for candidate in (mark_low, mark):
        if candidate is not None and math.isfinite(candidate):
            worst = candidate if worst is None else min(worst, candidate)

    if worst is None:
        # No price to judge the stop with. Time is still knowable, but claiming HOLD_POSITION
        # while blind to the stop would be a false negative, so say so.
        if held_ms >= max_hold_ms:
            return ExitEvaluation(EXIT_INTENT, TIME_EXIT, held_minutes, detail)
        return ExitEvaluation(HOLD_POSITION, MARK_UNKNOWN, held_minutes, detail)

    if worst <= position.stop_price:
        return ExitEvaluation(EXIT_INTENT, VOLATILITY_STOP, held_minutes,
                              {**detail, "triggered_on": worst})

    if held_ms >= max_hold_ms:
        return ExitEvaluation(EXIT_INTENT, TIME_EXIT, held_minutes, detail)

    return ExitEvaluation(HOLD_POSITION, HOLDING, held_minutes, detail)
