"""Moving block bootstrap over evaluable dates, and the Bonferroni levels the gate reads.

Dates are the resampling unit and blocks keep neighbouring dates together, because a market's
cross-sectional IC is autocorrelated: resampling individual query rows would treat the same day's
names as independent draws and shrink every interval until anything looks significant.

D0 froze the whole recipe - block length 20 sessions, 10000 replicates, percentile intervals,
seed 20260917, and Bonferroni over the 14 primary tests. Paired comparisons against N1 and N2a
reuse one index matrix per horizon so a difference is measured on the same resampled days as the
level it is differenced from.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_d_analog.models import HardFail

BLOCK_LENGTH = 20
REPLICATES = 10_000
SEED = 20260917
FAMILY_SIZE = 14
FAMILY_ALPHA = 0.05
#: D0 ``statistics.family_wise``: two-sided 1 - 0.05/14 = 0.99643 for gate conditions.
BONFERRONI_ALPHA = FAMILY_ALPHA / FAMILY_SIZE
NOMINAL_ALPHA = 0.05


@dataclass(frozen=True)
class Interval:
    point: float
    low: float
    high: float
    level: str

    def as_dict(self) -> dict[str, float | str]:
        return {"point": self.point, "low": self.low, "high": self.high, "level": self.level}

    @property
    def excludes_zero_above(self) -> bool:
        return np.isfinite(self.low) and self.low > 0.0

    @property
    def excludes_zero_below(self) -> bool:
        return np.isfinite(self.high) and self.high < 0.0


def block_indices(date_count: int, *, horizon: int, block_length: int = BLOCK_LENGTH,
                  replicates: int = REPLICATES, seed: int = SEED) -> np.ndarray:
    """``(replicates, date_count)`` positions into the date series, drawn as overlapping blocks.

    The seed is spawned from the declared literal with the horizon, so each horizon's draws are
    the same whatever order the tests run in; tests that share a horizon share their draws, which
    is what makes the paired differences paired.
    """
    if date_count <= 0:
        raise HardFail("F4", "no evaluable dates to bootstrap")
    length = min(block_length, date_count)
    starts_available = date_count - length + 1
    blocks = int(np.ceil(date_count / length))
    generator = np.random.default_rng([seed, horizon])
    starts = generator.integers(0, starts_available, size=(replicates, blocks))
    offsets = np.arange(length)
    drawn = (starts[:, :, None] + offsets[None, None, :]).reshape(replicates, blocks * length)
    return drawn[:, :date_count]


def interval(values: np.ndarray, indices: np.ndarray, *, alpha: float,
             level: str) -> Interval:
    """Percentile interval of the mean of a per-date series under the given resample draws."""
    if values.shape[0] != indices.shape[1]:
        raise HardFail("R5", f"series of {values.shape[0]} does not match draws of "
                             f"{indices.shape[1]} dates")
    point = float(np.mean(values))
    means = np.mean(values[indices], axis=1)
    low, high = np.quantile(means, [alpha / 2.0, 1.0 - alpha / 2.0])
    return Interval(point, float(low), float(high), level)


def both_levels(values: np.ndarray, indices: np.ndarray) -> dict[str, Interval]:
    """The Bonferroni interval the gate reads, and the nominal 95% D0 also asks to report."""
    return {"bonferroni": interval(values, indices, alpha=BONFERRONI_ALPHA, level="bonferroni"),
            "nominal": interval(values, indices, alpha=NOMINAL_ALPHA, level="nominal_95")}


def paired_difference(left: np.ndarray, right: np.ndarray, indices: np.ndarray,
                      ) -> dict[str, Interval]:
    """Interval of the mean per-date difference, both series resampled on the same days."""
    if left.shape != right.shape:
        raise HardFail("R5", "paired difference needs two series of the same length")
    return both_levels(left - right, indices)


def describe(intervals: Mapping[str, Interval]) -> dict[str, dict]:
    return {name: value.as_dict() for name, value in intervals.items()}


def draw_digest(indices: np.ndarray) -> str:
    """A digest of the resample draws, so two runs can be shown to have bootstrapped identically."""
    import hashlib

    return hashlib.sha256(np.ascontiguousarray(indices.astype(np.int32)).tobytes()).hexdigest()


def block_partition(count: int, blocks: int) -> Sequence[np.ndarray]:
    return [np.asarray(part) for part in np.array_split(np.arange(count), blocks)]
