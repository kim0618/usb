"""The declared E1 hypotheses and the robustness battery.

Masks are transcribed from the declaration and nothing else. A hypothesis that reads a feature
which is undefined on a row (a window with fewer than two premarket prints, an RVOL without
enough prior premarket sessions, a zero premarket range) excludes that row rather than treating
the missing value as a zero, so each block reports its own ``n``.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import stats
from app.backtest.strategy_e1_premarket.config import E1Rules

HYPOTHESES = ("H1", "H2", "H3", "H4", "H5")
#: The three sets §core_comparison asks to separate.
CORE_SETS = ("gap_only", "gap_plus_volume", "gap_plus_closing_strength")
HORIZONS = ("1m", "5m", "15m")


def mask(name: str, features: Mapping[str, np.ndarray]) -> np.ndarray:
    gap = features["premarket_gap"]
    rvol = features["premarket_rvol"]
    position = features["position_in_premarket_range"]
    r0900 = features["return_0900_0925"]
    r30 = features["return_last30m"]
    rel = features["relative_strength_vs_spy"]
    value = features["premarket_dollar_volume"]
    finite_gap = np.isfinite(gap)
    if name == "H1":
        return finite_gap & (gap > 0) & np.isfinite(r0900) & (r0900 > 0)
    if name == "H2":
        return finite_gap & (gap > 0) & np.isfinite(rvol) & (rvol >= 3.0)
    if name == "H3":
        return np.isfinite(position) & (position >= 0.8) & np.isfinite(r30) & (r30 > 0)
    if name == "H4":
        return (finite_gap & (gap > 0) & np.isfinite(rel) & (rel > 0)
                & np.isfinite(value) & (value >= 1_000_000.0))
    if name == "H5":
        return (finite_gap & (gap > 0) & np.isfinite(rvol) & (rvol >= 3.0)
                & np.isfinite(position) & (position >= 0.8)
                & np.isfinite(r0900) & (r0900 > 0))
    if name == "gap_only":
        return finite_gap & (gap > 0)
    if name == "gap_plus_volume":
        return mask("H2", features)
    if name == "gap_plus_closing_strength":
        return finite_gap & (gap > 0) & np.isfinite(position) & (position >= 0.8)
    raise KeyError(f"{name} is not a declared E1 set")


#: E0's ``summarise`` names a threshold by ``int(round(x * 100))``, which turns 0.5% into "0pct".
#: E0 is closed and must not be edited, so E1 asks it only for the whole-percent thresholds and
#: adds the declared half-percent ones itself, under names that say what they are.
WHOLE_PERCENT_UP = (0.01, 0.02, 0.03)
WHOLE_PERCENT_DOWN = (-0.01, -0.02, -0.03)


def summarise(values: np.ndarray, rules: E1Rules) -> dict[str, Any]:
    block = stats.summarise(values, gap_up=WHOLE_PERCENT_UP, gap_down=WHOLE_PERCENT_DOWN)
    if not block.get("n"):
        return block
    finite = values[np.isfinite(values)]
    for threshold in rules.up_thresholds:
        if threshold not in WHOLE_PERCENT_UP:
            block[f"p_ge_{threshold * 100:g}pct".replace(".", "_")] = float(
                np.mean(finite >= threshold))
    for threshold in rules.down_thresholds:
        if threshold not in WHOLE_PERCENT_DOWN:
            block[f"p_le_minus_{abs(threshold) * 100:g}pct".replace(".", "_")] = float(
                np.mean(finite <= threshold))
    return block


def lift(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, float]:
    """Candidate minus baseline, with the declared E1 downside metric as the ratio."""
    keys = ("mean", "median", "win_rate", "p_ge_0_5pct", "p_gap_ge_1pct", "p_gap_ge_2pct",
            "p_gap_ge_3pct", "p_le_minus_0_5pct", "p_gap_le_minus_1pct", "p_gap_le_minus_2pct",
            "p_gap_le_minus_3pct")
    out = {k: float(candidate[k]) - float(baseline[k])
           for k in keys if k in candidate and k in baseline}
    down_c = float(candidate.get("p_gap_le_minus_1pct", float("nan")))
    down_b = float(baseline.get("p_gap_le_minus_1pct", float("nan")))
    out["downside_ratio"] = down_c / down_b if down_b > 0 else float("nan")
    return out


def volatility_selector(candidate: Mapping[str, Any], baseline: Mapping[str, Any],
                        lifts: Mapping[str, float]) -> bool:
    """The declared E0 failure mode: a positive mean bought with both tails, not with direction."""
    if not candidate.get("n"):
        return False
    mean_up = lifts.get("mean", 0.0) > 0
    median_flat = lifts.get("median", 0.0) <= 0
    both_tails = (candidate.get("p_gap_ge_1pct", 0.0) > baseline.get("p_gap_ge_1pct", 0.0)
                  and candidate.get("p_gap_le_minus_1pct", 0.0)
                  > baseline.get("p_gap_le_minus_1pct", 0.0))
    return bool(mean_up and median_flat and both_tails)


def baseline_by_quarter(values: np.ndarray, sessions: np.ndarray, rules: E1Rules) -> dict[str, Any]:
    quarters = np.array([stats.quarter_of(s) for s in sessions], dtype=object)
    return {q: summarise(values[quarters == q], rules) for q in sorted(set(quarters.tolist()))}


def evaluate_candidate(name: str, selected: np.ndarray, values: np.ndarray, sessions: np.ndarray,
                       tickers: np.ndarray, baseline: Mapping[str, Any],
                       quarter_baseline: Mapping[str, Any], rules: E1Rules,
                       *, with_bootstrap: bool = True) -> dict[str, Any]:
    chosen = values[selected]
    block: dict[str, Any] = {"name": name, "summary": summarise(chosen, rules)}
    if not block["summary"].get("n"):
        return block
    block["lift"] = lift(block["summary"], baseline)
    block["volatility_selector"] = volatility_selector(block["summary"], baseline, block["lift"])
    block["quarterly"] = _quarterly(chosen, sessions[selected], quarter_baseline, rules)
    block["extreme_removal"] = stats.extreme_removal(chosen, float(baseline["mean"]))
    block["symbol_concentration"] = stats.symbol_concentration(
        chosen, tickers[selected], float(baseline["mean"]))
    if with_bootstrap:
        settings = rules.bootstrap
        result = stats.session_bootstrap(chosen, sessions[selected], values, sessions,
                                         resamples=int(settings["resamples"]),
                                         seed=int(settings["seed"]))
        block["bootstrap"] = {
            "resamples": result.resamples, "mean_lift": result.mean_lift,
            "mean_lift_ci": list(result.mean_lift_ci), "median_lift": result.median_lift,
            "median_lift_ci": list(result.median_lift_ci),
            "p_mean_lift_le_zero": result.p_mean_lift_le_zero,
        }
    return block


def _quarterly(values: np.ndarray, sessions: np.ndarray, quarter_baseline: Mapping[str, Any],
               rules: E1Rules) -> list[dict[str, Any]]:
    quarters = np.array([stats.quarter_of(s) for s in sessions], dtype=object)
    rows = []
    for quarter in sorted(set(quarters.tolist())):
        block = summarise(values[quarters == quarter], rules)
        base = quarter_baseline.get(quarter)
        if block.get("n") and base and base.get("n"):
            block["lift"] = lift(block, base)
            block["baseline_n"] = base["n"]
            block["baseline_mean"] = base["mean"]
        rows.append({"quarter": quarter, **block})
    return rows


def horizon_table(selected: np.ndarray, labels: Mapping[str, np.ndarray], rules: E1Rules,
                  baseline_mask: np.ndarray | None = None) -> list[dict[str, Any]]:
    """R, MFE and MAE at every declared horizon, plus the strict-bar reading of R."""
    rows = []
    for horizon in HORIZONS:
        for kind in (f"R_{horizon}", f"R_{horizon}_strict", f"MFE_{horizon}", f"MAE_{horizon}"):
            series = labels.get(kind)
            if series is None:
                continue
            block = summarise(series[selected], rules)
            row = {"label": kind, **{k: block.get(k) for k in
                                     ("n", "mean", "median", "win_rate", "p5", "p95")}}
            if baseline_mask is not None:
                base = summarise(series[baseline_mask], rules)
                row["baseline_mean"] = base.get("mean")
                row["baseline_median"] = base.get("median")
                if row.get("mean") is not None and base.get("mean") is not None:
                    row["mean_lift"] = row["mean"] - base["mean"]
                    row["median_lift"] = row["median"] - base["median"]
            rows.append(row)
    return rows
