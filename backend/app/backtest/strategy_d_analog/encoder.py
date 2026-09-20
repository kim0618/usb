"""Pattern windows and the two declared representations (D0 ``representations``, D2 §6~§8).

A window is ``W+1`` closes, ``P(e-W) .. P(e)``, on the split-normalized path ``P = close / F``.
Query windows and library windows go through this one function in the same batch layout, so the
same ``(ticker, e)`` encodes to the same bits whichever role it plays (test V4b).

Nothing is repaired here. A missing bar is not interpolated, forward filled or back filled: the
universe rule has already excluded any window that spans one, so a non-finite input means a
caller broke the contract and the run stops (R5).
"""

from dataclasses import dataclass

import numpy as np

from app.backtest.strategy_d_analog.models import HardFail

REPRESENTATION_A = "A"
REPRESENTATION_B = "B"


@dataclass(frozen=True)
class Encoded:
    """One batch of vectors plus the mask of the rows for which the representation is defined."""

    vectors: np.ndarray  # (n, W+1) for A, (n, W) for B, float64, C-contiguous
    defined: np.ndarray  # (n,) bool

    @property
    def defined_count(self) -> int:
        return int(self.defined.sum())


def gather(price: np.ndarray, end_idx: np.ndarray, ticker_col: np.ndarray, window: int,
           as_of_idx: int | None = None) -> np.ndarray:
    """``(n, W+1)`` float64 of ``P(e-W..e)``, one row per ``(ticker, e)`` key, in the given order.

    ``as_of_idx`` is the R4 guard: a window may end on the as-of session but never after it. The
    array handed in is normally already an as-of view, in which case a later row has no address
    at all and this check can only fire on a caller mistake.
    """
    ends = np.asarray(end_idx, dtype=np.int64)
    cols = np.asarray(ticker_col, dtype=np.int64)
    if ends.shape != cols.shape:
        raise HardFail("R5", f"gather got {ends.size} end indices and {cols.size} tickers")
    if ends.size == 0:
        return np.empty((0, window + 1), dtype=np.float64)
    if as_of_idx is not None and int(ends.max()) > as_of_idx:
        raise HardFail("R4", f"window ends at {int(ends.max())}, past the as-of session {as_of_idx}")
    if int(ends.min()) < window:
        raise HardFail("R5", f"window of {window} sessions cannot end at index {int(ends.min())}")
    if int(ends.max()) >= price.shape[0]:
        raise HardFail("R4", f"window ends at {int(ends.max())} on a panel of {price.shape[0]} rows")
    offsets = np.arange(-window, 1, dtype=np.int64)
    out = price[ends[:, None] + offsets[None, :], cols[:, None]]
    return np.ascontiguousarray(out, dtype=np.float64)


def _check_prices(prices: np.ndarray) -> None:
    """R5: the universe rule promised 61 finite, positive closes. Anything else is a broken caller."""
    if prices.size == 0:
        return
    if not np.isfinite(prices).all():
        raise HardFail("R5", f"{int((~np.isfinite(prices)).sum())} non-finite closes in a window"
                             " (no interpolation, forward fill or back fill exists in D)")
    if (prices <= 0).any():
        raise HardFail("R5", f"{int((prices <= 0).sum())} non-positive closes in a window")


def encode_a(prices: np.ndarray) -> Encoded:
    """``z_i = (P_i - mean) / std_ddof0`` over ``i = 0..W``; a constant window has no direction.

    The undefined test is ``ptp == 0``, not ``std == 0``. In exact arithmetic the two agree, but
    in float64 the population standard deviation of a genuinely constant window is not zero -
    61 copies of 10.07 give 5.3e-15 - and dividing by that would blow rounding noise up to unit
    scale. Testing the range instead implements D0's rule exactly rather than changing it (V3).
    """
    _check_prices(prices)
    defined = np.ptp(prices, axis=1) != 0.0
    mean = prices.mean(axis=1, keepdims=True)
    std = prices.std(axis=1, ddof=0, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        vectors = (prices - mean) / std
    vectors[~defined] = 0.0
    if defined.any() and not np.isfinite(vectors[defined]).all():
        raise HardFail("R5", "a defined z-normalized window is not finite")
    return Encoded(np.ascontiguousarray(vectors), defined)


def encode_b(prices: np.ndarray) -> Encoded:
    """``c_i = ln(P_i / P_0)`` for ``i = 1..W``; length W, amplitude kept, leading zero dropped.

    The ratio is taken before the logarithm, as the declaration writes it. ``log(P_i) - log(P_0)``
    is the same quantity in exact arithmetic and rounds differently, so the form is fixed here.
    A constant window is defined in B and encodes to the zero vector - that asymmetry with A is
    a consequence of the two declared formulas, not a choice made here.
    """
    _check_prices(prices)
    with np.errstate(divide="ignore", invalid="ignore"):
        vectors = np.log(prices[:, 1:] / prices[:, :1])
    defined = np.isfinite(vectors).all(axis=1)
    vectors[~defined] = 0.0
    return Encoded(np.ascontiguousarray(vectors), defined)


def encode(representation: str, prices: np.ndarray) -> Encoded:
    if representation == REPRESENTATION_A:
        return encode_a(prices)
    if representation == REPRESENTATION_B:
        return encode_b(prices)
    raise HardFail("R1", f"unknown representation {representation!r}")
