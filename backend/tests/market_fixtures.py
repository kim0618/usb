from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.market.domain import DailyBar, MarketSession, MinuteBar


ET = ZoneInfo("America/New_York")


def daily_bar(symbol: str = "AAA", day: date = date(2024, 6, 18)) -> DailyBar:
    observed = datetime(day.year, day.month, day.day, 16, 1, tzinfo=ET)
    return DailyBar(
        symbol=symbol,
        trading_date=day,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=1_000_000,
        observed_at=observed,
        available_at=observed + timedelta(minutes=1),
    )


def minute_bar(
    symbol: str = "AAA",
    minute: int = 31,
    *,
    session: MarketSession = MarketSession.REGULAR,
    available_minute: int | None = None,
    close: float = 101.0,
) -> MinuteBar:
    timestamp = datetime(2024, 6, 18, 9, minute, tzinfo=ET)
    observed = timestamp + timedelta(minutes=1)
    available = datetime(
        2024, 6, 18, 9, available_minute, tzinfo=ET
    ) if available_minute is not None else observed
    return MinuteBar(
        symbol=symbol,
        timestamp=timestamp,
        session=session,
        open=100.0,
        high=max(102.0, close),
        low=99.0,
        close=close,
        volume=10_000,
        observed_at=observed,
        available_at=available,
    )

