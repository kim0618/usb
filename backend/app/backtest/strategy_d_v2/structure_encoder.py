"""The structure vector: ten rank coordinates assembled into one point in ``[0, 1]^10``.

This module is deliberately thin, and deliberately without a search. D-V2A-1 is allowed to build
vectors and to measure whether they exist; finding neighbours is D-V2A-2's job. What lives here
is the assembly, the all-or-nothing validity rule, and the declared distance - the last one
implemented for fixtures so that the metric D-V2A-2 will use is pinned by a test before it is
ever pointed at real data.
"""

from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_d_v2.config import FEATURE_NAMES, V2ARules
from app.backtest.strategy_d_v2.models import HardFail
from app.backtest.strategy_d_v2.structure_features import StructureFeatures

#: Equal weights, one per coordinate. Not a parameter: the declaration forbids any other vector.
EQUAL_WEIGHTS = np.ones(len(FEATURE_NAMES), dtype=np.float64)
ENCODER_CONTRACT = "d-v2a-structure-vector-v1"


@dataclass(frozen=True)
class StructureVectors:
    """``(n, 10)`` vectors for a set of ``(session, ticker)`` pairs, with their validity mask."""

    session_idx: np.ndarray
    ticker_col: np.ndarray
    vectors: np.ndarray
    defined: np.ndarray

    def __len__(self) -> int:
        return int(self.vectors.shape[0])

    @property
    def defined_count(self) -> int:
        return int(self.defined.sum())


def encode(features: StructureFeatures, session_idx: np.ndarray,
           ticker_col: np.ndarray) -> StructureVectors:
    """Gather rank coordinates into vectors. A window missing any coordinate is not imputed."""
    matrix = features.matrix(session_idx, ticker_col)
    defined = features.defined[session_idx, ticker_col]
    if matrix.shape[0] and not np.isfinite(matrix[defined]).all():
        raise HardFail("R12", "a defined structure vector carries a non-finite coordinate")
    matrix[~defined] = np.nan
    return StructureVectors(session_idx, ticker_col, matrix, defined)


def assert_equal_weights(rules: V2ARules) -> None:
    """R1: the declaration must still say equal weights before any vector is compared."""
    weights = str(rules.raw["similarity"]["weights"])
    if "equal" not in weights.lower():
        raise HardFail("R1", f"similarity weights are declared as {weights!r}")
    if rules.metric != "euclidean":
        raise HardFail("R1", f"similarity metric is declared as {rules.metric!r}")


def distance(left: np.ndarray, right: np.ndarray) -> float:
    """``||x - y||_2`` on two ten-coordinate vectors - the declared metric, equal weighted.

    Used by fixtures and by D-V2A-2. D-V2A-1 never calls it on real data: no neighbour of any
    real query is computed in this phase.
    """
    x = np.asarray(left, dtype=np.float64)
    y = np.asarray(right, dtype=np.float64)
    if x.shape != (len(FEATURE_NAMES),) or y.shape != (len(FEATURE_NAMES),):
        raise HardFail("R5", f"structure distance got shapes {x.shape} and {y.shape}")
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        raise HardFail("R12", "structure distance got an undefined vector")
    return float(np.sqrt(np.sum((x - y) ** 2)))
