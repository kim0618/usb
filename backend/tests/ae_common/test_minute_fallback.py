"""E finalization's minute fallback: a tick walk that spends ``MAX_TICK_PAGES`` short of its cached
minute is finished from one usa06011 page, or stays STALE. Finalizer unit cases, the producer morning
on the virtual clock, and the 2026-10-30 INTC shape from its sealed bars."""

from collections import Counter
from datetime import date, datetime, time
import gzip
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.ae_common import producer as P
from app.ae_common.deadline import DeadlineReached
from app.integrations.kiwoom.client import KiwoomPage
from app.strategy_e_max_rt import finalizer as FZ
from tests.ae_common.helpers import SESSION, LimitedClient, at, minute_rows
from tests.ae_common.test_producer import A_SYMBOLS, E_SYMBOLS, morning
from tests.ae_common.test_provider_retry import code7, decision_view

ET = ZoneInfo("America/New_York")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CUTOFF = 9 * 60 + 24


def row(day: date, minute: int, o: float, h: float, l: float, c: float, v: float) -> dict:
    stamp = day.strftime("%Y%m%d")
    return {"bus_dt": stamp, "cntr_tm": f"{stamp}{minute // 60:02d}{minute % 60:02d}00", "open_pric": str(o),
            "high_pric": str(h), "low_pric": str(l), "cur_prc": str(c), "trde_qty": f"{v:.0f}"}


def ticks_of(minute_row: dict, n: int) -> list[dict]:
    """``n`` minute-stamped ticks whose aggregate is exactly ``minute_row``."""
    v = int(float(minute_row["trde_qty"]))
    base = v // n
    return [{**minute_row, "trde_qty": str(base if i < n - 1 else v - base * (n - 1))} for i in range(n)]


def newest_first(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: r["cntr_tm"], reverse=True)


class PagedLane:
    """Kiwoom-like paging, newest first, 100 rows a page."""

    def __init__(self, rows, *, size=100, resolved_exchange=None, error=None):
        self.rows, self.size, self.error = rows, size, error
        self.resolved_exchange = resolved_exchange
        self.calls, self.errors, self.latencies = 0, 0, []

    def page(self, symbol, exchange, continuation=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        start = 0 if continuation is None else continuation.next
        chunk = self.rows[start:start + self.size]
        return SimpleNamespace(body={"result_list": chunk}, continuation=start + self.size < len(self.rows),
                               next=start + self.size, resolved_exchange=self.resolved_exchange)


def bar(minute: int) -> tuple[float, float, float, float, float]:
    p = 20.0 + minute / 100
    return (p, p + 0.05, p - 0.05, p + 0.01, float(1000 + minute))


def truth(first: int, last: int, day: date = SESSION) -> list[dict]:
    return newest_first([row(day, m, *bar(m)) for m in range(first, last + 1)])


def cache_through(through: int) -> FZ.SymbolCache:
    cache = FZ.SymbolCache("X", "ND", "X")
    cache.bars = {m: list(bar(m)) for m in range(240, through + 1)}
    cache.complete_through = through
    return cache


def clock(h, m, s=0):
    return lambda: datetime.combine(SESSION, time(h, m, s), tzinfo=ET)


def tick_rows(per_minute: dict[int, int]) -> list[dict]:
    rows = []
    for minute, n in per_minute.items():
        rows += ticks_of(row(SESSION, minute, *bar(minute)), n)
    return newest_first(rows)


def capped_walk(per_minute=300, through=554, newest=567):
    """A walk from 09:27 whose 30 pages stop well short of ``through + 1``."""
    cache = cache_through(through)
    ticks = PagedLane(tick_rows({m: per_minute for m in range(newest, through - 20, -1)}))
    FZ.finalize_ticks(ticks, cache, SESSION, clock(9, 27, 50))
    return cache, ticks


# -- when the fallback is considered ---------------------------------------------------------------------

@pytest.mark.parametrize("pages", [1, 10, 18, 21, 29, 30])
def test_a_walk_that_reaches_its_minute_never_asks_for_the_fallback(pages):
    cache = cache_through(554)
    newer = 100 * pages - 1                      # the walk's last row on page ``pages`` is the first at <= 09:14
    per_minute = {m: 0 for m in range(566, 554, -1)}
    for i in range(newer):
        per_minute[566 - i % 12] += 1
    rows = tick_rows({m: n for m, n in per_minute.items() if n}) + tick_rows({554: 50, 553: 50})
    lane = PagedLane(rows)
    FZ.finalize_ticks(lane, cache, SESSION, clock(9, 27))
    assert lane.calls == pages and cache.tick_pages == pages and cache.tick_reached is True
    assert cache.contiguous is True and cache.finalization_source == FZ.SOURCE_TICK
    assert not FZ.needs_minute_fallback(cache)


def test_a_walk_that_ends_early_without_the_cap_is_not_a_fallback_case():
    cache = cache_through(554)
    empty = SimpleNamespace(page=lambda *a, **k: SimpleNamespace(body={"result_list": []}, continuation=True),
                            calls=0, errors=0, latencies=[])
    FZ.finalize_ticks(empty, cache, SESSION, clock(9, 27))   # an empty page that claims more: not reached
    assert cache.tick_pages == 1 and cache.tick_reached is False and cache.contiguous is False
    assert not FZ.needs_minute_fallback(cache)               # the cap was not spent: STALE as before


def test_a_capped_walk_short_of_its_minute_asks_for_exactly_one_fallback():
    cache, ticks = capped_walk()
    assert ticks.calls == FZ.MAX_TICK_PAGES == cache.tick_pages and cache.tick_reached is False
    assert cache.contiguous is False and cache.finalization_source == FZ.SOURCE_STALE
    assert cache.tick_need_from == 555 and cache.tick_oldest_minute == 558
    assert FZ.needs_minute_fallback(cache)


# -- the fallback itself -----------------------------------------------------------------------------------

def test_one_minute_page_completes_the_gap_with_the_minute_lanes_own_bars():
    cache, _ = capped_walk()
    minute = PagedLane(truth(468, 567))                       # 100 rows: 07:48 .. 09:27
    out = FZ.finalize_minute_fallback(minute, cache, SESSION, clock(9, 28, 30))
    assert minute.calls == 1
    assert cache.contiguous is True and cache.finalization_source == FZ.SOURCE_MINUTE_FALLBACK
    assert {m: tuple(v) for m, v in cache.bars.items()} == {m: bar(m) for m in range(240, CUTOFF + 1)}
    assert max(cache.bars) == CUTOFF                         # 09:25 .. 09:27 ended before the fetch, still dropped
    assert out["filled_from"] == 555 and out["filled_through"] == 558 and out["compared_minutes"] == 6
    assert cache.finalized_at == clock(9, 28, 30)() and cache.complete_through == 554   # rolling's own marker kept


def test_the_bar_still_open_at_the_fetch_is_not_merged():
    cache, _ = capped_walk()
    FZ.finalize_minute_fallback(PagedLane(truth(468, 567)), cache, SESSION, clock(9, 25, 0))
    assert max(cache.bars) == CUTOFF and cache.contiguous


def snapshot(cache):
    return (dict((m, list(v)) for m, v in cache.bars.items()), cache.contiguous, cache.finalized_at,
            cache.finalization_source, cache.data_source)


@pytest.mark.parametrize("make_lane,reason", [
    (lambda: PagedLane(truth(500, 567), size=11), "PAGE_DOES_NOT_REACH_NEED_FROM"),   # 09:17 .. 09:27, more pages
    (lambda: PagedLane([]), "EMPTY_OR_MALFORMED_PAGE"),
    (lambda: PagedLane([{k: v for k, v in r.items() if k != "cur_prc"} for r in truth(468, 567)]), "MALFORMED_ROW"),
    (lambda: PagedLane(truth(468, 567), resolved_exchange="NY"), "EXCHANGE_CHANGED"),
    (lambda: PagedLane([{**r, "trde_qty": "1"} if r["cntr_tm"].endswith("0920" + "00") else r
                        for r in truth(468, 567)]), "TICK_MINUTE_CONFLICT"),
    (lambda: PagedLane([r for r in truth(468, 567) if not r["cntr_tm"].endswith("092200")]), "TICK_MINUTE_CONFLICT"),
    (lambda: PagedLane([row(date(2026, 9, 16), 300, 1, 1, 1, 1, 1)] + truth(469, 567)), "ROW_AFTER_SESSION"),
])
def test_a_page_that_cannot_finish_the_symbol_leaves_it_exactly_as_the_walk_did(make_lane, reason):
    cache, _ = capped_walk()
    before = snapshot(cache)
    with pytest.raises(FZ.FallbackRejected) as rejected:
        FZ.finalize_minute_fallback(make_lane(), cache, SESSION, clock(9, 28))
    assert rejected.value.reason == reason
    assert snapshot(cache) == before and cache.contiguous is False


def test_a_fetch_before_0925_is_refused():
    cache, _ = capped_walk()
    with pytest.raises(FZ.FallbackRejected, match="FETCHED_BEFORE_FINALIZE"):
        FZ.finalize_minute_fallback(PagedLane(truth(468, 567)), cache, SESSION, clock(9, 24, 59))


def test_a_provider_error_propagates_and_changes_nothing():
    cache, _ = capped_walk()
    before = snapshot(cache)
    with pytest.raises(RuntimeError):
        FZ.finalize_minute_fallback(PagedLane([], error=RuntimeError("down")), cache, SESSION, clock(9, 28))
    assert snapshot(cache) == before


def test_a_short_page_with_no_continuation_covers_the_whole_session():
    cache, _ = capped_walk()
    FZ.finalize_minute_fallback(PagedLane(truth(240, 567), size=1000), cache, SESSION, clock(9, 28))
    assert cache.contiguous
    assert {m: tuple(v) for m, v in cache.bars.items()} == {m: bar(m) for m in range(240, CUTOFF + 1)}


# -- the producer morning ------------------------------------------------------------------------------

HEAVY = FZ.hash_lanes(list(E_SYMBOLS))[1][0]                 # first in the tick shard: lane B reads it first


def heavy_ticks() -> list[dict]:
    symbols = sorted(set(A_SYMBOLS) | set(E_SYMBOLS) | {"SPY"})
    rows = {int(r["cntr_tm"][8:10]) * 60 + int(r["cntr_tm"][10:12]): r
            for r in minute_rows(SESSION, 9 * 60 + 40, price=10.0 + symbols.index(HEAVY) * 0.1)}
    return newest_first([t for m in range(566, 539, -1) for t in ticks_of(rows[m], 300)])


def run(tmp_path, monkeypatch, *, minute_error=None, tick_rejections=0, enabled=True):
    original = LimitedClient.request
    sent: Counter = Counter()
    ticks = heavy_ticks()
    walked = threading.Event()               # lane B has claimed HEAVY: lane A may not steal it first

    def request(self, api_id, path, body, continuation=None):
        if body["stk_cd"] != HEAVY or self.clock() < at(time(9, 25)):
            if api_id == FZ.MINUTE_API and self.clock() >= at(time(9, 25)):
                walked.wait(5)
            return original(self, api_id, path, body, continuation)
        if api_id == FZ.TICK_API:
            walked.set()
        sent[api_id] += 1
        if api_id == FZ.MINUTE_API and isinstance(minute_error, DeadlineReached):
            raise minute_error                                      # the limiter refusing: nothing sent
        self.limiter.acquire()
        self.clock.tick(self.seconds)
        if api_id == FZ.TICK_API:
            if sent[api_id] <= tick_rejections:
                raise code7(HEAVY)
            start = int(continuation.next_key) if continuation is not None else 0
            more = start + 100 < len(ticks)
            return KiwoomPage(body={"result_list": ticks[start:start + 100]}, continuation=more,
                              next_key=str(start + 100) if more else None)
        if minute_error is not None:
            raise minute_error
        return KiwoomPage(body={"result_list": list(self.pages[HEAVY])}, continuation=False, next_key=None)

    monkeypatch.setattr(LimitedClient, "request", request)
    if not enabled:
        monkeypatch.setattr(FZ, "needs_minute_fallback", lambda cache: False)
    result, clients, limiters, _, _ = morning(tmp_path)
    monkeypatch.undo()
    return result, sent, limiters


def fallback_of(result):
    return result.report["e_finalization"]["minute_fallback"]


def test_the_capped_symbol_is_finished_by_one_minute_page_and_the_decision_is_the_clean_one(tmp_path, monkeypatch):
    clean, *_ = morning(tmp_path / "clean")
    result, sent, limiters = run(tmp_path / "heavy", monkeypatch)
    assert sent == {FZ.TICK_API: FZ.MAX_TICK_PAGES, FZ.MINUTE_API: 1}
    fb = fallback_of(result)
    assert fb["calls"] == 1 and fb["max_pages_per_symbol"] == 1 and fb["api"] == FZ.MINUTE_API
    [record] = fb["symbols"]
    assert record["symbol"] == HEAVY and record["outcome"] == FZ.SOURCE_MINUTE_FALLBACK
    assert record["tick_pages"] == FZ.MAX_TICK_PAGES and record["tick_need_from"] == 555
    assert fb["sources"].get(FZ.SOURCE_STALE, 0) == 0
    assert decision_view(result) == decision_view(clean)
    assert result.e_shadow["from_snapshot"]["unexpected_stale_count"] == 0
    assert result.report["e_finalization"]["future_cutoff_violation_count"] == 0
    for limiter in limiters:
        assert all(p["last_sent_before_deadline"] for p in limiter.declaration()["phases"])


def test_without_the_fallback_the_same_morning_is_the_10_30_outcome(tmp_path, monkeypatch):
    result, sent, _ = run(tmp_path, monkeypatch, enabled=False)
    assert sent == {FZ.TICK_API: FZ.MAX_TICK_PAGES}
    gate = result.e_shadow["from_snapshot"]["gate"]
    assert gate["status"] == "DECISION_INCOMPLETE_UNIVERSE" and gate["unexpected_stale_examples"] == [HEAVY]
    assert "minute_fallback" not in result.report["e_finalization"]


@pytest.mark.parametrize("error,reason", [(code7(HEAVY), "PROVIDER_ERROR"),
                                          (DeadlineReached("deadline reached"), "DEADLINE_REACHED")])
def test_a_failed_fallback_leaves_the_symbol_stale_and_is_never_retried(tmp_path, monkeypatch, error, reason):
    result, sent, _ = run(tmp_path, monkeypatch, minute_error=error)
    assert sent == {FZ.TICK_API: FZ.MAX_TICK_PAGES, FZ.MINUTE_API: 1}           # no second walk, no second page
    [record] = fallback_of(result)["symbols"]
    assert record["outcome"] == FZ.SOURCE_STALE and record["reason"] == reason
    assert result.e_shadow["from_snapshot"]["gate"]["unexpected_stale_examples"] == [HEAVY]
    assert result.report["e_finalization"]["provider_retry"]["retry_requests"] == 0


def test_a_rejected_first_tick_page_is_retried_and_then_falls_back_once(tmp_path, monkeypatch):
    result, sent, _ = run(tmp_path, monkeypatch, tick_rejections=1)
    assert sent == {FZ.TICK_API: 1 + FZ.MAX_TICK_PAGES, FZ.MINUTE_API: 1}
    retry = result.report["e_finalization"]["provider_retry"]
    assert retry["recovered"] == [HEAVY] and retry["retry_requests"] == 1
    assert fallback_of(result)["symbols"][0]["outcome"] == FZ.SOURCE_MINUTE_FALLBACK
    assert result.e_shadow["from_snapshot"]["unexpected_stale_count"] == 0


def verdicts(tmp_dir):
    """Every E symbol's sealed bars and contiguity. Which lane read a symbol is thread timing, so the
    snapshot bytes differ run to run even for identical code; what a symbol ends with does not."""
    body = json.loads(gzip.open(tmp_dir / "snap" / "snapshot_0924.json.gz").read())
    return {s: (e["bars"], e["contiguous"], e["complete_through"]) for s, e in body["symbols"].items()}


def test_a_normal_morning_is_the_same_with_the_fallback_switched_off(tmp_path, monkeypatch):
    on, *_ = morning(tmp_path / "on")
    monkeypatch.setattr(FZ, "needs_minute_fallback", lambda cache: False)
    off, *_ = morning(tmp_path / "off")
    assert "minute_fallback" not in on.report["e_finalization"]
    assert verdicts(tmp_path / "on") == verdicts(tmp_path / "off")
    assert decision_view(on) == decision_view(off)


# -- 2026-10-30 INTC, from its sealed bars -----------------------------------------------------------------

def test_the_10_30_intc_shape_is_stale_without_and_complete_with_the_fallback():
    """Sealed production bars: <= 08:38 (rolling) and 09:21 .. 09:24 (tick walk). Raw ticks and the
    08:39 .. 09:20 bars were never stored, so those minutes and 09:25 .. 09:27 are synthetic here."""
    fx = json.loads((FIXTURES / "strategy_e_20261030_intc_0924.json").read_text())
    day = date.fromisoformat(fx["session"])
    sealed = {b[0]: b[1:] for b in fx["bars"]}
    assert fx["complete_through"] == 518 and fx["final_pages"] == 30 and fx["contiguous"] is False
    full = dict(sealed)
    for m in list(range(519, 561)) + [565, 566, 567]:          # synthetic: absent from every artifact
        p = 69.0 + (m % 7) * 0.01
        full[m] = [p, p + 0.02, p - 0.02, p + 0.01, 60000.0 + m]
    def minute_row(m):
        return row(day, m, *full[m])
    ticks = []                                                 # 09:27 in progress, then 450 ticks a minute
    for m, n in [(567, 400)] + [(m, 450) for m in range(566, 518, -1)]:
        ticks += ticks_of(minute_row(m), n)
    def fresh():
        cache = FZ.SymbolCache("INTC", fx["exchange"], fx["code"])
        cache.bars = {m: list(v) for m, v in sealed.items() if m <= 518}
        cache.complete_through = 518
        return cache
    now = lambda: datetime(2026, 10, 30, 9, 27, 52, tzinfo=ET)

    old = fresh()
    FZ.finalize_ticks(PagedLane(newest_first(ticks)), old, day, now)
    assert old.contiguous is False and old.tick_pages == 30 and old.tick_oldest_minute == 561   # as sealed
    assert sorted(m for m in old.bars if m > 518) == [561, 562, 563, 564]
    assert all(old.bars[m] == sealed[m] for m in (562, 563, 564))
    assert FZ.audit({"INTC": old}, datetime(2026, 10, 30, 9, 29, 45, tzinfo=ET))["stale"] == 1

    new = fresh()
    FZ.finalize_ticks(PagedLane(newest_first(ticks)), new, day, now)
    assert FZ.needs_minute_fallback(new)
    minute = PagedLane(newest_first([minute_row(m) for m in range(468, 568) if m in full]))
    out = FZ.finalize_minute_fallback(minute, new, day, lambda: datetime(2026, 10, 30, 9, 27, 53, tzinfo=ET))
    assert minute.calls == 1 and new.contiguous is True and max(new.bars) == CUTOFF
    assert out["filled_from"] == 519 and out["filled_through"] == 561 and out["compared_minutes"] == 3
    assert all(new.bars[m] == sealed[m] for m in sealed if m <= 518 or m >= 562)
    assert new.bars[561] == full[561] and all(m in new.bars for m in range(519, CUTOFF + 1))
    assert FZ.audit({"INTC": new}, datetime(2026, 10, 30, 9, 29, 45, tzinfo=ET))["stale"] == 0
