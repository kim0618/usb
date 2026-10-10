"""The HOT scan: which liquid perpetual moved the most in the last hour.

Contract (R0, fixed before outcomes were looked at):

* Universe: Binance USD-M, `contractType == PERPETUAL`, `status == TRADING`, `quoteAsset ==
  USDT`, BTCUSDT excluded (BTC is the main instrument, never a HOT candidate), and a perpetual
  carrying a real `deliveryDate` excluded (that is Binance announcing a delisting).
* Eligibility: 24h quote volume >= 20M, last-hour quote volume >= 0.5M, spread <= 10 bp and the
  estimated round trip of a 1,000 USDT market order <= 20 bp, both from the book read at scan
  time.
* Rank: the last hour's high-low range, descending. Nothing else. No 24h term, no composite: R0
  measured that a percentile blend of 1H and 24H saturates at the top (the #1/#2 gap was 0.00)
  and promotes yesterday's mover over the one moving now.

Cost: R0 measured a naive per-symbol scan at ~1,660 weight and 108 s. This one reads the whole
market in three batch calls, keeps the ~100 symbols the batch figures already allow, reads one
minute-kline page for each, and reads a book only for the leaders - about 165 weight, a few
seconds. The prefilter uses the same thresholds as the final gate, so it cannot drop a symbol
the final gate would have kept.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

import httpx

BINANCE_FAPI = "https://fapi.binance.com"

#: BTC is the fixed main instrument, so it never competes for HOT.
EXCLUDED_SYMBOLS = frozenset({"BTCUSDT"})
#: Binance's "no delivery" sentinel on perpetuals. Anything else is a scheduled delisting.
PERPETUAL_NO_DELIVERY_MS = 4133404800000

MIN_QUOTE_VOLUME_24H = 20_000_000.0
MIN_QUOTE_VOLUME_1H = 500_000.0
MAX_SPREAD_BPS = 10.0
MAX_ROUND_TRIP_SLIPPAGE_BPS = 20.0
SLIPPAGE_NOTIONAL_USDT = 1_000.0
TOP_N = 5

KLINE_LIMIT = 62          # weight 1 below 100; 60 closed bars plus the open one, plus slack
KLINE_BARS = 60
DEPTH_LIMIT = 50          # weight 2 at <= 50
DEPTH_BATCH = 8
DEPTH_MAX_CALLS = 16
WORKERS = 8
REQUEST_TIMEOUT_S = 5.0
SCAN_DEADLINE_S = 25.0
#: Abort when Binance reports this much of the IP's 2,400/min already used. The production IP
#: also carries the LIVE terminal's polling, and a scan button must never be what pushes it over.
USED_WEIGHT_ABORT = 1_200

#: Documented request weights (USD-M), confirmed against `x-mbx-used-weight-1m` deltas in R0.
ENDPOINT_WEIGHT = {
    "/fapi/v1/exchangeInfo": 1,
    "/fapi/v1/ticker/24hr": 40,
    "/fapi/v1/ticker/bookTicker": 5,
    "/fapi/v1/klines": 1,
    "/fapi/v1/depth": 2,
}
EXCHANGE_INFO_TTL_S = 3600.0

# Rejection reasons. A symbol carries the first reason that removed it, in pipeline order.
NOT_PERPETUAL = "NOT_PERPETUAL"
NOT_TRADING = "NOT_TRADING"
NOT_USDT_QUOTED = "NOT_USDT_QUOTED"
EXCLUDED_MAIN_SYMBOL = "EXCLUDED_MAIN_SYMBOL"
DELIVERY_SCHEDULED = "DELIVERY_SCHEDULED"
LOW_24H_VOLUME = "LOW_24H_VOLUME"
LOW_1H_VOLUME = "LOW_1H_VOLUME"
WIDE_SPREAD = "WIDE_SPREAD"
HIGH_SLIPPAGE = "HIGH_SLIPPAGE"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
NOT_EVALUATED = "NOT_EVALUATED"  # ranked below the point where five had already passed


class ScanFailed(RuntimeError):
    """The scan produced no result. The caller keeps whatever it showed before."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Transport(Protocol):
    def get(self, path: str, params: Mapping[str, Any] | None = None) -> tuple[Any, Mapping[str, str]]:
        """JSON body and response headers, or an exception."""


class HttpTransport:
    """Binance public REST over httpx. GET only, no key, no signature.

    Query strings are built by httpx, which percent-encodes them: five live USDT perpetuals are
    named in Chinese characters (龙虾USDT among them, a 70M-a-day market), and R0's first trial
    lost all five to a request library that did not.
    """

    def __init__(self, base_url: str = BINANCE_FAPI, *, timeout_s: float = REQUEST_TIMEOUT_S,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout_s, transport=transport,
                                    headers={"User-Agent": "usb-hot-coin-finder/0"})

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> tuple[Any, Mapping[str, str]]:
        response = self._client.get(path, params=dict(params or {}))
        response.raise_for_status()
        return response.json(), response.headers

    def close(self) -> None:
        self._client.close()


@dataclass
class ApiMeter:
    """What one scan cost. `weight` is the sum of documented weights of the requests sent;
    `max_used_weight_1m` is Binance's own figure for the whole IP, which includes other callers."""
    requests: int = 0
    weight: int = 0
    failed: int = 0
    by_endpoint: dict[str, int] = field(default_factory=dict)
    max_used_weight_1m: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record(self, path: str, headers: Mapping[str, str] | None, ok: bool) -> None:
        with self._lock:
            self._record(path, headers, ok)

    def _record(self, path: str, headers: Mapping[str, str] | None, ok: bool) -> None:
        self.requests += 1
        self.weight += ENDPOINT_WEIGHT.get(path, 1)
        self.by_endpoint[path] = self.by_endpoint.get(path, 0) + 1
        if not ok:
            self.failed += 1
        if headers:
            try:
                used = int(headers.get("x-mbx-used-weight-1m", "0"))
            except (TypeError, ValueError):
                used = 0
            self.max_used_weight_1m = max(self.max_used_weight_1m, used)

    def as_dict(self) -> dict[str, Any]:
        return {"requests": self.requests, "weight": self.weight, "failed": self.failed,
                "by_endpoint": dict(self.by_endpoint),
                "max_used_weight_1m": self.max_used_weight_1m}


@dataclass(frozen=True)
class HotCandidate:
    rank: int
    symbol: str
    last_price: float
    one_hour_range_pct: float
    one_hour_change_pct: float
    direction: str
    twenty_four_hour_range_pct: float
    one_hour_quote_volume: float
    twenty_four_hour_quote_volume: float
    spread_bps: float
    estimated_round_trip_slippage_bps_1000: float
    scanned_at_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank, "symbol": self.symbol, "last_price": self.last_price,
            "one_hour_range_pct": self.one_hour_range_pct,
            "one_hour_change_pct": self.one_hour_change_pct, "direction": self.direction,
            "twenty_four_hour_range_pct": self.twenty_four_hour_range_pct,
            "one_hour_quote_volume": self.one_hour_quote_volume,
            "twenty_four_hour_quote_volume": self.twenty_four_hour_quote_volume,
            "spread_bps": self.spread_bps,
            "estimated_round_trip_slippage_bps_1000": self.estimated_round_trip_slippage_bps_1000,
            "scanned_at_ms": self.scanned_at_ms,
        }


@dataclass
class ScanResult:
    scanned_at_ms: int
    duration_ms: int
    top: list[HotCandidate]
    universe_size: int
    stage_counts: dict[str, int]
    rejections: dict[str, str]
    api: ApiMeter

    def rejection_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for reason in self.rejections.values():
            counts[reason] = counts.get(reason, 0) + 1
        return dict(sorted(counts.items()))


# --------------------------------------------------------------------------- pure functions

def range_pct(high: float, low: float, reference: float) -> float | None:
    if reference <= 0 or high < low:
        return None
    return (high - low) / reference * 100.0


def walk_book(levels: Sequence[tuple[float, float]], notional: float, mid: float) -> float | None:
    """Average fill of a market order of `notional` USDT walked through `levels` (best first),
    as bp away from `mid`. `None` when the book read does not hold that much."""
    spent = 0.0
    filled = 0.0
    for price, qty in levels:
        level = price * qty
        if spent + level >= notional:
            filled += (notional - spent) / price
            return abs(notional / filled - mid) / mid * 1e4
        spent += level
        filled += qty
    return None


def book_quality(depth: Mapping[str, Any]) -> tuple[float, float | None] | None:
    """(spread bp, 1,000 USDT round-trip bp) from a depth payload, or None if one side is empty."""
    bids = [(float(p), float(q)) for p, q in depth.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in depth.get("asks", [])]
    if not bids or not asks:
        return None
    best_bid, best_ask = bids[0][0], asks[0][0]
    mid = (best_bid + best_ask) / 2
    if mid <= 0 or best_ask < best_bid:
        return None
    spread = (best_ask - best_bid) / mid * 1e4
    buy = walk_book(asks, SLIPPAGE_NOTIONAL_USDT, mid)
    sell = walk_book(bids, SLIPPAGE_NOTIONAL_USDT, mid)
    return spread, (None if buy is None or sell is None else buy + sell)


def hour_from_klines(klines: Sequence[Sequence[Any]], now_ms: int) -> dict[str, float] | None:
    """Last 60 *closed* one-minute bars: range, signed change and quote volume.

    A bar is closed when its close time is in the past. The 60 must be consecutive minutes; a
    gap (a halted or freshly listed market) is not an hour and is refused rather than stretched.
    """
    closed = [k for k in klines if int(k[6]) < now_ms][-KLINE_BARS:]
    if len(closed) < KLINE_BARS:
        return None
    for previous, current in zip(closed, closed[1:]):
        if int(current[0]) - int(previous[0]) != 60_000:
            return None
    first_open = float(closed[0][1])
    high = max(float(k[2]) for k in closed)
    low = min(float(k[3]) for k in closed)
    last_close = float(closed[-1][4])
    rng = range_pct(high, low, first_open)
    if rng is None:
        return None
    return {
        "range_pct": rng,
        "change_pct": (last_close - first_open) / first_open * 100.0,
        "quote_volume": sum(float(k[7]) for k in closed),
    }


def direction_of(change_pct: float) -> str:
    return "UP" if change_pct > 0 else "DOWN" if change_pct < 0 else "FLAT"


def classify_universe(symbols: Sequence[Mapping[str, Any]]) -> tuple[list[str], dict[str, str]]:
    """Admitted USDT perpetuals, and why every other row was left out."""
    admitted: list[str] = []
    rejected: dict[str, str] = {}
    for row in symbols:
        name = str(row.get("symbol", ""))
        if not name:
            continue
        if row.get("quoteAsset") != "USDT":
            rejected[name] = NOT_USDT_QUOTED
        elif row.get("contractType") != "PERPETUAL":
            rejected[name] = NOT_PERPETUAL
        elif row.get("status") != "TRADING":
            rejected[name] = NOT_TRADING
        elif name in EXCLUDED_SYMBOLS:
            rejected[name] = EXCLUDED_MAIN_SYMBOL
        elif row.get("deliveryDate") not in (None, PERPETUAL_NO_DELIVERY_MS):
            rejected[name] = DELIVERY_SCHEDULED
        else:
            admitted.append(name)
    return admitted, rejected


# --------------------------------------------------------------------------- the scan

class HotScanner:
    def __init__(self, transport: Transport, *, clock_ms: Callable[[], int] | None = None,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._transport = transport
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._monotonic = monotonic
        self._exchange_info: tuple[float, list[Mapping[str, Any]]] | None = None

    def _call(self, meter: ApiMeter, path: str, params: Mapping[str, Any] | None = None) -> Any:
        try:
            body, headers = self._transport.get(path, params)
        except Exception:
            meter.record(path, None, ok=False)
            raise
        meter.record(path, headers, ok=True)
        return body

    def _guard(self, meter: ApiMeter, started: float) -> None:
        if meter.max_used_weight_1m >= USED_WEIGHT_ABORT:
            raise ScanFailed("HOT_SCAN_RATE_BUDGET",
                             f"Binance 사용 weight가 {meter.max_used_weight_1m}/분이라 탐색을 중단했습니다.")
        if self._monotonic() - started > SCAN_DEADLINE_S:
            raise ScanFailed("HOT_SCAN_TIMEOUT", "탐색 시간이 초과되었습니다.")

    def _symbols(self, meter: ApiMeter) -> list[Mapping[str, Any]]:
        now = self._monotonic()
        if self._exchange_info and now - self._exchange_info[0] < EXCHANGE_INFO_TTL_S:
            return self._exchange_info[1]
        body = self._call(meter, "/fapi/v1/exchangeInfo")
        rows = list(body.get("symbols", []))
        if not rows:
            raise ValueError("exchangeInfo has no symbols")
        self._exchange_info = (now, rows)
        return rows

    def symbol_row(self, symbol: str) -> Mapping[str, Any] | None:
        """The symbol's exchangeInfo row from the cached universe (read once if not cached)."""
        rows = self._symbols(ApiMeter())
        return next((row for row in rows if row.get("symbol") == symbol), None)

    def scan(self) -> ScanResult:
        started = self._monotonic()
        meter = ApiMeter()
        try:
            rows = self._symbols(meter)
            tickers = {t["symbol"]: t for t in self._call(meter, "/fapi/v1/ticker/24hr")}
            books = {t["symbol"]: t for t in self._call(meter, "/fapi/v1/ticker/bookTicker")}
        except ScanFailed:
            raise
        except Exception as exc:
            # Without the whole-market batch there is nothing to rank. The previous result stands.
            raise ScanFailed("HOT_SCAN_UPSTREAM", f"Binance 시장 데이터를 읽지 못했습니다: {type(exc).__name__}") from exc
        self._guard(meter, started)
        scanned_at_ms = self._clock_ms()

        admitted, rejections = classify_universe(rows)
        stage = {"universe": len(admitted)}

        # Stage 1: batch figures only. Same thresholds as the final gate.
        day: dict[str, dict[str, float]] = {}
        for name in admitted:
            ticker, book = tickers.get(name), books.get(name)
            try:
                quote_volume = float(ticker["quoteVolume"])
                high, low, open_ = (float(ticker["highPrice"]), float(ticker["lowPrice"]),
                                    float(ticker["openPrice"]))
                last = float(ticker["lastPrice"])
                bid, ask = float(book["bidPrice"]), float(book["askPrice"])
            except (TypeError, KeyError, ValueError):
                rejections[name] = DATA_UNAVAILABLE
                continue
            day_range = range_pct(high, low, open_)
            mid = (bid + ask) / 2
            if day_range is None or mid <= 0 or ask < bid:
                rejections[name] = DATA_UNAVAILABLE
                continue
            if quote_volume < MIN_QUOTE_VOLUME_24H:
                rejections[name] = LOW_24H_VOLUME
                continue
            if (ask - bid) / mid * 1e4 > MAX_SPREAD_BPS:
                rejections[name] = WIDE_SPREAD
                continue
            day[name] = {"range": day_range, "volume": quote_volume, "last": last}
        stage["after_batch_prefilter"] = len(day)

        # Stage 2: one kline page per survivor. A symbol whose page fails is skipped, not fatal.
        now_ms = self._clock_ms()

        def read_hour(name: str) -> tuple[str, dict[str, float] | None, bool]:
            try:
                klines = self._call(meter, "/fapi/v1/klines",
                                    {"symbol": name, "interval": "1m", "limit": KLINE_LIMIT})
            except Exception:
                return name, None, False
            return name, hour_from_klines(klines, now_ms), True

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            hours = list(pool.map(read_hour, sorted(day)))
        self._guard(meter, started)
        ranked: list[tuple[str, dict[str, float]]] = []
        for name, hour, ok in hours:
            if not ok:
                rejections[name] = DATA_UNAVAILABLE
            elif hour is None:
                rejections[name] = INSUFFICIENT_HISTORY
            elif hour["quote_volume"] < MIN_QUOTE_VOLUME_1H:
                rejections[name] = LOW_1H_VOLUME
            else:
                ranked.append((name, hour))
        # Rank by the last hour's range alone; the symbol breaks exact ties so a scan is a
        # function of its inputs.
        ranked.sort(key=lambda item: (-item[1]["range_pct"], item[0]))
        stage["after_1h_volume"] = len(ranked)

        # Stage 3: books for the leaders, in rank order, until five pass or the cap is reached.
        passed: list[tuple[str, dict[str, float], float, float]] = []
        cursor = 0
        depth_calls = 0

        def read_book(name: str) -> tuple[str, Any]:
            try:
                return name, self._call(meter, "/fapi/v1/depth", {"symbol": name, "limit": DEPTH_LIMIT})
            except Exception:
                return name, None

        while len(passed) < TOP_N and cursor < len(ranked) and depth_calls < DEPTH_MAX_CALLS:
            batch = ranked[cursor:cursor + min(DEPTH_BATCH, DEPTH_MAX_CALLS - depth_calls)]
            cursor += len(batch)
            depth_calls += len(batch)
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                books_read = dict(pool.map(read_book, [name for name, _ in batch]))
            self._guard(meter, started)
            for name, hour in batch:
                depth = books_read.get(name)
                quality = book_quality(depth) if isinstance(depth, Mapping) else None
                if quality is None:
                    rejections[name] = DATA_UNAVAILABLE
                    continue
                spread, round_trip = quality
                if spread > MAX_SPREAD_BPS:
                    rejections[name] = WIDE_SPREAD
                elif round_trip is None or round_trip > MAX_ROUND_TRIP_SLIPPAGE_BPS:
                    rejections[name] = HIGH_SLIPPAGE
                elif len(passed) < TOP_N:
                    passed.append((name, hour, spread, round_trip))
                else:
                    rejections[name] = NOT_EVALUATED
        for name, _ in ranked[cursor:]:
            rejections[name] = NOT_EVALUATED
        stage["books_read"] = depth_calls
        stage["eligible_in_books_read"] = len(passed)

        top = [HotCandidate(
            rank=index + 1, symbol=name, last_price=day[name]["last"],
            one_hour_range_pct=hour["range_pct"], one_hour_change_pct=hour["change_pct"],
            direction=direction_of(hour["change_pct"]),
            twenty_four_hour_range_pct=day[name]["range"],
            one_hour_quote_volume=hour["quote_volume"],
            twenty_four_hour_quote_volume=day[name]["volume"],
            spread_bps=spread, estimated_round_trip_slippage_bps_1000=round_trip,
            scanned_at_ms=scanned_at_ms,
        ) for index, (name, hour, spread, round_trip) in enumerate(passed)]
        if not top:
            raise ScanFailed("HOT_SCAN_EMPTY", "조건을 통과한 후보가 없습니다.")
        return ScanResult(scanned_at_ms=scanned_at_ms,
                          duration_ms=int((self._monotonic() - started) * 1000), top=top,
                          universe_size=len(admitted), stage_counts=stage,
                          rejections=rejections, api=meter)


__all__ = [
    "ApiMeter", "HotCandidate", "HotScanner", "HttpTransport", "ScanFailed", "ScanResult",
    "book_quality", "classify_universe", "direction_of", "hour_from_klines", "walk_book",
]
