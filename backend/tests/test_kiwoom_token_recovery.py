"""Kiwoom market-data authentication recovers once without token stampedes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from threading import Barrier, Lock
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.core.exceptions import MarketDataError
from app.integrations.kiwoom.auth import KiwoomAuthClient
from app.integrations.kiwoom.client import KiwoomMarketDataClient
from app.integrations.kiwoom.rate_limit import KiwoomRateLimits, RequestRateLimiter

NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
EXPIRY = "20990101000000"


class NoopLimiter(RequestRateLimiter):
    def __init__(self) -> None:
        super().__init__(1)

    def acquire(self) -> None:
        pass


def limits() -> KiwoomRateLimits:
    value = KiwoomRateLimits()
    value.auth = value.query = value.chart = value.realtime_subscription = NoopLimiter()
    return value


def auth(http: httpx.Client, key: str) -> KiwoomAuthClient:
    return KiwoomAuthClient(
        base_url="https://api.kiwoom.com", app_key=key, app_secret="fake-secret",
        http=http, limiter=NoopLimiter(), clock=lambda: NOW,
    )


def client(http: httpx.Client, key: str, *, max_retries: int = 0,
           auth_client: KiwoomAuthClient | None = None) -> KiwoomMarketDataClient:
    return KiwoomMarketDataClient(
        base_url="https://api.kiwoom.com", auth=auth_client or auth(http, key),
        http=http, rate_limits=limits(), max_retries=max_retries, sleeper=lambda _: None,
    )


def token_response(value: str) -> httpx.Response:
    return httpx.Response(200, json={
        "token": value, "expires_dt": EXPIRY, "return_code": 0,
    })


def auth_failed() -> httpx.Response:
    return httpx.Response(200, json={
        "return_code": 3, "return_msg": "Token이 유효하지 않습니다",
    })


def test_valid_cached_token_uses_one_http_request_without_additional_issuance() -> None:
    issued = market = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued, market
        if request.url.path == "/oauth2/token":
            issued += 1
            return token_response("fake-valid")
        market += 1
        assert request.headers["authorization"] == "Bearer fake-valid"
        return httpx.Response(200, json={"return_code": 0, "stk_cd": "AAPL"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    authorization = auth(http, "valid-cached")
    assert authorization.access_token() == "fake-valid"
    assert client(http, "unused", auth_client=authorization).quote("AAPL", "ND")["stk_cd"] == "AAPL"
    assert (issued, market) == (1, 1)


def test_body_auth_failure_refreshes_once_and_retries_with_new_header(
        monkeypatch: pytest.MonkeyPatch) -> None:
    issued = 0
    headers: list[str] = []
    logs: list[str] = []

    def record(message: str, *args: object) -> None:
        logs.append(message % args if args else message)

    monkeypatch.setattr("app.integrations.kiwoom.client.logger.warning", record)
    monkeypatch.setattr("app.integrations.kiwoom.client.logger.info", record)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued
        if request.url.path == "/oauth2/token":
            issued += 1
            return token_response(f"fake-token-{issued}")
        headers.append(request.headers["authorization"])
        return auth_failed() if len(headers) == 1 else httpx.Response(
            200, json={"return_code": 0, "stk_cd": "AAPL"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    result = client(http, "body-auth-success").quote("AAPL", "ND")
    assert result["stk_cd"] == "AAPL"
    assert issued == 2
    assert headers == ["Bearer fake-token-1", "Bearer fake-token-2"]
    assert any("result=SUCCESS" in message for message in logs)
    assert all("fake-token" not in message for message in logs)


@pytest.mark.parametrize("http_status", [200, 401, 403])
def test_retry_auth_failure_fails_closed_after_one_refresh(http_status: int) -> None:
    issued = market = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued, market
        if request.url.path == "/oauth2/token":
            issued += 1
            return token_response(f"fake-double-{issued}")
        market += 1
        return auth_failed() if http_status == 200 else httpx.Response(http_status, json={})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(MarketDataError) as error:
        client(http, f"double-auth-{http_status}").quote("AAPL", "ND")
    assert error.value.code == "AUTH_FAILED"
    assert (issued, market) == (2, 2)


def test_invalid_symbol_never_refreshes() -> None:
    issued = market = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued, market
        if request.url.path == "/oauth2/token":
            issued += 1
            return token_response("fake-symbol")
        market += 1
        return httpx.Response(200, json={"return_code": 3,
                                         "return_msg": "종목코드가 존재하지 않습니다"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(MarketDataError) as error:
        client(http, "invalid-symbol").quote("BAD", "ND")
    assert error.value.code == "INVALID_SYMBOL"
    assert (issued, market) == (1, 1)


@pytest.mark.parametrize(("failure", "code", "market_calls"), [
    ("rate", "RATE_LIMITED", 3),
    ("network", "PROVIDER_TIMEOUT", 3),
])
def test_transport_failures_keep_existing_retry_and_never_refresh(
        failure: str, code: str, market_calls: int) -> None:
    issued = market = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued, market
        if request.url.path == "/oauth2/token":
            issued += 1
            return token_response("fake-transport")
        market += 1
        if failure == "rate":
            return httpx.Response(429)
        raise httpx.ReadTimeout("late", request=request)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(MarketDataError) as error:
        client(http, f"transport-{failure}", max_retries=2).quote("AAPL", "ND")
    assert error.value.code == code
    assert issued == 1 and market == market_calls


def test_refresh_endpoint_failure_does_not_retry_original_request_forever() -> None:
    issued = market = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued, market
        if request.url.path == "/oauth2/token":
            issued += 1
            return token_response("fake-stale") if issued == 1 else httpx.Response(500)
        market += 1
        return auth_failed()

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(MarketDataError) as error:
        client(http, "refresh-failure").quote("AAPL", "ND")
    assert error.value.code == "AUTH_FAILED"
    assert (issued, market) == (2, 1)


def test_concurrent_refresh_failure_is_one_failed_issuance_not_a_stampede() -> None:
    issued = 0
    issued_lock = Lock()
    stale_barrier = Barrier(2)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued
        if request.url.path == "/oauth2/token":
            with issued_lock:
                issued += 1
                number = issued
            return token_response("fake-failing-wave") if number == 1 else httpx.Response(500)
        stale_barrier.wait(timeout=5)
        return auth_failed()

    http = httpx.Client(transport=httpx.MockTransport(handler))
    authorization = auth(http, "shared-refresh-failure")
    assert authorization.access_token() == "fake-failing-wave"
    clients = [client(http, "unused-failure-1", auth_client=authorization),
               client(http, "unused-failure-2", auth_client=authorization)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(instance.quote, "AAPL", "ND") for instance in clients]
    errors = []
    for future in futures:
        with pytest.raises(MarketDataError) as error:
            future.result()
        errors.append(error.value.code)
    assert errors == ["AUTH_FAILED", "AUTH_FAILED"]
    assert issued == 2  # initial token plus one failed shared recovery issuance


def test_concurrent_auth_failures_share_one_actual_refresh_across_clients() -> None:
    issued = 0
    issued_lock = Lock()
    stale_barrier = Barrier(2)
    headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued
        if request.url.path == "/oauth2/token":
            with issued_lock:
                issued += 1
                number = issued
            return token_response(f"fake-shared-{number}")
        authorization = request.headers["authorization"]
        headers.append(authorization)
        if authorization == "Bearer fake-shared-1":
            stale_barrier.wait(timeout=5)
            return auth_failed()
        body = json.loads(request.content)
        return httpx.Response(200, json={"return_code": 0, "stk_cd": body["stk_cd"]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    first_auth = auth(http, "shared-concurrency")
    second_auth = auth(http, "shared-concurrency")
    assert first_auth.access_token() == "fake-shared-1"
    clients = [client(http, "unused-1", auth_client=first_auth),
               client(http, "unused-2", auth_client=second_auth)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(clients[index].quote, symbol, "ND")
                   for index, symbol in enumerate(("AAPL", "AMD"))]
    assert {future.result()["stk_cd"] for future in futures} == {"AAPL", "AMD"}
    assert issued == 2  # one initial issue plus exactly one shared recovery issue
    assert headers.count("Bearer fake-shared-1") == 2
    assert headers.count("Bearer fake-shared-2") == 2


def test_pagination_retries_only_failed_continuation_page_without_row_loss() -> None:
    issued = 0
    market = 0
    requests: list[tuple[str, str | None]] = []

    def row(day: str) -> dict[str, str]:
        return {"dt": day, "open_pric": "100", "high_pric": "100",
                "low_pric": "100", "cur_prc": "100", "acc_trde_qty": "10"}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued, market
        if request.url.path == "/oauth2/token":
            issued += 1
            return token_response(f"fake-page-{issued}")
        market += 1
        authorization = request.headers["authorization"]
        next_key = request.headers.get("next-key")
        requests.append((authorization, next_key))
        if next_key is None:
            return httpx.Response(200, headers={"cont-yn": "Y", "next-key": "page-2"},
                                  json={"return_code": 0, "result_list": [row("20260911")]})
        if authorization == "Bearer fake-page-1":
            return auth_failed()
        return httpx.Response(200, json={"return_code": 0,
                                         "result_list": [row("20260910")]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    rows = client(http, "pagination-recovery").daily_chart("AAPL", "ND", "20260913")
    assert [item["dt"] for item in rows] == ["20260911", "20260910"]
    assert requests == [
        ("Bearer fake-page-1", None),
        ("Bearer fake-page-1", "page-2"),
        ("Bearer fake-page-2", "page-2"),
    ]
    assert issued == 2 and market == 3


def test_minute_pagination_recovery_preserves_target_reached_contract() -> None:
    issued = market = 0

    def minute_row(day: str, clock: str) -> dict[str, str]:
        return {"bus_dt": day, "cntr_tm": f"{day}{clock}", "open_pric": "100",
                "high_pric": "100", "low_pric": "100", "cur_prc": "100",
                "trde_qty": "10"}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued, market
        if request.url.path == "/oauth2/token":
            issued += 1
            return token_response(f"fake-minute-{issued}")
        market += 1
        next_key = request.headers.get("next-key")
        if next_key is None:
            return httpx.Response(200, headers={"cont-yn": "Y", "next-key": "older"},
                                  json={"return_code": 0,
                                        "result_list": [minute_row("20260911", "190000")]})
        if request.headers["authorization"] == "Bearer fake-minute-1":
            return auth_failed()
        return httpx.Response(200, headers={"cont-yn": "Y", "next-key": "more"},
                              json={"return_code": 0,
                                    "result_list": [minute_row("20260911", "155900")]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    start = datetime(2026, 9, 11, 15, 59, tzinfo=ZoneInfo("America/New_York"))
    result = client(http, "minute-pagination").minute_chart("AAPL", "ND", start)
    assert result.pages_used == 2
    assert result.target_reached
    assert result.continuation_remaining
    assert len(result.rows) == 2
    assert issued == 2 and market == 3
