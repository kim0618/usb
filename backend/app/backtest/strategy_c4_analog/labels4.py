"""Forward outcomes for query and library rows, on C-M's matching cells.

The label definitions are imported from ``strategy_c_selection.labels`` unchanged: entry at the
D+1 open, window D+1..D+h, close return clipped to [-1,1], MFE and MAE unclipped, and C's
validity rule. What C-4 adds is the excess a row is measured against, and it adds it in C's own
terms - the mean of the same session's (price, ATR, ADV) matching cell - so a query row and a
library row are scored by one definition.
"""

from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_c_selection.labels import LabelSet

CELL_FEATURES = ("price_bucket", "atr_bucket", "adv20_bucket")


@dataclass(frozen=True)
class ExcessLabels:
    horizon: int
    excess: np.ndarray        # (T, N) close return minus the matched cell mean
    cell_matched: np.ndarray  # (T, N) bool, the cell held at least min_members rows
    date_excess: np.ndarray   # (T, N) close return minus the session mean, reported for robustness
    cell_code: np.ndarray     # (T, N) int64, -1 when a bucket is undefined


def cell_codes(feature_values: dict[str, np.ndarray]) -> np.ndarray:
    price = feature_values["price_bucket"]
    atr = feature_values["atr_bucket"]
    adv = feature_values["adv20_bucket"]
    defined = np.isfinite(price) & np.isfinite(atr) & np.isfinite(adv)
    code = np.full(price.shape, -1, dtype=np.int64)
    combined = ((np.nan_to_num(price) * 10 + np.nan_to_num(atr)) * 10
                + np.nan_to_num(adv)).astype(np.int64)
    code[defined] = combined[defined]
    return code


def build(labels: LabelSet, feature_values: dict[str, np.ndarray], population: np.ndarray,
          horizon: int, min_members: int) -> ExcessLabels:
    """``population`` is the label-valid base-eligible mask the cell means are taken over."""
    close = labels.close_return[horizon]
    code = cell_codes(feature_values)
    t, n = close.shape
    excess = np.full((t, n), np.nan)
    date_excess = np.full((t, n), np.nan)
    matched = np.zeros((t, n), dtype=bool)
    for i in range(t):
        row = population[i] & np.isfinite(close[i])
        if not row.any():
            continue
        values = close[i, row]
        session_mean = float(values.mean())
        date_excess[i, row] = values - session_mean
        codes = code[i, row]
        usable = codes >= 0
        means = np.full(values.shape, session_mean)
        flags = np.zeros(values.shape, dtype=bool)
        if usable.any():
            unique, inverse = np.unique(codes[usable], return_inverse=True)
            totals = np.bincount(inverse, weights=values[usable], minlength=unique.size)
            counts = np.bincount(inverse, minlength=unique.size)
            cell_mean = totals / counts
            big = counts >= min_members
            means[usable] = np.where(big[inverse], cell_mean[inverse], session_mean)
            flags[usable] = big[inverse]
        excess[i, row] = values - means
        matched[i, row] = flags
    return ExcessLabels(horizon, excess, matched, date_excess, code)
