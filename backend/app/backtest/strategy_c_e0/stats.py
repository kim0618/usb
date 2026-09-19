"""C-E0 statistics: cohort excess over matched controls, and the EM - M_ONLY difference.

The estimator is V1's `_diff_ci`: per signal date, sum the candidate outcome and the sum of its
cell's control mean, then divide the totals. Dates are the resampling unit, so a date's candidates
and their controls always move together. H2 subtracts the two cohort excesses **inside the same
bootstrap draw**, as declared, instead of differencing two independent intervals.
"""

from collections.abc import Sequence
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

BLOCK_LENGTH = 10
REPLICATES = 10_000
SEED = 20260918
LEVELS = (0.95, 0.99)
COST_ROUND_TRIP = 0.0025  # reference only, never a gate input


def block_indices(n_dates: int, *, replicates: int = REPLICATES, seed: int = SEED,
                  block: int = BLOCK_LENGTH) -> np.ndarray:
    """Moving block bootstrap draws over the signal dates (same construction as V1)."""
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(n_dates / block)
    starts = rng.integers(0, max(1, n_dates - block + 1), size=(replicates, n_blocks))
    return (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(replicates, -1)[:, :n_dates]


@dataclass(frozen=True)
class Series:
    """Per-date totals of one cohort at one horizon: candidate sum, control sum, count."""

    value: np.ndarray
    base: np.ndarray
    count: np.ndarray

    @property
    def point(self) -> float:
        total = self.count.sum()
        return float("nan") if total == 0 else float((self.value.sum() - self.base.sum()) / total)

    def boot(self, draws: np.ndarray) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            return ((self.value[draws].sum(axis=1) - self.base[draws].sum(axis=1))
                    / self.count[draws].sum(axis=1))


def per_date(frame: pd.DataFrame, dates: Sequence[int], column: str) -> Series:
    """Totals of `column` and `base_<column>` over matched, label-valid rows of `frame`."""
    if frame.empty:
        zeros = np.zeros(len(dates))
        return Series(zeros, zeros.copy(), zeros.copy())
    grouped = frame.groupby("date_idx")
    value = grouped[column].sum().reindex(list(dates), fill_value=0.0).to_numpy(dtype=float)
    base = grouped[f"base_{column}"].sum().reindex(list(dates), fill_value=0.0).to_numpy(dtype=float)
    count = grouped[column].size().reindex(list(dates), fill_value=0).to_numpy(dtype=float)
    return Series(value, base, count)


def percentiles(boot: np.ndarray, levels: Sequence[float] = LEVELS) -> dict[str, list[float]]:
    return {f"ci{int(round(level * 10000))}": [float(np.nanpercentile(boot, 50 * (1 - level))),
                                               float(np.nanpercentile(boot, 50 * (1 + level)))]
            for level in levels}


def excess(frame: pd.DataFrame, dates: Sequence[int], column: str, draws: np.ndarray | None,
           levels: Sequence[float] = LEVELS) -> dict:
    series = per_date(frame, dates, column)
    out: dict = {"n": int(series.count.sum()), "point": series.point,
                 "candidate_mean": _mean(series.value, series.count),
                 "control_mean": _mean(series.base, series.count)}
    if not frame.empty:
        out["median_per_candidate"] = float((frame[column] - frame[f"base_{column}"]).median())
    if draws is not None:
        out.update(percentiles(series.boot(draws), levels))
    return out


def difference(left: pd.DataFrame, right: pd.DataFrame, dates: Sequence[int], column: str,
               draws: np.ndarray | None, levels: Sequence[float] = LEVELS) -> dict:
    """left excess - right excess, both resampled on the same draw (H2 and every increment)."""
    a, b = per_date(left, dates, column), per_date(right, dates, column)
    out: dict = {"point": a.point - b.point, "left_point": a.point, "right_point": b.point,
                 "n_left": int(a.count.sum()), "n_right": int(b.count.sum())}
    if draws is not None:
        out.update(percentiles(a.boot(draws) - b.boot(draws), levels))
    return out


def _mean(total: np.ndarray, count: np.ndarray) -> float:
    n = count.sum()
    return float("nan") if n == 0 else float(total.sum() / n)


def time_blocks(dates: Sequence[int], n_blocks: int = 4) -> list[list[int]]:
    return [list(map(int, part)) for part in np.array_split(np.asarray(list(dates)), n_blocks)]


def concentration(frame: pd.DataFrame, *, top_tickers: int = 5, top_dates: int = 10) -> dict:
    """Ticker and date concentration of one cohort, as the gate's condition 8 reads it."""
    n = len(frame)
    if n == 0:
        return {"n": 0}
    by_ticker = frame["ticker"].value_counts()
    by_date = frame["date_idx"].value_counts()
    return {
        "n": int(n),
        "unique_tickers": int(frame["ticker"].nunique()),
        "unique_dates": int(frame["date_idx"].nunique()),
        "single_ticker_share": float(by_ticker.iloc[0] / n),
        "top5_ticker_share": float(by_ticker.head(top_tickers).sum() / n),
        "single_date_share": float(by_date.iloc[0] / n),
        "top10_date_share": float(by_date.head(top_dates).sum() / n),
        "top_tickers": [[str(t), int(c), float(c / n)] for t, c in by_ticker.head(10).items()],
        "top_dates": [[int(d), int(c), float(c / n)] for d, c in by_date.head(10).items()],
    }
