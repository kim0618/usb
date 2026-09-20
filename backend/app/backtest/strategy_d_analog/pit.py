"""Runtime point-in-time invariants of the neighbour search (D2 design §18, R4~R12).

The D PIT contract is enforced in two different ways on purpose. Reads of the price panel are
made impossible rather than forbidden: ``source.AsOfView`` hands out arrays that simply end at
the as-of session, so a future row has no address. Everything that cannot be expressed as a
slice - the embargo between a neighbour's label window and the query window, the concentration
caps, the absence of the query's own symbol - is checked here after the fact, on the result that
was actually produced, so a wrong answer stops the run instead of reaching an artifact.

Nothing in this module reads a label value. ``EmbargoView`` gates the *validity* mask alone.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_d_analog.models import HardFail, PointInTimeViolation


@dataclass(frozen=True)
class EmbargoView:
    """The one gate between a query ``(D, W, h)`` and the history it is allowed to see.

    D0 ``embargo_policy.effective_rule`` is ``d + h <= D - W``: a neighbour's label window must
    be over before the query's own pattern window opens. The bound is inclusive - a neighbour
    whose label ends on the very first session of the query window is usable, because the two
    share a close price but no return (D2 design §10.2). Widening or narrowing it here would be
    a rule change, so the limit is computed from the declared expression and nothing else.
    """

    query_end_idx: int
    window: int
    horizon: int

    @property
    def limit_idx(self) -> int:
        """Largest library end index ``d`` this query may see: ``D - W - h``."""
        return self.query_end_idx - self.window - self.horizon

    def allows(self, end_idx: int) -> bool:
        return end_idx <= self.limit_idx

    def assert_candidates(self, end_idx: np.ndarray, where: str) -> None:
        """R6 / R7: every candidate, and later every accepted neighbour, clears the embargo."""
        if end_idx.size and int(end_idx.max()) > self.limit_idx:
            worst = int(end_idx.max())
            raise PointInTimeViolation(
                f"{where}: library end {worst} + h {self.horizon} > D {self.query_end_idx}"
                f" - W {self.window} (limit {self.limit_idx})")

    def label_validity(self, validity: np.ndarray, end_idx: np.ndarray) -> np.ndarray:
        """R8: reading the label validity of a window the embargo does not clear is a hard fail.

        The mask itself is decidable at ``d + h`` (D0 ``labels.why_not_c_disappeared``), so a
        read that clears the embargo needs nothing the query date does not already know.
        """
        self.assert_candidates(end_idx, "label validity requested past the embargo")
        return validity[end_idx]


def assert_finite(values: np.ndarray, where: str) -> None:
    """R12: a score built from defined vectors is finite. A NaN means a contract broke upstream."""
    if values.size and not np.isfinite(values).all():
        raise HardFail("R12", f"{where}: {int((~np.isfinite(values)).sum())} non-finite scores")


def assert_library_index(end_idx: np.ndarray, ticker_col: np.ndarray, *, stride: int,
                         minimum: int) -> None:
    """R11: rows are ``(end_idx, ticker)`` ascending, unique, on the stride, and old enough.

    The stride and the 61-bar minimum are guaranteed by construction, so a violation here means
    the library was assembled by something other than the declared rule - which is exactly the
    case worth stopping for (D1 Pre-flight §11.1 R11).
    """
    if end_idx.shape != ticker_col.shape:
        raise HardFail("R11", "library end index and ticker columns disagree in length")
    if end_idx.size == 0:
        return
    off_stride = end_idx[end_idx % stride != 0]
    if off_stride.size:
        raise HardFail("R11", f"{off_stride.size} library rows are off the stride of {stride}"
                              f", e.g. end {int(off_stride[0])}")
    if int(end_idx.min()) < minimum:
        raise HardFail("R11", f"library row ends at {int(end_idx.min())}, before index {minimum}")
    if end_idx.size < 2:
        return
    step = np.diff(end_idx.astype(np.int64) * (int(ticker_col.max()) + 1) + ticker_col.astype(np.int64))
    if (step <= 0).any():
        raise HardFail("R11", "library rows are not strictly ascending in (end_idx, ticker)")


def assert_neighbors(*, view: EmbargoView, rows: Sequence[int], end_idx: np.ndarray,
                     ticker_col: np.ndarray, figi_code: np.ndarray, query_ticker_col: int,
                     query_figi_code: int, ticker_cap: int, date_cap: int, top_k: int) -> None:
    """R7, R9, R10 on one accepted neighbour list, in the order it will be written out."""
    chosen = np.asarray(rows, dtype=np.int64)
    if chosen.size > top_k:
        raise HardFail("R10", f"{chosen.size} neighbours accepted for a top_k of {top_k}")
    if np.unique(chosen).size != chosen.size:
        raise HardFail("R10", "the same library row was accepted twice")
    if chosen.size == 0:
        return
    ends, tickers, figis = end_idx[chosen], ticker_col[chosen], figi_code[chosen]
    view.assert_candidates(ends, "accepted neighbour")
    if (tickers == query_ticker_col).any():
        raise HardFail("R9", "an accepted neighbour carries the query's own ticker")
    if query_figi_code >= 0 and (figis == query_figi_code).any():
        raise HardFail("R9", "an accepted neighbour shares the query's composite FIGI")
    _, ticker_counts = np.unique(tickers, return_counts=True)
    if ticker_counts.max() > ticker_cap:
        raise HardFail("R10", f"ticker cap {ticker_cap} broken ({int(ticker_counts.max())} windows)")
    _, date_counts = np.unique(ends, return_counts=True)
    if date_counts.max() > date_cap:
        raise HardFail("R10", f"date cap {date_cap} broken ({int(date_counts.max())} windows)")


class InvariantLog:
    """How many times each runtime invariant was checked, for ``pit_runtime.json`` (D2 §20.1)."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def record(self, code: str, times: int = 1) -> None:
        self.counts[code] = self.counts.get(code, 0) + times

    def as_dict(self) -> Mapping[str, int]:
        return dict(sorted(self.counts.items()))
