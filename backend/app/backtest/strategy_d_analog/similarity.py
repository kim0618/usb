"""The two declared similarity measures, as block matrix products (D0 ``similarity_methods``).

Representation A is scored by Pearson correlation and representation B by Euclidean distance.
Both come out of one ``gemm`` per block, which is what makes an exact search over a quarter of a
million windows affordable; the alternative - building a difference vector per pair - costs the
study about a day and buys nothing.

Two values leave this module for every pair: the measure in its own units and a ``rank_score``
that is always "larger is more similar". Ordering uses the rank score alone, so nothing
downstream has to remember which way a metric points.
"""

from dataclasses import dataclass
from enum import Enum

import numpy as np

from app.backtest.strategy_d_analog import pit
from app.backtest.strategy_d_analog.models import HardFail


class Metric(str, Enum):
    PEARSON = "pearson"      # representation A
    EUCLIDEAN = "euclidean"  # representation B


METRIC_OF = {"A": Metric.PEARSON, "B": Metric.EUCLIDEAN}


@dataclass(frozen=True)
class Scores:
    """``(q, L)`` blocks: the metric in its own units, and the orientation-free ranking value."""

    metric: Metric
    metric_value: np.ndarray
    rank_score: np.ndarray


def library_sq_norm(library: np.ndarray) -> np.ndarray:
    """``||l||^2`` per library row, computed once so the distance expansion needs one product."""
    return np.einsum("ij,ij->i", library, library)


def score_block(metric: Metric, queries: np.ndarray, library: np.ndarray,
                library_sq_norm_values: np.ndarray | None = None) -> Scores:
    """Score one block of queries against one slice of the library.

    Neither side may carry an undefined vector. A constant window has no z-normalized direction
    and is dropped by the library builder and the query builder before this point, so a zero
    row arriving here is a broken caller rather than a degenerate input to absorb (V11).
    """
    if queries.ndim != 2 or library.ndim != 2 or queries.shape[1] != library.shape[1]:
        raise HardFail("R12", f"score block shapes {queries.shape} and {library.shape} disagree")
    if metric is Metric.PEARSON:
        if queries.size and (np.ptp(queries, axis=1) == 0).any():
            raise HardFail("R12", "a constant query vector reached the Pearson block")
        if library.size and (np.ptp(library, axis=1) == 0).any():
            raise HardFail("R12", "a constant library vector reached the Pearson block")
        rho = (queries @ library.T) / queries.shape[1]
        pit.assert_finite(rho, "pearson block")
        return Scores(metric, rho, rho)

    norms = library_sq_norm(library) if library_sq_norm_values is None else library_sq_norm_values
    if norms.shape[0] != library.shape[0]:
        raise HardFail("R12", "library square norms do not match the library block")
    gram = queries @ library.T
    query_norm = np.einsum("ij,ij->i", queries, queries)
    # The expansion cancels catastrophically for near-identical vectors and can land a few ulps
    # below zero; clipping there is rounding repair, not a floor on the metric.
    squared = np.maximum(query_norm[:, None] + norms[None, :] - 2.0 * gram, 0.0)
    distance = np.sqrt(squared)
    pit.assert_finite(distance, "euclidean block")
    return Scores(metric, distance, -distance)
