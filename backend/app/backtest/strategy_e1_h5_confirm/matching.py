"""Coarsened exact matching: is H5 a premarket effect, or is it small-and-cheap?

E1 reported that H5's mean rose steeply as price and size fell, and that its baseline carried
the same gradient. This module answers the question that raises, deterministically and with no
fitted model: compare every H5 observation with the non-H5 observations that share its price,
previous-day liquidity, premarket liquidity and gap-size bucket.

Two estimators, both declared before any return was read:

* **Primary (session demeaned).** Each row's return first has its own session's universe mean
  subtracted, which removes the market-wide move of that morning; matching then happens across
  sessions inside a cell. This keeps the sample large enough to say anything.
* **Secondary (same session).** The control pool is restricted to the same session and nothing is
  demeaned. Stricter, much smaller, and reported with its own match rate; it never overturns the
  primary.

An H5 row whose cell holds no control is dropped and counted. A match rate is not a detail here:
if most H5 rows have no comparable non-H5 row, that is itself the finding.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight.stats import bucket_index

CELL_VARIABLES = ("price_bucket", "previous_day_dollar_volume_bucket",
                  "premarket_dollar_volume_bucket", "premarket_gap_bucket")
#: Feature each cell variable is cut from.
CELL_SOURCE = {"price_bucket": "close_price",
               "previous_day_dollar_volume_bucket": "previous_day_dollar_volume",
               "premarket_dollar_volume_bucket": "premarket_dollar_volume",
               "premarket_gap_bucket": "premarket_gap"}


def cell_ids(features: Mapping[str, np.ndarray], edges: Mapping[str, Sequence[float | None]],
             ) -> tuple[np.ndarray, np.ndarray]:
    """A single integer per row identifying its match cell; ``-1`` where any variable is outside."""
    parts = []
    valid = np.ones(next(iter(features.values())).shape, dtype=bool)
    for name in CELL_VARIABLES:
        index = bucket_index(features[CELL_SOURCE[name]], tuple(edges[name]))
        valid &= index >= 0
        parts.append(index)
    ids = np.zeros(valid.shape, dtype=np.int64)
    for index in parts:
        ids = ids * 16 + np.maximum(index, 0)
    ids = np.where(valid, ids, -1)
    return ids, valid


@dataclass
class MatchResult:
    name: str
    h5_rows: int
    matched_rows: int
    unmatched_rows: int
    control_rows: int
    cells_used: int
    mean_lift: float
    median_lift: float
    win_rate_lift: float
    h5_mean: float
    control_mean: float
    downside_ratio: float
    differences: np.ndarray = field(repr=False, default_factory=lambda: np.array([]))
    matched_symbols: np.ndarray = field(repr=False, default_factory=lambda: np.array([]))
    matched_sessions: np.ndarray = field(repr=False, default_factory=lambda: np.array([]))

    @property
    def match_rate(self) -> float:
        return self.matched_rows / self.h5_rows if self.h5_rows else float("nan")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "h5_rows": self.h5_rows, "matched_rows": self.matched_rows,
                "unmatched_rows": self.unmatched_rows, "control_rows": self.control_rows,
                "cells_used": self.cells_used, "match_rate": self.match_rate,
                "mean_lift": self.mean_lift, "median_lift": self.median_lift,
                "win_rate_lift": self.win_rate_lift, "h5_mean": self.h5_mean,
                "control_mean": self.control_mean, "downside_ratio": self.downside_ratio}


def _empty(name: str, h5_rows: int) -> MatchResult:
    nan = float("nan")
    return MatchResult(name, h5_rows, 0, h5_rows, 0, 0, nan, nan, nan, nan, nan, nan)


def _session_means(values: np.ndarray, sessions: np.ndarray) -> np.ndarray:
    """Mean over the finite values of each session.

    NaN-aware on purpose: a single missing value must not turn a whole session's mean into NaN
    and, through the demeaning, wipe out every row of that session.
    """
    keys, inverse = np.unique(sessions, return_inverse=True)
    finite = np.isfinite(values)
    totals = np.bincount(inverse[finite], weights=values[finite], minlength=keys.size)
    counts = np.bincount(inverse[finite], minlength=keys.size)
    with np.errstate(invalid="ignore", divide="ignore"):
        means = np.where(counts > 0, totals / np.maximum(counts, 1), np.nan)
    return means[inverse]


def _assemble(name: str, h5_index: np.ndarray, differences: np.ndarray, values: np.ndarray,
              is_h5: np.ndarray, control_win: np.ndarray, control_down: np.ndarray,
              control_mean: np.ndarray, symbols: np.ndarray, sessions: np.ndarray,
              cells_used: int, control_rows: int, down_threshold: float = -0.01) -> MatchResult:
    ok = np.isfinite(differences)
    matched = h5_index[ok]
    diffs = differences[ok]
    if diffs.size == 0:
        return _empty(name, int(h5_index.size))
    h5_values = values[matched]
    h5_down = float(np.mean(h5_values <= down_threshold))
    ctrl_down = float(np.nanmean(control_down[ok]))
    return MatchResult(
        name=name, h5_rows=int(h5_index.size), matched_rows=int(diffs.size),
        unmatched_rows=int(h5_index.size - diffs.size), control_rows=control_rows,
        cells_used=cells_used,
        mean_lift=float(np.mean(diffs)), median_lift=float(np.median(diffs)),
        win_rate_lift=float(np.mean(h5_values > 0) - np.nanmean(control_win[ok])),
        h5_mean=float(np.mean(h5_values)), control_mean=float(np.nanmean(control_mean[ok])),
        downside_ratio=h5_down / ctrl_down if ctrl_down and ctrl_down > 0 else float("nan"),
        differences=diffs, matched_symbols=symbols[matched], matched_sessions=sessions[matched])


def session_demeaned(values: np.ndarray, sessions: np.ndarray, symbols: np.ndarray,
                     cells: np.ndarray, is_h5: np.ndarray, down_threshold: float = -0.01,
                     subset: np.ndarray | None = None) -> MatchResult:
    """Primary estimator: subtract the session's universe mean, then match inside a cell.

    ``subset`` restricts which rows are matched and reported (a price bucket, a regime) but not
    which rows define the session mean. The declaration says the demeaning is "over all universe
    rows of the same session", so a bucket analysis must not silently re-baseline itself against
    the bucket.
    """
    adjusted = values - _session_means(values, sessions)
    usable = np.isfinite(values) & (cells >= 0)
    if subset is not None:
        usable &= subset
    control = usable & ~is_h5
    keys = np.unique(cells[usable])
    control_mean = {}
    control_raw_mean = {}
    control_win = {}
    control_down = {}
    for key in keys:
        selected = control & (cells == key)
        if not selected.any():
            continue
        control_mean[key] = float(np.nanmean(adjusted[selected]))
        control_raw_mean[key] = float(np.nanmean(values[selected]))
        control_win[key] = float(np.mean(values[selected] > 0))
        control_down[key] = float(np.mean(values[selected] <= down_threshold))
    h5_index = np.flatnonzero(usable & is_h5)
    nan = float("nan")
    differences = np.array([adjusted[i] - control_mean.get(cells[i], nan) for i in h5_index])
    win = np.array([control_win.get(cells[i], nan) for i in h5_index])
    down = np.array([control_down.get(cells[i], nan) for i in h5_index])
    raw = np.array([control_raw_mean.get(cells[i], nan) for i in h5_index])
    return _assemble("session_demeaned_cell_matching", h5_index, differences, values, is_h5,
                     win, down, raw, symbols, sessions, len(control_mean),
                     int(control.sum()), down_threshold)


def same_session(values: np.ndarray, sessions: np.ndarray, symbols: np.ndarray,
                 cells: np.ndarray, is_h5: np.ndarray,
                 down_threshold: float = -0.01) -> MatchResult:
    """Secondary estimator: the control pool is the same session and the same cell."""
    usable = np.isfinite(values) & (cells >= 0)
    control = usable & ~is_h5
    combined = np.array([f"{s}|{c}" for s, c in zip(sessions, cells)], dtype=object)
    control_mean: dict[Any, float] = {}
    control_win: dict[Any, float] = {}
    control_down: dict[Any, float] = {}
    keys, inverse = np.unique(combined, return_inverse=True)
    for position, key in enumerate(keys):
        selected = control & (inverse == position)
        if not selected.any():
            continue
        control_mean[key] = float(np.nanmean(values[selected]))
        control_win[key] = float(np.mean(values[selected] > 0))
        control_down[key] = float(np.mean(values[selected] <= down_threshold))
    h5_index = np.flatnonzero(usable & is_h5)
    nan = float("nan")
    differences = np.array([values[i] - control_mean.get(combined[i], nan) for i in h5_index])
    win = np.array([control_win.get(combined[i], nan) for i in h5_index])
    down = np.array([control_down.get(combined[i], nan) for i in h5_index])
    raw = np.array([control_mean.get(combined[i], nan) for i in h5_index])
    return _assemble("same_session_cell_matching", h5_index, differences, values, is_h5,
                     win, down, raw, symbols, sessions, len(control_mean),
                     int(control.sum()), down_threshold)


def by_bucket(values: np.ndarray, sessions: np.ndarray, symbols: np.ndarray, cells: np.ndarray,
              is_h5: np.ndarray, feature: np.ndarray, edges: Sequence[float | None],
              minimum_rows: int) -> list[dict[str, Any]]:
    """The primary estimator restricted to each reported bucket, with the declared merge rule.

    The merge is mechanical: a bucket with fewer than ``minimum_rows`` H5 rows is folded into the
    next higher bucket before anything is reported, so a thin bucket can never be presented as a
    result and the choice of which to merge is not made after seeing numbers.
    """
    index = bucket_index(feature, tuple(edges))
    labels = []
    for low, high in zip(edges, edges[1:]):
        low_text = "-inf" if low is None else f"{low:g}"
        high_text = "+inf" if high is None else f"{high:g}"
        labels.append(f"[{low_text}, {high_text})")
    groups: list[list[int]] = [[i] for i in range(len(labels))]
    merged: list[list[int]] = []
    carry: list[int] = []
    for position in range(len(groups)):
        carry = carry + groups[position]
        rows = int((is_h5 & np.isin(index, carry)).sum())
        if rows >= minimum_rows or position == len(groups) - 1:
            merged.append(carry)
            carry = []
    if carry:
        if merged:
            merged[-1].extend(carry)
        else:
            merged.append(carry)

    out = []
    for group in merged:
        selected = np.isin(index, group)
        label = labels[group[0]] if len(group) == 1 else f"{labels[group[0]]}..{labels[group[-1]]}"
        if not (selected & is_h5).any():
            out.append({"bucket": label, "h5_rows": 0})
            continue
        result = session_demeaned(values, sessions, symbols, cells, is_h5, subset=selected)
        out.append({"bucket": label, **result.to_dict()})
    return out
