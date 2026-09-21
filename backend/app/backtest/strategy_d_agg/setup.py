"""Session top-quantile selection on A(q). Reads the signal, never an outcome.

D0 ``setup.rule``: per session, order the signal-OK queries by A(q) descending, ties by
``sample_rank`` ascending, and take the first ``ceil(q * n_ok)``. The selection is made before
label validity is known; invalid rows are dropped afterwards by the caller and counted.

The quantile is taken as an exact fraction so the quota is ``ceil`` of an exact product. For the
declared quantiles (0.10, 0.05, 0.02) and every n in 1..1000 the float product happens to give
the same ceiling (checked), so this is a guarantee rather than a correction: a quota never depends
on how a decimal rounds in binary.

Import rule (tested): no module of this package that can see a future bar - ``excursions``,
``d1``, any label module - is imported here.
"""

from fractions import Fraction

import numpy as np

from app.backtest.strategy_d_agg.models import HardFail


def exact_fraction(value) -> Fraction:
    """A declared quantile as an exact fraction (``0.10`` -> 1/10, not the binary double)."""
    return Fraction(str(value))


def quota(n_ok: int, quantile: Fraction) -> int:
    """``ceil(quantile * n_ok)`` in exact arithmetic."""
    if n_ok < 0 or not (0 < quantile <= 1):
        raise HardFail("R1", f"quota({n_ok}, {quantile})")
    product = quantile * n_ok
    return int(-(-product.numerator // product.denominator))


def select(session_idx: np.ndarray, sample_rank: np.ndarray, analog: np.ndarray,
           signal_ok: np.ndarray, quantile: Fraction) -> np.ndarray:
    """Boolean mask of the selected rows. Arrays are row-aligned query columns."""
    if not (session_idx.shape == sample_rank.shape == analog.shape == signal_ok.shape):
        raise HardFail("R5", "setup selection got misaligned arrays")
    if not np.isfinite(analog[signal_ok]).all():
        raise HardFail("R5", "a signal-OK query has a non-finite A(q)")
    chosen = np.zeros(session_idx.shape, dtype=bool)
    candidates = np.flatnonzero(signal_ok)
    # sort: session asc, A desc, sample_rank asc
    order = candidates[np.lexsort((sample_rank[candidates], -analog[candidates],
                                   session_idx[candidates]))]
    sessions, starts = np.unique(session_idx[order], return_index=True)
    ends = np.append(starts[1:], order.size)
    for start, end in zip(starts, ends):
        chosen[order[start: start + quota(int(end - start), quantile)]] = True
    return chosen
