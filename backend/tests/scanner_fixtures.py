from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.market.domain import DailyBar
from app.market.fake import FakeMarketDataProvider
from app.market.reference import FakeSymbolMetadataProvider, SymbolMetadata


ET = ZoneInfo("America/New_York")
TRADING_DATE = date(2024, 6, 28)
SCAN_AS_OF = datetime(2024, 6, 28, 18, 0, tzinfo=ET)


def trading_days(count: int, end: date = TRADING_DATE) -> list[date]:
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def history(
    symbol: str,
    *,
    count: int = 21,
    base_close: float = 100.0,
    daily_step: float = 0.0,
    historical_volume: int = 300_000,
    latest_volume: int | None = None,
    latest_close: float | None = None,
    latest_available_at: datetime | None = None,
) -> list[DailyBar]:
    result: list[DailyBar] = []
    days = trading_days(count)
    for index, day in enumerate(days):
        close = base_close + daily_step * index
        if index == len(days) - 1 and latest_close is not None:
            close = latest_close
        volume = (
            latest_volume
            if index == len(days) - 1 and latest_volume is not None
            else historical_volume
        )
        observed = datetime.combine(day, time(16, 1), tzinfo=ET)
        available = observed + timedelta(minutes=1)
        if index == len(days) - 1 and latest_available_at is not None:
            available = latest_available_at
            observed = min(observed, available)
        result.append(
            DailyBar(
                symbol=symbol,
                trading_date=day,
                open=close,
                high=close + 1.0,
                low=max(0.01, close - 1.0),
                close=close,
                volume=volume,
                observed_at=observed,
                available_at=available,
            )
        )
    return result


def symbol_metadata(
    symbol: str,
    *,
    market_cap: float = 1_000_000_000.0,
    active: bool = True,
) -> SymbolMetadata:
    observed = datetime(2024, 6, 27, 18, 0, tzinfo=ET)
    return SymbolMetadata(
        symbol=symbol,
        company_name=f"{symbol} Corp",
        market_cap=market_cap,
        exchange="NASDAQ",
        active=active,
        observed_at=observed,
        available_at=observed,
    )


def scanner_providers(
    symbols: list[str],
    *,
    bars_by_symbol: dict[str, list[DailyBar]] | None = None,
    metadata_by_symbol: dict[str, SymbolMetadata] | None = None,
) -> tuple[FakeMarketDataProvider, FakeSymbolMetadataProvider]:
    bars = history("SPY", daily_step=0.1)
    for symbol in symbols:
        bars.extend((bars_by_symbol or {}).get(symbol, history(symbol)))
    metadata = [
        (metadata_by_symbol or {}).get(symbol, symbol_metadata(symbol))
        for symbol in symbols
    ]
    return FakeMarketDataProvider(daily_bars=bars), FakeSymbolMetadataProvider(metadata)

