"""What counts as a large move, and whether one happened.

Two target kinds are kept strictly apart (contract section 8). A path target asks whether the
threshold was ever touched inside the horizon; an endpoint target asks where price finished.
They answer different questions and must never be pooled: over 24 hours BTC can touch +2% and
-2% in the same window, so the path targets for UP and DOWN are not mutually exclusive and are
modelled as independent labels (contract section 9).

The reference price is the close of the decision bar. The forward window starts at the next bar,
so nothing in a label is knowable at decision time.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import windows as W

PATH = "PATH_TOUCH"
ENDPOINT = "ENDPOINT"

UP = "UP"
DOWN = "DOWN"


@dataclass(frozen=True)
class TargetSpec:
    """One preregistered label: a direction, a size and a horizon."""

    name: str
    direction: str          # UP or DOWN
    threshold_bp: int       # 50 = 0.50%
    horizon_minutes: int
    kind: str               # PATH or ENDPOINT

    @property
    def horizon_label(self) -> str:
        return f"{self.horizon_minutes // 60}H"

    @property
    def threshold_pct(self) -> float:
        return self.threshold_bp / 100.0


def build_specs(grid: dict[str, tuple[int, ...]]) -> tuple[TargetSpec, ...]:
    """Expand {horizon_minutes: (threshold_bp, ...)} into both directions and both kinds."""
    specs: list[TargetSpec] = []
    for kind in (PATH, ENDPOINT):
        for horizon in sorted(grid):
            for bp in grid[horizon]:
                for direction in (UP, DOWN):
                    hours = horizon // 60
                    suffix = "" if kind == PATH else "_EP"
                    specs.append(TargetSpec(
                        name=f"{hours}H_{direction}_{bp:03d}{suffix}",
                        direction=direction, threshold_bp=bp,
                        horizon_minutes=horizon, kind=kind))
    return tuple(specs)


def labels(spec: TargetSpec, high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Label for every bar, as float with NaN where the forward window is incomplete.

    NaN rather than 0 matters: a row whose horizon runs off the end of the data has no answer,
    and filling it with "no move" would quietly teach the model that the last day of the sample
    is always calm.
    """
    h = spec.horizon_minutes
    ratio = 1.0 + spec.threshold_pct / 100.0 * (1 if spec.direction == UP else -1)
    trigger = close * ratio

    if spec.kind == PATH:
        reach = W.forward_max(high, h) if spec.direction == UP else W.forward_min(low, h)
        hit = reach >= trigger if spec.direction == UP else reach <= trigger
    elif spec.kind == ENDPOINT:
        reach = np.full(len(close), np.nan)
        if h < len(close):
            reach[:len(close) - h] = close[h:]
        hit = reach >= trigger if spec.direction == UP else reach <= trigger
    else:
        raise ValueError(f"unknown target kind {spec.kind!r}")

    out = hit.astype(np.float64)
    out[np.isnan(reach)] = np.nan
    return out


def build_all(specs: tuple[TargetSpec, ...], high: np.ndarray, low: np.ndarray,
              close: np.ndarray) -> dict[str, np.ndarray]:
    return {spec.name: labels(spec, high, low, close) for spec in specs}
