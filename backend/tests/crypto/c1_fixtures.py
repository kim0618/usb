"""A synthetic Bybit/Binance window a test can bend into any C1 state.

C1 needs a long run-up before it can say anything: 30 whole UTC days for the bucket window and
1,440 bars for the volatility window. Rather than mock the conditions away, these helpers build
the real quantity of real-shaped input and let a test set the three conditions by moving the
inputs, so what is under test is the engine's reading of the contract rather than a stub.

Shapes:
  * the perpetual walks with alternating +/- returns, so the 24 h standard deviation of its 1m log
    returns is the step size and a test can put the regime where it wants it;
  * the spot close is the perpetual divided by exp(basis), so `basis` *is* S1 at the 5m boundary;
  * open interest is a 5m series a test can tilt up or down.
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

from app.crypto.c1.contract import DAY_BARS, MINUTE_MS, NORM_DAYS, VOL_WINDOW_BARS
from app.crypto.c1.grid import Bar, build_grid

DAY_MS = 86_400_000
# A day-aligned instant well inside the venues' history, so nothing here depends on "now".
ORIGIN_MS = 1_735_689_600_000          # 2025-01-01T00:00:00Z

# Above the frozen HIGH cutoff (0.00109249...) and below it.
HIGH_STEP = 0.0015
LOW_STEP = 0.0004


def window_bars(days: int = NORM_DAYS + 2) -> int:
    """Enough bars that the last one is decidable: 30 whole days plus the volatility window."""
    return days * DAY_BARS + VOL_WINDOW_BARS


def build(*, bars: int | None = None, start_ms: int = ORIGIN_MS,
          step: float | Callable[[int], float] = HIGH_STEP,
          basis: Callable[[int], float] | None = None,
          oi: Callable[[int], float] | None = None,
          drop_minutes: Sequence[int] = (),
          drop_spot_minutes: Sequence[int] = (),
          oi_until: int | None = None):
    """Build (grid, funding) for `bars` minutes from `start_ms`.

    `step` is the per-minute absolute log return, so the 24 h standard deviation equals it.
    `basis` returns S1 for minute i; the default spreads values so the 10th percentile is a real
    cutoff and not a degenerate one. `drop_minutes` removes perpetual bars, `drop_spot_minutes`
    removes spot bars, and `oi_until` stops the open-interest series early.
    """
    count = window_bars() if bars is None else bars
    step_of = step if callable(step) else (lambda _i: step)
    # A deterministic sawtooth: ten distinct values, so the bucket cutoffs are well separated and
    # a test can land strictly below the 10th percentile on purpose.
    basis_of = basis or (lambda i: -1e-4 * (i % 10))
    oi_of = oi or (lambda i: 1_000_000.0 + (i % 240) * 10.0)

    price = 90_000.0
    perp: list[Bar] = []
    spot: list[tuple[int, float]] = []
    dropped = set(drop_minutes)
    dropped_spot = set(drop_spot_minutes)
    for index in range(count):
        ts = start_ms + index * MINUTE_MS
        if index:
            price *= math.exp(step_of(index) * (1 if index % 2 else -1))
        if index not in dropped:
            perp.append(Bar(ts_ms=ts, open=price, high=price * 1.0004, low=price * 0.9996,
                            close=price, volume=1.0))
        if index not in dropped_spot:
            spot.append((ts, price / math.exp(basis_of(index))))

    oi_rows: list[tuple[int, float]] = []
    limit = count if oi_until is None else oi_until
    # 5m stamps on the boundary grid, reaching back two hours so the 1 h change is defined at the
    # first decidable bar.
    for index in range(-24, limit, 5):
        oi_rows.append((start_ms + index * MINUTE_MS, oi_of(index)))

    grid = build_grid(perp, spot, oi_rows, start_ms=start_ms,
                      end_ms=start_ms + (count - 1) * MINUTE_MS)
    return grid, []


def falling_oi(from_index: int, span: int = 120) -> Callable[[int], float]:
    """Open interest that decreases over the hour ending at `from_index`."""
    def oi(index: int) -> float:
        if index < from_index - span:
            return 1_000_000.0
        return 1_000_000.0 - (index - (from_index - span)) * 50.0
    return oi


def basis_dipping(at: Sequence[int], depth: float = -0.01) -> Callable[[int], float]:
    """The default sawtooth, pushed far below every cutoff at the given minutes."""
    marked = set(at)

    def basis(index: int) -> float:
        return depth if index in marked else -1e-4 * (index % 10)
    return basis
