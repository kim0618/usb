"""Explicitly allowlisted Kiwoom market-data HTTP client."""

from collections.abc import Callable
from dataclasses import dataclass
import time
from typing import Any

import httpx

from app.core.exceptions import MarketDataError
from app.integrations.kiwoom.auth import KiwoomAuthClient
from app.integrations.kiwoom.rate_limit import KiwoomRateLimits, RequestRateLimiter


@dataclass(frozen=True)
class KiwoomPage:
    body: dict[str, Any]
    continuation: bool
    next_key: str | None


class KiwoomMarketDataClient:
    ALLOWED_ENDPOINTS = frozenset(
        {
            ("usa10100", "/api/us/stkinfo"),
            ("usa20100", "/api/us/mrkcond"),
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

    def daily_chart(self, symbol: str, exchange: str, start: str | None = None) -> list[dict[str, Any]]:
        body = {"stex_tp": exchange, "stk_cd": symbol, "upd_stkpc_tp": "1", "exrt_appl_tp": "0"}
        if start:
            body["strt_dt"] = start
        return self._collect("usa06012", "/api/us/chart", body)

    def minute_chart(self, symbol: str, exchange: str, start: str | None = None) -> list[dict[str, Any]]:
        body = {"stex_tp": exchange, "stk_cd": symbol, "tic_scope": "1", "upd_stkpc_tp": "0", "exrt_appl_tp": "0"}
        if start:
            body["strt_dt"] = start
        return self._collect("usa06011", "/api/us/chart", body)

    def _collect(self, api_id: str, path: str, body: dict[str, str], max_pages: int = 10) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page: KiwoomPage | None = None
        for _ in range(max_pages):
            page = self.request(api_id, path, body, continuation=page)
            records = page.body.get("result_list", [])
            if isinstance(records, list):
                rows.extend(record for record in records if isinstance(record, dict))
            if not page.continuation:
                break
        return rows
