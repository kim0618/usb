"""Binance public market data for the HOT symbol: live feed, chart history, instrument spec.

The terminal's own market data is Bybit's, and it stays Bybit's for BTC and for the two other
build instruments. A HOT symbol is whichever Binance USD-M perpetual moved the most, and some of
them (龙虾USDT among them) are not listed on Bybit at all, so the HOT symbol's market data comes
from Binance instead. Each class here mirrors the interface of its Bybit counterpart, so the
paper runtime, the routes and the screen read a HOT symbol exactly the way they read BTC.

Public endpoints only. Nothing here holds a key or knows an order route.

Binance serves its streams from two paths: the book (`depth20`) on `/public`, the market streams
(`markPrice`, `kline`) on `/market`. A single combined connection on either path silently
delivers only its own half, so the feed opens one socket per path.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import random
import threading
import time
import urllib.parse
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx
import websockets

from ..paper.book import BookSide, Quote
from ..paper.instrument import InstrumentSpec, RiskTierTable

FAPI = "https://fapi.binance.com"
WS_BASE = "wss://fstream.binance.com"
BYBIT_REST = "https://api.bybit.com"
BOOK_DEPTH = 5
KLINE_KEEP = 600
DEFAULT_FUNDING_MINUTES = 480


# --------------------------------------------------------------------------- live feed

@dataclass
class FeedTelemetry:
    connected: bool = False
    connects: int = 0
    reconnects: int = 0
    book_gaps: int = 0
    book_resyncs: int = 0
    malformed: int = 0
    messages: int = 0
    ticker_deltas_without_snapshot: int = 0
    last_message_ms: int | None = None
    last_error: str | None = None


class SnapshotBook:
    """Binance `depth20` is a full top-20 snapshot every 100 ms, so the book is replaced, not
    patched, and there is no sequence to keep: a lost message costs 100 ms, not the book."""

    def __init__(self) -> None:
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}

    @property
    def ready(self) -> bool:
        return bool(self.bids) and bool(self.asks)

    def reset(self) -> None:
        self.bids = {}
        self.asks = {}

    def replace(self, bids: list[list[str]], asks: list[list[str]]) -> None:
        self.bids = {Decimal(p): Decimal(q) for p, q in bids if Decimal(q) > 0}
        self.asks = {Decimal(p): Decimal(q) for p, q in asks if Decimal(q) > 0}


def stream_name(symbol: str, suffix: str) -> str:
    return f"{symbol.lower()}@{suffix}"


def stream_url(path: str, streams: list[str]) -> str:
    # Percent-encode the symbol part (Chinese-named perpetuals) and keep `@` and `/` literal.
    return f"{WS_BASE}{path}?streams=" + urllib.parse.quote("/".join(streams), safe="/@")


@dataclass
class BinancePublicFeed:
    """Same surface as `terminal.feed.BybitPublicFeed`: `quote()`, `chart()`, `view()`,
    `seed_klines()`, `start()`, `stop()`. `quote()` is None until a book and a mark have both
    arrived, exactly like the Bybit feed."""
    symbol: str
    book: SnapshotBook = field(default_factory=SnapshotBook)
    ticker: dict[str, Any] = field(default_factory=dict)
    telemetry: FeedTelemetry = field(default_factory=FeedTelemetry)
    klines: list[dict[str, Any]] = field(default_factory=list)
    last_ticker_ms: int | None = None
    source: str = "BINANCE_PUBLIC"
    _task: asyncio.Task | None = None
    _stop: asyncio.Event | None = None
    _sockets: dict[str, Any] = field(default_factory=dict)
    faults_injected: int = 0

    # ---------------------------------------------------------------- view
    def quote(self) -> Quote | None:
        mark = self.ticker.get("markPrice")
        if mark is None or not self.book.ready:
            return None
        bids = [[str(p), str(q)] for p, q in sorted(self.book.bids.items(), reverse=True)[:BOOK_DEPTH]]
        asks = [[str(p), str(q)] for p, q in sorted(self.book.asks.items())[:BOOK_DEPTH]]
        return Quote(
            ts_ms=self.last_ticker_ms or int(time.time() * 1000),
            bids=BookSide.from_rows(bids, descending=True),
            asks=BookSide.from_rows(asks, descending=False),
            mark_price=Decimal(str(mark)),
            last_price=Decimal(str(self.ticker["lastPrice"])) if self.ticker.get("lastPrice") else None,
            index_price=Decimal(str(self.ticker["indexPrice"])) if self.ticker.get("indexPrice") else None,
            funding_rate=Decimal(str(self.ticker["fundingRate"])) if self.ticker.get("fundingRate") else None,
            next_funding_time_ms=int(self.ticker["nextFundingTime"]) if self.ticker.get("nextFundingTime") else None,
        )

    def chart(self, limit: int = 120) -> list[dict[str, Any]]:
        return self.klines[-limit:]

    def view(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "source": self.source,
            "connected": self.telemetry.connected, "connects": self.telemetry.connects,
            "reconnects": self.telemetry.reconnects, "book_gaps": self.telemetry.book_gaps,
            "book_resyncs": self.telemetry.book_resyncs, "malformed": self.telemetry.malformed,
            "messages": self.telemetry.messages, "last_message_ms": self.telemetry.last_message_ms,
            "last_error": self.telemetry.last_error, "book_ready": self.book.ready,
            "faults_injected": self.faults_injected, "ticker_fields": len(self.ticker),
            "funding_cap": None, "open_interest": None,
        }

    # ---------------------------------------------------------------- seed
    def seed_klines(self, *, limit: int = 120, client: httpx.Client | None = None) -> None:
        """Prepend REST history older than what the socket already delivered (same rule as the
        Bybit feed: observed bars are never replaced by a snapshot taken a moment later)."""
        owned = client is None
        http = client or httpx.Client(base_url=FAPI, timeout=20,
                                      headers={"User-Agent": "usb-crypto-hot/1"})
        try:
            response = http.get("/fapi/v1/klines", params={
                "symbol": self.symbol, "interval": "1m", "limit": limit})
            response.raise_for_status()
            now = int(time.time() * 1000)
            seeded = [{"start_ms": int(r[0]), "open": r[1], "high": r[2], "low": r[3],
                       "close": r[4], "volume": r[5], "confirmed": int(r[6]) < now}
                      for r in response.json()]
            if self.klines:
                oldest = self.klines[0]["start_ms"]
                self.klines = ([b for b in seeded if b["start_ms"] < oldest] + self.klines)[-KLINE_KEEP:]
            else:
                self.klines = seeded
        finally:
            if owned:
                http.close()

    # ---------------------------------------------------------------- messages
    def handle(self, message: Mapping[str, Any], received_ms: int) -> None:
        self.telemetry.messages += 1
        self.telemetry.last_message_ms = received_ms
        data = message.get("data") if isinstance(message, Mapping) else None
        if not isinstance(data, Mapping):
            self.telemetry.malformed += 1
            return
        if data.get("s") not in (None, self.symbol):
            # A frame for another instrument is never attributed to this one.
            self.telemetry.malformed += 1
            return
        try:
            kind = data.get("e")
            if kind == "depthUpdate":
                self.book.replace(list(data["b"]), list(data["a"]))
            elif kind == "markPriceUpdate":
                self.ticker.update({"markPrice": data["p"], "indexPrice": data.get("i"),
                                    "fundingRate": data.get("r"),
                                    "nextFundingTime": data.get("T")})
                self.last_ticker_ms = int(data["E"])
            elif kind == "kline":
                k = data["k"]
                self._merge_bar({"start_ms": int(k["t"]), "open": k["o"], "high": k["h"],
                                 "low": k["l"], "close": k["c"], "volume": k["v"],
                                 "confirmed": bool(k.get("x"))})
                self.ticker["lastPrice"] = k["c"]
            else:
                self.telemetry.malformed += 1
        except (KeyError, TypeError, ValueError, ArithmeticError):
            self.telemetry.malformed += 1

    def _merge_bar(self, entry: dict[str, Any]) -> None:
        if self.klines and self.klines[-1]["start_ms"] == entry["start_ms"]:
            self.klines[-1] = entry
        elif self.klines and entry["start_ms"] < self.klines[-1]["start_ms"]:
            return
        else:
            self.klines.append(entry)
        del self.klines[:-KLINE_KEEP]

    def on_disconnect(self, path: str | None = None) -> None:
        if path in (None, "/public"):
            self.book.reset()
        if path in (None, "/market"):
            self.ticker.clear()
        self.telemetry.connected = False

    # ---------------------------------------------------------------- loop
    def _streams(self, path: str) -> list[str]:
        if path == "/public":
            return [stream_name(self.symbol, "depth20@100ms")]
        return [stream_name(self.symbol, "markPrice@1s"), stream_name(self.symbol, "kline_1m")]

    async def _run_path(self, path: str, connector: Callable[[str], Any] | None) -> None:
        assert self._stop is not None
        attempt = 0
        while not self._stop.is_set():
            url = stream_url(path + "/stream", self._streams(path))
            try:
                factory = connector or (lambda u: websockets.connect(
                    u, ping_interval=20, ping_timeout=20, max_queue=256, open_timeout=10))
                async with factory(url) as socket:
                    self._sockets[path] = socket
                    if attempt:
                        self.telemetry.reconnects += 1
                    self.on_disconnect(path)
                    self.telemetry.connects += 1
                    self.telemetry.connected = True
                    attempt = 0
                    while not self._stop.is_set():
                        raw = await asyncio.wait_for(socket.recv(), timeout=30)
                        try:
                            self.handle(json.loads(raw), int(time.time() * 1000))
                        except json.JSONDecodeError:
                            self.telemetry.malformed += 1
            except (OSError, websockets.ConnectionClosed, asyncio.TimeoutError) as exc:
                self.telemetry.last_error = f"{path} {type(exc).__name__}: {exc}"
                self.on_disconnect(path)
                attempt += 1
                delay = min(30.0, 2 ** min(attempt, 5)) + random.random()
                if not self._stop.is_set():
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=delay)
            finally:
                self._sockets.pop(path, None)
        self.on_disconnect(path)

    async def run(self, *, connector: Callable[[str], Any] | None = None) -> None:
        self._stop = asyncio.Event()
        await asyncio.gather(self._run_path("/public", connector),
                             self._run_path("/market", connector))

    async def inject_disconnect(self) -> bool:
        closed = False
        for socket in list(self._sockets.values()):
            self.faults_injected += 1
            await socket.close()
            closed = True
        return closed

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._sockets.clear()
        self.on_disconnect()


# --------------------------------------------------------------------------- chart history

@dataclass(frozen=True)
class _Interval:
    source: str
    source_ms: int
    bucket_ms: int


INTERVALS = {
    "1m": _Interval("1m", 60_000, 60_000),
    "10m": _Interval("5m", 300_000, 600_000),     # folded from 5m on UTC epoch boundaries
    "1h": _Interval("1h", 3_600_000, 3_600_000),
    "4h": _Interval("4h", 14_400_000, 14_400_000),
    "1d": _Interval("1d", 86_400_000, 86_400_000),
}
PAGE_LIMIT = 1000           # weight 5 per page
MAX_TARGET_BARS = 3000
CACHE_TTL_S = 15.0
CACHE_ENTRIES = 48


class BinanceChartHistory:
    """Same `get()` contract and body shape as `terminal.chart_history.BybitChartHistory`, with
    `source` naming Binance. Symbol validation is the caller's (the route resolves it first)."""

    def __init__(self, request: Callable[[str, dict[str, Any]], Any] | None = None,
                 *, clock: Callable[[], float] = time.monotonic) -> None:
        self._request = request or self._http_request
        self._clock = clock
        self._cache: OrderedDict[tuple[str, str, int | None, int], tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _http_request(path: str, params: dict[str, Any]) -> Any:
        with httpx.Client(base_url=FAPI, timeout=20, headers={"User-Agent": "usb-crypto-hot/1"}) as client:
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()

    def forget(self, symbol: str) -> int:
        """Drop every cached page for one symbol. Called when the HOT slot moves on."""
        with self._lock:
            keys = [key for key in self._cache if key[0] == symbol]
            for key in keys:
                del self._cache[key]
            return len(keys)

    def cached_symbols(self) -> set[str]:
        with self._lock:
            return {key[0] for key in self._cache}

    def _rows(self, interval: _Interval, needed: int, before_ms: int | None,
              symbol: str) -> tuple[list[dict[str, Any]], bool]:
        rows: dict[int, dict[str, Any]] = {}
        cursor = before_ms
        exhausted = False
        while len(rows) < needed:
            limit = min(PAGE_LIMIT, needed - len(rows))
            params: dict[str, Any] = {"symbol": symbol, "interval": interval.source, "limit": limit}
            if cursor is not None:
                params["endTime"] = cursor - 1
            page = self._request("/fapi/v1/klines", params)
            if not page:
                exhausted = True
                break
            oldest = None
            for raw in page:
                start = int(raw[0])
                if before_ms is not None and start >= before_ms:
                    continue
                rows[start] = {"start_ms": start, "open": raw[1], "high": raw[2], "low": raw[3],
                               "close": raw[4], "volume": raw[5]}
                oldest = start if oldest is None else min(oldest, start)
            if oldest is None or len(page) < limit:
                exhausted = True
                break
            cursor = oldest
        return [rows[k] for k in sorted(rows)], exhausted

    def get(self, timeframe: str, limit: int, before_ms: int | None = None,
            symbol: str | None = None) -> dict[str, Any]:
        from ..terminal.chart_history import aggregate_rows
        if timeframe not in INTERVALS:
            raise ValueError(f"unsupported timeframe: {timeframe}")
        if not 1 <= limit <= MAX_TARGET_BARS:
            raise ValueError(f"limit must be in [1, {MAX_TARGET_BARS}]")
        if not symbol:
            raise ValueError("symbol is required")
        interval = INTERVALS[timeframe]
        normalized = (before_ms - before_ms % interval.bucket_ms) if before_ms is not None else None
        key = (symbol, timeframe, normalized, limit)
        now = self._clock()
        with self._lock:
            # An expired page is never served again (it is refetched and replaced), so holding it
            # until 48 newer keys push it out only keeps a few thousand candle dicts alive.
            for stale in [held for held, (at, _) in self._cache.items() if now - at >= CACHE_TTL_S]:
                del self._cache[stale]
            cached = self._cache.get(key)
            if cached is not None and now - cached[0] < CACHE_TTL_S:
                self._cache.move_to_end(key)
                return cached[1]
            needed = limit * (interval.bucket_ms // interval.source_ms) + 2
            source, exhausted = self._rows(interval, needed, normalized, symbol)
            candles = aggregate_rows(source, interval.bucket_ms)
            if normalized is not None:
                candles = [row for row in candles if row["start_ms"] < normalized]
            candles = candles[-limit:]
            body = {"symbol": symbol, "timeframe": timeframe, "bucket_ms": interval.bucket_ms,
                    "source": "BINANCE_PUBLIC_KLINE", "source_interval": interval.source,
                    "bars": candles, "has_more": not exhausted and len(candles) == limit,
                    "next_before_ms": candles[0]["start_ms"] if candles else normalized}
            self._cache[key] = (now, body)
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_ENTRIES:
                self._cache.popitem(last=False)
            return body


# --------------------------------------------------------------------------- instrument spec

def _filters(row: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {f["filterType"]: f for f in row.get("filters", []) if isinstance(f, Mapping)}


def instrument_from_exchange_info(row: Mapping[str, Any], *, max_leverage: Decimal,
                                  funding_minutes: int, source: str) -> InstrumentSpec:
    """A paper instrument spec from Binance's own filters. Every field is required; a missing
    filter raises rather than borrowing BTC's value. Leverage limits come from the margin table
    the paper run uses (Binance's public exchangeInfo carries none that are real)."""
    symbol = str(row["symbol"])
    f = _filters(row)

    def need(kind: str, key: str) -> Decimal:
        value = f.get(kind, {}).get(key)
        if value in (None, ""):
            raise ValueError(f"{symbol} exchangeInfo is missing {kind}.{key}")
        return Decimal(str(value))

    market = "MARKET_LOT_SIZE" if "MARKET_LOT_SIZE" in f else "LOT_SIZE"
    notional = need("MIN_NOTIONAL", "notional") if "MIN_NOTIONAL" in f else need("NOTIONAL", "minNotional")
    return InstrumentSpec(
        symbol=symbol, contract_type="LinearPerpetual", settle_coin=str(row.get("marginAsset") or "USDT"),
        tick_size=need("PRICE_FILTER", "tickSize"), qty_step=need(market, "stepSize"),
        min_order_qty=need(market, "minQty"), max_order_qty=need("LOT_SIZE", "maxQty"),
        max_mkt_order_qty=need(market, "maxQty"), min_notional_value=notional,
        min_leverage=Decimal(1), max_leverage=max_leverage, leverage_step=Decimal(1),
        funding_interval_minutes=funding_minutes, source=source)


def risk_payload_from_binance_brackets(symbol: str, brackets: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Binance leverage brackets in the shape `RiskTierTable.from_payload` reads.

    `cum` is Binance's maintenance amount, the same quantity Bybit calls `mmDeduction`, so the
    continuity check the paper engine relies on applies unchanged."""
    rows = []
    for item in sorted(brackets, key=lambda b: Decimal(str(b["notionalCap"]))):
        leverage = Decimal(str(item["initialLeverage"]))
        rows.append({"id": int(item["bracket"]), "symbol": symbol,
                     "riskLimitValue": str(item["notionalCap"]),
                     "maintenanceMargin": str(item["maintMarginRatio"]),
                     "initialMargin": str(Decimal(1) / leverage), "maxLeverage": str(leverage),
                     "mmDeduction": str(item.get("cum", "0"))})
    return {"retCode": 0, "retMsg": "BINANCE_ACCOUNT_LEVERAGE_BRACKET", "result": {"list": rows}}


@dataclass(frozen=True)
class ReferencePaths:
    exchange_info: Path
    risk_limit: Path


def reference_dir(root: Path, symbol: str) -> Path:
    return root / "reference" / symbol / "reference"


def find_reference(root: Path, symbol: str) -> ReferencePaths | None:
    folder = reference_dir(root, symbol)
    if not folder.is_dir():
        return None
    info = sorted(folder.glob("binance_exchange_info_*.json"))
    risk = sorted(folder.glob("risk_limit_*.json"))
    if not info or not risk:
        return None
    return ReferencePaths(info[-1], risk[-1])


def write_reference(root: Path, symbol: str, *, exchange_row: Mapping[str, Any],
                    funding_minutes: int, risk_payload: Mapping[str, Any],
                    now_s: int | None = None) -> ReferencePaths:
    """Save the measured responses a HOT paper run is built from, the way the BTC/ETH/SOL runs
    keep theirs, so a restart rebuilds the same spec from the same bytes."""
    folder = reference_dir(root, symbol)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time()) if now_s is None else now_s
    info_path = folder / f"binance_exchange_info_{stamp}.json"
    risk_path = folder / f"risk_limit_{stamp}.json"
    for path, body in ((info_path, {"symbol_row": exchange_row, "funding_interval_minutes": funding_minutes}),
                       (risk_path, risk_payload)):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True))
        tmp.replace(path)
    return ReferencePaths(info_path, risk_path)


def load_reference(paths: ReferencePaths) -> tuple[InstrumentSpec, RiskTierTable]:
    raw = paths.exchange_info.read_bytes()
    body = json.loads(raw)
    tiers = RiskTierTable.from_file(paths.risk_limit)
    broken = tiers.verify_continuity()
    if broken:
        raise ValueError(f"risk table is discontinuous at {broken[0]}")
    spec = instrument_from_exchange_info(
        body["symbol_row"], max_leverage=tiers.tiers[0].max_leverage,
        funding_minutes=int(body.get("funding_interval_minutes") or DEFAULT_FUNDING_MINUTES),
        source=(f"binance GET /fapi/v1/exchangeInfo {paths.exchange_info} sha256 "
                f"{hashlib.sha256(raw).hexdigest()}; margin {tiers.source}"))
    return spec, tiers


class MarginTableUnavailable(RuntimeError):
    """No measured margin table for the symbol: its paper run is refused with this reason."""


def fetch_reference(root: Path, symbol: str, exchange_row: Mapping[str, Any], *,
                    bracket_provider: Callable[[str], list[Mapping[str, Any]] | None] | None = None,
                    http: Callable[[str, str, dict[str, Any]], Any] | None = None) -> ReferencePaths:
    """Measure and save what a HOT paper run needs.

    Margin table: Bybit's public risk-limit for the same symbol first, which is the source every
    existing paper run already uses. A symbol Bybit does not list falls back to the account's own
    Binance leverage brackets when the LIVE side can provide them; otherwise the paper run is
    refused. Nothing is assumed: Binance's public `maintMarginPercent` is the same 2.5 for BTC and
    for every alt, so it is not a margin table and is not used.
    """
    get = http or _http_get
    payload = get(BYBIT_REST, "/v5/market/risk-limit", {"category": "linear", "symbol": symbol})
    rows = (payload or {}).get("result", {}).get("list") if isinstance(payload, Mapping) else None
    if not (isinstance(payload, Mapping) and payload.get("retCode") == 0 and rows):
        brackets = bracket_provider(symbol) if bracket_provider else None
        if not brackets:
            raise MarginTableUnavailable(
                f"{symbol}: Bybit에 상장되지 않았고 Binance 계좌 bracket도 없어 PAPER 마진표를 만들 수 없습니다.")
        payload = risk_payload_from_binance_brackets(symbol, brackets)
    funding = DEFAULT_FUNDING_MINUTES
    try:
        info = get(FAPI, "/fapi/v1/fundingInfo", {})
        for item in info or []:
            if item.get("symbol") == symbol and item.get("fundingIntervalHours"):
                funding = int(item["fundingIntervalHours"]) * 60
    except Exception:
        pass  # absent from fundingInfo means the default 8h interval
    return write_reference(root, symbol, exchange_row=exchange_row, funding_minutes=funding,
                           risk_payload=payload)


def _http_get(base: str, path: str, params: dict[str, Any]) -> Any:
    with httpx.Client(base_url=base, timeout=15, headers={"User-Agent": "usb-crypto-hot/1"}) as client:
        response = client.get(path, params=params)
        response.raise_for_status()
        return response.json()


__all__ = ["BinanceChartHistory", "BinancePublicFeed", "MarginTableUnavailable", "ReferencePaths",
           "fetch_reference", "find_reference", "instrument_from_exchange_info", "load_reference",
           "risk_payload_from_binance_brackets", "write_reference"]


# --------------------------------------------------------------------------- 15 s candles

from ..terminal import trade_candles as _tc  # noqa: E402


class BinanceTradeCandleFeed(_tc.TradeCandleFeed):
    """The 15 s chart's trade feed for the HOT symbol: Binance `aggTrade` on the `/market` path.

    Same thread, same candle book, same status vocabulary as the Bybit feed it extends; only
    the socket and the message shape differ. Binance pings the client itself and `websockets`
    answers, so there is no application-level ping.
    """

    def handle(self, message: dict[str, Any], received_ms: int) -> None:
        self.telemetry.messages += 1
        self.telemetry.last_message_ms = received_ms
        data = message.get("data") if isinstance(message, dict) else None
        if not isinstance(data, dict) or data.get("e") != "aggTrade" or data.get("s") != self.symbol:
            self.telemetry.malformed += 1
            return
        self.telemetry.last_trade_ms = received_ms
        try:
            ts = int(data["T"])
            self.book.add(exec_id=str(data["a"]), ts_ms=ts,
                          price=Decimal(str(data["p"])), size=Decimal(str(data["q"])))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            self.telemetry.malformed += 1
            return
        lag = received_ms - ts
        t = self.telemetry
        t.last_lag_ms = lag
        t.avg_lag_ms = lag if t.avg_lag_ms is None else t.avg_lag_ms * 0.95 + lag * 0.05

    async def _run(self) -> None:
        attempt = 0
        url = stream_url("/market/stream", [stream_name(self.symbol, "aggTrade")])
        while not self._stop.is_set():
            try:
                factory = self._connector or (lambda: websockets.connect(
                    url, ping_interval=20, ping_timeout=20, max_queue=1024, open_timeout=10))
                async with factory() as socket:
                    now = int(time.time() * 1000)
                    if self.telemetry.disconnected_since_ms is not None:
                        self.book.mark_gap(self.telemetry.disconnected_since_ms, now)
                        self.telemetry.disconnected_since_ms = None
                    if attempt or self.telemetry.connects:
                        self.telemetry.reconnects += 1
                    self.telemetry.connects += 1
                    self.telemetry.connected = True
                    self.telemetry.reconnecting = False
                    attempt = 0
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(socket.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        try:
                            self.handle(json.loads(raw), int(time.time() * 1000))
                        except json.JSONDecodeError:
                            self.telemetry.malformed += 1
            except Exception as exc:  # isolation: nothing escapes this thread
                self.telemetry.last_error = f"{type(exc).__name__}: {exc}"
            if self.telemetry.connected or self.telemetry.disconnected_since_ms is None:
                self.telemetry.disconnected_since_ms = int(time.time() * 1000)
            self.telemetry.connected = False
            self.telemetry.reconnecting = not self._stop.is_set()
            attempt += 1
            end = time.time() + min(30.0, 2 ** min(attempt, 5)) + random.random()
            while not self._stop.is_set() and time.time() < end:
                await asyncio.sleep(0.2)

    def status(self, now_ms: int) -> str:
        # Binance's heartbeat is a protocol ping, invisible here, so a quiet market reads as
        # waiting rather than stale while the socket is up.
        t = self.telemetry
        alive = self._thread is not None and self._thread.is_alive()
        if not t.connected:
            return "RECONNECTING" if alive and t.reconnecting else "DISCONNECTED"
        if t.last_trade_ms is not None and now_ms - t.last_trade_ms <= _tc.TRADE_RECENT_MS:
            return "CONNECTED"
        return "CONNECTED_WAITING_FOR_TRADE"

    def view(self, *, since_ms: int | None = None, now_ms: int | None = None) -> dict[str, Any]:
        body = super().view(since_ms=since_ms, now_ms=now_ms)
        body["source"] = "BINANCE_PUBLIC_AGGTRADE_REALTIME"
        return body

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
