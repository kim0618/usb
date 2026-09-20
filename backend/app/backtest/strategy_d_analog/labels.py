"""Forward label *values* - the only D module that turns future bars into a number.

D2 was allowed to ask whether a window's label existed; nothing before this file was allowed to
ask what it was worth. Keeping that boundary visible is the point of a separate module: the
signal path receives label values only through ``pit.EmbargoView``, and the query's own realized
label is produced by ``evaluation.py``, which ``signal.py`` may not import (D3 §15).

Definitions are D0's, transcribed and not re-derived:

    close_return_h   = P(D+h) / P0 - 1, clipped to [-1, 1],  P0 = O(D+1)
    excess_return_h  = close_return_h - median close_return_h over every label-valid eligible
                       ticker of the same date (the full universe, not the query sample)

Prices are split-normalized at their own session, ``x(t) / F(t)``. A split executed between D+1
and D+h therefore lands in the ratio, which is the adjustment a label needs and a feature must
never see (C ``labels.py`` uses the same basis).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import warnings

import numpy as np

from app.backtest.strategy_c_selection.panel import Panel
from app.backtest.strategy_d_analog.label_extension import LabelValidity
from app.backtest.strategy_d_analog.models import HardFail

#: D0 ``labels.close_return_h``: the clip bounds the ratio a single label can contribute.
RETURN_CLIP = 1.0
#: Identifies the label *value* contract, as the D2 identity names the validity contract
#: (D1 Pre-flight §10.3). A change to any definition above is a new version, not an edit.
LABEL_VALUE_CONTRACT = "d-label-value-v1"


@dataclass(frozen=True)
class ForwardLabels:
    """Per horizon, ``(T, N)`` label values and the mask saying which of them are usable.

    ``excess`` is NaN wherever the label is invalid, so an arithmetic mistake downstream shows up
    as NaN rather than as a plausible number computed from a window that had no label.
    """

    horizons: tuple[int, ...]
    close_return: dict[int, np.ndarray]
    excess_return: dict[int, np.ndarray]
    market_median: dict[int, np.ndarray]   # (T,) the date's universe median close return
    valid: dict[int, np.ndarray]
    eligible: np.ndarray                   # (T, N) universe rule, as of each session

    def excess_for(self, horizon: int) -> np.ndarray:
        if horizon not in self.excess_return:
            raise HardFail("R1", f"horizon {horizon} was not computed")
        return self.excess_return[horizon]

    def counts(self) -> dict[str, dict[str, int]]:
        return {str(h): {"label_valid_eligible": int((self.valid[h] & self.eligible).sum()),
                         "dates_with_median": int(np.isfinite(self.market_median[h]).sum()),
                         "finite_excess": int(np.isfinite(self.excess_return[h]).sum())}
                for h in self.horizons}


def _forward(array: np.ndarray, step: int) -> np.ndarray:
    """Row ``i`` holds ``array[i + step]``; rows that would read past the end become NaN."""
    out = np.full_like(array, np.nan)
    if step < array.shape[0]:
        out[: array.shape[0] - step] = array[step:]
    return out


def compute_labels(panel: Panel, validity: LabelValidity, eligible: np.ndarray,
                   log: Callable[[str], None] = lambda _: None) -> ForwardLabels:
    """Close returns and excess returns for every ``(session, ticker)`` and every horizon.

    The market leg of the excess is the median over the date's *full* eligible, label-valid
    universe. Using the query sample instead would make one query's benchmark depend on which
    other queries were sampled, which is not what D0 declares.
    """
    if eligible.shape != panel.close.shape:
        raise HardFail("R5", f"eligibility mask {eligible.shape} does not match {panel.close.shape}")
    factor, _, _ = panel.split_arrays()
    open_price, close_price = panel.open / factor, panel.close / factor
    reference = _forward(open_price, 1)

    close_return: dict[int, np.ndarray] = {}
    excess_return: dict[int, np.ndarray] = {}
    market_median: dict[int, np.ndarray] = {}
    for horizon in validity.horizons:
        valid = validity.valid[horizon]
        with np.errstate(divide="ignore", invalid="ignore"):
            raw = _forward(close_price, horizon) / reference - 1.0
        raw = np.clip(raw, -RETURN_CLIP, RETURN_CLIP)
        raw = np.where(valid, raw, np.nan)
        if np.isfinite(raw[valid]).size and not np.isfinite(raw[valid]).all():
            raise HardFail("R5", f"h={horizon}: a label declared valid is not a finite return")
        basis = np.where(valid & eligible, raw, np.nan)
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            # A session before the seasoning length, or within h of the end of the dataset, has no
            # label-valid eligible ticker at all. That date simply has no benchmark and its median
            # stays NaN; it is never a query date or a library end date.
            warnings.filterwarnings("ignore", "All-NaN slice encountered", RuntimeWarning)
            median = np.nanmedian(basis, axis=1)
        close_return[horizon] = raw
        market_median[horizon] = median
        excess_return[horizon] = raw - median[:, None]
        log(f"labels h={horizon}: {int((valid & eligible).sum()):,} valid eligible ticker-dates, "
            f"{int(np.isfinite(median).sum())} dates with a universe median")
    return ForwardLabels(validity.horizons, close_return, excess_return, market_median,
                         dict(validity.valid), eligible)


def eligibility_matrix(session_count: int, ticker_count: int,
                       masks: Mapping[int, np.ndarray]) -> np.ndarray:
    """Stack per-session universe masks into the ``(T, N)`` array the excess leg needs."""
    out = np.zeros((session_count, ticker_count), dtype=bool)
    for index, mask in masks.items():
        if mask.shape != (ticker_count,):
            raise HardFail("R5", f"session {index} mask has shape {mask.shape}")
        out[index] = mask
    return out


def gather(values: np.ndarray, session_idx: np.ndarray, ticker_col: np.ndarray) -> np.ndarray:
    """Pick one label per ``(session, ticker)`` pair, in the order the pairs were given."""
    if session_idx.shape != ticker_col.shape:
        raise HardFail("R5", "label gather got mismatched session and ticker arrays")
    if session_idx.size == 0:
        return np.empty(0, dtype=np.float64)
    if int(session_idx.max()) >= values.shape[0] or int(ticker_col.max()) >= values.shape[1]:
        raise HardFail("R5", "label gather addresses a row or column outside the panel")
    return values[session_idx, ticker_col]


def horizons_of(combinations: Sequence[tuple[int, int]]) -> tuple[int, ...]:
    return tuple(sorted({int(h) for _, h in combinations}))


@dataclass(frozen=True)
class ForwardExtremes:
    """Per horizon, ``(T, N)`` MFE and MAE - the best and worst the label window ever reached.

    D0 defines both in the same ``labels`` block as the close return, on the same ``P0 = O(D+1)``
    basis and the same D+1..D+h window. D3 had no use for them (it only needed validity and the
    close return), so they are computed here, for the secondary metrics D0 asks D4 to report.
    They are never a gate input and never reach a signal.
    """

    horizons: tuple[int, ...]
    mfe: dict[int, np.ndarray]
    mae: dict[int, np.ndarray]


def compute_extremes(panel: Panel, validity, horizons: Sequence[int]) -> ForwardExtremes:
    """``mfe_h = max(H(D+1..D+h)) / P0 - 1``, ``mae_h = min(L(D+1..D+h)) / P0 - 1`` (D0 labels)."""
    wanted = tuple(sorted({int(h) for h in horizons}))
    factor, _, _ = panel.split_arrays()
    open_price = panel.open / factor
    high, low = panel.high / factor, panel.low / factor
    reference = _forward(open_price, 1)
    mfe: dict[int, np.ndarray] = {}
    mae: dict[int, np.ndarray] = {}
    best = np.full(panel.close.shape, np.nan)
    worst = np.full(panel.close.shape, np.nan)
    step = 0
    for horizon in wanted:
        while step < horizon:
            step += 1
            ahead_high, ahead_low = _forward(high, step), _forward(low, step)
            best = np.where(np.isnan(best), ahead_high, np.fmax(best, ahead_high))
            worst = np.where(np.isnan(worst), ahead_low, np.fmin(worst, ahead_low))
        with np.errstate(divide="ignore", invalid="ignore"):
            valid = validity.valid[horizon]
            mfe[horizon] = np.where(valid, best / reference - 1.0, np.nan)
            mae[horizon] = np.where(valid, worst / reference - 1.0, np.nan)
    return ForwardExtremes(wanted, mfe, mae)
