"""Point-in-time invariants of the V2-A coordinate system.

V1's two-layer design is kept. Reads of the price panel are made impossible rather than merely
forbidden (``source.AsOfView`` hands out arrays that end at the as-of session), and everything
that cannot be expressed as a slice is checked here on the result that was actually produced.

What changes from V1 is the embargo constant. V1 used the pattern window ``W``; V2-A has no
pattern window, so the coordinate lookback ``L`` takes its place and the rule reads
``d + h <= D - L``. The bound is computed from the declaration, never from a literal here.

Nothing in this module reads a label value. ``EmbargoView`` gates the validity mask alone.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

# V1's library ordering and index invariants are metric-agnostic and are reused as declared.
from app.backtest.strategy_d_analog.pit import assert_library_index  # noqa: F401
from app.backtest.strategy_d_v2.models import HardFail, PointInTimeViolation

PIT_CONTRACT = "d-v2a-pit-v1"


@dataclass(frozen=True)
class EmbargoView:
    """The gate between a query ``(D, h)`` and the history it may see: ``d + h <= D - L``.

    The bound is inclusive, as in V1: a library window whose label ends on the first session of
    the query's own coordinate window shares a close price with it but no return.
    """

    query_end_idx: int
    lookback: int
    horizon: int

    @property
    def limit_idx(self) -> int:
        """Largest library end index this query may see: ``D - L - h``."""
        return self.query_end_idx - self.lookback - self.horizon

    def allows(self, end_idx: int) -> bool:
        return end_idx <= self.limit_idx

    def assert_candidates(self, end_idx: np.ndarray, where: str) -> None:
        if end_idx.size and int(end_idx.max()) > self.limit_idx:
            worst = int(end_idx.max())
            raise PointInTimeViolation(
                f"{where}: library end {worst} + h {self.horizon} > D {self.query_end_idx}"
                f" - L {self.lookback} (limit {self.limit_idx})")

    def label_validity(self, validity: np.ndarray, end_idx: np.ndarray) -> np.ndarray:
        """Reading the label validity of a window the embargo does not clear is a hard fail."""
        self.assert_candidates(end_idx, "label validity requested past the embargo")
        return validity[end_idx]


def assert_backward_window(end_idx: int, lookback: int, as_of_idx: int) -> None:
    """R4: a coordinate window must lie entirely at or before the session it describes."""
    if end_idx > as_of_idx:
        raise PointInTimeViolation(f"coordinate window ends at {end_idx}, past as-of {as_of_idx}")
    if end_idx - lookback < 0:
        raise HardFail("R5", f"coordinate window {end_idx}-{lookback} starts before the grid")


def assert_volume_window_protected(lookback: int, seasoning: int) -> None:
    """Contract §4.1: unadjusted volume may only be read inside the split protection window.

    The universe rule excludes any ticker-date with a split in ``(D - seasoning, D]``, so a
    volume window that reaches no further back than ``seasoning`` cannot contain one. A longer
    volume window would need a declared ``V * F`` transform, which V2-A does not have.
    """
    if lookback > seasoning:
        raise HardFail("R5", f"volume window of {lookback} sessions reaches outside the"
                             f" (D-{seasoning}, D] split protection window")


def assert_rank_frame(population: np.ndarray, as_of_row: int) -> None:
    """R4: a date's rank frame is built from that date's own row and from no other."""
    if population.ndim != 1:
        raise HardFail("R5", f"rank frame population has shape {population.shape}")
    if as_of_row < 0:
        raise PointInTimeViolation(f"rank frame requested for row {as_of_row}")


def assert_no_label_values(names: Sequence[str]) -> None:
    """F5: nothing carrying a forward return may enter a D-V2A-1 artifact."""
    banned = ("close_return", "excess_return", "mfe", "mae", "forward_return", "label_value")
    hit = [n for n in names if any(token in str(n).lower() for token in banned)]
    if hit:
        raise HardFail("F5", f"alpha firewall: {hit} would put a label value in a D1 artifact")
