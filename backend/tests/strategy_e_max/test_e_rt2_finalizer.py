"""E-RT2 finalizer: cutoff merge, lane equivalence, contiguity and staleness with a fake Kiwoom lane."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.integrations.kiwoom.client import KiwoomMarketDataClient
from app.strategy_e_max_rt import finalizer as FZ

ET = ZoneInfo("America/New_York")
D = date(2026, 9, 23)


def at(h, m, s=0):
    return datetime(2026, 9, 23, h, m, s, tzinfo=ET)


def row(h, m, o, hi, lo, c, v, day=D):
    return {"cntr_tm": f"{day:%Y%m%d}{h:02d}{m:02d}00", "bus_dt": f"{day:%Y%m%d}", "open_pric": str(o),
            "high_pric": str(hi), "low_pric": str(lo), "cur_prc": str(c), "trde_qty": str(v)}


class FakeLane:
    """Pages newest-first like Kiwoom; ``minutes`` for usa06011, ``ticks`` for usa06010."""

    def __init__(self, rows, page=100):
        self.rows, self.size, self.calls, self.errors, self.latencies = sorted(rows, key=lambda r: r["cntr_tm"], reverse=True), page, 0, 0, []

    def page(self, symbol, exchange, continuation=None):
        self.calls += 1
        start = 0 if continuation is None else continuation.next
        chunk = self.rows[start:start + self.size]
        return SimpleNamespace(body={"result_list": chunk}, continuation=start + self.size < len(self.rows),
                               next=start + self.size)


def minute_rows(until_h, until_m, start=(4, 0)):
    out, t = [], at(*start)
    while t <= at(until_h, until_m):
        p = 10 + (t.hour * 60 + t.minute) / 1000
        out.append(row(t.hour, t.minute, p, p + 0.01, p - 0.01, p, 100))
        t += timedelta(minutes=1)
    return out


def test_tick_client_adds_only_the_read_only_tick_chart() -> None:
    added = FZ.ChartLaneClient.ALLOWED_ENDPOINTS - KiwoomMarketDataClient.ALLOWED_ENDPOINTS
    assert added == {("usa06010", "/api/us/chart")}


def test_refresh_keeps_complete_minutes_only_and_extends_contiguously() -> None:
    cache = FZ.SymbolCache("X", "ND")
    FZ.refresh(FakeLane(minute_rows(5, 0)), cache, D, lambda: at(5, 0, 30))       # 05:00 in progress
    assert max(cache.bars) == 4 * 60 + 59 and cache.complete_through == 4 * 60 + 59
    FZ.refresh(FakeLane(minute_rows(8, 0)), cache, D, lambda: at(8, 0, 10))       # page 06:21-08:00: gap after 04:59
    assert cache.complete_through == 4 * 60 + 59                                   # not extended over the gap


def test_minute_finalization_merges_through_0924_and_excludes_later_bars() -> None:
    cache = FZ.SymbolCache("X", "ND")
    FZ.refresh(FakeLane(minute_rows(9, 0)), cache, D, lambda: at(9, 0, 5))
    FZ.finalize_minute(FakeLane(minute_rows(9, 27)), cache, D, lambda: at(9, 27, 2))
    assert max(cache.bars) == 9 * 60 + 24 and cache.contiguous and cache.data_source == "KIWOOM_usa06011"
    assert FZ.audit({"X": cache}, at(9, 29, 45)) == {"symbols": 1, "stale": 0, "stale_examples": [],
                                                   "evidence_after_cutoff": 0}


def test_tick_finalization_equals_minute_finalization() -> None:
    minutes = minute_rows(9, 26)
    ticks = []
    for r in minutes:                       # two ticks per minute whose aggregate is the minute bar
        o, h, l, c = (float(r[k]) for k in ("open_pric", "high_pric", "low_pric", "cur_prc"))
        ticks.append({**r, "open_pric": str(o), "high_pric": str(h), "low_pric": str(o), "cur_prc": str(o), "trde_qty": "40"})
        ticks.append({**r, "cntr_tm": r["cntr_tm"], "open_pric": str(c), "high_pric": str(c), "low_pric": str(l),
                      "cur_prc": str(c), "trde_qty": "60"})
    a, b = FZ.SymbolCache("A", "ND"), FZ.SymbolCache("B", "ND")
    for cache in (a, b):
        FZ.refresh(FakeLane(minute_rows(9, 21)), cache, D, lambda: at(9, 21, 30))
    FZ.finalize_minute(FakeLane(minutes), a, D, lambda: at(9, 26, 1))
    lane = FakeLane(ticks)
    lane.rows = list(reversed(ticks))                                        # newest first, stable within a minute
    FZ.finalize_ticks(lane, b, D, lambda: at(9, 26, 2))
    assert b.contiguous and a.contiguous
    assert {m: a.bars[m] for m in range(9 * 60 + 20, 9 * 60 + 25)} == {m: b.bars[m] for m in range(9 * 60 + 20, 9 * 60 + 25)}


def test_stale_when_finalized_before_cutoff_or_after_deadline_or_not_contiguous() -> None:
    early, late, gap = (FZ.SymbolCache(s, "ND") for s in ("E", "L", "G"))
    FZ.finalize_minute(FakeLane(minute_rows(9, 23)), early, D, lambda: at(9, 24, 30))
    FZ.finalize_minute(FakeLane(minute_rows(9, 29)), late, D, lambda: at(9, 29, 50))
    FZ.finalize_minute(FakeLane(minute_rows(9, 27, start=(8, 0))), gap, D, lambda: at(9, 27, 1))   # no cache before 07:48
    audit = FZ.audit({"E": early, "L": late, "G": gap}, at(9, 29, 45))
    assert audit["stale"] == 3


def test_lane_split_is_deterministic_by_prior_liquidity() -> None:
    a, b = FZ.split_lanes(["BIG", "MID", "SMALL"], 2)
    assert (a, b) == (["BIG", "MID"], ["SMALL"])


def test_share_class_code_mapping() -> None:
    assert [FZ.kiwoom_code(s) for s in ("BRK.B", "BF.B", "HEI.A", "MOG.A", "AAPL")] == ["BRKb", "BFb", "HEIa", "MOGa", "AAPL"]
    cache = FZ.SymbolCache("BRK.B", "NY", FZ.kiwoom_code("BRK.B"))
    seen = []

    class Spy(FakeLane):
        def page(self, symbol, exchange, continuation=None):
            seen.append(symbol)
            return super().page(symbol, exchange, continuation)
    FZ.refresh(Spy(minute_rows(5, 0)), cache, D, lambda: at(5, 0, 30))
    assert seen == ["BRKb"] and cache.symbol == "BRK.B"


def test_listed_dotted_codes_are_kept() -> None:
    listing = {"BH.A": "NY", "UHAL.B": "NY", "BRKb": "NY"}
    assert FZ.kiwoom_code("BH.A", listing) == "BH.A" and FZ.kiwoom_code("BRK.B", listing) == "BRKb"
