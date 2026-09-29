"""Rolling window primitives, all backward-looking unless the name says forward.

Every function here is the place a leak would hide, so each one states which bars it reads.
Index i always refers to the bar that opens at ts[i] and closes at ts[i] + 60s, and a backward
window ending at i includes bar i itself, because bar i has closed by the time a decision is
taken at its close.
"""
from __future__ import annotations

import numpy as np


def window_max(x: np.ndarray, w: int) -> np.ndarray:
    """out[i] = max(x[i : i+w]), length len(x) - w + 1.

    Sparse-table doubling: log2(w) vectorised passes instead of a Python loop over bars.
    """
    if w < 1:
        raise ValueError("window must be >= 1")
    if w > len(x):
        return np.empty(0, dtype=x.dtype)
    m = x
    size = 1
    while size * 2 <= w:
        m = np.maximum(m[:-size], m[size:])
        size *= 2
    rest = w - size
    if rest:
        m = np.maximum(m[:len(m) - rest], m[rest:])
    return m


def window_min(x: np.ndarray, w: int) -> np.ndarray:
    """out[i] = min(x[i : i+w])."""
    return -window_max(-x, w)


def forward_max(x: np.ndarray, h: int) -> np.ndarray:
    """out[i] = max(x[i+1 : i+1+h]), NaN where the window runs past the end.

    The window starts at i+1: a decision taken at the close of bar i cannot be filled by bar i.
    """
    out = np.full(len(x), np.nan)
    m = window_max(x, h)               # m[j] = max(x[j : j+h])
    out[:len(m) - 1] = m[1:]           # out[i] = m[i+1]
    return out


def forward_min(x: np.ndarray, h: int) -> np.ndarray:
    """out[i] = min(x[i+1 : i+1+h])."""
    out = np.full(len(x), np.nan)
    m = window_min(x, h)
    out[:len(m) - 1] = m[1:]
    return out


def shift(x: np.ndarray, k: int) -> np.ndarray:
    """out[i] = x[i-k] for k > 0, NaN before that. Backward only."""
    if k <= 0:
        raise ValueError("shift is backward only; k must be > 0")
    out = np.full(len(x), np.nan)
    out[k:] = x[:-k]
    return out


def rolling_mean(x: np.ndarray, w: int) -> np.ndarray:
    """out[i] = mean(x[i-w+1 : i+1]), NaN until the window is full.

    Uses a cumulative sum, which is why callers pass small-magnitude series (log returns, basis
    in bp) rather than raw prices: a cumsum over 3M large floats loses precision.
    """
    out = np.full(len(x), np.nan)
    if w > len(x):
        return out
    c = np.concatenate(([0.0], np.cumsum(x, dtype=np.float64)))
    out[w - 1:] = (c[w:] - c[:-w]) / w
    return out


def rolling_std(x: np.ndarray, w: int) -> np.ndarray:
    """Population standard deviation over a backward window."""
    out = np.full(len(x), np.nan)
    if w > len(x):
        return out
    c = np.concatenate(([0.0], np.cumsum(x, dtype=np.float64)))
    c2 = np.concatenate(([0.0], np.cumsum(np.square(x, dtype=np.float64))))
    mean = (c[w:] - c[:-w]) / w
    var = (c2[w:] - c2[:-w]) / w - mean * mean
    out[w - 1:] = np.sqrt(np.maximum(var, 0.0))
    return out


def rolling_max(x: np.ndarray, w: int) -> np.ndarray:
    """out[i] = max(x[i-w+1 : i+1])."""
    out = np.full(len(x), np.nan)
    m = window_max(x, w)
    out[w - 1:] = m
    return out


def rolling_min(x: np.ndarray, w: int) -> np.ndarray:
    """out[i] = min(x[i-w+1 : i+1])."""
    out = np.full(len(x), np.nan)
    m = window_min(x, w)
    out[w - 1:] = m
    return out
