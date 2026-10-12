"""The incremental C1 window must produce the grid a full 33-day refresh produces, tick by tick.

Two runtimes share one simulated venue and one clock: `full` downloads the whole window on every
tick (the old behaviour, `CRYPTO_C1_INCREMENTAL=off`) and `incremental` keeps it. After every tick
every grid series is compared bit for bit, along with the funding list, the engine's dedupe state,
the last evaluation and the ledgers.

The venue is built to be awkward, because the easy case proves nothing: bars that arrive minutes
late, bars that arrive hours late (past the refetched tail, so only gap repair can find them),
minutes that never arrive, the same for spot closes and open-interest records, signals that fire
inside the replay, a pause long enough to force a full refresh, and a venue revision of an old bar
that only the audit can notice.
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from app.crypto.c1 import incremental as inc
from app.crypto.c1 import runtime as runtime_mod
from app.crypto.c1.contract import FIVE_MIN_MS, MINUTE_MS
from app.crypto.c1.grid import Bar
from app.crypto.c1.runtime import C1Runtime
from app.crypto.c1.sources import MarketData

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
ORIGIN_MS = 1_735_689_600_000            # 2025-01-01T00:00:00Z, day aligned
VENUE_DAYS = 37
HIGH_STEP = 0.0015                       # 24 h volatility above the frozen HIGH cutoff


def _h(value: int, salt: int) -> int:
    return (value // MINUTE_MS * 2654435761 + salt * 40503) % 1_000_003


class Venue:
    """Deterministic public series with publication delays. `visible_at` is when a fetch first
    sees an item; `math.inf` means never."""

    def __init__(self) -> None:
        self.perp: dict[int, tuple[float, float, float, float, float]] = {}
        self.perp_at: dict[int, float] = {}
        self.spot: dict[int, float] = {}
        self.spot_at: dict[int, float] = {}
        self.oi: dict[int, float] = {}
        self.oi_at: dict[int, float] = {}
        self.funding: dict[int, float] = {}
        price = 90_000.0
        count = VENUE_DAYS * 1440
        for index in range(count):
            ts = ORIGIN_MS + index * MINUTE_MS
            if index:
                price *= math.exp(HIGH_STEP * (1 if index % 2 else -1))
            self.perp[ts] = (price, price * 1.0004, price * 0.9996, price, 1.0)
            delay = 61_000.0
            if _h(ts, 1) % 97 == 0:
                delay = 7 * MINUTE_MS            # late, inside the refetched tail
            if _h(ts, 2) % 2003 == 0:
                delay = 5 * HOUR_MS              # late, past the tail: only gap repair finds it
            if _h(ts, 3) % 20011 == 0:
                delay = math.inf                 # never published
            self.perp_at[ts] = ts + delay
            # S1 dips far below every cutoff once every three hours, on the minute that closes a
            # 5m block (the minute a boundary reads): rare enough to stay under the 10th
            # percentile, frequent enough that the replay fires signals.
            dip = index % 180 == 179
            basis = -0.01 if dip else -1e-4 * (index % 10)
            self.spot[ts] = price / math.exp(basis)
            spot_delay = 2_000.0 + 61_000.0
            if _h(ts, 4) % 89 == 0:
                spot_delay = 9 * MINUTE_MS
            if _h(ts, 5) % 2203 == 0:
                spot_delay = 4 * HOUR_MS
            if _h(ts, 6) % 20021 == 0:
                spot_delay = math.inf
            self.spot_at[ts] = ts + spot_delay
        for index in range(-24 * 60, count, 5):
            ts = ORIGIN_MS + index * MINUTE_MS
            # Falling for most of every 12 hours, so the 1 h change is negative most of the time.
            self.oi[ts] = 1_000_000.0 - ((index % 720) * 25.0)
            oi_delay = 30_000.0
            if _h(ts, 7) % 13 == 0:
                oi_delay = 12 * MINUTE_MS
            if _h(ts, 8) % 701 == 0:
                oi_delay = 4 * HOUR_MS
            if _h(ts, 9) % 4001 == 0:
                oi_delay = math.inf
            self.oi_at[ts] = ts + oi_delay
        for index in range(-3, VENUE_DAYS * 3 + 3):
            self.funding[ORIGIN_MS + index * 8 * HOUR_MS] = 0.0001 * ((index % 5) - 2)


class VenueMarket(MarketData):
    """MarketData over the simulated venue. `fetch` is inherited, so the full refresh composes
    the four reads exactly as production does."""

    def __init__(self, venue: Venue, clock) -> None:          # noqa: D107 - no network clients
        self.venue = venue
        self.clock = clock
        self.calls = 0
        self.rows = 0                                        # what a real venue would serialise

    def close(self) -> None:
        pass

    def _now(self) -> float:
        return self.clock() * 1000

    def perp_bars(self, start_ms: int, end_ms: int) -> list[Bar]:
        self.calls += 1
        now = self._now()
        out = []
        for ts in range(start_ms - start_ms % MINUTE_MS, end_ms, MINUTE_MS):
            if ts < start_ms or ts not in self.venue.perp or self.venue.perp_at[ts] > now:
                continue
            o, h, l, c, v = self.venue.perp[ts]
            out.append(Bar(ts_ms=ts, open=o, high=h, low=l, close=c, volume=v))
        self.rows += len(out)
        return out

    def spot_closes(self, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        self.calls += 1
        now = self._now()
        out = [(ts, self.venue.spot[ts])
               for ts in range(start_ms - start_ms % MINUTE_MS, end_ms, MINUTE_MS)
               if ts >= start_ms and ts in self.venue.spot and self.venue.spot_at[ts] <= now]
        self.rows += len(out)
        return out

    def open_interest(self, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        self.calls += 1
        now = self._now()
        out = [(ts, value) for ts, value in sorted(self.venue.oi.items())
               if start_ms <= ts < end_ms and self.venue.oi_at[ts] <= now]
        self.rows += len(out)
        return out

    def funding_rates(self, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        self.calls += 1
        now = self._now()
        return [(ts, rate) for ts, rate in sorted(self.venue.funding.items())
                if start_ms <= ts < end_ms and ts <= now]


def _bits(values) -> list[bytes]:
    return [struct.pack("<d", float(value)) for value in values]


def assert_same_grid(full, incremental) -> None:
    assert incremental.start_ms == full.start_ms
    assert len(incremental) == len(full)
    for name in inc.GRID_SERIES:
        a, b = getattr(full, name), getattr(incremental, name)
        if name == "oi_stamp":
            assert a == b, name
            continue
        if _bits(a) != _bits(b):
            first = next(i for i, (x, y) in enumerate(zip(_bits(a), _bits(b))) if x != y)
            raise AssertionError(f"{name}[{first}] full={a[first]!r} incremental={b[first]!r} "
                                 f"ts={full.ts(first)}")


def assert_same_runtime(full: C1Runtime, incremental: C1Runtime, *,
                        evaluation: bool = True) -> None:
    assert_same_grid(full.grid, incremental.grid)
    assert incremental.funding == full.funding
    assert incremental.engine.armed == full.engine.armed
    assert incremental.last_decided_at_ms == full.last_decided_at_ms
    a, b = full.engine.last_evaluation, incremental.engine.last_evaluation
    if evaluation:
        assert (a is None) == (b is None)
    if evaluation and a is not None:
        assert (a.bar_ms, a.state, a.eligible, a.completeness.missing) == \
            (b.bar_ms, b.state, b.eligible, b.completeness.missing)
        assert repr(a.features.to_json()) == repr(b.features.to_json())
    assert {k: v.to_json() for k, v in full.signals.items()} == \
        {k: v.to_json() for k, v in incremental.signals.items()}
    assert repr({k: v.to_json() for k, v in full.trades.items()}) == \
        repr({k: v.to_json() for k, v in incremental.trades.items()})
    assert repr({k: v.to_json() for k, v in full.c1x.items()}) == \
        repr({k: v.to_json() for k, v in incremental.c1x.items()})


@pytest.fixture(scope="module")
def venue() -> Venue:
    return Venue()


def _pair(tmp_path: Path, venue: Venue, monkeypatch, now: list[float]):
    clock = lambda: now[0]                                   # noqa: E731
    monkeypatch.setenv(runtime_mod.INCREMENTAL_ENV, "off")
    full = C1Runtime(tmp_path / "full", VenueMarket(venue, clock), clock=clock)
    monkeypatch.setenv(runtime_mod.INCREMENTAL_ENV, "on")
    incremental = C1Runtime(tmp_path / "incremental", VenueMarket(venue, clock), clock=clock)
    assert full.window is None and incremental.window is not None
    return full, incremental


def _run(full: C1Runtime, incremental: C1Runtime, now: list[float], steps) -> None:
    full.bootstrap()
    incremental.bootstrap()
    assert_same_runtime(full, incremental)
    for step in steps:
        now[0] += step
        full.tick()
        incremental.tick()
        assert full.error is None, full.error
        assert incremental.error is None, incremental.error
        assert_same_runtime(full, incremental)


def test_incremental_window_matches_full_refresh_every_tick(tmp_path, venue, monkeypatch) -> None:
    # Start mid-hour, 34 days in, so the first ticks already cover late bars and dips.
    now = [(ORIGIN_MS + 34 * DAY_MS + 7 * HOUR_MS + 13 * MINUTE_MS + 7_000) / 1000]
    full, incremental = _pair(tmp_path, venue, monkeypatch, now)
    # Twenty-second ticks with the irregular gaps a busy event loop produces, then an hour of
    # one-minute ticks so delayed records land while the window is being kept.
    steps = [20.0, 20.4, 19.7, 41.0, 20.0, 80.3] * 8 + [60.0] * 60
    _run(full, incremental, now, steps)
    stats = incremental.window.stats()
    assert stats["incremental"] >= len(steps) - 1
    assert stats["full"] == 1                                 # the bootstrap only
    assert incremental.signals, "the replay must exercise signal emission"
    # The point of the change: a kept window reads a few hours of rows, not the whole window.
    assert incremental.market.rows < full.market.rows / 20


def test_hours_late_data_is_repaired_like_a_full_refresh(tmp_path, venue, monkeypatch) -> None:
    """Find a bar published five hours late and tick across its arrival."""
    late = sorted(ts for ts, at in venue.perp_at.items()
                  if at - ts == 5 * HOUR_MS and ts > ORIGIN_MS + 33 * DAY_MS)[0]
    now = [(late + 5 * HOUR_MS - 3 * MINUTE_MS) / 1000]
    full, incremental = _pair(tmp_path, venue, monkeypatch, now)
    _run(full, incremental, now, [60.0] * 8)
    index = full.grid.index_of(late)
    assert not math.isnan(full.grid.closes[index])


def test_long_pause_falls_back_to_a_full_refresh(tmp_path, venue, monkeypatch) -> None:
    now = [(ORIGIN_MS + 34 * DAY_MS + 2 * HOUR_MS) / 1000]
    full, incremental = _pair(tmp_path, venue, monkeypatch, now)
    _run(full, incremental, now, [20.0, 20.0, 7 * 3600.0, 20.0, 20.0])
    assert incremental.window.fallback_reasons == {"LONG_PAUSE": 1}


def test_restart_bootstraps_like_before(tmp_path, venue, monkeypatch) -> None:
    """Restart both after a stretch of ticks and compare the restarted pair. (A restarted runtime
    is not compared with a live one: what the ledger holds and what memory held differ in ways
    this change does not touch.)"""
    now = [(ORIGIN_MS + 34 * DAY_MS + 11 * HOUR_MS) / 1000]
    full, incremental = _pair(tmp_path, venue, monkeypatch, now)
    _run(full, incremental, now, [20.0] * 6)
    clock = lambda: now[0]                                   # noqa: E731
    monkeypatch.setenv(runtime_mod.INCREMENTAL_ENV, "off")
    full_again = C1Runtime(tmp_path / "full", VenueMarket(venue, clock), clock=clock)
    monkeypatch.setenv(runtime_mod.INCREMENTAL_ENV, "on")
    incremental_again = C1Runtime(tmp_path / "incremental", VenueMarket(venue, clock), clock=clock)
    _run(full_again, incremental_again, now, [20.0] * 4)
    assert incremental_again.window.stats()["incremental"] == 4


def test_a_venue_revision_is_found_by_the_audit_and_then_matches(tmp_path, monkeypatch) -> None:
    """The one assumption: old finished bars are not revised. If one is, the full refresh shows
    it at once and the incremental window shows it after the audit reaches that slice."""
    venue = Venue()
    now = [(ORIGIN_MS + 34 * DAY_MS + 5 * HOUR_MS) / 1000]
    full, incremental = _pair(tmp_path, venue, monkeypatch, now)
    _run(full, incremental, now, [20.0] * 3)
    target = incremental.window.start_ms + 20 * DAY_MS        # stays inside the window
    o, h, l, c, v = venue.perp[target]
    venue.perp[target] = (o, h, l, c * 1.001, v)
    found = None
    for _ in range(200):
        now[0] += 20.0
        full.tick()
        incremental.tick()
        if incremental.window.audit_mismatches:
            found = incremental.window.audit_mismatches
            break
    assert found
    now[0] += 20.0
    full.tick()
    incremental.tick()
    assert_same_runtime(full, incremental)
    assert incremental.window.fallback_reasons.get("NO_WINDOW") == 1


def test_off_switch_restores_the_full_download(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(runtime_mod.INCREMENTAL_ENV, "off")
    assert C1Runtime(tmp_path).window is None
    monkeypatch.delenv(runtime_mod.INCREMENTAL_ENV)
    assert C1Runtime(tmp_path).window is not None


def test_the_kept_window_reaches_back_like_the_full_fetch() -> None:
    # MarketData.fetch reads spot from start - 5m and open interest from start - 2h.
    assert inc.SPOT_LEAD_MS == FIVE_MIN_MS
    assert inc.OI_LEAD_MS == 2 * HOUR_MS
