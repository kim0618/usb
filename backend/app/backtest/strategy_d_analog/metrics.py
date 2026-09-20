"""Cross-sectional evaluation statistics: the date-level Spearman IC, quintile baskets, blocks.

The unit of evidence is the date, not the query. A pooled correlation over every (date, ticker)
row would let a handful of wide days dominate and would treat names on the same day as
independent observations, which they are not. D0 fixes the alternative: one Spearman per date
over that date's valid queries, then an equal-weight mean over dates, and a block bootstrap that
resamples dates rather than rows.

Nothing here decides anything. The functions return numbers; ``gate.py`` compares them against
the conditions D0 froze before any of this was computed.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_d_analog.features import _average_rank
from app.backtest.strategy_d_analog.models import HardFail

QUINTILE_COUNT = 5
TERCILE_COUNT = 3


def average_rank(values: np.ndarray) -> np.ndarray:
    """1-based ranks, ties averaged. Exposed so every ranking in D4 uses one implementation."""
    return _average_rank(values)


def spearman(left: np.ndarray, right: np.ndarray) -> float:
    """Spearman rank correlation. NaN when either side has no spread to correlate."""
    if left.shape != right.shape:
        raise HardFail("R5", f"spearman got {left.shape} and {right.shape}")
    if left.shape[0] < 2:
        return float("nan")
    a, b = average_rank(left), average_rank(right)
    a = a - a.mean()
    b = b - b.mean()
    denominator = np.sqrt((a * a).sum() * (b * b).sum())
    if denominator == 0.0:
        return float("nan")
    return float((a * b).sum() / denominator)


@dataclass(frozen=True)
class DailySeries:
    """One value per evaluable date, plus the dates themselves so blocks stay chronological."""

    date_idx: np.ndarray
    value: np.ndarray

    def __len__(self) -> int:
        return int(self.date_idx.shape[0])

    @property
    def mean(self) -> float:
        return float(np.mean(self.value)) if len(self) else float("nan")

    @property
    def median(self) -> float:
        return float(np.median(self.value)) if len(self) else float("nan")

    @property
    def std(self) -> float:
        return float(np.std(self.value, ddof=1)) if len(self) > 1 else float("nan")

    @property
    def positive_ratio(self) -> float:
        return float(np.mean(self.value > 0)) if len(self) else float("nan")

    def summary(self) -> dict[str, float]:
        return {"count": len(self), "mean": self.mean, "median": self.median, "std": self.std,
                "positive_ratio": self.positive_ratio}


def quintile_baskets(signal: np.ndarray, realized: np.ndarray,
                     tie_break: np.ndarray) -> np.ndarray:
    """Mean realized return of each of five equal-count baskets, ordered by the signal.

    Splitting happens inside the date, never over the pooled history: a percentile computed over
    the whole sample would let a later regime decide which names were bullish earlier on.
    ``tie_break`` makes the split deterministic when signals repeat.
    """
    count = signal.shape[0]
    out = np.full(QUINTILE_COUNT, np.nan)
    if count < QUINTILE_COUNT:
        return out
    order = np.lexsort((tie_break, signal))
    for index, block in enumerate(np.array_split(order, QUINTILE_COUNT)):
        if block.size:
            out[index] = float(np.mean(realized[block]))
    return out


def monotonic_violations(baskets: np.ndarray) -> int:
    """How many adjacent quintile steps go the wrong way. Descriptive: D0 declares no such gate."""
    usable = baskets[np.isfinite(baskets)]
    if usable.shape[0] < 2:
        return -1
    return int(np.sum(np.diff(usable) < 0))


def basket_trend(baskets: np.ndarray) -> float:
    """Spearman of basket mean against basket index, a single number for the quintile shape."""
    usable = np.isfinite(baskets)
    if usable.sum() < 2:
        return float("nan")
    return spearman(np.nonzero(usable)[0].astype(np.float64), baskets[usable])


def bucket_by_rank(values: np.ndarray, buckets: int, tie_break: np.ndarray) -> np.ndarray:
    """Assign equal-count bucket indices 0..buckets-1 inside one date, lowest value first."""
    out = np.full(values.shape[0], -1, dtype=np.int8)
    if values.shape[0] < buckets:
        return out
    order = np.lexsort((tie_break, values))
    for index, block in enumerate(np.array_split(order, buckets)):
        out[block] = index
    return out


def time_blocks(date_idx: np.ndarray, blocks: int = 4) -> list[np.ndarray]:
    """D0 ``time_block_rule``: evaluable dates split into consecutive blocks of near-equal count."""
    if date_idx.shape[0] == 0:
        return [np.empty(0, dtype=np.int64) for _ in range(blocks)]
    order = np.argsort(date_idx, kind="stable")
    return [np.asarray(part) for part in np.array_split(order, blocks)]


def block_means(series: DailySeries, blocks: int = 4) -> list[float]:
    return [float(np.mean(series.value[part])) if part.size else float("nan")
            for part in time_blocks(series.date_idx, blocks)]


def concentration(contributions: np.ndarray, labels: Sequence, top: int = 3) -> dict:
    """How much of a total a few names or dates account for - a diagnostic, never a gate.

    D0 declares no concentration condition, so this cannot change a verdict; it is reported so a
    result that rests on two names or one fortnight is visible rather than implied.
    """
    total = float(np.sum(np.abs(contributions)))
    if total == 0.0 or contributions.shape[0] == 0:
        return {"total_abs": total, "top": []}
    order = np.argsort(-np.abs(contributions))[:top]
    return {"total_abs": total,
            "top": [{"label": str(labels[int(i)]), "value": float(contributions[int(i)]),
                     "abs_share": float(abs(contributions[int(i)]) / total)} for i in order]}
