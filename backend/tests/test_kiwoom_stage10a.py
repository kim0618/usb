from datetime import date, datetime, timedelta, timezone
import logging
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import MarketDataError
from app.integrations.kiwoom.auth import KiwoomAuthClient
from app.integrations.kiwoom.client import KiwoomMarketDataClient
from app.integrations.kiwoom.rate_limit import KiwoomRateLimits, RequestRateLimiter
from app.market.kiwoom import KiwoomMarketDataProvider
from app.market.provider import MarketDataProvider
from app.market.reference import SymbolMetadataProvider

NOW = datetime(2026, 7, 2, 19, 0, tzinfo=timezone.utc)


class NoopLimiter(RequestRateLimiter):
    def __init__(self) -> None:
        super().__init__(1)

    def acquire(self) -> None:
        pass


def limits() -> KiwoomRateLimits:
    value = KiwoomRateLimits()
    value.auth = value.query = value.chart = value.realtime_subscription = NoopLimiter()
    return value


def auth(http: httpx.Client, clock=lambda: NOW) -> KiwoomAuthClient:  # type: ignore[no-untyped-def]
    return KiwoomAuthClient(base_url="https://api.kiwoom.com", app_key="key", app_secret="secret", http=http, limiter=NoopLimiter(), clock=clock)


def client(http: httpx.Client, **kwargs: object) -> KiwoomMarketDataClient:
    return KiwoomMarketDataClient(base_url="https://api.kiwoom.com", auth=auth(http), http=http, rate_limits=limits(), sleeper=lambda _: None, **kwargs)


def test_auth_success_cache_and_secret_masking(caplog: pytest.LogCaptureFixture) -> None:
    calls = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/oauth2/token"
        return httpx.Response(200, json={"token": "access-value", "token_type": "bearer", "expires_dt": "20990101000000", "return_code": 0})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    instance = auth(http)
    with caplog.at_level(logging.INFO):
        assert instance.access_token() == instance.access_token() == "access-value"
    assert calls == 1
    assert "key" not in caplog.text and "secret" not in caplog.text and "access-value" not in caplog.text


def test_auth_failure_and_expiration_refresh() -> None:
    failed = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(401, json={})))
    with pytest.raises(MarketDataError, match="authentication failed") as error:
        auth(failed).access_token()
    assert error.value.code == "AUTH_FAILED"

    current = [NOW]
    calls = 0
    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        expiry = (current[0] + timedelta(minutes=2)).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d%H%M%S")
        return httpx.Response(200, json={"token": f"token-{calls}", "expires_dt": expiry, "return_code": 0})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    instance = auth(http, lambda: current[0])
    assert instance.access_token() == "token-1"
    current[0] += timedelta(seconds=61)
    assert instance.access_token() == "token-2"


def test_read_only_allowlist_blocks_every_order_path_before_http() -> None:
    http_calls = 0
    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(500)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(MarketDataError) as error:
        client(http).request("ust20000", "/api/us/ordr", {})
    assert error.value.code == "ENDPOINT_BLOCKED" and http_calls == 0
    assert not hasattr(client(http), "submit_order")


def test_quote_unknown_fields_and_error_normalization() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "t", "expires_dt": "20990101000000", "return_code": 0})
        return httpx.Response(200, json={"stk_cd": "AAPL", "unknown": {"nested": True}, "return_code": 0})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert client(http).quote("AAPL", "ND")["stk_cd"] == "AAPL"

    invalid = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"token": "t", "expires_dt": "20990101000000", "return_code": 0}) if request.url.path == "/oauth2/token" else httpx.Response(200, json={"return_code": 1, "return_msg": "종목코드 오류"})))
    with pytest.raises(MarketDataError) as error:
        client(invalid).quote("BAD", "ND")
    assert error.value.code == "INVALID_SYMBOL"


@pytest.mark.parametrize("status,code", [(429, "RATE_LIMITED"), (500, "MARKET_DATA_UNAVAILABLE")])
def test_retry_is_bounded(status: int, code: str) -> None:
    calls = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "t", "expires_dt": "20990101000000", "return_code": 0})
        calls += 1
        return httpx.Response(status)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(MarketDataError) as error:
        client(http, max_retries=2).quote("AAPL", "ND")
    assert error.value.code == code and calls == 3


def test_timeout_is_normalized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "t", "expires_dt": "20990101000000", "return_code": 0})
        raise httpx.ReadTimeout("late", request=request)
    with pytest.raises(MarketDataError) as error:
        client(httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0).quote("AAPL", "ND")
    assert error.value.code == "PROVIDER_TIMEOUT"


def test_provider_contract_mapping_point_in_time_and_metadata_cache() -> None:
    quote_calls = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal quote_calls
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "t", "expires_dt": "20990101000000", "return_code": 0})
        api_id = request.headers["api-id"]
        if api_id == "usa20100":
            quote_calls += 1
            return httpx.Response(200, json={"stex_tp": "ND", "stk_cd": "AAPL", "stk_enm": "APPLE INC", "mac": "3000000", "trd_susp_tp": "N", "return_code": 0})
        if api_id == "usa06012":
            return httpx.Response(200, json={"result_list": [
                {"dt": "20260701", "open_pric": "+100", "high_pric": "+110", "low_pric": "+99", "cur_prc": "+108", "acc_trde_qty": "1000", "extra": "ok"},
                {"dt": "20260702", "open_pric": "108", "high_pric": "111", "low_pric": "107", "cur_prc": "110", "acc_trde_qty": "900"},
            ], "return_code": 0})
        return httpx.Response(200, json={"result_list": [{"bus_dt": "20260702", "cntr_tm": "093000", "open_pric": "108", "high_pric": "109", "low_pric": "107", "cur_prc": "108.5", "trde_qty": "50"}], "return_code": 0})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = KiwoomMarketDataProvider(client(http), clock=lambda: NOW)
    assert isinstance(provider, MarketDataProvider) and isinstance(provider, SymbolMetadataProvider)
    daily = provider.get_daily_bars(["aapl"], end=date(2026, 7, 2))
    assert [bar.trading_date for bar in daily] == [date(2026, 7, 1)]
    assert daily[0].symbol == "AAPL" and daily[0].close == 108 and daily[0].available_at.tzinfo
    minute = provider.get_minute_bars(["AAPL"])
    assert minute[0].timestamp.tzinfo and minute[0].available_at >= minute[0].observed_at
    assert provider.get_metadata(["AAPL"], NOW)["AAPL"].exchange == "NASDAQ"
    assert provider.get_metadata(["AAPL"], NOW)["AAPL"].company_name == "APPLE INC"
    assert quote_calls == 1


def test_settings_enforce_simulation_and_hide_secrets() -> None:
    settings = Settings(_env_file=None, market_data_provider="kiwoom", broker_provider="simulation", kiwoom_app_key="real-key", kiwoom_app_secret="real-secret")
    serialized = str(settings.model_dump())
    assert "real-key" not in serialized and "real-secret" not in serialized
    assert settings.has_kiwoom_credentials
    with pytest.raises(ValidationError):
        Settings(_env_file=None, market_data_provider="kiwoom", broker_provider="live")


def test_rate_limiter_is_shared_and_waits() -> None:
    current = [0.0]
    sleeps: list[float] = []
    def sleep(delay: float) -> None:
        sleeps.append(delay)
        current[0] += delay
    limiter = RequestRateLimiter(2, clock=lambda: current[0], sleeper=sleep)
    limiter.acquire(); limiter.acquire(); limiter.acquire()
    assert sleeps == [0.5, 0.5]
