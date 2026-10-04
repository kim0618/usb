from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx


@dataclass
class RestTelemetry:
    requests: int = 0
    retries: int = 0
    rate_limits: int = 0


class RateLimiter:
    def __init__(self, requests_per_second: float = 8.0) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self._interval = 1.0 / requests_per_second
        self._next = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if now < self._next:
            time.sleep(self._next - now)
        self._next = max(now, self._next) + self._interval


class BybitPublicClient:
    BASE_URL = "https://api.bybit.com"

    def __init__(self, *, requests_per_second: float = 8.0, max_retries: int = 5, transport: httpx.BaseTransport | None = None, sleeper: Callable[[float], None] = time.sleep) -> None:
        self.limiter = RateLimiter(requests_per_second)
        self.max_retries = max_retries
        self.telemetry = RestTelemetry()
        self._sleeper = sleeper
        self._client = httpx.Client(base_url=self.BASE_URL, timeout=30, transport=transport, headers={"User-Agent": "usb-crypto-d2/1"})

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BybitPublicClient": return self
    def __exit__(self, *_: object) -> None: self.close()

    def get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            self.telemetry.requests += 1
            try:
                response = self._client.get(endpoint, params=params)
                if response.status_code == 429:
                    self.telemetry.rate_limits += 1
                    raise httpx.HTTPStatusError("rate limited", request=response.request, response=response)
                response.raise_for_status()
                payload = response.json()
                if payload.get("retCode") == 10006:
                    self.telemetry.rate_limits += 1
                    raise RuntimeError("Bybit rate limit")
                if payload.get("retCode") in {10000, 10016}:
                    raise RuntimeError(f"transient Bybit service error: {payload.get('retCode')}")
                if payload.get("retCode") != 0:
                    raise ValueError(f"Bybit error: {payload.get('retCode')} {payload.get('retMsg')}")
                return payload
            except (httpx.TransportError, httpx.HTTPStatusError, RuntimeError):
                if attempt == self.max_retries:
                    raise
                self.telemetry.retries += 1
                self._sleeper(min(30.0, (2**attempt) + random.random()))
        raise AssertionError("unreachable")
