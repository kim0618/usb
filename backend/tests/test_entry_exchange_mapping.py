"""An entry lookup must reach the venue its candidate actually lists on.

Production Run 5 approved ORCL, whose scanner snapshot stored NYSE, and the entry
runtime still queried it as a NASDAQ listing: Kiwoom answered INVALID_SYMBOL every
minute tick and the candidate never reached a strategy state. The exchange the
scanner stored is the authority, and these tests hold it all the way to the
outbound request body rather than to an intermediate object.
"""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.core.exceptions import MarketDataError
from app.integrations.kiwoom.auth import KiwoomAuthClient
from app.integrations.kiwoom.client import KiwoomMarketDataClient
from app.integrations.kiwoom.rate_limit import KiwoomRateLimits, RequestRateLimiter
from app.market.kiwoom import KiwoomMarketDataProvider
from app.market.universe import KiwoomUniverseSource
from app.services.entry_management_runtime import (
    ApprovedCandidate, EntryAction, EntryLifecycleService,
)
from app.strategy.lifecycle import OvernightSuitability, TrailingProfile
from tests.test_entry_management_runtime import DAY, OPEN, durable, seed_session  # noqa: F401

ET = ZoneInfo("America/New_York")
AS_OF = OPEN + timedelta(minutes=1)

# Run 5's approved set, with the venue each symbol is actually listed on.
LISTINGS = {"SPCX": "ND", "ORCL": "NY", "AMD": "ND", "AAPL": "ND", "META": "ND"}
CANONICAL = {"ND": "NASDAQ", "NY": "NYSE", "NA": "AMEX"}


class NoopLimiter(RequestRateLimiter):
    def __init__(self) -> None:
        super().__init__(1)

    def acquire(self) -> None:
        pass


class FakeKiwoom:
    """Kiwoom as it actually behaves: a symbol asked for on the wrong venue is unknown."""

    def __init__(self, listings: dict[str, str]) -> None:
        self.listings = listings
        self.queries: list[tuple[str, str, str]] = []  # (api id, symbol, exchange)

    def exchanges_for(self, symbol: str) -> list[str]:
        return [exchange for api, queried, exchange in self.queries if queried == symbol]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"token": "t", "expires_dt": "20990101000000",
                                             "return_code": 0})
        api = request.headers["api-id"]
        body = json.loads(request.content)
        symbol, exchange = body["stk_cd"], body["stex_tp"]
        self.queries.append((api, symbol, exchange))
        if self.listings.get(symbol) != exchange:
            return httpx.Response(200, json={"return_code": 3,
                                             "return_msg": "종목코드가 존재하지 않습니다"})
        if api == "usa06012":
            return httpx.Response(200, json={"return_code": 0, "result_list": [
                {"dt": (DAY - timedelta(days=offset)).strftime("%Y%m%d"), "open_pric": "100",
                 "high_pric": "101", "low_pric": "99", "cur_prc": "100",
                 "acc_trde_qty": "1000000"}
                for offset in (1, 4, 5, 6, 7)
            ]})
        if api == "usa06011":
            requested_date = body.get("strt_dt")
            if requested_date == (DAY - timedelta(days=1)).strftime("%Y%m%d"):
                return httpx.Response(200, json={"return_code": 0, "result_list": [
                    {"bus_dt": requested_date, "cntr_tm": "155900", "open_pric": "100",
                     "high_pric": "100", "low_pric": "100", "cur_prc": "100",
                     "trde_qty": "20000"},
                ]})
            return httpx.Response(200, json={"return_code": 0, "result_list": [
                {"bus_dt": DAY.strftime("%Y%m%d"), "cntr_tm": "080000", "open_pric": "105",
                 "high_pric": "105", "low_pric": "105", "cur_prc": "105", "trde_qty": "100000"},
                {"bus_dt": DAY.strftime("%Y%m%d"), "cntr_tm": "093000", "open_pric": "106",
                 "high_pric": "106", "low_pric": "106", "cur_prc": "106", "trde_qty": "20000"},
            ]})
        return httpx.Response(200, json={"return_code": 0, "stex_tp": exchange, "stk_cd": symbol,
                                         "stk_enm": symbol, "mac": "3000000000",
                                         "trd_susp_tp": "N"})


def provider_for(kiwoom: FakeKiwoom) -> KiwoomMarketDataProvider:
    http = httpx.Client(transport=httpx.MockTransport(kiwoom))
    limits = KiwoomRateLimits()
    limits.auth = limits.query = limits.chart = limits.realtime_subscription = NoopLimiter()
    auth = KiwoomAuthClient(base_url="https://api.kiwoom.com", app_key="k", app_secret="s",
                            http=http, limiter=NoopLimiter(), clock=lambda: AS_OF)
    client = KiwoomMarketDataClient(base_url="https://api.kiwoom.com", auth=auth, http=http,
                                    rate_limits=limits, sleeper=lambda _: None)
    return KiwoomMarketDataProvider(client, clock=lambda: AS_OF)


def approved(symbol: str, exchange: str, candidate_id: int = 1) -> ApprovedCandidate:
    return ApprovedCandidate(1, 1, candidate_id, symbol, exchange, TrailingProfile.WIDE,
                             OvernightSuitability.MEDIUM)


# --- Provider binding ------------------------------------------------------------------

@pytest.mark.parametrize("exchange, code", [("NASDAQ", "ND"), ("NYSE", "NY"), ("AMEX", "NA")])
def test_each_supported_listing_is_queried_on_its_own_venue(exchange: str, code: str) -> None:
    kiwoom = FakeKiwoom({"TEST": code})
    provider = provider_for(kiwoom)

    provider.bind_exchange("TEST", exchange)
    bars = provider.get_daily_bars(["TEST"], DAY - timedelta(days=45), DAY - timedelta(days=1))

    assert kiwoom.exchanges_for("TEST") == [code]
    assert bars, "a correctly routed symbol returns its history"


def test_one_run_routes_nasdaq_nyse_and_amex_symbols_independently() -> None:
    kiwoom = FakeKiwoom({"NAS": "ND", "NYS": "NY", "AMX": "NA"})
    provider = provider_for(kiwoom)

    for symbol, exchange in (("NAS", "NASDAQ"), ("NYS", "NYSE"), ("AMX", "AMEX")):
        provider.bind_exchange(symbol, exchange)
    provider.get_daily_bars(["NAS", "NYS", "AMX"], DAY - timedelta(days=45),
                            DAY - timedelta(days=1))

    assert kiwoom.exchanges_for("NAS") == ["ND"]
    assert kiwoom.exchanges_for("NYS") == ["NY"]
    assert kiwoom.exchanges_for("AMX") == ["NA"]
    assert provider.provider_failures == {}


def test_the_venue_follows_the_binding_not_the_ticker() -> None:
    """The same ticker resolves by its stored authority, so no symbol is special-cased."""
    listed_ny = FakeKiwoom({"ORCL": "NY"})
    on_nyse = provider_for(listed_ny)
    on_nyse.bind_exchange("ORCL", "NYSE")
    on_nyse.get_daily_bars(["ORCL"], DAY - timedelta(days=45), DAY - timedelta(days=1))

    listed_nd = FakeKiwoom({"ORCL": "ND"})
    on_nasdaq = provider_for(listed_nd)
    on_nasdaq.bind_exchange("ORCL", "NASDAQ")
    on_nasdaq.get_daily_bars(["ORCL"], DAY - timedelta(days=45), DAY - timedelta(days=1))

    assert listed_ny.exchanges_for("ORCL") == ["NY"]
    assert listed_nd.exchanges_for("ORCL") == ["ND"]


@pytest.mark.parametrize("unsupported", ["", "UNKNOWN", "TSX", "LSE", "NASDAQ GS", "XNYS"])
def test_an_unsupported_exchange_fails_closed_before_any_request(unsupported: str) -> None:
    kiwoom = FakeKiwoom({"TEST": "ND"})
    provider = provider_for(kiwoom)

    with pytest.raises(MarketDataError) as error:
        provider.bind_exchange("TEST", unsupported)

    assert error.value.code == "UNSUPPORTED_EXCHANGE"
    assert kiwoom.queries == [], "no lookup may be attempted on a guessed venue"


def test_a_bound_venue_replaces_the_default_for_that_symbol_only() -> None:
    kiwoom = FakeKiwoom({"NYS": "NY", "NAS": "ND"})
    provider = provider_for(kiwoom)

    provider.bind_exchange("NYS", "NYSE")
    provider.get_daily_bars(["NYS", "NAS"], DAY - timedelta(days=45), DAY - timedelta(days=1))

    assert kiwoom.exchanges_for("NYS") == ["NY"]
    assert kiwoom.exchanges_for("NAS") == ["ND"], "an unbound symbol keeps the configured default"


# --- Morning Scanner regression ---------------------------------------------------------

def test_universe_priming_still_binds_ranking_venues() -> None:
    kiwoom = FakeKiwoom({"AAPL": "ND", "JPM": "NY"})

    def ranking(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/us/rkinfo":
            return httpx.Response(200, json={"return_code": 0, "result_list": [
                {"stk_cd": "AAPL", "stex_tp": "2", "stk_enm": "Apple", "mac": "3000000000000"},
                {"stk_cd": "JPM", "stex_tp": "1", "stk_enm": "JPMorgan", "mac": "500000000000"},
            ]})
        return kiwoom(request)

    http = httpx.Client(transport=httpx.MockTransport(ranking))
    limits = KiwoomRateLimits()
    limits.auth = limits.query = limits.chart = limits.realtime_subscription = NoopLimiter()
    auth = KiwoomAuthClient(base_url="https://api.kiwoom.com", app_key="k", app_secret="s",
                            http=http, limiter=NoopLimiter(), clock=lambda: AS_OF)
    provider = KiwoomMarketDataProvider(KiwoomMarketDataClient(
        base_url="https://api.kiwoom.com", auth=auth, http=http, rate_limits=limits,
        sleeper=lambda _: None), clock=lambda: AS_OF)
    source = KiwoomUniverseSource(provider)

    candidates = source.acquire(2)
    source.prime_provider(candidates, AS_OF)
    provider.get_daily_bars(["AAPL", "JPM"], DAY - timedelta(days=45), DAY - timedelta(days=1))

    assert [(item.symbol, item.exchange_code) for item in candidates] == [("AAPL", "ND"),
                                                                         ("JPM", "NY")]
    assert kiwoom.exchanges_for("JPM") == ["NY"]
    assert kiwoom.exchanges_for("AAPL") == ["ND"]


# --- Entry authority --------------------------------------------------------------------

def test_the_scanner_snapshot_is_the_entry_exchange_authority(durable) -> None:  # noqa: F811
    runtime, factory = durable
    with factory() as session:
        seed_session(session, date(2024, 6, 17), tuple(LISTINGS),
                     {symbol: CANONICAL[code] for symbol, code in LISTINGS.items()})

    candidates = EntryLifecycleService(runtime).approved_candidates_for_entry_session(DAY)

    assert {item.symbol: item.exchange for item in candidates} == {
        "SPCX": "NASDAQ", "ORCL": "NYSE", "AMD": "NASDAQ", "AAPL": "NASDAQ", "META": "NASDAQ"}


def test_run_five_approvals_each_reach_their_own_venue(durable) -> None:  # noqa: F811
    """SPCX, ORCL, AMD, AAPL and META stay five candidates, and only ORCL queries NYSE."""
    runtime, factory = durable
    with factory() as session:
        seed_session(session, date(2024, 6, 17), tuple(LISTINGS),
                     {symbol: CANONICAL[code] for symbol, code in LISTINGS.items()})
    service = EntryLifecycleService(runtime)
    kiwoom = FakeKiwoom(LISTINGS)
    provider = provider_for(kiwoom)

    candidates = service.approved_candidates_for_entry_session(DAY)
    outcomes = [service.evaluate(item, provider, as_of=AS_OF) for item in candidates]

    assert len(candidates) == len(outcomes) == 5
    assert {symbol: set(kiwoom.exchanges_for(symbol)) for symbol in LISTINGS} == {
        "SPCX": {"ND"}, "ORCL": {"NY"}, "AMD": {"ND"}, "AAPL": {"ND"}, "META": {"ND"}}
    assert provider.provider_failures == {}, "no candidate was rejected as an unknown symbol"
    assert all(outcome.action is not EntryAction.SKIPPED for outcome in outcomes)


def test_an_nyse_candidate_is_never_queried_as_a_nasdaq_listing(durable) -> None:  # noqa: F811
    """The exact production defect: ORCL on ND answers INVALID_SYMBOL every tick."""
    runtime, _ = durable
    service = EntryLifecycleService(runtime)
    kiwoom = FakeKiwoom({"ORCL": "NY"})
    provider = provider_for(kiwoom)

    outcome = service.evaluate(approved("ORCL", "NYSE"), provider, as_of=AS_OF)

    assert "ND" not in kiwoom.exchanges_for("ORCL")
    assert kiwoom.exchanges_for("ORCL") == ["NY", "NY", "NY"], (
        "current minute, previous close minute, and daily history all route to NYSE")
    assert outcome.state is not None, "a routed candidate reaches a durable strategy state"


def test_an_entry_candidate_without_a_known_exchange_never_guesses(durable) -> None:  # noqa: F811
    runtime, _ = durable
    kiwoom = FakeKiwoom({"ORCL": "NY"})
    provider = provider_for(kiwoom)

    with pytest.raises(MarketDataError) as error:
        EntryLifecycleService(runtime).evaluate(approved("ORCL", ""), provider, as_of=AS_OF)

    assert error.value.code == "UNSUPPORTED_EXCHANGE"
    assert kiwoom.queries == []
