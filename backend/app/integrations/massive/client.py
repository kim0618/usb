"""Bounded, read-only Massive REST client for the historical data source spike.

Only the Stocks Custom Bars endpoint is reachable. Authentication uses the documented
``Authorization: Bearer`` header, so the key is never part of a URL, a followed
``next_url``, a log line, or an exception message. The free Stocks Basic plan allows
5 API calls per minute: every HTTP attempt, retries included, passes one limiter spaced
to that rate. Retries are bounded, and each failure class ends in a typed error.
"""

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
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
from app.integrations.massive.minute_bars import MalformedPayload, MinuteBar, epoch_ms, parse_bar


BASE_URL = "https://api.massive.com"
AGGREGATES_PATH_PREFIX = "/v2/aggs/ticker/"
BASIC_CALLS_PER_MINUTE = 5
PAGE_LIMIT = 50_000  # documented maximum; one ET date holds at most 1,440 minute bars
MAX_PAGES = 3
MAX_RETRIES = 2
RATE_LIMIT_BACKOFF_SECONDS = 60.0  # one full Basic-plan minute window
MAX_RETRY_AFTER_SECONDS = 60.0
TIMEOUT_SECONDS = 20.0
ACCEPTED_STATUSES = frozenset({"OK", "DELAYED"})
SECRET_QUERY_KEYS = frozenset({"apikey"})


class MassiveConfigurationError(ConfigurationError):
    """Massive is not usable as configured; raised before any HTTP request."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class MassiveError(MarketDataError):
    """Typed Massive failure: RATE_LIMITED, PROVIDER_TIMEOUT, NETWORK_ERROR,
    MALFORMED_PAYLOAD, NOT_AUTHORIZED, PROVIDER_ERROR, PAGINATION_LIMIT or
    UNTRUSTED_NEXT_URL. Messages carry no key, header, URL query, or payload text."""


@dataclass
class RequestAccounting:
    http_requests: int = 0
    pages: int = 0
    status_codes: Counter[int] = field(default_factory=Counter)
    transport_failures: Counter[str] = field(default_factory=Counter)
    provider_statuses: Counter[str] = field(default_factory=Counter)
    results_count: int = 0


@dataclass(frozen=True)
class AggregateFetch:
    bars: tuple[MinuteBar, ...]
    pages: tuple[dict[str, Any], ...]  # sanitized response bodies, for an optional raw save


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
        self.accounting = RequestAccounting()

    def __repr__(self) -> str:
        return f"MassiveAggregatesClient(base_url={self._base_url!r}, api_key=SecretStr('**********'))"

    @property
    def limiter(self) -> RequestRateLimiter:
        return self._limiter

    def minute_aggregates(self, symbol: str, start: datetime, end: datetime) -> AggregateFetch:
        """Every page of 1-minute bars in ``[start, end]``, or a typed failure.

        ``adjusted=false`` keeps raw traded prices, like the Kiwoom minute chart
        (``upd_stkpc_tp=0``). Follow-up pages use ``next_url`` as given, on the same
        origin and endpoint only, with any key-like query parameter removed.
        """
        for moment in (start, end):
            if moment.tzinfo is None or moment.utcoffset() is None:
                raise ValueError("aggregate range must be timezone-aware")
        if end <= start:
            raise ValueError("aggregate range end must be after start")
        url = (f"{self._base_url}{AGGREGATES_PATH_PREFIX}{symbol}/range/1/minute/"
               f"{epoch_ms(start)}/{epoch_ms(end)}")
        params: dict[str, str] | None = {"adjusted": "false", "sort": "asc", "limit": str(PAGE_LIMIT)}
        bars: list[MinuteBar] = []
        pages: list[dict[str, Any]] = []
        for _ in range(self._max_pages):
            body = self._get(url, params)
            self.accounting.pages += 1
            bars.extend(self._page_bars(body))
            pages.append(sanitize_body(body))
            next_url = body.get("next_url")
            if next_url is None or next_url == "":
                return AggregateFetch(tuple(bars), tuple(pages))
            url, params = self._trusted_next_url(next_url), None
        raise MassiveError("PAGINATION_LIMIT", f"next_url remained after {self._max_pages} pages")

    def _get(self, url: str, params: dict[str, str] | None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        for attempt in range(self._max_retries + 1):
            can_retry = attempt < self._max_retries
            self._limiter.acquire()
            self.accounting.http_requests += 1
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
            if status == 429:
                if can_retry:
                    self._sleeper(self._retry_after(response))
                    continue
                raise MassiveError("RATE_LIMITED", f"Massive rate limit persisted after "
                                                   f"{self._max_retries} retries (HTTP 429)")
            if status in {401, 403}:
                raise MassiveError("NOT_AUTHORIZED", f"Massive rejected the key or the plan does "
                                                     f"not cover this request (HTTP {status})")
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
    def _retry_after(response: httpx.Response) -> float:
        try:
            seconds = float(response.headers.get("retry-after", ""))
        except ValueError:
            return RATE_LIMIT_BACKOFF_SECONDS
        if not math.isfinite(seconds) or seconds < 0:
            return RATE_LIMIT_BACKOFF_SECONDS
        return min(seconds, MAX_RETRY_AFTER_SECONDS)

    def _payload(self, response: httpx.Response) -> dict[str, Any]:
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
    def _page_bars(body: Mapping[str, Any]) -> list[MinuteBar]:
        results = body.get("results")
        if results is None:
            return []  # an empty range may omit ``results``
        if not isinstance(results, list):
            raise MassiveError("MALFORMED_PAYLOAD", "Massive results is not a list")
        try:
            return [parse_bar(row) for row in results]
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
                         sleeper: Callable[[float], None] = time.sleep) -> MassiveAggregatesClient:
    """The spike client, or MISSING_API_KEY before any HTTP client or request exists."""
    key = settings.massive_api_key
    if key is None or not key.get_secret_value().strip():
        raise MassiveConfigurationError(
            "MISSING_API_KEY", "MASSIVE_API_KEY is not configured in the repository-root .env")
    return MassiveAggregatesClient(key, http=http, sleeper=sleeper)
