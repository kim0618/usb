"""Analog search: nearest context vectors among rows the embargo and the caps still allow.

The library is held in one matrix ordered by session, so "every analog strictly older than the
embargo" is a prefix of it and a future analog is not merely filtered out, it is unreachable.
Distances are Euclidean on the [0,1] context vector with every coordinate weighted equally.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

INITIAL_POOL = 400


@dataclass(frozen=True)
class LibraryIndex:
    matrix: np.ndarray          # (L, F) float32, rows ordered by date_idx ascending
    date_idx: np.ndarray        # (L,) int32
    ticker_col: np.ndarray      # (L,) int32
    figi: np.ndarray            # (L,) int32
    rows_by_ticker: dict[int, np.ndarray]
    rows_by_figi: dict[int, np.ndarray]
    square_norm: np.ndarray     # (L,) float32
    order: np.ndarray           # (L,) int64, the permutation applied to the caller's rows

    @property
    def size(self) -> int:
        return self.matrix.shape[0]


def build_index(matrix: np.ndarray, date_idx: np.ndarray, ticker_col: np.ndarray,
                figi: np.ndarray) -> LibraryIndex:
    order = np.lexsort((ticker_col, date_idx))
    matrix = np.ascontiguousarray(matrix[order])
    date_idx = date_idx[order].astype(np.int32)
    ticker_col = ticker_col[order].astype(np.int32)
    figi = figi[order].astype(np.int32)
    by_ticker: dict[int, np.ndarray] = {}
    for value in np.unique(ticker_col):
        by_ticker[int(value)] = np.flatnonzero(ticker_col == value).astype(np.int64)
    by_figi: dict[int, np.ndarray] = {}
    for value in np.unique(figi[figi >= 0]):
        by_figi[int(value)] = np.flatnonzero(figi == value).astype(np.int64)
    square = (matrix.astype(np.float32) ** 2).sum(axis=1)
    return LibraryIndex(matrix, date_idx, ticker_col, figi, by_ticker, by_figi, square, order)


def cut_for(index: LibraryIndex, query_date_idx: int, embargo: int) -> int:
    """Number of library rows with ``date_idx <= query_date_idx - embargo``."""
    return int(np.searchsorted(index.date_idx, query_date_idx - embargo, side="right"))


def _greedy(order: np.ndarray, ticker_col: np.ndarray, date_idx: np.ndarray, figi: np.ndarray, *,
            top_k: int, ticker_cap: int, date_cap: int, figi_cap: int) -> list[int]:
    """One analog per entity and at most ``date_cap`` per analog date, nearest first.

    An entity is a ticker, and also a composite FIGI when both rows carry one: two share classes
    of one issuer are one company, so they may not both sit in a query's neighbourhood.
    """
    chosen: list[int] = []
    ticker_seen: dict[int, int] = {}
    figi_seen: dict[int, int] = {}
    date_seen: dict[int, int] = {}
    for position in order:
        ticker = int(ticker_col[position])
        code = int(figi[position])
        day = int(date_idx[position])
        if ticker_seen.get(ticker, 0) >= ticker_cap:
            continue
        if code >= 0 and figi_seen.get(code, 0) >= figi_cap:
            continue
        if date_seen.get(day, 0) >= date_cap:
            continue
        chosen.append(int(position))
        ticker_seen[ticker] = ticker_seen.get(ticker, 0) + 1
        if code >= 0:
            figi_seen[code] = figi_seen.get(code, 0) + 1
        date_seen[day] = date_seen.get(day, 0) + 1
        if len(chosen) == top_k:
            break
    return chosen


def search_date(index: LibraryIndex, queries: np.ndarray, query_tickers: Sequence[int],
                query_figis: Sequence[int], cut: int, *, top_k: int, ticker_cap: int,
                date_cap: int, figi_cap: int = 1, pool: int = INITIAL_POOL) -> list[list[int]]:
    """Top-k analogs of every query of one session. Returns library row positions, best first."""
    if cut <= 0:
        return [[] for _ in query_tickers]
    library = index.matrix[:cut]
    scores_all = index.square_norm[:cut][None, :] - 2.0 * (queries @ library.T)
    out: list[list[int]] = []
    for position, (ticker, figi) in enumerate(zip(query_tickers, query_figis)):
        scores = scores_all[position].copy()
        blocked = index.rows_by_ticker.get(int(ticker))
        if blocked is not None:
            scores[blocked[: np.searchsorted(blocked, cut)]] = np.inf
        if int(figi) >= 0:
            blocked = index.rows_by_figi.get(int(figi))
            if blocked is not None:
                scores[blocked[: np.searchsorted(blocked, cut)]] = np.inf
        size = scores.size
        window = min(size, pool)
        chosen: list[int] = []
        while True:
            if window >= size:
                candidates = np.arange(size)
            else:
                candidates = np.argpartition(scores, window - 1)[:window]
            finite = candidates[np.isfinite(scores[candidates])]
            order = finite[np.lexsort((index.ticker_col[finite], index.date_idx[finite],
                                       scores[finite]))]
            chosen = _greedy(order, index.ticker_col, index.date_idx, index.figi, top_k=top_k,
                             ticker_cap=ticker_cap, date_cap=date_cap, figi_cap=figi_cap)
            if len(chosen) >= top_k or window >= size:
                break
            window = min(size, window * 4)
        out.append(chosen)
    return out
