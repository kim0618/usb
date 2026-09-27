"""G0 summaries and the two-way cluster bootstrap.

The distribution block reuses E0's ``summarise`` (imported unchanged) for the whole-percent
thresholds and adds the declared half-percent ones under their own names, as E1 did, because
E0 names a threshold by ``int(round(x * 100))`` and would call 0.5% "0pct".

The bootstrap resamples **clusters**, not rows, and is run twice: once over source sessions
(every row of one evening shares that evening's market move) and once over tickers (one name's
rows share its news, float and spread). The declared gate reads the *weaker* of the two
intervals. Each draw recomputes the candidate and the baseline on the same drawn clusters, so a
draw of strong evenings moves both sides and cancels out of the lift. Every statistic is a mean
of a per-row quantity (the return, or an indicator such as ``R >= 2%``), so a draw is summarised
exactly from per-cluster sums and counts.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import stats as E0S

UP = (0.01, 0.02, 0.03, 0.05)
DOWN = (-0.01, -0.02, -0.03)
MFE_UP = (0.01, 0.02, 0.03, 0.05)
MAE_DOWN = (-0.01, -0.02, -0.03)


def summarise(values: np.ndarray) -> dict[str, Any]:
    block = E0S.summarise(values, gap_up=UP, gap_down=DOWN)
    if not block.get("n"):
        return block
    finite = values[np.isfinite(values)]
    block["p_ge_0_5pct"] = float(np.mean(finite >= 0.005))
    block["p_le_minus_0_5pct"] = float(np.mean(finite <= -0.005))
    return block


def excursions(mfe: np.ndarray, mae: np.ndarray) -> dict[str, Any]:
    ok = np.isfinite(mfe) & np.isfinite(mae)
    mfe, mae = mfe[ok], mae[ok]
    if mfe.size == 0:
        return {"n": 0}
    out: dict[str, Any] = {"n": int(mfe.size), "mfe_mean": float(mfe.mean()),
                           "mfe_median": float(np.median(mfe)), "mae_mean": float(mae.mean()),
                           "mae_median": float(np.median(mae)),
                           "mfe_p95": float(np.percentile(mfe, 95)),
                           "mae_p5": float(np.percentile(mae, 5))}
    for t in MFE_UP:
        out[f"p_mfe_ge_{int(t * 100)}pct"] = float(np.mean(mfe >= t))
    for t in MAE_DOWN:
        out[f"p_mae_le_minus_{int(-t * 100)}pct"] = float(np.mean(mae <= t))
    return out


TAIL_KEYS = ("p_ge_0_5pct", "p_gap_ge_1pct", "p_gap_ge_2pct", "p_gap_ge_3pct", "p_gap_ge_5pct")
DOWN_KEYS = ("p_le_minus_0_5pct", "p_gap_le_minus_1pct", "p_gap_le_minus_2pct",
             "p_gap_le_minus_3pct")


def lift(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, float]:
    """Differences (return units / probability points) and ratios against the baseline."""
    out: dict[str, float] = {}
    for key in ("mean", "median", "win_rate") + TAIL_KEYS + DOWN_KEYS:
        if key in candidate and key in baseline:
            out[key] = float(candidate[key]) - float(baseline[key])
    for key in TAIL_KEYS + DOWN_KEYS:
        if key in candidate and key in baseline and float(baseline[key]) > 0:
            out[f"{key}_ratio"] = float(candidate[key]) / float(baseline[key])
    return out


def excursion_lift(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, float]:
    out = {}
    for key, value in candidate.items():
        if key != "n" and key in baseline:
            out[key] = float(value) - float(baseline[key])
            if key.startswith("p_") and float(baseline[key]) > 0:
                out[f"{key}_ratio"] = float(value) / float(baseline[key])
    return out


def cluster_bootstrap(candidate: Mapping[str, np.ndarray], candidate_clusters: np.ndarray,
                      baseline: Mapping[str, np.ndarray], baseline_clusters: np.ndarray, *,
                      resamples: int, seed: int, chunk: int = 250) -> dict[str, Any]:
    """Percentile CIs of candidate-minus-baseline for every per-row metric passed in.

    ``candidate`` and ``baseline`` map a metric name to a per-row array (finite everywhere).
    """
    order, base_inv = np.unique(baseline_clusters, return_inverse=True)
    pos = np.searchsorted(order, candidate_clusters)
    pos = np.clip(pos, 0, order.size - 1)
    valid = order[pos] == candidate_clusters
    if not valid.all():
        raise ValueError("candidate clusters must be a subset of the baseline clusters")
    k = order.size
    names = list(candidate)
    cand_sums = np.stack([np.bincount(pos, weights=candidate[n], minlength=k) for n in names])
    base_sums = np.stack([np.bincount(base_inv, weights=baseline[n], minlength=k) for n in names])
    cand_counts = np.bincount(pos, minlength=k).astype(float)
    base_counts = np.bincount(base_inv, minlength=k).astype(float)

    rng = np.random.default_rng(seed)
    draws: list[np.ndarray] = []
    done = 0
    while done < resamples:
        size = min(chunk, resamples - done)
        picks = rng.integers(0, k, size=(size, k))
        weights = np.zeros((size, k))
        for i in range(size):
            weights[i] = np.bincount(picks[i], minlength=k)
        cn = weights @ cand_counts
        bn = weights @ base_counts
        usable = (cn > 0) & (bn > 0)
        w = weights[usable]
        diff = (w @ cand_sums.T) / cn[usable, None] - (w @ base_sums.T) / bn[usable, None]
        draws.append(diff)
        done += size
    all_draws = np.concatenate(draws) if draws else np.full((1, len(names)), np.nan)
    out: dict[str, Any] = {"resamples": int(all_draws.shape[0]), "clusters": int(k)}
    for j, name in enumerate(names):
        column = all_draws[:, j]
        out[name] = {"lift": float(np.mean(column)),
                     "ci95": [float(np.percentile(column, 2.5)), float(np.percentile(column, 97.5))],
                     "p_le_zero": float(np.mean(column <= 0))}
    return out


def extreme_removal(values: np.ndarray, baseline: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Original, top 1, top 5 and top 1% removed: mean, median, tails and their lifts."""
    order = np.argsort(values)[::-1]
    out = []
    for name, k in (("original", 0), ("drop_top_1", 1), ("drop_top_5", 5),
                    ("drop_top_1pct", max(1, int(round(values.size * 0.01))))):
        if k >= values.size:
            out.append({"removal": name, "n": 0})
            continue
        kept = values[np.sort(order[k:])]
        block = summarise(kept)
        out.append({"removal": name, "removed": int(k), "n": int(kept.size),
                    "mean": block["mean"], "median": block["median"],
                    "win_rate": block["win_rate"],
                    "p_gap_ge_2pct": block["p_gap_ge_2pct"], "p_gap_ge_3pct": block["p_gap_ge_3pct"],
                    "lift": lift(block, baseline)})
    return out


def concentration(values: np.ndarray, tickers: np.ndarray, baseline_mean: float) -> dict[str, Any]:
    """Share of the candidate's total excess from its top 1 / 5 / 10 tickers, plus HHI.

    E0's ``symbol_concentration`` (imported unchanged) gives the top-5 block and the result after
    removing them; top 1, top 10 and the Herfindahl index of positive excess are added here.
    """
    base = E0S.symbol_concentration(values, tickers, baseline_mean, top=5)
    excess = values - baseline_mean
    names, inverse = np.unique(tickers, return_inverse=True)
    sums = np.bincount(inverse, weights=excess, minlength=names.size)
    counts = np.bincount(inverse, minlength=names.size)
    total = float(sums.sum())
    ranked = np.argsort(sums)[::-1]
    positive = np.clip(sums, 0, None)
    shares_pos = positive / positive.sum() if positive.sum() > 0 else np.zeros_like(positive)
    base.update({
        "top1_share_of_total_excess": float(sums[ranked[:1]].sum() / total) if total else float("nan"),
        "top5_share_of_total_excess": float(sums[ranked[:5]].sum() / total) if total else float("nan"),
        "top10_share_of_total_excess": float(sums[ranked[:10]].sum() / total) if total else float("nan"),
        "hhi_positive_excess": float(np.sum(shares_pos ** 2)),
        "rows_hhi": float(np.sum((counts / counts.sum()) ** 2)),
        "top10": [{"ticker": str(names[i]), "rows": int(counts[i]), "excess_sum": float(sums[i])}
                  for i in ranked[:10]],
    })
    return base


def period_table(values: np.ndarray, periods: np.ndarray, base_values: np.ndarray,
                 base_periods: np.ndarray) -> list[dict[str, Any]]:
    out = []
    for period in sorted(set(base_periods.tolist())):
        block = summarise(values[periods == period])
        base = summarise(base_values[base_periods == period])
        row = {"period": period, "n": block.get("n", 0), "baseline_n": base.get("n", 0)}
        if block.get("n") and base.get("n"):
            row.update({"mean": block["mean"], "median": block["median"],
                        "win_rate": block["win_rate"],
                        "mean_lift": block["mean"] - base["mean"],
                        "tail2_lift": block["p_gap_ge_2pct"] - base["p_gap_ge_2pct"]})
        out.append(row)
    return out


def cost_stress(gross_mean: float, grid_bp: Sequence[float]) -> dict[str, Any]:
    """Round-trip cost in bp subtracted once per trade; break-even is the gross mean in bp."""
    out = {"gross_mean_bp": gross_mean * 1e4}
    for cost in grid_bp:
        out[f"net_at_{int(cost)}bp"] = gross_mean * 1e4 - float(cost)
    out["break_even_cost_bp"] = gross_mean * 1e4
    return out
