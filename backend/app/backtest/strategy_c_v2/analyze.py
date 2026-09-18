"""Matched-control excess statistics for V2A/V2B, reusing V1 rows, cells and bootstrap draws.

``excess_stats`` generalizes V1 ``attach_matched_base`` to any metric column with its own
validity: a candidate counts when its cell has >= min_controls controls for which the same
metric is valid, and its base is those controls' mean. The mean excess CI resamples signal
dates exactly as V1 ``_diff_ci`` does.
"""

from collections.abc import Sequence

import numpy as np
import pandas as pd

from app.backtest.strategy_c_selection.evaluate import N_TIME_BLOCKS


def matched_metric(frame: pd.DataFrame, metric: str, valid: str, min_controls: int) -> pd.DataFrame:
    usable = frame[frame[valid] & np.isfinite(frame[metric])]
    controls = usable[~usable["candidate"]]
    stats = controls.groupby("cell")[metric].agg(["mean", "count"])
    cand = usable[usable["candidate"]][["date_idx", "ticker", "cell", metric]].join(stats, on="cell")
    cand = cand[cand["count"].fillna(0) >= min_controls]
    return cand.rename(columns={metric: "value", "mean": "base"})


def _ci(boot: np.ndarray, level: float) -> list[float]:
    return [float(np.nanpercentile(boot, 50 * (1 - level))), float(np.nanpercentile(boot, 50 * (1 + level)))]


def excess_stats(cand: pd.DataFrame, window: Sequence[int], draws: np.ndarray | None,
                 levels: Sequence[float], blocks: bool = True) -> dict:
    n = len(cand)
    if n == 0:
        return {"n": 0}
    out = {"n": int(n), "unique_tickers": int(cand["ticker"].nunique()),
           "candidate_mean": float(cand["value"].mean()), "control_mean": float(cand["base"].mean()),
           "candidate_median": float(cand["value"].median()),
           "median_excess": float((cand["value"] - cand["base"]).median())}
    out["excess"] = out["candidate_mean"] - out["control_mean"]
    if draws is not None:
        per = cand.assign(one=1.0).groupby("date_idx")[["value", "base", "one"]].sum() \
            .reindex(list(window), fill_value=0.0).to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            boot = (per[draws, 0].sum(axis=1) - per[draws, 1].sum(axis=1)) / per[draws, 2].sum(axis=1)
        out["ci"] = {f"{level:.6f}": _ci(boot, level) for level in levels}
    if blocks:
        block_excess = []
        for dates in np.array_split(np.asarray(window), N_TIME_BLOCKS):
            sub = cand[cand["date_idx"].isin(set(dates.tolist()))]
            block_excess.append(float((sub["value"] - sub["base"]).mean()) if len(sub) else None)
        out["block_excess"] = block_excess
    return out


def sign_of(stats: dict, level_key: str) -> str:
    low, high = stats["ci"][level_key]
    return "pos" if low > 0 else "neg" if high < 0 else "zero"


def classify(signs: dict[str, str]) -> str:
    """Rules from c_v2ab_rules_v1.json c_v2b classification_per_variant, first match wins."""
    u, dn, a, r, c = (signs[k] for k in ("log_up_10", "log_down_10", "log_asym_10", "range_10", "close_10"))
    if u == "zero" and dn == "zero" and r == "zero":
        return "NEITHER"
    if r == "pos" and (c == "pos" or a == "pos"):
        return "BOTH"
    if u == "pos" and dn in ("zero", "neg"):
        return "DIRECTIONAL"
    if c == "pos" and r == "zero":
        return "DIRECTIONAL"
    if u == "pos" and dn == "pos" and a == "zero" and c == "zero":
        return "VOLATILITY"
    if r == "pos" and c == "zero" and a == "neg":
        return "VOLATILITY"
    return "UNCLASSIFIED"


def overall(labels: Sequence[str]) -> str:
    for label in set(labels):
        if list(labels).count(label) >= 2:
            return label
    return "MIXED"
