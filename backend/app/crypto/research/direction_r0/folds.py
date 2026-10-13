"""Walk-forward quarterly folds with purge + 1 day embargo (contract section 4)."""
from __future__ import annotations

import datetime as dt

import numpy as np

from ..ptm_r1 import data as PD

EMBARGO_MIN = 1440


def _minute(y: int, mo: int) -> int:
    t = int(dt.datetime(y, mo, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    return (t - PD.TRAIN_START_MS) // 60_000


def test_blocks() -> list[tuple[int, int]]:
    starts = [(2021, 11), (2022, 2), (2022, 5), (2022, 8), (2022, 11), (2023, 2), (2023, 5)]
    ends = [(2022, 2), (2022, 5), (2022, 8), (2022, 11), (2023, 2), (2023, 5), (2023, 7)]
    out = []
    for (y0, m0), (y1, m1) in zip(starts, ends):
        a = _minute(y0, m0)
        b = (PD.TRAIN_END_MS - PD.TRAIN_START_MS) // 60_000 if (y1, m1) == (2023, 7) else _minute(y1, m1)
        out.append((a, b))
    return out


def folds(entry_min: np.ndarray, label_end_min: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """(train_idx, test_idx) per block. Test = entry inside the block; train = label ends >= 1 day before."""
    out = []
    for a, b in test_blocks():
        test = np.flatnonzero((entry_min >= a) & (entry_min < b))
        train = np.flatnonzero(label_end_min <= a - EMBARGO_MIN)
        out.append((train, test))
    return out
