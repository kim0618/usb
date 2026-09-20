"""A(q): the median forward excess return of a query's Top-K structure analogs.

This module is deliberately the smallest one in the package, and deliberately ignorant. It takes
an array of numbers and a group size and returns one number per group. It does not know what the
numbers are, which query they belong to, how far away the neighbour was, or what the query's own
future did - and it cannot find out, because there is no argument through which any of that could
arrive and no import through which it could be fetched.

That ignorance is the declared contract, not a style choice:

* the signal is the plain median of the accepted fifty (no mean, no trimmed mean, no weighted
  median, no top-20 subset);
* every neighbour carries equal weight, so the D2 distance never enters the arithmetic;
* the query's own realized label lives in ``evaluation_labels`` and is joined only in D-V2A-4.
"""

from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_d_v2.models import HardFail

SIGNAL_CONTRACT = "d-v2a-analog-signal-median-v1"


@dataclass(frozen=True)
class AnalogSignal:
    """One value per group, plus the group size it was built from."""

    values: np.ndarray
    group_size: int
    groups: int

    def __len__(self) -> int:
        return int(self.values.shape[0])


def median_by_group(values: np.ndarray, group_size: int) -> AnalogSignal:
    """The declared signal: ``median`` of each contiguous block of ``group_size`` values.

    Contiguity is the caller's contract and is checked by the caller (D3 asserts that the
    neighbour rows arrive in query order, rank 1..K). Here the rules are arithmetic: the array
    must divide evenly, every value must be finite, and the statistic is ``np.median`` - which
    for an even group averages the two middle values, the ordinary definition of a median and
    the one V1's signal used.
    """
    array = np.asarray(values, dtype=np.float64)
    if group_size <= 0:
        raise HardFail("R1", f"group size {group_size} is not positive")
    if array.ndim != 1:
        raise HardFail("R5", f"analog signal got an array of shape {array.shape}")
    if array.shape[0] % group_size:
        raise HardFail("R5", f"{array.shape[0]} values do not divide into groups of {group_size}")
    if array.size and not np.isfinite(array).all():
        raise HardFail("R12", f"{int((~np.isfinite(array)).sum())} neighbour labels are not finite;"
                              " a group with a missing label is a parent contract violation,"
                              " never something to median around")
    blocks = array.reshape(-1, group_size)
    return AnalogSignal(np.median(blocks, axis=1), group_size, int(blocks.shape[0]))


def assert_is_the_declared_median(signal: AnalogSignal, values: np.ndarray) -> None:
    """R1: the stored signal is the median of the same numbers it claims to summarise."""
    blocks = np.asarray(values, dtype=np.float64).reshape(-1, signal.group_size)
    if not np.array_equal(np.median(blocks, axis=1), signal.values):
        raise HardFail("R1", "A(q) disagrees with the median of its own neighbour labels")
