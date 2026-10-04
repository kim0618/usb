"""Bybit PUBLIC market feed for the terminal.

Read-only by construction: it subscribes to public topics and holds no credentials. The local
book follows D2's discipline - a continuity gap discards the book and resubscribes, because
Bybit only resends a snapshot on a fresh subscribe and a silently stalled book would quietly
feed stale quotes into fills.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

import httpx
import websockets

from ..orderbook import BookGap, LocalOrderBook
from ..paper.book import BookSide, Quote

WS_URL = "wss://stream.bybit.com/v5/public/linear"
REST_URL = "https://api.bybit.com"
SYMBOL = "BTCUSDT"
BOOK_TOPIC = f"orderbook.50.{SYMBOL}"
TOPICS = (BOOK_TOPIC, f"tickers.{SYMBOL}", f"kline.1.{SYMBOL}")
BOOK_DEPTH = 5


def book_topic(symbol: str) -> str:
    return f"orderbook.50.{symbol}"


def topics_for(symbol: str) -> tuple[str, ...]:
    """The three public topics one instrument needs: its book, its ticker, its 1m candles.

    Derived rather than listed so a feed built for ETHUSDT cannot subscribe to a BTCUSDT topic
    by having been handed a stale tuple. The module-level `TOPICS` is kept because it is the
    default symbol's and existing callers read it.
    """
    return (book_topic(symbol), f"tickers.{symbol}", f"kline.1.{symbol}")


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


@dataclass
class BybitPublicFeed:
    """Owns the live view. `quote()` is the single price authority the engine ever sees.

    One feed is one instrument: its own socket, its own book, its own ticker and its own
    candles. A multi-symbol terminal runs one of these per symbol rather than one feed that
    tracks three, because the book's continuity rule (a sequence gap discards the book and
    resubscribes) is per instrument, and sharing one socket would mean an ETH gap throwing
    away the BTC book.
    """
    #: The instrument this feed is about. Fixed at construction; every topic is derived from it.
    symbol: str = SYMBOL
    book: LocalOrderBook = field(default_factory=LocalOrderBook)
    ticker: dict[str, Any] = field(default_factory=dict)
    telemetry: FeedTelemetry = field(default_factory=FeedTelemetry)
    klines: list[dict[str, Any]] = field(default_factory=list)
    last_ticker_ms: int | None = None
    _needs_resync: bool = False
    _task: asyncio.Task | None = None
    _stop: asyncio.Event | None = None
    _socket: Any = None
    faults_injected: int = 0

    # ------------------------------------------------------------------ view

    def quote(self) -> Quote | None:
        """None until both a book and a mark price have arrived. A partial view is not a quote."""
        mark = self.ticker.get("markPrice")
        if mark is None:
            return None
        bids = [[str(price), str(qty)] for price, qty in sorted(self.book.bids.items(), reverse=True)[:BOOK_DEPTH]]
        asks = [[str(price), str(qty)] for price, qty in sorted(self.book.asks.items())[:BOOK_DEPTH]]
        ts = self.last_ticker_ms or int(time.time() * 1000)
        return Quote(
            ts_ms=ts,
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
            # Named so a screen can check that the feed it is reading is the symbol it asked
            # for, instead of trusting that the request routed correctly.
            "symbol": self.symbol,
            "connected": self.telemetry.connected,
            "connects": self.telemetry.connects,
            "reconnects": self.telemetry.reconnects,
            "book_gaps": self.telemetry.book_gaps,
            "book_resyncs": self.telemetry.book_resyncs,
            "malformed": self.telemetry.malformed,
            "messages": self.telemetry.messages,
            "last_message_ms": self.telemetry.last_message_ms,
            "last_error": self.telemetry.last_error,
            "book_ready": self.book.ready,
            "faults_injected": self.faults_injected,
            "ticker_fields": len(self.ticker),
            "funding_cap": self.ticker.get("fundingCap"),
            "open_interest": self.ticker.get("openInterest"),
        }

    # ------------------------------------------------------------------ seed

    def seed_klines(self, *, limit: int = 120, client: httpx.Client | None = None) -> None:
        """One REST read so the chart is not empty for the first two minutes."""
        owned = client is None
        http = client or httpx.Client(base_url=REST_URL, timeout=20,
                                      headers={"User-Agent": "usb-crypto-d3-terminal/1"})
        try:
            response = http.get("/v5/market/kline", params={
                "category": "linear", "symbol": self.symbol, "interval": "1", "limit": limit})
            response.raise_for_status()
            payload = response.json()
            if payload.get("retCode") != 0:
                raise RuntimeError(f"kline seed failed: {payload.get('retCode')} {payload.get('retMsg')}")
            rows = payload["result"]["list"]
            self.klines = [
                {"start_ms": int(row[0]), "open": row[1], "high": row[2], "low": row[3],
                 "close": row[4], "volume": row[5], "confirmed": True}
                for row in sorted(rows, key=lambda item: int(item[0]))
            ]
        finally:
            if owned:
                http.close()

    # ------------------------------------------------------------------ messages

    def handle(self, message: dict[str, Any], received_ms: int) -> None:
        self.telemetry.messages += 1
        self.telemetry.last_message_ms = received_ms
        topic = str(message.get("topic", ""))
        data = message.get("data")
        try:
            if topic.startswith("orderbook."):
                try:
                    self.book.apply(str(message["type"]), data)
                except BookGap:
                    self.telemetry.book_gaps += 1
                    self._needs_resync = True
            elif topic.startswith("tickers."):
                kind = str(message["type"])
                if kind == "snapshot":
                    self.ticker = dict(data)
                elif not self.ticker:
                    self.telemetry.ticker_deltas_without_snapshot += 1
                    return
                else:
                    self.ticker.update(data)
                self.last_ticker_ms = int(message["ts"])
            elif topic.startswith("kline."):
                for bar in data:
                    self._merge_bar(bar)
        except (KeyError, TypeError, ValueError):
            self.telemetry.malformed += 1

    def _merge_bar(self, bar: dict[str, Any]) -> None:
        entry = {"start_ms": int(bar["start"]), "open": bar["open"], "high": bar["high"],
                 "low": bar["low"], "close": bar["close"], "volume": bar["volume"],
                 "confirmed": bar.get("confirm") is True}
        if self.klines and self.klines[-1]["start_ms"] == entry["start_ms"]:
            self.klines[-1] = entry
        else:
            self.klines.append(entry)
        del self.klines[:-600]

    def on_disconnect(self) -> None:
        self.book.reset()
        self.ticker.clear()
        self._needs_resync = False
        self.telemetry.connected = False

    # ------------------------------------------------------------------ loop

    async def run(self, *, connector: Callable[[], Any] | None = None,
                  backoff: Callable[[int], float] | None = None) -> None:
        self._stop = asyncio.Event()
        attempt = 0
        while not self._stop.is_set():
            try:
                factory = connector or (lambda: websockets.connect(
                    WS_URL, ping_interval=20, ping_timeout=20, max_queue=256))
                async with factory() as socket:
                    self._socket = socket
                    if attempt:
                        self.telemetry.reconnects += 1
                    self.on_disconnect()
                    self.telemetry.connects += 1
                    self.telemetry.connected = True
                    attempt = 0
                    await socket.send(json.dumps({"op": "subscribe",
                                                  "args": list(topics_for(self.symbol))}))
                    while not self._stop.is_set():
                        raw = await asyncio.wait_for(socket.recv(), timeout=30)
                        try:
                            self.handle(json.loads(raw), int(time.time() * 1000))
                        except json.JSONDecodeError:
                            self.telemetry.malformed += 1
                        if self._needs_resync:
                            topic = book_topic(self.symbol)
                            await socket.send(json.dumps({"op": "unsubscribe", "args": [topic]}))
                            await socket.send(json.dumps({"op": "subscribe", "args": [topic]}))
                            self._needs_resync = False
                            self.telemetry.book_resyncs += 1
            except (OSError, websockets.ConnectionClosed, asyncio.TimeoutError) as exc:
                self._socket = None
                self.telemetry.last_error = f"{type(exc).__name__}: {exc}"
                self.on_disconnect()
                attempt += 1
                delay = backoff(attempt) if backoff else min(30.0, 2 ** min(attempt, 5)) + random.random()
                if not self._stop.is_set() and delay:
                    await asyncio.sleep(delay)
        self.on_disconnect()

    async def inject_disconnect(self) -> bool:
        """Close the live socket so the reconnect path runs for real.

        This closes the transport rather than resetting local state: a soak that only cleared
        the book would prove nothing about resubscribing, backoff or the snapshot that follows.
        """
        socket = self._socket
        if socket is None:
            return False
        self.faults_injected += 1
        await socket.close()
        return True

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
