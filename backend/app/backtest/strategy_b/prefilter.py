"""The cheap half of Strategy B's scanner gate, vectorised over one symbol-session.

Why this exists: the pure layer answers one minute at a time and costs about 176 us per
answer. The full research grid is 3,883 symbols x 374 minutes x 104 sessions = 151M answers,
7.4 hours on one core, which makes repeating an experiment expensive enough that one starts
"just tweaking" the rules instead. This module spends about 99 us per symbol-session (not per
minute) to find the minutes that could possibly pass, and the pure layer then decides those.

The contract with the pure layer, in one line:

    a minute this module drops is a minute the gate could not have passed.

It reaches that by computing only the **cheap** gate conditions of B-F0 4.1 - the three
momentum legs and the rolling dollar volume - and none of the expensive ones (RVOL, halt
inference, tape density, corporate actions, scope). Fewer conditions can only let more
minutes through, so the output is a superset of the gate by construction, and a test pins the
cheap verdict itself to the pure functions minute by minute.

Nothing here is an authority. No value computed in this module reaches a result file; the
pure layer recomputes everything it decides on.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from app.strategy_b.config import DollarVolumePriceBasis, FeatureConfig, ScannerConfig
from app.strategy_b.models import AVAILABILITY_DELAY, MomentumBar, Session
from app.strategy_b.session import AggregationScope, SessionBoundaries

MOMENTUM_LEGS: tuple[tuple[int, str], ...] = (
    (1, "return_1m_threshold"), (3, "return_3m_threshold"), (5, "return_5m_threshold"))


@dataclass(frozen=True, slots=True)
class PrefilterResult:
    """Which minutes of one symbol-session the pure layer still has to look at."""

    symbol: str
    minutes: tuple[datetime, ...]
    ticks_considered: int

    @property
    def survivors(self) -> int:
        return len(self.minutes)


def cheap_gate_minutes(symbol: str, bars: Sequence[MomentumBar], ticks: Sequence[datetime], *,
                       boundaries: SessionBoundaries, scanner: ScannerConfig,
                       features: FeatureConfig) -> PrefilterResult:
    """As-of minutes whose momentum leg and dollar volume could pass the gate.

    ``ticks`` are availability moments (bar start + 1 minute), the same moments the engine
    would hand to the pure layer. ``bars`` are the session's actual bars, oldest first.

    The dollar-volume basis must be ``CLOSE``: that is the research default and the only basis
    whose value does not depend on source VWAP availability, which this module does not read.
    """
    if features.dollar_volume_basis is not DollarVolumePriceBasis.CLOSE:
        raise ValueError("the prefilter only implements the CLOSE dollar-volume basis")
    if features.return_scope is not AggregationScope.EXTENDED_DAY:
        raise ValueError("the prefilter assumes the declared EXTENDED_DAY return scope")
    if features.dollar_volume_scope is not AggregationScope.SESSION_LOCAL:
        raise ValueError("the prefilter assumes the declared SESSION_LOCAL dollar-volume scope")
    if any(boundaries.classify(tick) is not Session.REGULAR for tick in ticks):
        raise ValueError("every tick must be a regular-session moment (B-F0 4.1)")
    if not bars or not ticks:
        return PrefilterResult(symbol, (), len(ticks))

    origin = boundaries.premarket_start
    minute = timedelta(minutes=1)
    available = np.fromiter(((bar.timestamp + AVAILABILITY_DELAY - origin) / minute for bar in bars),
                            dtype=np.float64, count=len(bars))
    opens = np.fromiter(((bar.timestamp - origin) / minute for bar in bars),
                        dtype=np.float64, count=len(bars))
    close = np.fromiter((bar.close for bar in bars), dtype=np.float64, count=len(bars))
    volume = np.fromiter((bar.volume for bar in bars), dtype=np.float64, count=len(bars))
    notional = np.concatenate([[0.0], np.cumsum(close * volume)])
    moments = np.fromiter(((tick - origin) / minute for tick in ticks),
                          dtype=np.float64, count=len(ticks))

    # Returns read the extended day, so their scope starts at the tape's own origin and the
    # only way to miss is an empty prefix.
    now = np.searchsorted(available, moments, side="right")
    passing = np.zeros(moments.shape, dtype=bool)
    for minutes, threshold_name in MOMENTUM_LEGS:
        reference = np.searchsorted(available, moments - minutes, side="right")
        usable = (now >= 1) & (reference >= 1)
        if not usable.any():
            continue
        change = np.zeros(moments.shape)
        change[usable] = (close[now[usable] - 1] / close[reference[usable] - 1] - 1) * 100
        passing |= usable & (change >= getattr(scanner, threshold_name))

    # Dollar volume is session-local: the window may not reach back past the opening bell, and
    # premarket bars never count, however recent they are.
    regular_open = (boundaries.regular_open - origin) / minute
    first_regular = int(np.searchsorted(opens, regular_open, side="left"))
    window_open = moments - features.rolling_dollar_volume_window_minutes
    low = np.maximum(np.searchsorted(available, window_open, side="right"), first_regular)
    dollar_volume = notional[np.maximum(now, low)] - notional[low]
    passing &= (window_open >= regular_open) & (dollar_volume >= scanner.min_dollar_volume)
    return PrefilterResult(symbol, tuple(tick for tick, keep in zip(ticks, passing) if keep),
                           len(ticks))
