"""USB provider adapter backed only by Kiwoom read-only market-data APIs."""

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone

from app.core.exceptions import MarketDataError
from app.integrations.kiwoom.client import KiwoomMarketDataClient
from app.integrations.kiwoom.mapping import map_daily_bar, map_metadata, map_minute_bar
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.market.provider import MarketDataProvider
from app.market.reference import SymbolMetadata, SymbolMetadataProvider
from app.market.symbols import normalize_symbol


class KiwoomMarketDataProvider(MarketDataProvider, SymbolMetadataProvider):
    def __init__(
        self,
        client: KiwoomMarketDataClient,
        *,
        exchanges: Mapping[str, str] | None = None,
        default_exchange: str = "ND",
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.client = client
        self._exchanges = {normalize_symbol(k): v.strip().upper() for k, v in (exchanges or {}).items()}
        self._default_exchange = default_exchange.strip().upper()
        self._clock = clock
        self._metadata_cache: dict[tuple[str, str], SymbolMetadata] = {}

    def _exchange(self, symbol: str) -> str:
        exchange = self._exchanges.get(symbol, self._default_exchange)
        if exchange not in {"NA", "ND", "NY"}:
            raise MarketDataError("INVALID_SYMBOL", "A supported exchange is required")
        return exchange

    def get_daily_bars(
        self, symbols: Sequence[str], start: date | None = None, end: date | None = None
    ) -> list[DailyBar]:
        received_at = self._clock()
        bars: list[DailyBar] = []
        for raw_symbol in sorted(set(symbols)):
            symbol = normalize_symbol(raw_symbol)
            rows = self.client.daily_chart(
                symbol, self._exchange(symbol), None if start is None else start.strftime("%Y%m%d")
            )
            for row in rows:
                try:
                    bar = map_daily_bar(symbol, row, received_at)
                except MarketDataError as exc:
                    if exc.code == "FUTURE_DATA":
                        continue
                    raise
                if (start is None or bar.trading_date >= start) and (end is None or bar.trading_date <= end):
                    bars.append(bar)
        return sorted(bars, key=lambda bar: (bar.trading_date, bar.symbol))

    def get_minute_bars(
        self,
        symbols: Sequence[str],
        start: datetime | None = None,
        end: datetime | None = None,
        session: MarketSession | None = None,
    ) -> list[MinuteBar]:
        received_at = self._clock()
        bars: list[MinuteBar] = []
        for raw_symbol in sorted(set(symbols)):
            symbol = normalize_symbol(raw_symbol)
            rows = self.client.minute_chart(
                symbol, self._exchange(symbol), None if start is None else start.astimezone(timezone.utc).strftime("%Y%m%d")
            )
            for row in rows:
                try:
                    bar = map_minute_bar(symbol, row, received_at)
                except MarketDataError as exc:
                    if exc.code == "FUTURE_DATA":
                        continue
                    raise
                if (start is None or bar.timestamp >= start) and (end is None or bar.timestamp <= end) and (session is None or bar.session == session):
                    bars.append(bar)
        return sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol))

    def get_metadata(self, symbols: Sequence[str], as_of: datetime) -> dict[str, SymbolMetadata]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        result: dict[str, SymbolMetadata] = {}
        for raw_symbol in sorted(set(symbols)):
            symbol = normalize_symbol(raw_symbol)
            key = (symbol, self._exchange(symbol))
            item = self._metadata_cache.get(key)
            if item is None:
                received_at = self._clock()
                item = map_metadata(symbol, self.client.quote(symbol, key[1]), received_at)
                self._metadata_cache[key] = item
            if item.available_at <= as_of:
                result[symbol] = item
        return result
