"""P2 folds, derived from P1's so that every row's gate is already out of sample.

P1 validated nine folds. A P2 fold validates on one of them and trains on the ones before it, so
a P2 training row is always older than every P2 validation row, and the P1 probability that gated
it came from a model trained older still. Fold 1 has nothing before it and is therefore training
data only, which leaves eight P2 folds.

The embargo is the same 1,440 minutes P1 used. A direction label looks up to 24 hours forward, so
without it the last day of each training window would describe the beginning of the validation
window.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EMBARGO_MINUTES = 1440
MINUTE_MS = 60_000


@dataclass(frozen=True)
class Fold:
    index: int                  # the P1 fold this validates on
    train_p1_folds: tuple[int, ...]
    valid_p1_fold: int

    def masks(self, p1_fold: np.ndarray, ts_ms: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        valid = p1_fold == self.valid_p1_fold
        train = np.isin(p1_fold, self.train_p1_folds)
        if valid.any():
            cutoff = int(ts_ms[valid].min()) - EMBARGO_MINUTES * MINUTE_MS
            train &= ts_ms < cutoff
        return train, valid

    def as_dict(self) -> dict[str, object]:
        return {"fold": self.index, "trains_on_p1_folds": list(self.train_p1_folds),
                "validates_on_p1_fold": self.valid_p1_fold}


def build(p1_folds: tuple[int, ...] = tuple(range(1, 10))) -> tuple[Fold, ...]:
    return tuple(Fold(index=i, train_p1_folds=p1_folds[:position], valid_p1_fold=fold)
                 for i, (position, fold) in enumerate(
                     ((p, f) for p, f in enumerate(p1_folds) if p > 0), start=1))


def gate_threshold(large_move_train: np.ndarray, quantile: float) -> float:
    """The large-move cut, computed on training rows only.

    A fixed probability cannot be used across horizons: P1's large-move probability has a median
    of 0.25 for 4H at one percent and 0.52 for 12H, so one absolute threshold keeps 8 percent of
    rows in one place and 57 percent in another. A quantile keeps the subset comparable, and
    taking it from training rows keeps the validation set from choosing its own size.
    """
    if len(large_move_train) == 0:
        raise ValueError("no training rows to take the gate quantile from")
    return float(np.quantile(large_move_train, quantile))
