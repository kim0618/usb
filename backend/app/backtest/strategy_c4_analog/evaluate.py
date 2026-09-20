"""Selection statistics: daily rank correlation, quintiles, block bootstrap, stability, mixture.

The resampling unit is the query date, as it was in C-M, C-E0 and EQM-V0: candidates of one
session share a market, so treating them as independent draws would understate every interval.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

QUINTILES = 5


def average_rank(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="stable")
    sorted_values = values[order]
    low = np.searchsorted(sorted_values, sorted_values, side="left")
    high = np.searchsorted(sorted_values, sorted_values, side="right")
    average = (low + high - 1) / 2.0 + 1.0
    ranks = np.empty(values.size, dtype=float)
    ranks[order] = average
    return ranks


def spearman(left: np.ndarray, right: np.ndarray) -> float:
    if left.size < 2:
        return float("nan")
    a = average_rank(left)
    b = average_rank(right)
    a = a - a.mean()
    b = b - b.mean()
    denominator = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if denominator <= 0:
        return float("nan")
    return float((a * b).sum() / denominator)


@dataclass(frozen=True)
class DailySeries:
    dates: np.ndarray   # (D,) int, query date index
    values: np.ndarray  # (D,) float
    counts: np.ndarray  # (D,) int

    @property
    def mean(self) -> float:
        return float(np.mean(self.values)) if self.values.size else float("nan")


def daily_ic(forecast: np.ndarray, realized: np.ndarray, date_idx: np.ndarray,
             min_rows: int) -> DailySeries:
    dates, values, counts = [], [], []
    for day in np.unique(date_idx):
        rows = date_idx == day
        n = int(rows.sum())
        if n < min_rows:
            continue
        value = spearman(forecast[rows], realized[rows])
        if not np.isfinite(value):
            continue
        dates.append(int(day))
        values.append(value)
        counts.append(n)
    return DailySeries(np.asarray(dates, dtype=int), np.asarray(values, dtype=float),
                       np.asarray(counts, dtype=int))


def quintile_of(values: np.ndarray, tie_break: np.ndarray) -> np.ndarray:
    """0..4 by ascending forecast, ties broken by the supplied key, near-equal basket sizes."""
    order = np.lexsort((tie_break, values))
    out = np.empty(values.size, dtype=np.int8)
    edges = np.linspace(0, values.size, QUINTILES + 1).round().astype(int)
    for bucket in range(QUINTILES):
        out[order[edges[bucket]: edges[bucket + 1]]] = bucket
    return out


@dataclass(frozen=True)
class BasketStats:
    per_date: dict[str, np.ndarray]
    dates: np.ndarray


def daily_baskets(forecast: np.ndarray, date_idx: np.ndarray, tie_break: np.ndarray,
                  outcomes: dict[str, np.ndarray], min_rows: int) -> BasketStats:
    dates = []
    collected: dict[str, list[list[float]]] = {name: [] for name in outcomes}
    for day in np.unique(date_idx):
        rows = np.flatnonzero(date_idx == day)
        if rows.size < min_rows:
            continue
        bucket = quintile_of(forecast[rows], tie_break[rows])
        dates.append(int(day))
        for name, values in outcomes.items():
            collected[name].append([float(np.mean(values[rows[bucket == q]]))
                                    if np.any(bucket == q) else np.nan for q in range(QUINTILES)])
    return BasketStats({name: np.asarray(rows, dtype=float) for name, rows in collected.items()},
                       np.asarray(dates, dtype=int))


def block_draws(n_dates: int, *, block_length: int, replicates: int, seed: int) -> np.ndarray:
    if n_dates == 0:
        return np.zeros((replicates, 0), dtype=np.int64)
    generator = np.random.default_rng(seed)
    blocks = int(np.ceil(n_dates / block_length))
    starts = generator.integers(0, n_dates, size=(replicates, blocks))
    offsets = np.arange(block_length)[None, None, :]
    indices = (starts[:, :, None] + offsets) % n_dates
    return indices.reshape(replicates, -1)[:, :n_dates]


def interval(values: np.ndarray, draws: np.ndarray, alpha: float) -> tuple[float, float]:
    if values.size == 0:
        return float("nan"), float("nan")
    means = values[draws].mean(axis=1)
    low = float(np.quantile(means, (1 - alpha) / 2))
    high = float(np.quantile(means, 1 - (1 - alpha) / 2))
    return low, high


def summarize(values: np.ndarray, draws: np.ndarray, alpha: float) -> dict[str, float]:
    low, high = interval(values, draws, alpha)
    return {"point": float(np.mean(values)) if values.size else float("nan"),
            "ci_low": low, "ci_high": high, "dates": int(values.size)}


def align(left: DailySeries, right: DailySeries) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    shared = np.intersect1d(left.dates, right.dates)
    a = left.values[np.isin(left.dates, shared)]
    b = right.values[np.isin(right.dates, shared)]
    return shared, a, b


def time_blocks(dates: np.ndarray, blocks: int) -> list[np.ndarray]:
    return [np.asarray(part) for part in np.array_split(np.sort(dates), blocks)]


def concentration(labels: np.ndarray, top: int) -> dict[str, float]:
    if labels.size == 0:
        return {"single_share": float("nan"), f"top_{top}_share": float("nan")}
    _, counts = np.unique(labels, return_counts=True)
    counts = np.sort(counts)[::-1]
    return {"single_share": float(counts[0] / labels.size),
            f"top_{top}_share": float(counts[:top].sum() / labels.size)}


def top_labels(labels: np.ndarray, top: int) -> list:
    unique, counts = np.unique(labels, return_counts=True)
    order = np.argsort((-counts, unique), axis=0) if False else np.lexsort((unique, -counts))
    return [unique[i] for i in order[:top]]


def hit_rate(values: np.ndarray, threshold: float) -> float:
    return float(np.mean(values >= threshold)) if values.size else float("nan")


def basket_means(stats: BasketStats, name: str) -> np.ndarray:
    return stats.per_date[name]


def spread_series(stats: BasketStats, name: str) -> np.ndarray:
    rows = stats.per_date[name]
    return rows[:, QUINTILES - 1] - rows[:, 0]


def column_series(stats: BasketStats, name: str, quintile: int) -> np.ndarray:
    return stats.per_date[name][:, quintile]


def nan_safe(values: Sequence[float]) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    return array[np.isfinite(array)]
