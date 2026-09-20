"""The query's own realized future - D4's answer key, kept out of the signal path.

Everything here reads sessions after the query date. That is legitimate for evaluating a signal
and fatal for generating one, so the two live in different files and ``signal.py`` may not import
this one (an AST test enforces it). D3 produces this table and does nothing else with it: no
mean, no direction agreement, no IC, no quintile. Reading it is D4's job.

The label definitions are the same ones the neighbours were scored with - D0 ``labels.applies_to``
says so explicitly - so a query and a neighbour are never measured by different rules.
"""

from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_d_analog.labels import ForwardLabels, gather
from app.backtest.strategy_d_analog.models import HardFail


@dataclass(frozen=True)
class EvaluationLabels:
    """One row per (query date, sampled ticker, horizon): the realized excess return and validity.

    Stored per horizon rather than per TestId because the query's future does not depend on which
    window length or representation selected its analogues; D4 joins it on ``(query, horizon)``.
    """

    horizon: int
    query_date_idx: np.ndarray
    sample_rank: np.ndarray
    ticker_col: np.ndarray
    forward_return: np.ndarray      # excess_return_h, NaN where the label is invalid
    close_return: np.ndarray        # the raw clipped close return behind it
    label_valid: np.ndarray

    def __len__(self) -> int:
        return int(self.query_date_idx.shape[0])

    def counts(self) -> dict[str, int]:
        return {"rows": len(self), "valid": int(self.label_valid.sum()),
                "invalid": int((~self.label_valid).sum())}


def build(labels: ForwardLabels, horizon: int, query_date_idx: np.ndarray,
          sample_rank: np.ndarray, ticker_col: np.ndarray) -> EvaluationLabels:
    """Look up each sampled query's own realized label. No aggregation happens here."""
    if not (query_date_idx.shape == sample_rank.shape == ticker_col.shape):
        raise HardFail("R5", "evaluation label inputs have different lengths")
    sessions = query_date_idx.astype(np.int64)
    columns = ticker_col.astype(np.int64)
    valid = gather(labels.valid[horizon], sessions, columns).astype(bool)
    excess = gather(labels.excess_for(horizon), sessions, columns)
    close = gather(labels.close_return[horizon], sessions, columns)
    # A valid label must produce a finite excess; the market median of its date always exists,
    # because the query itself is an eligible, label-valid member of that date's universe.
    if valid.any() and not np.isfinite(excess[valid]).all():
        raise HardFail("R5", f"h={horizon}: a valid query label has a non-finite excess return")
    return EvaluationLabels(horizon, query_date_idx.astype(np.int32),
                            sample_rank.astype(np.int16), ticker_col.astype(np.int32),
                            np.where(valid, excess, np.nan), np.where(valid, close, np.nan), valid)
