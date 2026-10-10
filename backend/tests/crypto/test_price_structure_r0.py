"""Price Structure R0 unit tests.

Contract: docs/crypto/price_structure_r0/CONTRACT_V2.md
Research only: nothing here touches C1/C1x, AUTO, order paths or any production module.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.crypto.research.price_structure_r0 import data as psdata
from app.crypto.research.price_structure_r0.data import Bars, aggregate, wilder_atr
from app.crypto.research.price_structure_r0.engine import Engine, EngineConfig
from app.crypto.research.price_structure_r0.events import EventEngine, RangeEdge, buffer
from app.crypto.research.price_structure_r0.levels import LevelBook, Level
from app.crypto.research.price_structure_r0.swings import RangeState, Swing, ZigZag, trend_structure
from app.crypto.research.price_structure_r0.vwap import SessionVwap

MIN = 60_000


def mk(tf: str, step: int, closes, highs=None, lows=None, vol=1.0, t0: int = 0) -> Bars:
    c = np.asarray(closes, dtype=float)
    h = np.asarray(highs, dtype=float) if highs is not None else c
    l = np.asarray(lows, dtype=float) if lows is not None else c
    o = np.concatenate([[c[0]], c[:-1]])
    v = np.full(len(c), float(vol))
    return Bars(tf, step, np.arange(t0, t0 + step * len(c), step, dtype=np.int64),
                o, h, l, c, v, v * c, wilder_atr(h, l, c))


# --------------------------------------------------------------------- ATR, bars
def test_wilder_atr_seed_and_recursion():
    h = np.arange(10.0, 30.0)
    l = h - 2.0
    c = h - 1.0
    atr = wilder_atr(h, l, c, period=14)
    assert np.isnan(atr[:14]).all()
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    assert atr[14] == pytest.approx(float(np.mean(tr[:14])))
    assert atr[15] == pytest.approx((atr[14] * 13 + tr[14]) / 14)


def test_aggregate_folds_and_requires_alignment():
    c = np.arange(1.0, 11.0)
    b1 = mk("1m", MIN, c, highs=c + 1, lows=c - 1)
    b5 = aggregate(b1, "5m", 5)
    assert len(b5) == 2
    assert b5.open[0] == b1.open[0] and b5.close[0] == c[4]
    assert b5.high[0] == (c[:5] + 1).max() and b5.low[0] == (c[:5] - 1).min()
    assert b5.volume[0] == pytest.approx(b1.volume[:5].sum())
    with pytest.raises(RuntimeError):
        aggregate(mk("1m", MIN, c, t0=MIN), "5m", 5)


def test_aggregate_drops_the_incomplete_tail():
    c = np.arange(1.0, 8.0)  # seven 1m bars, one complete 5m bar
    assert len(aggregate(mk("1m", MIN, c), "5m", 5)) == 1


# ------------------------------------------------------------------------ swings
def _ramp_then_drop(n_up: int, n_dn: int) -> Bars:
    up = list(np.linspace(100.0, 160.0, n_up))
    dn = list(np.linspace(160.0, 100.0, n_dn))
    c = np.asarray(up + dn)
    return mk("1h", 3_600_000, c, highs=c + 0.5, lows=c - 0.5)


def test_zigzag_confirms_a_swing_after_the_pivot_and_never_before():
    b = _ramp_then_drop(40, 40)
    zz = ZigZag("1h", 1.0, label_frame=True)
    out = []
    for i in range(len(b)):
        for s in zz.on_bar(b, i):
            out.append((i, s))
    assert out, "expected at least one confirmed swing"
    # The series only rises once ATR exists, so the first structural point is the low it rose from.
    assert out[0][1].kind == "SWING_LOW"
    peak = next((i, s) for i, s in out if s.kind == "SWING_HIGH")
    i, s = peak
    assert s.confirmed_at == b.close_ms(i)
    assert s.pivot_at < s.confirmed_at
    assert s.price == pytest.approx(float(b.high.max()))
    for _, sw in out:
        assert sw.pivot_at < sw.confirmed_at


def test_zigzag_is_prefix_stable():
    b = _ramp_then_drop(40, 40)
    full = ZigZag("1h", 1.0, label_frame=True)
    for i in range(len(b)):
        full.on_bar(b, i)
    for cut in (30, 50, 65, 80):
        part = ZigZag("1h", 1.0, label_frame=True)
        for i in range(cut):
            part.on_bar(b, i)
        assert [s.immutable() for s in part.confirmed] == \
               [s.immutable() for s in full.confirmed[:len(part.confirmed)]]


def test_swing_objects_are_frozen():
    s = Swing("1h", "SWING_HIGH", 1.0, 0, MIN, 1.0, "HH")
    with pytest.raises(Exception):
        s.price = 2.0  # type: ignore[misc]


def test_trend_structure_requires_both_sides():
    zz = ZigZag("1h", 1.0, label_frame=True)
    zz.confirmed = [Swing("1h", "SWING_HIGH", 2.0, 0, 1, 1.0, "HH")]
    assert trend_structure(zz)[0] == "NEUTRAL"
    zz.confirmed.append(Swing("1h", "SWING_LOW", 1.0, 0, 2, 1.0, "HL"))
    assert trend_structure(zz)[0] == "BULLISH"
    zz.confirmed = [Swing("1h", "SWING_HIGH", 2.0, 0, 1, 1.0, "LH"),
                    Swing("1h", "SWING_LOW", 1.0, 0, 2, 1.0, "LL")]
    assert trend_structure(zz)[0] == "BEARISH"
    zz.confirmed = [Swing("1h", "SWING_HIGH", 2.0, 0, 1, 1.0, "HH"),
                    Swing("1h", "SWING_LOW", 1.0, 0, 2, 1.0, "LL")]
    assert trend_structure(zz)[0] == "NEUTRAL"


def test_hh_hl_labels_are_frozen_at_confirmation():
    zz = ZigZag("1h", 1.0, label_frame=True)
    assert zz._label("SWING_HIGH", 10.0) == "UNLABELED"
    zz.prev_high_price = 10.0
    assert zz._label("SWING_HIGH", 11.0) == "HH"
    assert zz._label("SWING_HIGH", 9.0) == "LH"
    assert zz._label("SWING_HIGH", 10.0) == "HH"  # equality ties to the bullish label
    zz.prev_low_price = 5.0
    assert zz._label("SWING_LOW", 6.0) == "HL"
    assert zz._label("SWING_LOW", 4.0) == "LL"


# ------------------------------------------------------------------------ levels
def _sw(tf: str, price: float, at: int, kind: str = "SWING_HIGH") -> Swing:
    return Swing(tf, kind, price, at - 1, at, price, "NA" if tf == "5m" else "HH")


def test_merge_discards_the_candidate_so_a_line_never_moves():
    bk = LevelBook()
    a = bk.offer(_sw("1h", 100.0, 10), close=90.0, atr_1h=4.0, atr_5m=1.0, now_ms=10)
    assert a is not None and a.touches == 1
    assert bk.offer(_sw("1h", 100.9, 20), close=90.0, atr_1h=4.0, atr_5m=1.0, now_ms=20) is None
    assert a.price == 100.0 and a.touches == 2 and len(bk.active) == 1


def test_a_5m_candidate_near_an_active_1h_level_is_not_created():
    bk = LevelBook()
    bk.offer(_sw("1h", 100.0, 10), close=90.0, atr_1h=4.0, atr_5m=4.0, now_ms=10)
    assert bk.offer(_sw("5m", 100.5, 20), close=90.0, atr_1h=4.0, atr_5m=4.0, now_ms=20) is None
    assert bk.suppressed_by_1h == 1 and len(bk.active) == 1


def test_a_1h_candidate_on_top_of_a_5m_level_is_still_created():
    """The contract's cross timeframe rule is one directional. This pins the known duplication."""
    bk = LevelBook()
    bk.offer(_sw("5m", 100.0, 10), close=90.0, atr_1h=4.0, atr_5m=4.0, now_ms=10)
    assert bk.offer(_sw("1h", 100.0, 20), close=90.0, atr_1h=4.0, atr_5m=4.0, now_ms=20) is not None
    assert len(bk.active) == 2


def test_side_is_set_from_the_close_that_confirmed_the_swing():
    bk = LevelBook()
    above = bk.offer(_sw("1h", 100.0, 10), close=90.0, atr_1h=1.0, atr_5m=1.0, now_ms=10)
    below = bk.offer(_sw("1h", 50.0, 20), close=90.0, atr_1h=1.0, atr_5m=1.0, now_ms=20)
    assert above.side == "DOWN" and below.side == "UP"


def test_touch_cooldown_blocks_a_second_touch_inside_six_bars():
    bk = LevelBook()
    lv = bk.offer(_sw("1h", 100.0, 0), close=90.0, atr_1h=1.0, atr_5m=1.0, now_ms=0)
    seen: dict[int, int] = {}
    assert bk.touch_scan(100.1, 99.9, 1.0, 1000, 0, seen) == [lv.id]
    assert bk.touch_scan(100.1, 99.9, 1.0, 2000, 3, seen) == []
    assert bk.touch_scan(100.1, 99.9, 1.0, 3000, 6, seen) == [lv.id]
    assert lv.touches == 3


def test_expiry_reasons():
    day = 86_400_000
    bk = LevelBook()
    old1h = bk.offer(_sw("1h", 100.0, 0), close=100.0, atr_1h=1.0, atr_5m=1.0, now_ms=0)
    assert bk.expire_scan(100.0, 1.0, 11 * day) == [old1h.id]
    assert bk.levels[old1h.id].state == "EXPIRED"
    bk2 = LevelBook()
    old5m = bk2.offer(_sw("5m", 100.0, 0), close=100.0, atr_1h=1.0, atr_5m=1.0, now_ms=0)
    assert bk2.expire_scan(100.0, 1.0, 49 * 3_600_000) == [old5m.id]
    bk3 = LevelBook()
    far = bk3.offer(_sw("1h", 100.0, 0), close=100.0, atr_1h=1.0, atr_5m=1.0, now_ms=0)
    assert bk3.expire_scan(100.0, 1.0, 2 * day) == []  # close is still on the level
    assert bk3.expire_scan(200.0, 1.0, 2 * day) == [far.id]


def test_publish_caps_ten_overall_and_five_per_side():
    bk = LevelBook()
    for n in range(12):
        bk.offer(_sw("5m", 100.0 + 10 * n, 10 * n), close=50.0, atr_1h=1.0, atr_5m=1.0, now_ms=10 * n)
    for n in range(12):
        bk.offer(_sw("5m", 40.0 - n, 500 + n), close=50.0, atr_1h=1.0, atr_5m=1.0, now_ms=500 + n)
    out = bk.publish(50.0, 1.0, 1000)
    assert len(out["published_levels"]) == 10
    assert sum(1 for p in out["published_levels"] if p["price"] > 50) == 5
    assert out["nearest_resistance"]["price"] == 100.0
    assert out["nearest_support"]["price"] == 40.0
    assert out["active_level_count"] == 24


def test_role_is_derived_from_where_price_is():
    lv = Level(1, "1h", 100.0, "SWING_HIGH", 0, 0, "DOWN")
    assert lv.role(90.0, 1.0) == "RESISTANCE"
    assert lv.role(110.0, 1.0) == "SUPPORT"
    assert lv.role(100.0, 1.0) == "AT_PRICE"


# ------------------------------------------------------------------------ events
def test_buffer_takes_the_larger_of_ten_basis_points_and_a_quarter_atr():
    assert buffer(100_000.0, 1.0) == pytest.approx(100.0)
    assert buffer(100_000.0, 1_000.0) == pytest.approx(250.0)


def test_a_level_already_below_price_cannot_break_up():
    """Contract 17.1. Without the side rule this fires on the bar the level is born."""
    ev = EventEngine()
    lv = Level(1, "5m", 100.0, "SWING_LOW", 0, 0, side="UP")
    assert ev.scan_breakouts([lv], "5m", 200.0, 201.0, 1.0, 1000, 5) == []
    lv2 = Level(2, "5m", 100.0, "SWING_HIGH", 0, 0, side="DOWN")
    fired = ev.scan_breakouts([lv2], "5m", 200.0, 201.0, 1.0, 1000, 5)
    assert len(fired) == 1 and fired[0].direction == "UP" and lv2.side == "UP"


def test_breakout_needs_two_closes_beyond_the_buffer():
    ev = EventEngine()
    lv = Level(1, "5m", 100.0, "SWING_HIGH", 0, 0, side="DOWN")
    assert ev.scan_breakouts([lv], "5m", 99.0, 101.0, 1.0, 1000, 5) == []
    assert len(ev.scan_breakouts([lv], "5m", 101.0, 102.0, 1.0, 2000, 6)) == 1


def test_rebreak_cooldown_is_per_direction_and_suppresses_only_the_event():
    ev = EventEngine()
    lv = Level(1, "5m", 100.0, "SWING_HIGH", 0, 0, side="DOWN")
    assert len(ev.scan_breakouts([lv], "5m", 101.0, 102.0, 1.0, 1000, 0)) == 1  # UP at bar 0
    assert len(ev.scan_breakouts([lv], "5m", 98.0, 97.0, 1.0, 2000, 5)) == 1  # DOWN is a new dir
    assert ev.scan_breakouts([lv], "5m", 101.0, 102.0, 1.0, 3000, 8) == []  # UP again, bar 8 < 24
    assert lv.side == "UP" and ev.cooldown_suppressed == 1
    assert len(ev.scan_breakouts([lv], "5m", 98.0, 97.0, 1.0, 4000, 40)) == 1


def test_range_edge_breakout_is_reported_as_its_own_source():
    ev = EventEngine()
    edge = RangeEdge("RANGE_HIGH", 100.0, "DOWN")
    fired = ev.scan_breakouts([edge], "5m", 101.0, 102.0, 1.0, 1000, 5)
    assert len(fired) == 1 and fired[0].source == "RANGE_EDGE" and fired[0].level_id == "RANGE_HIGH"


def _open_up_breakout(ev: EventEngine, price=100.0, at=0, tf="5m"):
    lv = Level(1, tf, price, "SWING_HIGH", 0, 0, side="DOWN")
    bo = ev.scan_breakouts([lv], tf, price * 1.01, price * 1.011, 1.0, at, 5)[0]
    ev.open_retest(bo, at)
    return bo


def test_retest_held_requires_a_touch_then_two_closes_back():
    ev = EventEngine()
    bo = _open_up_breakout(ev, at=0)
    p, b = bo.level_price, bo.buffer
    ev.step_retest(p + b + 1, p + b + 1, p + b + 2, 300_000)
    assert ev.active_retest.state == "AWAITING_RETEST"
    ev.step_retest(p + b + 1, p + b - 1, p + b + 2, 600_000)
    assert ev.active_retest.state == "RETEST_TOUCHED"
    ev.step_retest(p + b + 5, p + b, p + b + 6, 900_000)
    assert ev.active_retest.state == "RETEST_TOUCHED"
    ev.step_retest(p + b + 5, p + b, p + b + 6, 1_200_000)
    assert ev.active_retest.state == "RETEST_HELD"


def test_retest_failed_is_the_failed_break_and_decays():
    ev = EventEngine()
    bo = _open_up_breakout(ev, at=0)
    p, b = bo.level_price, bo.buffer
    ev.step_retest(p - b - 1, p - b - 2, p, 300_000)
    ev.step_retest(p - b - 1, p - b - 2, p, 600_000)
    assert ev.active_retest.state == "RETEST_FAILED"
    assert ev.failed_break_state(600_000) == "FAILED_BREAK_UP"
    assert ev.failed_break_state(600_000 + 48 * 5 * 60_000) == "FAILED_BREAK_UP"
    assert ev.failed_break_state(600_000 + 49 * 5 * 60_000) == "NONE"


def test_window_elapse_terminates_both_awaiting_and_touched():
    ev = EventEngine()
    bo = _open_up_breakout(ev, at=0)
    ev.step_retest(bo.level_price * 1.02, bo.level_price * 1.02, bo.level_price * 1.02,
                   bo.confirmed_at + 48 * 5 * 60_000)
    assert ev.active_retest.state == "NO_RETEST"
    ev2 = EventEngine()
    bo2 = _open_up_breakout(ev2, at=0)
    p, b = bo2.level_price, bo2.buffer
    ev2.step_retest(p + b + 1, p + b - 1, p + b + 2, 300_000)
    assert ev2.active_retest.state == "RETEST_TOUCHED"
    ev2.step_retest(p + b + 1, p + b + 1, p + b + 2, 48 * 5 * 60_000)
    assert ev2.active_retest.state == "RETEST_INCONCLUSIVE"


def test_a_new_breakout_supersedes_the_open_machine():
    ev = EventEngine()
    first = _open_up_breakout(ev, at=0)
    lv = Level(2, "5m", 200.0, "SWING_HIGH", 0, 0, side="DOWN")
    bo2 = ev.scan_breakouts([lv], "5m", 210.0, 211.0, 1.0, 300_000, 6)[0]
    ev.open_retest(bo2, 300_000)
    assert ev.retests[0].state == "SUPERSEDED" and ev.retests[0].breakout is first
    assert ev.active_retest.breakout is bo2


def test_retest_window_is_48_bars_of_the_level_frame():
    ev = EventEngine()
    bo = _open_up_breakout(ev, at=0, tf="1h")
    assert ev.active_retest.deadline_ms == 48 * 60 * 60_000
    ev2 = EventEngine()
    _open_up_breakout(ev2, at=0, tf="5m")
    assert ev2.active_retest.deadline_ms == 48 * 5 * 60_000


def test_sweep_needs_the_wick_the_close_back_inside_and_the_next_close_inside():
    ev = EventEngine()
    lv = Level(1, "5m", 100.0, "SWING_HIGH", 0, 0, side="DOWN")
    assert ev.scan_sweeps([lv], high=105.0, low=95.0, close=99.0, atr_5m=1.0,
                          at_ms=1000, bar_idx=5, broke_now=set()) == []
    out = ev.scan_sweeps([lv], high=99.5, low=95.0, close=98.0, atr_5m=1.0,
                         at_ms=2000, bar_idx=6, broke_now=set())
    assert len(out) == 1 and out[0].direction == "UP" and out[0].extreme == 105.0
    assert out[0].wick_at == 1000 and out[0].confirmed_at == 2000


def test_sweep_is_dropped_when_the_next_close_is_beyond_the_level():
    ev = EventEngine()
    lv = Level(1, "5m", 100.0, "SWING_HIGH", 0, 0, side="DOWN")
    ev.scan_sweeps([lv], 105.0, 95.0, 99.0, 1.0, 1000, 5, set())
    assert ev.scan_sweeps([lv], 101.0, 99.0, 100.5, 1.0, 2000, 6, set()) == []
    assert ev.sweeps == []


def test_a_level_below_price_is_only_swept_from_above():
    """Contract 17.2: a wick over a level that sits under price is ordinary trading."""
    ev = EventEngine()
    lv = Level(1, "5m", 100.0, "SWING_LOW", 0, 0, side="UP")
    ev.scan_sweeps([lv], high=105.0, low=99.0, close=104.0, atr_5m=1.0,
                   at_ms=1000, bar_idx=5, broke_now=set())
    assert (1, "UP") not in ev._pending_sweeps
    ev2 = EventEngine()
    lv2 = Level(1, "5m", 100.0, "SWING_LOW", 0, 0, side="UP")
    ev2.scan_sweeps([lv2], high=105.0, low=95.0, close=104.0, atr_5m=1.0,
                    at_ms=1000, bar_idx=5, broke_now=set())
    assert (1, "DOWN") in ev2._pending_sweeps


# -------------------------------------------------------------------------- vwap
def test_vwap_is_turnover_over_volume_and_resets_each_utc_day():
    v = SessionVwap()
    v.on_1m(0, 11.0, 9.0, 10.0, volume=2.0, turnover=20.0)
    v.on_1m(MIN, 21.0, 19.0, 20.0, volume=1.0, turnover=40.0)
    assert v.vwap == pytest.approx(60.0 / 3.0)
    v.on_1m(86_400_000, 5.0, 5.0, 5.0, volume=1.0, turnover=7.0)
    assert v.vwap == pytest.approx(7.0)


def test_vwap_position_bands():
    v = SessionVwap()
    v.on_1m(0, 11.0, 9.0, 10.0, 1.0, 10.0)
    v.on_1m(MIN, 31.0, 29.0, 30.0, 1.0, 30.0)
    z, pos = v.position(v.vwap)
    assert pos == "AT" and z == pytest.approx(0.0)
    assert v.position(v.vwap + 3 * v.sigma)[1] == "ABOVE_UPPER"
    assert v.position(v.vwap + 0.5 * v.sigma)[1] == "ABOVE"
    assert v.position(v.vwap - 0.5 * v.sigma)[1] == "BELOW"
    assert v.position(v.vwap - 3 * v.sigma)[1] == "BELOW_LOWER"


def test_zero_volume_session_is_reported_as_AT_not_as_a_crash():
    v = SessionVwap()
    v.on_1m(0, 1.0, 1.0, 1.0, volume=0.0, turnover=0.0)
    assert v.position(1.0) == (0.0, "AT")


# ------------------------------------------------------------------------- range
def test_range_freezes_only_when_the_edges_are_revisited():
    zz = ZigZag("1h", 1.0, label_frame=True)
    zz.confirmed = [Swing("1h", "SWING_HIGH", 110.0, 0, 1, 1.0, "HH"),
                    Swing("1h", "SWING_LOW", 100.0, 0, 2, 1.0, "HL"),
                    Swing("1h", "SWING_HIGH", 109.5, 0, 3, 1.0, "LH"),
                    Swing("1h", "SWING_LOW", 100.5, 0, 4, 1.0, "HL")]
    b = mk("1h", 3_600_000, np.full(40, 105.0), highs=np.full(40, 106.0), lows=np.full(40, 104.0))
    r = RangeState()
    r.evaluate_1h(zz, b, 30)
    assert r.state == "IN_RANGE" and r.high == 110.0 and r.low == 100.0
    zz.confirmed[-1] = Swing("1h", "SWING_LOW", 106.0, 0, 4, 1.0, "HL")  # edge not revisited
    r2 = RangeState()
    r2.evaluate_1h(zz, b, 30)
    assert r2.state == "NO_RANGE"


def test_range_broken_state_clears_after_24_1h_bars():
    r = RangeState(state="IN_RANGE", high=110.0, low=100.0, frozen_at=0)
    r.on_edge_break("UP", 1000, t_1h=10)
    assert r.state == "RANGE_BROKEN_UP"
    zz = ZigZag("1h", 1.0, label_frame=True)
    b = mk("1h", 3_600_000, np.full(60, 105.0))
    r.evaluate_1h(zz, b, 33)
    assert r.state == "RANGE_BROKEN_UP"
    r.evaluate_1h(zz, b, 34)
    assert r.state == "NO_RANGE" and r.high is None


# ------------------------------------------------------------------------ engine
@pytest.fixture(scope="module")
def real_bars():
    try:
        return psdata.load()
    except RuntimeError as exc:
        pytest.skip(f"research raw data absent: {exc}")


def test_engine_state_is_identical_when_the_future_is_removed(real_bars):
    """The repaint test. Truncating the data at T must reproduce the full run's state at T."""
    b1 = real_bars["1m"]
    T = psdata.REPLAY_START_MS + 14 * 86_400_000
    cut = int((T - int(b1.ts[0])) // MIN)
    sl = lambda x: x[:cut]
    trunc1 = Bars("1m", MIN, sl(b1.ts), sl(b1.open), sl(b1.high), sl(b1.low), sl(b1.close),
                  sl(b1.volume), sl(b1.turnover), wilder_atr(sl(b1.high), sl(b1.low), sl(b1.close)))
    trunc = {"1m": trunc1, "5m": aggregate(trunc1, "5m", 5), "1h": aggregate(trunc1, "1h", 60)}
    a = Engine(trunc, EngineConfig(3.0, 6.0, T, T)); a.run()
    b = Engine(real_bars, EngineConfig(3.0, 6.0, T, T)); b.run()
    assert len(a.hourly_snapshots) == 1 and len(b.hourly_snapshots) == 1
    assert a.hourly_snapshots[0] == b.hourly_snapshots[0]


def test_engine_reports_no_repaint_violations_over_a_short_window(real_bars):
    start = psdata.REPLAY_START_MS
    eng = Engine(real_bars, EngineConfig(3.0, 6.0, start, start + 3 * 86_400_000))
    eng.run()
    assert eng.audit.violations == []
    assert eng.audit.vanished == []
    assert eng.counters["snapshots"] == 3 * 1440 + 1
    assert max(eng.counters["published"]) <= 10


def test_engine_never_publishes_a_level_before_it_was_confirmed(real_bars):
    start = psdata.REPLAY_START_MS
    eng = Engine(real_bars, EngineConfig(3.0, 6.0, start, start + 2 * 86_400_000))
    eng.run()
    for snap in eng.hourly_snapshots:
        for p in snap["published_levels"]:
            assert p["created_at"] <= snap["info_ts"]
            assert p["pivot_at"] < p["created_at"]
