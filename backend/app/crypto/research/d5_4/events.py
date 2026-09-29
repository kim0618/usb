"""Features, robust z normalisation, and event detection with merging.

The one idea that separates this from D5 is that a *state* lasting thirty minutes is one event,
not thirty signals. Everything downstream counts episodes, so the merge rule lives here and is
tested directly rather than assumed.

Normalisation is a robust z rather than D5's decile buckets. A decile cannot resolve a tail, and
this research is entirely about tails: the median event displacement has to clear six times the
round-trip cost before a family is allowed into a backtest at all.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..d6 import features as F

DAY_BARS = 1440
MINUTE_MS = 60_000
MAD_TO_SIGMA = 1.4826


def lag(x: np.ndarray, k: int) -> np.ndarray:
    """D6 features._lag, copied. Trailing shift; the first k rows are NaN."""
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:-k] if k else x
    return out


def compute_features(grid: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The six contracted features. Row t uses rows <= t only."""
    with np.errstate(divide="ignore", invalid="ignore"):
        ln_c = np.log(np.asarray(grid["close"], float))
        ln_oi = np.log(np.asarray(grid["oi"], float))
        ln_idx = np.log(np.asarray(grid["index_close"], float))
    r1 = ln_c - lag(ln_c, 1)
    rv15 = F._rstd(r1, 15)
    rv1440 = F._rstd(r1, DAY_BARS)
    with np.errstate(divide="ignore", invalid="ignore"):
        volratio = rv15 / rv1440
    return {
        "disp15": F._clean(ln_c - lag(ln_c, 15)),
        "oichg15": F._clean(ln_oi - lag(ln_oi, 15)),
        "basis": F._clean(ln_c - ln_idx),
        "rv15": F._clean(rv15),
        "rv1440": F._clean(rv1440),
        "volratio": F._clean(volratio),
    }


def robust_z(x: np.ndarray, ts: np.ndarray, *, window_days: int = 30,
             min_valid_fraction: float = 0.5) -> np.ndarray:
    """Per UTC day, median and MAD from the previous `window_days` complete days.

    The PIT structure is D5's bucketize verbatim - the day being scored contributes nothing to
    its own cutoffs. Only the statistic differs: median and MAD instead of quantiles, because a
    quantile of a 30 day window cannot describe a ten sigma move.
    """
    x = np.asarray(x, dtype=float)
    out = np.full(len(x), np.nan)
    if len(ts) == 0:
        return out
    day_ms = DAY_BARS * MINUTE_MS
    first = int(ts[0] // day_ms)
    last = int(ts[-1] // day_ms)
    needed = window_days * DAY_BARS * min_valid_fraction
    for day in range(first + window_days, last + 1):
        lo = int(np.searchsorted(ts, (day - window_days) * day_ms, side="left"))
        hi = int(np.searchsorted(ts, day * day_ms, side="left"))
        nxt = int(np.searchsorted(ts, (day + 1) * day_ms, side="left"))
        history = x[lo:hi]
        history = history[np.isfinite(history)]
        if len(history) < needed:
            continue
        median = np.median(history)
        mad = np.median(np.abs(history - median))
        if mad <= 0:
            continue
        out[hi:nxt] = (x[hi:nxt] - median) / (MAD_TO_SIGMA * mad)
    return out


@dataclass(frozen=True)
class Event:
    start: int          # bar index where the condition first held
    end: int            # last bar of the consecutive run
    displacement: float # |disp15| at the start bar, the event's own size


def merge(mask: np.ndarray, cooldown_bars: int) -> list[Event]:
    """Consecutive true bars become one event; the next one waits out the cooldown.

    Returns events in time order. `displacement` is filled by the caller, which knows the
    feature; keeping this function about the merge alone is what makes it testable in isolation.
    """
    indices = np.nonzero(mask)[0]
    events: list[Event] = []
    if len(indices) == 0:
        return events
    available_from = -1
    position = 0
    while position < len(indices):
        start = int(indices[position])
        end = start
        while position + 1 < len(indices) and indices[position + 1] == indices[position] + 1:
            position += 1
            end = int(indices[position])
        if start >= available_from:
            events.append(Event(start=start, end=end, displacement=float("nan")))
            available_from = end + 1 + cooldown_bars
        position += 1
    return events


@dataclass
class CandidateSignals:
    """Everything one candidate needs: its events and the per-bar values behind them."""

    candidate_id: str
    side: str
    events: list[Event]
    features: dict[str, np.ndarray]
    z: dict[str, np.ndarray]
    valid: np.ndarray


def condition_mask(z: dict[str, np.ndarray], features: dict[str, np.ndarray],
                   condition: dict[str, float]) -> np.ndarray:
    """Build the event condition from the contract's own `condition` block.

    Keys are read literally, so a condition the contract does not state cannot appear here by
    accident: `z_<feature>_max`, `z_<feature>_min`, `<feature>_min`, `<feature>_max`.
    """
    mask = np.ones(len(next(iter(features.values()))), dtype=bool)
    for key, value in condition.items():
        if key.startswith("z_"):
            name = key[2:].rsplit("_", 1)[0]
            series = z[name]
        else:
            name = key.rsplit("_", 1)[0]
            series = features[name]
        if key.endswith("_max"):
            mask &= series <= value
        elif key.endswith("_min"):
            mask &= series >= value
        else:
            raise ValueError(f"condition key must end in _min or _max: {key}")
    return mask


def build(grid: dict[str, np.ndarray], candidate: dict, *, cooldown_bars: int,
          window_days: int = 30, threshold_override: dict[str, float] | None = None
          ) -> CandidateSignals:
    """Detect and merge one candidate's events over the whole grid."""
    features = compute_features(grid)
    z = {name: robust_z(features[name], grid["ts"], window_days=window_days)
         for name in ("disp15", "oichg15", "basis")}
    valid = np.isfinite(features["disp15"]) & np.isfinite(features["volratio"])
    for series in z.values():
        valid &= np.isfinite(series)

    condition = dict(candidate["condition"])
    if threshold_override:
        condition.update(threshold_override)
    mask = condition_mask(z, features, condition) & valid

    events = merge(mask, cooldown_bars)
    events = [Event(start=e.start, end=e.end,
                    displacement=abs(float(features["disp15"][e.start]))) for e in events]
    return CandidateSignals(candidate_id=candidate["id"], side=candidate["side"],
                            events=events, features=features, z=z, valid=valid)
