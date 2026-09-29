"""The four FDN-V1 features and the D5 bucket rule.

All four formulas are inherited, not invented. Each one names the frozen contract it was copied
from, and `test_d6_features.py` cross-checks every formula against the D5 implementation that
produced the research results, so "verbatim" is a tested claim rather than a comment.

Every function here is trailing: the value at the last row uses that row and earlier rows only.
Feeding a longer array that ends at the same bar produces the same number, which is what the PIT
tests assert.
"""
from __future__ import annotations

import numpy as np

from .model import BarWindow, BucketAssignment, FeatureValues

DAY_BARS = 1440
MINUTE_MS = 60_000

VOL_LOW = "LOW"
VOL_MID = "MID"
VOL_HIGH = "HIGH"


def _lag(x: np.ndarray, k: int) -> np.ndarray:
    """D5 features._lag, copied."""
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:-k] if k else x
    return out


def _rsum(x: np.ndarray, w: int) -> np.ndarray:
    """D5 features._rsum, copied. Trailing sum over t-w+1..t, NaN until the window is full."""
    c = np.concatenate([[0.0], np.cumsum(x)])
    out = np.full(len(x), np.nan)
    if w <= len(x):
        out[w - 1:] = c[w:] - c[:-w]
    return out


def _rstd(x: np.ndarray, w: int) -> np.ndarray:
    """D5 features._rstd, copied. Population std (ddof 0), NaN unless all w rows are finite."""
    x0 = np.nan_to_num(x)
    valid = _rsum(np.isfinite(x).astype(float), w) == w
    m = _rsum(x0, w) / w
    v = _rsum(x0 * x0, w) / w - m * m
    out = np.sqrt(np.clip(v, 0, None))
    out[~valid] = np.nan
    return out


def _clean(x: np.ndarray) -> np.ndarray:
    """D5 compute_features finishes by turning every non-finite value into NaN."""
    x = np.asarray(x, dtype=float)
    x[~np.isfinite(x)] = np.nan
    return x


def f_basis(close: np.ndarray, index_close: np.ndarray) -> np.ndarray:
    """ln(C[t] / IDX[t]). D5 contract sec6 E3 `fund_premium`."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return _clean(np.log(np.asarray(close, float)) - np.log(np.asarray(index_close, float)))


def f_oi1h(oi: np.ndarray) -> np.ndarray:
    """ln(OI[t] / OI[t-60]). D5 contract sec6 D1 `oi_chg_60`."""
    with np.errstate(divide="ignore", invalid="ignore"):
        ln_oi = np.log(np.asarray(oi, float))
    return _clean(ln_oi - _lag(ln_oi, 60))


def f_drop1h(close: np.ndarray) -> np.ndarray:
    """ln(C[t] / C[t-60]). D5 contract sec6 A1 `trend_ret_60`."""
    with np.errstate(divide="ignore", invalid="ignore"):
        ln_c = np.log(np.asarray(close, float))
    return _clean(ln_c - _lag(ln_c, 60))


def f_rv24h(close: np.ndarray) -> np.ndarray:
    """std of 1m log returns over t-1439..t, ddof 0. D5 contract sec10 volatility regime.

    Note the input depth: 1440 returns need 1441 closes. The contract states the lookback as
    1440 bars of returns, which is the same window D5 used.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        ln_c = np.log(np.asarray(close, float))
    return _clean(_rstd(ln_c - _lag(ln_c, 1), DAY_BARS))


def compute_series(window: BarWindow) -> dict[str, np.ndarray]:
    """All four features over the whole window. Row i uses rows <= i only."""
    return {
        "f_basis": f_basis(window.close, window.index_close),
        "f_oi1h": f_oi1h(window.oi),
        "f_drop1h": f_drop1h(window.close),
        "f_rv24h": f_rv24h(window.close),
    }


def compute_at_t(window: BarWindow) -> FeatureValues:
    """The four values at bar t, which is the last row of the window."""
    series = compute_series(window)
    return FeatureValues(
        f_basis=float(series["f_basis"][-1]),
        f_oi1h=float(series["f_oi1h"][-1]),
        f_drop1h=float(series["f_drop1h"][-1]),
        f_rv24h=float(series["f_rv24h"][-1]),
    )


def bucket_cutoffs(history: np.ndarray, quantiles: tuple[float, ...],
                   min_valid_fraction: float, window_bars: int) -> tuple[np.ndarray | None, float]:
    """D5 features.bucketize, stated for one day instead of the whole array.

    `history` is the feature over the previous `window_days` UTC days. D5 takes the finite values
    of that block, refuses when fewer than half the slots are finite, and otherwise takes plain
    `np.quantile`. Returns the cutoffs and the observed valid fraction (for H3 to report).

    The denominator is the *nominal* window (`window_bars`), not the length of whatever was
    handed in. D5 compares against the fixed 30-day width, so a window truncated by the start of
    the data set is refused there and has to be refused here too.
    """
    history = np.asarray(history, dtype=float)
    finite = history[np.isfinite(history)]
    valid_fraction = len(finite) / window_bars if window_bars else 0.0
    if valid_fraction < min_valid_fraction:
        return None, valid_fraction
    return np.quantile(finite, list(quantiles)), valid_fraction


def assign_bucket(value: float, cutoffs: np.ndarray | None,
                  valid_fraction: float = 1.0) -> BucketAssignment:
    """B1..B5 with D5's boundary convention: `searchsorted(cut, v, side="right")`.

    That convention makes B1 strictly below the 10th percentile; a value exactly on a cutoff
    falls into the bucket above it. Reproduced rather than reasoned about.
    """
    if cutoffs is None or not np.isfinite(value):
        return BucketAssignment(bucket=None, cutoffs=None if cutoffs is None
                                else tuple(float(c) for c in cutoffs),
                                valid_fraction=valid_fraction)
    idx = int(np.searchsorted(cutoffs, value, side="right"))
    return BucketAssignment(bucket=idx + 1, cutoffs=tuple(float(c) for c in cutoffs),
                            valid_fraction=valid_fraction)


def volatility_label(rv: float, low_below: float, high_above: float) -> str | None:
    """D5 features.regime_labels: HIGH when rv > hi, LOW when rv < lo, MID in between."""
    if not np.isfinite(rv):
        return None
    if rv > high_above:
        return VOL_HIGH
    if rv < low_below:
        return VOL_LOW
    return VOL_MID


def day_index(ts_ms: int) -> int:
    """UTC day number, the unit D5's bucket window is cut on."""
    return int(ts_ms // (DAY_BARS * MINUTE_MS))


def previous_days_slice(ts_ms: np.ndarray, decision_ts_ms: int, window_days: int) -> slice:
    """Rows of the previous `window_days` complete UTC days before the decision bar's day.

    D5 forms the cutoffs for UTC day D from `[D-30d, D)`, so the decision bar's own day is
    excluded. On the gap-free 1m grid this is a contiguous block.
    """
    today = day_index(decision_ts_ms)
    start_ms = (today - window_days) * DAY_BARS * MINUTE_MS
    end_ms = today * DAY_BARS * MINUTE_MS
    lo = int(np.searchsorted(ts_ms, start_ms, side="left"))
    hi = int(np.searchsorted(ts_ms, end_ms, side="left"))
    return slice(lo, hi)
