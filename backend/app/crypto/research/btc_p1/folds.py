"""Expanding walk-forward folds with an embargo, plus the inner split that calibration uses.

Random splits are forbidden (contract section 16). Chronological splits alone are not enough
either: a training row at time t carries a label that looks up to 24 hours forward, so rows in
the last day of a training window describe a future that lies inside the validation window. Each
train set therefore ends one full maximum horizon before its validation starts.

The same embargo is applied at the internal boundary where the calibration slice begins, for the
same reason.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Share of each training window, chronologically first, used to fit the model itself. The
#: remainder fits the calibrator, so the calibrator is never asked about rows the model memorised.
MODEL_SHARE = 0.80


@dataclass(frozen=True)
class Fold:
    index: int
    train_start_ms: int
    train_end_ms: int          # exclusive, already embargoed
    valid_start_ms: int
    valid_end_ms: int          # exclusive

    def as_dict(self) -> dict[str, object]:
        return {"fold": self.index,
                "train_start": _utc(self.train_start_ms), "train_end": _utc(self.train_end_ms),
                "valid_start": _utc(self.valid_start_ms), "valid_end": _utc(self.valid_end_ms)}


def _utc(ms: int) -> str:
    return str(np.datetime64(int(ms), "ms")) + "Z"


def to_ms(day: str) -> int:
    return int(np.datetime64(day + "T00:00:00", "ms").astype("int64"))


def build(sample_start: str, fold_starts: tuple[str, ...], end: str,
          embargo_minutes: int) -> tuple[Fold, ...]:
    """One fold per boundary; each validates until the next boundary, the last until `end`."""
    embargo_ms = embargo_minutes * 60_000
    start_ms, end_ms = to_ms(sample_start), to_ms(end)
    bounds = [to_ms(d) for d in fold_starts]
    folds = []
    for i, boundary in enumerate(bounds):
        valid_end = bounds[i + 1] if i + 1 < len(bounds) else end_ms
        folds.append(Fold(index=i + 1, train_start_ms=start_ms,
                          train_end_ms=boundary - embargo_ms,
                          valid_start_ms=boundary, valid_end_ms=valid_end))
    return tuple(folds)


def split_train(train_ts: np.ndarray, embargo_minutes: int) -> tuple[np.ndarray, np.ndarray]:
    """Chronological model/calibration split inside one training window, with the same embargo.

    Returns boolean masks over `train_ts`. The gap between them is dropped rather than given to
    either side: those rows' labels straddle the boundary.
    """
    if len(train_ts) == 0:
        return np.zeros(0, bool), np.zeros(0, bool)
    cut = int(len(train_ts) * MODEL_SHARE)
    cut = min(max(cut, 1), len(train_ts) - 1)
    boundary_ms = int(train_ts[cut])
    embargo_ms = embargo_minutes * 60_000
    model = train_ts < boundary_ms - embargo_ms
    calib = train_ts >= boundary_ms
    return model, calib


def decision_rows(ts: np.ndarray, *, step_minutes: int, warmup_minutes: int,
                  max_horizon_minutes: int) -> np.ndarray:
    """Indices of the bars at which a decision is evaluated.

    Hourly rather than every minute: consecutive minutes carry almost the same features and
    almost the same label, so a per-minute grid would inflate the row count roughly sixtyfold
    without adding independent observations.
    """
    first = warmup_minutes
    last = len(ts) - max_horizon_minutes - 1
    rows = np.arange(first, last + 1, dtype=np.int64)
    keep = ((ts[rows] // 60_000) % step_minutes) == 0
    return rows[keep]
