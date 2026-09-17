"""Strategy B V1 types: setups, candidate states, sessions, bars and feature snapshots.

Units, fixed for the whole package:

* ``return_*`` and every ``*_pct`` value are percent points: ``5.0`` means +5%.
* ``*_fraction`` values are plain fractions in ``[0, 1]``.
* Every datetime is timezone-aware.

Nothing here is shared with Strategy A. ``CandidateState`` is B's own FSM vocabulary and
is not ``StrategyPhase``; ``Session`` is B's own label and is not ``MarketSession``.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from math import isfinite

from app.strategy_b.errors import SyntheticBarMisuse


BAR_INTERVAL = timedelta(minutes=1)
AVAILABILITY_DELAY = BAR_INTERVAL
"""A one-minute bar opening at ``t`` is usable from ``t + 1 minute``.

This is the same contract the historical replay provider uses for Strategy A, restated
here so Strategy B does not import A's replay package. It is the only availability rule
in this package: every as-of cut goes through ``MomentumBar.available_at``.
"""


class SetupType(StrEnum):
    """The two V1 setups. A third setup needs its own engine work before it gets a name."""

    HOD_BREAKOUT = "HOD_BREAKOUT"
    FIRST_PULLBACK = "FIRST_PULLBACK"


class CandidateState(StrEnum):
    """Strategy B candidate FSM states (B-internal, not a backtest-core type)."""

    DETECTED = "DETECTED"
    QUALIFIED = "QUALIFIED"
    WATCHING = "WATCHING"
    SETUP_READY = "SETUP_READY"
    ENTRY_SIGNALLED = "ENTRY_SIGNALLED"
    ENTERED = "ENTERED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


TERMINAL_CANDIDATE_STATES = frozenset({
    CandidateState.ENTERED, CandidateState.REJECTED, CandidateState.EXPIRED,
    CandidateState.CANCELLED,
})


class Session(StrEnum):
    PREMARKET = "PREMARKET"
    REGULAR = "REGULAR"
    AFTER = "AFTER"
    OUTSIDE = "OUTSIDE"


class Availability(StrEnum):
    """Why a measured value exists or does not. ``AVAILABLE`` is the only one with a value."""

    AVAILABLE = "AVAILABLE"
    NO_DATA = "NO_DATA"
    """No actual bar is available in the scope at the as-of time."""
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    """The lookback reaches before the scope start, so the window is not fully observable."""
    NO_SOURCE_VWAP = "NO_SOURCE_VWAP"
    """A bar with volume carries no source VWAP. No synthetic VWAP fallback: not (H+L+C)/3,
    not the close."""
    ZERO_BASELINE = "ZERO_BASELINE"
    """The denominator is zero, so the ratio is undefined rather than infinite."""


@dataclass(frozen=True, slots=True)
class Measured:
    """A feature value together with its availability.

    ``value`` is set exactly when ``status`` is ``AVAILABLE``, so a ``None`` always has a
    stated reason next to it.
    """

    value: float | None
    status: Availability

    def __post_init__(self) -> None:
        if (self.value is None) == (self.status is Availability.AVAILABLE):
            raise ValueError("value must be set exactly when status is AVAILABLE")
        if self.value is not None and not isfinite(self.value):
            raise ValueError("a measured value must be finite")

    @classmethod
    def of(cls, value: float) -> "Measured":
        return cls(value, Availability.AVAILABLE)

    @classmethod
    def missing(cls, status: Availability) -> "Measured":
        return cls(None, status)


class RvolStatus(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class TapeDensity(StrEnum):
    """Intraday tape density from data available at the as-of time only.

    This is not the post-session ``SessionVerdict``: that verdict needs the official daily
    bar of the same day, which does not exist until the session is over.
    """

    DENSE = "DENSE"
    SPARSE = "SPARSE"
    VERY_SPARSE = "VERY_SPARSE"
    UNKNOWN = "UNKNOWN"


class SessionVerdict(StrEnum):
    """Post-session sparse validation verdict (HYBRID-S, not A's STRICT completeness)."""

    VERIFIED_SPARSE = "VERIFIED_SPARSE"
    API_LOSS_SUSPECT = "API_LOSS_SUSPECT"
    EMPTY_SESSION = "EMPTY_SESSION"
    CORPORATE_ACTION_SUSPECT = "CORPORATE_ACTION_SUSPECT"


class HaltStatus(StrEnum):
    """Research halt inference from minute evidence. Three distinct answers, never collapsed.

    ``UNKNOWN`` is neither ``HALT_INFERRED`` nor ``NO_HALT_SIGNAL``: a consumer that needs a
    yes/no must decide what to do with it explicitly. The status says nothing about
    liquidity; a thin symbol can be ``UNKNOWN`` here and low-liquidity in the dollar-volume
    features at the same time, and filtering on liquidity is a separate decision.
    """

    HALT_INFERRED = "HALT_INFERRED"
    """Historical minute evidence strongly resembles a halt and resume pattern."""
    NO_HALT_SIGNAL = "NO_HALT_SIGNAL"
    """The available evidence does not indicate a halt."""
    UNKNOWN = "UNKNOWN"
    """Sparse or no-trade data prevents a reliable determination."""


class CorporateActionFlag(StrEnum):
    SPLIT_ON_DAY = "SPLIT_ON_DAY"
    RECENT_SPLIT = "RECENT_SPLIT"
    IPO_WARMUP = "IPO_WARMUP"
    DELISTING_WINDOW = "DELISTING_WINDOW"
    CA_SUSPECT = "CA_SUSPECT"
    SYMBOL_CHANGE = "SYMBOL_CHANGE"


@dataclass(frozen=True, slots=True)
class MomentumBar:
    """One one-minute bar as Strategy B consumes it.

    ``timestamp`` is the bar-open time. ``vwap`` and ``transactions`` map to Massive ``vw``
    and ``n``; either may be absent at the source. ``volume`` is a float because Massive
    volumes can be fractional.

    ``synthetic=True`` marks a clock-fill minute made by ``sparse_session.minute_clock_view``:
    carried-forward price, zero volume, no trade. Synthetic bars are valid for clock-based
    returns, volume windows and RVOL. They must never reach candlestick patterns, ATR,
    breakout/pullback detection or fill decisions; ``require_actual_bars`` enforces that.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    session: Session
    vwap: float | None = None
    transactions: float | None = None
    synthetic: bool = False

    def __post_init__(self) -> None:
        _require_aware(self.timestamp, "timestamp")
        if self.timestamp.second or self.timestamp.microsecond:
            raise ValueError("timestamp must be an exact minute boundary")
        for name in ("open", "high", "low", "close"):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.high < max(self.open, self.low, self.close) or self.low > min(self.open, self.close):
            raise ValueError("high/low must bound open and close")
        if not isfinite(self.volume) or self.volume < 0:
            raise ValueError("volume must be finite and non-negative")
        if self.vwap is not None and (not isfinite(self.vwap) or self.vwap <= 0):
            raise ValueError("vwap must be finite and positive when present")
        if self.transactions is not None and (not isfinite(self.transactions) or self.transactions < 0):
            raise ValueError("transactions must be finite and non-negative when present")
        if self.synthetic and (
                self.volume != 0 or self.vwap is not None or self.transactions not in (None, 0)
                or not self.open == self.high == self.low == self.close):
            raise ValueError("a synthetic bar is a flat, zero-volume, no-trade carry-forward")

    @property
    def available_at(self) -> datetime:
        return self.timestamp + AVAILABILITY_DELAY


def require_actual_bars(bars: tuple[MomentumBar, ...] | list[MomentumBar]) -> None:
    """Refuse synthetic bars where only actual trades are meaningful (patterns, ATR, fills)."""
    for bar in bars:
        if bar.synthetic:
            raise SyntheticBarMisuse(
                f"synthetic clock bar at {bar.timestamp.isoformat()} reached an actual-bar "
                "consumer; patterns, ATR, breakout/pullback detection and fills need real trades")


@dataclass(frozen=True, slots=True)
class OfficialDailyBar:
    """The provider's official daily OHLCV for one session, known only after that session."""

    session_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if not isfinite(self.volume) or self.volume < 0:
            raise ValueError("volume must be finite and non-negative")
        for name in ("open", "high", "low", "close"):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True, slots=True)
class RvolResult:
    value: float | None
    status: RvolStatus
    sessions_used: int
    sessions_required: int
    split_adjusted: bool
    availability: Availability = Availability.AVAILABLE

    def __post_init__(self) -> None:
        if (self.value is None) != (self.status is RvolStatus.UNKNOWN):
            raise ValueError("an RVOL value exists exactly when status is FULL or PARTIAL")
        if (self.value is None) == (self.availability is Availability.AVAILABLE):
            raise ValueError("availability must explain exactly the missing RVOL values")

    def measured(self) -> Measured:
        return Measured(self.value, self.availability)


@dataclass(frozen=True, slots=True)
class HaltInference:
    status: HaltStatus
    last_inferred_gap_end: datetime | None
    open_gap_minutes: int | None
    """Minutes since the last actual bar opened, when that is long enough to be a live gap."""


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    """Everything B may know about one symbol at ``as_of``, and nothing after it."""

    symbol: str
    as_of: datetime
    session: Session

    price: Measured
    price_age_seconds: float | None

    return_1m: Measured
    return_3m: Measured
    return_5m: Measured

    session_vwap: Measured
    vwap_distance_pct: Measured

    hod: Measured
    lod: Measured
    hod_distance_pct: Measured

    rolling_dollar_volume: Measured
    cumulative_dollar_volume: Measured

    volume_acceleration: Measured

    rvol: Measured
    rvol_status: RvolStatus

    missing_minute_ratio: Measured
    sparse_status: TapeDensity

    halt_inferred: HaltStatus

    split_adjusted: bool
    corporate_action_flags: frozenset[CorporateActionFlag]

    def __post_init__(self) -> None:
        _require_aware(self.as_of, "as_of")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
