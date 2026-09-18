"""Forward labels for every (session D, ticker): the future, measured from D+1's open.

This module is the only code allowed to read rows after D, and ``features.py`` never imports it.
Label prices are brought to one basis with ``F`` including splits executed after D, which is
the economic adjustment a label needs and a feature must never see.

For horizon k the window is sessions D+1..D+k. D's own high, low and close are never in it.
"""

from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_c_selection.panel import Panel

LABEL_CA_HORIZON = 10


@dataclass(frozen=True)
class LabelSet:
    horizons: tuple[int, ...]
    reference: np.ndarray  # P0 = open(D+1) / F(D+1)
    no_entry_bar: np.ndarray
    label_ca_suspect: np.ndarray
    disappeared: dict[int, np.ndarray]
    mfe: dict[int, np.ndarray]
    mae: dict[int, np.ndarray]
    close_return: dict[int, np.ndarray]
    close_return_from_d_close: dict[int, np.ndarray]
    time_to_mfe: dict[int, np.ndarray]
    time_to_mae: dict[int, np.ndarray]

    def valid(self, k: int) -> np.ndarray:
        return ~self.no_entry_bar & ~self.label_ca_suspect & ~self.disappeared[k] \
            & ~np.isnan(self.mfe[k])


def _forward(array: np.ndarray, j: int) -> np.ndarray:
    """Row i holds array[i + j]; rows past the end are NaN."""
    out = np.full_like(array, np.nan)
    if j < array.shape[0]:
        out[: array.shape[0] - j] = array[j:]
    return out


def compute_labels(panel: Panel, horizons: tuple[int, ...], ca_ratio: float) -> LabelSet:
    factor, _, _ = panel.split_arrays()
    o, h, lo, c = (panel.open / factor, panel.high / factor, panel.low / factor, panel.close / factor)
    t, n = c.shape
    has_bar = ~np.isnan(panel.close)
    last_bar = np.where(has_bar.any(axis=0), t - 1 - has_bar[::-1].argmax(axis=0), -1)[None, :]
    index = np.arange(t)[:, None]

    reference = _forward(o, 1)
    no_entry_bar = np.isnan(reference) | (reference <= 0)

    # Corporate-action suspicion inside D..D+10, on the split-normalized series.
    suspect = np.zeros((t, n), dtype=bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        gap = reference / c
        suspect |= (gap >= ca_ratio) | (gap <= 1.0 / ca_ratio)
        previous_close = c.copy()
        running = _forward(c, 1)
        for j in range(2, LABEL_CA_HORIZON + 1):
            prev = np.where(np.isnan(running), previous_close, running)
            close_j, open_j = _forward(c, j), _forward(o, j)
            for ratio in (close_j / prev, open_j / prev):
                suspect |= (ratio >= ca_ratio) | (ratio <= 1.0 / ca_ratio)
            previous_close, running = prev, close_j

    mfe, mae, close_ret, close_from_d, t_mfe, t_mae, disappeared = {}, {}, {}, {}, {}, {}, {}
    for k in horizons:
        best_high = np.full((t, n), np.nan)
        best_low = np.full((t, n), np.nan)
        at_high = np.full((t, n), np.nan)
        at_low = np.full((t, n), np.nan)
        last_close = np.full((t, n), np.nan)
        for j in range(1, k + 1):
            hj, lj, cj = _forward(h, j), _forward(lo, j), _forward(c, j)
            higher = ~np.isnan(hj) & (np.isnan(best_high) | (hj > best_high))
            lower = ~np.isnan(lj) & (np.isnan(best_low) | (lj < best_low))
            best_high = np.where(higher, hj, best_high)
            best_low = np.where(lower, lj, best_low)
            at_high = np.where(higher, j, at_high)
            at_low = np.where(lower, j, at_low)
            last_close = np.where(np.isnan(cj), last_close, cj)
        with np.errstate(divide="ignore", invalid="ignore"):
            mfe[k] = best_high / reference - 1.0
            mae[k] = best_low / reference - 1.0
            close_ret[k] = last_close / reference - 1.0
            close_from_d[k] = _forward(c, k) / c - 1.0
        t_mfe[k], t_mae[k] = at_high, at_low
        disappeared[k] = (last_bar < index + k) | (index + k > t - 1)
    return LabelSet(tuple(horizons), reference, no_entry_bar, suspect & ~no_entry_bar, disappeared,
                    mfe, mae, close_ret, close_from_d, t_mfe, t_mae)
