"""The event lifecycle and the three confirmation detectors.

The state machine is the point of this module. An event puts the strategy into
`WAIT_CONFIRMATION` and nothing else happens until the price either shows a direction or the
window runs out, at which point the answer is `EXPIRED` and no trade is taken. Forcing a trade on
every event is exactly what D5.4 did.

Every detector reads closes up to the bar it is judging and never past it, so a confirmation at
bar i is a statement about bars <= i alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from ..d5_4.events import Event

LONG = "LONG"
SHORT = "SHORT"

IDLE = "IDLE"
EVENT_ACTIVE = "EVENT_ACTIVE"
WAIT_CONFIRMATION = "WAIT_CONFIRMATION"
CONFIRMED_LONG = "CONFIRMED_LONG"
CONFIRMED_SHORT = "CONFIRMED_SHORT"
EXPIRED = "EXPIRED"

SHOCK_LOOKBACK = 14          # disp15 spans 15 bars, so the window opens 14 bars before the start
COMPRESSION_BARS = 15


@dataclass(frozen=True)
class ShockWindow:
    """The bars that produced the displacement, plus the event run itself."""

    start: int
    end: int
    high: float
    low: float
    pre_price: float          # P0, the close before the displacement began
    range_fraction: float     # R

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2


@dataclass(frozen=True)
class Confirmation:
    """A direction the price showed, or the absence of one."""

    event_start: int
    event_end: int
    state: str                       # CONFIRMED_LONG, CONFIRMED_SHORT or EXPIRED
    bar: int | None = None           # bar whose close confirmed
    side: str | None = None
    level: float | None = None       # the level that was broken or reclaimed
    stop_price: float | None = None  # structural invalidation
    target_price: float | None = None
    distance: float | None = None    # how far the confirming close sat past the level
    shock: ShockWindow | None = None
    detail: dict[str, Any] | None = None

    @property
    def confirmed(self) -> bool:
        return self.state in (CONFIRMED_LONG, CONFIRMED_SHORT)

    def as_dict(self) -> dict[str, Any]:
        return {"event_start": self.event_start, "event_end": self.event_end,
                "state": self.state, "bar": self.bar, "side": self.side, "level": self.level,
                "stop_price": self.stop_price, "target_price": self.target_price,
                "distance": self.distance,
                "wait_bars": None if self.bar is None else self.bar - self.event_end,
                "shock_range": None if self.shock is None else self.shock.range_fraction,
                **(self.detail or {})}


def shock_window(grid: dict[str, np.ndarray], event: Event) -> ShockWindow:
    lo = max(0, event.start - SHOCK_LOOKBACK)
    hi = event.end
    high = float(grid["mark_high"][lo:hi + 1].max())
    low = float(grid["mark_low"][lo:hi + 1].min())
    mid = (high + low) / 2
    return ShockWindow(start=lo, end=hi, high=high, low=low,
                       pre_price=float(grid["close"][lo]),
                       range_fraction=(high - low) / mid if mid else float("nan"))


def _expired(event: Event, shock: ShockWindow) -> Confirmation:
    return Confirmation(event_start=event.start, event_end=event.end, state=EXPIRED, shock=shock)


def confirm_breakout(grid: dict[str, np.ndarray], event: Event, *, window_bars: int,
                     margin_fraction: float, min_margin: float) -> Confirmation:
    """Candidate A: leaving the shock range by a meaningful margin names the direction."""
    shock = shock_window(grid, event)
    if not np.isfinite(shock.range_fraction):
        return _expired(event, shock)
    margin = max(margin_fraction * shock.range_fraction, min_margin)
    up_level = shock.high * (1 + margin)
    down_level = shock.low * (1 - margin)
    n = len(grid["ts"])
    for bar in range(event.end + 1, min(event.end + 1 + window_bars, n)):
        close = float(grid["close"][bar])
        if close > up_level:
            return Confirmation(event.start, event.end, CONFIRMED_LONG, bar, LONG,
                                level=shock.high, stop_price=shock.high, target_price=None,
                                distance=(close - shock.high) / shock.high, shock=shock,
                                detail={"margin": margin})
        if close < down_level:
            return Confirmation(event.start, event.end, CONFIRMED_SHORT, bar, SHORT,
                                level=shock.low, stop_price=shock.low, target_price=None,
                                distance=(shock.low - close) / shock.low, shock=shock,
                                detail={"margin": margin})
    return _expired(event, shock)


def confirm_retracement(grid: dict[str, np.ndarray], event: Event, *, window_bars: int,
                        retrace_fraction: float, min_margin: float,
                        displacement_sign: float) -> Confirmation:
    """Candidate B: half the shock given back says normalisation is actually happening.

    The level is measured from the pre-shock price, not from a running extreme. A running extreme
    keeps updating, so "bounced k off the low" eventually becomes true for almost every event and
    stops being a filter at all; the pre-freeze QC measured that at 99%.
    """
    shock = shock_window(grid, event)
    if not np.isfinite(shock.range_fraction):
        return _expired(event, shock)
    n = len(grid["ts"])
    down = displacement_sign < 0

    if down:
        level = shock.low + retrace_fraction * (shock.pre_price - shock.low)
        level = max(level, shock.low * (1 + min_margin))
        for bar in range(event.end + 1, min(event.end + 1 + window_bars, n)):
            close = float(grid["close"][bar])
            if close > level:
                return Confirmation(event.start, event.end, CONFIRMED_LONG, bar, LONG,
                                    level=level, stop_price=shock.low,
                                    target_price=shock.pre_price,
                                    distance=(level - shock.low) / shock.low, shock=shock,
                                    detail={"retrace_fraction": retrace_fraction})
    else:
        level = shock.high - retrace_fraction * (shock.high - shock.pre_price)
        level = min(level, shock.high * (1 - min_margin))
        for bar in range(event.end + 1, min(event.end + 1 + window_bars, n)):
            close = float(grid["close"][bar])
            if close < level:
                return Confirmation(event.start, event.end, CONFIRMED_SHORT, bar, SHORT,
                                    level=level, stop_price=shock.high,
                                    target_price=shock.pre_price,
                                    distance=(shock.high - level) / shock.high, shock=shock,
                                    detail={"retrace_fraction": retrace_fraction})
    return _expired(event, shock)


def confirm_compression_expansion(grid: dict[str, np.ndarray], event: Event, *,
                                  window_bars: int, compression_fraction: float,
                                  min_margin: float, rv15: np.ndarray) -> Confirmation:
    """Candidate C: shock, digestion, resolution. All three stages inside the window."""
    shock = shock_window(grid, event)
    reference = float(rv15[event.start])
    if not np.isfinite(reference) or reference <= 0:
        return _expired(event, shock)
    n = len(grid["ts"])
    stop = min(event.end + 1 + window_bars, n)

    for bar in range(event.end + 1, stop):
        if float(rv15[bar]) >= compression_fraction * reference:
            continue
        lo = max(0, bar - COMPRESSION_BARS + 1)
        ceiling = float(grid["mark_high"][lo:bar + 1].max())
        floor = float(grid["mark_low"][lo:bar + 1].min())
        up_level = ceiling * (1 + min_margin)
        down_level = floor * (1 - min_margin)
        for later in range(bar + 1, stop):
            close = float(grid["close"][later])
            if close > up_level:
                return Confirmation(event.start, event.end, CONFIRMED_LONG, later, LONG,
                                    level=ceiling, stop_price=floor, target_price=None,
                                    distance=(close - ceiling) / ceiling, shock=shock,
                                    detail={"compression_bar": bar, "compression_high": ceiling,
                                            "compression_low": floor})
            if close < down_level:
                return Confirmation(event.start, event.end, CONFIRMED_SHORT, later, SHORT,
                                    level=floor, stop_price=ceiling, target_price=None,
                                    distance=(floor - close) / floor, shock=shock,
                                    detail={"compression_bar": bar, "compression_high": ceiling,
                                            "compression_low": floor})
        return _expired(event, shock)
    return _expired(event, shock)


def detector_for(candidate: dict, *, grid: dict[str, np.ndarray], features: dict[str, np.ndarray],
                 window_bars: int, min_margin: float,
                 overrides: dict[str, float] | None = None) -> Callable[[Event], Confirmation]:
    """Bind one contract candidate to its detector, with the arm's overrides applied."""
    overrides = overrides or {}
    if candidate["id"] == "A":
        margin = overrides.get("margin_fraction", 0.25)
        return lambda event: confirm_breakout(grid, event, window_bars=window_bars,
                                              margin_fraction=margin, min_margin=min_margin)
    if candidate["id"] == "B":
        fraction = overrides.get("retrace_fraction", 0.5)
        return lambda event: confirm_retracement(
            grid, event, window_bars=window_bars, retrace_fraction=fraction,
            min_margin=min_margin,
            displacement_sign=float(features["disp15"][event.start]))
    if candidate["id"] == "C":
        compression = overrides.get("compression_fraction", 0.5)
        return lambda event: confirm_compression_expansion(
            grid, event, window_bars=window_bars, compression_fraction=compression,
            min_margin=min_margin, rv15=features["rv15"])
    raise ValueError(f"unknown candidate: {candidate['id']}")
