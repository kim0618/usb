"""Five-session excursion geometry: the only D-AGG module that turns future bars into numbers.

Definitions are D0's (``d_agg_rules_v1.json`` ``labels`` / ``events``), transcribed:

    P0      = O(D+1)                                  entry, the first price after the signal
    MFE_5   = max(H(D+1), ..., H(D+5)) / P0 - 1       >= 0 on every valid row (L <= O <= H)
    MAE_5   = min(L(D+1), ..., L(D+5)) / P0 - 1       <= 0 on every valid row
    UP10    = MFE_5 >= +0.10   (inclusive, THRESHOLD_EPS)
    DN10    = MAE_5 <= -0.10   (inclusive, THRESHOLD_EPS)

Prices are split-normalised at their own session, ``x(t) / F(t)`` with ``F(t)`` the product of
the ticker's splits executed on or before ``t`` - V2-A's label basis. A split executed inside
D+1..D+5 therefore leaves the window on one basis, and a split executed after D+5 cannot reach it.

The window is five *grid* sessions, never "the next five bars": a session without a bar is not
replaced by D+6. Validity is V2-A's frozen label validity, reused, not re-derived:

    a bar on D+1 (and a positive open), a bar on session D+5 itself, not label CA suspect

That contract has two consequences this module states instead of hiding:

* a missing *middle* session (D+2..D+4) is valid under V2-A; the row gets ``VALID_GAP`` and
  ``window_bars`` < 5, and MFE/MAE are the extremes of the bars that exist - nothing is imputed.
* the label CA rule reads opens and closes over D..D+10 (V2-A: "V1 rule as is for h <= 10"), so
  *validity* depends on D+6..D+10 while the *values* read only D+1..D+5.

UP10 and DN10 are not exclusive, and nothing here asks which came first: daily bars cannot say.
Nothing here sees a signal, a selection, or an aggregate of outcomes.
"""

from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_c_selection.panel import Panel
from app.backtest.strategy_d_agg.config import THRESHOLD_EPS
from app.backtest.strategy_d_agg.models import STATUS_CODE, VALID_CODES, HardFail
from app.backtest.strategy_d_analog.label_extension import LabelValidity, compute_validity

GEOMETRY_CONTRACT = "d-agg-excursion-geometry-v1"


def _forward(array: np.ndarray, step: int, fill) -> np.ndarray:
    """Row ``i`` holds ``array[i + step]``; rows that would read past the end take ``fill``."""
    out = np.full_like(array, fill)
    if step < array.shape[0]:
        out[: array.shape[0] - step] = array[step:]
    return out


@dataclass(frozen=True)
class Excursions:
    """``(T, N)`` geometry of every window. Invalid rows carry NaN values and False flags."""

    horizon: int
    entry_open: np.ndarray      # float64, P0 = O(D+1)/F(D+1), NaN when there is no entry bar
    mfe: np.ndarray             # float64
    mae: np.ndarray             # float64
    up: np.ndarray              # bool
    down: np.ndarray            # bool
    valid: np.ndarray           # bool, == V2-A label validity for this horizon
    status: np.ndarray          # int8, models.EXCURSION_STATUS_ORDER
    window_bars: np.ndarray     # int8, bars present on D+1..D+h
    missing_subclass: np.ndarray  # int8, models.MISSING_SUBCLASS_ORDER (descriptive only)

    def gather(self, session_idx: np.ndarray, ticker_col: np.ndarray) -> dict[str, np.ndarray]:
        if session_idx.shape != ticker_col.shape:
            raise HardFail("R5", "excursion gather got mismatched session and ticker arrays")
        return {name: getattr(self, name)[session_idx, ticker_col]
                for name in ("entry_open", "mfe", "mae", "up", "down", "valid", "status",
                             "window_bars", "missing_subclass")}


def threshold_flags(mfe: np.ndarray, mae: np.ndarray, valid: np.ndarray, *, up: float,
                    down: float, eps: float = THRESHOLD_EPS) -> tuple[np.ndarray, np.ndarray]:
    """Inclusive event flags. NaN compares False, and invalid rows are forced False."""
    with np.errstate(invalid="ignore"):
        return valid & (mfe >= up - eps), valid & (mae <= down + eps)


def compute(panel: Panel, *, horizon: int, up: float, down: float, ca_ratio: float,
            validity: LabelValidity | None = None) -> Excursions:
    """Excursion geometry for every ``(session, ticker)`` of the panel, vectorised over sessions."""
    if horizon < 1:
        raise HardFail("R1", f"excursion horizon {horizon}")
    validity = validity or compute_validity(panel, (horizon,), ca_ratio)
    if horizon not in validity.valid:
        raise HardFail("R1", f"label validity has no h={horizon}")
    factor, _, _ = panel.split_arrays()
    open_p, high_p, low_p = panel.open / factor, panel.high / factor, panel.low / factor
    has_bar = ~np.isnan(panel.close)
    entry = _forward(open_p, 1, np.nan)

    best = np.full(panel.close.shape, np.nan)
    worst = np.full(panel.close.shape, np.nan)
    bars = np.zeros(panel.close.shape, dtype=np.int8)
    for step in range(1, horizon + 1):
        best = np.fmax(best, _forward(high_p, step, np.nan))
        worst = np.fmin(worst, _forward(low_p, step, np.nan))
        bars += _forward(has_bar, step, False).astype(np.int8)

    valid = validity.valid[horizon].copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        mfe = np.where(valid, best / entry - 1.0, np.nan)
        mae = np.where(valid, worst / entry - 1.0, np.nan)
    if not (np.isfinite(mfe[valid]).all() and np.isfinite(mae[valid]).all()):
        raise HardFail("R5", "a valid excursion window has a non-finite MFE or MAE")
    if (mfe[valid] < 0).any() or (mae[valid] > 0).any():
        raise HardFail("R5", "a valid window has MFE < 0 or MAE > 0: a bar breaks L <= O <= H")
    up_flag, down_flag = threshold_flags(mfe, mae, valid, up=up, down=down)

    sessions = panel.close.shape[0]
    boundary = (np.arange(sessions) + horizon >= sessions)[:, None] & np.ones_like(valid)
    status = np.full(valid.shape, STATUS_CODE["VALID"], dtype=np.int8)
    status[valid & (bars < horizon)] = STATUS_CODE["VALID_GAP"]
    invalid = ~valid
    ca = validity.ca_suspect[horizon]
    missing = validity.missing_horizon_bar[horizon]
    no_entry = validity.no_entry_bar
    # First match wins, most structural first; every invalid row lands in exactly one code.
    assigned = np.zeros(valid.shape, dtype=bool)
    for name, mask in (("BOUNDARY", boundary), ("NO_ENTRY_BAR", no_entry),
                       ("MISSING_HORIZON_BAR", missing), ("LABEL_CA_SUSPECT", ca)):
        take = invalid & mask & ~assigned
        status[take] = STATUS_CODE[name]
        assigned |= take
    if (invalid & ~assigned).any():
        raise HardFail("R5", "an invalid window matches no declared status")
    if not np.array_equal(np.isin(status, VALID_CODES), valid):
        raise HardFail("R5", "status partition disagrees with V2-A label validity")

    # Descriptive only: does any bar exist after D+h? Reads beyond the window by construction.
    later = np.zeros(has_bar.shape, dtype=bool)
    suffix = np.flip(np.logical_or.accumulate(np.flip(has_bar, axis=0), axis=0), axis=0)
    later[: max(sessions - horizon - 1, 0)] = suffix[horizon + 1:]
    subclass = np.zeros(valid.shape, dtype=np.int8)
    is_missing = status == STATUS_CODE["MISSING_HORIZON_BAR"]
    subclass[is_missing & later] = 1
    subclass[is_missing & ~later] = 2

    return Excursions(horizon, np.where(np.isfinite(entry) & (entry > 0), entry, np.nan),
                      mfe, mae, up_flag, down_flag, valid, status, bars, subclass)


def eps_band_rows(ex: Excursions, *, up: float, down: float, eps: float = THRESHOLD_EPS) -> dict[str, int]:
    """How many valid rows sit within ``eps`` of a threshold: whether the float policy mattered.

    A count of near-ties, not of events, and not split by any selection.
    """
    with np.errstate(invalid="ignore"):
        return {"up_band": int((ex.valid & (np.abs(ex.mfe - up) <= eps)).sum()),
                "down_band": int((ex.valid & (np.abs(ex.mae - down) <= eps)).sum())}


def close_and_excess(panel: Panel, eligible: np.ndarray, validity: LabelValidity,
                     horizon: int) -> tuple[np.ndarray, np.ndarray]:
    """V2-A ``close_return_h`` and ``excess_return_h`` matrices, for D-AGG-2 secondary X-8 only.

    Kept here so this module stays the package's single importer of the label value code.
    """
    from app.backtest.strategy_d_analog.labels import compute_labels
    labels = compute_labels(panel, validity, eligible)
    return labels.close_return[horizon], labels.excess_return[horizon]
