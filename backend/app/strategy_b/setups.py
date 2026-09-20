"""Strategy B setup detection. V1 detects ``HOD_BREAKOUT`` only.

Declared rules: ``docs/backtest/strategy_b/B_F0_FSM_RULES_V1.md`` section 7.

The pattern in one line: the symbol printed the high of the day, then held a short, shallow
consolidation under it, and a break of that high by ``breakout_buffer_pct`` is the entry
trigger. The consolidation is the risk unit: its low is the initial stop.

``FIRST_PULLBACK`` stays defined in the config and undetected here. One setup at a time keeps
the study answerable: a mixed result over two setups says nothing about either.

Only actual bars take part. A silent minute is not a consolidation bar, so a symbol that
stops trading for ten minutes does not thereby "hold" its high; the window counts observed
bars (``*_bars`` in the config), not wall-clock minutes.

Every read is cut at ``as_of`` through ``SessionTape``, and the high of the day is the running
extreme up to that cut, never the day's final high.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.strategy_b.config import HodBreakoutConfig
from app.strategy_b.features import SessionTape
from app.strategy_b.models import SetupType
from app.strategy_b.session import AggregationScope


class SetupRejection(StrEnum):
    """Why no setup exists at this as-of time. Not an error: most minutes have no setup."""

    NO_BARS = "NO_BARS"
    WINDOW_TOO_SHORT = "WINDOW_TOO_SHORT"
    """The high of the day is too recent: fewer than ``consolidation_min_bars`` bars since."""
    WINDOW_TOO_LONG = "WINDOW_TOO_LONG"
    """The high has been sitting there for more than ``consolidation_max_bars`` bars."""
    PULLBACK_TOO_DEEP = "PULLBACK_TOO_DEEP"
    """The window dropped further than ``max_pullback_pct`` below the high: no longer a hold."""
    STOP_NOT_BELOW_TRIGGER = "STOP_NOT_BELOW_TRIGGER"
    """Degenerate geometry (zero or negative risk per share). Never tradeable."""


@dataclass(frozen=True, slots=True)
class HodBreakoutSetup:
    """An armed HOD breakout: what to buy at, where the stop is, and what the pattern was."""

    symbol: str
    setup: SetupType
    as_of: datetime
    hod: float
    hod_bar_timestamp: datetime
    consolidation_bars: int
    consolidation_low: float
    trigger_price: float
    initial_stop: float

    def __post_init__(self) -> None:
        if self.setup is not SetupType.HOD_BREAKOUT:
            raise ValueError("HodBreakoutSetup carries SetupType.HOD_BREAKOUT")
        if not self.initial_stop < self.trigger_price:
            raise ValueError("the initial stop must sit below the trigger price")

    def risk_per_share(self, entry_price: float) -> float:
        """1R for a fill at ``entry_price``. The caller sizes positions from this."""
        return entry_price - self.initial_stop


@dataclass(frozen=True, slots=True)
class SetupDecision:
    setup: HodBreakoutSetup | None
    rejection: SetupRejection | None

    def __post_init__(self) -> None:
        if (self.setup is None) == (self.rejection is None):
            raise ValueError("a decision is either a setup or a rejection")


def detect_hod_breakout(tape: SessionTape, as_of: datetime, *, scope: AggregationScope,
                        config: HodBreakoutConfig) -> SetupDecision:
    """Look for a HOD breakout setup in ``tape`` as of ``as_of``.

    ``scope`` is the same aggregation scope the HOD feature uses, so the setup and the
    ``FeatureSnapshot`` can never disagree about what the high of the day is.
    """
    window = tape.scope_range(as_of, scope)
    if window is None:
        return SetupDecision(None, SetupRejection.NO_BARS)
    _, lo, hi = window
    if hi <= lo:
        return SetupDecision(None, SetupRejection.NO_BARS)

    hod = tape.high(lo, hi)
    peak = _first_index_reaching(tape, lo, hi, hod)
    consolidation_bars = hi - (peak + 1)
    if consolidation_bars < config.consolidation_min_bars:
        return SetupDecision(None, SetupRejection.WINDOW_TOO_SHORT)
    if consolidation_bars > config.consolidation_max_bars:
        return SetupDecision(None, SetupRejection.WINDOW_TOO_LONG)

    consolidation_low = tape.low(peak + 1, hi)
    if consolidation_low < hod * (1 - config.max_pullback_pct / 100):
        return SetupDecision(None, SetupRejection.PULLBACK_TOO_DEEP)

    trigger_price = hod * (1 + config.breakout_buffer_pct / 100)
    if consolidation_low >= trigger_price:
        return SetupDecision(None, SetupRejection.STOP_NOT_BELOW_TRIGGER)

    return SetupDecision(
        HodBreakoutSetup(
            symbol=tape.symbol,
            setup=SetupType.HOD_BREAKOUT,
            as_of=as_of,
            hod=hod,
            hod_bar_timestamp=tape.bars[peak].timestamp,
            consolidation_bars=consolidation_bars,
            consolidation_low=consolidation_low,
            trigger_price=trigger_price,
            initial_stop=consolidation_low,
        ),
        None,
    )


def _first_index_reaching(tape: SessionTape, lo: int, hi: int, hod: float) -> int:
    """Earliest index in ``[lo, hi)`` whose running high equals ``hod``.

    The prefix maximum is non-decreasing, so this is a binary search: no scan of the tape.
    A repeat touch of the same price is not a new high, which is why the *earliest* index
    defines the consolidation window.
    """
    left, right = lo, hi - 1
    while left < right:
        middle = (left + right) // 2
        if tape.high(lo, middle + 1) >= hod:
            right = middle
        else:
            left = middle + 1
    return left
