"""Direction targets: which side comes first, where price ends, and which excursion dominates.

The primary target is first touch. P1 already showed that path-touch is the thing the data
supports, so "does +X arrive before -X" is the natural directional question and the one that maps
onto a trade without inventing a new notion of direction.

One case is deliberately thrown away rather than guessed. With 1-minute bars, when a bar's high
clears +X and its low clears -X, the order inside that minute is unknowable. Those rows are
labelled AMBIGUOUS and excluded, because filling them in by a rule (say, "assume the close side
won") would invent exactly the directional signal this study is trying to measure.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

UP_FIRST = 1
DOWN_FIRST = 0
NEITHER = -1
AMBIGUOUS = -2

FIRST_TOUCH = "FIRST_TOUCH"
ENDPOINT = "ENDPOINT"
DOMINANT_EXCURSION = "DOMINANT_EXCURSION"

#: Rows evaluated per pass when slicing forward windows. Keeps a 24h window under ~60 MB.
CHUNK = 4_000


@dataclass(frozen=True)
class DirectionSpec:
    name: str
    kind: str
    horizon_minutes: int
    threshold_bp: int

    @property
    def horizon_label(self) -> str:
        return f"{self.horizon_minutes // 60}H"


def _chunks(rows: np.ndarray):
    """Row index ranges, so a 24h forward window never materialises more than ~60 MB at once."""
    for start in range(0, len(rows), CHUNK):
        yield slice(start, min(start + CHUNK, len(rows)))


def _forward(values: np.ndarray, idx: np.ndarray, horizon: int) -> np.ndarray:
    """block[i, j] = values[idx[i] + 1 + j]. The window starts at the next bar, never idx itself."""
    offsets = np.arange(1, horizon + 1, dtype=np.int64)
    return values[idx[:, None] + offsets[None, :]]


def _first_index(hit: np.ndarray, horizon: int) -> np.ndarray:
    """First column where `hit` is True, or `horizon` when it never is."""
    return np.where(hit.any(axis=1), hit.argmax(axis=1), horizon)


def first_touch(spec: DirectionSpec, high: np.ndarray, low: np.ndarray, close: np.ndarray,
                rows: np.ndarray) -> np.ndarray:
    """UP_FIRST / DOWN_FIRST / NEITHER / AMBIGUOUS for each decision row."""
    horizon = spec.horizon_minutes
    move = spec.threshold_bp / 10_000.0
    out = np.full(len(rows), NEITHER, dtype=np.int64)

    for window in _chunks(rows):
        idx = rows[window]
        reference = close[idx][:, None]
        up_at = _first_index(_forward(high, idx, horizon) >= reference * (1.0 + move), horizon)
        down_at = _first_index(_forward(low, idx, horizon) <= reference * (1.0 - move), horizon)

        label = np.full(len(idx), NEITHER, dtype=np.int64)
        label[up_at < down_at] = UP_FIRST
        label[down_at < up_at] = DOWN_FIRST
        # Equal and inside the horizon means one minute cleared both sides; the order within that
        # minute is not in the data, so the row is dropped rather than decided by a rule.
        label[(up_at == down_at) & (up_at < horizon)] = AMBIGUOUS
        out[window] = label
    return out


def endpoint_direction(spec: DirectionSpec, close: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """UP_FIRST when the horizon ends above +X, DOWN_FIRST below -X, NEITHER inside the band."""
    horizon = spec.horizon_minutes
    move = spec.threshold_bp / 10_000.0
    reference = close[rows]
    final = close[rows + horizon]
    out = np.full(len(rows), NEITHER, dtype=np.int64)
    out[final >= reference * (1.0 + move)] = UP_FIRST
    out[final <= reference * (1.0 - move)] = DOWN_FIRST
    return out


def dominant_excursion(spec: DirectionSpec, high: np.ndarray, low: np.ndarray,
                       close: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """Whichever excursion travelled further, provided one of them cleared the threshold.

    Requiring the threshold keeps this from labelling a flat window by a few basis points of
    noise; without it most rows would carry a direction that no trade could have captured.
    """
    horizon = spec.horizon_minutes
    move = spec.threshold_bp / 10_000.0
    out = np.full(len(rows), NEITHER, dtype=np.int64)

    for window in _chunks(rows):
        idx = rows[window]
        reference = close[idx]
        up_excursion = _forward(high, idx, horizon).max(axis=1) / reference - 1.0
        down_excursion = 1.0 - _forward(low, idx, horizon).min(axis=1) / reference

        label = np.full(len(reference), NEITHER, dtype=np.int64)
        qualifies = (up_excursion >= move) | (down_excursion >= move)
        label[qualifies & (up_excursion > down_excursion)] = UP_FIRST
        label[qualifies & (down_excursion > up_excursion)] = DOWN_FIRST
        label[qualifies & (up_excursion == down_excursion)] = AMBIGUOUS
        out[window] = label
    return out


def build(spec: DirectionSpec, high: np.ndarray, low: np.ndarray, close: np.ndarray,
          rows: np.ndarray) -> np.ndarray:
    if spec.kind == FIRST_TOUCH:
        return first_touch(spec, high, low, close, rows)
    if spec.kind == ENDPOINT:
        return endpoint_direction(spec, close, rows)
    if spec.kind == DOMINANT_EXCURSION:
        return dominant_excursion(spec, high, low, close, rows)
    raise ValueError(f"unknown direction target kind {spec.kind!r}")


def directional_mask(labels: np.ndarray) -> np.ndarray:
    """Rows that carry a direction. NEITHER and AMBIGUOUS are excluded, never recoded."""
    return (labels == UP_FIRST) | (labels == DOWN_FIRST)


def class_counts(labels: np.ndarray) -> dict[str, int]:
    return {"UP_FIRST": int((labels == UP_FIRST).sum()),
            "DOWN_FIRST": int((labels == DOWN_FIRST).sum()),
            "NEITHER": int((labels == NEITHER).sum()),
            "AMBIGUOUS": int((labels == AMBIGUOUS).sum()),
            "total": int(len(labels))}
