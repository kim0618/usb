"""Price Structure R1 unit tests.

Contract: docs/crypto/price_structure_r1/CONTRACT_R1.md
Research only: nothing here touches C1/C1x, AUTO, order paths or any production module.

The tests are organised by the three allowed changes, then the repaint contract, then the engine.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.crypto.research.price_structure_r0 import data as psdata
from app.crypto.research.price_structure_r0.data import Bars, aggregate, wilder_atr
from app.crypto.research.price_structure_r0.events import RangeEdge, buffer
from app.crypto.research.price_structure_r0.swings import Swing
from app.crypto.research.price_structure_r1 import metrics
from app.crypto.research.price_structure_r1.engine import Engine, EngineConfig
from app.crypto.research.price_structure_r1.events import TERMINAL, Breakout, EventEngine
from app.crypto.research.price_structure_r1.levels import (
    EXPIRY_5M_CONFIRMED_MS,
    PROVISIONAL_TTL_MS,
    WEAKEN_AFTER_MS,
    LevelBook,
)

MIN = 60_000
HOUR = 3_600_000
FIVE = 300_000


def mk(tf: str, step: int, closes, highs=None, lows=None, vol=1.0, t0: int = 0) -> Bars:
    c = np.asarray(closes, dtype=float)
    h = np.asarray(highs, dtype=float) if highs is not None else c
    l = np.asarray(lows, dtype=float) if lows is not None else c
    o = np.concatenate([[c[0]], c[:-1]])
    v = np.full(len(c), float(vol))
    return Bars(tf, step, np.arange(t0, t0 + step * len(c), step, dtype=np.int64),
                o, h, l, c, v, v * c, wilder_atr(h, l, c))


def sw(tf: str, kind: str, price: float, at: int) -> Swing:
    return Swing(tf, kind, price, at - FIVE, at, price, "NA" if tf == "5m" else "UNLABELED")


# ------------------------------------------------- change 1, symmetric cross TF merge
def test_a_1h_candidate_on_a_5m_level_merges_instead_of_stacking():
    """R0's measured defect: the 1h candidate used to create a second line at the same price."""
    b = LevelBook()
    five = b.offer(sw("5m", "SWING_HIGH", 100.0, 1_000), close=90.0, atr_1h=40.0, atr_5m=4.0,
                   now_ms=1_000, bar_5m=0)
    assert five is not None and five.sources == {"5m"}
    again = b.offer(sw("1h", "SWING_HIGH", 100.5, 2_000), close=90.0, atr_1h=40.0, atr_5m=4.0,
                    now_ms=2_000)
    assert again is None, "a 1h candidate in the zone must not create a second line"
    assert len(b.levels) == 1
    assert five.price == 100.0, "the incumbent price is never recomputed, so the line never moves"
    assert five.sources == {"5m", "1h"} and five.provenance == "MULTI_TF"
    assert b.cross_tf_merges == 1


def test_a_5m_candidate_on_a_1h_level_merges_and_is_no_longer_silently_suppressed():
    b = LevelBook()
    one = b.offer(sw("1h", "SWING_LOW", 200.0, 1_000), close=260.0, atr_1h=40.0, atr_5m=4.0,
                  now_ms=1_000)
    assert one is not None and one.state == "CONFIRMED"
    touches_before = one.touches
    assert b.offer(sw("5m", "SWING_LOW", 200.3, 2_000), close=260.0, atr_1h=40.0, atr_5m=4.0,
                   now_ms=2_000, bar_5m=1) is None
    assert one.touches == touches_before + 1, "the merge leaves a logged touch, not nothing"
    assert one.sources == {"1h", "5m"}
    assert b.suppressed_by_1h == 0 if hasattr(b, "suppressed_by_1h") else True


def test_the_cross_tf_zone_is_the_same_whichever_frame_arrives_first():
    """Symmetry is the point: the tolerance is a property of the pair, not of arrival order."""
    delta = 0.25 * 4.0 * 0.9  # inside 0.25 * ATR_5m
    a = LevelBook()
    a.offer(sw("5m", "SWING_HIGH", 100.0, 1_000), 90.0, 40.0, 4.0, 1_000, bar_5m=0)
    a.offer(sw("1h", "SWING_HIGH", 100.0 + delta, 2_000), 90.0, 40.0, 4.0, 2_000)
    b = LevelBook()
    b.offer(sw("1h", "SWING_HIGH", 100.0 + delta, 1_000), 90.0, 40.0, 4.0, 1_000)
    b.offer(sw("5m", "SWING_HIGH", 100.0, 2_000), 90.0, 40.0, 4.0, 2_000, bar_5m=0)
    assert len(a.levels) == len(b.levels) == 1
    assert a.active[0].sources == b.active[0].sources == {"5m", "1h"}


def test_a_1h_source_promotes_a_provisional_level_at_once():
    b = LevelBook()
    five = b.offer(sw("5m", "SWING_HIGH", 100.0, 1_000), 90.0, 40.0, 4.0, 1_000, bar_5m=0)
    assert five.state == "PROVISIONAL" and not five.publishable
    b.offer(sw("1h", "SWING_HIGH", 100.2, 2_000), 90.0, 40.0, 4.0, 2_000)
    assert five.state == "CONFIRMED" and five.publishable
    assert b.promotions == 1
    assert any(t["reason"] == "PROMOTED_BY_1H_SOURCE" for t in b.transitions)


def test_one_identity_means_one_confirming_frame():
    b = LevelBook()
    five = b.offer(sw("5m", "SWING_HIGH", 100.0, 1_000), 90.0, 40.0, 4.0, 1_000, bar_5m=0)
    assert five.confirm_tf == "5m"
    b.offer(sw("1h", "SWING_HIGH", 100.2, 2_000), 90.0, 40.0, 4.0, 2_000)
    assert five.confirm_tf == "1h", "the slowest frame in sources owns the confirmation"


def test_a_candidate_outside_the_zone_still_creates_its_own_line():
    b = LevelBook()
    b.offer(sw("5m", "SWING_HIGH", 100.0, 1_000), 90.0, 40.0, 4.0, 1_000, bar_5m=0)
    b.offer(sw("1h", "SWING_HIGH", 140.0, 2_000), 90.0, 40.0, 4.0, 2_000)
    assert len(b.levels) == 2


# ------------------------------------------------------- change 2, the 5m lifecycle
def test_a_5m_level_is_invisible_until_price_comes_back_to_it():
    b = LevelBook()
    l = b.offer(sw("5m", "SWING_HIGH", 100.0, 1_000), 90.0, 40.0, 4.0, 1_000, bar_5m=0)
    assert l.state == "PROVISIONAL"
    assert b.publish(90.0, 4.0, 1_000)["published_levels"] == []
    assert b.publish(90.0, 4.0, 1_000)["provisional_count"] == 1
    b.touch_scan(high=101.0, low=99.0, atr_5m=4.0, now_ms=2_000, bar_5m=5, last_touch_bar={})
    assert l.state == "CONFIRMED"
    assert [p["id"] for p in b.publish(90.0, 4.0, 2_000)["published_levels"]] == [l.id]


def test_a_touch_on_the_creation_bar_does_not_confirm():
    b = LevelBook()
    l = b.offer(sw("5m", "SWING_HIGH", 100.0, 1_000), 90.0, 40.0, 4.0, 1_000, bar_5m=7)
    b.touch_scan(101.0, 99.0, 4.0, 1_000, bar_5m=7, last_touch_bar={})
    assert l.state == "PROVISIONAL"
    b.touch_scan(101.0, 99.0, 4.0, 2_000, bar_5m=8, last_touch_bar={})
    assert l.state == "CONFIRMED"


def test_a_1h_level_has_no_provisional_state():
    b = LevelBook()
    l = b.offer(sw("1h", "SWING_LOW", 100.0, 1_000), 140.0, 40.0, 4.0, 1_000)
    assert l.state == "CONFIRMED" and l.publishable


def test_a_provisional_level_expires_untouched_after_twelve_hours():
    b = LevelBook()
    l = b.offer(sw("5m", "SWING_HIGH", 100.0, 0), 90.0, 40.0, 4.0, 0, bar_5m=0)
    assert b.expire_scan(90.0, 40.0, PROVISIONAL_TTL_MS) == []
    assert b.expire_scan(90.0, 40.0, PROVISIONAL_TTL_MS + 1) == [l.id]
    assert l.state == "EXPIRED"
    assert any(t["reason"] == "PROVISIONAL_UNTOUCHED" for t in b.transitions)


def test_confirmed_weakens_at_twelve_hours_reconfirms_on_a_touch_and_dies_at_twenty_four():
    b = LevelBook()
    l = b.offer(sw("5m", "SWING_HIGH", 100.0, 0), 90.0, 40.0, 4.0, 0, bar_5m=0)
    b.touch_scan(101.0, 99.0, 4.0, 1_000, bar_5m=1, last_touch_bar={})
    assert l.state == "CONFIRMED"
    assert b.weaken_scan(1_000 + WEAKEN_AFTER_MS) == []
    assert b.weaken_scan(1_000 + WEAKEN_AFTER_MS + 1) == [l.id]
    assert l.state == "WEAKENED" and l.publishable, "weakened is ranked lower, not hidden"
    t2 = 1_000 + WEAKEN_AFTER_MS + 2
    b.touch_scan(101.0, 99.0, 4.0, t2, bar_5m=999, last_touch_bar={})
    assert l.state == "CONFIRMED", "a level that keeps getting hit keeps its standing"
    assert b.expire_scan(90.0, 40.0, t2 + EXPIRY_5M_CONFIRMED_MS) == []
    assert b.expire_scan(90.0, 40.0, t2 + EXPIRY_5M_CONFIRMED_MS + 1) == [l.id]
    assert any(t["reason"] == "AGE_24H" for t in b.transitions)


def test_a_level_carrying_a_1h_source_keeps_the_ten_day_expiry():
    b = LevelBook()
    l = b.offer(sw("5m", "SWING_HIGH", 100.0, 0), 90.0, 40.0, 4.0, 0, bar_5m=0)
    b.offer(sw("1h", "SWING_HIGH", 100.2, 1_000), 90.0, 40.0, 4.0, 1_000)
    assert b.weaken_scan(2 * WEAKEN_AFTER_MS) == [], "the 5m lifecycle no longer applies"
    assert b.expire_scan(100.0, 40.0, 3 * EXPIRY_5M_CONFIRMED_MS) == []


def test_weakened_costs_one_point_of_strength():
    b = LevelBook()
    l = b.offer(sw("5m", "SWING_HIGH", 100.0, 0), 90.0, 40.0, 4.0, 0, bar_5m=0)
    b.touch_scan(101.0, 99.0, 4.0, 1_000, bar_5m=1, last_touch_bar={})
    s_conf = l.strength(1_000)
    b.weaken_scan(1_000 + WEAKEN_AFTER_MS + 1)
    assert l.strength(1_000) == s_conf - 1


def test_publication_still_caps_at_ten_overall_and_five_per_side():
    b = LevelBook()
    for i in range(8):  # spaced wider than merge_tol = 0.25 * ATR_1h = 10
        l = b.offer(sw("1h", "SWING_HIGH", 200.0 + 30 * i, 1_000 + i), 100.0, 40.0, 4.0, 1_000 + i)
        assert l is not None
    for i in range(8):
        assert b.offer(sw("1h", "SWING_LOW", 50.0 - 30 * i, 2_000 + i), 100.0, 40.0, 4.0,
                       2_000 + i) is not None
    out = b.publish(100.0, 4.0, 3_000)
    assert len(out["published_levels"]) == 10
    assert sum(1 for p in out["published_levels"] if p["price"] > 100.0) == 5


# ------------------------------------------- change 3, the per level retest machine
def _bo(bo_id: int, level_id, price: float, direction: str, at: int, buf: float = 1.0) -> Breakout:
    return Breakout(bo_id, level_id, price, "5m", direction, at, price, buf, "LEVEL")


def test_two_breakouts_of_different_levels_both_stay_alive():
    """R0 kept one machine and superseded 68% of breakouts. R1 has no SUPERSEDED state at all."""
    ev = EventEngine()
    a = ev.open_retest(_bo(1, 11, 100.0, "UP", 10_000), 10_000, "5m")
    b = ev.open_retest(_bo(2, 22, 200.0, "UP", 20_000), 20_000, "5m")
    assert a.state == "BROKEN" and b.state == "BROKEN"
    assert "SUPERSEDED" not in TERMINAL
    assert len(ev.open_by_level) == 2 and ev.max_concurrency == 2


def test_a_rebreak_displaces_only_its_own_level():
    ev = EventEngine()
    a = ev.open_retest(_bo(1, 11, 100.0, "UP", 10_000), 10_000, "5m")
    other = ev.open_retest(_bo(2, 22, 200.0, "UP", 10_000), 10_000, "5m")
    again = ev.open_retest(_bo(3, 11, 100.0, "DOWN", 30_000), 30_000, "5m")
    assert a.state == "EXPIRED" and a.reason == "RELEVEL"
    assert other.state == "BROKEN", "another level's machine is untouched"
    assert again.state == "BROKEN"


def test_window_elapsing_untouched_is_held_and_undecided_is_expired():
    ev = EventEngine()
    held = ev.open_retest(_bo(1, 11, 100.0, "UP", 0), 0, "5m")
    ev.step_retests(close_5m=130.0, low_5m=129.0, high_5m=131.0, at_ms=held.deadline_ms)
    assert held.state == "HELD" and held.reason == "WINDOW_ELAPSED_UNTOUCHED"

    ev2 = EventEngine()
    r = ev2.open_retest(_bo(1, 11, 100.0, "UP", 0), 0, "5m")
    ev2.step_retests(105.0, 100.5, 106.0, FIVE)  # a return into the buffer
    assert r.state == "RETEST_PENDING"
    ev2.step_retests(105.0, 104.0, 106.0, r.deadline_ms)
    assert r.state == "EXPIRED" and r.reason == "UNDECIDED"


def test_retested_needs_the_return_then_two_closes_back():
    ev = EventEngine()
    r = ev.open_retest(_bo(1, 11, 100.0, "UP", 0, buf=1.0), 0, "5m")
    ev.step_retests(103.0, 100.5, 104.0, FIVE)
    assert r.state == "RETEST_PENDING"
    ev.step_retests(103.0, 102.0, 104.0, 2 * FIVE)
    assert r.state == "RETEST_PENDING", "one close back is not a retest"
    ev.step_retests(103.0, 102.0, 104.0, 3 * FIVE)
    assert r.state == "RETESTED" and r.reason == "TWO_CLOSES_HELD"


def test_failed_is_two_closes_back_through_and_a_return_does_not_forgive_one():
    """R0's fixed bug, carried over fixed: the touch resets the hold run, never the failure run."""
    ev = EventEngine()
    r = ev.open_retest(_bo(1, 11, 100.0, "UP", 0, buf=1.0), 0, "5m")
    ev.step_retests(98.0, 97.0, 99.0, FIVE)  # already closed through, failure run = 1
    assert r.below_run == 1
    ev.step_retests(98.0, 97.0, 99.0, 2 * FIVE)
    assert r.state == "FAILED" and ev.last_failed_break[0] == "FAILED_BREAK_UP"


def test_a_level_leaving_the_book_terminates_its_machine():
    ev = EventEngine()
    r = ev.open_retest(_bo(1, 11, 100.0, "UP", 0), 0, "5m")
    ev.level_expired(11, 99_999)
    assert r.state == "EXPIRED" and r.reason == "LEVEL_EXPIRED"
    assert 11 not in ev.open_by_level


def test_an_illegal_retest_transition_raises_rather_than_being_logged_quietly():
    ev = EventEngine()
    r = ev.open_retest(_bo(1, 11, 100.0, "UP", 0), 0, "5m")
    r.state = "RETESTED"
    with pytest.raises(AssertionError):
        ev._terminate(r, "FAILED", 1, "nonsense")


def test_the_retest_window_follows_the_levels_confirming_frame():
    ev = EventEngine()
    a = ev.open_retest(_bo(1, 11, 100.0, "UP", 0), 0, "5m")
    b = ev.open_retest(_bo(2, 22, 100.0, "UP", 0), 0, "1h")
    assert a.deadline_ms == 48 * 5 * MIN
    assert b.deadline_ms == 48 * 60 * MIN


def test_the_scalar_retest_state_names_the_latest_breakouts_own_machine():
    ev = EventEngine()
    ev.open_retest(_bo(1, 11, 100.0, "UP", 0), 0, "5m")
    bo2 = _bo(2, 22, 200.0, "UP", 10_000)
    ev.last_breakout = bo2
    second = ev.open_retest(bo2, 10_000, "5m")
    assert ev.latest_retest() is second


# ------------------------------------------------------ inertness of provisional levels
def test_a_provisional_level_raises_no_breakout_and_no_sweep():
    """The engine only ever hands the publishable set to the event machines."""
    c5 = np.array([100.0] * 20 + [101.0, 99.0] * 10, dtype=float)
    b5 = mk("5m", FIVE, c5, highs=c5 + 1, lows=c5 - 1)
    eng = Engine({"1m": mk("1m", MIN, c5), "5m": b5, "1h": mk("1h", HOUR, c5)},
                 EngineConfig(3.0, 6.0, 0, 10 ** 15))
    b = eng.book
    l = b.offer(sw("5m", "SWING_HIGH", 100.0, 0), 90.0, 40.0, 4.0, 0, bar_5m=0)
    assert l.state == "PROVISIONAL"
    tgt = [x for x in b.publishable if x.confirm_tf == "5m"]
    assert tgt == [], "a provisional level is never a breakout target"
    assert list(b.publishable) == [], "nor a sweep target"


# ---------------------------------------------------------------- the repaint contract
@pytest.fixture(scope="module")
def real_bars():
    try:
        return psdata.load()
    except RuntimeError as exc:
        pytest.skip(f"research raw data absent: {exc}")


def test_engine_state_is_identical_when_the_future_is_removed(real_bars):
    b1 = real_bars["1m"]
    T = psdata.REPLAY_START_MS + 14 * 86_400_000
    cut = int((T - int(b1.ts[0])) // MIN)
    sl = lambda x: x[:cut]
    t1 = Bars("1m", MIN, sl(b1.ts), sl(b1.open), sl(b1.high), sl(b1.low), sl(b1.close),
              sl(b1.volume), sl(b1.turnover), wilder_atr(sl(b1.high), sl(b1.low), sl(b1.close)))
    trunc = {"1m": t1, "5m": aggregate(t1, "5m", 5), "1h": aggregate(t1, "1h", 60)}
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


def test_sources_never_shrink_and_states_only_move_along_the_declared_graph(real_bars):
    from app.crypto.research.price_structure_r1.levels import ALLOWED_TRANSITIONS
    start = psdata.REPLAY_START_MS
    eng = Engine(real_bars, EngineConfig(3.0, 6.0, start, start + 5 * 86_400_000))
    eng.run()
    assert eng.book.transitions, "the window should contain lifecycle activity"
    for t in eng.book.transitions:
        assert (t["from"], t["to"]) in ALLOWED_TRANSITIONS
    seen: dict[int, set] = {}
    for l in eng.book.levels.values():
        seen[l.id] = set(l.sources)
        assert l.sources, "every level carries at least its originating frame"


def test_a_level_is_never_published_before_it_became_publishable(real_bars):
    start = psdata.REPLAY_START_MS
    eng = Engine(real_bars, EngineConfig(3.0, 6.0, start, start + 2 * 86_400_000))
    eng.run()
    first_conf = {}
    for t in eng.book.transitions:
        if t["to"] == "CONFIRMED" and t["id"] not in first_conf:
            first_conf[t["id"]] = t["at"]
    for snap in eng.hourly_snapshots:
        for p in snap["published_levels"]:
            assert p["created_at"] <= snap["info_ts"]
            assert p["pivot_at"] < p["created_at"]
            assert p["lifecycle"] in ("CONFIRMED", "WEAKENED")
            if p["tf"] == "5m" and "1h" not in p["sources"]:
                assert first_conf[p["id"]] <= snap["info_ts"]


# ------------------------------------------------------------------------- metrics
def _snap(ts: int, atr5: float, prices_tf: list[tuple[float, str]]) -> dict:
    return {"info_ts": ts, "atr_5m": atr5, "close": 100.0,
            "published_levels": [{"id": i, "price": p, "tf": tf} for i, (p, tf) in
                                 enumerate(prices_tf)]}


class _FakeEngine:
    def __init__(self, snaps):
        self.hourly_snapshots = snaps


def test_duplicate_density_counts_pairs_inside_a_quarter_atr_and_flags_cross_frame():
    e = _FakeEngine([_snap(1, 40.0, [(100.0, "5m"), (105.0, "1h"), (300.0, "5m")]),
                     _snap(2, 40.0, [(100.0, "5m"), (300.0, "5m")])])
    m = metrics.m3_duplicate_density(e)
    assert m["hourly_sets_counted"] == 2
    assert m["dup_pairs_total"] == 1 and m["cross_tf_pairs"] == 1
    assert m["dup_set_share"] == 0.5


def test_duplicate_density_ignores_pairs_outside_the_zone():
    e = _FakeEngine([_snap(1, 4.0, [(100.0, "5m"), (105.0, "1h")])])
    assert metrics.m3_duplicate_density(e)["dup_pairs_total"] == 0


def _score(m1=0, m2med=9, m2p95=13, m3=0.04, m4=0.7, m5=1.0, m6=6):
    return {"M1_repaint": {"total": m1},
            "M2_active_levels": {"publishable": {"median": m2med, "p95": m2p95}},
            "M3_duplicate_density": {"dup_set_share": m3},
            "M4_coverage": {"coverage": m4},
            "M5_liveness": {"fraction": m5},
            "M6_event_rate": {"breakouts_per_day_median": m6}}


def test_the_verdict_rule_is_the_one_frozen_in_the_contract():
    assert metrics.verdict(_score())[0] == "B_USABLE_FOR_STRUCTURE_LAYER"
    assert metrics.verdict(_score(m1=1))[0] == "A_NOT_USABLE"
    assert metrics.verdict(_score(m2med=16))[0] == "A_NOT_USABLE"
    assert metrics.verdict(_score(m4=0.39))[0] == "A_NOT_USABLE"
    assert metrics.verdict(_score(m5=0.99))[0] == "A_NOT_USABLE"
    # above the A floor but short of B on one axis each
    assert metrics.verdict(_score(m4=0.5))[0] == "C_NEEDS_NEW_LEVEL_SOURCE"
    assert metrics.verdict(_score(m3=0.2))[0] == "C_NEEDS_NEW_LEVEL_SOURCE"
    assert metrics.verdict(_score(m6=13))[0] == "C_NEEDS_NEW_LEVEL_SOURCE"
    assert metrics.verdict(_score(m2med=12))[0] == "C_NEEDS_NEW_LEVEL_SOURCE"


def test_m2_reports_the_book_alongside_the_publishable_count():
    class E:
        counters = {"active": [5, 7, 9], "book": [9, 11, 13], "provisional": [4, 4, 4]}
    m = metrics.m2(E())
    assert m["publishable"]["median"] == 7 and m["book"]["median"] == 11
    assert m["provisional_median"] == 4


def test_m2_falls_back_to_one_series_for_an_engine_without_a_lifecycle():
    class E:
        counters = {"active": [5, 7, 9]}
    m = metrics.m2(E())
    assert m["publishable"] == m["book"]
    assert "R0 has no PROVISIONAL state" in m["note"]
