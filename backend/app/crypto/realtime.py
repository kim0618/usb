from __future__ import annotations

import asyncio
import json
import random
import signal
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import websockets

from .orderbook import BookGap, LocalOrderBook
from .storage import JsonlDatasetWriter, stream_manifest_entry, write_stream_manifest


TOPICS = ("publicTrade.BTCUSDT", "orderbook.50.BTCUSDT", "kline.1.BTCUSDT", "tickers.BTCUSDT")
BOOK_TOPIC = "orderbook.50.BTCUSDT"

# Merged ticker state is written on every message, so the field list is fixed and schema-versioned.
TICKER_FIELDS = ("symbol", "lastPrice", "markPrice", "indexPrice", "fundingRate", "fundingCap",
                 "fundingIntervalHour", "nextFundingTime", "openInterest", "openInterestValue",
                 "volume24h", "turnover24h", "bid1Price", "bid1Size", "ask1Price", "ask1Size")


@dataclass
class RealtimeTelemetry:
    reconnects: int = 0
    malformed: int = 0
    book_gaps: int = 0
    duplicate_trades: int = 0
    book_resyncs: int = 0
    ticker_deltas_without_snapshot: int = 0
    messages: int = 0


class RealtimeProcessor:
    def __init__(self, root: Path, session_id: int | None = None) -> None:
        self.root = root; self.book = LocalOrderBook(); self.telemetry = RealtimeTelemetry()
        self.trade_ids: set[str] = set(); self.last_feature_second: int | None = None
        self.ticker_state: dict[str, Any] = {}
        self.ranges: dict[str, list[int]] = {}
        self.needs_book_resync = False
        # One session id for all four writers so a run's outputs stay correlatable.
        self.session_id = int(time.time()) if session_id is None else session_id
        self.trades = JsonlDatasetWriter(root / "realtime" / "public_trade" / f"trades_{self.session_id}.jsonl")
        self.features = JsonlDatasetWriter(root / "features" / "orderbook_1s" / f"features_{self.session_id}.jsonl")
        self.klines = JsonlDatasetWriter(root / "features" / "finalized_kline" / f"klines_{self.session_id}.jsonl")
        self.tickers = JsonlDatasetWriter(root / "features" / "ticker_state" / f"tickers_{self.session_id}.jsonl")

    def observe(self, stream: str, timestamp_ms: int) -> None:
        span = self.ranges.get(stream)
        if span is None: self.ranges[stream] = [timestamp_ms, timestamp_ms]
        else: span[0] = min(span[0], timestamp_ms); span[1] = max(span[1], timestamp_ms)

    def on_reconnect(self) -> None:
        # Both the book and the ticker are snapshot+delta streams; a reconnect invalidates each.
        self.book.reset(); self.ticker_state.clear(); self.needs_book_resync = False

    def process(self, message: dict[str, Any], received_ms: int) -> None:
        self.telemetry.messages += 1
        topic = str(message.get("topic", "")); data = message.get("data")
        try:
            if topic.startswith("publicTrade."):
                for trade in data:
                    exec_id = str(trade["i"])
                    if exec_id in self.trade_ids: self.telemetry.duplicate_trades += 1; continue
                    self.trade_ids.add(exec_id)
                    self.trades.append([{**trade, "exec_id": exec_id, "message_timestamp_ms": int(message["ts"]), "receive_timestamp_ms": received_ms, "lag_ms": received_ms - int(message["ts"]), "schema_version": "crypto.trade.v1"}])
                    self.observe("realtime_public_trade", int(message["ts"]))
            elif topic.startswith("orderbook."):
                try: self.book.apply(str(message["type"]), data)
                except BookGap:
                    # Bybit only re-sends a snapshot on a fresh subscribe, so a gap must trigger resync.
                    self.telemetry.book_gaps += 1; self.needs_book_resync = True; return
                second = int(message["ts"]) // 1000
                if second != self.last_feature_second:
                    self.features.append([{**self.book.features(int(message["ts"]), received_ms), "schema_version": "crypto.orderbook_feature.v1"}]); self.last_feature_second = second
                    self.observe("orderbook_1s", int(message["ts"]))
            elif topic.startswith("kline."):
                confirmed = [{**bar, "message_timestamp_ms": int(message["ts"]), "receive_timestamp_ms": received_ms, "schema_version": "crypto.kline.final.v1"} for bar in data if bar.get("confirm") is True]
                if confirmed:
                    self.klines.append(confirmed); self.observe("finalized_kline", int(message["ts"]))
            elif topic.startswith("tickers."):
                message_type = str(message["type"])
                if message_type == "snapshot": self.ticker_state = dict(data)
                elif not self.ticker_state:
                    # A delta before any snapshot cannot be merged into state; never write a hollow row.
                    self.telemetry.ticker_deltas_without_snapshot += 1; return
                else: self.ticker_state.update(data)
                self.tickers.append([{key: self.ticker_state.get(key) for key in TICKER_FIELDS} | {
                    "message_type": message_type, "changed_fields": sorted(data),
                    "message_timestamp_ms": int(message["ts"]), "receive_timestamp_ms": received_ms,
                    "schema_version": "crypto.ticker.v2"}])
                self.observe("ticker_state", int(message["ts"]))
        except (KeyError, TypeError, ValueError): self.telemetry.malformed += 1


async def run_realtime(root: Path, *, duration_seconds: float | None = None, connector: Callable[[], Any] | None = None, backoff: Callable[[int], float] | None = None) -> RealtimeTelemetry:
    processor = RealtimeProcessor(root); stop = asyncio.Event()
    session_start_ms = int(time.time() * 1000)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, stop.set)
        except NotImplementedError: pass
    started = time.monotonic(); attempt = 0
    while not stop.is_set() and (duration_seconds is None or time.monotonic() - started < duration_seconds):
        try:
            factory = connector or (lambda: websockets.connect("wss://stream.bybit.com/v5/public/linear", ping_interval=20, ping_timeout=20))
            async with factory() as ws:
                if attempt: processor.telemetry.reconnects += 1; processor.on_reconnect()
                attempt = 0
                await ws.send(json.dumps({"op": "subscribe", "args": list(TOPICS)}))
                while not stop.is_set():
                    remaining = None if duration_seconds is None else duration_seconds - (time.monotonic() - started)
                    if remaining is not None and remaining <= 0: stop.set(); break
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(30, remaining) if remaining else 30)
                    try: processor.process(json.loads(raw), int(time.time() * 1000))
                    except json.JSONDecodeError: processor.telemetry.malformed += 1
                    if processor.needs_book_resync:
                        await ws.send(json.dumps({"op": "unsubscribe", "args": [BOOK_TOPIC]}))
                        await ws.send(json.dumps({"op": "subscribe", "args": [BOOK_TOPIC]}))
                        processor.needs_book_resync = False; processor.telemetry.book_resyncs += 1
        except (OSError, websockets.ConnectionClosed, asyncio.TimeoutError):
            attempt += 1
            delay = backoff(attempt) if backoff else min(30, 2 ** min(attempt, 5)) + random.random()
            if not stop.is_set() and delay: await asyncio.sleep(delay)
    session_end_ms = int(time.time() * 1000)
    streams = (("realtime_public_trade", processor.trades), ("orderbook_1s", processor.features),
               ("finalized_kline", processor.klines), ("ticker_state", processor.tickers))
    telemetry = asdict(processor.telemetry)
    for dataset, writer in streams:
        row_count, checksum = writer.finalize()
        span = processor.ranges.get(dataset)
        write_stream_manifest(root, stream_manifest_entry(
            dataset=dataset, session_id=processor.session_id, session_start_ms=session_start_ms,
            session_end_ms=session_end_ms, actual_start_ms=span[0] if span else None,
            actual_end_ms=span[1] if span else None,
            relative_path=writer.final_path.relative_to(root).as_posix(),
            row_count=row_count, checksum=checksum, telemetry=telemetry))
    return processor.telemetry
