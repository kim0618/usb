"""The only module in this package that reaches the network, and the guard that keeps it honest.

Four public Binance USD-M endpoints, all `GET`, all unsigned, all market data. The allow-list is a
constant and every request goes through `fetch_json`, which refuses anything outside it - the same
shape of guard the V0 collector puts around its three endpoints, and for the same reason: a package
that lives beside a process holding API keys should make "it cannot sign a request" checkable
instead of stated.

There is no client object and no connection pool on purpose. These reads happen once a minute at
most, so `urllib` with an explicit timeout is the whole requirement, and it keeps the dependency
surface of a read-only viewer at zero.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

#: The one host this package may talk to.
HOST = "https://fapi.binance.com"

#: Section 8 of the contract, plus the 1m klines of section 5. Nothing signed, nothing private.
#: A path not in this tuple cannot be requested, so no amount of later editing elsewhere in the
#: package can turn this into an account or order call.
ALLOWED_PATHS: tuple[str, ...] = (
    "/fapi/v1/klines",
    "/fapi/v1/premiumIndex",
    "/fapi/v1/openInterest",
    "/futures/data/openInterestHist",
)

USER_AGENT = "usb-crypto-market-context-v1/1 (public market data only)"
TIMEOUT_S = 20.0
RETRIES = 3
#: Most rows one klines page returns. The venue's own cap is 1500.
KLINE_PAGE = 1500
MINUTE_MS = 60_000


class VenueError(RuntimeError):
    """A read did not produce usable data. Carried to the screen as a reason, never swallowed."""


def guard(path: str) -> None:
    """Refuse any path outside the allow-list. Called before every request."""
    if path not in ALLOWED_PATHS:
        raise VenueError(f"PATH_NOT_ALLOWED:{path}")


def fetch_json(path: str, params: dict[str, Any], *, timeout_s: float = TIMEOUT_S,
               retries: int = RETRIES, opener: Any = None) -> Any:
    """One public GET, retried on transport failure. `opener` exists for the tests only."""
    guard(path)
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{HOST}{path}?{query}" if query else f"{HOST}{path}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    last: Exception | None = None
    for attempt in range(retries):
        try:
            open_it = opener or urllib.request.urlopen
            with open_it(request, timeout=timeout_s) as handle:
                return json.loads(handle.read().decode("utf-8"))
        except Exception as caught:                       # transport, decode or venue error alike
            last = caught
            if attempt == retries - 1:
                break
            time.sleep(0.6 * (attempt + 1))
    raise VenueError(f"FETCH_FAILED:{path}:{type(last).__name__}")


# --------------------------------------------------------------------------- klines


def klines(symbol: str, start_ms: int, end_ms: int, *, opener: Any = None) -> list[dict[str, Any]]:
    """Closed 1m bars with `start_ms <= open_time < end_ms`, ascending, no duplicates.

    The venue's `endTime` is inclusive, so it is asked for `end_ms - 1`; every caller here thinks
    in half-open intervals and the one place that has to know otherwise is this line.

    The rows are kept in the field names Market Context R0's collector used, so that the same
    `Bars` construction reads either a live page or that study's frozen file.
    """
    rows: dict[int, dict[str, Any]] = {}
    cursor = start_ms
    while cursor < end_ms:
        chunk_end = min(cursor + KLINE_PAGE * MINUTE_MS, end_ms)
        page = fetch_json("/fapi/v1/klines",
                          {"symbol": symbol, "interval": "1m", "startTime": cursor,
                           "endTime": chunk_end - 1, "limit": KLINE_PAGE}, opener=opener)
        if not isinstance(page, list):
            raise VenueError(f"KLINES_NOT_A_LIST:{cursor}")
        if not page:
            # An empty page inside the requested span is a hole the caller has to see, because a
            # silently shortened series would change the anchor without saying so.
            raise VenueError(f"KLINES_EMPTY_PAGE:{cursor}")
        for row in page:
            stamp = int(row[0])
            if start_ms <= stamp < end_ms:
                rows[stamp] = {"timestamp_ms": stamp, "open": row[1], "high": row[2],
                               "low": row[3], "close": row[4], "volume": row[5],
                               "turnover": row[7], "trades": row[8],
                               "taker_buy_base": row[9], "taker_buy_quote": row[10]}
        cursor = chunk_end
    return [rows[stamp] for stamp in sorted(rows)]


# --------------------------------------------------------------------------- derivatives


def premium_index(symbol: str, *, opener: Any = None) -> dict[str, Any]:
    """Mark price, index price, last funding rate and the next funding time."""
    body = fetch_json("/fapi/v1/premiumIndex", {"symbol": symbol}, opener=opener)
    if not isinstance(body, dict):
        raise VenueError("PREMIUM_INDEX_NOT_AN_OBJECT")
    return body


def open_interest(symbol: str, *, opener: Any = None) -> dict[str, Any]:
    """Current open interest in base units, with the venue's own timestamp."""
    body = fetch_json("/fapi/v1/openInterest", {"symbol": symbol}, opener=opener)
    if not isinstance(body, dict):
        raise VenueError("OPEN_INTEREST_NOT_AN_OBJECT")
    return body


def open_interest_hist(symbol: str, period: str, limit: int, *,
                       opener: Any = None) -> list[dict[str, Any]]:
    """Closed open-interest buckets, ascending. The venue returns oldest first."""
    body = fetch_json("/futures/data/openInterestHist",
                      {"symbol": symbol, "period": period, "limit": limit}, opener=opener)
    if not isinstance(body, list):
        raise VenueError("OPEN_INTEREST_HIST_NOT_A_LIST")
    return body


__all__ = ["HOST", "ALLOWED_PATHS", "USER_AGENT", "KLINE_PAGE", "VenueError", "guard",
           "fetch_json", "klines", "premium_index", "open_interest", "open_interest_hist"]
