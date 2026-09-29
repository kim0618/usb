"""Probability quality, not accuracy.

Accuracy is close to useless here and the contract says so: if a 3% move happens 10% of the time,
answering "no" every time scores 90%. What matters is whether the numbers mean what they say, so
the primary measures are the Brier score against the base-rate model, the ranking measures, and
calibration.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .models import PROB_CLIP, RidgeLogistic, logit


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def brier_skill(y: np.ndarray, p: np.ndarray, reference: np.ndarray) -> float:
    """1 - Brier(model) / Brier(M0). Positive means better than the base rate."""
    ref = brier(y, reference)
    if ref <= 0:
        return float("nan")
    return float(1.0 - brier(y, p) / ref)


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    q = np.clip(p, PROB_CLIP, 1.0 - PROB_CLIP)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def roc_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Mann-Whitney form, with average ranks so tied scores cannot inflate the result."""
    pos, neg = int(y.sum()), int(len(y) - y.sum())
    if pos == 0 or neg == 0:
        return float("nan")
    order = np.argsort(p, kind="stable")
    ranks = np.empty(len(p), dtype=np.float64)
    sorted_p = p[order]
    i = 0
    while i < len(sorted_p):
        j = i
        while j + 1 < len(sorted_p) and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def average_precision(y: np.ndarray, p: np.ndarray) -> float:
    """PR AUC as average precision: the step-wise area, not a trapezoid over an unstable curve."""
    pos = int(y.sum())
    if pos == 0:
        return float("nan")
    order = np.argsort(-p, kind="stable")
    hits = y[order]
    tp = np.cumsum(hits)
    precision = tp / np.arange(1, len(hits) + 1)
    return float(np.sum(precision * hits) / pos)


def reliability(y: np.ndarray, p: np.ndarray, buckets: int) -> list[dict[str, Any]]:
    """Equal-width buckets over [0, 1], each reporting what it promised and what happened."""
    edges = np.linspace(0.0, 1.0, buckets + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, buckets - 1)
    rows = []
    for b in range(buckets):
        mask = idx == b
        n = int(mask.sum())
        rows.append({
            "bucket": f"{edges[b]:.1f}-{edges[b + 1]:.1f}",
            "n": n,
            "mean_predicted": float(np.mean(p[mask])) if n else None,
            "actual_rate": float(np.mean(y[mask])) if n else None,
        })
    return rows


def expected_calibration_error(y: np.ndarray, p: np.ndarray, buckets: int) -> float:
    """Sample-weighted mean gap between promise and outcome across the reliability buckets."""
    rows = reliability(y, p, buckets)
    total = sum(r["n"] for r in rows)
    if total == 0:
        return float("nan")
    gap = sum(r["n"] * abs(r["mean_predicted"] - r["actual_rate"])
              for r in rows if r["n"] > 0)
    return float(gap / total)


def calibration_line(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    """Logistic regression of the outcome on the predicted logit.

    Slope 1 with intercept 0 is perfect. Slope below 1 means the model is overconfident: its
    extremes are further from the base rate than the data supports.
    """
    x = logit(p).reshape(-1, 1)
    if np.allclose(x, x[0]) or len(np.unique(y)) < 2:
        return {"slope": float("nan"), "intercept": float("nan")}
    model = RidgeLogistic(l2=0.0, iters=50, scale_inputs=False).fit(x, y.astype(np.float64))
    return {"slope": float(model.coef[1]), "intercept": float(model.coef[0])}


def confidence_buckets(y: np.ndarray, p: np.ndarray, thresholds: tuple[float, ...],
                       base_rate: float) -> list[dict[str, Any]]:
    """What actually happens where the model is most sure, and how that compares to the base rate."""
    rows = []
    for t in thresholds:
        mask = p >= t
        n = int(mask.sum())
        actual = float(np.mean(y[mask])) if n else None
        rows.append({
            "threshold": t, "n": n, "actual_rate": actual,
            "mean_predicted": float(np.mean(p[mask])) if n else None,
            "lift": (actual / base_rate) if (n and base_rate > 0) else None,
        })
    return rows


def separation(y_up: np.ndarray, p_up: np.ndarray, y_down: np.ndarray, p_down: np.ndarray,
               quantile: float = 0.90) -> dict[str, Any]:
    """Diagnostic only: where the two directional probabilities disagree most, what happened.

    This is the shape a trade would eventually be built on, which is exactly why it is reported
    here and not acted on: P1 does not produce rules.
    """
    diff = p_down - p_up
    if len(diff) == 0:
        return {"n": 0}
    hi = float(np.quantile(diff, quantile))
    lo = float(np.quantile(diff, 1.0 - quantile))
    down_heavy, up_heavy = diff >= hi, diff <= lo
    return {
        "n": int(len(diff)),
        "mean_separation": float(np.mean(diff)),
        "down_heavy": {
            "n": int(down_heavy.sum()),
            "threshold": hi,
            "actual_down_rate": float(np.mean(y_down[down_heavy])) if down_heavy.any() else None,
            "actual_up_rate": float(np.mean(y_up[down_heavy])) if down_heavy.any() else None,
        },
        "up_heavy": {
            "n": int(up_heavy.sum()),
            "threshold": lo,
            "actual_down_rate": float(np.mean(y_down[up_heavy])) if up_heavy.any() else None,
            "actual_up_rate": float(np.mean(y_up[up_heavy])) if up_heavy.any() else None,
        },
    }


def summary(y: np.ndarray, p: np.ndarray, reference: np.ndarray, *, buckets: int,
            thresholds: tuple[float, ...]) -> dict[str, Any]:
    base = float(np.mean(y)) if len(y) else float("nan")
    return {
        "n": int(len(y)),
        "positives": int(y.sum()),
        "base_rate": base,
        "brier": brier(y, p),
        "brier_skill": brier_skill(y, p, reference),
        "log_loss": log_loss(y, p),
        "roc_auc": roc_auc(y, p),
        "pr_auc": average_precision(y, p),
        "ece": expected_calibration_error(y, p, buckets),
        **{f"calibration_{k}": v for k, v in calibration_line(y, p).items()},
        "predicted": {
            "mean": float(np.mean(p)), "std": float(np.std(p)),
            "min": float(np.min(p)), "max": float(np.max(p)),
            "p05": float(np.quantile(p, 0.05)), "p50": float(np.quantile(p, 0.50)),
            "p95": float(np.quantile(p, 0.95)),
        },
        "reliability": reliability(y, p, buckets),
        "confidence": confidence_buckets(y, p, thresholds, base),
    }
