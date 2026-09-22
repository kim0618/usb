"""Net R aggregation and the session-block bootstrap the contract declares.

The resampling unit is the trading session, not the trade. Trades inside one session share
that day's move and up to three of them are open at once, so drawing trades independently
would claim a precision the sample does not have. A drawn session brings all of its trades.

The bootstrap machinery is C-E0's (``block_indices`` for the draws, ``percentiles`` for the
interval), reused rather than rewritten so the repository has one construction. The contract
sets ``block_length`` to 1, which makes ``block_indices`` an independent resample of sessions:
C-E0 used 10 over 490 dates to carry date-to-date dependence, while this window has 84
sessions, where 10-session blocks would leave about eight effective blocks.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

from app.backtest.strategy_b_e0.contract import Statistics
from app.backtest.strategy_b_e0.gate import Interval
from app.backtest.strategy_c_e0.stats import block_indices, percentiles


@dataclass(frozen=True, slots=True)
class SessionSeries:
    """Per-session totals of one quantity: the sum of its values and how many there were."""

    sessions: tuple[date, ...]
    total: np.ndarray
    count: np.ndarray

    @property
    def n(self) -> int:
        return int(self.count.sum())

    @property
    def point(self) -> float:
        total = self.count.sum()
        return float("nan") if total == 0 else float(self.total.sum() / total)

    def draw(self, draws: np.ndarray) -> np.ndarray:
        with np.errstate(divide="ignore", invalid="ignore"):
            return self.total[draws].sum(axis=1) / self.count[draws].sum(axis=1)


def series(values_by_session: Mapping[date, Sequence[float]],
           sessions: Sequence[date]) -> SessionSeries:
    """Lay per-session values onto the full session grid, zero-filling silent days.

    A session with no value contributes 0 to the total and 0 to the count, so it dilutes
    nothing; it exists in the grid only so that resampling draws from every session the run
    covered, including the ones where the strategy did nothing.
    """
    grid = tuple(sessions)
    total = np.zeros(len(grid), dtype=float)
    count = np.zeros(len(grid), dtype=float)
    index = {day: i for i, day in enumerate(grid)}
    for day, values in values_by_session.items():
        if day not in index:
            raise ValueError(f"{day} is not one of the run's sessions")
        total[index[day]] = float(sum(values))
        count[index[day]] = len(values)
    return SessionSeries(grid, total, count)


def draws(n_sessions: int, stats: Statistics) -> np.ndarray:
    return block_indices(n_sessions, replicates=stats.replicates, seed=stats.seed,
                         block=stats.block_length)


def mean_interval(data: SessionSeries, stats: Statistics,
                  drawn: np.ndarray | None = None) -> Interval:
    """The mean and its percentile bootstrap interval, resampled by session."""
    drawn = draws(len(data.sessions), stats) if drawn is None else drawn
    lower, upper = _bounds(data.draw(drawn), stats)
    return Interval(point=data.point, lower=lower, upper=upper)


def difference_interval(left: SessionSeries, right: SessionSeries, stats: Statistics,
                        drawn: np.ndarray | None = None) -> Interval:
    """mean(left) - mean(right), differenced *inside* each draw.

    Both cohorts are resampled on the same draw, so the interval is of the difference itself.
    Subtracting two independently drawn intervals would be a different, and wrong, statistic.
    """
    if left.sessions != right.sessions:
        raise ValueError("both cohorts must be laid on the same session grid")
    drawn = draws(len(left.sessions), stats) if drawn is None else drawn
    lower, upper = _bounds(left.draw(drawn) - right.draw(drawn), stats)
    return Interval(point=left.point - right.point, lower=lower, upper=upper)


def _bounds(boot: np.ndarray, stats: Statistics) -> tuple[float, float]:
    if stats.interval_method != "PERCENTILE":
        raise ValueError(f"only the declared PERCENTILE method is implemented, "
                         f"not {stats.interval_method!r}")
    (bounds,) = percentiles(boot, levels=(stats.confidence,)).values()
    return float(bounds[0]), float(bounds[1])
