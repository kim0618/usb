"""Strategy B ``REALTIME_MOMENTUM_V1`` config schema.

The structure is the deliverable here, not the numbers. Every default is a research
placeholder, and ``defaults_status`` says so inside the serialized config, so a run
identity built from these defaults cannot be mistaken for a tuned strategy.

Units follow ``models``: ``*_pct`` and return thresholds are percent points, ``*_fraction``
is a fraction, ``*_r`` is in R multiples.

Time units are in the field name, and the two never mix:

* ``*_minutes`` is **wall-clock** elapsed time (TTLs, the time stop, windows, halt gaps), so a
  sparse tape cannot stretch a TTL.
* ``*_bars`` counts **actual observed, non-synthetic** market bars (consolidation and pullback
  duration), because a pattern made of synthetic carry-forward minutes is not a pattern.

On a sparse tape with trades at 09:31, 09:34 and 09:39 that is 3 bars but 8 elapsed minutes.

Serialization is strict both ways: ``from_dict`` refuses unknown or missing keys and wrong
types, and ``canonical_json`` / ``fingerprint`` are deterministic so the config can enter
a run identity later.
"""

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import time
from enum import StrEnum
import hashlib
import json
from math import isfinite
import types
import typing
from typing import Any, Self

from app.strategy_b.errors import InvalidConfig
from app.strategy_b.session import AggregationScope


STRATEGY_ID = "REALTIME_MOMENTUM_V1"
SCHEMA_VERSION = 1
RESEARCH_DEFAULTS = "RESEARCH_DEFAULT_UNOPTIMIZED"


class TrailingModel(StrEnum):
    PREVIOUS_ACTUAL_BAR_LOW = "PREVIOUS_ACTUAL_BAR_LOW"
    BREAKEVEN_AFTER_PARTIAL = "BREAKEVEN_AFTER_PARTIAL"


class SymbolListProvenance(StrEnum):
    """Where a hand-maintained symbol list came from.

    ``RESEARCH_DEFAULT_UNVERIFIED`` means verified = false, source = research default: typed in
    by hand, not checked against an exchange notice or a provider flag. A verified source gets
    its own member together with the code that loads it.
    """

    RESEARCH_DEFAULT_UNVERIFIED = "RESEARCH_DEFAULT_UNVERIFIED"


class DollarVolumePriceBasis(StrEnum):
    """The one place the price in intraday dollar volume (USD) is chosen.

    ``CLOSE``: per bar ``close × volume``. The research default, because a close exists in both
    historical Massive bars and any realtime minute bar, so the scanner number never depends
    on whether a VWAP source exists. ``SOURCE_VWAP``: per bar ``source vwap × volume``, closer
    to traded value but ``NO_SOURCE_VWAP`` whenever a bar with volume has no source VWAP.
    VWAP availability does not change the ``CLOSE`` basis.
    """

    CLOSE = "CLOSE"
    SOURCE_VWAP = "SOURCE_VWAP"


@dataclass(frozen=True, slots=True)
class ScannerConfig:
    return_1m_threshold: float = 2.0
    return_3m_threshold: float = 4.0
    return_5m_threshold: float = 6.0
    min_dollar_volume: float = 250_000.0
    min_rvol: float = 3.0
    max_spread_pct: float = 1.0
    """Declared but not applied in V1: Basic minute bars carry no quotes and B estimates no
    spread (`B_F0_FSM_RULES_V1.md` 4.2)."""
    scan_window_start_et: str = "09:35"
    """HH:MM ET. Before this the session is too young for RVOL and a consolidation window."""
    scan_window_end_et: str = "15:30"
    """HH:MM ET. After this a new entry would collide with the time stop and the EOD exit."""

    def __post_init__(self) -> None:
        _positive(self, "min_dollar_volume", "min_rvol", "max_spread_pct")
        _finite(self, "return_1m_threshold", "return_3m_threshold", "return_5m_threshold")
        _clock(self, "scan_window_start_et", "scan_window_end_et")
        if parse_et_clock(self.scan_window_start_et) >= parse_et_clock(self.scan_window_end_et):
            raise InvalidConfig("scanner.scan_window_start_et must be before scan_window_end_et")


@dataclass(frozen=True, slots=True)
class CandidateConfig:
    score_threshold: float = 0.6
    candidate_ttl_minutes: int = 30
    """Wall-clock minutes."""
    setup_ttl_minutes: int = 10
    """Wall-clock minutes."""
    signal_ttl_minutes: int = 2
    """Wall-clock minutes."""
    price_drift_tolerance_pct: float = 1.0
    score_weight_momentum: float = 0.5
    score_weight_rvol: float = 0.3
    score_weight_liquidity: float = 0.2
    score_ratio_cap: float = 2.0
    """Each score component saturates at this multiple of its gate threshold, so one huge
    mover cannot own the ranking."""

    def __post_init__(self) -> None:
        _finite(self, "score_threshold")
        _positive(self, "candidate_ttl_minutes", "setup_ttl_minutes", "signal_ttl_minutes",
                  "price_drift_tolerance_pct", "score_ratio_cap")
        _fraction(self, "score_weight_momentum", "score_weight_rvol", "score_weight_liquidity")
        total = self.score_weight_momentum + self.score_weight_rvol + self.score_weight_liquidity
        if abs(total - 1.0) > 1e-9:
            raise InvalidConfig(f"candidate score weights must sum to 1.0, not {total}")


@dataclass(frozen=True, slots=True)
class HodBreakoutConfig:
    consolidation_min_bars: int = 3
    """Actual observed bars."""
    consolidation_max_bars: int = 15
    """Actual observed bars."""
    breakout_buffer_pct: float = 0.1
    max_pullback_pct: float = 3.0

    def __post_init__(self) -> None:
        _positive(self, "consolidation_min_bars", "consolidation_max_bars", "max_pullback_pct")
        _non_negative(self, "breakout_buffer_pct")
        _ordered(self, "consolidation_min_bars", "consolidation_max_bars")


@dataclass(frozen=True, slots=True)
class FirstPullbackConfig:
    min_pullback_pct: float = 2.0
    max_pullback_pct: float = 8.0
    min_duration_bars: int = 2
    """Actual observed bars."""
    max_duration_bars: int = 10
    """Actual observed bars."""

    def __post_init__(self) -> None:
        _positive(self, "min_pullback_pct", "max_pullback_pct", "min_duration_bars",
                  "max_duration_bars")
        _ordered(self, "min_pullback_pct", "max_pullback_pct")
        _ordered(self, "min_duration_bars", "max_duration_bars")


@dataclass(frozen=True, slots=True)
class RiskConfig:
    risk_per_trade_pct: float = 0.5
    max_position_pct: float = 20.0
    max_open_positions: int = 3
    max_entries_per_symbol: int = 1
    daily_loss_limit_r: float = 3.0

    def __post_init__(self) -> None:
        _positive(self, "risk_per_trade_pct", "max_position_pct", "max_open_positions",
                  "max_entries_per_symbol", "daily_loss_limit_r")
        if self.max_position_pct > 100:
            raise InvalidConfig("risk.max_position_pct cannot exceed 100 percent")


@dataclass(frozen=True, slots=True)
class ExitConfig:
    partial_take_profit_r: float = 2.0
    partial_exit_fraction: float = 0.5
    trailing_model: TrailingModel = TrailingModel.PREVIOUS_ACTUAL_BAR_LOW
    time_stop_minutes: int = 30
    """Wall-clock minutes."""
    eod_exit_et: str = "15:55"
    """HH:MM ET. B holds nothing overnight."""

    def __post_init__(self) -> None:
        _positive(self, "partial_take_profit_r", "partial_exit_fraction", "time_stop_minutes")
        _clock(self, "eod_exit_et")
        if self.partial_exit_fraction > 1:
            raise InvalidConfig("exit.partial_exit_fraction must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class FeatureConfig:
    return_scope: AggregationScope = AggregationScope.EXTENDED_DAY
    vwap_scope: AggregationScope = AggregationScope.SESSION_LOCAL
    hod_scope: AggregationScope = AggregationScope.SESSION_LOCAL
    dollar_volume_scope: AggregationScope = AggregationScope.SESSION_LOCAL
    dollar_volume_basis: DollarVolumePriceBasis = DollarVolumePriceBasis.CLOSE
    rolling_dollar_volume_window_minutes: int = 5
    volume_acceleration_window_minutes: int = 5
    density_scope: AggregationScope = AggregationScope.SESSION_LOCAL
    dense_max_missing_ratio: float = 0.2
    sparse_max_missing_ratio: float = 0.6

    def __post_init__(self) -> None:
        _positive(self, "rolling_dollar_volume_window_minutes", "volume_acceleration_window_minutes")
        _fraction(self, "dense_max_missing_ratio", "sparse_max_missing_ratio")
        _ordered(self, "dense_max_missing_ratio", "sparse_max_missing_ratio")


@dataclass(frozen=True, slots=True)
class RvolConfig:
    lookback_sessions: int = 20
    min_partial_sessions: int = 5
    scope: AggregationScope = AggregationScope.SESSION_LOCAL

    def __post_init__(self) -> None:
        _positive(self, "lookback_sessions", "min_partial_sessions")
        _ordered(self, "min_partial_sessions", "lookback_sessions")


@dataclass(frozen=True, slots=True)
class HaltInferenceConfig:
    """Research flag thresholds, not an exchange halt rule."""

    min_gap_minutes: int = 5
    max_gap_minutes: int = 10
    min_abs_post_gap_return_pct: float = 5.0
    min_post_gap_volume_ratio: float = 2.0
    """Post-gap bar volume over the mean actual-bar volume earlier in the same session."""

    def __post_init__(self) -> None:
        _positive(self, "min_gap_minutes", "max_gap_minutes", "min_abs_post_gap_return_pct",
                  "min_post_gap_volume_ratio")
        _ordered(self, "min_gap_minutes", "max_gap_minutes")
        if self.min_gap_minutes < 2:
            raise InvalidConfig("halt.min_gap_minutes must be at least 2; consecutive bars have gap 1")


@dataclass(frozen=True, slots=True)
class SparseValidationConfig:
    price_tolerance: float = 1e-6
    """Relative tolerance for minute-vs-daily open/high/low equality."""
    volume_tolerance: float = 0.01
    """Minute volume may exceed daily volume by this fraction before it is suspect."""
    min_volume_coverage: float | None = None
    """Optional floor for minute/daily volume. Disabled by default: observed 0.74 to 1.0."""
    split_ratio_tolerance: float = 0.02

    def __post_init__(self) -> None:
        _non_negative(self, "price_tolerance", "volume_tolerance", "split_ratio_tolerance")
        if self.min_volume_coverage is not None:
            _fraction(self, "min_volume_coverage")


@dataclass(frozen=True, slots=True)
class ScopeConfig:
    allowed_security_types: tuple[str, ...] = ("CS",)
    allowed_exchanges: tuple[str, ...] = ("XNAS", "XNYS", "XASE")
    test_symbols: tuple[str, ...] = ("ZVZZT", "ZWZZT", "ZXZZT", "ZVV", "NTEST", "ATEST")
    """Backup safeguard behind ``TickerMetadataAsOf.test_issue``, not an official list."""
    test_symbols_provenance: SymbolListProvenance = SymbolListProvenance.RESEARCH_DEFAULT_UNVERIFIED
    """``test_symbols`` has not been checked against exchange test-issue notices."""
    price_floor: float = 1.0
    median_dollar_volume_floor: float = 1_000_000.0
    median_lookback_sessions: int = 20
    min_history_sessions: int = 5

    def __post_init__(self) -> None:
        _positive(self, "price_floor", "median_dollar_volume_floor", "median_lookback_sessions",
                  "min_history_sessions")
        _ordered(self, "min_history_sessions", "median_lookback_sessions")
        for name in ("allowed_security_types", "allowed_exchanges", "test_symbols"):
            values = getattr(self, name)
            if len(set(values)) != len(values) or any(not v or v != v.strip().upper() for v in values):
                raise InvalidConfig(f"scope.{name} must be unique upper-case codes")


@dataclass(frozen=True, slots=True)
class CorporateActionConfig:
    recent_split_calendar_days: int = 5
    ipo_warmup_sessions: int = 20
    delisting_window_calendar_days: int = 10
    symbol_change_calendar_days: int = 5

    def __post_init__(self) -> None:
        _positive(self, "recent_split_calendar_days", "ipo_warmup_sessions",
                  "delisting_window_calendar_days", "symbol_change_calendar_days")


@dataclass(frozen=True, slots=True)
class StrategyBConfig:
    strategy_id: str = STRATEGY_ID
    schema_version: int = SCHEMA_VERSION
    defaults_status: str = RESEARCH_DEFAULTS
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    candidate: CandidateConfig = field(default_factory=CandidateConfig)
    hod_breakout: HodBreakoutConfig = field(default_factory=HodBreakoutConfig)
    first_pullback: FirstPullbackConfig = field(default_factory=FirstPullbackConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    exit: ExitConfig = field(default_factory=ExitConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    rvol: RvolConfig = field(default_factory=RvolConfig)
    halt: HaltInferenceConfig = field(default_factory=HaltInferenceConfig)
    sparse_validation: SparseValidationConfig = field(default_factory=SparseValidationConfig)
    scope: ScopeConfig = field(default_factory=ScopeConfig)
    corporate_actions: CorporateActionConfig = field(default_factory=CorporateActionConfig)

    def __post_init__(self) -> None:
        if self.strategy_id != STRATEGY_ID:
            raise InvalidConfig(f"strategy_id must be {STRATEGY_ID}")
        if self.schema_version != SCHEMA_VERSION:
            raise InvalidConfig(f"schema_version {self.schema_version} is not {SCHEMA_VERSION}")
        if not self.defaults_status.strip():
            raise InvalidConfig("defaults_status must name where the numbers came from")

    def to_dict(self) -> dict[str, Any]:
        return _to_plain(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Self:
        return _from_plain(cls, payload, "config")

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _to_plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _to_plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    return value


def _from_plain(cls: type, payload: Any, path: str) -> Any:
    if not isinstance(payload, dict):
        raise InvalidConfig(f"{path} must be an object")
    hints = typing.get_type_hints(cls)
    names = {f.name for f in fields(cls)}
    unknown = sorted(set(payload) - names)
    missing = sorted(names - set(payload))
    if unknown or missing:
        raise InvalidConfig(f"{path} keys mismatch: unknown={unknown} missing={missing}")
    values = {name: _coerce(hints[name], payload[name], f"{path}.{name}") for name in names}
    try:
        return cls(**values)
    except InvalidConfig:
        raise
    except (TypeError, ValueError) as error:
        raise InvalidConfig(f"{path}: {error}") from None


def _coerce(hint: Any, raw: Any, path: str) -> Any:
    origin = typing.get_origin(hint)
    if origin in (types.UnionType, typing.Union):
        options = [option for option in typing.get_args(hint) if option is not type(None)]
        if raw is None:
            return None
        return _coerce(options[0], raw, path)
    if is_dataclass(hint):
        return _from_plain(hint, raw, path)
    if isinstance(hint, type) and issubclass(hint, StrEnum):
        try:
            return hint(raw)
        except ValueError:
            raise InvalidConfig(f"{path} must be one of {[m.value for m in hint]}") from None
    if origin is tuple:
        if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
            raise InvalidConfig(f"{path} must be a list of strings")
        return tuple(raw)
    if hint is bool or isinstance(raw, bool):
        raise InvalidConfig(f"{path} must not be a boolean")
    if hint is int:
        if not isinstance(raw, int):
            raise InvalidConfig(f"{path} must be an integer")
        return raw
    if hint is float:
        if not isinstance(raw, (int, float)):
            raise InvalidConfig(f"{path} must be a number")
        return float(raw)
    if hint is str:
        if not isinstance(raw, str):
            raise InvalidConfig(f"{path} must be a string")
        return raw
    raise InvalidConfig(f"{path} has an unsupported schema type")


def parse_et_clock(text: str) -> time:
    """Parse an ``HH:MM`` ET wall-clock string from the config. Raises ``InvalidConfig``."""
    parts = text.split(":")
    if len(parts) != 2 or not all(p.isdigit() and len(p) == 2 for p in parts):
        raise InvalidConfig(f"{text!r} must be an HH:MM ET clock time")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise InvalidConfig(f"{text!r} is not a valid clock time")
    return time(hour, minute)


def _clock(section: Any, *names: str) -> None:
    for name in names:
        value = getattr(section, name)
        if not isinstance(value, str):
            raise InvalidConfig(f"{type(section).__name__}.{name} must be an HH:MM string")
        parse_et_clock(value)


def _finite(section: Any, *names: str) -> None:
    for name in names:
        value = getattr(section, name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise InvalidConfig(f"{type(section).__name__}.{name} must be a finite number")


def _positive(section: Any, *names: str) -> None:
    _finite(section, *names)
    for name in names:
        if getattr(section, name) <= 0:
            raise InvalidConfig(f"{type(section).__name__}.{name} must be positive")


def _non_negative(section: Any, *names: str) -> None:
    _finite(section, *names)
    for name in names:
        if getattr(section, name) < 0:
            raise InvalidConfig(f"{type(section).__name__}.{name} must be non-negative")


def _fraction(section: Any, *names: str) -> None:
    _finite(section, *names)
    for name in names:
        if not 0 <= getattr(section, name) <= 1:
            raise InvalidConfig(f"{type(section).__name__}.{name} must be in [0, 1]")


def _ordered(section: Any, low: str, high: str) -> None:
    if getattr(section, low) > getattr(section, high):
        raise InvalidConfig(f"{type(section).__name__}.{low} must not exceed {high}")
