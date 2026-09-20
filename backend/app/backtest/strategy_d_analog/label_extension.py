"""Whether a window has a usable forward label - never what that label is worth.

A neighbour is only admissible if its own label was decidable, so D2 needs the *validity* of a
label 20 sessions out while being blind to its value. This module computes exactly that mask and
returns nothing else: no return, no MFE, no MAE, no reference price leaves it.

Two departures from C ``labels.py``, both declared in D0:

* C ``LabelSet.valid`` folds in ``disappeared``, which asks whether any bar exists on or after
  D+h anywhere in the dataset and therefore reads past D+h. D asks the decidable question - is
  there a bar on session ``d+h`` itself - so a neighbour's validity is known at query time
  (``labels.why_not_c_disappeared``).
* C ``LABEL_CA_HORIZON`` is 10, so a 10x jump at D+15 passes as a valid 20-day label. D0
  ``labels.label_ca_suspect.h_20`` extends the same ratio rule to D+11..D+20.

The recursion below is C's, re-derived here rather than imported, for the reason D1 recorded in
its §11.1 TEMPORARY_D_EXTENSION decision: C is mid-study (C-V2 runs carry ``labels.py`` in their
code digest) and editing or depending on it would move C's identity. The D test suite pins this
implementation against ``LabelSet.label_ca_suspect`` at the 10-session mark, so the two cannot
drift apart unnoticed (test T12).
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_c_selection.panel import Panel
from app.backtest.strategy_d_analog.models import HardFail

#: Where C stops. Up to here the D mask must equal C's; past it D applies the same ratio rule.
C_LABEL_CA_HORIZON = 10


def _forward(array: np.ndarray, step: int, fill) -> np.ndarray:
    """Row ``i`` holds ``array[i + step]``; rows that would read past the end take ``fill``."""
    out = np.full_like(array, fill)
    if step < array.shape[0]:
        out[: array.shape[0] - step] = array[step:]
    return out


@dataclass(frozen=True)
class LabelValidity:
    """Per horizon, ``(T, N)`` bool: this ``(session, ticker)`` window has a decidable label.

    ``reasons`` carries the three failure masks separately so the run can report why windows
    dropped out without ever holding a label value.
    """

    horizons: tuple[int, ...]
    valid: dict[int, np.ndarray]
    no_entry_bar: np.ndarray
    missing_horizon_bar: dict[int, np.ndarray]
    ca_suspect: dict[int, np.ndarray]

    def for_horizon(self, horizon: int) -> np.ndarray:
        if horizon not in self.valid:
            raise HardFail("R1", f"horizon {horizon} was not computed")
        return self.valid[horizon]

    def counts(self) -> dict[str, dict[str, int]]:
        return {str(h): {"valid": int(self.valid[h].sum()),
                         "no_entry_bar": int(self.no_entry_bar.sum()),
                         "missing_horizon_bar": int(self.missing_horizon_bar[h].sum()),
                         "label_ca_suspect": int(self.ca_suspect[h].sum())}
                for h in self.horizons}


def _ca_suspect_series(open_p: np.ndarray, close_p: np.ndarray, reference: np.ndarray,
                       ratio: float, horizon: int) -> dict[int, np.ndarray]:
    """C's D..D+10 recursion, run out to ``horizon`` and snapshotted where each h needs it.

    The carried ``prev`` close is what makes the rule survive a missing bar: a session with no
    bar does not reset the comparison, it is skipped and the previous close stays the reference.
    """
    suspect = np.zeros(close_p.shape, dtype=bool)
    snapshots: dict[int, np.ndarray] = {}
    with np.errstate(divide="ignore", invalid="ignore"):
        gap = reference / close_p
        suspect |= (gap >= ratio) | (gap <= 1.0 / ratio)
        previous_close = close_p.copy()
        running = _forward(close_p, 1, np.nan)
        for step in range(2, horizon + 1):
            prev = np.where(np.isnan(running), previous_close, running)
            close_j, open_j = _forward(close_p, step, np.nan), _forward(open_p, step, np.nan)
            for series in (close_j / prev, open_j / prev):
                suspect |= (series >= ratio) | (series <= 1.0 / ratio)
            previous_close, running = prev, close_j
            snapshots[step] = suspect.copy()
    return snapshots


def compute_validity(panel: Panel, horizons: Sequence[int], ca_ratio: float) -> LabelValidity:
    """Label validity for every ``(session, ticker)`` and every requested horizon.

    The CA window is the declared one and does not follow the horizon below 10: D0 states
    ``h_le_10`` as "the C mask as is", a single D..D+10 window shared by h = 1, 3, 5 and 10.
    """
    wanted = tuple(sorted({int(h) for h in horizons}))
    if not wanted or wanted[0] < 1:
        raise HardFail("R1", f"label horizons {wanted} are not positive")
    factor, _, _ = panel.split_arrays()
    open_p, close_p = panel.open / factor, panel.close / factor
    reference = _forward(open_p, 1, np.nan)
    no_entry_bar = np.isnan(reference) | (reference <= 0)

    ceiling = max(C_LABEL_CA_HORIZON, max(wanted))
    snapshots = _ca_suspect_series(open_p, close_p, reference, ca_ratio, ceiling)
    has_bar = ~np.isnan(panel.close)

    valid: dict[int, np.ndarray] = {}
    missing: dict[int, np.ndarray] = {}
    suspect: dict[int, np.ndarray] = {}
    for horizon in wanted:
        window_end = min(max(horizon, C_LABEL_CA_HORIZON), ceiling)
        suspect[horizon] = snapshots[window_end] & ~no_entry_bar
        missing[horizon] = ~_forward(has_bar, horizon, False)
        valid[horizon] = ~no_entry_bar & ~missing[horizon] & ~suspect[horizon]
    return LabelValidity(wanted, valid, no_entry_bar, missing, suspect)
