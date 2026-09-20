"""B0: the equal-weight signed rank composite the analog will have to beat.

``B0(q) = (1/10) * sum_i s_i * r_i(q)`` - ten declared signs, ten equal weights, no neighbours
and no fitting. The signs come from ``d_v2a_rules_v1.json`` §baseline_B0 and the prior-strength
labels beside them are metadata: STRONG and WEAK describe how confident the declaration is, and
neither may touch a weight (contract §6.4).

The module takes a rank matrix and nothing else. It has no way to reach a label, which is the
point: D-V2A-1 is allowed to produce B0 values and is forbidden from learning whether they were
any good.
"""

from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_d_v2.config import FEATURE_NAMES, V2ARules
from app.backtest.strategy_d_v2.models import HardFail

B0_CONTRACT = "d-v2a-b0-equal-weight-signed-rank-v1"


@dataclass(frozen=True)
class Composite:
    """One composite per input row, plus the sign vector it was built with."""

    values: np.ndarray
    signs: tuple[int, ...]
    names: tuple[str, ...]

    def __len__(self) -> int:
        return int(self.values.shape[0])


def composite(rank_matrix: np.ndarray, signs: tuple[int, ...],
              names: tuple[str, ...] = FEATURE_NAMES) -> Composite:
    """The declared composite over an ``(n, k)`` rank matrix in coordinate order."""
    matrix = np.asarray(rank_matrix, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != len(names):
        raise HardFail("R5", f"B0 got a rank matrix of shape {matrix.shape} for {len(names)} names")
    if len(signs) != len(names):
        raise HardFail("R1", f"B0 got {len(signs)} signs for {len(names)} coordinates")
    if any(s not in (-1, 1) for s in signs):
        raise HardFail("R1", f"B0 signs must be +1 or -1: {signs}")
    weights = np.asarray(signs, dtype=np.float64) / float(len(names))
    return Composite(matrix @ weights, tuple(signs), tuple(names))


def build(rank_matrix: np.ndarray, rules: V2ARules) -> Composite:
    """B0 from the declaration: equal weights, declared signs, coordinate order."""
    weights = str(rules.raw["baseline_B0"]["weights"])
    if "equal" not in weights.lower():
        raise HardFail("R1", f"B0 weights are declared as {weights!r}")
    if bool(rules.raw["baseline_B0"]["uses_neighbors"]):
        raise HardFail("R1", "B0 is declared to use neighbours; it must not")
    return composite(rank_matrix, rules.b0_sign_vector, FEATURE_NAMES)


def strong_only(rank_matrix: np.ndarray, rules: V2ARules) -> Composite:
    """``B0-strong``: the six STRONG-prior coordinates, declared as a secondary diagnostic.

    Built here so the sign metadata has exactly one use, and that use is a reported diagnostic
    rather than a weight. It is never the primary comparator.
    """
    names = rules.b0_strong_names
    index = [FEATURE_NAMES.index(name) for name in names]
    signs = tuple(rules.b0_signs[name] for name in names)
    return composite(np.asarray(rank_matrix, dtype=np.float64)[:, index], signs, names)
