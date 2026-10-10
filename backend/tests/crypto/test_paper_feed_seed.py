"""Chart history on a lazily created symbol feed.

The defect this is about: `seed_klines` was called in the lifespan on the default symbol only,
and `feed_for` started ETHUSDT's and SOLUSDT's sockets without ever seeding them. After a
service restart BTCUSDT showed a full window immediately while the other two began at one bar
and took two hours of live pushes to catch up. Nothing was wrong with the data; the history was
simply never fetched.

What is proved here:

* the seed fills history and never overwrites bars this process observed, so it is safe to run
  against a feed that is already live (which a lazily created one always is);
* all three symbols have the same contract: the first look returns a full window;
* the seed happens exactly once per symbol per process, under concurrency and under repeated
  tab switching, and the default symbol's lifespan seed is not repeated by the first request;
* a failed seed leaves the feed alive on live bars, says so rather than looking like a thin
  chart, and is retried on the next look;
* the paged chart-history store and the tape/compression paths are untouched.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.crypto.terminal import api as terminal_api
from app.crypto.terminal.feed import BybitPublicFeed
from tests.crypto.conftest import RISK_LIMIT_PAYLOAD


# ---------------------------------------------------------------- helpers

def kline_payload(bars: list[tuple[int, str]]) -> dict:
    """Bybit's shape, newest first, which is how the exchange actually returns it."""
    return {"retCode": 0, "retMsg": "OK", "result": {"list": [
        [str(start), "1", "9", "0", close, "10", "0"] for start, close in reversed(bars)]}}


def seeding_client(bars: list[tuple[int, str]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=kline_payload(bars))
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://kline.test")


def live_bar(start_ms: int, close: str, *, confirm: bool = True) -> dict:
    return {"start": start_ms, "open": "1", "high": "9", "low": "0", "close": close,
            "volume": "10", "confirm": confirm}


class CountingSeed:
    """A stand-in for the REST read that records how many times it actually ran."""

    def __init__(self, *, bars: int = 120, fail_times: int = 0, delay_s: float = 0.0) -> None:
        self.bars, self.delay_s = bars, delay_s
        self.remaining_failures = fail_times
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, feed: BybitPublicFeed, **_: object) -> None:
        with self._lock:
            self.calls.append(feed.symbol)
            fail = self.remaining_failures > 0
            if fail:
                self.remaining_failures -= 1
        if self.delay_s:
            time.sleep(self.delay_s)
        if fail:
            raise RuntimeError(f"kline seed refused for {feed.symbol}")
        seeded = [{"start_ms": i * 60_000, "open": "1", "high": "9", "low": "0",
                   "close": str(1000 + i), "volume": "10", "confirmed": True}
                  for i in range(self.bars)]
        if feed.klines:
            oldest = feed.klines[0]["start_ms"]
            feed.klines = [b for b in seeded if b["start_ms"] < oldest] + feed.klines
        else:
            feed.klines = seeded

    def count(self, symbol: str) -> int:
        return self.calls.count(symbol)


@pytest.fixture(autouse=True)
def _isolate_the_module_runtime():
    """`api.runtime` is a module-level singleton and `create_app` builds against it.

    Neither `build` nor `stop` clears the lazily created symbol feeds - that is pre-existing and
    harmless in production, where one process means one build - but across tests it means the
    second file to ask for ETHUSDT finds the first file's feed and seed record still there. The
    records are cleared around each test rather than in the runtime, because making `stop`
    forget a history that is still sitting in the feed object would be a worse lie than this.
    """
    for store in (terminal_api.runtime.feeds, terminal_api.runtime._seeds):
        store.clear()
    terminal_api.runtime._seeded.clear()
    yield
    for store in (terminal_api.runtime.feeds, terminal_api.runtime._seeds):
        store.clear()
    terminal_api.runtime._seeded.clear()


@pytest.fixture
def seed(monkeypatch) -> CountingSeed:
    counter = CountingSeed()
    monkeypatch.setattr(BybitPublicFeed, "start", lambda self: None)
    # A plain callable set as a class attribute is not a descriptor and would never
    # receive `self`, so it is bound explicitly, as the repo's other fixtures do.
    monkeypatch.setattr(BybitPublicFeed, "seed_klines",
                        lambda self, **kw: counter(self, **kw))
    return counter


@pytest.fixture
def runtime(seed: CountingSeed) -> terminal_api.Runtime:
    """A runtime with no prior memory, which is what a restart hands the process."""
    return terminal_api.Runtime()


# ---------------------------------------------------------------- the seed itself

def test_the_seed_becomes_the_history_when_the_feed_has_none() -> None:
    feed = BybitPublicFeed(symbol="ETHUSDT")
    with seeding_client([(i * 60_000, str(100 + i)) for i in range(120)]) as client:
        feed.seed_klines(client=client)
    assert len(feed.klines) == 120
    assert [bar["start_ms"] for bar in feed.klines] == [i * 60_000 for i in range(120)]
    assert feed.klines[-1]["close"] == "219"


def test_the_seed_fills_history_without_overwriting_bars_the_socket_already_delivered() -> None:
    """A lazily created feed is always already running when its chart is first asked for."""
    feed = BybitPublicFeed(symbol="ETHUSDT")
    # Three live bars, the newest still open, exactly the state a restart leaves.
    feed.handle({"topic": "kline.1.ETHUSDT", "type": "snapshot", "ts": 1,
                 "data": [live_bar(117 * 60_000, "LIVE-117"),
                          live_bar(118 * 60_000, "LIVE-118"),
                          live_bar(119 * 60_000, "LIVE-119", confirm=False)]}, 1)
    assert len(feed.klines) == 3

    with seeding_client([(i * 60_000, f"REST-{i}") for i in range(120)]) as client:
        feed.seed_klines(client=client)

    assert len(feed.klines) == 120
    starts = [bar["start_ms"] for bar in feed.klines]
    assert starts == sorted(starts) and len(starts) == len(set(starts))
    # The observed bars are still the observed ones; only the gap in front was filled.
    assert [bar["close"] for bar in feed.klines[-3:]] == ["LIVE-117", "LIVE-118", "LIVE-119"]
    assert feed.klines[-1]["confirmed"] is False, "the open bar must not be marked confirmed"
    assert feed.klines[0]["close"] == "REST-0"


def test_seeding_twice_does_not_duplicate_or_reorder_anything() -> None:
    feed = BybitPublicFeed(symbol="SOLUSDT")
    bars = [(i * 60_000, str(i)) for i in range(120)]
    with seeding_client(bars) as client:
        feed.seed_klines(client=client)
        first = list(feed.klines)
        feed.seed_klines(client=client)
    assert feed.klines == first


def test_a_refusal_from_the_exchange_raises_and_leaves_the_history_alone() -> None:
    feed = BybitPublicFeed(symbol="ETHUSDT")
    feed.handle({"topic": "kline.1.ETHUSDT", "type": "snapshot", "ts": 1,
                 "data": [live_bar(10 * 60_000, "LIVE")]}, 1)

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"retCode": 10001, "retMsg": "bad symbol"})

    with httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://k.test") as client:
        with pytest.raises(RuntimeError):
            feed.seed_klines(client=client)
    assert [bar["close"] for bar in feed.klines] == ["LIVE"], "live bars were lost on a refusal"


# ---------------------------------------------------------------- A/B: cold start, all symbols

@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
async def test_the_first_look_at_any_symbol_returns_a_full_window(runtime, seed, symbol) -> None:
    feed = await runtime.seeded_feed_for(symbol)
    assert feed.symbol == symbol
    assert len(feed.chart()) == 120
    assert runtime.seed_state(symbol) == "READY"
    assert seed.count(symbol) == 1


async def test_a_restart_gives_all_three_symbols_the_same_history_contract(runtime, seed) -> None:
    """No prior memory, so this is the restart case: whoever is asked first is seeded first."""
    for symbol in ("SOLUSDT", "BTCUSDT", "ETHUSDT"):
        assert runtime.seed_state(symbol) == "UNSEEDED"
    bars = {}
    for symbol in ("SOLUSDT", "BTCUSDT", "ETHUSDT"):
        bars[symbol] = len((await runtime.seeded_feed_for(symbol)).chart())
    assert bars == {"SOLUSDT": 120, "BTCUSDT": 120, "ETHUSDT": 120}
    assert [seed.count(s) for s in ("SOLUSDT", "BTCUSDT", "ETHUSDT")] == [1, 1, 1]


# ---------------------------------------------------------------- C: exactly once

@pytest.mark.parametrize("symbol", ["ETHUSDT", "SOLUSDT"])
async def test_ten_concurrent_looks_seed_once(monkeypatch, symbol) -> None:
    slow = CountingSeed(delay_s=0.05)
    monkeypatch.setattr(BybitPublicFeed, "start", lambda self: None)
    monkeypatch.setattr(BybitPublicFeed, "seed_klines",
                        lambda self, **kw: slow(self, **kw))
    runtime = terminal_api.Runtime()

    feeds = await asyncio.gather(*[runtime.seeded_feed_for(symbol) for _ in range(10)])

    assert slow.count(symbol) == 1, f"seeded {slow.count(symbol)} times"
    assert len({id(feed) for feed in feeds}) == 1, "ten feed objects for one symbol"
    assert all(len(feed.chart()) == 120 for feed in feeds)
    assert runtime.seed_state(symbol) == "READY"


async def test_a_caller_that_gives_up_mid_seed_does_not_cancel_it_for_the_others(
        monkeypatch) -> None:
    slow = CountingSeed(delay_s=0.1)
    monkeypatch.setattr(BybitPublicFeed, "start", lambda self: None)
    monkeypatch.setattr(BybitPublicFeed, "seed_klines",
                        lambda self, **kw: slow(self, **kw))
    runtime = terminal_api.Runtime()

    leaver = asyncio.create_task(runtime.seeded_feed_for("ETHUSDT"))
    stayer = asyncio.create_task(runtime.seeded_feed_for("ETHUSDT"))
    await asyncio.sleep(0.02)
    leaver.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leaver

    feed = await stayer
    assert len(feed.chart()) == 120, "the surviving caller lost the seed to a cancelled one"
    assert slow.count("ETHUSDT") == 1
    assert runtime.seed_state("ETHUSDT") == "READY"


# ---------------------------------------------------------------- D: repeated switching

async def test_switching_tabs_round_and_round_never_seeds_again(runtime, seed) -> None:
    order = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT", "BTCUSDT"]
    for symbol in order:
        feed = await runtime.seeded_feed_for(symbol)
        assert len(feed.chart()) == 120, f"{symbol} lost its history on a revisit"
    assert [seed.count(s) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")] == [1, 1, 1]
    assert len(seed.calls) == 3


async def test_the_lifespan_seed_of_the_default_symbol_is_not_repeated(runtime, seed) -> None:
    """What `mark_seeded` is for: the lifespan seeds BTC itself, before the socket starts."""
    runtime.feeds["BTCUSDT"] = runtime.feed
    runtime.feed.symbol = "BTCUSDT"
    seed(runtime.feed)                       # stands in for the lifespan's own call
    runtime.mark_seeded("BTCUSDT")
    assert runtime.seed_state("BTCUSDT") == "READY"

    feed = await runtime.seeded_feed_for("BTCUSDT")
    assert feed is runtime.feed
    assert len(feed.chart()) == 120
    assert seed.count("BTCUSDT") == 1, "the first request seeded a second time"


# ---------------------------------------------------------------- E: failure handling

async def test_a_failed_seed_keeps_the_feed_alive_and_says_it_failed(monkeypatch) -> None:
    failing = CountingSeed(fail_times=1)
    monkeypatch.setattr(BybitPublicFeed, "start", lambda self: None)
    monkeypatch.setattr(BybitPublicFeed, "seed_klines",
                        lambda self, **kw: failing(self, **kw))
    runtime = terminal_api.Runtime()

    feed = await runtime.seeded_feed_for("ETHUSDT")
    # No crash, and the feed is the same live object the socket is pushing into.
    assert feed is runtime.feeds["ETHUSDT"]
    assert feed.chart() == []
    assert "kline seed failed" in (feed.telemetry.last_error or "")
    # A short chart must not read as the truth.
    assert runtime.seed_state("ETHUSDT") == "FAILED"

    # Live bars keep arriving regardless.
    feed.handle({"topic": "kline.1.ETHUSDT", "type": "snapshot", "ts": 1,
                 "data": [live_bar(5 * 60_000, "LIVE")]}, 1)
    assert len(feed.chart()) == 1


async def test_a_failed_seed_is_retried_on_the_next_look(monkeypatch) -> None:
    failing = CountingSeed(fail_times=1)
    monkeypatch.setattr(BybitPublicFeed, "start", lambda self: None)
    monkeypatch.setattr(BybitPublicFeed, "seed_klines",
                        lambda self, **kw: failing(self, **kw))
    runtime = terminal_api.Runtime()

    await runtime.seeded_feed_for("SOLUSDT")
    assert runtime.seed_state("SOLUSDT") == "FAILED"

    feed = await runtime.seeded_feed_for("SOLUSDT")
    assert failing.count("SOLUSDT") == 2, "the failure was sticky"
    assert len(feed.chart()) == 120
    assert runtime.seed_state("SOLUSDT") == "READY"

    # And once it has succeeded it is not tried again.
    await runtime.seeded_feed_for("SOLUSDT")
    assert failing.count("SOLUSDT") == 2


async def test_one_symbols_failed_seed_does_not_touch_another(monkeypatch) -> None:
    class OnlyEthFails(CountingSeed):
        def __call__(self, feed, **kw):
            if feed.symbol == "ETHUSDT":
                self.calls.append(feed.symbol)
                raise RuntimeError("eth refused")
            super().__call__(feed, **kw)

    seed = OnlyEthFails()
    monkeypatch.setattr(BybitPublicFeed, "start", lambda self: None)
    monkeypatch.setattr(BybitPublicFeed, "seed_klines",
                        lambda self, **kw: seed(self, **kw))
    runtime = terminal_api.Runtime()

    await runtime.seeded_feed_for("ETHUSDT")
    sol = await runtime.seeded_feed_for("SOLUSDT")
    assert runtime.seed_state("ETHUSDT") == "FAILED"
    assert runtime.seed_state("SOLUSDT") == "READY"
    assert len(sol.chart()) == 120


async def test_an_unsupported_symbol_is_refused_before_a_feed_or_a_seed_exists(runtime,
                                                                               seed) -> None:
    from app.crypto.symbols import SymbolNotSupported
    with pytest.raises(SymbolNotSupported):
        await runtime.seeded_feed_for("DOGEUSDT")
    assert runtime.feeds == {}
    assert seed.calls == []
    assert runtime.seed_state("DOGEUSDT") == "UNSEEDED"


# ---------------------------------------------------------------- the chart route

@pytest.fixture
def client(tmp_path: Path, monkeypatch, seed: CountingSeed) -> TestClient:
    risk = tmp_path / "risk.json"
    risk.write_text(json.dumps(RISK_LIMIT_PAYLOAD))
    config_path = tmp_path / "run_config.json"
    config_path.write_text(json.dumps({
        "run_id": "seed-test", "starting_capital_krw": "1000000", "fx_krw_per_usdt": "1000",
        "fx_source": "test", "fx_asof_utc": "2026-09-23T00:00:00Z", "fee_version": "test-v1",
        "fee_taker_rate": "0.0006", "fee_maker_rate": "0.0002", "fee_source": "test",
        "fee_effective_date": "2026-01-01", "slippage_model": "NONE", "slippage_bps": "0",
        "leverage": "10", "risk_limit_path": str(risk)}))
    monkeypatch.setenv(terminal_api.CONFIG_ENV, str(config_path))
    monkeypatch.setenv("CRYPTO_PAPER_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(terminal_api.Runtime, "start", lambda self: None)
    with TestClient(terminal_api.create_app()) as test_client:
        yield test_client


def test_every_symbols_chart_answers_with_a_full_window_on_its_first_request(
        client: TestClient, seed: CountingSeed) -> None:
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        body = client.get(f"/api/crypto/chart?symbol={symbol}").json()
        assert body["symbol"] == symbol
        assert len(body["bars"]) == 120, f"{symbol} first request returned {len(body['bars'])}"
        assert body["seed"] == "READY"
    # BTC was seeded by the lifespan; the route must not have repeated it.
    assert [seed.count(s) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")] == [1, 1, 1]


def test_the_chart_route_reports_a_failed_seed_instead_of_a_short_window(
        client: TestClient, monkeypatch) -> None:
    always_fails = CountingSeed(fail_times=99)
    monkeypatch.setattr(BybitPublicFeed, "seed_klines",
                        lambda self, **kw: always_fails(self, **kw))
    body = client.get("/api/crypto/chart?symbol=ETHUSDT").json()
    assert body["seed"] == "FAILED"
    assert body["bars"] == []
    assert body["symbol"] == "ETHUSDT"


def test_the_chart_route_still_honours_its_limit_and_symbol_contract(client: TestClient) -> None:
    body = client.get("/api/crypto/chart?symbol=ETHUSDT&limit=30").json()
    assert len(body["bars"]) == 30
    assert body["symbol"] == "ETHUSDT"
    assert sorted(body["bars"][0]) == ["close", "confirmed", "high", "low", "open", "start_ms",
                                       "volume"]
    # The 600-bar ceiling is unchanged.
    assert len(client.get("/api/crypto/chart?symbol=ETHUSDT&limit=5000").json()["bars"]) == 120


def test_an_unsupported_symbol_on_the_chart_route_is_still_a_400(client: TestClient) -> None:
    response = client.get("/api/crypto/chart?symbol=DOGEUSDT")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SYMBOL_NOT_SUPPORTED"


# ---------------------------------------------------------------- F/G: regressions

def test_the_paged_chart_history_store_is_untouched_by_the_seed() -> None:
    """The 1m/10m/1h/4h/1d paging path has its own store and never reads `feed.klines`."""
    source = Path("backend/app/crypto/terminal/chart_history.py").read_text()
    assert "klines" not in source
    assert "BybitPublicFeed" not in source
    assert "seed_klines" not in source


def test_the_seed_does_not_reach_the_tape_the_session_or_compression(runtime, seed) -> None:
    import inspect
    from app.crypto.terminal.api import Runtime
    body = inspect.getsource(Runtime.seeded_feed_for) + inspect.getsource(Runtime.seed_state)
    for forbidden in ("session", "segments", "compress", "rotate", "ledger", "tape", "order"):
        assert forbidden not in body.replace("# ", "").split('"""')[-1], forbidden


def test_seeding_changes_nothing_about_the_session_or_its_storage(client: TestClient) -> None:
    before = client.get("/api/crypto/state").json()["storage"]
    for symbol in ("ETHUSDT", "SOLUSDT", "BTCUSDT"):
        client.get(f"/api/crypto/chart?symbol={symbol}")
    after = client.get("/api/crypto/state").json()["storage"]
    assert after["segments"] == before["segments"]
    assert after["compressed_segments"] == before["compressed_segments"]
    assert after["compress_new_segments"] == before["compress_new_segments"]
    assert after["ledger_bytes"] == before["ledger_bytes"]


def test_the_seed_never_calls_the_quote_path_so_orders_are_unaffected(runtime, seed) -> None:
    """`feed_for` stays synchronous, which is why the twelve non-chart callers are unchanged."""
    import inspect
    assert not inspect.iscoroutinefunction(terminal_api.Runtime.feed_for)
    assert not inspect.iscoroutinefunction(terminal_api.paper_feed)
    assert inspect.iscoroutinefunction(terminal_api.Runtime.seeded_feed_for)
    assert inspect.iscoroutinefunction(terminal_api.seeded_paper_feed)
