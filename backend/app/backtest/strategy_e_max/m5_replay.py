"""E-MAX-M5 replay diagnostics: global exposure on top of R1 / max 3 / B2 / X1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from fractions import Fraction
from typing import Any

import numpy as np

from app.strategy_e_max import exposure


def scale_all(x1, g: Fraction) -> list[dict[str, Any]]:
    return [exposure.apply_global(s, g) for s in x1]


def invariants(e1, scaled: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    checks = {"same_trade_set": True, "exact_scaling": True}
    for name, sessions in scaled.items():
        g = exposure.MULTIPLIERS[name]
        dg = Decimal(g.numerator) / Decimal(g.denominator)
        for a, b in zip(e1, sessions):
            strip = lambda rows: [{k: v for k, v in r.items() if k != "weight"} for r in rows]
            if strip(a["records"]) != strip(b["records"]) or a["selected_order"] != b["selected_order"]:
                checks["same_trade_set"] = False
            if any(b["returns"][k] != v * dg for k, v in a["returns"].items()):
                checks["exact_scaling"] = False
    checks["pass"] = all(checks.values())
    return checks


def _mdd_window(series: np.ndarray) -> tuple[int, int]:
    equity = np.cumprod(1.0 + series)
    peak_equity = np.maximum.accumulate(np.concatenate(([1.0], equity)))[1:]
    trough = int(np.argmin(equity / peak_equity - 1.0))
    peaks = np.concatenate(([1.0], equity))[: trough + 2]
    peak = int(np.argmax(peaks)) - 1          # -1 means the starting equity of 1
    return peak, trough


def breadth_interaction(sessions: Sequence[str], series: np.ndarray, replayed) -> dict[str, Any]:
    by = {s["session"]: s for s in replayed}
    high = {d for d in sessions if d in by and Fraction(by[d].get("breadth_multiplier", "1/1")) != 1}

    def summary(days):
        idx = [i for i, d in enumerate(sessions) if d in days]
        active = [d for d in days if d in by and by[d]["active"]]
        return {"sessions": len(idx), "active_sessions": len(active),
                "final_exposure": sorted({by[d]["final_exposure_multiplier"] for d in active}),
                "sum_session_return_10bp": float(series[idx].sum()) if idx else 0.0}

    peak, trough = _mdd_window(series)
    window = list(range(peak + 1, trough + 1))
    return {"normal": summary(set(sessions) - high), "high_breadth": summary(high),
            "mdd_window": {"from": sessions[window[0]] if window else None,
                           "to": sessions[trough],
                           "sessions": len(window),
                           "sum_normal_10bp": float(sum(series[i] for i in window if sessions[i] not in high)),
                           "sum_high_breadth_10bp": float(sum(series[i] for i in window if sessions[i] in high)),
                           "high_breadth_sessions_in_window": sum(sessions[i] in high for i in window)}}


def tail(series: np.ndarray, sessions: Sequence[str]) -> dict[str, Any]:
    total = float(series.sum())
    order = np.argsort(-series, kind="stable")
    out = {"sum_session_returns_10bp": total}
    for k in (1, 3, 5):
        top = float(series[order[:k]].sum())
        out[f"top{k}"] = {"sessions": [sessions[i] for i in order[:k]], "sum": top,
                          "share_of_total": (top / total) if total > 0 else None}
    return out


def blocks(series: np.ndarray, sessions: Sequence[str], gate_blocks) -> list[dict[str, Any]]:
    out = []
    for b in gate_blocks:
        idx = [i for i, d in enumerate(sessions) if b["first"] <= d <= b["last"]]
        r = series[idx]
        equity = np.cumprod(1.0 + r)
        peak = np.maximum.accumulate(np.concatenate(([1.0], equity)))[1:]
        out.append({"block": b["block"], "first": b["first"], "last": b["last"], "trades": b["trades"],
                    "mean_10bp": float(r.mean()), "compounded_10bp": float(equity[-1] - 1.0),
                    "block_mdd_10bp": float(np.min(equity / peak - 1.0)), "positive": bool(r.mean() > 0)})
    return out


def calendar(stability: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    out = {}
    for period in ("month", "quarter"):
        rows = stability[period]
        values = [(r[period], r["return_10bp"]) for r in rows]
        out[period] = {"positive": sum(v > 0 for _, v in values), "negative": sum(v < 0 for _, v in values),
                       "best": max(values, key=lambda x: x[1]), "worst": min(values, key=lambda x: x[1])}
    return out


def stop_b(cagr: float, mdd: float, ref_cagr: float, ref_mdd: float) -> dict[str, Any]:
    cagr_ratio = cagr / ref_cagr if ref_cagr else None
    mdd_ratio = abs(mdd) / abs(ref_mdd) if ref_mdd else None
    return {"cagr_ratio": cagr_ratio, "mdd_ratio": mdd_ratio,
            "flagged": bool(cagr_ratio is not None and mdd_ratio is not None and mdd_ratio > cagr_ratio)}
