"""Evaluating the declared hypotheses and the robustness battery on a row table.

A hypothesis is a mask over the declared features. The masks are built from the declaration
and nothing else - there is no search, no sweep and no threshold that was chosen after a
result was seen. A hypothesis that reads CLV drops the rows where CLV is undefined (high ==
low), which is why each block reports its own ``n`` rather than inheriting the baseline's.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import stats
from app.backtest.strategy_e0_overnight.config import E0Rules

DAILY_HYPOTHESES = ("H1", "H2", "H3")
MINUTE_HYPOTHESES = ("H4", "H5")


def daily_mask(name: str, features: Mapping[str, np.ndarray]) -> np.ndarray:
    """The declared daily rules, transcribed one for one from the declaration file."""
    day_return, clv = features["day_return"], features["clv"]
    rvol, rel = features["rvol"], features["relative_strength_5d"]
    defined = np.isfinite(clv) & np.isfinite(day_return) & np.isfinite(rvol) & np.isfinite(rel)
    if name == "H1":
        return defined & (day_return > 0) & (clv >= 0.8)
    if name == "H2":
        return defined & (day_return > 0) & (clv >= 0.8) & (rvol >= 2.0)
    if name == "H3":
        return defined & (clv >= 0.8) & (rvol >= 2.0) & (rel > 0)
    raise KeyError(f"{name} is not a declared daily hypothesis")


def minute_mask(name: str, h2: np.ndarray, closing: Mapping[str, np.ndarray]) -> np.ndarray:
    """H4 and H5: the declared daily H2 plus a declared closing-strength condition."""
    if name == "H4":
        ret = closing["return_1530_to_close"]
        return h2 & np.isfinite(ret) & (ret > 0)
    if name == "H5":
        ret = closing["return_1545_to_close"]
        near = closing["close_to_day_high"]
        return h2 & np.isfinite(ret) & np.isfinite(near) & (near >= -0.005) & (ret > 0)
    raise KeyError(f"{name} is not a declared minute hypothesis")


def baseline_by_quarter(values: np.ndarray, sessions: np.ndarray) -> dict[str, Any]:
    quarters = np.array([stats.quarter_of(s) for s in sessions], dtype=object)
    return {q: stats.summarise(values[quarters == q]) for q in sorted(set(quarters.tolist()))}


def evaluate_candidate(name: str, mask: np.ndarray, values: np.ndarray, sessions: np.ndarray,
                       tickers: np.ndarray, baseline: Mapping[str, Any],
                       quarter_baseline: Mapping[str, Any], rules: E0Rules,
                       *, with_bootstrap: bool = True) -> dict[str, Any]:
    """Everything the gate and the report need about one candidate set."""
    selected = values[mask]
    block: dict[str, Any] = {"name": name, "summary": stats.summarise(selected)}
    if block["summary"]["n"] == 0:
        return block
    block["lift"] = stats.lift(block["summary"], baseline)
    block["quarterly"] = stats.quarterly(selected, sessions[mask], quarter_baseline)
    block["extreme_removal"] = stats.extreme_removal(selected, float(baseline["mean"]))
    block["symbol_concentration"] = stats.symbol_concentration(
        selected, tickers[mask], float(baseline["mean"]))
    for bucket in ("dollar_volume", "close_price"):
        block[f"by_{bucket}"] = None  # filled by the caller, which owns the feature arrays
    if with_bootstrap:
        settings = rules.bootstrap
        result = stats.session_bootstrap(
            selected, sessions[mask], values, sessions,
            resamples=int(settings["resamples"]), seed=int(settings["seed"]))
        block["bootstrap"] = {
            "resamples": result.resamples,
            "mean_lift": result.mean_lift,
            "mean_lift_ci": list(result.mean_lift_ci),
            "median_lift": result.median_lift,
            "median_lift_ci": list(result.median_lift_ci),
            "p_mean_lift_le_zero": result.p_mean_lift_le_zero,
        }
    return block


def horizon_table(mask: np.ndarray, columns: Mapping[str, np.ndarray],
                  baseline_mask: np.ndarray | None = None) -> list[dict[str, Any]]:
    """Close -> next open / 1m / 5m / 15m for one candidate set, plus MFE and MAE.

    The comparison is descriptive only: E0 declares no exit rule, and an MFE is not a fill.
    """
    rows = []
    for label in ("minute_overnight", "close_to_next_1m", "close_to_next_5m", "close_to_next_15m",
                  "next_5m_mfe", "next_5m_mae", "next_15m_mfe", "next_15m_mae"):
        series = columns.get(label)
        if series is None:
            continue
        block = stats.summarise(series[mask])
        row = {"horizon": label, **{k: block.get(k) for k in ("n", "mean", "median", "win_rate",
                                                              "p5", "p95")}}
        if baseline_mask is not None:
            base = stats.summarise(series[baseline_mask])
            row["baseline_mean"] = base.get("mean")
            row["baseline_median"] = base.get("median")
            row["mean_lift"] = (row["mean"] - base["mean"]
                                if row.get("mean") is not None and base.get("mean") is not None
                                else None)
        rows.append(row)
    return rows
