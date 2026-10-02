"""Where the live C1 inputs come from, and why from there.

The study read four series. Three are Bybit public endpoints and this module reaches them through
the *same* client and the *same* `SERIES` specs that built the D2 historical files the study was
run on (`app.crypto.rest.BybitPublicClient`, `app.crypto.models.SERIES`), so the live series and
the researched series have one provenance rather than two that happen to agree:

    kline_1m           /v5/market/kline              the traded instrument and the target
    open_interest_5m   /v5/market/open-interest      condition 2
    funding            /v5/market/funding/history    the cost side of the shadow trade

The fourth is Binance BTCUSDT **spot**, the denominator of S1. There was no reader for it in this
package: the existing Binance client is the signed USDT-M futures account client, and the study
read spot from Binance's public data archive. So `BinanceSpotClient` below is new, and it is
public, unsigned and read-only - it cannot reach an account path because it holds no key.

Everything here reads. Nothing in this module can place, cancel or size an order.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Sequence

import httpx

from ..historical import iter_window
from ..models import SERIES
from ..rest import BybitPublicClient, RateLimiter
from .contract import FIVE_MIN_MS, MINUTE_MS, SPOT_SYMBOL
from .grid import Bar

BINANCE_SPOT_URL = "https://api.binance.com"
SPOT_PAGE_LIMIT = 1000
BYBIT_KLINE_PAGE = 1000
BYBIT_OI_PAGE = 200


def _float(value: Any) -> float:
    return float(value)


class BinanceSpotClient:
    """Public, unsigned Binance spot klines. One endpoint, GET only.

    Binance caps `limit` at 1000 and returns ascending rows; the caller walks the window forward
    by the last timestamp it actually received rather than by an assumed page span, so a venue gap
    cannot put the cursor past data that does exist.
    """

    def __init__(self, *, requests_per_second: float = 6.0, max_retries: int = 4,
                 transport: httpx.BaseTransport | None = None,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.limiter = RateLimiter(requests_per_second)
        self.max_retries = max_retries
        self._sleeper = sleeper
        self._client = httpx.Client(base_url=BINANCE_SPOT_URL, timeout=30, transport=transport,
                                    headers={"User-Agent": "usb-crypto-c1-signal/1"})

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BinanceSpotClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, params: dict[str, Any]) -> list[list[Any]]:
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                response = self._client.get("/api/v3/klines", params=params)
                if response.status_code in (418, 429):
                    raise httpx.HTTPStatusError("rate limited", request=response.request,
                                                response=response)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise RuntimeError(f"unexpected Binance spot payload: {type(payload).__name__}")
                return payload
            except (httpx.TransportError, httpx.HTTPStatusError, RuntimeError):
                if attempt == self.max_retries:
                    raise
                self._sleeper(min(30.0, 2 ** attempt))
        raise AssertionError("unreachable")

    def closes(self, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        """(open_time, close) for every finished 1m spot bar in [start, end)."""
        rows: dict[int, float] = {}
        cursor = start_ms
        while cursor < end_ms:
            page = self._get({"symbol": SPOT_SYMBOL, "interval": "1m", "startTime": cursor,
                              "endTime": end_ms - 1, "limit": SPOT_PAGE_LIMIT})
            if not page:
                break
            newest = cursor
            for row in page:
                open_time = int(row[0])
                if start_ms <= open_time < end_ms:
                    rows[open_time] = _float(row[4])
                newest = max(newest, open_time)
            if newest + MINUTE_MS <= cursor:
                break
            cursor = newest + MINUTE_MS
        return sorted(rows.items())


def bybit_bars(client: BybitPublicClient, start_ms: int, end_ms: int) -> list[Bar]:
    """Finished Bybit 1m candles in [start, end), through the D2 collection path."""
    spec = SERIES["kline_1m"]
    bars: dict[int, Bar] = {}
    span = spec.interval_ms * BYBIT_KLINE_PAGE
    cursor = start_ms
    while cursor < end_ms:
        window_end = min(end_ms - 1, cursor + span - 1)
        for row in iter_window(client, spec, cursor, window_end):
            ts = int(row[0])
            if start_ms <= ts < end_ms:
                bars[ts] = Bar(ts_ms=ts, open=_float(row[1]), high=_float(row[2]),
                               low=_float(row[3]), close=_float(row[4]), volume=_float(row[5]))
        cursor = window_end + 1
    return [bars[key] for key in sorted(bars)]


def bybit_open_interest(client: BybitPublicClient, start_ms: int,
                        end_ms: int) -> list[tuple[int, float]]:
    """(stamp, open interest) 5m records in [start, end), through the D2 collection path.

    The stamp is left exactly as the venue gives it. The five-minute knowledge delay the contract
    applies to it belongs to the grid, not here, so there is one place that can get it wrong.
    """
    spec = SERIES["open_interest_5m"]
    rows: dict[int, float] = {}
    span = spec.interval_ms * BYBIT_OI_PAGE
    cursor = start_ms
    while cursor < end_ms:
        window_end = min(end_ms - 1, cursor + span - 1)
        for row in iter_window(client, spec, cursor, window_end):
            ts = int(row["timestamp"])
            if start_ms <= ts < end_ms:
                rows[ts] = _float(row["openInterest"])
        cursor = window_end + 1
    return sorted(rows.items())


def bybit_funding(client: BybitPublicClient, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """(settlement, rate) in [start, end). Used by the shadow trade's cost, never as a feature."""
    spec = SERIES["funding"]
    rows: dict[int, float] = {}
    span = spec.interval_ms * 200
    cursor = start_ms
    while cursor < end_ms:
        window_end = min(end_ms - 1, cursor + span - 1)
        for row in iter_window(client, spec, cursor, window_end):
            ts = int(row["fundingRateTimestamp"])
            if start_ms <= ts < end_ms:
                rows[ts] = _float(row["fundingRate"])
        cursor = window_end + 1
    return sorted(rows.items())


class MarketData:
    """One place that owns the four read-only series and hands back aligned windows.

    `fetch` is deliberately a single call that returns everything a grid needs: the three Bybit
    series and the Binance spot closes for the same span, so a caller cannot accidentally build a
    grid from windows that do not line up.
    """

    def __init__(self, bybit: BybitPublicClient | None = None,
                 spot: BinanceSpotClient | None = None) -> None:
        self.bybit = bybit or BybitPublicClient()
        self.spot = spot or BinanceSpotClient()

    def close(self) -> None:
        self.bybit.close()
        self.spot.close()

    def fetch(self, start_ms: int, end_ms: int) -> dict[str, Any]:
        start_ms -= start_ms % MINUTE_MS
        end_ms -= end_ms % MINUTE_MS
        return {
            "start_ms": start_ms, "end_ms": end_ms,
            "bars": bybit_bars(self.bybit, start_ms, end_ms),
            # The spot window reaches back one 5m interval so the first boundary of the grid has
            # its own five minutes available rather than inheriting a carried value.
            "spot": self.spot.closes(start_ms - FIVE_MIN_MS, end_ms),
            # Open interest reaches back the lag the change looks over plus its knowledge delay.
            "open_interest": bybit_open_interest(self.bybit, start_ms - 2 * 3_600_000, end_ms),
            # Funding needs the held window; one extra day covers a shadow opened just before.
            "funding": bybit_funding(self.bybit, start_ms - 86_400_000, end_ms + 86_400_000),
        }
