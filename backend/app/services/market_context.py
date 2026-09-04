"""Read-only, fail-safe extended-hours market context composition."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from threading import Lock
from zoneinfo import ZoneInfo

from app.core.exceptions import MarketDataError
from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession, MinuteBar
from app.market.provider import MarketDataProvider


ET = ZoneInfo("America/New_York")


class MissingReason(StrEnum):
    NOT_FETCHED = "NOT_FETCHED"
    NOT_AVAILABLE_FROM_PROVIDER = "NOT_AVAILABLE_FROM_PROVIDER"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    OUTSIDE_SESSION = "OUTSIDE_SESSION"
    UNIT_UNCONFIRMED = "UNIT_UNCONFIRMED"
    PROVIDER_ERROR = "PROVIDER_ERROR"


@dataclass(frozen=True)
class ExtendedQuote:
    price: float | None = None
    return_pct: float | None = None
    observed_at: datetime | None = None
    reason: MissingReason | None = None

    def as_dict(self) -> dict[str, object | None]:
        return {"price": self.price, "return_pct": self.return_pct,
                "observed_at": self.observed_at, "reason": self.reason}


class MarketContextService:
    def __init__(self, provider: MarketDataProvider | None, *, ttl_seconds: int = 60,
                 clock: Callable[[], datetime], calendar: MarketCalendar | None = None) -> None:
        self.provider = provider
        self.ttl = timedelta(seconds=ttl_seconds)
        self.clock = clock
        self.calendar = calendar or MarketCalendar("America/New_York")
        self._cache: dict[tuple[str, date], tuple[datetime, dict[str, object]]] = {}
        self._lock = Lock()

    def compose(self, symbol: str, previous_close: float | None, *, trading_date: date,
                as_of: datetime | None = None) -> dict[str, object]:
        now = as_of or self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        now_et = now.astimezone(ET)
        window = self.calendar.session(trading_date)
        unavailable = self._missing(MissingReason.NOT_FETCHED if self.provider is None else MissingReason.NOT_AVAILABLE_FROM_PROVIDER)
        result = {"premarket": unavailable, "postmarket": unavailable,
                  "market_cap_reason": MissingReason.UNIT_UNCONFIRMED,
                  "industry_reason": MissingReason.NOT_AVAILABLE_FROM_PROVIDER}
        if self.provider is None or window is None or trading_date > now_et.date():
            return result
        return self._quotes(symbol, trading_date, now_et, previous_close,
                            window.market_open, window.market_close, result)

    @staticmethod
    def _missing(reason: MissingReason) -> dict[str, object | None]:
        return ExtendedQuote(reason=reason).as_dict()

    def _quotes(self, symbol: str, trading_date: date, now: datetime,
                previous_close: float | None, market_open: datetime,
                market_close: datetime, result: dict[str, object]) -> dict[str, object]:
        key = (symbol, trading_date)
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < self.ttl:
                return cached[1]
        try:
            day_start = datetime.combine(trading_date, time(4), ET)
            day_end = datetime.combine(trading_date, time(20), ET)
            fetch_end = min(now, day_end) if trading_date == now.date() else day_end
            bars = self.provider.get_minute_bars([symbol], start=day_start, end=fetch_end) if self.provider else []
            premarket = [bar for bar in bars if day_start <= bar.timestamp < market_open]
            postmarket = [bar for bar in bars if market_close <= bar.timestamp < day_end]
            regular = [bar for bar in bars if market_open <= bar.timestamp < market_close]
            regular_close = max(regular, key=lambda bar: bar.timestamp).close if regular else previous_close
            result["premarket"] = self._from_bars(premarket, previous_close).as_dict()
            result["postmarket"] = self._from_bars(postmarket, regular_close).as_dict()
        except (MarketDataError, OSError, ValueError) as exc:
            reason = (
                MissingReason.INSUFFICIENT_HISTORY
                if isinstance(exc, MarketDataError) and exc.code == "INSUFFICIENT_HISTORY"
                else MissingReason.PROVIDER_ERROR
            )
            result["premarket"] = self._missing(reason)
            result["postmarket"] = self._missing(reason)
        with self._lock:
            self._cache[key] = (now, result)
        return result

    @staticmethod
    def _from_bars(bars: list[MinuteBar], base: float | None) -> ExtendedQuote:
        if not bars:
            return ExtendedQuote(reason=MissingReason.NOT_AVAILABLE_FROM_PROVIDER)
        latest = max(bars, key=lambda bar: bar.timestamp)
        return_pct = None if base in (None, 0) else (latest.close - base) / base
        return ExtendedQuote(price=latest.close, return_pct=return_pct, observed_at=latest.timestamp)
