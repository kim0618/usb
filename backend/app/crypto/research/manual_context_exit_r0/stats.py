"""Statistics of contract section 8: sample gates, block bootstrap, Holm.

Deterministic: every bootstrap draws from its own generator seeded with SEED and a stable label,
so a result does not depend on the order in which cells are computed.
"""
from __future__ import annotations

import hashlib
import math
from typing import Sequence

import numpy as np

SEED = 20261011
RESAMPLES = 2_000


def gate(n: int) -> str:
    if n < 20:
        return "INSUFFICIENT"
    if n < 50:
        return "EARLY"
    if n < 100:
        return "REVIEW"
    return "STRONGER"


def _rng(label: str) -> np.random.Generator:
    h = int(hashlib.sha256(f"{SEED}:{label}".encode()).hexdigest()[:16], 16)
    return np.random.default_rng(h)


def block_sums(blocks: np.ndarray, columns: np.ndarray, label: str,
               resamples: int = RESAMPLES, chunk: int = 100) -> np.ndarray:
    """Block bootstrap of column sums: whole blocks are resampled with replacement and the
    columns (n x c) are summed over the resample. Returns (resamples x c). Any statistic that is a
    function of sums (means, rates, differences of them) is then computed per draw."""
    columns = np.asarray(columns, float)
    if columns.ndim == 1:
        columns = columns[:, None]
    ids, inverse = np.unique(np.asarray(blocks), return_inverse=True)
    k = len(ids)
    per_block = np.zeros((k, columns.shape[1]))
    np.add.at(per_block, inverse, columns)
    rng = _rng(label)
    out = np.empty((resamples, columns.shape[1]))
    for start in range(0, resamples, chunk):
        m = min(chunk, resamples - start)
        w = rng.multinomial(k, np.full(k, 1.0 / k), size=m) if k else np.zeros((m, 0))
        out[start:start + m] = w @ per_block
    return out


def mean_diff(values: np.ndarray, in_a: np.ndarray, in_b: np.ndarray, blocks: np.ndarray,
              label: str) -> dict:
    """mean(values | a) - mean(values | b) with a block bootstrap over `blocks`."""
    v = np.where(np.isfinite(values), values, 0.0)
    a = in_a & np.isfinite(values)
    b = in_b & np.isfinite(values)
    cols = np.column_stack([v * a, a, v * b, b])
    point = (cols[:, 0].sum() / cols[:, 1].sum() - cols[:, 2].sum() / cols[:, 3].sum()
             if cols[:, 1].sum() and cols[:, 3].sum() else float("nan"))
    if not np.isfinite(point):
        return summarize(point, np.array([]))
    d = block_sums(blocks, cols, label)
    with np.errstate(invalid="ignore", divide="ignore"):
        draws = d[:, 0] / d[:, 1] - d[:, 2] / d[:, 3]
    return summarize(point, draws[np.isfinite(draws)])


def mean_ci(values: np.ndarray, blocks: np.ndarray, label: str) -> dict:
    v = np.asarray(values, float)
    ok = np.isfinite(v)
    if not ok.any():
        return summarize(float("nan"), np.array([]))
    cols = np.column_stack([np.where(ok, v, 0.0), ok])
    d = block_sums(blocks, cols, label)
    with np.errstate(invalid="ignore", divide="ignore"):
        draws = d[:, 0] / d[:, 1]
    return summarize(float(v[ok].mean()), draws[np.isfinite(draws)])


def summarize(point: float, draws: np.ndarray) -> dict:
    if len(draws) == 0 or not np.isfinite(point):
        return {"point": None if not np.isfinite(point) else round(float(point), 4),
                "ci95": None, "p_two_sided": None}
    lo, hi = np.percentile(draws, [2.5, 97.5])
    p = 2.0 * min(float(np.mean(draws <= 0)), float(np.mean(draws >= 0)))
    return {"point": round(float(point), 4), "ci95": [round(float(lo), 4), round(float(hi), 4)],
            "p_two_sided": round(min(1.0, p), 6)}


def holm(pvalues: Sequence[float | None]) -> list[float | None]:
    """Holm step-down adjusted p-values. None (not testable) stays None and is not counted."""
    idx = [i for i, p in enumerate(pvalues) if p is not None]
    m = len(idx)
    order = sorted(idx, key=lambda i: pvalues[i])
    adj: list[float | None] = [None] * len(pvalues)
    running = 0.0
    for rank, i in enumerate(order):
        val = min(1.0, (m - rank) * float(pvalues[i]))
        running = max(running, val)
        adj[i] = round(running, 6)
    return adj


def median(x: np.ndarray) -> float | None:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    return round(float(np.median(x)), 4) if len(x) else None


def mean(x: np.ndarray) -> float | None:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    return round(float(np.mean(x)), 4) if len(x) else None


def pct(x: np.ndarray, q: float) -> float | None:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    return round(float(np.percentile(x, q)), 4) if len(x) else None


def rate(flags: np.ndarray) -> float | None:
    flags = np.asarray(flags, bool)
    return round(float(flags.mean()), 4) if len(flags) else None


def finite_or_none(x: float) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), 4)
