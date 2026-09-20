"""N1 and N2: what the analog signal has to beat before it counts as information.

D0 declares two baselines and they answer different questions. N1 keeps everything about D
except the similarity - same library, same embargo, same caps, same top_k, same median-of-
neighbours signal - and picks the neighbours at random inside the query's own volatility
quintile. If D cannot beat N1, the pattern matching is doing nothing that a coin could not.
N2 asks the other question: is D's signal anything more than five ordinary price features, as a
competing kNN (N2a) and as a regression D must survive the residual of (N2b)?

DECLARED DEVIATION - N1 draw order
----------------------------------
D0 ``baseline_N1.draw`` orders candidates by ``sha256('N1|seed|replicate|query_date|
query_ticker|lib_date|lib_ticker')`` - one hash per (replicate, query, candidate). Measured on
this machine that is 5.57e11 hashes, about 86 single-core hours, and it is the only part of the
declared study that cannot be run. This module keeps every property the baseline depends on -
a uniform draw without replacement from the same candidate pool, deterministic in (replicate,
query), no global random state, the same caps and top_k - by seeding a PCG64 stream with
``sha256`` of D0's own key *prefix* (everything up to the candidate) and taking a uniformly
random ordered prefix of the candidate permutation. The distribution of the drawn set and its
order is identical to the declared recipe; the particular permutation is not. This is recorded
in the run identity as ``n1_draw_contract`` and reported as a deviation, not hidden as a detail.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib

import numpy as np

from app.backtest.strategy_d_analog import neighbor_search, pit, similarity
from app.backtest.strategy_d_analog.models import HardFail

#: How much of the random permutation is materialised before the caps are applied. Large enough
#: that the date cap (5 per library end date) can never be what stops a draw reaching top_k.
N1_POOL = 2000
N1_REPLICATES = 20
N1_SEED = "20260917"
N1_DRAW_CONTRACT = "d-n1-draw-v1-prefix-seeded"
N2A_CONTRACT = "d-n2a-feature-knn-v1"
N2B_CONTRACT = "d-n2b-orthogonalized-v1"


def n1_seed(replicate: int, query_session: str, query_ticker: str) -> int:
    """D0's key prefix, hashed once per (replicate, query) instead of once per candidate."""
    body = f"N1|{N1_SEED}|{replicate}|{query_session}|{query_ticker}"
    return int.from_bytes(hashlib.sha256(body.encode("utf-8")).digest(), "big")


def draw_random_analogs(*, candidates: np.ndarray, ticker_col: np.ndarray, end_idx: np.ndarray,
                        query_ticker_col: int, query_figi_code: int, figi_code: np.ndarray,
                        seed: int, top_k: int, ticker_cap: int, date_cap: int,
                        pool: int = N1_POOL) -> list[int]:
    """One replicate's random neighbour set: uniform draw, same-symbol dropped, then the caps.

    Dropping the query's own symbol after the draw rather than before is deliberate and gives the
    same answer: a candidate that can never be accepted consumes no cap, so removing it from the
    pool and skipping it in the walk are the same thing.
    """
    if candidates.shape[0] == 0:
        return []
    generator = np.random.Generator(np.random.PCG64(seed))
    size = min(pool, candidates.shape[0])
    order = generator.choice(candidates.shape[0], size=size, replace=False, shuffle=True)
    drawn = candidates[order]
    keep = ticker_col[drawn] != query_ticker_col
    if query_figi_code >= 0:
        keep &= figi_code[drawn] != query_figi_code
    drawn = drawn[keep]
    accepted = neighbor_search.greedy_caps(np.arange(drawn.shape[0]), ticker_col[drawn],
                                           end_idx[drawn], top_k=top_k, ticker_cap=ticker_cap,
                                           date_cap=date_cap)
    return [int(drawn[i]) for i in accepted]


@dataclass
class LibraryContext:
    """Everything a baseline needs about one ``(W, h)`` library - identities, never path vectors."""

    window: int
    horizon: int
    row: np.ndarray            # library_meta row index, ascending in (end_idx, ticker)
    end_idx: np.ndarray
    ticker_col: np.ndarray
    figi_code: np.ndarray
    excess: np.ndarray         # the window's realized excess_return_h
    quintile: np.ndarray       # realized-volatility quintile at its own end date
    features: np.ndarray       # (L, 5) standardized features at its own end date
    finite: np.ndarray         # every feature finite

    def __len__(self) -> int:
        return int(self.end_idx.shape[0])

    def cut_for(self, query_end_idx: int) -> int:
        view = pit.EmbargoView(query_end_idx, self.window, self.horizon)
        cut = int(np.searchsorted(self.end_idx, view.limit_idx, side="right"))
        view.assert_candidates(self.end_idx[:cut], "baseline library prefix")
        return cut


def quintile_pools(context: LibraryContext, cut: int) -> list[np.ndarray]:
    """Candidate indices per volatility quintile within the embargoed prefix (D0 N1 matching)."""
    bucket = context.quintile[:cut]
    return [np.nonzero(bucket == value)[0] for value in range(5)]


def n1_signal(*, context: LibraryContext, cut: int, pools: Sequence[np.ndarray],
              query_session: str, query_tickers: Sequence[str], query_ticker_col: np.ndarray,
              query_figi_code: np.ndarray, query_quintile: np.ndarray, top_k: int,
              ticker_cap: int, date_cap: int, replicates: int = N1_REPLICATES,
              ) -> tuple[np.ndarray, np.ndarray]:
    """``(replicates, queries)`` random-analog signals and the neighbour count behind each."""
    signals = np.full((replicates, len(query_tickers)), np.nan)
    counts = np.zeros((replicates, len(query_tickers)), dtype=np.int16)
    for replicate in range(replicates):
        for position, ticker in enumerate(query_tickers):
            bucket = int(query_quintile[position])
            if bucket < 0:
                continue
            accepted = draw_random_analogs(
                candidates=pools[bucket], ticker_col=context.ticker_col[:cut],
                end_idx=context.end_idx[:cut], query_ticker_col=int(query_ticker_col[position]),
                query_figi_code=int(query_figi_code[position]),
                figi_code=context.figi_code[:cut],
                seed=n1_seed(replicate, query_session, ticker), top_k=top_k,
                ticker_cap=ticker_cap, date_cap=date_cap)
            counts[replicate, position] = len(accepted)
            if len(accepted) == top_k:
                signals[replicate, position] = float(np.median(context.excess[np.asarray(accepted)]))
    return signals, counts


def n2a_signal(*, context: LibraryContext, cut: int, query_features: np.ndarray,
               query_ticker_col: np.ndarray, query_figi_code: np.ndarray, top_k: int,
               ticker_cap: int, date_cap: int, chunk: int = neighbor_search.QUERY_CHUNK,
               ) -> tuple[np.ndarray, np.ndarray]:
    """D's pipeline with the path swapped for five features: same policies, same selection.

    Using the shipped ``score_block`` and ``select`` rather than a private copy is the point -
    N2a must differ from D in the distance and in nothing else, or the comparison is unfair in a
    direction nobody can see.
    """
    count = query_features.shape[0]
    signals = np.full(count, np.nan)
    accepted_counts = np.zeros(count, dtype=np.int16)
    if cut == 0 or count == 0:
        return signals, accepted_counts
    library = np.ascontiguousarray(context.features[:cut])
    norms = np.einsum("ij,ij->i", library, library)
    usable = np.nonzero(np.isfinite(query_features).all(axis=1))[0]
    for start in range(0, usable.shape[0], chunk):
        block = usable[start:start + chunk]
        scores = similarity.score_block(similarity.Metric.EUCLIDEAN, query_features[block],
                                        library, norms)
        for offset, position in enumerate(block):
            drop = neighbor_search.drop_same_symbol(
                context.ticker_col[:cut], context.figi_code[:cut],
                int(query_ticker_col[position]), int(query_figi_code[position]))
            drop |= ~context.finite[:cut]
            chosen, _ = neighbor_search.select(scores.rank_score[offset], drop,
                                               context.ticker_col[:cut], context.end_idx[:cut],
                                               top_k=top_k, ticker_cap=ticker_cap,
                                               date_cap=date_cap)
            accepted_counts[position] = len(chosen)
            if len(chosen) == top_k:
                signals[position] = float(np.median(context.excess[np.asarray(chosen)]))
    return signals, accepted_counts


def orthogonalize(signal_rank: np.ndarray, features: np.ndarray) -> np.ndarray:
    """D0 N2b: OLS of rank(S) on the five standardized features plus an intercept; return residual.

    Least squares rather than a correlation, so the residual is what is left of the signal's
    ranking after *any* linear combination of the five features has been taken out of it.
    """
    if signal_rank.shape[0] != features.shape[0]:
        raise HardFail("R5", "orthogonalization got mismatched rows")
    if signal_rank.shape[0] <= features.shape[1] + 1:
        return np.full(signal_rank.shape, np.nan)
    design = np.column_stack([np.ones(features.shape[0]), features])
    coefficients, *_ = np.linalg.lstsq(design, signal_rank, rcond=None)
    return signal_rank - design @ coefficients


def contracts() -> Mapping[str, str]:
    return {"n1_draw_contract": N1_DRAW_CONTRACT, "n1_pool": N1_POOL,
            "n1_replicates": N1_REPLICATES, "n2a_contract": N2A_CONTRACT,
            "n2b_contract": N2B_CONTRACT}
