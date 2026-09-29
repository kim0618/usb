"""The D6-C input grid: the D6-B loader plus the mark extremes the stop needs.

`d6.data.load_inputs` carries everything a decision needs. A stop also needs to know how far the
mark fell *inside* a bar, which no decision ever asks for, so the mark low and high are joined
here from the same D2 file the rest of the grid comes from.
"""
from __future__ import annotations

import numpy as np

from ..dataset import MINUTE_MS, _read
from ..d6.data import load_inputs as load_decision_inputs

MARK_FIELDS = ("low", "high")


def load_inputs(rebuild: bool = False) -> dict[str, np.ndarray]:
    """The decision grid plus `mark_low` and `mark_high`, aligned on the same 1m rows."""
    grid = dict(load_decision_inputs(rebuild=rebuild))
    mark = _read("mark_1m", list(MARK_FIELDS))
    index = np.searchsorted(mark["timestamp_ms"], grid["ts"])
    if len(index) and (index.max() >= len(mark["timestamp_ms"])
                       or not np.array_equal(mark["timestamp_ms"][index], grid["ts"])):
        raise RuntimeError("mark_1m does not cover every research grid minute")
    for field in MARK_FIELDS:
        grid[f"mark_{field}"] = mark[field][index]
    if not np.all(grid["mark_low"] <= grid["mark_close"] + 1e-9):
        raise RuntimeError("mark_low above mark_close on at least one bar")
    if not np.all(grid["mark_high"] >= grid["mark_close"] - 1e-9):
        raise RuntimeError("mark_high below mark_close on at least one bar")
    return grid


def bar_index(grid: dict[str, np.ndarray], ts_ms: int) -> int:
    position = int(np.searchsorted(grid["ts"], ts_ms, side="left"))
    if position >= len(grid["ts"]) or int(grid["ts"][position]) != ts_ms:
        raise KeyError(f"{ts_ms} is not a bar open time on the research grid")
    return position


def window_slice(grid: dict[str, np.ndarray], start_ms: int, end_ms: int) -> slice:
    lo = int(np.searchsorted(grid["ts"], start_ms, side="left"))
    hi = int(np.searchsorted(grid["ts"], end_ms, side="left"))
    return slice(lo, hi)


__all__ = ["load_inputs", "bar_index", "window_slice", "MINUTE_MS"]
