"""15-second candles from Bybit publicTrade, for the manual terminal's chart only.

Not research data and not an execution input. The research canonical timeframe stays 1m; nothing
here is persisted, and nothing here is read by the paper engine, the sizing probe or a fill.

Isolation is the design constraint. The execution quote comes from `BybitPublicFeed`: one socket
carrying the order book and the ticker, drained by one loop. A trade burst on that socket would
queue behind - or ahead of - the book deltas the fills depend on. So trades get:

* their **own socket** (a second connection subscribed to `publicTrade.<symbol>` only),
* their **own thread and event loop**, so a burst, a hang or an exception here cannot delay a
  single await on the execution loop, and a crash ends this thread only,
* their **own reconnect/backoff and telemetry**, independent of the main feed's.

The only shared object is `CandleBook`, guarded by a lock that is held for a few dict updates.

Bucketing: UTC 15 s, `start = floor(T / 15000) * 15000` on the exchange trade time `T`. A candle is
open while trades for its bucket can still arrive; it is finalized, and never changed again, when a
trade for a later bucket arrives or when the wall clock passes its end by `FINALIZE_GRACE_MS`. A
trade for an already finalized bucket is counted as late and dropped. A bucket with no trades has
no candle: nothing is fabricated. A candle whose window overlaps a disconnect is marked `partial`.
"""
from __future__ import annotations

import asyncio
import json
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

import websockets

WS_URL = "wss://stream.bybit.com/v5/public/linear"
BUCKET_MS = 15_000
FINALIZE_GRACE_MS = 2_000
HISTORY = 960          # finalized candles kept: 4 hours
SEEN_IDS = 50_000      # execId dedupe window
# Liveness is not "a trade arrived recently": a quiet market can go many seconds without one.
# When the socket has been silent for PING_AFTER_S we send Bybit's application ping; its pong
# is a message, so a healthy connection always has a fresh heartbeat. Silence past DEAD_AFTER_S
# (no trade and no pong) means the connection is gone and it is rebuilt.
PING_AFTER_S = 10
DEAD_AFTER_S = 30
HEARTBEAT_STALE_MS = 25_000     # two missed pings: the connection is suspect
TRADE_RECENT_MS = BUCKET_MS     # a trade inside the last bucket reads as active trading


@dataclass
class Candle:
    start_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int
    open_trade_ms: int
    close_trade_ms: int
    confirmed: bool = False
    partial: bool = False

    def view(self) -> dict[str, Any]:
        return {"start_ms": self.start_ms, "end_ms": self.start_ms + BUCKET_MS,
                "open": str(self.open), "high": str(self.high), "low": str(self.low),
                "close": str(self.close), "volume": str(self.volume),
                "trade_count": self.trade_count, "confirmed": self.confirmed, "partial": self.partial}


@dataclass
class CandleBook:
    """The aggregator. Pure and clock-free except for the `now_ms` callers pass in."""
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY))
    current: Candle | None = None
    seen: set = field(default_factory=set)
    seen_order: deque = field(default_factory=deque)
    duplicates: int = 0
    late_trades: int = 0
    trades: int = 0
    gaps: list = field(default_factory=list)   # (from_ms, to_ms) windows with no connection
    lock: threading.Lock = field(default_factory=threading.Lock)

    def _remember(self, exec_id: str) -> bool:
        if exec_id in self.seen:
            return False
        self.seen.add(exec_id)
        self.seen_order.append(exec_id)
        if len(self.seen_order) > SEEN_IDS:
            self.seen.discard(self.seen_order.popleft())
        return True

    def _finalize(self) -> None:
        candle = self.current
        if candle is None:
            return
        candle.confirmed = True
        end = candle.start_ms + BUCKET_MS
        candle.partial = candle.partial or any(start < end and stop > candle.start_ms
                                               for start, stop in self.gaps)
        self.history.append(candle)
        self.current = None

    def add(self, *, exec_id: str, ts_ms: int, price: Decimal, size: Decimal) -> str:
        """Returns what happened to the trade: ADDED / DUPLICATE / LATE."""
        with self.lock:
            if not self._remember(exec_id):
                self.duplicates += 1
                return "DUPLICATE"
            start = ts_ms - ts_ms % BUCKET_MS
            last_final = self.history[-1].start_ms if self.history else None
            if (last_final is not None and start <= last_final) or (
                    self.current is not None and start < self.current.start_ms):
                self.late_trades += 1
                return "LATE"
            if self.current is not None and start > self.current.start_ms:
                self._finalize()
            self.trades += 1
            c = self.current
            if c is None:
                self.current = Candle(start_ms=start, open=price, high=price, low=price, close=price,
                                      volume=size, trade_count=1, open_trade_ms=ts_ms,
                                      close_trade_ms=ts_ms)
                return "ADDED"
            # Out of order inside the open bucket: open/close follow trade time, not arrival.
            if ts_ms < c.open_trade_ms:
                c.open, c.open_trade_ms = price, ts_ms
            if ts_ms >= c.close_trade_ms:
                c.close, c.close_trade_ms = price, ts_ms
            c.high = max(c.high, price)
            c.low = min(c.low, price)
            c.volume += size
            c.trade_count += 1
            return "ADDED"

    def tick(self, now_ms: int) -> None:
        """Close the open candle once its window is over and the grace for late trades passed."""
        with self.lock:
            if self.current is not None and now_ms >= self.current.start_ms + BUCKET_MS + FINALIZE_GRACE_MS:
                self._finalize()

    def mark_gap(self, from_ms: int, to_ms: int) -> None:
        with self.lock:
            self.gaps.append((from_ms, to_ms))
            del self.gaps[:-100]
            if self.current is not None and self.current.start_ms < to_ms:
                self.current.partial = True

    def snapshot(self, *, since_ms: int | None, now_ms: int) -> dict[str, Any]:
        self.tick(now_ms)
        with self.lock:
            rows = [c.view() for c in self.history if since_ms is None or c.start_ms > since_ms]
            current = self.current.view() if self.current is not None else None
            return {"candles": rows, "current": current, "history_size": len(self.history),
                    "duplicates": self.duplicates, "late_trades": self.late_trades, "trades": self.trades}


@dataclass
class TradeTelemetry:
    connected: bool = False
    connects: int = 0
    reconnects: int = 0
    messages: int = 0
    malformed: int = 0
    last_message_ms: int | None = None
    last_error: str | None = None
    started_ms: int | None = None
    disconnected_since_ms: int | None = None
    # Exchange trade time to our receipt, last message and a slow-moving average (candle build
    # latency is this plus a few microseconds of aggregation).
    last_lag_ms: int | None = None
    avg_lag_ms: float | None = None
    last_trade_ms: int | None = None      # our receive time of the last trade message
    pings: int = 0
    pongs: int = 0
    reconnecting: bool = False


class TradeCandleFeed:
    """Second, trade-only connection on its own thread. Never touches the execution feed."""

    def __init__(self, symbol: str = "BTCUSDT", *, book: CandleBook | None = None,
                 connector: Callable[[], Any] | None = None) -> None:
        self.symbol = symbol
        self.topic = f"publicTrade.{symbol}"
        self.book = book or CandleBook()
        self.telemetry = TradeTelemetry()
        self._connector = connector
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ messages

    def handle(self, message: dict[str, Any], received_ms: int) -> None:
        self.telemetry.messages += 1
        self.telemetry.last_message_ms = received_ms
        if message.get("op") == "ping" or message.get("ret_msg") == "pong":
            self.telemetry.pongs += 1
            return
        if not str(message.get("topic", "")).startswith("publicTrade."):
            return
        self.telemetry.last_trade_ms = received_ms
        try:
            newest = None
            for trade in message["data"]:
                ts = int(trade["T"])
                newest = ts if newest is None else max(newest, ts)
                self.book.add(exec_id=str(trade["i"]), ts_ms=ts,
                              price=Decimal(str(trade["p"])), size=Decimal(str(trade["v"])))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            self.telemetry.malformed += 1
            return
        if newest is not None:
            lag = received_ms - newest
            t = self.telemetry
            t.last_lag_ms = lag
            t.avg_lag_ms = lag if t.avg_lag_ms is None else t.avg_lag_ms * 0.95 + lag * 0.05

    # ------------------------------------------------------------------ loop

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                factory = self._connector or (lambda: websockets.connect(
                    WS_URL, ping_interval=20, ping_timeout=20, max_queue=1024))
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
                    await socket.send(json.dumps({"op": "subscribe", "args": [self.topic]}))
                    last_rx = time.time()
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(socket.recv(), timeout=PING_AFTER_S)
                        except asyncio.TimeoutError:
                            if time.time() - last_rx > DEAD_AFTER_S:
                                raise ConnectionError(f"no trade or pong for {DEAD_AFTER_S}s")
                            await socket.send(json.dumps({"op": "ping"}))
                            self.telemetry.pings += 1
                            continue
                        last_rx = time.time()
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
            delay = min(30.0, 2 ** min(attempt, 5)) + random.random()
            end = time.time() + delay
            while not self._stop.is_set() and time.time() < end:
                await asyncio.sleep(0.2)

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        try:
            self._loop.run_until_complete(self._run())
        finally:
            self._loop.close()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self.telemetry.started_ms = int(time.time() * 1000)
        self._thread = threading.Thread(target=self._thread_main, name="trade-candles", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    # ------------------------------------------------------------------ view

    def status(self, now_ms: int) -> str:
        """Connection health and market activity, kept apart.

        CONNECTED                   socket up, heartbeat fresh, a trade within the last bucket
        CONNECTED_WAITING_FOR_TRADE socket up, heartbeat fresh, the market is simply quiet
        STALE                       socket reported up but neither trade nor pong for 25 s
        RECONNECTING                socket down, the loop is rebuilding it
        DISCONNECTED                the feed is not running at all
        """
        t = self.telemetry
        alive = self._thread is not None and self._thread.is_alive()
        if not t.connected:
            return "RECONNECTING" if alive and t.reconnecting else "DISCONNECTED"
        if t.last_message_ms is None or now_ms - t.last_message_ms > HEARTBEAT_STALE_MS:
            return "STALE"
        if t.last_trade_ms is not None and now_ms - t.last_trade_ms <= TRADE_RECENT_MS:
            return "CONNECTED"
        return "CONNECTED_WAITING_FOR_TRADE"

    def view(self, *, since_ms: int | None = None, now_ms: int | None = None) -> dict[str, Any]:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        body = self.book.snapshot(since_ms=since_ms, now_ms=now)
        t = self.telemetry
        body.update({
            "timeframe": "15s", "bucket_ms": BUCKET_MS, "server_time_ms": now,
            "status": self.status(now),
            "feed": {"connected": t.connected, "connects": t.connects, "reconnects": t.reconnects,
                     "messages": t.messages, "malformed": t.malformed, "last_message_ms": t.last_message_ms,
                     "last_error": t.last_error, "started_ms": t.started_ms,
                     "last_lag_ms": t.last_lag_ms, "last_trade_ms": t.last_trade_ms,
                     "pings": t.pings, "pongs": t.pongs,
                     "avg_lag_ms": round(t.avg_lag_ms, 1) if t.avg_lag_ms is not None else None,
                     "thread_alive": self._thread is not None and self._thread.is_alive()},
            "coverage_from_ms": t.started_ms,
            "source": "BYBIT_PUBLIC_TRADE_REALTIME",
            "research_canonical": False,
        })
        return body
