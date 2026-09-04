from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
import httpx

from app.api.router import display_market_status
from app.core.config import Settings
from app.core.exceptions import MarketDataError
from app.integrations.kiwoom.auth import KiwoomAuthClient
from app.integrations.kiwoom.client import KiwoomMarketDataClient
from app.integrations.kiwoom.rate_limit import KiwoomRateLimits, RequestRateLimiter
from app.market.kiwoom import KiwoomMarketDataProvider
from app.market.domain import DailyBar, MarketSession, MinuteBar
from app.services.market_context import MarketContextService, MissingReason


ET = ZoneInfo("America/New_York")


def minute(hour: int, minute_value: int, close: float, session: MarketSession) -> MinuteBar:
    stamp = datetime(2026, 9, 3, hour, minute_value, tzinfo=ET)
    return MinuteBar(symbol="AAPL", timestamp=stamp, session=session, open=close,
                     high=close, low=close, close=close, volume=100,
                     observed_at=stamp, available_at=stamp)


class FixtureProvider:
    def __init__(self, bars: list[MinuteBar]) -> None:
        self.bars = bars
        self.calls = 0

    def get_minute_bars(self, symbols, start=None, end=None, session=None):
        self.calls += 1
        return [bar for bar in self.bars if (start is None or bar.timestamp >= start) and (end is None or bar.timestamp <= end)]

    def get_daily_bars(self, symbols, start=None, end=None) -> list[DailyBar]:
        return []


def service(provider: FixtureProvider, now: datetime) -> MarketContextService:
    return MarketContextService(provider, clock=lambda: now, ttl_seconds=60)


def test_premarket_uses_previous_regular_close_and_caches() -> None:
    now = datetime(2026, 9, 3, 8, 1, tzinfo=ET)
    provider = FixtureProvider([minute(7, 59, 105, MarketSession.PREMARKET)])
    composer = service(provider, now)
    first = composer.compose("AAPL", 100.0, trading_date=date(2026, 9, 3), as_of=now)["premarket"]
    second = composer.compose("AAPL", 100.0, trading_date=date(2026, 9, 3), as_of=now)["premarket"]
    assert first == second
    assert first["price"] == 105
    assert first["return_pct"] == pytest.approx(0.05)
    assert first["observed_at"].tzinfo is not None
    assert provider.calls == 1


def test_postmarket_uses_same_day_regular_close() -> None:
    now = datetime(2026, 9, 3, 17, 1, tzinfo=ET)
    provider = FixtureProvider([
        minute(15, 59, 110, MarketSession.REGULAR),
        minute(17, 0, 121, MarketSession.POSTMARKET),
    ])
    result = service(provider, now).compose("AAPL", 100.0, trading_date=date(2026, 9, 3), as_of=now)["postmarket"]
    assert result["price"] == 121
    assert result["return_pct"] == pytest.approx(0.1)


def test_closed_session_returns_latest_premarket_and_postmarket() -> None:
    now = datetime(2026, 9, 3, 21, tzinfo=ET)
    provider = FixtureProvider([
        minute(9, 29, 105, MarketSession.PREMARKET),
        minute(15, 59, 110, MarketSession.REGULAR),
        minute(19, 58, 121, MarketSession.POSTMARKET),
    ])
    result = service(provider, now).compose(
        "AAPL", 100.0, trading_date=date(2026, 9, 3), as_of=now,
    )
    assert result["premarket"]["price"] == 105
    assert result["premarket"]["return_pct"] == pytest.approx(0.05)
    assert result["postmarket"]["price"] == 121
    assert result["postmarket"]["return_pct"] == pytest.approx(0.1)
    assert provider.calls == 1


def test_closed_session_missing_extended_bars_is_safe() -> None:
    now = datetime(2026, 9, 3, 21, tzinfo=ET)
    result = service(FixtureProvider([]), now).compose(
        "AAPL", 100, trading_date=date(2026, 9, 3), as_of=now,
    )
    assert result["premarket"]["reason"] == MissingReason.NOT_AVAILABLE_FROM_PROVIDER
    assert result["postmarket"]["reason"] == MissingReason.NOT_AVAILABLE_FROM_PROVIDER


def test_trading_date_is_not_shifted_by_kst_rollover() -> None:
    now = datetime(2026, 9, 4, 9, tzinfo=ZoneInfo("Asia/Seoul"))
    provider = FixtureProvider([
        minute(15, 59, 110, MarketSession.REGULAR),
        minute(19, 58, 121, MarketSession.POSTMARKET),
    ])
    result = service(provider, now).compose(
        "AAPL", 100, trading_date=date(2026, 9, 3), as_of=now,
    )
    assert result["postmarket"]["price"] == 121
    assert result["postmarket"]["observed_at"].astimezone(ZoneInfo("Asia/Seoul")).date() == date(2026, 9, 4)


def test_missing_reasons_and_naive_time() -> None:
    regular = datetime(2026, 9, 3, 12, tzinfo=ET)
    result = service(FixtureProvider([]), regular).compose("AAPL", 100, trading_date=date(2026, 9, 3), as_of=regular)
    assert result["premarket"]["reason"] == MissingReason.NOT_AVAILABLE_FROM_PROVIDER
    assert result["postmarket"]["reason"] == MissingReason.NOT_AVAILABLE_FROM_PROVIDER
    assert result["market_cap_reason"] == MissingReason.UNIT_UNCONFIRMED
    with pytest.raises(ValueError, match="timezone-aware"):
        service(FixtureProvider([]), regular).compose("AAPL", 100, trading_date=date(2026, 9, 3), as_of=datetime(2026, 9, 3, 8))


@pytest.mark.parametrize(("when", "expected"), [
    (datetime(2026, 9, 3, 8, tzinfo=ET), "PREMARKET"),
    (datetime(2026, 9, 3, 10, tzinfo=ET), "REGULAR"),
    (datetime(2026, 9, 3, 17, tzinfo=ET), "POSTMARKET"),
    (datetime(2026, 9, 3, 21, 43, tzinfo=ET), "CLOSED"),
])
def test_display_sessions(when: datetime, expected: str) -> None:
    settings = Settings(_env_file=None)
    assert display_market_status(when, settings)["session"] == expected


def test_early_close_and_dst_are_calendar_driven() -> None:
    settings = Settings(_env_file=None)
    assert display_market_status(datetime(2025, 11, 28, 14, tzinfo=ET), settings)["session"] == "POSTMARKET"
    assert display_market_status(datetime(2026, 1, 5, 10, tzinfo=ET), settings)["session"] == "REGULAR"
    assert display_market_status(datetime(2026, 7, 6, 10, tzinfo=ET), settings)["session"] == "REGULAR"


class TruncatedProvider(FixtureProvider):
    def get_minute_bars(self, symbols, start=None, end=None, session=None):
        raise MarketDataError("INSUFFICIENT_HISTORY", "bounded history did not reach target")


def test_truncated_history_is_not_reported_as_actual_no_data() -> None:
    now = datetime(2026, 9, 4, 1, tzinfo=ET)
    result = service(TruncatedProvider([]), now).compose(
        "TSLA", 370.51, trading_date=date(2026, 9, 3), as_of=now,
    )
    assert result["premarket"]["reason"] == MissingReason.INSUFFICIENT_HISTORY
    assert result["postmarket"]["reason"] == MissingReason.INSUFFICIENT_HISTORY


class NoopLimiter(RequestRateLimiter):
    def __init__(self) -> None:
        super().__init__(1)

    def acquire(self) -> None:
        pass


def test_postmarket_after_more_than_ten_pages_uses_snapshot_same_day_close() -> None:
    chart_calls = 0

    def raw(day: str, at: str, price: str) -> dict[str, str]:
        return {"bus_dt": day, "cntr_tm": at, "open_pric": price, "high_pric": price,
                "low_pric": price, "cur_prc": price, "trde_qty": "10"}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chart_calls
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "t", "expires_dt": "20990101000000", "return_code": 0})
        chart_calls += 1
        if chart_calls <= 10:
            rows = [raw("20260904", "190000", "380")]
        elif chart_calls == 11:
            rows = [raw("20260903", "195900", "374.2151")]
        else:
            rows = [raw("20260903", "040000", "360")]
        return httpx.Response(200, headers={"cont-yn": "Y", "next-key": str(chart_calls)},
                              json={"result_list": rows, "return_code": 0})

    limits = KiwoomRateLimits()
    limits.auth = limits.query = limits.chart = limits.realtime_subscription = NoopLimiter()
    http = httpx.Client(transport=httpx.MockTransport(handler))
    auth = KiwoomAuthClient(base_url="https://api.kiwoom.com", app_key="key", app_secret="secret",
                            http=http, limiter=NoopLimiter(), clock=lambda: datetime(2026, 9, 4, 12, tzinfo=ET))
    client = KiwoomMarketDataClient(base_url="https://api.kiwoom.com", auth=auth, http=http,
                                    rate_limits=limits, sleeper=lambda _: None)
    provider = KiwoomMarketDataProvider(client, clock=lambda: datetime(2026, 9, 4, 12, tzinfo=ET))
    result = MarketContextService(provider, clock=lambda: datetime(2026, 9, 4, 12, tzinfo=ET)).compose(
        "TSLA", 370.51, trading_date=date(2026, 9, 3),
    )
    assert chart_calls == 12 and client.last_minute_collection is not None
    assert client.last_minute_collection.target_reached
    assert result["postmarket"]["observed_at"] == datetime(2026, 9, 3, 19, 59, tzinfo=ET)
    assert result["postmarket"]["price"] == pytest.approx(374.2151)
    assert result["postmarket"]["return_pct"] == pytest.approx(0.01)
