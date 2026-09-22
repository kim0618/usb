"""E-MAX V1 integrated replay (E-MAX-M6): one pass from the prepared source frames.

S1 = R1 ordering, max 3, X1 through ``capacity.execute`` (E-D2 / E-D3 / E-D4 / E-D5 unmodified).
S2 = S1 + B2 and S3 = S2 + global 2.0x, both through ``v1.compose``. No stage result file is read
as an input. The checks and diagnostics below read only the replayed sessions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any

import numpy as np

from app.backtest.strategy_e_max import m1_replay as MR
from app.backtest.strategy_e_r3 import run as R3
from app.strategy_e_max import breadth, capacity, ranking, v1

TOLERANCE = Decimal("1e-18")
COST_BP = {"COST_05BP": 5, "COST_10BP": 10, "COST_15BP": 15, "COST_20BP": 20}


def selections(prepared) -> dict[date, tuple[tuple[str, ...], tuple[str, ...]]]:
    out = {}
    for d, frame in prepared.frames.items():
        cands = prepared.seals[d].signal.candidate_symbols
        out[d] = ranking.select(v1.RANKING, cands, MR._features(frame, cands), v1.MAX_SELECTED)
    return out


def s1_session(prepared, d: date, chosen) -> dict[str, Any]:
    frame, sealed, rows, origins = prepared.frames[d], prepared.seals[d], prepared.rows, prepared.origins[d]
    selected, rest = chosen[d]
    priority = {s: k + 1 for k, s in enumerate(selected + rest)}
    view = R3.session_frame(frame, rows)
    out = capacity.execute(sealed.signal, selected, {s: rows[s][d].bars for s in selected},
                           view.descriptors, prepared.calendar, capacity=v1.MAX_SELECTED)
    records = []
    for record in out["records"]:
        record["selection_rank"] = priority[record["symbol"]]
        record["universe_origin"] = origins[record["symbol"]]
        records.append(record)
    for symbol in rest:
        records.append(MR.not_selected_row(d.isoformat(), symbol, priority[symbol],
                                           view.descriptors[symbol], origins[symbol]))
    out["records"] = sorted(records, key=lambda r: r["symbol"])
    out["candidates"] = sealed.signal.candidate_count
    out["selected_order"] = list(selected)
    out["digests"]["seal"] = sealed.seal_digest
    return out


def replay(prepared, chosen) -> tuple[list, list, list]:
    s1 = [s1_session(prepared, d, chosen) for d in prepared.frames]
    composed = [v1.compose(s) for s in s1]
    return s1, [a for a, _ in composed], [b for _, b in composed]


def exposure_map(s2) -> dict[str, str]:
    return {s["session"]: s["breadth_multiplier"] for s in sorted(s2, key=lambda s: s["session"])
            if Fraction(s["breadth_multiplier"]) != 1}


def _dec(weight: str) -> Decimal:
    f = Fraction(weight)
    return Decimal(f.numerator) / Decimal(f.denominator)


def independent_checks(prepared, chosen, s3) -> dict[str, Any]:
    checks = {"record_recompute": True, "equal_weight": True, "exposure_values": True,
              "cost_scaling": True, "selection": True, "breadth_map": True}
    worst = Decimal(0)
    for s in s3:
        d = date.fromisoformat(s["session"])
        signal, frame = prepared.seals[d].signal, prepared.frames[d]
        cands = signal.candidate_symbols
        order = ranking.order(v1.RANKING, cands, MR._features(frame, cands))
        if tuple(s["selected_order"]) != order[:v1.MAX_SELECTED]:
            checks["selection"] = False
        if any(r["standard_pnl"] and not r["selected"] for r in s["records"]):
            checks["selection"] = False
        rate = breadth.h5_rate(signal.candidate_count, signal.eligible_count)
        high = rate is not None and rate >= breadth.THRESHOLD
        if (Fraction(s["breadth_multiplier"]) != 1) != high:
            checks["breadth_map"] = False
        final = v1.FINAL_EXPOSURE["high_breadth" if high else "normal"]
        sized = [r for r in s["records"] if r["standard_pnl"]]
        if sized:
            if {Fraction(r["weight"]) for r in sized} != {final / len(sized)}:
                checks["equal_weight"] = False
            if Fraction(s["exposure"]) != final:
                checks["exposure_values"] = False
        for name, value in s["returns"].items():
            recomputed = sum((_dec(r["weight"]) * r[name] for r in sized), Decimal(0))
            worst = max(worst, abs(recomputed - value))
        if sized:
            for name, bp in COST_BP.items():
                drag = s["returns"]["GROSS_0BP"] - s["returns"][name]
                if abs(drag - _dec(s["exposure"]) * Decimal(bp) / Decimal(10000)) > TOLERANCE:
                    checks["cost_scaling"] = False
    checks["record_recompute"] = worst <= TOLERANCE
    checks["record_recompute_max_abs_error"] = str(worst)
    checks["pass"] = all(v for k, v in checks.items() if isinstance(v, bool))
    return checks


# -- diagnostics ------------------------------------------------------------------------------------

def funnel(ev: Mapping[str, Any], s3) -> dict[str, Any]:
    f = ev["funnel"]
    high = [s for s in s3 if Fraction(s["breadth_multiplier"]) != 1]
    return {"universe_rows": f["eligible_universe_rows"]["count"], "h5_candidates": f["H5_candidates"]["count"],
            "selected": f["selected_candidates"]["count"], "capacity_skipped": f["capacity_skipped"]["count"],
            "valid_entries": f["valid_entries"]["count"], "entry_invalid": f["invalid_entries"]["count"],
            "entry_invalid_reasons": f["invalid_entries"].get("reasons", {}),
            "valid_exact_exits": f["valid_exact_exits"]["count"],
            "unresolved_exit": f["unresolved_exits"]["count"],
            "unresolved_exit_reasons": f["unresolved_exits"].get("reasons", {}),
            "standard_trades": f["standard_pnl_trades"]["count"],
            "standard_pnl_coverage": f["standard_pnl_coverage"],
            "sessions_with_frames": len(s3), "active_sessions": sum(bool(s["active"]) for s in s3),
            "high_breadth_sessions": len(high),
            "high_breadth_active_sessions": sum(bool(s["active"]) for s in high)}


def cost_table(ev: Mapping[str, Any], cagr: Mapping[str, float], calmar: Mapping[str, float],
               ceiling: float) -> dict[str, Any]:
    out = {}
    for name in ("GROSS_0BP", "COST_05BP", "COST_10BP", "COST_15BP", "COST_20BP"):
        s = ev["scenarios"][name]
        out[name] = {"cagr": cagr[name], "cumulative": s["cumulative_return"], "mdd": s["maximum_drawdown"],
                     "calmar": calmar[name], "sharpe": s["sharpe"], "profit_factor": s["profit_factor"],
                     "session_mean": s["all_session_mean"], "mdd_ceiling_pass": s["maximum_drawdown"] >= ceiling}
    return out


def _mdd(r: np.ndarray) -> float:
    if r.size == 0:
        return 0.0
    equity = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(np.concatenate(([1.0], equity)))[1:]
    return float(np.min(equity / peak - 1.0))


def drawdown(series: np.ndarray, sessions: Sequence[str]) -> dict[str, Any]:
    equity = np.cumprod(1.0 + series)
    with_start = np.concatenate(([1.0], equity))
    peak_curve = np.maximum.accumulate(with_start)[1:]
    dd = equity / peak_curve - 1.0
    trough = int(np.argmin(dd))
    peak_value = float(peak_curve[trough])
    at_peak = [i - 1 for i in range(trough + 2) if with_start[i] == peak_value]   # -1 = start
    first, last = at_peak[0], at_peak[-1]
    later = [i for i in range(trough + 1, len(series)) if equity[i] >= peak_value]
    recovery = later[0] if later else None

    def name(i: int) -> str:
        return "START" if i < 0 else sessions[i]
    return {"mdd": float(dd[trough]), "peak_session_first": name(first), "peak_session_last": name(last),
            "trough_session": sessions[trough],
            "recovery_session": sessions[recovery] if recovery is not None else "OPEN",
            "underwater_sessions_peak_to_recovery": (recovery - last - 1) if recovery is not None
            else len(series) - last - 1,
            "sessions_peak_to_trough": trough - last,
            "sessions_trough_to_recovery": (recovery - trough) if recovery is not None else None}


def breadth_dependence(series: np.ndarray, sessions: Sequence[str], s3) -> dict[str, Any]:
    by = {s["session"]: s for s in s3}
    total = float(series.sum())
    out = {"sum_session_returns_10bp": total}
    for label, test in (("high_breadth", lambda d: d in by and Fraction(by[d]["breadth_multiplier"]) != 1),
                        ("normal", lambda d: not (d in by and Fraction(by[d]["breadth_multiplier"]) != 1))):
        idx = [i for i, d in enumerate(sessions) if test(d)]
        part = float(series[idx].sum())
        out[label] = {"sessions": len(idx),
                      "active_sessions": sum(bool(by[sessions[i]]["active"]) for i in idx if sessions[i] in by),
                      "trades": sum(sum(r["standard_pnl"] for r in by[sessions[i]]["records"])
                                    for i in idx if sessions[i] in by),
                      "sum_session_returns_10bp": part,
                      "share_of_total": part / total if total > 0 else None,
                      "compounded_10bp": float(np.prod(1.0 + series[idx]) - 1.0)}
    return out


def regimes(series: np.ndarray, sessions: Sequence[str], s3, rules: Mapping) -> dict[str, Any]:
    by = {s["session"]: s for s in s3}
    total = float(series.sum())
    out = {}
    for name in ("LEGACY_NARROW", "BROAD_COVERAGE"):
        first, last = rules["diagnostics"]["coverage_regime"][name]
        idx = [i for i, d in enumerate(sessions) if first <= d <= last]
        r = series[idx]
        out[name] = {"first": sessions[idx[0]], "last": sessions[idx[-1]], "sessions": len(idx),
                     "trades": sum(sum(x["standard_pnl"] for x in by[sessions[i]]["records"])
                                   for i in idx if sessions[i] in by),
                     "cumulative_10bp": float(np.prod(1.0 + r) - 1.0), "mean_session_10bp": float(r.mean()),
                     "mdd_10bp": _mdd(r), "sum_session_returns_10bp": float(r.sum()),
                     "share_of_total": float(r.sum()) / total if total > 0 else None}
    return out


def quarters(series: np.ndarray, sessions: Sequence[str], names: Sequence[str]) -> dict[str, Any]:
    total = float(series.sum())
    out = {}
    for q in names:
        year, n = int(q[:4]), int(q[-1])
        idx = [i for i, d in enumerate(sessions) if int(d[:4]) == year and (int(d[5:7]) - 1) // 3 + 1 == n]
        r = series[idx]
        out[q] = {"sessions": len(idx), "compounded_10bp": float(np.prod(1.0 + r) - 1.0),
                  "sum_session_returns_10bp": float(r.sum()),
                  "share_of_total": float(r.sum()) / total if total > 0 else None}
    return out


def first_difference(a: Any, b: Any, path: str = "") -> str | None:
    """Path of the first difference between two JSON values, or None when equal."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                return f"{path}/{key} (missing)"
            found = first_difference(a[key], b[key], f"{path}/{key}")
            if found:
                return found
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path} (length {len(a)} != {len(b)})"
        for i, (x, y) in enumerate(zip(a, b)):
            found = first_difference(x, y, f"{path}[{i}]")
            if found:
                return found
        return None
    return None if a == b else f"{path}: {a!r} != {b!r}"
