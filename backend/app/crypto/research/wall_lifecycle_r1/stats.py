"""Point estimates, moving-block bootstrap, Holm and a permutation Spearman. Pure numpy."""
from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from . import contract as K


def summary(values: Sequence[float]) -> dict[str, Any]:
    arr = np.asarray([v for v in values if v is not None and not math.isnan(v)], dtype=float)
    if arr.size == 0:
        return {"n": 0, "mean": None, "median": None, "p5": None, "p95": None, "stage": K.stage(0)}
    return {"n": int(arr.size), "mean": float(arr.mean()), "median": float(np.median(arr)),
            "p5": float(np.percentile(arr, 5)), "p95": float(np.percentile(arr, 95)),
            "stage": K.stage(int(arr.size))}


def block_bootstrap(values: Sequence[float], times_ms: Sequence[int], *, n: int = K.BOOTSTRAP_N,
                    seed: int = K.SEED, block_ms: int = K.BLOCK_MS) -> dict[str, Any]:
    """Mean with a moving-block bootstrap: blocks are the episodes inside each wall-clock block of
    `block_ms`, resampled with replacement. Two-sided p for mean = 0."""
    pairs = [(t, v) for t, v in zip(times_ms, values) if v is not None and not math.isnan(v)]
    if len(pairs) < 2:
        return {"n": len(pairs), "mean": pairs[0][1] if pairs else None, "ci95": None, "p": None}
    pairs.sort()
    blocks: dict[int, list[float]] = {}
    for t, v in pairs:
        blocks.setdefault(int(t // block_ms), []).append(v)
    keys = sorted(blocks)
    rng = np.random.default_rng(seed)
    arrays = [np.asarray(blocks[k]) for k in keys]
    means = np.empty(n)
    for i in range(n):
        pick = rng.integers(0, len(arrays), size=len(arrays))
        sample = np.concatenate([arrays[j] for j in pick])
        means[i] = sample.mean()
    mean = float(np.mean([v for _t, v in pairs]))
    lo, hi = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    share_le = float(np.mean(means <= 0))
    share_ge = float(np.mean(means >= 0))
    p = min(1.0, 2 * min(share_le, share_ge))
    return {"n": len(pairs), "mean": mean, "ci95": [lo, hi], "p": p, "blocks": len(keys)}


def holm(pvalues: dict[str, float | None]) -> dict[str, float | None]:
    """Holm-Bonferroni adjusted p per key; None stays None and does not count."""
    items = [(k, p) for k, p in pvalues.items() if p is not None]
    m = len(items)
    items.sort(key=lambda kv: kv[1])
    out: dict[str, float | None] = {k: None for k in pvalues}
    running = 0.0
    for i, (k, p) in enumerate(items):
        adj = min(1.0, (m - i) * p)
        running = max(running, adj)
        out[k] = running
    return out


def spearman_perm(x: Sequence[float], y: Sequence[float], *, n: int = K.PERMUTATIONS,
                  seed: int = K.SEED) -> dict[str, Any]:
    xs = np.asarray(x, dtype=float)
    ys = np.asarray(y, dtype=float)
    if xs.size < 3:
        return {"n": int(xs.size), "rho": None, "p": None}

    def rho(a: np.ndarray, b: np.ndarray) -> float:
        ra, rb = _rank(a), _rank(b)
        if ra.std() == 0 or rb.std() == 0:
            return 0.0
        return float(np.corrcoef(ra, rb)[0, 1])

    observed = rho(xs, ys)
    rng = np.random.default_rng(seed)
    count = 0
    for _ in range(n):
        if abs(rho(xs, rng.permutation(ys))) >= abs(observed):
            count += 1
    return {"n": int(xs.size), "rho": observed, "p": (count + 1) / (n + 1)}


def _rank(a: np.ndarray) -> np.ndarray:
    order = a.argsort()
    ranks = np.empty(a.size)
    ranks[order] = np.arange(1, a.size + 1)
    # average ties
    uniq, inv, counts = np.unique(a, return_inverse=True, return_counts=True)
    sums = np.zeros(uniq.size)
    np.add.at(sums, inv, ranks)
    return sums[inv] / counts[inv]


def quartile_bins(values: Sequence[float]) -> list[float]:
    arr = np.asarray(values, dtype=float)
    return [float(np.percentile(arr, q)) for q in (25, 50, 75)] if arr.size >= 4 else []


def concentration(nets: Sequence[float]) -> float | None:
    """Share of the total positive net held by the top CONCENTRATION_TOP_SHARE of events."""
    arr = np.sort(np.asarray([v for v in nets if v is not None], dtype=float))[::-1]
    pos = arr[arr > 0]
    if pos.size == 0:
        return None
    k = max(1, int(math.ceil(arr.size * K.CONCENTRATION_TOP_SHARE)))
    return float(pos[:k].sum() / pos.sum())


__all__ = ["summary", "block_bootstrap", "holm", "spearman_perm", "quartile_bins", "concentration"]
