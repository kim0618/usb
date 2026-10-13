"""CRYPTO UI POLLING OPTIMIZATION V1: what the polled GETs may no longer do, and what they must
still answer.

* `GET /api/crypto/sizing` prices on a clone: same numbers as the old forced observation, and no
  tape record, ledger line or fsync.
* `snapshot()["storage"]` is read from disk at most every 10 s, and a rotation drops the reading.
* `GET /api/crypto/binance/sizing` reads the account at the snapshot's tiers; the OPEN path still
  forces a full read.
* `GET /api/crypto/hot` makes no signed read for the PAPER screen and reuses one for 30 s on the
  LIVE screen; the scan's own lock still reads every source.
"""
from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.crypto.hot_coins.lock import ALLOWED, BLOCKED, HOT_POSITION_OPEN, SymbolExposure
from app.crypto.paper import segments as seg
from app.crypto.paper import sizing as paper_sizing
from app.crypto.terminal import api as terminal_api
from app.crypto.terminal import hot_routes, live_routes
from app.crypto.terminal.api import jsonable
from tests.crypto.binance_fixtures import POSITION_RISK_FLAT, FakeBinance
from tests.crypto.test_binance_live_sizing import adapter as live_adapter
from tests.crypto.test_paper_sizing_api import client  # noqa: F401  (fixture)
from tests.crypto.test_paper_terminal_api import snapshot_message, ticker_snapshot


def _run_files(run_dir: Path) -> dict[str, tuple[int, int]]:
    return {path.name: (path.stat().st_size, path.stat().st_mtime_ns)
            for path in run_dir.iterdir() if path.is_file()}


def _move_feed(bid: str, ask: str, mark: str, ts: int) -> None:
    message = snapshot_message(bid, ask)
    message["ts"] = ts
    terminal_api.runtime.feed.handle(message, ts + 1)
    terminal_api.runtime.feed.handle(ticker_snapshot(mark, ts), ts + 1)


# ------------------------------------------------------------------ /sizing


def test_sizing_prices_the_newest_tick_exactly_as_the_forced_observation_did(client: TestClient) -> None:  # noqa: F811
    _move_feed("100500.0", "100500.1", "100500.0", 5_000)
    body = client.get("/api/crypto/sizing").json()

    # The old route: force the quote onto the real engine, then price the presets on it.
    session = terminal_api.runtime.session
    session.observe(terminal_api.runtime.feed.quote(), force=True)
    expected = jsonable({side: paper_sizing.presets(session.engine, side) for side in ("LONG", "SHORT")})

    assert body["sides"] == expected
    assert body["mark_price"] == str(session.engine.quote.mark_price)
    assert body["quote_ts_ms"] == session.engine.quote.ts_ms
    assert body["available_balance"] == str(session.engine.account.available_balance)


def test_sizing_get_writes_nothing_and_fsyncs_nothing(client: TestClient, monkeypatch) -> None:  # noqa: F811
    _move_feed("100500.0", "100500.1", "100500.0", 5_000)
    session = terminal_api.runtime.session
    syncs: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (syncs.append(fd), real_fsync(fd))[1])
    files = _run_files(session.run_dir)
    records, events = len(session.tape), len(session.engine.ledger.events)
    quote_before = session.engine.quote

    for step in range(5):
        _move_feed("100500.0", "100500.1", str(100500 + step), 6_000 + step * 10)
        assert client.get("/api/crypto/sizing").status_code == 200

    assert syncs == []
    assert _run_files(session.run_dir) == files
    assert len(session.tape) == records
    assert len(session.engine.ledger.events) == events
    assert session.engine.quote is quote_before          # the real engine did not move


def test_an_order_still_acts_on_the_newest_quote(client: TestClient) -> None:  # noqa: F811
    """Commands kept their own forced observation; only the polled read lost it."""
    _move_feed("100700.0", "100700.1", "100700.0", 7_000)
    records = len(terminal_api.runtime.session.tape)
    response = client.post("/api/crypto/order", json={"side": "LONG", "intent": "OPEN", "qty": "0.001"})
    assert response.status_code == 200, response.text
    session = terminal_api.runtime.session
    assert session.engine.quote.ts_ms == 7_000
    assert len(session.tape) > records


# ------------------------------------------------------------------ storage()


def _tape(tmp_path: Path, ttl_ms: int, clock: list[float]) -> seg.SegmentedTape:
    run = tmp_path / "run"
    run.mkdir()
    (run / seg.ACTIVE_TAPE).write_text('{"kind": "MARKET"}\n' * 3)
    return seg.SegmentedTape(run_dir=run, storage_ttl_ms=ttl_ms, clock_ms=lambda: clock[0])


def test_storage_is_read_from_disk_at_most_once_per_ttl(tmp_path: Path, monkeypatch) -> None:
    clock = [0.0]
    tape = _tape(tmp_path, 10_000, clock)
    reads: list[int] = []
    real = seg.load_manifests
    monkeypatch.setattr(seg, "load_manifests", lambda run_dir: (reads.append(1), real(run_dir))[1])

    first = tape.storage()
    clock[0] = 9_999
    (tape.active_path).write_text('{"kind": "MARKET"}\n' * 7)
    assert tape.storage() == first                      # still the cached reading
    assert first["records"] == 3
    calls = len(reads)
    clock[0] = 10_000
    assert tape.storage()["records"] == 7               # expired: read again
    assert len(reads) > calls


def test_a_cached_reading_cannot_be_edited_by_its_caller(tmp_path: Path) -> None:
    tape = _tape(tmp_path, 10_000, [0.0])
    tape.storage()["records"] = -1
    assert tape.storage()["records"] == 3


def test_a_rotation_drops_the_cached_reading(tmp_path: Path, monkeypatch) -> None:
    clock = [0.0]
    tape = _tape(tmp_path, 10_000, clock)
    assert tape.storage()["segments"] == 0
    monkeypatch.setattr(seg, "capture", lambda *a, **k: {"checkpoint": True})
    tape.rotate(engine=None, tape_records=3)            # type: ignore[arg-type]
    after = tape.storage()
    assert after["segments"] == 1 and after["records"] == 3 and after["active_bytes"] == 0


def test_no_ttl_keeps_the_old_behaviour(tmp_path: Path) -> None:
    tape = _tape(tmp_path, 0, [0.0])
    assert tape.storage()["records"] == 3
    tape.active_path.write_text('{"kind": "MARKET"}\n' * 5)
    assert tape.storage()["records"] == 5


def test_the_terminal_session_caches_storage_for_ten_seconds(client: TestClient) -> None:  # noqa: F811
    session = terminal_api.runtime.session
    assert session.segments.storage_ttl_ms == 10_000
    assert client.get("/api/crypto/state").json()["storage"]["segments"] == 0


# ------------------------------------------------------------------ binance/sizing


def test_live_sizing_without_force_uses_the_snapshot_tiers() -> None:
    live, fake = live_adapter(FakeBinance(position_rows=POSITION_RISK_FLAT))
    live.get_sizing(force=False)
    full = fake.count("/fapi/v3/account")
    for _ in range(5):
        assert live.get_sizing(force=False)["available"] is True
    assert fake.count("/fapi/v3/account") == full       # no further full signed read


def test_the_open_preflight_still_forces_a_full_read() -> None:
    live, fake = live_adapter(FakeBinance(position_rows=POSITION_RISK_FLAT))
    live.get_sizing(force=False)
    full = fake.count("/fapi/v3/account")
    live.preflight_open("LONG", qty="0.001")
    assert fake.count("/fapi/v3/account") == full + 1


def test_the_polled_route_asks_for_the_tiered_snapshot(monkeypatch) -> None:
    asked: list[bool] = []

    class Adapter:
        def get_sizing(self, depth_limit: int = 20, *, force: bool = True):
            asked.append(force)
            return {"available": False, "reject_code": "X", "reject_message": "x"}

    monkeypatch.setattr(live_routes, "_entry", lambda symbol: (SimpleNamespace(adapter=Adapter()), None))
    with TestClient(terminal_api.app) as test_client:
        assert test_client.get("/api/crypto/binance/sizing").status_code == 200
    assert asked == [False]


# ------------------------------------------------------------------ /hot


class _Live:
    """A stand-in for `LiveExposure` that counts its signed reads."""
    reads_exchange = True

    def __init__(self, amount: str = "0") -> None:
        self.amount = Decimal(amount)
        self.reads = 0
        self._seen: dict = {}

    def exposure(self, symbol: str) -> SymbolExposure:
        # Records its read the way `LiveExposure.exposure` does after a signed read.
        self.reads += 1
        self._seen[symbol] = (live_routes.time.monotonic() * 1000, self.amount, 0)
        return SymbolExposure(symbol, self.amount, 0, 0)

    def recent_exposure(self, symbol: str, max_age_ms: float) -> SymbolExposure:
        return live_routes.LiveExposure.recent_exposure(self, symbol, max_age_ms)  # type: ignore[arg-type]


class _Paper:
    def exposure(self, symbol: str) -> SymbolExposure:
        return SymbolExposure(symbol, Decimal(0), 0, 0)


@pytest.fixture
def hot_sources(monkeypatch):
    live = _Live("1")
    monkeypatch.setattr(hot_routes.controller, "sources", [_Paper(), live])
    monkeypatch.setattr(hot_routes.controller, "current", lambda: SimpleNamespace(symbol="MAGICUSDT"))
    monkeypatch.setattr(live_routes, "live_in_flight", SimpleNamespace(count=lambda symbol: 0))
    return live


def test_the_paper_screen_makes_no_signed_read(hot_sources) -> None:
    decision, basis = hot_routes._view_lock("PAPER")
    assert hot_sources.reads == 0
    assert basis == hot_routes.LOCK_PAPER_ONLY
    assert decision.state == ALLOWED                    # paper is flat; LIVE was not asked


def test_the_live_screen_reuses_a_signed_read_for_thirty_seconds(hot_sources, monkeypatch) -> None:
    now = [1_000_000.0]
    monkeypatch.setattr(live_routes.time, "monotonic", lambda: now[0] / 1000)
    first, basis = hot_routes._view_lock("BINANCE_LIVE")
    assert (first.state, first.code, basis) == (BLOCKED, HOT_POSITION_OPEN, hot_routes.LOCK_LIVE_RECENT)
    assert hot_sources.reads == 1
    now[0] += 29_999
    hot_routes._view_lock("BINANCE_LIVE")
    assert hot_sources.reads == 1
    now[0] += 1
    hot_routes._view_lock("BINANCE_LIVE")
    assert hot_sources.reads == 2


def test_the_scan_lock_always_reads_the_account(hot_sources) -> None:
    hot_routes._view_lock("BINANCE_LIVE")
    before = hot_sources.reads
    for _ in range(3):
        assert hot_routes.controller.lock().state == BLOCKED
    assert hot_sources.reads == before + 3


def test_a_caller_that_names_no_screen_gets_the_fresh_lock(hot_sources) -> None:
    _, basis = hot_routes._view_lock(None)
    assert basis == hot_routes.LOCK_FRESH and hot_sources.reads == 1


def test_live_exposure_records_its_last_signed_read(monkeypatch) -> None:
    live = live_routes.LiveExposure()
    rows = [{"symbol": "MAGICUSDT", "positionAmt": "-3"}]
    calls: list[str] = []
    client_stub = SimpleNamespace(call=lambda name, params: (calls.append(name), rows if name == "position_risk" else [])[1])
    monkeypatch.setattr(live_routes, "_shared_ready", lambda: True)
    monkeypatch.setattr(live_routes.live_runtime, "client", client_stub)
    monkeypatch.setattr(live_routes, "_reconcile_in_flight", lambda symbol: None)
    first = live.recent_exposure("MAGICUSDT", 30_000)
    again = live.recent_exposure("MAGICUSDT", 30_000)
    assert first.position_amt == again.position_amt == Decimal(3)
    assert calls == ["position_risk", "open_orders"]
    live.exposure("MAGICUSDT")                          # the scan's read is never cached
    assert calls == ["position_risk", "open_orders"] * 2
