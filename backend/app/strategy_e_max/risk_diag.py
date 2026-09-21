"""M0 risk diagnostics over a session-return series (no gate reads them)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Any

import numpy as np


def _worst(groups: dict[str, list[float]]) -> dict[str, Any]:
    compounded = {k: float(np.prod(1.0 + np.array(v)) - 1.0) for k, v in groups.items()}
    key = min(compounded, key=lambda k: (compounded[k], k))
    return {"period": key, "return": compounded[key]}


def diagnostics(sessions: Sequence[str], returns: Sequence[float]) -> dict[str, Any]:
    r = np.asarray(returns, dtype=float)
    days = [date.fromisoformat(s) for s in sessions]
    worst_i = int(np.argmin(r))
    weeks, months, quarters = defaultdict(list), defaultdict(list), defaultdict(list)
    for d, value in zip(days, r):
        year, week, _ = d.isocalendar()
        weeks[f"{year}-W{week:02d}"].append(value)
        months[d.strftime("%Y-%m")].append(value)
        quarters[f"{d.year}Q{(d.month - 1) // 3 + 1}"].append(value)
    equity = np.cumprod(1.0 + r)
    peak_value, peak_index, longest, longest_span = 1.0, -1, 0, None
    for i, value in enumerate(equity):
        if value >= peak_value:
            if i - peak_index - 1 > longest:
                longest = i - peak_index - 1
                longest_span = (sessions[peak_index + 1] if peak_index + 1 < len(sessions) else None,
                                sessions[i], True)
            peak_value, peak_index = value, i
    open_tail = len(r) - peak_index - 1
    if open_tail > longest:
        longest = open_tail
        longest_span = (sessions[peak_index + 1], sessions[-1], False)
    run = best = 0
    for value in r:
        run = run + 1 if value < 0 else 0
        best = max(best, run)
    return {
        "worst_session": {"session": sessions[worst_i], "return": float(r[worst_i])},
        "worst_week": _worst(weeks), "worst_month": _worst(months), "worst_quarter": _worst(quarters),
        "recovery_duration_sessions": longest,
        "recovery_span": None if longest_span is None else
        {"from": longest_span[0], "to": longest_span[1], "recovered": longest_span[2]},
        "longest_losing_run_sessions": best,
    }
