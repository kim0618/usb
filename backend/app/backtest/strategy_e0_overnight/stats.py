"""Summaries, bucket tables, robustness checks and the block bootstrap.

Every number the report quotes is produced by one of these functions, so a metric is defined
once. Two conventions matter for reading the output:

* **lift** is always the candidate's statistic minus the same statistic on the full baseline,
  in return units (0.0015 = 15 basis points), never a ratio;
* the bootstrap resamples **sessions**, not rows. Overnight returns of one session share the
  whole market's move, so a row bootstrap would treat 2,400 correlated rows as 2,400
  independent draws and report an interval several times too narrow.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

GAP_UP = (0.0, 0.01, 0.02, 0.03)
GAP_DOWN = (-0.01, -0.03, -0.05)


def summarise(values: np.ndarray, *, gap_up: Sequence[float] = GAP_UP,
              gap_down: Sequence[float] = GAP_DOWN) -> dict[str, Any]:
    """The distribution block the report prints for the baseline and for every candidate."""
    values = values[np.isfinite(values)]
    n = int(values.size)
    if n == 0:
        return {"n": 0}
    out: dict[str, Any] = {
        "n": n,
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "win_rate": float(np.mean(values > 0)),
        "std": float(np.std(values, ddof=1)) if n > 1 else float("nan"),
        "p5": float(np.percentile(values, 5)),
        "p25": float(np.percentile(values, 25)),
        "p75": float(np.percentile(values, 75)),
        "p95": float(np.percentile(values, 95)),
    }
    for threshold in gap_up:
        key = "p_gap_ge_0" if threshold == 0 else f"p_gap_ge_{int(round(threshold * 100))}pct"
        out[key] = float(np.mean(values >= threshold)) if threshold else float(np.mean(values > 0))
    for threshold in gap_down:
        out[f"p_gap_le_minus_{abs(int(round(threshold * 100)))}pct"] = float(np.mean(values <= threshold))
    positive, negative = values[values > 0], values[values < 0]
    out["mean_positive_gap"] = float(np.mean(positive)) if positive.size else float("nan")
    out["mean_negative_gap"] = float(np.mean(negative)) if negative.size else float("nan")
    out["worst_5"] = [float(x) for x in np.sort(values)[:5]]
    out["best_5"] = [float(x) for x in np.sort(values)[-5:][::-1]]
    return out


def lift(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, float]:
    """Candidate minus baseline for the statistics the gate reads."""
    keys = ("mean", "median", "win_rate", "p_gap_ge_1pct", "p_gap_ge_2pct", "p_gap_ge_3pct",
            "p_gap_le_minus_1pct", "p_gap_le_minus_3pct", "p_gap_le_minus_5pct")
    out = {}
    for key in keys:
        if key in candidate and key in baseline:
            out[key] = float(candidate[key]) - float(baseline[key])
    down_c = float(candidate.get("p_gap_le_minus_5pct", float("nan")))
    down_b = float(baseline.get("p_gap_le_minus_5pct", float("nan")))
    out["downside_ratio"] = down_c / down_b if down_b > 0 else float("nan")
    return out


def bucket_labels(edges: Sequence[float | None]) -> list[str]:
    labels = []
    for lo, hi in zip(edges, edges[1:]):
        lo_text = "-inf" if lo is None else f"{lo:g}"
        hi_text = "+inf" if hi is None else f"{hi:g}"
        labels.append(f"[{lo_text}, {hi_text})")
    return labels


def bucket_index(values: np.ndarray, edges: Sequence[float | None]) -> np.ndarray:
    """``-1`` for a value outside every declared bucket or not finite; else the bucket index.

    The last declared bucket is closed on the right when its upper edge is a number, so a CLV
    of exactly 1.0 lands in ``[0.95, 1.0]`` rather than nowhere.
    """
    out = np.full(values.shape, -1, dtype=np.int32)
    finite = np.isfinite(values)
    last = len(edges) - 2
    for i, (lo, hi) in enumerate(zip(edges, edges[1:])):
        sel = finite.copy()
        if lo is not None:
            sel &= values >= lo
        if hi is not None:
            sel &= (values <= hi) if i == last else (values < hi)
        out[sel & (out == -1)] = i
    return out


def bucket_table(values: np.ndarray, labels_: np.ndarray, edges: Sequence[float | None],
                 baseline: Mapping[str, Any]) -> list[dict[str, Any]]:
    index = bucket_index(values, edges)
    rows = []
    for i, name in enumerate(bucket_labels(edges)):
        sel = index == i
        block = summarise(labels_[sel])
        if block["n"]:
            block["lift"] = lift(block, baseline)
        rows.append({"bucket": name, **block})
    dropped = int((index == -1).sum())
    if dropped:
        rows.append({"bucket": "UNDEFINED_OR_OUTSIDE", "n": dropped})
    return rows


def quarter_of(session) -> str:
    return f"{session.year}Q{(session.month - 1) // 3 + 1}"


def quarterly(values: np.ndarray, sessions: np.ndarray, baseline_by_quarter: Mapping[str, Any],
              ) -> list[dict[str, Any]]:
    """Per calendar quarter of D, compared with the same quarter's own baseline.

    A quarter is compared with its own baseline rather than the whole-period one so that a
    quarter in which the entire market gapped up cannot be read as evidence for the signal.
    """
    quarters = np.array([quarter_of(s) for s in sessions], dtype=object)
    rows = []
    for quarter in sorted(set(quarters.tolist())):
        sel = quarters == quarter
        block = summarise(values[sel])
        base = baseline_by_quarter.get(quarter)
        if block.get("n") and base:
            block["lift"] = lift(block, base)
            block["baseline_n"] = base["n"]
            block["baseline_mean"] = base["mean"]
        rows.append({"quarter": quarter, **block})
    return rows


def extreme_removal(values: np.ndarray, baseline_mean: float) -> list[dict[str, Any]]:
    """Mean and mean lift after dropping the largest positive outcomes."""
    order = np.argsort(values)[::-1]
    rows = []
    for name, k in (("drop_top_1", 1), ("drop_top_5", 5),
                    ("drop_top_1pct", max(1, int(round(values.size * 0.01))))):
        if k >= values.size:
            rows.append({"removal": name, "n": 0})
            continue
        kept = values[np.sort(order[k:])]
        rows.append({"removal": name, "removed": int(k), "n": int(kept.size),
                     "mean": float(np.mean(kept)), "median": float(np.median(kept)),
                     "win_rate": float(np.mean(kept > 0)),
                     "mean_lift": float(np.mean(kept) - baseline_mean)})
    return rows


def symbol_concentration(values: np.ndarray, tickers: np.ndarray, baseline_mean: float,
                         *, top: int = 5) -> dict[str, Any]:
    """How much of the candidate's total excess return a few names explain.

    Excess is measured per row as ``value - baseline_mean``, so the shares below say which
    tickers produced the edge, not merely which ones rose.
    """
    excess = values - baseline_mean
    total = float(excess.sum())
    names, inverse = np.unique(tickers, return_inverse=True)
    sums = np.bincount(inverse, weights=excess, minlength=names.size)
    counts = np.bincount(inverse, minlength=names.size)
    order = np.argsort(sums)[::-1]
    leaders = [{"ticker": str(names[i]), "rows": int(counts[i]), "excess_sum": float(sums[i]),
                "share_of_total_excess": float(sums[i] / total) if total else float("nan")}
               for i in order[:top]]
    top_names = {str(names[i]) for i in order[:top]}
    keep = ~np.isin(tickers, list(top_names))
    kept = values[keep]
    return {
        "distinct_tickers": int(names.size),
        "total_excess": total,
        "top_contributors": leaders,
        "top_share_of_total_excess": float(sum(x["excess_sum"] for x in leaders) / total)
        if total else float("nan"),
        "after_removing_top": {
            "n": int(kept.size),
            "mean": float(np.mean(kept)) if kept.size else float("nan"),
            "median": float(np.median(kept)) if kept.size else float("nan"),
            "mean_lift": float(np.mean(kept) - baseline_mean) if kept.size else float("nan"),
        },
    }


@dataclass(frozen=True)
class BootstrapResult:
    resamples: int
    mean_lift: float
    mean_lift_ci: tuple[float, float]
    median_lift: float
    median_lift_ci: tuple[float, float]
    p_mean_lift_le_zero: float


def _session_blocks(values: np.ndarray, sessions: np.ndarray, order: Sequence[Any],
                    bins: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per session: the sum, the count and the histogram of the values it contributes."""
    index = {s: i for i, s in enumerate(order)}
    sums = np.zeros(len(order))
    counts = np.zeros(len(order))
    hist = np.zeros((len(order), bins.size - 1), dtype=np.float32)
    for session, group in _group(values, sessions):
        i = index.get(session)
        if i is None:
            continue
        sums[i] = float(group.sum())
        counts[i] = float(group.size)
        hist[i] = np.histogram(np.clip(group, bins[0], bins[-1]), bins=bins)[0]
    return sums, counts, hist


def _group(values: np.ndarray, sessions: np.ndarray):
    order = np.argsort(sessions, kind="stable")
    sorted_sessions = sessions[order]
    sorted_values = values[order]
    edges = np.flatnonzero(sorted_sessions[1:] != sorted_sessions[:-1]) + 1
    for lo, hi in zip(np.concatenate([[0], edges]),
                      np.concatenate([edges, [sorted_sessions.size]])):
        yield sorted_sessions[lo], sorted_values[lo:hi]


def _median_from_hist(cumulative: np.ndarray, centres: np.ndarray) -> np.ndarray:
    """Median of each row of a histogram, to the resolution of one bin."""
    totals = cumulative[:, -1]
    target = totals / 2.0
    idx = np.array([np.searchsorted(row, t, side="left") for row, t in zip(cumulative, target)])
    idx = np.clip(idx, 0, centres.size - 1)
    return centres[idx]


def session_bootstrap(candidate_values: np.ndarray, candidate_sessions: np.ndarray,
                      baseline_values: np.ndarray, baseline_sessions: np.ndarray,
                      *, resamples: int, seed: int, bin_width: float = 1e-4,
                      chunk: int = 500) -> BootstrapResult:
    """Resample whole sessions and recompute the lift on each draw.

    Both sides are recomputed on the *same* drawn sessions, so a draw of unusually strong days
    moves the candidate and the baseline together and cancels out of the lift.

    A draw is summarised from per-session sums, counts and histograms rather than by rebuilding
    the concatenated sample, which is what makes 10,000 draws over a million rows tractable.
    The mean lift is therefore exact; the median lift carries the resolution of one bin
    (``bin_width``, one basis point by default), which is finer than any threshold the gate reads.
    """
    order = list(np.unique(baseline_sessions))
    bins = np.arange(-1.0, 1.0 + bin_width, bin_width)
    centres = (bins[:-1] + bins[1:]) / 2.0
    cand_sums, cand_counts, cand_hist = _session_blocks(
        candidate_values, candidate_sessions, order, bins)
    base_sums, base_counts, base_hist = _session_blocks(
        baseline_values, baseline_sessions, order, bins)

    rng = np.random.default_rng(seed)
    n_sessions = len(order)
    mean_lifts: list[np.ndarray] = []
    median_lifts: list[np.ndarray] = []
    done = 0
    while done < resamples:
        size = min(chunk, resamples - done)
        picks = rng.integers(0, n_sessions, size=(size, n_sessions))
        weights = np.zeros((size, n_sessions), dtype=np.float32)
        for i in range(size):
            weights[i] = np.bincount(picks[i], minlength=n_sessions)
        cand_n = weights @ cand_counts
        base_n = weights @ base_counts
        usable = (cand_n > 0) & (base_n > 0)
        if not usable.any():
            done += size
            continue
        weights = weights[usable]
        cand_mean = (weights @ cand_sums) / (weights @ cand_counts)
        base_mean = (weights @ base_sums) / (weights @ base_counts)
        mean_lifts.append(cand_mean - base_mean)
        cand_median = _median_from_hist(np.cumsum(weights @ cand_hist, axis=1), centres)
        base_median = _median_from_hist(np.cumsum(weights @ base_hist, axis=1), centres)
        median_lifts.append(cand_median - base_median)
        done += size

    means = np.concatenate(mean_lifts) if mean_lifts else np.array([np.nan])
    medians = np.concatenate(median_lifts) if median_lifts else np.array([np.nan])
    return BootstrapResult(
        resamples=int(means.size),
        mean_lift=float(np.mean(means)),
        mean_lift_ci=(float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))),
        median_lift=float(np.mean(medians)),
        median_lift_ci=(float(np.percentile(medians, 2.5)), float(np.percentile(medians, 97.5))),
        p_mean_lift_le_zero=float(np.mean(means <= 0)),
    )
