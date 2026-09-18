"""Bounded, read-only Massive REST client for the historical data source spike.

Only the Stocks Custom Bars endpoint, the Splits reference endpoint (a read-only
corporate-action check), and the dated reference endpoints a research universe is
selected from (grouped daily, tickers, ticker details) are reachable. Authentication uses the documented
``Authorization: Bearer`` header, so the key is never part of a URL, a followed
``next_url``, a log line, or an exception message. The free Stocks Basic plan allows
5 API calls per minute: every HTTP attempt, retries included, passes one limiter spaced
to that rate. Retries are bounded, and each failure class ends in a typed error.
"""

from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import math
import re
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from pydantic import SecretStr

from app.core.config import Settings
from app.core.exceptions import ConfigurationError, MarketDataError
from app.integrations.kiwoom.rate_limit import RequestRateLimiter
from app.integrations.massive.daily_bars import DailyAggregateBar, parse_daily_bar
from app.integrations.massive.minute_bars import MalformedPayload, MinuteBar, epoch_ms, parse_bar


BASE_URL = "https://api.massive.com"
AGGREGATES_PATH_PREFIX = "/v2/aggs/ticker/"
SPLITS_PATH = "/v3/reference/splits"
GROUPED_DAILY_PATH_PREFIX = "/v2/aggs/grouped/locale/us/market/stocks/"
REFERENCE_TICKERS_PATH = "/v3/reference/tickers"
REFERENCE_TICKERS_PAGE_LIMIT = 1_000
REFERENCE_TICKERS_MAX_PAGES = 12  # ~5,000 common stocks is 6 pages; bounded, never open-ended
BASIC_CALLS_PER_MINUTE = 5
PAGE_LIMIT = 50_000  # documented maximum; one ET date holds at most 1,440 minute bars
MAX_PAGES = 3  # single-session spike: one ET date never needs more than one page
MAX_PAGES_HARD_CEILING = 16  # long range: ~1 year of every-minute bars is 11 pages; never unbounded
SPLITS_PAGE_LIMIT = 1_000
MAX_RETRIES = 2
RATE_LIMIT_BACKOFF_SECONDS = 60.0  # one full Basic-plan minute window
MAX_RETRY_AFTER_SECONDS = 60.0
TIMEOUT_SECONDS = 20.0
ACCEPTED_STATUSES = frozenset({"OK", "DELAYED"})
SECRET_QUERY_KEYS = frozenset({"apikey"})
# Stocks Basic answers a request that reaches into a timeframe the plan does not cover
# with HTTP 403 and a message about the plan, not about the key. That is a data-coverage
# limit, not a credential failure, so it gets its own code instead of NOT_AUTHORIZED.
PLAN_TIMEFRAME_PATTERN = re.compile(r"plan[^.]{0,80}?(time\s?frame|timeframe)", re.IGNORECASE)


class MassiveConfigurationError(ConfigurationError):
    """Massive is not usable as configured; raised before any HTTP request."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class MassiveError(MarketDataError):
    """Typed Massive failure: RATE_LIMITED, PROVIDER_TIMEOUT, NETWORK_ERROR,
    MALFORMED_PAYLOAD, NOT_AUTHORIZED, PLAN_TIMEFRAME_NOT_INCLUDED, PROVIDER_ERROR,
    PAGINATION_LIMIT, PAGINATION_LOOP, DUPLICATE_PAGE or UNTRUSTED_NEXT_URL. Messages
    carry no key, header, URL query, or payload text."""


@dataclass
class RequestAccounting:
    http_requests: int = 0
    pages: int = 0
    status_codes: Counter[int] = field(default_factory=Counter)
    transport_failures: Counter[str] = field(default_factory=Counter)
    provider_statuses: Counter[str] = field(default_factory=Counter)
    results_count: int = 0
    response_bytes: int = 0  # raw HTTP body bytes, every attempt with a body
    retries: int = 0  # attempts beyond the first, per request


@dataclass(frozen=True)
class PageSignature:
    """What identifies a page's content without keeping its rows."""

    first_bar_start: datetime
    last_bar_start: datetime
    rows: int


@dataclass(frozen=True)
class AggregatePage:
    """One page of an aggregates response: its bars and its sanitized body."""

    number: int  # 1-based position in this fetch
    bars: tuple[MinuteBar | DailyAggregateBar, ...]
    body: dict[str, Any]  # sanitized; ``results`` removed unless the caller kept it


@dataclass(frozen=True)
class AggregateFetch:
    bars: tuple[MinuteBar, ...]
    pages: tuple[dict[str, Any], ...]  # sanitized response bodies, for an optional raw save


def long_range_page_cap(start: datetime, end: datetime, *, limit: int = PAGE_LIMIT) -> int:
    """A bounded page cap derived from the range: every minute of every calendar day in
    ``[start, end]`` as one bar, divided by the page limit, plus one page of margin.

    Trading never fills every minute, so the true page count is well below this. A range
    whose cap would exceed ``MAX_PAGES_HARD_CEILING`` is refused rather than paginated
    without bound.
    """
    if end <= start:
        raise ValueError("aggregate range end must be after start")
    minutes = math.ceil((end - start) / timedelta(minutes=1))
    cap = math.ceil(minutes / limit) + 1
    if cap > MAX_PAGES_HARD_CEILING:
        raise ValueError(f"range needs a page cap of {cap}, above the hard ceiling "
                         f"{MAX_PAGES_HARD_CEILING}; split the range")
    return cap


def daily_page_cap(start: date, end: date, *, limit: int = PAGE_LIMIT) -> int:
    """A bounded page cap for a daily range: one row per calendar day, plus one page.

    A session never produces more than one daily bar, so calendar days is already an
    over-count. The same hard ceiling applies as for the minute path.
    """
    if end < start:
        raise ValueError("daily range end must not be before start")
    days = (end - start).days + 1
    cap = math.ceil(days / limit) + 1
    if cap > MAX_PAGES_HARD_CEILING:
        raise ValueError(f"range needs a page cap of {cap}, above the hard ceiling "
                         f"{MAX_PAGES_HARD_CEILING}; split the range")
    return cap


def strip_secret_query(url: str) -> str:
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key.lower() not in SECRET_QUERY_KEYS]
    return urlunsplit(parts._replace(query=urlencode(query)))


def sanitize_body(body: Mapping[str, Any]) -> dict[str, Any]:
    clean = dict(body)
    if isinstance(clean.get("next_url"), str):
        clean["next_url"] = strip_secret_query(clean["next_url"])
    return clean


def _status_token(value: str) -> str:
    return re.sub(r"[^A-Z0-9_]", "", value.upper())[:24] or "UNPRINTABLE"


class MassiveAggregatesClient:
    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str = BASE_URL,
        http: httpx.Client | None = None,
        timeout: float = TIMEOUT_SECONDS,
        max_retries: int = MAX_RETRIES,
        max_pages: int = MAX_PAGES,
        limiter: RequestRateLimiter | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        page_observer: Callable[[bytes], None] | None = None,
    ) -> None:
        if not api_key.get_secret_value().strip():
            raise MassiveConfigurationError("MISSING_API_KEY", "MASSIVE_API_KEY is not configured")
        parts = urlsplit(base_url)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._origin = (parts.scheme, parts.netloc)
        self._http = http or httpx.Client(timeout=timeout)
        self._max_retries = max(0, max_retries)
        self._max_pages = max(1, max_pages)
        self._sleeper = sleeper
        self._limiter = limiter or RequestRateLimiter(BASIC_CALLS_PER_MINUTE / 60.0, sleeper=sleeper)
        # Called with each accepted response body before it is parsed, so a caller can
        # inspect or archive the provider's own bytes. It never sees a header or a URL.
        self._page_observer = page_observer
        self.accounting = RequestAccounting()

    def __repr__(self) -> str:
        return f"MassiveAggregatesClient(base_url={self._base_url!r}, api_key=SecretStr('**********'))"

    @property
    def limiter(self) -> RequestRateLimiter:
        return self._limiter

    def minute_aggregates(self, symbol: str, start: datetime, end: datetime, *,
                          max_pages: int | None = None, keep_pages: bool = True) -> AggregateFetch:
        """Every page of 1-minute bars in ``[start, end]``, or a typed failure.

        ``adjusted=false`` keeps raw traded prices, like the Kiwoom minute chart
        (``upd_stkpc_tp=0``). Follow-up pages use ``next_url`` as given, on the same
        origin and endpoint only, with any key-like query parameter removed.

        ``max_pages`` overrides the client cap for one call (a long range passes
        ``long_range_page_cap``); it is never unbounded. A ``next_url`` seen before is a
        PAGINATION_LOOP; a page whose rows repeat or overlap an earlier page is a
        DUPLICATE_PAGE. With ``keep_pages=False`` the returned ``pages`` hold each page's
        body without ``results``, so the rows exist once, as bars.

        Every row of the range is held in memory at once. A collection that must not do
        that iterates ``iter_minute_aggregate_pages`` instead.
        """
        bars: list[MinuteBar] = []
        pages: list[dict[str, Any]] = []
        for page in self.iter_minute_aggregate_pages(symbol, start, end, max_pages=max_pages,
                                                     keep_results=keep_pages):
            bars.extend(page.bars)
            pages.append(page.body)
        return AggregateFetch(tuple(bars), tuple(pages))

    def iter_minute_aggregate_pages(self, symbol: str, start: datetime, end: datetime, *,
                                    max_pages: int | None = None,
                                    keep_results: bool = False) -> Iterator[AggregatePage]:
        """The same fetch, one page at a time, so a long range never has to be held whole.

        The pagination guards are identical to ``minute_aggregates``: a bounded page cap,
        a loop check on ``next_url``, and a duplicate or overlapping page check against
        the pages already yielded. Arguments are validated before the first request, so a
        bad range fails at the call rather than at the first iteration.
        """
        for moment in (start, end):
            if moment.tzinfo is None or moment.utcoffset() is None:
                raise ValueError("aggregate range must be timezone-aware")
        if end <= start:
            raise ValueError("aggregate range end must be after start")
        url = (f"{self._base_url}{AGGREGATES_PATH_PREFIX}{symbol}/range/1/minute/"
               f"{epoch_ms(start)}/{epoch_ms(end)}")
        params: dict[str, str] = {"adjusted": "false", "sort": "asc", "limit": str(PAGE_LIMIT)}
        page_cap = (self._max_pages if max_pages is None
                    else max(1, min(max_pages, MAX_PAGES_HARD_CEILING)))
        return self._paginate(url, params, page_cap, keep_results)

    def _paginate(self, url: str, params: dict[str, str] | None, page_cap: int,
                  keep_results: bool,
                  parser: Callable[[Any], Any] = parse_bar) -> Iterator[AggregatePage]:
        seen_urls = {url}
        signatures: list[PageSignature] = []
        for number in range(1, page_cap + 1):
            body = self._get(url, params)
            page_bars = self._page_bars(body, parser)
            self._check_page_not_duplicate(page_bars, signatures)
            self.accounting.pages += 1  # a page counts once its rows parsed and proved new
            clean = sanitize_body(body)
            if not keep_results:
                clean.pop("results", None)
            next_url = body.get("next_url")
            del body
            yield AggregatePage(number, tuple(page_bars), clean)
            if next_url is None or next_url == "":
                return
            url, params = self._trusted_next_url(next_url), None
            if url in seen_urls:
                raise MassiveError("PAGINATION_LOOP", "next_url repeats a page already requested")
            seen_urls.add(url)
        raise MassiveError("PAGINATION_LIMIT", f"next_url remained after {page_cap} pages")

    @staticmethod
    def _check_page_not_duplicate(page_bars: Sequence[Any], signatures: list[PageSignature]) -> None:
        if not page_bars:
            return
        signature = PageSignature(page_bars[0].bar_start, page_bars[-1].bar_start, len(page_bars))
        if signature in signatures:
            raise MassiveError("DUPLICATE_PAGE", "a page repeated the rows of an earlier page")
        if signatures and signature.first_bar_start <= signatures[-1].last_bar_start:
            raise MassiveError("DUPLICATE_PAGE", "a page overlaps the rows of the previous page")
        signatures.append(signature)

    def daily_aggregates(self, symbol: str, start: date, end: date, *,
                         max_pages: int | None = None) -> tuple[DailyAggregateBar, ...]:
        """Every daily bar whose session falls in ``[start, end]``, or a typed failure.

        Same endpoint, same origin, same key handling and the same pagination guards as
        the minute path; only the timespan differs. ``adjusted=false`` is fixed here, so
        a caller can never ask this client for a back-adjusted series by accident.
        """
        if end < start:
            raise ValueError("daily range end must not be before start")
        url = (f"{self._base_url}{AGGREGATES_PATH_PREFIX}{symbol}/range/1/day/"
               f"{start.isoformat()}/{end.isoformat()}")
        params: dict[str, str] = {"adjusted": "false", "sort": "asc", "limit": str(PAGE_LIMIT)}
        cap = daily_page_cap(start, end) if max_pages is None else max(
            1, min(max_pages, MAX_PAGES_HARD_CEILING))
        bars: list[DailyAggregateBar] = []
        for page in self._paginate(url, params, cap, keep_results=False, parser=parse_daily_bar):
            bars.extend(page.bars)  # type: ignore[arg-type]
        return tuple(bars)

    def reference_splits(self, symbol: str, start: date, end: date) -> tuple[dict[str, Any], ...]:
        """One bounded, read-only page of stock splits executed in ``[start, end]``.

        Only used to look for corporate actions inside a historical range. A ``next_url``
        is reported by the caller as a limitation, never followed.
        """
        if end < start:
            raise ValueError("split range end must not be before start")
        params = {"ticker": symbol, "execution_date.gte": start.isoformat(),
                  "execution_date.lte": end.isoformat(), "limit": str(SPLITS_PAGE_LIMIT), "sort": "execution_date"}
        body = self._get(f"{self._base_url}{SPLITS_PATH}", params)
        self.accounting.pages += 1
        results = body.get("results")
        if results is None:
            return ()
        if not isinstance(results, list) or not all(isinstance(item, Mapping) for item in results):
            raise MassiveError("MALFORMED_PAYLOAD", "Massive splits results is not a list of objects")
        return tuple(dict(item) for item in results)

    def grouped_daily(self, session: date) -> dict[str, Any]:
        """Every US stock's unadjusted daily bar for one session, as one sanitized body.

        Read-only reference for a dated liquidity ranking. ``adjusted=false`` is fixed for
        the same reason as ``daily_aggregates``: a later split must not reach back into a
        ranking made before it.
        """
        url = f"{self._base_url}{GROUPED_DAILY_PATH_PREFIX}{session.isoformat()}"
        body = self._get(url, {"adjusted": "false"})
        self.accounting.pages += 1
        results = body.get("results")
        if results is not None and (not isinstance(results, list) or not all(
                isinstance(item, Mapping) for item in results)):
            raise MassiveError("MALFORMED_PAYLOAD", "Massive grouped results is not a list of objects")
        return sanitize_body(body)

    def reference_tickers(self, as_of: date, *, security_type: str,
                          max_pages: int = REFERENCE_TICKERS_MAX_PAGES) -> tuple[dict[str, Any], ...]:
        """Every active stock ticker of one type as the reference stood on ``as_of``.

        Follows ``next_url`` on the same origin and path only, never beyond ``max_pages``.
        Returned bodies are sanitized and carry no ``next_url``.
        """
        url = f"{self._base_url}{REFERENCE_TICKERS_PATH}"
        params: dict[str, str] | None = {
            "market": "stocks", "type": security_type, "date": as_of.isoformat(),
            "active": "true", "limit": str(REFERENCE_TICKERS_PAGE_LIMIT), "sort": "ticker",
            "order": "asc"}
        seen = {url}
        pages: list[dict[str, Any]] = []
        for _ in range(max(1, max_pages)):
            body = self._get(url, params)
            self.accounting.pages += 1
            results = body.get("results")
            if results is not None and (not isinstance(results, list) or not all(
                    isinstance(item, Mapping) for item in results)):
                raise MassiveError("MALFORMED_PAYLOAD", "Massive tickers results is not a list")
            next_url = body.get("next_url")
            clean = sanitize_body(body)
            clean.pop("next_url", None)
            pages.append(clean)
            if next_url is None or next_url == "":
                return tuple(pages)
            url, params = self._trusted_reference_url(next_url, REFERENCE_TICKERS_PATH), None
            if url in seen:
                raise MassiveError("PAGINATION_LOOP", "next_url repeats a page already requested")
            seen.add(url)
        raise MassiveError("PAGINATION_LIMIT", f"next_url remained after {max_pages} pages")

    def ticker_details(self, symbol: str, as_of: date) -> dict[str, Any]:
        """One ticker's reference details as they stood on ``as_of`` (market cap, SIC, CIK)."""
        url = f"{self._base_url}{REFERENCE_TICKERS_PATH}/{symbol}"
        body = self._get(url, {"date": as_of.isoformat()})
        self.accounting.pages += 1
        results = body.get("results")
        if not isinstance(results, Mapping):
            raise MassiveError("MALFORMED_PAYLOAD", "Massive ticker details results is not an object")
        return sanitize_body(body)

    def _trusted_reference_url(self, next_url: Any, path: str) -> str:
        if not isinstance(next_url, str):
            raise MassiveError("MALFORMED_PAYLOAD", "Massive next_url is not a string")
        parts = urlsplit(next_url)
        if (parts.scheme, parts.netloc) != self._origin or parts.path != path:
            raise MassiveError("UNTRUSTED_NEXT_URL", "next_url does not point at the same reference endpoint")
        return strip_secret_query(next_url)

    def _get(self, url: str, params: dict[str, str] | None) -> dict[str, Any]:
        headers ={"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        for attempt in range(self._max_retries + 1):
            can_retry = attempt < self._max_retries
            self._limiter.acquire()
            self.accounting.http_requests += 1
            if attempt:
                self.accounting.retries += 1
            try:
                response = self._http.get(url, params=params, headers=headers)
            except httpx.TimeoutException:
                self.accounting.transport_failures["timeout"] += 1
                if can_retry:
                    self._sleeper(2**attempt)
                    continue
                raise MassiveError("PROVIDER_TIMEOUT", "Massive request timed out") from None
            except httpx.HTTPError:
                self.accounting.transport_failures["network"] += 1
                if can_retry:
                    self._sleeper(2**attempt)
                    continue
                raise MassiveError("NETWORK_ERROR", "Massive request failed in transport") from None
            status = response.status_code
            self.accounting.status_codes[status] += 1
            self.accounting.response_bytes += len(response.content)
            if status == 429:
                if can_retry:
                    self._sleeper(self._retry_after(response))
                    continue
                raise MassiveError("RATE_LIMITED", f"Massive rate limit persisted after "
                                                   f"{self._max_retries} retries (HTTP 429)")
            if status in {401, 403}:
                raise self._rejection(status, response)
            if status >= 500:
                if can_retry:
                    self._sleeper(2**attempt)
                    continue
                raise MassiveError("PROVIDER_ERROR", f"Massive server error (HTTP {status})")
            if status != 200:
                raise MassiveError("PROVIDER_ERROR", f"Massive rejected the request (HTTP {status})")
            return self._payload(response)
        raise AssertionError("bounded retry loop exhausted")

    @staticmethod
    def _rejection(status: int, response: httpx.Response) -> MassiveError:
        """Separate a plan timeframe limit from a credential failure, without quoting the body."""
        if status == 403 and PLAN_TIMEFRAME_PATTERN.search(response.text[:500]):
            return MassiveError(
                "PLAN_TIMEFRAME_NOT_INCLUDED",
                "Massive plan does not include this data timeframe (HTTP 403); the request "
                "reached into a period the plan does not cover")
        return MassiveError("NOT_AUTHORIZED", f"Massive rejected the key or the plan does "
                                              f"not cover this request (HTTP {status})")

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        try:
            seconds = float(response.headers.get("retry-after", ""))
        except ValueError:
            return RATE_LIMIT_BACKOFF_SECONDS
        if not math.isfinite(seconds) or seconds < 0:
            return RATE_LIMIT_BACKOFF_SECONDS
        return min(seconds, MAX_RETRY_AFTER_SECONDS)

    def _payload(self, response: httpx.Response) -> dict[str, Any]:
        if self._page_observer is not None:
            self._page_observer(response.content)
        try:
            body = response.json()
        except ValueError:
            raise MassiveError("MALFORMED_PAYLOAD", "Massive returned a non-JSON body") from None
        if not isinstance(body, dict):
            raise MassiveError("MALFORMED_PAYLOAD", "Massive body is not a JSON object")
        status = body.get("status")
        if not isinstance(status, str):
            raise MassiveError("MALFORMED_PAYLOAD", "Massive body has no status")
        token = _status_token(status)
        self.accounting.provider_statuses[token] += 1
        if status not in ACCEPTED_STATUSES:
            raise MassiveError("PROVIDER_ERROR", f"Massive payload status {token}")
        count = body.get("resultsCount")
        if isinstance(count, int) and not isinstance(count, bool):
            self.accounting.results_count += count
        return body

    @staticmethod
    def _page_bars(body: Mapping[str, Any],
                   parser: Callable[[Any], Any] = parse_bar) -> list[Any]:
        results = body.get("results")
        if results is None:
            return []  # an empty range may omit ``results``
        if not isinstance(results, list):
            raise MassiveError("MALFORMED_PAYLOAD", "Massive results is not a list")
        try:
            return [parser(row) for row in results]
        except MalformedPayload as exc:
            raise MassiveError("MALFORMED_PAYLOAD", str(exc)) from None

    def _trusted_next_url(self, next_url: Any) -> str:
        if not isinstance(next_url, str):
            raise MassiveError("MALFORMED_PAYLOAD", "Massive next_url is not a string")
        parts = urlsplit(next_url)
        if (parts.scheme, parts.netloc) != self._origin or not parts.path.startswith(AGGREGATES_PATH_PREFIX):
            raise MassiveError("UNTRUSTED_NEXT_URL", "next_url does not point at the Massive aggregates endpoint")
        return strip_secret_query(next_url)


def build_massive_client(settings: Settings, *, http: httpx.Client | None = None,
                         sleeper: Callable[[float], None] = time.sleep,
                         page_observer: Callable[[bytes], None] | None = None,
                         ) -> MassiveAggregatesClient:
    """The spike client, or MISSING_API_KEY before any HTTP client or request exists."""
    key = settings.massive_api_key
    if key is None or not key.get_secret_value().strip():
        raise MassiveConfigurationError(
            "MISSING_API_KEY", "MASSIVE_API_KEY is not configured in the repository-root .env")
    return MassiveAggregatesClient(key, http=http, sleeper=sleeper, page_observer=page_observer)
