from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.core.config import Settings
from app.integrations.kiwoom.auth import KiwoomAuthClient
from app.integrations.kiwoom.client import KiwoomMarketDataClient
from app.integrations.kiwoom.mapping import map_metadata
from app.integrations.kiwoom.rate_limit import KiwoomRateLimits, RequestRateLimiter
from app.market.kiwoom import KiwoomMarketDataProvider
from app.market.universe import KiwoomUniverseSource
from app.scanner.domain import ExclusionReason
from app.scanner.scanner import QuantScanner

NOW = datetime(2026, 7, 2, 21, tzinfo=timezone.utc)


class NoopLimiter(RequestRateLimiter):
    def __init__(self) -> None:
        super().__init__(1)

    def acquire(self) -> None:
        pass


def _provider(handler: httpx.MockTransport) -> KiwoomMarketDataProvider:
    http = httpx.Client(transport=handler)
    limiter = NoopLimiter()
    limits = KiwoomRateLimits()
    limits.auth = limits.query = limits.chart = limits.realtime_subscription = limiter
    auth = KiwoomAuthClient(base_url="https://api.kiwoom.com", app_key="k", app_secret="s", http=http, limiter=limiter, clock=lambda: NOW)
    client = KiwoomMarketDataClient(base_url="https://api.kiwoom.com", auth=auth, http=http, rate_limits=limits)
    return KiwoomMarketDataProvider(client, clock=lambda: NOW)


def test_universe_prefilter_deduplicates_normalizes_exchange_and_primes_metadata() -> None:
    def response(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "t", "expires_dt": "20990101000000", "return_code": 0})
        if request.headers["api-id"] == "usa20540":
            return httpx.Response(200, json={"result_list": [
                {"stk_cd": "aapl", "stex_tp": "2", "stk_enm": "Apple", "trde_prica": "100"},
                {"stk_cd": "AAPL", "stex_tp": "ND", "stk_enm": "Apple"},
                {"stk_cd": "JPM", "stex_tp": "1", "stk_enm": "JPMorgan"},
            ], "return_code": 0})
        return httpx.Response(200, json={"result_list": [
            {"stk_cd": "AAPL", "stex_tp": "2", "mac": "3000000000000"},
            {"stk_cd": "JPM", "stex_tp": "1", "mac": "500000000000"},
        ], "return_code": 0})

    provider = _provider(httpx.MockTransport(response))
    source = KiwoomUniverseSource(provider)
    candidates = source.acquire(2)
    assert [(item.symbol, item.exchange_code) for item in candidates] == [("AAPL", "ND"), ("JPM", "NY")]
    source.prime_provider(candidates, NOW)
    metadata = provider.get_metadata(["AAPL", "JPM"], NOW)
    assert metadata["AAPL"].market_cap == 3_000_000_000_000
    assert provider.client.request_counts["/api/us/rkinfo"] == 2


def test_missing_market_cap_is_explicit_not_silently_zero() -> None:
    provider = _provider(httpx.MockTransport(lambda request: httpx.Response(200, json={"token": "t", "expires_dt": "20990101000000", "return_code": 0}) if request.url.path == "/oauth2/token" else httpx.Response(200, json={"result_list": [], "return_code": 0})))
    source = KiwoomUniverseSource(provider)
    candidate = source._optional_number(None)
    assert candidate is None
    metadata = map_metadata("AAPL", {"stex_tp": "ND", "stk_cd": "AAPL"}, NOW)
    assert metadata.market_cap is None


def test_real_mapping_runs_existing_quant_and_keeps_spy_separate() -> None:
    symbols = [f"S{i:02d}" for i in range(10)]
    dates = [date(2026, 6, 3) + timedelta(days=i) for i in range(29) if (date(2026, 6, 3) + timedelta(days=i)).weekday() < 5]

    def response(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "t", "expires_dt": "20990101000000", "return_code": 0})
        api_id = request.headers["api-id"]
        body = __import__("json").loads(request.content)
        if api_id == "usa20540":
            return httpx.Response(200, json={"result_list": [{"stk_cd": s, "stex_tp": "2", "stk_enm": s} for s in symbols], "return_code": 0})
        if api_id == "usa20550":
            return httpx.Response(200, json={"result_list": [{"stk_cd": s, "stex_tp": "2", "mac": str(1_000_000_000 + i)} for i, s in enumerate(symbols)], "return_code": 0})
        symbol = body["stk_cd"]
        offset = 0 if symbol == "SPY" else symbols.index(symbol) + 1
        rows = [{"dt": day.strftime("%Y%m%d"), "open_pric": str(100 + offset + i), "high_pric": str(102 + offset + i), "low_pric": str(99 + offset + i), "cur_prc": str(101 + offset + i), "acc_trde_qty": str(1_000_000 + offset * 10_000 + i * 1000)} for i, day in enumerate(dates)]
        return httpx.Response(200, json={"result_list": rows, "return_code": 0})

    provider = _provider(httpx.MockTransport(response))
    source = KiwoomUniverseSource(provider)
    candidates = source.acquire(10)
    source.prime_provider(candidates, NOW)
    scanner = QuantScanner(provider, provider)
    first = scanner.scan(symbols, trading_date=dates[-1], scan_as_of=NOW)
    chart_attempts = provider.client.request_counts["/api/us/chart"]
    second = scanner.scan(symbols, trading_date=dates[-1], scan_as_of=NOW)
    assert first.candidates == second.candidates
    assert len(first.candidates) == 10 and len(first.top8) == 8
    assert "SPY" not in {item.symbol for item in first.candidates}
    assert provider.client.order_request_count == 0
    assert provider.client.request_counts["/api/us/chart"] == chart_attempts


def test_scanner_safety_opt_in_defaults_off_and_broker_stays_simulation() -> None:
    settings = Settings(_env_file=None, market_data_provider="kiwoom", broker_provider="simulation")
    assert settings.run_kiwoom_real_scanner is False
    assert settings.kiwoom_mode == "market_data_only"
    with pytest.raises(Exception):
        Settings(_env_file=None, market_data_provider="kiwoom", broker_provider="paper")
