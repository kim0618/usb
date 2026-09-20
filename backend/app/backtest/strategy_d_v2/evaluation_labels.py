"""The only V2-A module that turns future bars into numbers.

Two different consumers need forward excess returns and they must use one definition, so there
is one implementation: V1's ``labels.compute_labels``, transcribed from the declaration and
already pinned against Strategy C's label set. D3 reads the resulting ``(session, ticker)``
matrix twice - once at the neighbours' own end dates, which is the analog signal's input, and
once at the query dates, which is the evaluation target D-V2A-4 will score against.

The separation the contract asks for is therefore not "two formulas kept in step" but "one
formula, two index sets", plus a signal module that has no way to reach the second one.

    excess_return_h(t, i) = close_return_h(t, i) - median close_return_h over every label-valid
                            eligible ticker of session t

Nothing here decides anything: it produces labels and their validity mask, and it never sees a
signal, a distance or a neighbour.
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_c_selection.panel import Panel
# The declared label value contract, shared with V1 and pinned against C's LabelSet.
from app.backtest.strategy_d_analog.labels import LABEL_VALUE_CONTRACT, compute_labels
from app.backtest.strategy_d_analog.label_extension import LabelValidity
from app.backtest.strategy_d_v2.models import HardFail

EVALUATION_CONTRACT = "d-v2a-forward-excess-v1"


@dataclass(frozen=True)
class ForwardExcess:
    """One horizon's ``(T, N)`` excess return matrix and the mask saying which entries are usable."""

    horizon: int
    excess: np.ndarray
    valid: np.ndarray
    value_contract: str = LABEL_VALUE_CONTRACT

    def gather(self, session_idx: np.ndarray, ticker_col: np.ndarray) -> np.ndarray:
        """Pick one label per ``(session, ticker)``. The caller decides which sessions."""
        if session_idx.shape != ticker_col.shape:
            raise HardFail("R5", "label gather got mismatched session and ticker arrays")
        if session_idx.size == 0:
            return np.empty(0, dtype=np.float64)
        if int(session_idx.max()) >= self.excess.shape[0] or int(ticker_col.max()) >= self.excess.shape[1]:
            raise HardFail("R5", "label gather addresses a row or column outside the panel")
        return self.excess[session_idx, ticker_col]

    def gather_valid(self, session_idx: np.ndarray, ticker_col: np.ndarray) -> np.ndarray:
        return self.valid[session_idx, ticker_col]


def forward_excess(panel: Panel, validity: LabelValidity, eligible: np.ndarray, horizon: int,
                   log: Callable[[str], None] = lambda _: None) -> ForwardExcess:
    """The declared excess return for one horizon, for every ``(session, ticker)``."""
    if horizon not in validity.horizons:
        raise HardFail("R1", f"label validity was not computed for h={horizon}")
    labels = compute_labels(panel, validity, eligible, log)
    excess = labels.excess_for(horizon)
    valid = validity.valid[horizon]
    if excess.shape != panel.close.shape:
        raise HardFail("R5", f"excess matrix {excess.shape} does not match the panel")
    if np.isfinite(excess[valid & eligible]).size and not np.isfinite(excess[valid & eligible]).all():
        raise HardFail("R5", f"h={horizon}: a label-valid eligible window has a non-finite excess")
    return ForwardExcess(horizon, excess, valid)


@dataclass(frozen=True)
class QueryLabels:
    """The evaluation target of one query set. Written to its own artifact, joined only in D4."""

    session_idx: np.ndarray
    ticker_col: np.ndarray
    values: np.ndarray
    valid: np.ndarray
    horizon: int

    def counts(self) -> dict[str, int]:
        return {"rows": int(self.values.shape[0]), "valid": int(self.valid.sum()),
                "invalid": int((~self.valid).sum())}


def query_labels(excess: ForwardExcess, session_idx: np.ndarray,
                 ticker_col: np.ndarray) -> QueryLabels:
    """Each query's own realized excess return, and whether it is usable.

    An invalid label is kept as NaN with its flag rather than dropped: D3 records status, and
    D-V2A-4 decides what a date's valid set is.
    """
    values = excess.gather(session_idx, ticker_col)
    valid = excess.gather_valid(session_idx, ticker_col)
    values = np.where(valid, values, np.nan)
    return QueryLabels(session_idx, ticker_col, values, valid, excess.horizon)
