"""The P1 large-move gate, built only from out-of-sample P1 predictions.

This is where P2 is easiest to break. The forward shadow's frozen artifact was trained through
2025-12-31, so applying it across 2022-2025 to decide which rows count would let a model that
has seen those years choose its own evaluation set. The gate would then carry information from
the future of every row it gates.

So the gate reads `predictions_v1.parquet` instead: P1's per-fold validation predictions, each
produced by a model trained only on data before that row. That restricts P2 to the 41,400
timestamps P1 validated on, which is the price of not leaking.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

P1_PREDICTIONS = Path("data/research/crypto/btc_p1/predictions_v1.parquet")

#: P1's calibrated M2 probability: the model the historical result rests on.
P1_COLUMN = "p_m2_cal"


class GateError(RuntimeError):
    """The P1 predictions cannot support the gate that was asked for."""


@dataclass(frozen=True)
class GateFrame:
    """Aligned P1 output for one (horizon, threshold) pair, one row per decision timestamp."""

    ts_ms: np.ndarray
    fold: np.ndarray
    year: np.ndarray
    vol_regime: np.ndarray
    trend_regime: np.ndarray
    p_up: np.ndarray
    p_down: np.ndarray

    @property
    def large_move(self) -> np.ndarray:
        """P1's own large-move state: the stronger of the two touch probabilities.

        Preregistered in the P1 forward contract as `max(P_UP, P_DOWN)`, so it is not a new
        statistic invented for P2.
        """
        return np.maximum(self.p_up, self.p_down)

    @property
    def separation(self) -> np.ndarray:
        """P1's directional lean, which the historical study measured at AUC ~0.51."""
        return self.p_down - self.p_up


def load(horizon_minutes: int, threshold_bp: int) -> GateFrame:
    import pyarrow.parquet as pq

    if not P1_PREDICTIONS.exists():
        raise GateError(f"P1 predictions missing at {P1_PREDICTIONS}")
    hours = horizon_minutes // 60
    up_name, down_name = f"{hours}H_UP_{threshold_bp:03d}", f"{hours}H_DOWN_{threshold_bp:03d}"

    table = pq.read_table(P1_PREDICTIONS,
                          columns=["ts_ms", "target", "fold", "year", "vol_regime",
                                   "trend_regime", P1_COLUMN])
    target = table.column("target").to_numpy(zero_copy_only=False)
    ts = table.column("ts_ms").to_numpy()
    prob = table.column(P1_COLUMN).to_numpy()

    up_sel, down_sel = target == up_name, target == down_name
    if not up_sel.any() or not down_sel.any():
        raise GateError(f"{up_name}/{down_name} absent from the P1 predictions")
    ts_up, ts_down = ts[up_sel], ts[down_sel]
    if not np.array_equal(ts_up, ts_down):
        raise GateError(f"{up_name} and {down_name} rows are not aligned")

    return GateFrame(
        ts_ms=ts_up,
        fold=table.column("fold").to_numpy()[up_sel],
        year=table.column("year").to_numpy(zero_copy_only=False)[up_sel],
        vol_regime=table.column("vol_regime").to_numpy(zero_copy_only=False)[up_sel],
        trend_regime=table.column("trend_regime").to_numpy(zero_copy_only=False)[up_sel],
        p_up=prob[up_sel], p_down=prob[down_sel])


def apply(frame: GateFrame, threshold: float) -> np.ndarray:
    """Rows where P1 thought a large move was likely, at a fixed preregistered threshold."""
    return frame.large_move >= threshold


def describe(frame: GateFrame, threshold: float) -> dict[str, Any]:
    keep = apply(frame, threshold)
    return {
        "p1_column": P1_COLUMN,
        "source": "P1 per-fold validation predictions (out of sample for every row)",
        "threshold": threshold,
        "rows_total": int(len(keep)),
        "rows_kept": int(keep.sum()),
        "share_kept": float(keep.mean()),
        "large_move_quantiles": {
            q: float(np.quantile(frame.large_move, q))
            for q in (0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)
        },
    }
