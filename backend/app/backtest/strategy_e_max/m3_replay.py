"""E-MAX-M3 replay: B1 = R1 / max 3 at 1.0x; B2 = the same sessions scaled by the breadth rule."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any

import numpy as np

from app.backtest.strategy_e_max import m1_replay as MR
from app.strategy_e_max import breadth


def multipliers(prepared, sessions: Sequence[Mapping[str, Any]]) -> dict[str, Fraction]:
    out = {}
    for s in sessions:
        signal = prepared.seals[date.fromisoformat(s["session"])].signal
        out[s["session"]] = breadth.multiplier(signal.candidate_count, signal.eligible_count)
    return out


def scale_all(b1: Sequence[Mapping[str, Any]], mult: Mapping[str, Fraction]) -> list[dict[str, Any]]:
    return [breadth.scale(s, mult[s["session"]]) for s in b1]


def invariants(b1, b2, mult) -> dict[str, Any]:
    checks = {"same_trade_set": True, "zero_delta_at_1x": True, "exact_1_5x_when_high": True}
    for a, b in zip(b1, b2):
        strip = lambda rows: [{k: v for k, v in r.items() if k != "weight"} for r in rows]
        if strip(a["records"]) != strip(b["records"]) or a["selected_order"] != b["selected_order"]:
            checks["same_trade_set"] = False
        k = mult[a["session"]]
        dk = Decimal(k.numerator) / Decimal(k.denominator)
        for name, value in a["returns"].items():
            if k == 1 and b["returns"][name] != value:
                checks["zero_delta_at_1x"] = False
            if k != 1 and b["returns"][name] != value * dk:
                checks["exact_1_5x_when_high"] = False
    checks["pass"] = all(checks.values())
    return checks


def opportunity(prepared, b1, mult, timeline: Sequence[str]) -> dict[str, Any]:
    by = {s["session"]: s for s in b1}
    rows = {d.isoformat(): prepared.seals[d].signal.eligible_count for d in prepared.frames}
    out = {}
    for name, test in (("FULL_DEVELOPMENT", lambda d: True),
                       ("LEGACY_NARROW", lambda d: d < MR.BROAD_START),
                       ("BROAD_COVERAGE", lambda d: d >= MR.BROAD_START)):
        days = [d for d in timeline if test(d)]
        high = [d for d in days if d in mult and mult[d] != 1]
        out[name] = {
            "sessions": len(days),
            "sessions_universe_ge_100": sum(rows.get(d, 0) >= breadth.MIN_UNIVERSE_ROWS for d in days),
            "high_breadth_sessions": len(high),
            "high_breadth_active_sessions": sum(bool(by[d]["active"]) for d in high),
            "high_breadth_standard_trades": sum(sum(r["standard_pnl"] for r in by[d]["records"]) for d in high),
        }
    return out


def changed_sessions(delta: np.ndarray, timeline: Sequence[str], mult: Mapping[str, Fraction],
                     blocks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def summarise(idx: Sequence[int]) -> dict[str, Any]:
        changed = [i for i in idx if mult.get(timeline[i], Fraction(1)) != 1]
        d = delta[changed] if changed else np.array([])
        return {"exposure_changed_sessions": len(changed),
                "positive_delta": int((d > 0).sum()), "negative_delta": int((d < 0).sum()),
                "zero_delta": int((d == 0).sum()),
                "sum_delta_10bp": float(d.sum()) if d.size else 0.0,
                "mean_delta_all_sessions_10bp": float(delta[list(idx)].mean()) if idx else None}

    all_idx = list(range(len(timeline)))
    out = {"FULL_DEVELOPMENT": summarise(all_idx),
           "LEGACY_NARROW": summarise([i for i in all_idx if timeline[i] < MR.BROAD_START]),
           "BROAD_COVERAGE": summarise([i for i in all_idx if timeline[i] >= MR.BROAD_START])}
    out["blocks"] = [{"block": b["block"], **summarise([i for i in all_idx
                                                        if b["first"] <= timeline[i] <= b["last"]])}
                     for b in blocks]
    return out
