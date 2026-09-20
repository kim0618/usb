"""Point-in-time audit for E1: two boundaries, tested two different ways.

**The decision-time boundary.** Every premarket feature must be a function of bars starting at or
before 09:24 ET. The audit replaces every bar from 09:25 onward - the 09:25..09:29 bars and the
whole regular session, the labels' own data included - with large, structurally different noise,
rebuilds the premarket block, and requires every feature to be identical bit for bit. A feature
that peeked at the open, or even at the 09:25 bar, moves; one that respects the boundary cannot.
The poison keeps values positive and finite so that a session's eligibility does not change.

**The previous-session boundary.** Every daily input must come from D-1, never from D. Rather
than poison the panel, the audit reads back which daily row each E1 row actually used and
requires its session to be the previous XNYS grid session of D. That is an exact statement about
the join, and it catches an off-by-one that a noise test could only catch probabilistically.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight.minute import SymbolTape
from app.backtest.strategy_e1_premarket import premarket as P

POISON_FROM_MINUTE = P.DECISION_LAST_BAR + 1     # 09:25 ET


def poison_tape(tape: SymbolTape, seed: int) -> SymbolTape:
    """A copy whose bars from 09:25 ET onward carry noise instead of prices."""
    rng = np.random.default_rng(seed)
    target = tape.minute >= POISON_FROM_MINUTE
    fields = {}
    for name in ("open", "high", "low", "close", "volume", "vwap"):
        array = getattr(tape, name).copy()
        noise = rng.uniform(1.0, 1000.0, size=int(target.sum())) * 17.0
        values = array[target]
        # Positive, finite, and missing-preserving, so the session's eligibility is untouched
        # and only the *information* in those bars is destroyed.
        array[target] = np.where(np.isfinite(values), noise, values)
        fields[name] = array
    return SymbolTape(symbol=tape.symbol, et_day=tape.et_day.copy(), minute=tape.minute.copy(),
                      sources=dict(tape.sources), overlap_sessions=tape.overlap_sessions, **fields)


def decision_boundary(tapes: Sequence[SymbolTape], *, seed: int = 20260920) -> dict[str, Any]:
    """Rebuild the premarket block on poisoned tapes and compare feature by feature."""
    mismatches: dict[str, int] = {}
    compared = 0
    symbols = 0
    for tape in tapes:
        clean = P.session_rows(tape)
        dirty = P.session_rows(poison_tape(tape, seed + symbols))
        symbols += 1
        shared = sorted(set(clean) & set(dirty))
        for key in shared:
            compared += 1
            a, b = clean[key], dirty[key]
            fields = dict(a.premarket)
            fields.update(P.derived_features(a.premarket))
            fields["pm_rvol"] = a.pm_rvol
            other = dict(b.premarket)
            other.update(P.derived_features(b.premarket))
            other["pm_rvol"] = b.pm_rvol
            for name, value in fields.items():
                reference = other.get(name, float("nan"))
                same = (value == reference) or (not np.isfinite(value)
                                                and not np.isfinite(reference))
                if not same:
                    mismatches[name] = mismatches.get(name, 0) + 1
        if len(clean) != len(dirty):
            mismatches["session_count"] = mismatches.get("session_count", 0) + 1
    return {"check": "decision_time_boundary_0924",
            "symbols": symbols, "sessions_compared": compared,
            "mismatched_features": mismatches,
            "verdict": "PASS" if not mismatches else "FAIL"}


def join_boundary(sessions: np.ndarray, daily_sessions: Sequence[date],
                  used_session_idx: np.ndarray, grid: Sequence[date]) -> dict[str, Any]:
    """Every E1 row must have used the daily row of the previous grid session."""
    index_of = {s: i for i, s in enumerate(grid)}
    used = np.array([daily_sessions[i] for i in used_session_idx], dtype=object)
    wrong = 0
    examples: list[list[str]] = []
    for session, actual in zip(sessions, used):
        position = index_of.get(session)
        expected = grid[position - 1] if position not in (None, 0) else None
        if expected is None or actual != expected:
            wrong += 1
            if len(examples) < 5:
                examples.append([str(session), str(actual), str(expected)])
    return {"check": "daily_input_is_previous_session",
            "rows_checked": int(sessions.size), "wrong": wrong, "examples": examples,
            "verdict": "PASS" if wrong == 0 else "FAIL"}
