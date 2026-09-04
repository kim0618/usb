"""Explicitly allowlisted Kiwoom market-data HTTP client."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import time
from typing import Any

import httpx

from app.core.exceptions import MarketDataError
from app.integrations.kiwoom.auth import KiwoomAuthClient
from app.integrations.kiwoom.rate_limit import KiwoomRateLimits, RequestRateLimiter
from app.integrations.kiwoom.timestamps import ET, minute_timestamp


MAX_MINUTE_HISTORY_PAGES = 50


@dataclass(frozen=True)
class KiwoomPage:
    body: dict[str, Any]
    continuation: bool
    next_key: str | None


@dataclass(frozen=True)
class MinuteHistoryCollection:
    rows: tuple[dict[str, Any], ...]
    pages_used: int
    target_reached: bool
    continuation_remaining: bool


class KiwoomMarketDataClient:
    ALLOWED_ENDPOINTS = frozenset(
        {
            ("usa10100", "/api/us/stkinfo"),
            ("usa10099", "/api/us/stkinfo"),
            ("usa20100", "/api/us/mrkcond"),
            ("usa20530", "/api/us/rkinfo"),
            ("usa20540", "/api/us/rkinfo"),
            ("usa20550", "/api/us/rkinfo"),
            ("usa20590", "/api/us/mrkcond"),
            ("usa06011", "/api/us/chart"),
            ("usa06012", "/api/us/chart"),
        }
    )

    def __init__(
        self,
        *,
        base_url: str,
        auth: KiwoomAuthClient,
        http: httpx.Client | None = None,
        timeout: float = 10.0,
        max_retries: int = 2,
        rate_limits: KiwoomRateLimits | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._http = http or httpx.Client(timeout=timeout)
        self._max_retries = max(0, max_retries)
        self._limits = rate_limits or KiwoomRateLimits()
        self._sleeper = sleeper
        self._request_counts: dict[str, int] = {}
        self._last_minute_collection: MinuteHistoryCollection | None = None

    @property
    def request_counts(self) -> dict[str, int]:
        """Return aggregate HTTP attempt counts without exposing payloads."""
        return dict(self._request_counts)

    @property
    def order_request_count(self) -> int:
        """Safety metric: order paths can never pass the allowlist."""
        return sum(count for path, count in self._request_counts.items() if path == "/api/us/ordr")

    @property
    def last_minute_collection(self) -> MinuteHistoryCollection | None:
        return self._last_minute_collection

    def request(
        self,
        api_id: str,
        path: str,
        body: dict[str, str],
        *,
        continuation: KiwoomPage | None = None,
    ) -> KiwoomPage:
        if (api_id, path) not in self.ALLOWED_ENDPOINTS:
            raise MarketDataError("ENDPOINT_BLOCKED", "Endpoint is not allowed in MARKET_DATA_ONLY mode")
        limiter = self._limits.chart if path == "/api/us/chart" else self._limits.query
        headers = {"authorization": f"Bearer {self._auth.access_token()}", "api-id": api_id}
        if continuation and continuation.continuation and continuation.next_key:
            headers.update({"cont-yn": "Y", "next-key": continuation.next_key})
        for attempt in range(self._max_retries + 1):
            limiter.acquire()
            self._request_counts[path] = self._request_counts.get(path, 0) + 1
            try:
                response = self._http.post(f"{self._base_url}{path}", headers=headers, json=body)
            except httpx.TimeoutException as exc:
                if attempt < self._max_retries:
                    self._sleeper(2**attempt)
                    continue
                raise MarketDataError("PROVIDER_TIMEOUT", "Kiwoom market-data request timed out") from exc
            except httpx.HTTPError as exc:
                if attempt < self._max_retries:
                    self._sleeper(2**attempt)
                    continue
                raise MarketDataError("MARKET_DATA_UNAVAILABLE", "Kiwoom market data is unavailable") from exc
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self._max_retries:
                    self._sleeper(2**attempt)
                    continue
                code = "RATE_LIMITED" if response.status_code == 429 else "MARKET_DATA_UNAVAILABLE"
                raise MarketDataError(code, "Kiwoom market-data request failed")
            if response.status_code in {401, 403}:
                raise MarketDataError("AUTH_FAILED", "Kiwoom authorization failed")
            try:
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise MarketDataError("MARKET_DATA_UNAVAILABLE", "Kiwoom returned an invalid response") from exc
            if not isinstance(payload, dict):
                raise MarketDataError("MARKET_DATA_UNAVAILABLE", "Kiwoom returned an invalid response")
            if payload.get("return_code") not in (None, 0):
                message = str(payload.get("return_msg", ""))
                code = "INVALID_SYMBOL" if "종목" in message else "MARKET_DATA_UNAVAILABLE"
                raise MarketDataError(code, "Kiwoom market-data query was rejected")
            return KiwoomPage(
                payload,
                response.headers.get("cont-yn", "N") == "Y",
                response.headers.get("next-key"),
            )
        raise AssertionError("bounded retry loop exhausted")

    def quote(self, symbol: str, exchange: str) -> dict[str, Any]:
        return self.request("usa20100", "/api/us/mrkcond", {"stex_tp": exchange, "stk_cd": symbol}).body

    def metadata(self, symbol: str, exchange: str) -> dict[str, Any]:
        return self.request("usa10100", "/api/us/stkinfo", {"stex_tp": exchange, "stk_cd": symbol}).body

    def symbol_list(self, exchange: str = "%") -> list[dict[str, Any]]:
        return self._collect("usa10099", "/api/us/stkinfo", {"stex_tp": exchange}, row_key="list")

    def transaction_amount_ranking(self) -> list[dict[str, Any]]:
        return self._collect("usa20540", "/api/us/rkinfo", self._ranking_body())

    def volume_ranking(self) -> list[dict[str, Any]]:
        return self._collect("usa20530", "/api/us/rkinfo", {**self._ranking_body(), "qry_tp": "0"})

    def market_cap_ranking(self) -> list[dict[str, Any]]:
        return self._collect("usa20550", "/api/us/rkinfo", self._ranking_body())

    @staticmethod
    def _ranking_body() -> dict[str, str]:
        return {"stex_tp": "0", "inds_cd": "", "stk_tp": "1", "trde_qty_tp": "0", "stk_cnd": "0", "pric_cnd": "0", "trde_prica_cnd": "0"}

    def daily_chart(self, symbol: str, exchange: str, start: str | None = None) -> list[dict[str, Any]]:
        body = {"stex_tp": exchange, "stk_cd": symbol, "upd_stkpc_tp": "1", "exrt_appl_tp": "0"}
        if start:
            body["strt_dt"] = start
        return self._collect("usa06012", "/api/us/chart", body)

    def minute_chart(
        self, symbol: str, exchange: str, start: datetime | None = None,
    ) -> MinuteHistoryCollection:
        if start is not None and (start.tzinfo is None or start.utcoffset() is None):
            raise ValueError("minute history start must be timezone-aware")
        body = {"stex_tp": exchange, "stk_cd": symbol, "tic_scope": "1", "upd_stkpc_tp": "0", "exrt_appl_tp": "0"}
        if start:
            body["strt_dt"] = start.astimezone(ET).strftime("%Y%m%d")
        rows: list[dict[str, Any]] = []
        page: KiwoomPage | None = None
        target_reached = False
        for pages_used in range(1, MAX_MINUTE_HISTORY_PAGES + 1):
            page = self.request("usa06011", "/api/us/chart", body, continuation=page)
            records = page.body.get("result_list", [])
            page_rows = [record for record in records if isinstance(record, dict)] if isinstance(records, list) else []
            rows.extend(page_rows)
            timestamps = []
            for row in page_rows:
                try:
                    timestamps.append(minute_timestamp(row))
                except (KeyError, TypeError, ValueError):
                    continue
            target_reached = start is not None and bool(timestamps) and min(timestamps) <= start.astimezone(ET)
            if target_reached or not page.continuation:
                result = MinuteHistoryCollection(tuple(rows), pages_used, target_reached, page.continuation)
                self._last_minute_collection = result
                return result
        assert page is not None
        result = MinuteHistoryCollection(tuple(rows), MAX_MINUTE_HISTORY_PAGES, False, page.continuation)
        self._last_minute_collection = result
        if page.continuation:
            raise MarketDataError(
                "INSUFFICIENT_HISTORY",
                "Kiwoom minute history was truncated before the requested start boundary",
            )
        return result

    def _collect(self, api_id: str, path: str, body: dict[str, str], max_pages: int = 10, row_key: str = "result_list") -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page: KiwoomPage | None = None
        for _ in range(max_pages):
            page = self.request(api_id, path, body, continuation=page)
            records = page.body.get(row_key, [])
            if isinstance(records, list):
                rows.extend(record for record in records if isinstance(record, dict))
            if not page.continuation:
                break
        return rows
