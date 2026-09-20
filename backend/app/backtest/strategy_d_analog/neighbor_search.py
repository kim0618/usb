"""Top-K historical analogue search: embargo, identity exclusions, concentration caps, ranking.

The order of the four policies is not interchangeable and D0 fixes it. Exclusions that can never
be accepted - the query's own ticker, its own company - are removed before ranking, because a
candidate that is skipped and a candidate that was never there give the same greedy result. The
caps are the opposite case and must be applied *after* ranking: if a ticker's best window is
blocked by a full date, that ticker's second-best window can still be accepted later, so any
"keep one window per ticker first" shortcut quietly changes the answer (test V17b).

Only the top of the ranking is materialised. Greedy acceptance of an element depends on the
elements ahead of it and on nothing behind it, so running the greedy over the sorted first M is
the prefix of running it over everything; if K are accepted the answer is already final, and if
not, M doubles. The pool boundary is taken as "everything at least as good as the M-th value",
ties included, so a tie can never be cut in half by the optimisation (test V20, §26).
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_d_analog import pit, similarity
from app.backtest.strategy_d_analog.models import HardFail, QueryStatus

#: Initial candidate pool, 8x the declared K. Not a rule: widening it cannot change any result,
#: only how many passes the search needs. The distribution of final M values is reported.
INITIAL_M = 400
#: Queries scored per matrix product. Fixed and recorded in the run identity because the block
#: shape is part of what makes a re-run bit-identical (D2 §9.3).
QUERY_CHUNK = 50


@dataclass(frozen=True)
class QueryOutcome:
    """One query under one test: the accepted neighbours and the counters behind the decision."""

    status: QueryStatus
    rows: np.ndarray            # (n,) int64 library rows, rank 1..n in order
    metric_value: np.ndarray    # (n,) float64 in the metric's own units
    rank_score: np.ndarray      # (n,) float64, larger is more similar
    candidates_after_embargo: int
    candidates_after_same_symbol: int
    pool_final_m: int
    embargo_limit_idx: int

    @property
    def accepted_count(self) -> int:
        return int(self.rows.size)


def greedy_caps(order: Sequence[int], ticker_col: np.ndarray, end_idx: np.ndarray, *,
                top_k: int, ticker_cap: int, date_cap: int) -> list[int]:
    """Walk the ranking once, taking a candidate unless it would break either concentration cap."""
    accepted: list[int] = []
    per_ticker: dict[int, int] = {}
    per_date: dict[int, int] = {}
    for row in order:
        ticker = int(ticker_col[row])
        if per_ticker.get(ticker, 0) >= ticker_cap:
            continue
        end = int(end_idx[row])
        if per_date.get(end, 0) >= date_cap:
            continue
        per_ticker[ticker] = per_ticker.get(ticker, 0) + 1
        per_date[end] = per_date.get(end, 0) + 1
        accepted.append(int(row))
        if len(accepted) == top_k:
            break
    return accepted


def select(rank_score: np.ndarray, drop_mask: np.ndarray, ticker_col: np.ndarray,
           end_idx: np.ndarray, *, top_k: int, ticker_cap: int, date_cap: int,
           initial_m: int = INITIAL_M) -> tuple[list[int], int]:
    """Accept up to ``top_k`` candidates, expanding the pool until the answer cannot change."""
    scores = np.where(drop_mask, -np.inf, rank_score)
    live = int((~drop_mask).sum())
    if live == 0:
        return [], 0
    size = scores.shape[0]
    pool_m = min(max(initial_m, 1), live)
    while True:
        threshold = np.partition(scores, size - pool_m)[size - pool_m]
        pool = np.nonzero((scores >= threshold) & ~drop_mask)[0]
        order = pool[np.argsort(-scores[pool], kind="stable")]
        accepted = greedy_caps(order, ticker_col, end_idx, top_k=top_k, ticker_cap=ticker_cap,
                               date_cap=date_cap)
        if len(accepted) == top_k or pool_m == live:
            return accepted, pool_m
        pool_m = min(2 * pool_m, live)


def select_full_sort(rank_score: np.ndarray, drop_mask: np.ndarray, ticker_col: np.ndarray,
                     end_idx: np.ndarray, *, top_k: int, ticker_cap: int,
                     date_cap: int) -> list[int]:
    """The unoptimised answer: rank every live candidate, then apply the caps. Reference only.

    Kept in the shipped module rather than in the tests so the equivalence check compares the
    optimisation against code that takes the same inputs through the same tie-break, and so a
    later change to the ranking rule cannot be made in one place and forgotten in the other.
    """
    live = np.nonzero(~drop_mask)[0]
    order = live[np.argsort(-rank_score[live], kind="stable")]
    return greedy_caps(order, ticker_col, end_idx, top_k=top_k, ticker_cap=ticker_cap,
                       date_cap=date_cap)


def embargo_cut(end_idx: np.ndarray, view: pit.EmbargoView) -> int:
    """Where the usable prefix of the library ends for this query (D2 §10.4, §11 step 1)."""
    cut = int(np.searchsorted(end_idx, view.limit_idx, side="right"))
    view.assert_candidates(end_idx[:cut], "library prefix")
    return cut


def drop_same_symbol(ticker_col: np.ndarray, figi_code: np.ndarray, query_ticker_col: int,
                     query_figi_code: int) -> np.ndarray:
    """D0 ``same_symbol_policy``: the ticker match always, the FIGI match only when both exist."""
    drop = ticker_col == query_ticker_col
    if query_figi_code >= 0:
        drop = drop | (figi_code == query_figi_code)
    return drop


def search_block(*, metric: similarity.Metric, query_vectors: np.ndarray,
                 library_vectors: np.ndarray, library_sq_norm: np.ndarray | None,
                 ticker_col: np.ndarray, end_idx: np.ndarray, figi_code: np.ndarray,
                 query_ticker_col: np.ndarray, query_figi_code: np.ndarray,
                 view_of, top_k: int, ticker_cap: int, date_cap: int,
                 initial_m: int = INITIAL_M) -> list[QueryOutcome]:
    """Score one chunk of queries against the embargoed library prefix and select each one's K.

    Every query in the chunk shares the same prefix, because the embargo bound depends on the
    query date, the window and the horizon - all constant within a test and a date - and never
    on the query's ticker.
    """
    if query_vectors.shape[0] == 0:
        return []
    scores = similarity.score_block(metric, query_vectors, library_vectors, library_sq_norm)
    outcomes: list[QueryOutcome] = []
    for row in range(query_vectors.shape[0]):
        view = view_of(row)
        drop = drop_same_symbol(ticker_col, figi_code, int(query_ticker_col[row]),
                                int(query_figi_code[row]))
        accepted, pool_m = select(scores.rank_score[row], drop, ticker_col, end_idx, top_k=top_k,
                                  ticker_cap=ticker_cap, date_cap=date_cap, initial_m=initial_m)
        chosen = np.asarray(accepted, dtype=np.int64)
        pit.assert_neighbors(view=view, rows=chosen, end_idx=end_idx, ticker_col=ticker_col,
                             figi_code=figi_code, query_ticker_col=int(query_ticker_col[row]),
                             query_figi_code=int(query_figi_code[row]), ticker_cap=ticker_cap,
                             date_cap=date_cap, top_k=top_k)
        status = QueryStatus.OK if chosen.size == top_k else QueryStatus.INSUFFICIENT_NEIGHBORS
        outcomes.append(QueryOutcome(
            status=status, rows=chosen,
            metric_value=scores.metric_value[row][chosen] if chosen.size else np.empty(0),
            rank_score=scores.rank_score[row][chosen] if chosen.size else np.empty(0),
            candidates_after_embargo=int(ticker_col.shape[0]),
            candidates_after_same_symbol=int(ticker_col.shape[0] - drop.sum()),
            pool_final_m=pool_m, embargo_limit_idx=view.limit_idx))
    return outcomes


def assert_ranks(outcome: QueryOutcome, top_k: int) -> None:
    """R10: ranks are 1..n with no gap and no repeated library row, as written to the artifact."""
    if outcome.rows.size > top_k:
        raise HardFail("R10", f"{outcome.rows.size} neighbours for a top_k of {top_k}")
    if np.unique(outcome.rows).size != outcome.rows.size:
        raise HardFail("R10", "a library row appears twice in one neighbour list")
