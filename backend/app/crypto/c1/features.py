"""The three C1 conditions, computed the way the frozen study computes them.

The study is numpy over a 2.9 M row grid; this is plain Python over a rolling buffer, because
`app/crypto` is deployed as a self-contained snapshot that depends on nothing but the standard
library, fastapi, httpx and websockets. The arithmetic is deliberately transcribed rather than
rewritten, including two details that look like quirks and are not:

* `quantile` reproduces numpy's two-sided lerp (`a + (b-a)t` below the midpoint, `b - (b-a)(1-t)`
  above it). A cutoff that differed in the last bit would put bars in the wrong bucket.
* `rolling_std` keeps the study's `sum(x^2)/w - mean^2` form rather than a numerically better
  two-pass variance, for the same reason.

The one place where an exact transcription is impossible: the study's rolling sums are
differences of a `cumsum` taken from the start of the research grid, so its 24 h volatility
carries the rounding of every minute since 2021-02-01. A live engine holds a bounded buffer and
cannot reproduce that. Measured over the whole research window the gap between the two is at most
5.1e-14 while the closest the volatility ever comes to its cutoff is 9.1e-10, so no bar's regime
can turn on it - and `test_c1_parity.py` asserts that on the real series rather than trusting the
argument.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

from .contract import (
    B1_QUANTILE, BUCKET_MIN_VALID_SHARE, BUCKET_QUANTILES, CARRY_FORWARD_MAX_BARS, DAY_BARS,
    FIVE_MIN_MS, MINUTE_MS, NORM_DAYS, OI_CHANGE_LAG_MIN, VOL_REGIME_HIGH_CUTOFF,
    VOL_WINDOW_BARS,
)

NAN = float("nan")


def is_nan(value: float | None) -> bool:
    return value is None or value != value


def five_min_boundary(bar_open_ms: int) -> int:
    """The 5m bar a decision at the close of this 1m bar is allowed to see.

    Contract section 2: `floor((ts[t] + 60s) / 5m) * 5m`, the boundary of the last 5m bar that had
    already ended when bar t closed. Never the 5m bar still forming.
    """
    return ((bar_open_ms + MINUTE_MS) // FIVE_MIN_MS) * FIVE_MIN_MS


def five_min_closes(bars: Iterable[tuple[int, float]]) -> dict[int, float]:
    """5m close per boundary T: the close of the last 1m bar opening in [T - 5m, T).

    Keyed by T, the boundary at the *end* of the five minutes, which is the stamp the contract's
    feature grid uses. Bars are grouped rather than scanned per boundary so the cost is linear in
    the number of bars instead of bars x boundaries.
    """
    best: dict[int, tuple[int, float]] = {}
    for ts, close in bars:
        if is_nan(close):
            continue
        boundary = ts - ts % FIVE_MIN_MS + FIVE_MIN_MS
        held = best.get(boundary)
        if held is None or ts > held[0]:
            best[boundary] = (ts, close)
    return {boundary: value for boundary, (_, value) in best.items()}


def five_min_close(bars: Sequence[tuple[int, float]], boundary_ms: int) -> float:
    """One boundary's 5m close. The readable statement of the rule `five_min_closes` applies in
    bulk; the tests hold both against each other."""
    best_ts, best_close = None, NAN
    low = boundary_ms - FIVE_MIN_MS
    for ts, close in bars:
        if low <= ts < boundary_ms and not is_nan(close) and (best_ts is None or ts > best_ts):
            best_ts, best_close = ts, close
    return best_close


def carry_one(values: Sequence[float]) -> list[float]:
    """Contract section 2: a missing 5m value may take the previous bar's value once.

    Transcribed from the study's `carry_one`, which fills from the *original* previous value, so
    two missing bars in a row leave the second one missing instead of chaining.
    """
    out = list(values)
    for index in range(1, len(out)):
        if is_nan(out[index]) and not is_nan(values[index - 1]):
            out[index] = values[index - 1]
    for index in range(len(out)):
        if is_nan(out[index]):
            out[index] = NAN
    return out


def spot_basis(perp_close: float, spot_close: float) -> float:
    """S1 = ln(bybit perp last / binance spot), as two logs subtracted, like the study."""
    if is_nan(perp_close) or is_nan(spot_close) or perp_close <= 0 or spot_close <= 0:
        return NAN
    return math.log(perp_close) - math.log(spot_close)


def _lerp(low: float, high: float, frac: float) -> float:
    """numpy's `_lerp`: it switches to interpolating down from `high` at the midpoint."""
    diff = high - low
    if frac >= 0.5:
        return high - diff * (1.0 - frac)
    return low + diff * frac


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """`numpy.quantile(..., method="linear")` on an already sorted sequence."""
    count = len(sorted_values)
    if count == 0:
        return NAN
    if count == 1:
        return float(sorted_values[0])
    position = q * (count - 1)
    low_index = math.floor(position)
    if low_index >= count - 1:
        return float(sorted_values[-1])
    return _lerp(float(sorted_values[low_index]), float(sorted_values[low_index + 1]),
                 position - low_index)


def bucket_cutoffs(window: Iterable[float]) -> tuple[float, ...] | None:
    """The four bucket edges for one UTC day, from the previous `NORM_DAYS` whole days.

    Contract section 5: the window is the preceding 30 UTC days of the same 1m series and nothing
    else, and a window less than half populated produces no bucket at all rather than a cutoff
    drawn from whatever happened to be there.
    """
    finite = sorted(value for value in window if not is_nan(value))
    if len(finite) < NORM_DAYS * DAY_BARS * BUCKET_MIN_VALID_SHARE:
        return None
    return tuple(quantile(finite, q) for q in BUCKET_QUANTILES)


def bucket_index(value: float, cutoffs: Sequence[float] | None) -> int:
    """Bucket 0..4, or -1 for no bucket. `numpy.searchsorted(cutoffs, value, side="right")`."""
    if cutoffs is None or is_nan(value):
        return -1
    index = 0
    for cutoff in cutoffs:
        if value >= cutoff:
            index += 1
        else:
            break
    return index


def in_b1(value: float, cutoffs: Sequence[float] | None) -> bool:
    """Condition 1. B1 is *strictly* below the 10th percentile, which is what `side="right"`
    means here: a value sitting exactly on the cutoff lands in B2."""
    return bucket_index(value, cutoffs) == 0


def log_returns(closes: Sequence[float]) -> list[float]:
    """ln(close[t]) - ln(close[t-1]); the first row has no predecessor."""
    out = [NAN]
    for index in range(1, len(closes)):
        now, previous = closes[index], closes[index - 1]
        if is_nan(now) or is_nan(previous) or now <= 0 or previous <= 0:
            out.append(NAN)
        else:
            out.append(math.log(now) - math.log(previous))
    return out


def rolling_std(returns: Sequence[float], window: int = VOL_WINDOW_BARS) -> float:
    """Trailing standard deviation of the last `window` returns, ddof 0, or NaN.

    The study's `_rstd` requires every row of the window to be finite - it does not drop gaps and
    divide by what is left - so one missing minute in the last 24 h leaves the regime unknown.
    """
    if len(returns) < window:
        return NAN
    tail = returns[-window:]
    total = 0.0
    total_sq = 0.0
    for value in tail:
        if is_nan(value):
            return NAN
        total += value
        total_sq += value * value
    mean = total / window
    variance = total_sq / window - mean * mean
    return math.sqrt(variance) if variance > 0 else 0.0


def vol_regime(rv: float) -> str:
    """Condition 3. Only HIGH matters to C1; the other labels are returned for the snapshot.

    The cutoff is the frozen F1-train figure and is never re-estimated from recent data: doing so
    would make a live HIGH mean something different from the HIGH the study measured.
    """
    if is_nan(rv):
        return "UNKNOWN"
    if rv > VOL_REGIME_HIGH_CUTOFF:
        return "HIGH"
    from .contract import VOL_CUTOFF_LO_F1_TRAIN
    if rv < VOL_CUTOFF_LO_F1_TRAIN:
        return "LOW"
    return "MID"


def oi_log_change(oi_series: Sequence[float], lag: int = OI_CHANGE_LAG_MIN) -> float:
    """Condition 2's input: ln(OI[t] / OI[t - lag]) on the 1m grid, NaN when either end is absent.

    `oi_series` is the point-in-time OI aligned to the 1m grid (a 5m record stamped T counts as
    known only at T + 5 min), ascending, with the current bar last.
    """
    if len(oi_series) <= lag:
        return NAN
    now, before = oi_series[-1], oi_series[-1 - lag]
    if is_nan(now) or is_nan(before) or now <= 0 or before <= 0:
        return NAN
    return math.log(now / before)


def oi_falling(change: float) -> bool:
    """Strictly below zero. Five bars in the research window sit at exactly zero and the study's
    `< 0` excludes them, so an unchanged open interest is not a falling one."""
    return not is_nan(change) and change < 0.0


class RollingMoments:
    """The 24 h volatility, kept across consecutive bars instead of resummed on every one.

    `rolling_std` sums its window from scratch, which is 1,440 additions per bar; replaying five
    years of minutes that way takes hours. This holds the window's sum and sum of squares and
    slides them, and resyncs from `rolling_std` every `resync_every` bars so the incremental error
    can never accumulate beyond that many updates. The resync is what makes it safe to use the
    fast path at all - without it a long run drifts and nothing says by how much.
    """

    def __init__(self, returns: Sequence[float], window: int = VOL_WINDOW_BARS,
                 resync_every: int = VOL_WINDOW_BARS) -> None:
        self.returns = returns
        self.window = window
        self.resync_every = max(1, resync_every)
        self._index: int | None = None
        self._total = 0.0
        self._total_sq = 0.0
        self._nan = 0
        self._since_resync = 0

    def _resync(self, index: int) -> None:
        tail = self.returns[index + 1 - self.window: index + 1]
        self._total = 0.0
        self._total_sq = 0.0
        self._nan = 0
        for value in tail:
            if is_nan(value):
                self._nan += 1
            else:
                self._total += value
                self._total_sq += value * value
        self._index = index
        self._since_resync = 0

    def at(self, index: int) -> float:
        """Standard deviation of returns[index-window+1 .. index], ddof 0, or NaN."""
        if index + 1 < self.window:
            return NAN
        if self._index is None or index != self._index + 1 or self._since_resync >= self.resync_every:
            self._resync(index)
        else:
            entering = self.returns[index]
            leaving = self.returns[index - self.window]
            if is_nan(entering):
                self._nan += 1
            else:
                self._total += entering
                self._total_sq += entering * entering
            if is_nan(leaving):
                self._nan -= 1
            else:
                self._total -= leaving
                self._total_sq -= leaving * leaving
            self._index = index
            self._since_resync += 1
        if self._nan:
            return NAN
        mean = self._total / self.window
        variance = self._total_sq / self.window - mean * mean
        return math.sqrt(variance) if variance > 0 else 0.0
