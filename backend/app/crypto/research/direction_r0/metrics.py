"""Classification metrics and day-block bootstrap (contract section 6). Pure numpy."""
from __future__ import annotations

import math

import numpy as np

THRESHOLDS = (0.55, 0.60, 0.65)
SEED = 20261013
N_BOOT = 2000
BLOCK_DAYS = 7


def auc(y: np.ndarray, p: np.ndarray) -> float:
    """y in {+1,-1}; probability of LONG p. Mann-Whitney with average ranks."""
    pos = y == 1
    n1, n0 = int(pos.sum()), int((~pos).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    uniq, inv, counts = np.unique(p, return_inverse=True, return_counts=True)
    upper = np.cumsum(counts)
    avg_rank = upper - (counts - 1) / 2.0                  # average 1-based rank of each tie group
    ranks = avg_rank[inv]
    return float((ranks[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def classify(p: np.ndarray, t: float = 0.5) -> np.ndarray:
    out = np.zeros(len(p), dtype=np.int8)
    if t == 0.5:
        out[p >= 0.5] = 1
        out[p < 0.5] = -1
    else:
        out[p >= t] = 1
        out[p <= 1 - t] = -1
    return out


def balanced_accuracy(y, pred) -> float:
    m = pred != 0
    y, pred = y[m], pred[m]
    rl = (pred[y == 1] == 1).mean() if (y == 1).any() else np.nan
    rs = (pred[y == -1] == -1).mean() if (y == -1).any() else np.nan
    return float((rl + rs) / 2)


def report(y: np.ndarray, p: np.ndarray) -> dict:
    pred = classify(p)
    cm = {"pred_long_true_long": int(((pred == 1) & (y == 1)).sum()), "pred_long_true_short": int(((pred == 1) & (y == -1)).sum()),
          "pred_short_true_long": int(((pred == -1) & (y == 1)).sum()), "pred_short_true_short": int(((pred == -1) & (y == -1)).sum())}
    ypos = (y == 1).astype(float)
    bins = np.clip((p * 10).astype(int), 0, 9)
    rel = [{"bin": b / 10, "n": int((bins == b).sum()), "mean_p": float(p[bins == b].mean()),
            "long_rate": float(ypos[bins == b].mean())} for b in range(10) if (bins == b).any()]
    out = {"n": int(len(y)), "long_base_rate": float(ypos.mean()), "accuracy": float((pred == y).mean()),
           "balanced_accuracy": balanced_accuracy(y, pred), "auc": auc(y, p), "brier": float(np.mean((p - ypos) ** 2)),
           "long_precision": float((y[pred == 1] == 1).mean()) if (pred == 1).any() else None,
           "short_precision": float((y[pred == -1] == -1).mean()) if (pred == -1).any() else None,
           "pred_long_share": float((pred == 1).mean()), "confusion": cm, "reliability": rel, "thresholds": {}}
    for t in THRESHOLDS:
        pr = classify(p, t)
        m = pr != 0
        out["thresholds"][str(t)] = {
            "coverage": float(m.mean()), "n": int(m.sum()),
            "accuracy": float((pr[m] == y[m]).mean()) if m.any() else None,
            "long_calls": int((pr == 1).sum()), "short_calls": int((pr == -1).sum()),
            "long_accuracy": float((y[pr == 1] == 1).mean()) if (pr == 1).any() else None,
            "short_accuracy": float((y[pr == -1] == -1).mean()) if (pr == -1).any() else None}
    return out


class DayBlockBootstrap:
    """Moving blocks of BLOCK_DAYS consecutive days; a replicate is the concatenation of sampled blocks."""

    def __init__(self, days: np.ndarray, reps: int = N_BOOT, seed: int = SEED, block: int = BLOCK_DAYS):
        d0 = days.min()
        dd = days - d0
        self.n_days = int(dd.max()) + 1
        self.by_day = [np.flatnonzero(dd == k) for k in range(self.n_days)]
        nb = math.ceil(self.n_days / block)
        rng = np.random.default_rng(seed)
        starts = rng.integers(0, self.n_days - block + 1, size=(reps, nb))
        self.samples = []
        for row in starts:
            idx = [self.by_day[s + k] for s in row for k in range(block)]
            self.samples.append(np.concatenate(idx))

    def ci(self, stat, *arrays) -> dict:
        vals = np.array([stat(*(a[s] for a in arrays)) for s in self.samples])
        vals = vals[np.isfinite(vals)]
        if len(vals) < 2:
            return {"ci95": [float("nan"), float("nan")], "se": float("nan"), "boot": vals}
        return {"ci95": [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))],
                "se": float(vals.std(ddof=1)), "boot": vals}


def one_sided_p_above(boot: np.ndarray, null: float) -> float:
    """Share of bootstrap replicates at or below the null, centred: P(stat - est + null >= est)."""
    est = float(np.mean(boot))
    centred = boot - est + null
    return float(min(1.0, (np.sum(centred >= est) + 1) / (len(boot) + 1)))
