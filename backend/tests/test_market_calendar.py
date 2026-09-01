from datetime import date, time

from app.market.calendar import MarketCalendar


calendar = MarketCalendar()


def test_regular_trading_day() -> None:
    session = calendar.session(date(2024, 6, 18))
    assert session is not None
    assert session.market_open.time() == time(9, 30)
    assert session.market_close.time() == time(16, 0)
    assert not session.is_early_close


def test_weekend_is_not_trading_day_or_holiday() -> None:
    day = date(2024, 6, 15)
    assert not calendar.is_trading_day(day)
    assert not calendar.is_holiday(day)


def test_us_market_holiday() -> None:
    day = date(2024, 7, 4)
    assert not calendar.is_trading_day(day)
    assert calendar.is_holiday(day)


def test_early_close() -> None:
    session = calendar.session(date(2024, 11, 29))
    assert session is not None
    assert session.market_close.time() == time(13, 0)
    assert calendar.is_early_close(session.session_date)


def test_session_datetimes_are_timezone_aware() -> None:
    winter = calendar.session(date(2024, 1, 3))
    summer = calendar.session(date(2024, 7, 3))
    assert winter is not None and summer is not None
    assert winter.market_open.tzinfo is not None
    assert summer.market_open.tzinfo is not None
    assert winter.market_open.utcoffset() != summer.market_open.utcoffset()

