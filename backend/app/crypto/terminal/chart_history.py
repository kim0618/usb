"""Read-only Bybit chart history for the manual terminal.

This feed is presentation data only. It is separate from the paper execution feed and from
Binance LIVE account/order data. Native intervals are used when possible; 10m is folded from
native 5m bars on deterministic UTC epoch boundaries.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

import httpx

from ..symbols import resolve as resolve_symbol

REST_URL = "https://api.bybit.com"
SYMBOL = "BTCUSDT"
PROVIDER_PAGE_LIMIT = 1000
MAX_TARGET_BARS = 3000
CACHE_TTL_S = 15.0
CACHE_ENTRIES = 48


@dataclass(frozen=True)
class TimeframeSpec:
    label: str
    source_interval: str
    source_ms: int
    bucket_ms: int

    @property
    def source_bars_per_bucket(self) -> int:
        return self.bucket_ms // self.source_ms


SPECS = {
    "1m": TimeframeSpec("1m", "1", 60_000, 60_000),
    "10m": TimeframeSpec("10m", "5", 300_000, 600_000),
    "1h": TimeframeSpec("1h", "60", 3_600_000, 3_600_000),
    "4h": TimeframeSpec("4h", "240", 14_400_000, 14_400_000),
    "1d": TimeframeSpec("1d", "D", 86_400_000, 86_400_000),
}


def aggregate_rows(rows: list[dict[str, Any]], bucket_ms: int,
                   *, now_ms: int | None = None) -> list[dict[str, Any]]:
    """Deduplicate source timestamps and fold ascending OHLCV on UTC epoch boundaries."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    unique = {int(row["start_ms"]): row for row in rows}
    buckets: dict[int, dict[str, Any]] = {}
    for start_ms in sorted(unique):
        row = unique[start_ms]
        start = start_ms - start_ms % bucket_ms
        current = buckets.get(start)
        volume = Decimal(str(row.get("volume", "0")))
        if current is None:
            buckets[start] = {
                "start_ms": start, "open": str(row["open"]), "high": str(row["high"]),
                "low": str(row["low"]), "close": str(row["close"]), "volume": volume,
            }
            continue
        current["high"] = str(max(Decimal(current["high"]), Decimal(str(row["high"]))))
        current["low"] = str(min(Decimal(current["low"]), Decimal(str(row["low"]))))
        current["close"] = str(row["close"])
        current["volume"] += volume
    result = []
    for start in sorted(buckets):
        row = buckets[start]
        row["volume"] = str(row["volume"])
        row["confirmed"] = start + bucket_ms <= now
        result.append(row)
    return result


class BybitChartHistory:
    """Small in-process response cache over paged public kline reads."""

    def __init__(self, request: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
                 *, clock: Callable[[], float] = time.monotonic) -> None:
        self._request = request or self._http_request
        self._clock = clock
        #: Keyed by symbol first. A cache keyed on (timeframe, before, limit) alone would
        #: serve BTCUSDT's 1m bars to an ETHUSDT request made within the TTL, which is the one
        #: way a chart can show the wrong instrument while every label says the right one.
        self._cache: OrderedDict[tuple[str, str, int | None, int],
                                 tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _http_request(path: str, params: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(base_url=REST_URL, timeout=20,
                          headers={"User-Agent": "usb-crypto-chart-v2/1"}) as client:
            response = client.get(path, params=params)
            response.raise_for_status()
            payload = response.json()
        if payload.get("retCode") != 0:
            raise RuntimeError(f"Bybit kline failed: {payload.get('retCode')} {payload.get('retMsg')}")
        return payload

    def _source_rows(self, spec: TimeframeSpec, needed: int,
                     before_ms: int | None, symbol: str) -> tuple[list[dict[str, Any]], bool]:
        rows: dict[int, dict[str, Any]] = {}
        cursor = before_ms
        exhausted = False
        while len(rows) < needed:
            page_limit = min(PROVIDER_PAGE_LIMIT, needed - len(rows))
            params: dict[str, Any] = {"category": "linear", "symbol": symbol,
                                      "interval": spec.source_interval, "limit": page_limit}
            if cursor is not None:
                params["end"] = cursor - 1
            payload = self._request("/v5/market/kline", params)
            page = payload.get("result", {}).get("list", [])
            if not page:
                exhausted = True
                break
            oldest = None
            for raw in page:
                start = int(raw[0])
                if before_ms is not None and start >= before_ms:
                    continue
                rows[start] = {"start_ms": start, "open": raw[1], "high": raw[2],
                               "low": raw[3], "close": raw[4], "volume": raw[5]}
                oldest = start if oldest is None else min(oldest, start)
            if oldest is None or len(page) < page_limit:
                exhausted = True
                break
            cursor = oldest
        return [rows[key] for key in sorted(rows)], exhausted

    def get(self, timeframe: str, limit: int, before_ms: int | None = None,
            symbol: str | None = None) -> dict[str, Any]:
        """Paged display history for one instrument.

        `symbol` defaults to the module's default, so every existing caller is unchanged. It is
        resolved against the same whitelist the LIVE path uses rather than passed to Bybit
        verbatim: this is a public read, but an unvalidated symbol would still let a query
        parameter name any instrument Bybit lists and put it on a screen labelled otherwise.
        """
        if timeframe not in SPECS:
            raise ValueError(f"unsupported timeframe: {timeframe}")
        if not 1 <= limit <= MAX_TARGET_BARS:
            raise ValueError(f"limit must be in [1, {MAX_TARGET_BARS}]")
        instrument = resolve_symbol(symbol)
        spec = SPECS[timeframe]
        normalized_before = (before_ms - before_ms % spec.bucket_ms) if before_ms is not None else None
        key = (instrument, timeframe, normalized_before, limit)
        now = self._clock()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and now - cached[0] < CACHE_TTL_S:
                self._cache.move_to_end(key)
                return cached[1]

            needed = limit * spec.source_bars_per_bucket + 2
            source, exhausted = self._source_rows(spec, needed, normalized_before, instrument)
            candles = aggregate_rows(source, spec.bucket_ms)
            if normalized_before is not None:
                candles = [row for row in candles if row["start_ms"] < normalized_before]
            candles = candles[-limit:]
            body = {
                "symbol": instrument,
                "timeframe": timeframe, "bucket_ms": spec.bucket_ms,
                "source": "BYBIT_PUBLIC_KLINE", "source_interval": spec.source_interval,
                "bars": candles, "has_more": not exhausted and len(candles) == limit,
                "next_before_ms": candles[0]["start_ms"] if candles else normalized_before,
            }
            self._cache[key] = (now, body)
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_ENTRIES:
                self._cache.popitem(last=False)
            return body
