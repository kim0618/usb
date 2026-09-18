"""Selection-alpha statistics for one variant: matched base rates, lift, bootstrap, gate inputs.

Matching cell = (signal date, price bucket, pre-signal ATR% bucket, ADV20 bucket). A
candidate's matched base for a label is the mean of that label over the label-valid controls
of its cell, and only cells with at least ``min_controls`` such controls count. Lift is the
ratio of sums (candidate hits / matched base probabilities), so a date's candidates and their
bases move together under the date-block bootstrap.
"""

from collections.abc import Sequence
import math

import numpy as np
import pandas as pd

from app.backtest.strategy_c_selection.features import FeatureSet
from app.backtest.strategy_c_selection.labels import LabelSet
from app.backtest.strategy_c_selection.rules import SelectionRules, Variant

CLIP = 1.0
BLOCK_LENGTH = 10
REPLICATES = 2000
SEED = 20260917
PRIMARY_HIT = "mfe10_ge_15"
PRIMARY_K = 10
N_TIME_BLOCKS = 4


def build_rows(variant: Variant, features: FeatureSet, labels: LabelSet, window: Sequence[int],
               rules: SelectionRules, tickers: Sequence[str]) -> pd.DataFrame:
    eligible = features.eligible(variant)
    candidates = features.candidates(variant)
    in_window = np.zeros(eligible.shape[0], dtype=bool)
    in_window[list(window)] = True
    ii, jj = np.nonzero(eligible & in_window[:, None])
    frame = pd.DataFrame({"date_idx": ii, "ticker_idx": jj})
    frame["ticker"] = np.asarray(tickers, dtype=object)[jj]
    frame["candidate"] = candidates[ii, jj]
    for name in ("close", "atr_pct", "adv20_dollar", "price_bucket", "atr_bucket", "adv20_bucket"):
        frame[name] = features.values[name][ii, jj]
    frame["no_entry_bar"] = labels.no_entry_bar[ii, jj]
    frame["label_ca_suspect"] = labels.label_ca_suspect[ii, jj]
    for k in labels.horizons:
        frame[f"valid_{k}"] = labels.valid(k)[ii, jj]
        frame[f"disappeared_{k}"] = labels.disappeared[k][ii, jj]
        frame[f"mfe_{k}"] = labels.mfe[k][ii, jj]
        frame[f"mae_{k}"] = labels.mae[k][ii, jj]
        frame[f"close_{k}"] = np.clip(labels.close_return[k][ii, jj], -CLIP, CLIP)
        frame[f"time_to_mfe_{k}"] = labels.time_to_mfe[k][ii, jj]
    for name, (k, threshold) in rules.hit_definitions.items():
        frame[name] = (frame[f"mfe_{k}"] >= threshold).astype(float)
    buckets = frame[["price_bucket", "atr_bucket", "adv20_bucket"]]
    if buckets.isna().any().any():
        raise ValueError(f"{variant.name}: an eligible row has no matching bucket; eligibility and buckets disagree")
    frame["cell"] = (((frame["date_idx"] * 10 + frame["price_bucket"]) * 10 + frame["atr_bucket"]) * 10
                     + frame["adv20_bucket"]).astype(np.int64)
    return frame


def attach_matched_base(frame: pd.DataFrame, rules: SelectionRules, k: int) -> pd.DataFrame:
    """Candidate rows valid at horizon k with their cell's control means (NaN when unmatched)."""
    hits = [name for name, (hk, _) in rules.hit_definitions.items() if hk == k]
    metrics = hits + [f"mfe_{k}", f"mae_{k}", f"close_{k}"]
    valid = frame[f"valid_{k}"]
    controls = frame[~frame["candidate"] & valid]
    stats = controls.groupby("cell")[metrics].agg(["mean", "count"])
    base = pd.DataFrame({f"base_{m}": stats[(m, "mean")] for m in metrics})
    base["control_count"] = stats[(metrics[0], "count")]
    cand = frame[frame["candidate"] & valid].join(base, on="cell")
    cand["control_count"] = cand["control_count"].fillna(0)
    cand["matched"] = cand["control_count"] >= rules.min_controls
    date_all = frame[valid].groupby("date_idx")[metrics].mean().add_prefix("universe_")
    return cand.join(date_all, on="date_idx")


def block_indices(n_dates: int, replicates: int = REPLICATES, seed: int = SEED,
                  block: int = BLOCK_LENGTH) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(n_dates / block)
    starts = rng.integers(0, max(1, n_dates - block + 1), size=(replicates, n_blocks))
    return (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(replicates, -1)[:, :n_dates]


def _per_date(matched: pd.DataFrame, window: Sequence[int], columns: Sequence[str]) -> np.ndarray:
    sums = matched.groupby("date_idx")[list(columns)].sum().reindex(list(window), fill_value=0.0)
    return sums.to_numpy()


def _ratio_ci(num: np.ndarray, den: np.ndarray, draws: np.ndarray, levels: Sequence[float]) -> dict:
    boot_num, boot_den = num[draws].sum(axis=1), den[draws].sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        boot = boot_num / boot_den
    return {f"ci{int(round(level * 10000))}": [float(np.nanpercentile(boot, 50 * (1 - level))),
                                               float(np.nanpercentile(boot, 50 * (1 + level)))]
            for level in levels}


def _diff_ci(a: np.ndarray, b: np.ndarray, n: np.ndarray, draws: np.ndarray,
             levels: Sequence[float]) -> dict:
    with np.errstate(divide="ignore", invalid="ignore"):
        boot = (a[draws].sum(axis=1) - b[draws].sum(axis=1)) / n[draws].sum(axis=1)
    return {f"ci{int(round(level * 10000))}": [float(np.nanpercentile(boot, 50 * (1 - level))),
                                               float(np.nanpercentile(boot, 50 * (1 + level)))]
            for level in levels}


def _safe_ratio(a: float, b: float) -> float | None:
    return None if b == 0 or not np.isfinite(a) or not np.isfinite(b) else float(a / b)


def summarize(frame: pd.DataFrame, rules: SelectionRules, window: Sequence[int],
              draws: np.ndarray | None, levels: Sequence[float] = (0.95, 0.9833)) -> dict:
    window = list(window)
    cand_all = frame[frame["candidate"]]
    out: dict = {
        "eligible_rows": int(len(frame)),
        "candidates_total": int(len(cand_all)),
        "candidate_dates": int(cand_all["date_idx"].nunique()),
        "censoring": {
            group: {"rows": int(len(sub)),
                    "no_entry_bar": float(sub["no_entry_bar"].mean()) if len(sub) else None,
                    "label_ca_suspect": float(sub["label_ca_suspect"].mean()) if len(sub) else None,
                    "disappeared_10": float(sub["disappeared_10"].mean()) if len(sub) else None}
            for group, sub in (("candidates", cand_all), ("controls", frame[~frame["candidate"]]))},
        "hits": {},
    }
    by_k: dict[int, pd.DataFrame] = {}
    for k in sorted({hk for hk, _ in rules.hit_definitions.values()} | {PRIMARY_K}):
        cand = attach_matched_base(frame, rules, k)
        by_k[k] = cand
    primary = by_k[PRIMARY_K]
    matched = primary[primary["matched"]]
    out["label_valid_candidates_10"] = int(len(primary))
    out["unmatched_candidates_10"] = int((~primary["matched"]).sum())
    out["matched_candidates_10"] = int(len(matched))
    out["unique_tickers_10"] = int(matched["ticker"].nunique())

    for name, (k, _) in rules.hit_definitions.items():
        cand_k = by_k[k]
        m = cand_k[cand_k["matched"]]
        rate = float(m[name].mean()) if len(m) else float("nan")
        base = float(m[f"base_{name}"].mean()) if len(m) else float("nan")
        universe = float(m[f"universe_{name}"].mean()) if len(m) else float("nan")
        entry = {"n": int(len(m)), "candidate_rate": rate, "matched_base_rate": base,
                 "lift": _safe_ratio(rate, base), "universe_base_rate": universe,
                 "universe_lift_reference_only": _safe_ratio(rate, universe)}
        if draws is not None and len(m):
            per = _per_date(m, window, [name, f"base_{name}"])
            entry.update(_ratio_ci(per[:, 0], per[:, 1], draws, levels))
        out["hits"][name] = entry

    means = {}
    for label in (f"mfe_{PRIMARY_K}", f"mae_{PRIMARY_K}", f"close_{PRIMARY_K}"):
        entry = {"candidate_mean": float(matched[label].mean()) if len(matched) else None,
                 "matched_control_mean": float(matched[f"base_{label}"].mean()) if len(matched) else None,
                 "candidate_median": float(matched[label].median()) if len(matched) else None}
        if len(matched):
            entry["excess"] = entry["candidate_mean"] - entry["matched_control_mean"]
            if draws is not None:
                matched_one = matched.assign(one=1.0)
                per = _per_date(matched_one, window, [label, f"base_{label}", "one"])
                entry.update(_diff_ci(per[:, 0], per[:, 1], per[:, 2], draws, levels))
        means[label] = entry
    for k in rules.horizons:
        cand_k = by_k.get(k)
        if cand_k is None:
            cand_k = attach_matched_base(frame, rules, k)
        m = cand_k[cand_k["matched"]]
        means[f"horizon_{k}"] = {
            "n": int(len(m)),
            "candidate_mean_mfe": float(m[f"mfe_{k}"].mean()) if len(m) else None,
            "control_mean_mfe": float(m[f"base_mfe_{k}"].mean()) if len(m) else None,
            "candidate_mean_mae": float(m[f"mae_{k}"].mean()) if len(m) else None,
            "control_mean_mae": float(m[f"base_mae_{k}"].mean()) if len(m) else None,
            "candidate_mean_close": float(m[f"close_{k}"].mean()) if len(m) else None,
            "control_mean_close": float(m[f"base_close_{k}"].mean()) if len(m) else None,
            "candidate_mean_time_to_mfe": float(m[f"time_to_mfe_{k}"].mean()) if len(m) else None}
    out["means"] = means

    blocks = []
    for number, dates in enumerate(np.array_split(np.asarray(window), N_TIME_BLOCKS), start=1):
        sub = matched[matched["date_idx"].isin(set(dates.tolist()))]
        rate = float(sub[PRIMARY_HIT].mean()) if len(sub) else float("nan")
        base = float(sub[f"base_{PRIMARY_HIT}"].mean()) if len(sub) else float("nan")
        blocks.append({"block": number, "first_date_idx": int(dates[0]), "last_date_idx": int(dates[-1]),
                       "n": int(len(sub)), "unique_tickers": int(sub["ticker"].nunique()),
                       "candidate_rate": rate, "matched_base_rate": base, "lift": _safe_ratio(rate, base),
                       "close_10_excess": float((sub["close_10"] - sub["base_close_10"]).mean())
                       if len(sub) else None})
    out["time_blocks"] = blocks

    n = len(matched)
    if n:
        ticker_counts = matched["ticker"].value_counts()
        date_counts = matched["date_idx"].value_counts()
        out["concentration"] = {
            "single_ticker_max_share": float(ticker_counts.iloc[0] / n),
            "single_ticker_max": str(ticker_counts.index[0]),
            "top5_ticker_share": float(ticker_counts.iloc[:5].sum() / n),
            "single_date_max_share": float(date_counts.iloc[0] / n),
            "single_date_max_idx": int(date_counts.index[0]),
            "top10_date_share": float(date_counts.iloc[:10].sum() / n),
            "top5_tickers": {str(t): int(c) for t, c in ticker_counts.iloc[:5].items()},
            "hits_share_top5_tickers": float(
                matched[matched["ticker"].isin(ticker_counts.index[:5])][PRIMARY_HIT].sum()
                / max(matched[PRIMARY_HIT].sum(), 1.0)),
        }
    else:
        out["concentration"] = None
    return out


def gate(summary: dict, pit_violations: int, levels_key: str = "ci9833") -> dict:
    primary = summary["hits"].get(PRIMARY_HIT, {})
    close = summary["means"].get(f"close_{PRIMARY_K}", {})
    mae = summary["means"].get(f"mae_{PRIMARY_K}", {})
    blocks = summary["time_blocks"]
    conc = summary["concentration"] or {}
    lift = primary.get("lift")
    conditions = {
        "1_primary_lift_point": lift is not None and lift > 1.0,
        "2_primary_lift_ci_low": bool(primary.get(levels_key)) and primary[levels_key][0] > 1.0,
        "3_close_return_10_excess_ci_low": bool(close.get(levels_key)) and close[levels_key][0] > 0.0,
        "4_mae_10_excess_point": close.get("excess") is not None and mae.get("excess") is not None
        and mae["excess"] >= -0.02,
        "5_time_blocks_with_lift_gt_1": sum(1 for b in blocks if (b["lift"] or 0) > 1.0) >= 3
        and len(blocks) == N_TIME_BLOCKS,
        "6_matched_label_valid_candidates": summary["matched_candidates_10"] >= 300,
        "7_unique_tickers": summary["unique_tickers_10"] >= 100,
        "8_concentration": bool(conc) and conc["single_ticker_max_share"] <= 0.05
        and conc["top5_ticker_share"] <= 0.15 and conc["single_date_max_share"] <= 0.05
        and conc["top10_date_share"] <= 0.25,
        "9_pit_violations": pit_violations == 0,
    }
    return {"conditions": conditions, "all_pass": all(conditions.values())}


def overall_decision(gates: dict[str, dict]) -> str:
    if any(g["all_pass"] for g in gates.values()):
        return "PASS"
    for g in gates.values():
        c = g["conditions"]
        failing = {name for name, ok in c.items() if not ok}
        if c["1_primary_lift_point"] and c["2_primary_lift_ci_low"] and c["3_close_return_10_excess_ci_low"] \
                and failing and failing <= {"6_matched_label_valid_candidates", "7_unique_tickers",
                                            "8_concentration"}:
            return "INCONCLUSIVE"
    return "FAIL"
