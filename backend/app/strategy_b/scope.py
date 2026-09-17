"""PIT Research Scope (L1): which symbols B may study on trading date D, from D-1 data only.

The input types make same-day data unrepresentable rather than merely unused:

* ``ScopeInputs`` refuses metadata whose ``as_of_date`` is not before D and any daily bar
  dated D or later. It raises instead of filtering, because a caller that passes D data
  has a leak upstream that silently dropping one bar would hide.
* ``evaluate_research_scope`` takes nothing but ``ScopeInputs`` and config. Price and
  median dollar volume are derived here from the D-1 history; there is no argument for a
  "current price" or "today's volume".
* Input priority: PIT ticker metadata (security type, exchange/market, listing status, the
  provider's test-issue flag) and the D-1 daily history decide. ``ScopeConfig.test_symbols``
  is a hand-typed ``RESEARCH_DEFAULT_UNVERIFIED`` list and only a backup safeguard: a match
  excludes the symbol under its own reason, ``TEST_TICKER_UNVERIFIED_LIST``, so a report can
  tell a metadata exclusion from a guess. The list cannot re-include a symbol the metadata
  flags, and metadata saying "not a test issue" does not override the list.
* Market cap is not part of the metadata type. A ``date``-stamped market cap is that day's
  close × shares, so on D it would be a same-day value, and fetching it per symbol per day is
  not affordable anyway.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from math import isfinite
from statistics import median

from app.strategy_b.config import ScopeConfig
from app.strategy_b.errors import PointInTimeViolation


class ListingStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class ScopeExclusion(StrEnum):
    SECURITY_TYPE = "SECURITY_TYPE"
    EXCHANGE = "EXCHANGE"
    OTC = "OTC"
    TEST_TICKER = "TEST_TICKER"
    """The PIT metadata flags a test issue."""
    TEST_TICKER_UNVERIFIED_LIST = "TEST_TICKER_UNVERIFIED_LIST"
    """The symbol is on ``ScopeConfig.test_symbols``, a research default not checked against an exchange."""
    NOT_ACTIVE = "NOT_ACTIVE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    PRICE_BELOW_FLOOR = "PRICE_BELOW_FLOOR"
    DOLLAR_VOLUME_BELOW_FLOOR = "DOLLAR_VOLUME_BELOW_FLOOR"


OTC_MARKERS = frozenset({"OTC", "OTCM", "OOTC", "PINX", "XOTC"})


@dataclass(frozen=True, slots=True)
class TickerMetadataAsOf:
    """Reference ticker data exactly as the provider reported it for ``as_of_date``."""

    symbol: str
    as_of_date: date
    security_type: str
    primary_exchange: str
    market: str
    listing_status: ListingStatus
    list_date: date | None = None
    test_issue: bool | None = None
    """Provider test-issue flag as of ``as_of_date``; None when the source has no such field."""


@dataclass(frozen=True, slots=True)
class PriorDailyBar:
    trading_date: date
    close: float
    volume: float

    def __post_init__(self) -> None:
        if not isfinite(self.close) or self.close <= 0:
            raise ValueError("close must be finite and positive")
        if not isfinite(self.volume) or self.volume < 0:
            raise ValueError("volume must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class ScopeInputs:
    decision_date: date
    metadata: TickerMetadataAsOf
    daily_history: tuple[PriorDailyBar, ...]

    def __post_init__(self) -> None:
        if self.metadata.as_of_date >= self.decision_date:
            raise PointInTimeViolation(
                f"{self.metadata.symbol} metadata as of {self.metadata.as_of_date} is not before "
                f"decision date {self.decision_date}")
        dates = [bar.trading_date for bar in self.daily_history]
        leaked = [d for d in dates if d >= self.decision_date]
        if leaked:
            raise PointInTimeViolation(
                f"{self.metadata.symbol} daily history contains {leaked[0]}, on or after decision "
                f"date {self.decision_date}; the scope may only see D-1 and earlier")
        if dates != sorted(set(dates)):
            raise ValueError("daily history must be strictly increasing by trading_date")


@dataclass(frozen=True, slots=True)
class ScopeDecision:
    symbol: str
    decision_date: date
    included: bool
    exclusion_reasons: tuple[ScopeExclusion, ...]
    reference_price: float | None
    median_dollar_volume: float | None
    history_sessions: int


def evaluate_research_scope(inputs: ScopeInputs, config: ScopeConfig) -> ScopeDecision:
    """Every applicable exclusion, in enum order, so a report shows all reasons at once."""
    meta = inputs.metadata
    reasons: set[ScopeExclusion] = set()
    if meta.security_type.upper() not in config.allowed_security_types:
        reasons.add(ScopeExclusion.SECURITY_TYPE)
    if meta.market.upper() in OTC_MARKERS or meta.primary_exchange.upper() in OTC_MARKERS:
        reasons.add(ScopeExclusion.OTC)
    if meta.primary_exchange.upper() not in config.allowed_exchanges:
        reasons.add(ScopeExclusion.EXCHANGE)
    if meta.test_issue is True:
        reasons.add(ScopeExclusion.TEST_TICKER)
    if meta.symbol.upper() in config.test_symbols:
        reasons.add(ScopeExclusion.TEST_TICKER_UNVERIFIED_LIST)
    if meta.listing_status is not ListingStatus.ACTIVE:
        reasons.add(ScopeExclusion.NOT_ACTIVE)

    history = inputs.daily_history[-config.median_lookback_sessions:]
    reference_price = history[-1].close if history else None
    median_dollar_volume = median(b.close * b.volume for b in history) if history else None
    if len(history) < config.min_history_sessions:
        reasons.add(ScopeExclusion.INSUFFICIENT_HISTORY)
    if reference_price is not None and reference_price < config.price_floor:
        reasons.add(ScopeExclusion.PRICE_BELOW_FLOOR)
    if median_dollar_volume is not None and median_dollar_volume < config.median_dollar_volume_floor:
        reasons.add(ScopeExclusion.DOLLAR_VOLUME_BELOW_FLOOR)

    ordered = tuple(r for r in ScopeExclusion if r in reasons)
    return ScopeDecision(
        symbol=meta.symbol, decision_date=inputs.decision_date, included=not ordered,
        exclusion_reasons=ordered, reference_price=reference_price,
        median_dollar_volume=median_dollar_volume, history_sessions=len(history))


def evaluate_scope_batch(batch: Sequence[ScopeInputs], config: ScopeConfig) -> tuple[ScopeDecision, ...]:
    dates = {inputs.decision_date for inputs in batch}
    if len(dates) > 1:
        raise ValueError("one scope batch is one decision date")
    return tuple(evaluate_research_scope(inputs, config) for inputs in batch)
