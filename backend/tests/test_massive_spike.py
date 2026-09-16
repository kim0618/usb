"""Massive historical data source spike: no real key and no network in any test."""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import PROJECT_ROOT, Settings
from app.dev import run_massive_spike
from app.integrations.kiwoom.rate_limit import RequestRateLimiter
from app.integrations.massive.client import (
    BASIC_CALLS_PER_MINUTE, MAX_PAGES, MAX_RETRIES, MAX_RETRY_AFTER_SECONDS, PAGE_LIMIT,
    RATE_LIMIT_BACKOFF_SECONDS, MassiveAggregatesClient, MassiveConfigurationError, MassiveError,
    build_massive_client,
)
from app.integrations.massive.minute_bars import (
    EPOCH, MalformedPayload, SessionPart, classify, latest_completed_session,
    latest_completed_sessions, parse_bar, parse_timestamp_ms, request_range,
)
from app.integrations.massive.validation import validate
from app.market.calendar import MarketCalendar


ET = ZoneInfo("America/New_York")
UTC = timezone.utc
SENTINEL = "unit-test-massive-key-not-a-credential"
CALENDAR = MarketCalendar("America/New_York")
DAY = date(2026, 9, 14)
WINDOW = CALENDAR.session(DAY)
assert WINDOW is not None
START, END = request_range(WINDOW)
RUN_AT = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)  # 14:00 ET: 2026-09-15 is in progress
NEXT = "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/minute/1/2?cursor=page2"


def et(hour: int, minute: int, second: int = 0, day: date = DAY) -> datetime:
    return datetime.combine(day, time(hour, minute, second), tzinfo=ET)


def ms(moment: datetime) -> int:
    return (moment - EPOCH) // timedelta(milliseconds=1)


def row(moment: datetime, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {"t": ms(moment), "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5,
                                 "v": 1000, "vw": 100.2, "n": 12}
    values.update(overrides)
    return values


def rows_between(start: datetime, end: datetime, skip: tuple[datetime, ...] = ()) -> list[dict[str, object]]:
    rows, moment = [], start
    while moment < end:
        if moment not in skip:
            rows.append(row(moment))
        moment += timedelta(minutes=1)
    return rows


def full_day_rows() -> list[dict[str, object]]:
    return rows_between(et(4, 0), et(20, 0))


def body(rows: list[object], **extra: object) -> dict[str, object]:
    return {"ticker": "AAPL", "status": "OK", "adjusted": False, "queryCount": len(rows),
            "resultsCount": len(rows), "results": rows, "request_id": "req-test", **extra}


Step = Callable[[httpx.Request], httpx.Response]


def respond(status: int = 200, payload: object = None, *, text: str | None = None,
            headers: dict[str, str] | None = None) -> Step:
    def factory(_: httpx.Request) -> httpx.Response:
        if text is not None:
            return httpx.Response(status, text=text, headers=headers)
        return httpx.Response(status, json={} if payload is None else payload, headers=headers)
    return factory


def raise_(error_type: type[httpx.TransportError]) -> Step:
    def factory(request: httpx.Request) -> httpx.Response:
        raise error_type("simulated transport failure", request=request)
    return factory


class Transport:
    """Serves steps in order; the last step repeats."""

    def __init__(self, *steps: Step) -> None:
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        return step(request)

    def http(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def unlimited() -> RequestRateLimiter:
    return RequestRateLimiter(1_000_000.0, clock=lambda: 0.0, sleeper=lambda _: None)


def make_client(transport: Transport, sleeps: list[float] | None = None,
                **kwargs: object) -> MassiveAggregatesClient:
    return MassiveAggregatesClient(
        SecretStr(SENTINEL), http=transport.http(), limiter=unlimited(),
        sleeper=sleeps.append if sleeps is not None else (lambda _: None), **kwargs)  # type: ignore[arg-type]


def keyed_settings() -> Settings:
    return Settings(_env_file=None, massive_api_key=SENTINEL)


def run_cli(transport: Transport, *argv: str, clock: datetime = RUN_AT, **kwargs: object) -> int:
    return run_massive_spike.main(list(argv), settings_factory=keyed_settings, http=transport.http(),
                                  sleeper=lambda _: None, clock=lambda: clock, **kwargs)  # type: ignore[arg-type]


def assert_no_secret(*texts: str) -> None:
    for text in texts:
        assert SENTINEL not in text


# Settings and secret setup contract

def test_settings_massive_key_is_optional_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    assert Settings(_env_file=None).massive_api_key is None
    monkeypatch.setenv("MASSIVE_API_KEY", SENTINEL)
    settings = Settings(_env_file=None)
    assert isinstance(settings.massive_api_key, SecretStr)
    assert settings.massive_api_key.get_secret_value() == SENTINEL
    assert_no_secret(repr(settings), str(settings), str(settings.model_dump()))


def test_env_example_declares_blank_massive_key() -> None:
    lines = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "MASSIVE_API_KEY=" in lines


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_key_stops_before_any_http_request(key: str | None, capsys: pytest.CaptureFixture[str]) -> None:
    transport = Transport(respond(200, body([])))
    with pytest.raises(SystemExit) as stopped:
        run_massive_spike.main([], settings_factory=lambda: Settings(_env_file=None, massive_api_key=key),
                               http=transport.http(), clock=lambda: RUN_AT)
    assert "MISSING_API_KEY" in str(stopped.value)
    assert transport.requests == []
    assert "http_requests=0" in capsys.readouterr().out


def test_builder_refuses_missing_key_with_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with pytest.raises(MassiveConfigurationError) as failed:
        build_massive_client(Settings(_env_file=None))
    assert failed.value.code == "MISSING_API_KEY"


def test_default_client_uses_basic_plan_rate_and_hides_key() -> None:
    client = build_massive_client(keyed_settings(), http=Transport(respond(200, body([]))).http())
    assert client.limiter.requests_per_second == pytest.approx(BASIC_CALLS_PER_MINUTE / 60.0)
    assert_no_secret(repr(client))


# Request shape

def test_request_uses_bearer_header_and_documented_endpoint() -> None:
    transport = Transport(respond(200, body(full_day_rows())))
    fetch = make_client(transport).minute_aggregates("AAPL", START, END)
    request = transport.requests[0]
    assert (request.url.scheme, request.url.host) == ("https", "api.massive.com")
    assert request.url.path == f"/v2/aggs/ticker/AAPL/range/1/minute/{ms(START)}/{ms(END)}"
    assert dict(request.url.params) == {"adjusted": "false", "sort": "asc", "limit": str(PAGE_LIMIT)}
    assert request.headers["authorization"] == f"Bearer {SENTINEL}"
    assert_no_secret(str(request.url))
    assert len(fetch.bars) == 960


# Timestamps and sessions

def test_timestamp_is_utc_ms_bar_start_and_converts_to_et_across_dst() -> None:
    assert parse_timestamp_ms(0) == datetime(1970, 1, 1, tzinfo=UTC)
    summer = parse_bar(row(datetime(2026, 9, 14, 13, 30, tzinfo=UTC)))
    assert summer.bar_start.utcoffset() == timedelta(0)
    assert summer.bar_start_et == et(9, 30)
    assert summer.bar_start_et.utcoffset() == timedelta(hours=-4)
    assert summer.bar_end - summer.bar_start == timedelta(minutes=1)
    winter = parse_timestamp_ms(ms(datetime(2026, 3, 6, 14, 30, tzinfo=UTC))).astimezone(ET)
    after_switch = parse_timestamp_ms(ms(datetime(2026, 3, 9, 13, 30, tzinfo=UTC))).astimezone(ET)
    assert (winter.hour, winter.minute, winter.utcoffset()) == (9, 30, timedelta(hours=-5))
    assert (after_switch.hour, after_switch.minute, after_switch.utcoffset()) == (9, 30, timedelta(hours=-4))


@pytest.mark.parametrize("value", ["1789392600000", 1.7e12, True, -1, None])
def test_timestamp_rejects_anything_but_integer_ms(value: object) -> None:
    with pytest.raises(MalformedPayload):
        parse_timestamp_ms(value)


@pytest.mark.parametrize(("clock", "part"), [
    ((3, 59), SessionPart.OUTSIDE), ((4, 0), SessionPart.PREMARKET), ((9, 29), SessionPart.PREMARKET),
    ((9, 30), SessionPart.REGULAR), ((15, 59), SessionPart.REGULAR), ((16, 0), SessionPart.POSTMARKET),
    ((19, 59), SessionPart.POSTMARKET), ((20, 0), SessionPart.OUTSIDE),
])
def test_session_classification_follows_xnys_calendar(clock: tuple[int, int], part: SessionPart) -> None:
    assert classify(et(*clock), WINDOW) is part


def test_early_close_moves_postmarket_to_the_calendar_close() -> None:
    day = date(2025, 11, 28)
    window = CALENDAR.session(day)
    assert window is not None and window.is_early_close
    assert classify(et(12, 59, day=day), window) is SessionPart.REGULAR
    assert classify(et(13, 0, day=day), window) is SessionPart.POSTMARKET


@pytest.mark.parametrize(("now_et", "expected"), [
    (datetime(2026, 9, 15, 5, 0), date(2026, 9, 14)),    # premarket in progress
    (datetime(2026, 9, 15, 14, 0), date(2026, 9, 14)),   # regular session in progress
    (datetime(2026, 9, 15, 19, 59), date(2026, 9, 14)),  # postmarket in progress
    (datetime(2026, 9, 15, 20, 0), date(2026, 9, 15)),   # extended hours ended
    (datetime(2026, 9, 19, 12, 0), date(2026, 9, 18)),   # Saturday
    (datetime(2026, 9, 8, 3, 0), date(2026, 9, 4)),      # before premarket, after Labor Day
])
def test_target_is_the_latest_fully_completed_session(now_et: datetime, expected: date) -> None:
    window = latest_completed_session(CALENDAR, now_et.replace(tzinfo=ET))
    assert window.session_date == expected


def test_target_requires_aware_clock() -> None:
    with pytest.raises(ValueError):
        latest_completed_session(CALENDAR, datetime(2026, 9, 15, 12, 0))


def test_cli_never_requests_an_incomplete_session() -> None:
    transport = Transport(respond(200, body([])))
    assert run_cli(transport, clock=et(19, 59, day=date(2026, 9, 15))) == 0
    assert transport.requests[0].url.path.endswith(f"/{ms(START)}/{ms(END)}")


# Validation

def bars(rows: list[dict[str, object]]) -> list:
    return [parse_bar(item) for item in rows]


def test_complete_session_validates_clean() -> None:
    result = validate(bars(full_day_rows()), WINDOW)
    assert result.total_rows == 960
    assert result.session_rows == {SessionPart.PREMARKET: 330, SessionPart.REGULAR: 390,
                                   SessionPart.POSTMARKET: 240, SessionPart.OUTSIDE: 0}
    assert result.first_et == et(4, 0) and result.last_et == et(19, 59)
    assert result.first_premarket_et == et(4, 0)
    assert result.regular_open_row and result.last_regular_row
    assert result.rows_at_or_after_close == 240
    assert result.missing_regular_minutes == ()
    assert result.premarket_data_present and result.premarket_ohlc_present and result.premarket_volume_present
    assert result.verdict == "CLEAN"


def test_duplicate_and_non_monotonic_timestamps_are_detected() -> None:
    rows = full_day_rows()
    rows.insert(10, dict(rows[10]))
    rows[100], rows[101] = rows[101], rows[100]
    result = validate(bars(rows), WINDOW)
    assert result.duplicate_timestamps == 1
    assert result.non_monotonic_timestamps == 1
    assert result.verdict == "ANOMALIES:duplicate_timestamps,non_monotonic_timestamps"


def test_ohlc_null_negative_and_zero_volume_are_counted() -> None:
    rows = full_day_rows()
    rows[0] = row(et(4, 0), h=98.0)   # high below open, close and low
    rows[1] = row(et(4, 1), l=102.0)  # low above open, close and high
    rows[2] = row(et(4, 2), l=0.0)    # non-positive price
    rows[3] = row(et(4, 3), o=None)
    rows[4] = row(et(4, 4), v=-5)
    rows[5] = row(et(4, 5), v=0)
    del rows[6]["v"]
    result = validate(bars(rows), WINDOW)
    assert result.ohlc_violations == 3
    assert result.null_ohlcv_rows == 2
    assert result.negative_volume_rows == 1
    assert result.zero_volume_rows == 1
    assert result.premarket_ohlc_rows == 329
    assert result.verdict.startswith("ANOMALIES:ohlc_invariant_violations,null_ohlcv_rows,negative_volume_rows")


def test_missing_regular_minutes_and_absent_premarket_are_reported() -> None:
    rows = rows_between(et(9, 30), et(20, 0), skip=(et(10, 15),))
    result = validate(bars(rows), WINDOW)
    assert result.missing_regular_minutes == (et(10, 15),)
    assert not result.premarket_data_present
    assert not result.premarket_ohlc_present and not result.premarket_volume_present
    assert result.first_premarket_et is None
    assert result.verdict == "CLEAN"  # omitted minutes are documented no-trade behaviour


def test_missing_open_and_last_regular_minute_rows() -> None:
    rows = rows_between(et(4, 0), et(20, 0), skip=(et(9, 30), et(15, 59)))
    result = validate(bars(rows), WINDOW)
    assert not result.regular_open_row and not result.last_regular_row
    assert result.missing_regular_minutes == (et(9, 30), et(15, 59))


def test_unaligned_and_off_date_rows_are_anomalies() -> None:
    assert validate(bars([row(et(10, 0, 30))]), WINDOW).unaligned_timestamps == 1
    off_date = validate(bars([row(et(10, 0, day=date(2026, 9, 15)))]), WINDOW)
    assert off_date.off_date_rows == 1
    assert off_date.session_rows[SessionPart.OUTSIDE] == 1


def test_empty_result_is_no_data() -> None:
    result = validate([], WINDOW)
    assert result.verdict == "NO_DATA"
    assert len(result.missing_regular_minutes) == 390


# Fail-closed payloads and bounded retries

@pytest.mark.parametrize("step", [
    respond(200, text="<html>not json</html>"),
    respond(200, [1, 2]),
    respond(200, {"results": []}),
    respond(200, body([]) | {"results": {"t": 1}}),
    respond(200, body([{"o": 1.0}])),
    respond(200, body(["not an object"])),
    respond(200, body([row(et(9, 30), t="1789392600000")])),
    respond(200, body([row(et(9, 30), c="100.5")])),
    respond(200, body([row(et(9, 30), v=True)])),
    respond(200, body([], next_url=123)),
])
def test_malformed_payload_fails_closed_without_retry(step: Step) -> None:
    transport = Transport(step)
    client = make_client(transport)
    with pytest.raises(MassiveError) as failed:
        client.minute_aggregates("AAPL", START, END)
    assert failed.value.code == "MALFORMED_PAYLOAD"
    assert client.accounting.http_requests == 1
    assert_no_secret(str(failed.value), repr(failed.value))


def test_null_ohlcv_is_data_not_malformed() -> None:
    fetch = make_client(Transport(respond(200, body([row(et(9, 30), o=None)])))).minute_aggregates(
        "AAPL", START, END)
    assert fetch.bars[0].has_null_ohlcv


@pytest.mark.parametrize(("status", "accepted"), [("DELAYED", True), ("ERROR", False)])
def test_provider_status_is_checked(status: str, accepted: bool) -> None:
    client = make_client(Transport(respond(200, body([row(et(9, 30))], status=status))))
    if accepted:
        assert len(client.minute_aggregates("AAPL", START, END).bars) == 1
    else:
        with pytest.raises(MassiveError) as failed:
            client.minute_aggregates("AAPL", START, END)
        assert failed.value.code == "PROVIDER_ERROR"
    assert client.accounting.provider_statuses == {status: 1}


@pytest.mark.parametrize("status", [401, 403])
def test_auth_or_subscription_rejection_is_not_retried(status: int) -> None:
    sleeps: list[float] = []
    client = make_client(Transport(respond(status, {"status": "NOT_AUTHORIZED", "message": SENTINEL})), sleeps)
    with pytest.raises(MassiveError) as failed:
        client.minute_aggregates("AAPL", START, END)
    assert failed.value.code == "NOT_AUTHORIZED"
    assert client.accounting.http_requests == 1 and sleeps == []
    assert_no_secret(str(failed.value))


def test_client_error_is_provider_error_without_retry() -> None:
    client = make_client(Transport(respond(404, {"status": "NOT_FOUND"})))
    with pytest.raises(MassiveError) as failed:
        client.minute_aggregates("AAPL", START, END)
    assert failed.value.code == "PROVIDER_ERROR"
    assert client.accounting.http_requests == 1


def test_server_error_retries_bounded_then_fails() -> None:
    sleeps: list[float] = []
    client = make_client(Transport(respond(502)), sleeps)
    with pytest.raises(MassiveError) as failed:
        client.minute_aggregates("AAPL", START, END)
    assert failed.value.code == "PROVIDER_ERROR"
    assert client.accounting.http_requests == MAX_RETRIES + 1
    assert sleeps == [1, 2]


def test_429_is_retried_bounded_then_rate_limited() -> None:
    sleeps: list[float] = []
    client = make_client(Transport(respond(429, {"status": "ERROR"})), sleeps)
    with pytest.raises(MassiveError) as failed:
        client.minute_aggregates("AAPL", START, END)
    assert failed.value.code == "RATE_LIMITED"
    assert client.accounting.http_requests == MAX_RETRIES + 1
    assert client.accounting.status_codes == {429: MAX_RETRIES + 1}
    assert sleeps == [RATE_LIMIT_BACKOFF_SECONDS] * MAX_RETRIES


def test_429_honours_retry_after_with_a_cap_then_recovers() -> None:
    sleeps: list[float] = []
    transport = Transport(respond(429, headers={"Retry-After": "7"}),
                          respond(429, headers={"Retry-After": "9999"}),
                          respond(200, body([row(et(9, 30))])))
    client = make_client(transport, sleeps)
    assert len(client.minute_aggregates("AAPL", START, END).bars) == 1
    assert sleeps == [7.0, MAX_RETRY_AFTER_SECONDS]
    assert client.accounting.http_requests == 3


@pytest.mark.parametrize(("error", "code", "kind"), [
    (httpx.ReadTimeout, "PROVIDER_TIMEOUT", "timeout"),
    (httpx.ConnectError, "NETWORK_ERROR", "network"),
])
def test_transport_failures_retry_bounded(error: type[httpx.TransportError], code: str, kind: str) -> None:
    client = make_client(Transport(raise_(error)))
    with pytest.raises(MassiveError) as failed:
        client.minute_aggregates("AAPL", START, END)
    assert failed.value.code == code
    assert client.accounting.http_requests == MAX_RETRIES + 1
    assert client.accounting.transport_failures == {kind: MAX_RETRIES + 1}
    assert failed.value.__cause__ is None and failed.value.__suppress_context__


def test_basic_plan_throttle_spaces_requests_twelve_seconds() -> None:
    sleeps: list[float] = []
    limiter = RequestRateLimiter(BASIC_CALLS_PER_MINUTE / 60.0, clock=lambda: 0.0, sleeper=sleeps.append)
    transport = Transport(respond(200, body([], next_url=NEXT)), respond(200, body([])))
    client = MassiveAggregatesClient(SecretStr(SENTINEL), http=transport.http(), limiter=limiter,
                                     sleeper=lambda _: None)
    client.minute_aggregates("AAPL", START, END)
    assert sleeps == [pytest.approx(12.0)]


# Pagination

def test_pagination_follows_next_url_and_strips_any_key() -> None:
    rows = full_day_rows()
    transport = Transport(respond(200, body(rows[:500], next_url=NEXT + "&apiKey=LEAKED")),
                          respond(200, body(rows[500:])))
    client = make_client(transport)
    fetch = client.minute_aggregates("AAPL", START, END)
    assert len(fetch.bars) == 960
    assert client.accounting.pages == 2 and client.accounting.http_requests == 2
    second = transport.requests[1]
    assert second.url.params.get("cursor") == "page2"
    assert "LEAKED" not in str(second.url) and "apikey" not in str(second.url).lower()
    assert second.headers["authorization"] == f"Bearer {SENTINEL}"
    assert "LEAKED" not in json.dumps(fetch.pages)


def test_pagination_is_bounded() -> None:
    transport = Transport(respond(200, body([], next_url=NEXT)))
    client = make_client(transport)
    with pytest.raises(MassiveError) as failed:
        client.minute_aggregates("AAPL", START, END)
    assert failed.value.code == "PAGINATION_LIMIT"
    assert len(transport.requests) == MAX_PAGES


@pytest.mark.parametrize("next_url", [
    "https://evil.example/v2/aggs/ticker/AAPL/range/1/minute/1/2?cursor=x",
    "http://api.massive.com/v2/aggs/ticker/AAPL/range/1/minute/1/2?cursor=x",
    "https://api.massive.com/v3/trades/AAPL?cursor=x",
])
def test_untrusted_next_url_is_never_requested(next_url: str) -> None:
    transport = Transport(respond(200, body([], next_url=next_url)))
    with pytest.raises(MassiveError) as failed:
        make_client(transport).minute_aggregates("AAPL", START, END)
    assert failed.value.code == "UNTRUSTED_NEXT_URL"
    assert len(transport.requests) == 1


# CLI report and non-disclosure

def test_cli_reports_every_validation_item(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli(Transport(respond(200, body(full_day_rows())))) == 0
    out = capsys.readouterr().out.splitlines()
    for expected in (
        "target_trading_date=2026-09-14", "http_requests=1", "pagination_pages=1",
        "status_codes=200:1", "provider_status=OK:1", "total_rows=960",
        "first_timestamp_et=2026-09-14T04:00:00-04:00", "last_timestamp_et=2026-09-14T19:59:00-04:00",
        "premarket_rows=330", "regular_rows=390", "postmarket_rows=240",
        "first_premarket_timestamp_et=2026-09-14T04:00:00-04:00",
        "regular_open_row_0930=YES", "last_regular_row_1559=YES", "rows_at_or_after_close_1600=240",
        "duplicate_timestamps=0", "non_monotonic_timestamps=0", "ohlc_invariant_violations=0",
        "null_ohlcv_rows=0", "negative_volume_rows=0", "zero_volume_rows=0",
        "regular_missing_minutes=0", "data_quality=CLEAN",
    ):
        assert expected in out
    assert any(line.startswith("elapsed_seconds=") for line in out)
    assert any(line.startswith("premarket_volume_present=YES") for line in out)
    assert "timestamp_contract=bar_start (Massive docs: t is the start of the aggregate window)" in out


@pytest.mark.parametrize("step", [
    respond(403, {"status": "NOT_AUTHORIZED", "message": f"key {SENTINEL} rejected"}),
    respond(429, {"status": "ERROR"}),
    raise_(httpx.ReadTimeout),
    respond(200, text=f"garbage {SENTINEL}"),
    respond(200, body(full_day_rows())),
])
def test_cli_never_discloses_the_key(step: Step, capsys: pytest.CaptureFixture[str],
                                     caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    transport = Transport(step)
    code = run_cli(transport)
    out = capsys.readouterr()
    assert_no_secret(out.out, out.err, caplog.text, *(str(request.url) for request in transport.requests))
    if code:
        assert any(line.startswith("FAILED code=") for line in out.out.splitlines())


def test_save_raw_writes_sanitized_bodies_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rows = full_day_rows()
    transport = Transport(respond(200, body(rows[:10], next_url=NEXT + "&apiKey=LEAKED")),
                          respond(200, body(rows[10:])))
    assert run_cli(transport, "--save-raw", raw_dir=tmp_path) == 0
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert_no_secret(text, capsys.readouterr().out)
    assert "LEAKED" not in text and "authorization" not in text.lower()
    saved = json.loads(text)
    assert saved["target_trading_date"] == "2026-09-14" and len(saved["pages"]) == 2


def test_raw_scratch_dir_is_git_ignored() -> None:
    assert run_massive_spike.RAW_DIR.relative_to(PROJECT_ROOT).parts[:2] == ("data", "runtime")
    assert "data/runtime/*" in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


# Multi-symbol, multi-session validation

MULTI_RUN_AT = datetime(2026, 9, 15, 8, 40, tzinfo=UTC)  # 04:40 ET: 2026-09-15 is in progress
FIVE_SESSIONS = tuple(date(2026, 9, day) for day in (8, 9, 10, 11, 14))
THREE = ("AAPL", "AMD", "ORCL")


def session_router(failures: dict[tuple[str, date], int] | None = None) -> Step:
    """A full 04:00-20:00 ET day for whatever symbol and ET date the request names."""
    def factory(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.split("/")
        symbol, day = parts[4], parse_timestamp_ms(int(parts[8])).astimezone(ET).date()
        status = (failures or {}).get((symbol, day))
        if status is not None:
            return httpx.Response(status, json={"status": "NOT_AUTHORIZED"})
        return httpx.Response(200, json=body(rows_between(et(4, 0, day=day), et(20, 0, day=day))))
    return factory


def requested(transport: Transport) -> list[tuple[str, date]]:
    return [(request.url.path.split("/")[4],
             parse_timestamp_ms(int(request.url.path.split("/")[8])).astimezone(ET).date())
            for request in transport.requests]


@pytest.mark.parametrize(("now_et", "count", "expected"), [
    (datetime(2026, 9, 15, 4, 40), 5, FIVE_SESSIONS),
    (datetime(2026, 9, 9, 10, 0), 3, (date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 8))),
])
def test_latest_completed_sessions_oldest_first_skipping_holidays(
        now_et: datetime, count: int, expected: tuple[date, ...]) -> None:
    windows = latest_completed_sessions(CALENDAR, now_et.replace(tzinfo=ET), count)
    assert tuple(window.session_date for window in windows) == expected


def test_extended_hours_missing_minutes_are_reported_by_hour() -> None:
    assert validate(bars(full_day_rows()), WINDOW).premarket_missing_minutes == ()
    rows = rows_between(et(4, 0), et(20, 0), skip=(et(4, 5), et(5, 10), et(5, 11), et(19, 0)))
    result = validate(bars(rows), WINDOW)
    assert result.premarket_missing_minutes == (et(4, 5), et(5, 10), et(5, 11))
    assert result.postmarket_missing_minutes == (et(19, 0),)
    assert result.complete_regular and result.expected_regular_minutes == 390
    lines = run_massive_spike.validation_lines(result)
    assert "premarket_missing_minutes=3 by_hour_et=04:1,05:2" in lines
    assert "postmarket_missing_minutes=1 by_hour_et=19:1" in lines


def test_cli_validates_each_symbol_over_the_same_completed_sessions(capsys: pytest.CaptureFixture[str]) -> None:
    transport = Transport(session_router())
    assert run_cli(transport, "--symbols", *THREE, "--sessions", "5", clock=MULTI_RUN_AT) == 0
    assert requested(transport) == [(symbol, day) for symbol in THREE for day in FIVE_SESSIONS]
    out = capsys.readouterr().out.splitlines()
    assert "target_trading_dates=" + ",".join(map(str, FIVE_SESSIONS)) in out
    assert sum(line.startswith("=== symbol=") for line in out) == 15
    assert out.count("venue=NOT_IN_AGGREGATES_PAYLOAD") == 15
    for expected in (
        "total_symbol_sessions=15", "successful_sessions=15", "failed_sessions=0", "clean_sessions=15",
        "anomaly_sessions=0", "sessions_with_premarket=15", "sessions_with_premarket_volume=15",
        "sessions_with_0930=15", "sessions_with_1559=15", "sessions_with_390_regular_rows=15",
        "sessions_with_regular_missing_minutes=0", "http_requests_total=15", "pagination_pages_total=15",
        "status_codes_total=200:15", "failure_codes=none", "gate_G1_api_ok=PASS",
        "gate_G2_premarket_ohlc_volume=PASS", "gate_G3_0930_1559=PASS",
        "gate_G5_no_timestamp_ohlc_duplicate_errors=PASS",
    ):
        assert expected in out
    assert any(line.startswith("symbol_summary=ORCL sessions=5 failed=0 error_sessions=0 "
                               "avg_total_rows=960.0 avg_premarket_rows=330.0 avg_regular_rows=390.0")
               for line in out)
    assert_no_secret("\n".join(out))


def test_multi_run_passes_every_request_through_one_basic_plan_limiter() -> None:
    sleeps: list[float] = []
    transport = Transport(session_router())
    assert run_massive_spike.main(["--symbols", *THREE, "--sessions", "5"], settings_factory=keyed_settings,
                                  http=transport.http(), sleeper=sleeps.append,
                                  clock=lambda: MULTI_RUN_AT) == 0
    # The injected sleeper does not advance the clock, so waits accumulate 12 s per request.
    assert sleeps == pytest.approx([12.0 * n for n in range(1, 15)], abs=1.0)


def test_multi_run_counts_a_failed_session_and_keeps_going(capsys: pytest.CaptureFixture[str]) -> None:
    transport = Transport(session_router({("ORCL", date(2026, 9, 10)): 403}))
    assert run_cli(transport, "--symbols", *THREE, "--sessions", "5", clock=MULTI_RUN_AT) == 1
    assert len(transport.requests) == 15
    out = capsys.readouterr().out.splitlines()
    for expected in ("successful_sessions=14", "failed_sessions=1", "failure_codes=NOT_AUTHORIZED:1",
                     "gate_G1_api_ok=FAIL", "gate_G2_premarket_ohlc_volume=INCOMPLETE",
                     "gate_G3_0930_1559=INCOMPLETE"):
        assert expected in out
    assert any(line.startswith("FAILED code=NOT_AUTHORIZED") for line in out)
    assert any(line.startswith("symbol_summary=ORCL sessions=5 failed=1 error_sessions=1 ") for line in out)


def test_multi_run_gate_fails_on_an_anomalous_session(capsys: pytest.CaptureFixture[str]) -> None:
    def duplicated(request: httpx.Request) -> httpx.Response:
        response = session_router()(request)
        payload = response.json()
        payload["results"].append(payload["results"][0])
        return httpx.Response(200, json=payload)
    assert run_cli(Transport(duplicated), "--symbols", "AAPL", "--sessions", "2", clock=MULTI_RUN_AT) == 0
    out = capsys.readouterr().out.splitlines()
    assert "anomaly_sessions=2" in out
    assert "gate_G5_no_timestamp_ohlc_duplicate_errors=FAIL" in out


@pytest.mark.parametrize("argv", [
    ["--sessions", "6"], ["--sessions", "0"], ["--symbols", "AAPL", "AMD", "ORCL", "MSFT"],
    ["--symbol", "AAPL", "--symbols", "AMD"], ["--symbols", "AAPL", "aapl"],
])
def test_cli_refuses_scope_beyond_validation_bounds(argv: list[str]) -> None:
    transport = Transport(session_router())
    with pytest.raises(SystemExit) as stopped:
        run_cli(transport, *argv, clock=MULTI_RUN_AT)
    assert stopped.value.code == 2
    assert transport.requests == []
