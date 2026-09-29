"""15 s candles: bucketing, aggregation, dedupe, ordering, gaps, and isolation from execution."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.crypto.terminal import api as terminal_api
from app.crypto.terminal import server as terminal_server
from app.crypto.terminal.trade_candles import BUCKET_MS, FINALIZE_GRACE_MS, CandleBook, TradeCandleFeed
from tests.crypto.conftest import RISK_LIMIT_PAYLOAD
from tests.crypto.test_paper_terminal_api import snapshot_message, ticker_snapshot

D = Decimal
T0 = 1_790_000_010_000  # 10 s past a 15 s boundary? 1_790_000_010_000 % 15000 == 10_000


def add(book, i, ts, p, v="0.1"):
    return book.add(exec_id=str(i), ts_ms=ts, price=D(p), size=D(v))


def test_utc_15s_buckets() -> None:
    base = T0 - T0 % BUCKET_MS
    book = CandleBook()
    add(book, 1, base, "100")
    add(book, 2, base + 14_999, "101")
    add(book, 3, base + 15_000, "102")
    snap = book.snapshot(since_ms=None, now_ms=base + 15_001)
    assert [c["start_ms"] for c in snap["candles"]] == [base]
    assert snap["candles"][0]["end_ms"] == base + 15_000
    assert snap["current"]["start_ms"] == base + 15_000
    assert base % 15_000 == 0


def test_ohlcv_volume_and_count() -> None:
    base = T0 - T0 % BUCKET_MS
    book = CandleBook()
    for i, (dt, p, v) in enumerate([(0, "100", "0.5"), (1000, "103", "0.2"), (2000, "99", "0.1"), (3000, "101", "1")]):
        add(book, i, base + dt, p, v)
    c = book.snapshot(since_ms=None, now_ms=base + 1)["current"]
    assert (c["open"], c["high"], c["low"], c["close"]) == ("100", "103", "99", "101")
    assert D(c["volume"]) == D("1.8") and c["trade_count"] == 4 and c["confirmed"] is False


def test_duplicate_exec_id_is_counted_once() -> None:
    base = T0 - T0 % BUCKET_MS
    book = CandleBook()
    assert add(book, "x", base, "100") == "ADDED"
    assert add(book, "x", base, "100") == "DUPLICATE"
    snap = book.snapshot(since_ms=None, now_ms=base)
    assert snap["current"]["trade_count"] == 1 and snap["duplicates"] == 1


def test_out_of_order_inside_bucket_follows_trade_time() -> None:
    base = T0 - T0 % BUCKET_MS
    book = CandleBook()
    add(book, 1, base + 5000, "100")
    add(book, 2, base + 1000, "90")    # earlier trade arriving later: it is the open
    add(book, 3, base + 9000, "110")
    add(book, 4, base + 7000, "95")    # not the latest: close stays 110
    c = book.snapshot(since_ms=None, now_ms=base)["current"]
    assert (c["open"], c["close"], c["low"], c["high"]) == ("90", "110", "90", "110")


def test_late_trade_for_a_finalized_bucket_is_dropped_not_rewritten() -> None:
    base = T0 - T0 % BUCKET_MS
    book = CandleBook()
    add(book, 1, base, "100")
    add(book, 2, base + 15_000, "101")          # finalizes the first bucket
    assert add(book, 3, base + 100, "50") == "LATE"
    snap = book.snapshot(since_ms=None, now_ms=base + 15_001)
    assert snap["candles"][0]["low"] == "100" and snap["late_trades"] == 1


def test_no_trade_bucket_is_not_fabricated() -> None:
    base = T0 - T0 % BUCKET_MS
    book = CandleBook()
    add(book, 1, base, "100")
    add(book, 2, base + 45_000, "101")          # two empty buckets in between
    snap = book.snapshot(since_ms=None, now_ms=base + 45_001)
    assert [c["start_ms"] for c in snap["candles"]] == [base]
    assert snap["current"]["start_ms"] == base + 45_000


def test_open_candle_finalizes_after_its_window_plus_grace() -> None:
    base = T0 - T0 % BUCKET_MS
    book = CandleBook()
    add(book, 1, base, "100")
    assert book.snapshot(since_ms=None, now_ms=base + BUCKET_MS + FINALIZE_GRACE_MS - 1)["current"] is not None
    snap = book.snapshot(since_ms=None, now_ms=base + BUCKET_MS + FINALIZE_GRACE_MS)
    assert snap["current"] is None and snap["candles"][0]["confirmed"] is True


def test_since_returns_only_new_candles_and_history_is_bounded() -> None:
    base = T0 - T0 % BUCKET_MS
    book = CandleBook()
    for k in range(1200):
        add(book, k, base + k * BUCKET_MS, "100")
    snap = book.snapshot(since_ms=None, now_ms=base + 1200 * BUCKET_MS)
    assert snap["history_size"] == 960
    last = snap["candles"][-1]["start_ms"]
    add(book, "n", base + 1200 * BUCKET_MS + BUCKET_MS, "100")
    inc = book.snapshot(since_ms=last, now_ms=base + 1202 * BUCKET_MS)
    assert [c["start_ms"] for c in inc["candles"]] == [last + BUCKET_MS]


def test_disconnect_window_marks_candles_partial() -> None:
    base = T0 - T0 % BUCKET_MS
    book = CandleBook()
    add(book, 1, base, "100")
    book.mark_gap(base + 3000, base + 8000)
    add(book, 2, base + 15_000, "100")
    snap = book.snapshot(since_ms=None, now_ms=base + 15_001)
    assert snap["candles"][0]["partial"] is True and snap["current"]["partial"] is False


def test_burst_and_malformed_messages() -> None:
    feed = TradeCandleFeed()
    base = T0 - T0 % BUCKET_MS
    burst = {"topic": "publicTrade.BTCUSDT", "data": [
        {"i": f"e{k}", "T": base + k, "p": "100.5", "v": "0.001"} for k in range(5000)]}
    started = time.perf_counter()
    feed.handle(burst, base)
    elapsed = time.perf_counter() - started
    feed.handle({"topic": "publicTrade.BTCUSDT", "data": [{"nope": 1}]}, base)
    view = feed.view(now_ms=base + 1)
    assert view["current"]["trade_count"] == 5000
    assert feed.telemetry.malformed == 1
    assert elapsed < 1.0  # 5k trades aggregate well inside a second


class _FakeSocket:
    def __init__(self, messages, then_raise=True):
        self.messages = list(messages); self.sent = []; self.then_raise = then_raise
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def send(self, payload): self.sent.append(payload)
    async def recv(self):
        if self.messages:
            return self.messages.pop(0)
        raise ConnectionError("socket closed")


def test_reconnect_after_a_dropped_socket_and_thread_isolation() -> None:
    base = int(time.time() * 1000)
    sockets = [
        _FakeSocket([json.dumps({"topic": "publicTrade.BTCUSDT", "data": [{"i": "a", "T": base, "p": "1", "v": "1"}]})]),
        _FakeSocket([json.dumps({"topic": "publicTrade.BTCUSDT", "data": [{"i": "b", "T": base + 1, "p": "2", "v": "1"}]})]),
    ]
    feed = TradeCandleFeed(connector=lambda: sockets.pop(0) if sockets else _FakeSocket([]))
    import app.crypto.terminal.trade_candles as tc
    real_random = tc.random.random
    tc.random.random = lambda: 0.0
    try:
        # drive the loop directly on a private event loop with near-zero backoff
        async def run():
            task = asyncio.create_task(feed._run())
            for _ in range(400):
                await asyncio.sleep(0.01)
                if feed.telemetry.connects >= 2 and feed.book.trades >= 2:
                    break
            feed._stop.set(); await asyncio.wait_for(task, 5)
        import app.crypto.terminal.trade_candles as mod
        orig = mod.asyncio.sleep
        asyncio.run(run())
    finally:
        tc.random.random = real_random
    assert feed.telemetry.connects >= 2 and feed.telemetry.reconnects >= 1
    assert feed.book.trades == 2
    assert feed.book.gaps, "the dropped window is recorded"


def test_thread_crash_does_not_reach_the_caller() -> None:
    def boom():
        raise RuntimeError("socket factory exploded")
    feed = TradeCandleFeed(connector=boom)
    feed._stop.clear()
    thread = threading.Thread(target=feed._thread_main, daemon=True)
    thread.start(); time.sleep(0.3); feed._stop.set(); thread.join(5)
    assert not thread.is_alive()
    assert "exploded" in (feed.telemetry.last_error or "")
    assert feed.view()["status"] == "DISCONNECTED"


# ------------------------------------------------------------------ route + execution isolation


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    risk = tmp_path / "risk.json"
    risk.write_text(json.dumps(RISK_LIMIT_PAYLOAD))
    config_path = tmp_path / "run_config.json"
    config_path.write_text(json.dumps({
        "run_id": "c15-test", "starting_capital_krw": "1000000", "fx_krw_per_usdt": "1000",
        "fx_source": "test", "fx_asof_utc": "2026-09-23T00:00:00Z", "fee_version": "test-v1",
        "fee_taker_rate": "0.0006", "fee_maker_rate": "0.0002", "fee_source": "test",
        "fee_effective_date": "2026-01-01", "slippage_model": "NONE", "slippage_bps": "0",
        "leverage": "10", "risk_limit_path": str(risk)}))
    monkeypatch.setenv(terminal_api.CONFIG_ENV, str(config_path))
    monkeypatch.setenv("CRYPTO_PAPER_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(terminal_api.BybitPublicFeed, "seed_klines", lambda self, **kw: None)
    monkeypatch.setattr(terminal_api.BybitPublicFeed, "start", lambda self: None)
    monkeypatch.setattr(terminal_api.Runtime, "start", lambda self: None)
    monkeypatch.setattr(terminal_server, "trade_feed", TradeCandleFeed())
    with TestClient(terminal_server.app) as test_client:
        terminal_api.runtime.feed.handle(snapshot_message(), 1_001)
        terminal_api.runtime.feed.handle(ticker_snapshot(), 1_001)
        terminal_api.runtime.session.start(1_000)
        terminal_api.runtime.session.observe(terminal_api.runtime.feed.quote(), force=True)
        yield test_client


def test_route_is_incremental_and_carries_status(client: TestClient) -> None:
    now = int(time.time() * 1000)
    base = now - now % BUCKET_MS - 30_000
    feed = terminal_server.trade_feed
    for k in range(3):
        feed.handle({"topic": "publicTrade.BTCUSDT", "data": [{"i": f"r{k}", "T": base + k * BUCKET_MS, "p": "100", "v": "1"}]}, now)
    body = client.get("/api/crypto/candles-15s").json()
    assert len(body["candles"]) >= 2 and body["research_canonical"] is False
    assert body["status"] == "DISCONNECTED"  # no live socket in tests: the UI must say so
    last = body["candles"][-1]["start_ms"]
    assert client.get(f"/api/crypto/candles-15s?since_ms={last}").json()["candles"] == []


def test_dead_trade_feed_leaves_execution_untouched(client: TestClient) -> None:
    terminal_api.runtime.feed.telemetry.connected = True  # the execution socket is up
    before = client.get("/api/crypto/state").json()
    feed = terminal_server.trade_feed
    feed.telemetry.connected = False                      # the trade socket dies
    feed.telemetry.last_error = "ConnectionError: gone"
    state = client.get("/api/crypto/state").json()
    assert state["feed"]["connected"] is True and state["quote"] == before["quote"]
    assert client.get("/api/crypto/sizing?side=LONG").status_code == 200
    assert client.get("/api/crypto/live").status_code == 200
    session = terminal_api.runtime.session
    session.command({"command": "ORDER", "ts_ms": session.engine.quote.ts_ms, "side": "LONG",
                     "qty": "0.010", "intent": "OPEN", "request_id": "iso-1", "reason": "MANUAL"})
    assert session.engine.account.position.signed_qty == D("0.010")
    assert client.get("/api/crypto/candles-15s").json()["status"] == "DISCONNECTED"


def test_trade_feed_shares_no_object_with_the_execution_feed() -> None:
    feed = terminal_server.trade_feed
    execution = terminal_api.runtime.feed
    assert feed.book is not getattr(execution, "book", None)
    assert not hasattr(execution, "trade_feed")
    assert feed.topic == "publicTrade.BTCUSDT"
    from app.crypto.terminal import feed as execution_feed
    assert not any("publicTrade" in topic for topic in execution_feed.TOPICS)


# ------------------------------------------------------------------ liveness vs market activity


class _AliveThread:
    def is_alive(self): return True


def _feed_at(now, *, connected=True, last_msg=None, last_trade=None, reconnecting=False):
    feed = TradeCandleFeed()
    feed._thread = _AliveThread()
    t = feed.telemetry
    t.connected, t.last_message_ms, t.last_trade_ms, t.reconnecting = connected, last_msg, last_trade, reconnecting
    return feed.status(now)


def test_quiet_market_is_not_a_connection_problem() -> None:
    now = 1_000_000
    assert _feed_at(now, last_msg=now - 1_000, last_trade=now - 3_000) == "CONNECTED"
    # No trade for 40 s, but a pong 4 s ago: healthy, just quiet.
    assert _feed_at(now, last_msg=now - 4_000, last_trade=now - 40_000) == "CONNECTED_WAITING_FOR_TRADE"
    # Neither trade nor pong for 26 s while the socket claims to be up: suspect.
    assert _feed_at(now, last_msg=now - 26_000, last_trade=now - 60_000) == "STALE"
    assert _feed_at(now, connected=False, reconnecting=True) == "RECONNECTING"
    assert TradeCandleFeed().status(now) == "DISCONNECTED"


def test_pong_is_a_heartbeat_not_a_trade() -> None:
    feed = TradeCandleFeed()
    feed.handle({"success": True, "ret_msg": "pong", "op": "ping"}, 5_000)
    assert feed.telemetry.pongs == 1 and feed.telemetry.last_message_ms == 5_000
    assert feed.telemetry.last_trade_ms is None and feed.book.trades == 0


def test_silent_socket_gets_pinged_then_rebuilt(monkeypatch) -> None:
    import app.crypto.terminal.trade_candles as tc
    monkeypatch.setattr(tc, "PING_AFTER_S", 0.05)
    monkeypatch.setattr(tc, "DEAD_AFTER_S", 0.3)
    monkeypatch.setattr(tc.random, "random", lambda: 0.0)

    class Silent(_FakeSocket):
        async def recv(self):
            await asyncio.sleep(10)

    sockets = [Silent([])]
    feed = TradeCandleFeed(connector=lambda: sockets.pop(0) if sockets else _FakeSocket([]))

    async def run():
        task = asyncio.create_task(feed._run())
        for _ in range(300):
            await asyncio.sleep(0.01)
            if feed.telemetry.connects >= 2:
                break
        feed._stop.set()
        await asyncio.wait_for(task, 5)
    asyncio.run(run())
    assert feed.telemetry.pings >= 2
    assert "no trade or pong" in (feed.telemetry.last_error or "") or feed.telemetry.connects >= 2
