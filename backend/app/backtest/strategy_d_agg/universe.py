"""The same-date comparator population, prepared as identity and validity geometry only.

Order matters and is the point of this module: the as-of-D eligible universe comes first (V2-A
D1's rule, evaluated on an as-of view that ends at D), and only then is each member's future
window asked whether it is decidable. Nothing is dropped from the universe because of its
future; an undecidable window is kept as a row with an invalid status and counted.

What leaves this module per session: eligible_count, future_valid_count, future_invalid_count
and the invalid statuses. No outcome count (no UP10, no DN10, no MFE or MAE statistic) exists
here; D-AGG-2 computes those.
"""

from typing import Any

import numpy as np
import pyarrow as pa

from app.backtest.strategy_d_agg.excursions import Excursions
from app.backtest.strategy_d_agg.models import EXCURSION_STATUS_ORDER, STATUS_CODE, HardFail


def rows(eligible: np.ndarray, eval_start: int, eval_end: int) -> tuple[np.ndarray, np.ndarray]:
    """``(session_idx, ticker_col)`` of every eligible ticker-date in the evaluation range."""
    mask = np.zeros_like(eligible)
    mask[eval_start: eval_end + 1] = eligible[eval_start: eval_end + 1]
    session_idx, ticker_col = np.nonzero(mask)
    return session_idx.astype(np.int64), ticker_col.astype(np.int64)


def per_session(session_idx: np.ndarray, status: np.ndarray, eval_start: int,
                eval_end: int, sessions: tuple[str, ...]) -> pa.Table:
    dates = np.arange(eval_start, eval_end + 1)
    columns: dict[str, Any] = {"session_idx": dates.astype(np.int64),
                               "session": [sessions[i] for i in dates]}
    offset = session_idx - eval_start
    eligible_count = np.bincount(offset, minlength=dates.size)
    columns["eligible_count"] = eligible_count.astype(np.int64)
    valid = (status == STATUS_CODE["VALID"]) | (status == STATUS_CODE["VALID_GAP"])
    columns["future_valid_count"] = np.bincount(offset, weights=valid, minlength=dates.size).astype(np.int64)
    columns["future_invalid_count"] = columns["eligible_count"] - columns["future_valid_count"]
    for name in EXCURSION_STATUS_ORDER:
        hit = status == STATUS_CODE[name]
        columns[f"status_{name.lower()}"] = np.bincount(offset, weights=hit,
                                                        minlength=dates.size).astype(np.int64)
    table = pa.table(columns)
    if (np.asarray(columns["eligible_count"]) <= 0).any():
        raise HardFail("R5", "an evaluation session has an empty eligible universe")
    return table


def table(session_idx: np.ndarray, ticker_col: np.ndarray, tickers: tuple[str, ...],
          ex: Excursions) -> pa.Table:
    got = ex.gather(session_idx, ticker_col)
    return pa.table({
        "session_idx": session_idx, "ticker_col": ticker_col,
        "ticker": pa.array([tickers[j] for j in ticker_col], type=pa.string()),
        "entry_session_idx": session_idx + 1,
        "entry_open": got["entry_open"], "mfe_5": got["mfe"], "mae_5": got["mae"],
        "up10": got["up"], "dn10": got["down"], "excursion_valid": got["valid"],
        "status": got["status"], "window_bars": got["window_bars"],
        "missing_subclass": got["missing_subclass"]})
