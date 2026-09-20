"""The date's query sample: which eligible tickers become queries, decided before any label.

D0 ``evaluation.query_sampling`` orders the date's eligible tickers by
``sha256('Q|20260917|<D>|<ticker>')`` and keeps the first 300. The order depends on the session
date and the ticker string and on nothing else, so the sample of a date is the same in every
run, under every test, and - the point of the rule - cannot have been chosen with a label in
view. This module deliberately imports no label code at all (R13, test T13).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

from app.backtest.strategy_d_analog.identity import query_sample
from app.backtest.strategy_d_analog.models import HardFail


@dataclass(frozen=True)
class QuerySample:
    """One date's sample, in hash order. ``sample_rank`` is the position in that order."""

    query_date_idx: int
    session: date
    tickers: tuple[str, ...]
    ticker_col: np.ndarray  # (n,) int32 into Panel.tickers
    eligible_count: int

    def __len__(self) -> int:
        return len(self.tickers)

    @property
    def sample_rank(self) -> np.ndarray:
        return np.arange(len(self.tickers), dtype=np.int16)


def sample_date(query_date_idx: int, session: date, eligible_tickers: Sequence[str],
                column_of: dict[str, int], limit: int) -> QuerySample:
    """The first ``limit`` eligible tickers of the date in hash order (D0 ``query_sampling``)."""
    if limit <= 0:
        raise HardFail("R1", f"queries_per_date is {limit}")
    chosen = query_sample(session.isoformat(), eligible_tickers, limit)
    cols = np.array([column_of[t] for t in chosen], dtype=np.int32)
    return QuerySample(query_date_idx, session, chosen, cols, len(eligible_tickers))
