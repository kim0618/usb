"""Price Structure R2 unit tests.

Contract: docs/crypto/price_structure_r2/CONTRACT_R2.md
Research only: nothing here touches C1/C1x, AUTO, order paths, PAPER, LIVE or any production module.

Organised as the contract is: the token lattice first, then one block per family, then the lifetime
max rule, then the repaint contract, then the admission rule. The strongest test in the file is
``test_swing_only_is_r1_to_the_object``: it is contract section 2.1 expressed on a synthetic book.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.crypto.research.price_structure_r0 import data as psdata
from app.crypto.research.price_structure_r0.data import Bars, aggregate, wilder_atr
from app.crypto.research.price_structure_r0.swings import Swing
from app.crypto.research.price_structure_r1.levels import ALLOWED_TRANSITIONS, EXPIRY_1H_MS
from app.crypto.research.price_structure_r1.levels import LevelBook as LevelBookR1
from app.crypto.research.price_structure_r2 import metrics as r2metrics
from app.crypto.research.price_structure_r2.engine import Engine, EngineConfig
from app.crypto.research.price_structure_r2.events import EventEngine
from app.crypto.research.price_structure_r2.levels import (
    MAJOR,
    NEW_TOKENS,
    TOKENS,
    Candidate,
    LevelBook,
    frame_of,
    is_major,
)

MIN = 60_000
HOUR = 3_600_000
FIVE = 300_000
DAY = 86_400_000


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


def day_cand(price: float, at: int, kind: str = "PREV_DAY_HIGH") -> Candidate:
    return Candidate("D", price, kind, at - DAY, at, day_expiry=at + DAY)


def range_cand(price: float, at: int, episode: int = 1, kind: str = "RANGE_HIGH") -> Candidate:
    return Candidate("RANGE", price, kind, at, at, range_episode=episode)


# ------------------------------------------------------------- the token lattice, section 3
def test_the_token_set_and_the_major_set_are_the_frozen_ones():
    assert TOKENS == ("5m", "1h", "D", "RANGE", "FLIP")
    assert MAJOR == {"1h", "D", "RANGE", "FLIP"}
    assert NEW_TOKENS == {"D", "RANGE", "FLIP"}
    assert frame_of("5m") == "5m"
    assert all(frame_of(t) == "1h" for t in MAJOR)
    assert is_major({"5m"}) is False and is_major({"5m", "D"}) is True


def test_every_new_family_is_born_confirmed_major_and_breaks_on_the_hourly_frame():
    """Contract section 3.2: the four rules R1 keyed on `"1h" in sources` now key on major."""
    b = LevelBook()
    for cand in (day_cand(100.0, DAY), range_cand(200.0, DAY), 
                 Candidate("FLIP", 300.0, "FLIP_FROM_RANGE_HIGH", 0, DAY, flip_at=DAY)):
        l = b.offer(cand, close=50.0, atr_1h=40.0, atr_5m=4.0, now_ms=DAY)
        assert l is not None and l.major
        assert l.state == "CONFIRMED", "a major source has no PROVISIONAL state"
        assert l.confirm_tf == "1h", "one identity, one confirming frame"
        assert l.tf == "1h", "the originating frame of an hourly scale object"
        assert l.strength(DAY) >= 2, "the major strength bonus reaches the new tokens"


def test_a_major_level_is_never_weakened():
    b = LevelBook()
    l = b.offer(day_cand(100.0, DAY), close=50.0, atr_1h=40.0, atr_5m=4.0, now_ms=DAY)
    assert b.weaken_scan(DAY + 13 * HOUR) == []
    assert l.state == "CONFIRMED"


def test_a_five_minute_level_still_weakens_exactly_as_in_r1():
    b = LevelBook()
    l = b.offer(sw("5m", "SWING_HIGH", 100.0, 0), 90.0, 40.0, 4.0, 0, bar_5m=0)
    b.touch_scan(100.0, 100.0, 4.0, FIVE, 1, {})
    assert l.state == "CONFIRMED"
    assert b.weaken_scan(FIVE + 13 * HOUR) == [l.id]
    assert l.state == "WEAKENED"


def test_the_merge_zone_is_hourly_only_when_both_sides_are_hourly_scale():
    """Contract section 3.3. Nothing is widened against R0 or R1."""
    b = LevelBook()
    assert b._zone_tol("1h", "1h", 40.0, 4.0) == pytest.approx(10.0)
    assert b._zone_tol("1h", "5m", 40.0, 4.0) == pytest.approx(1.0)
    assert b._zone_tol("5m", "1h", 40.0, 4.0) == pytest.approx(1.0)
    assert b._zone_tol("5m", "5m", 40.0, 4.0) == pytest.approx(1.0)


def test_a_new_family_candidate_in_the_zone_merges_and_never_moves_the_line():
    b = LevelBook()
    five = b.offer(sw("5m", "SWING_HIGH", 100.0, 1_000), 90.0, 40.0, 4.0, 1_000, bar_5m=0)
    assert five.state == "PROVISIONAL" and five.sources == {"5m"}
    assert b.offer(day_cand(100.5, DAY), 90.0, 40.0, 4.0, DAY) is None
    assert len(b.levels) == 1
    assert five.price == 100.0, "the incumbent price is never recomputed"
    assert five.sources == {"5m", "D"} and five.provenance == "MULTI"
    assert five.state == "CONFIRMED", "a major source promotes a provisional level at once"
    assert five.day_expiry == DAY + DAY, "the token's anchor is adopted on the merge"


def test_an_anchor_is_written_once_and_never_revised():
    """Contract section 10: `day_expiry`, the range episode and `flip_at` are write once."""
    b = LevelBook()
    l = b.offer(day_cand(100.0, DAY), 50.0, 40.0, 4.0, DAY)
    first = l.day_expiry
    assert b.offer(day_cand(100.0, 2 * DAY), 50.0, 40.0, 4.0, 2 * DAY) is None
    assert l.day_expiry == first, "the second day's object must not extend the first anchor"


# ------------------------------------------------------- S1, previous session, section 4
def test_the_day_objects_are_the_completed_sessions_extremes_and_arrive_at_the_boundary():
    n = 3 * 1440
    c = np.full(n, 100.0)
    h, l = c.copy(), c.copy()
    h[500] = 150.0   # inside day 0
    l[700] = 50.0
    h[2000] = 160.0  # inside day 1
    l[2100] = 40.0
    b1 = mk("1m", MIN, c, highs=h, lows=l)
    bars = {"1m": b1, "5m": aggregate(b1, "5m", 5), "1h": aggregate(b1, "1h", 60)}
    eng = Engine(bars, EngineConfig(3.0, 6.0, 0, 10 ** 15, enable_d=True))
    eng.run()
    emitted = [(d["at"], d["kind"], d["price"]) for d in eng.day_objects]
    assert (DAY, "PREV_DAY_HIGH", 150.0) in emitted
    assert (DAY, "PREV_DAY_LOW", 50.0) in emitted
    assert (2 * DAY, "PREV_DAY_HIGH", 160.0) in emitted
    assert (2 * DAY, "PREV_DAY_LOW", 40.0) in emitted
    assert all(at % DAY == 0 for at, _, _ in emitted), "emitted only at a session boundary"
    assert not any(at == 0 for at, _, _ in emitted), "a partial session is not a session"


def test_the_day_token_expires_when_the_next_session_supersedes_it():
    b = LevelBook()
    l = b.offer(day_cand(100.0, DAY), 100.0, 40.0, 4.0, DAY)
    assert b.expire_scan(100.0, 40.0, DAY + DAY - MIN) == []
    gone = b.expire_scan(100.0, 40.0, DAY + DAY)
    assert gone == [l.id]
    assert l.state == "EXPIRED"
    assert b.transitions[-1]["reason"] == "DAY_SUPERSEDED"


def test_a_day_object_is_disabled_unless_its_family_is_enabled():
    n = 2 * 1440 + 10
    c = np.full(n, 100.0)
    b1 = mk("1m", MIN, c)
    bars = {"1m": b1, "5m": aggregate(b1, "5m", 5), "1h": aggregate(b1, "1h", 60)}
    eng = Engine(bars, EngineConfig(3.0, 6.0, 0, 10 ** 15))
    eng.run()
    assert eng.day_objects == []


# ----------------------------------------------------------- S2, the range, section 5
def test_with_s2_on_the_standalone_range_edges_leave_the_target_lists():
    c = np.full(600, 100.0)
    b1 = mk("1m", MIN, c)
    bars = {"1m": b1, "5m": aggregate(b1, "5m", 5), "1h": aggregate(b1, "1h", 60)}
    on = Engine(bars, EngineConfig(3.0, 6.0, 0, 0, enable_range=True))
    on.rng.state, on.rng.high, on.rng.low = "IN_RANGE", 110.0, 90.0
    on._sync_edges(HOUR)
    assert on.edges == [], "the boundary price now lives in the book, not beside it"
    off = Engine(bars, EngineConfig(3.0, 6.0, 0, 0))
    off.rng.state, off.rng.high, off.rng.low = "IN_RANGE", 110.0, 90.0
    off._sync_edges(HOUR)
    assert [e.id for e in off.edges] == ["RANGE_HIGH", "RANGE_LOW"], "R1 behaviour is untouched"


def test_the_range_token_expires_when_its_episode_ends_and_not_before():
    b = LevelBook()
    l = b.offer(range_cand(100.0, HOUR, episode=1), 95.0, 40.0, 4.0, HOUR)
    assert b.expire_scan(100.0, 40.0, HOUR + 30 * DAY) == [], "an open episode never times out"
    b.end_range_episode(1, 40 * DAY)
    assert b.expire_scan(100.0, 40.0, 40 * DAY) == [l.id]
    assert b.transitions[-1]["reason"] == "RANGE_ENDED"


def test_a_range_object_is_offered_on_the_frozen_transition_and_the_episode_is_tracked():
    c = np.full(200, 100.0)
    b1 = mk("1m", MIN, c)
    bars = {"1m": b1, "5m": aggregate(b1, "5m", 5), "1h": aggregate(b1, "1h", 60)}
    eng = Engine(bars, EngineConfig(3.0, 6.0, 0, 0, enable_range=True))
    eng.rng.log.append({"event": "RANGE_FROZEN", "at": HOUR, "high": 110.0, "low": 90.0,
                        "width": 20.0, "atr_1h": 4.0})
    eng._consume_range_log(100.0, 40.0, 4.0)
    assert [r["kind"] for r in eng.range_objects] == ["RANGE_HIGH", "RANGE_LOW"]
    assert eng._episode == 1 and eng._episode_open == 1
    assert set(eng.range_level_ids) == {"RANGE_HIGH", "RANGE_LOW"}
    eng.rng.log.append({"event": "RANGE_LOST", "at": 2 * HOUR, "high": 110.0, "low": 90.0})
    eng._consume_range_log(100.0, 40.0, 4.0)
    assert eng.book.episode_ended_at == {1: 2 * HOUR}
    assert eng._episode_open is None and eng.range_level_ids == {}


def test_a_range_broken_entry_does_not_end_the_episode():
    c = np.full(200, 100.0)
    b1 = mk("1m", MIN, c)
    bars = {"1m": b1, "5m": aggregate(b1, "5m", 5), "1h": aggregate(b1, "1h", 60)}
    eng = Engine(bars, EngineConfig(3.0, 6.0, 0, 0, enable_range=True))
    eng.rng.log.append({"event": "RANGE_FROZEN", "at": HOUR, "high": 110.0, "low": 90.0})
    eng._consume_range_log(100.0, 40.0, 4.0)
    eng.rng.log.append({"event": "RANGE_BROKEN_UP", "at": 2 * HOUR, "high": 110.0, "low": 90.0})
    eng._consume_range_log(100.0, 40.0, 4.0)
    assert eng.book.episode_ended_at == {}, "the 24 bar hold has not run out yet"


# ---------------------------------------------------------- S3, the flip, section 6
def _bo(bid, lid, price, direction, at, buf=1.0, source="LEVEL", tf="5m"):
    from app.crypto.research.price_structure_r1.events import Breakout
    return Breakout(bid, lid, price, tf, direction, at, price, buf, source)


def test_a_flip_is_queued_only_on_a_retested_termination():
    ev = EventEngine()
    r = ev.open_retest(_bo(1, 11, 100.0, "UP", 0, buf=1.0), 0, "5m")
    ev.step_retests(103.0, 100.5, 104.0, FIVE)      # RETEST_PENDING
    ev.step_retests(103.0, 102.0, 104.0, 2 * FIVE)
    ev.step_retests(103.0, 102.0, 104.0, 3 * FIVE)  # RETESTED
    assert r.state == "RETESTED"
    assert [b.id for b, _ in ev.flip_queue] == [1]
    assert ev.drain_flips() and ev.flip_queue == []


def test_a_held_a_failed_and_an_expired_termination_claim_nothing():
    ev = EventEngine()
    held = ev.open_retest(_bo(1, 11, 100.0, "UP", 0, buf=1.0), 0, "5m")
    ev.step_retests(103.0, 103.0, 104.0, held.deadline_ms)
    assert held.state == "HELD"
    failed = ev.open_retest(_bo(2, 22, 100.0, "UP", 0, buf=1.0), 0, "5m")
    ev.step_retests(98.0, 97.0, 99.0, FIVE)
    ev.step_retests(98.0, 97.0, 99.0, 2 * FIVE)
    assert failed.state == "FAILED"
    ev.open_retest(_bo(3, 33, 100.0, "UP", 0, buf=1.0), 0, "5m")
    ev.level_expired(33, 10 * FIVE)
    assert ev.flip_queue == [], "a flip is confirmed by the market, not claimed on the break"


def test_the_flip_token_promotes_the_level_it_is_added_to_and_is_append_only():
    b = LevelBook()
    l = b.offer(sw("5m", "SWING_HIGH", 100.0, 0), 90.0, 40.0, 4.0, 0, bar_5m=0)
    assert l.state == "PROVISIONAL" and not l.major
    assert b.add_flip(l, 5 * HOUR) is True
    assert l.sources == {"5m", "FLIP"} and l.major and l.state == "CONFIRMED"
    assert l.flip_at == 5 * HOUR and l.flips == 1
    assert b.add_flip(l, 9 * HOUR) is False, "the token is added once"
    assert l.flip_at == 5 * HOUR, "the anchor is never revised"


def test_the_flip_token_takes_the_hourly_horizon():
    b = LevelBook()
    l = b.offer(sw("5m", "SWING_HIGH", 100.0, 0), 90.0, 40.0, 4.0, 0, bar_5m=0)
    b.add_flip(l, HOUR)
    assert b.expire_scan(100.0, 40.0, HOUR + EXPIRY_1H_MS) == []
    assert b.expire_scan(100.0, 40.0, HOUR + EXPIRY_1H_MS + MIN) == [l.id]
    assert b.transitions[-1]["reason"] == "FLIP_AGE_10D"


def test_a_flip_of_an_object_outside_the_book_creates_a_level_at_that_price():
    c = np.full(200, 100.0)
    b1 = mk("1m", MIN, c)
    bars = {"1m": b1, "5m": aggregate(b1, "5m", 5), "1h": aggregate(b1, "1h", 60)}
    eng = Engine(bars, EngineConfig(3.0, 6.0, 0, 0, enable_flip=True))
    eng.ev.flip_queue.append((_bo(1, "RANGE_HIGH", 110.0, "UP", 0, source="RANGE_EDGE"), HOUR))
    eng._promote_flips(100.0, 40.0, 4.0)
    created = [l for l in eng.book.levels.values() if "FLIP" in l.sources]
    assert len(created) == 1
    assert created[0].price == 110.0
    assert created[0].origin_kind == "FLIP_FROM_RANGE_HIGH"
    assert created[0].sources == {"FLIP"} and created[0].state == "CONFIRMED"
    assert eng.flip_claims[0]["mode"] == "LEVEL_CREATED"


def test_a_flip_claim_is_ignored_when_its_family_is_off():
    c = np.full(200, 100.0)
    b1 = mk("1m", MIN, c)
    bars = {"1m": b1, "5m": aggregate(b1, "5m", 5), "1h": aggregate(b1, "1h", 60)}
    eng = Engine(bars, EngineConfig(3.0, 6.0, 0, 0))
    eng.ev.flip_queue.append((_bo(1, "RANGE_HIGH", 110.0, "UP", 0, source="RANGE_EDGE"), HOUR))
    eng._promote_flips(100.0, 40.0, 4.0)
    assert eng.book.levels == {} and eng.flip_claims == []
    assert eng.ev.flip_queue == [], "the queue is drained whether or not the family acts"


# -------------------------------------------------- the lifetime max rule, section 3.4
def test_a_level_survives_while_any_one_of_its_tokens_is_still_alive():
    b = LevelBook()
    l = b.offer(sw("5m", "SWING_HIGH", 100.0, 0), 90.0, 40.0, 4.0, 0, bar_5m=0)
    b.touch_scan(100.0, 100.0, 4.0, FIVE, 1, {})          # CONFIRMED, 5m horizon is 24h
    b.offer(day_cand(100.0, 12 * HOUR), 90.0, 40.0, 4.0, 12 * HOUR)
    assert l.sources == {"5m", "D"}
    assert b.expire_scan(100.0, 40.0, 30 * HOUR) == [], "the D token still has the day"
    gone = b.expire_scan(100.0, 40.0, 36 * HOUR)
    assert gone == [l.id], "both tokens are expired now"
    assert b.transitions[-1]["reason"] == "DAY_SUPERSEDED", "the governing token names the reason"


def test_the_far_and_untouched_rule_still_reaches_every_level():
    """It is what bounds the long lived tokens. Here the episode is open, so no token has expired."""
    b = LevelBook()
    l = b.offer(range_cand(100.0, HOUR, episode=1), 100.0, 40.0, 4.0, HOUR)
    assert b.expire_scan(100.0, 40.0, HOUR + 25 * HOUR) == [], "near price, nothing expires"
    gone = b.expire_scan(100.0 + 11 * 40.0, 40.0, HOUR + 25 * HOUR)
    assert gone == [l.id] and b.transitions[-1]["reason"] == "FAR_AND_UNTOUCHED"


def test_a_single_source_level_expires_for_exactly_r1s_reason():
    for cand, reason, when in (
            (sw("5m", "SWING_HIGH", 100.0, 0), "PROVISIONAL_UNTOUCHED", 13 * HOUR),
            (sw("1h", "SWING_HIGH", 100.0, 0), "AGE_10D", EXPIRY_1H_MS + MIN)):
        b = LevelBook()
        l = b.offer(cand, 90.0, 40.0, 4.0, 0, bar_5m=0)
        assert b.expire_scan(100.0, 40.0, when) == [l.id]
        assert b.transitions[-1]["reason"] == reason


# ----------------------------------------------- contract section 2.1, the equivalence
def test_swing_only_is_r1_to_the_object():
    """The R2 book fed only swings must agree with the R1 book, level by level and field by field."""
    seq = [("5m", "SWING_HIGH", 100.0, 1_000), ("1h", "SWING_HIGH", 100.4, 2_000),
           ("5m", "SWING_LOW", 80.0, 3_000), ("1h", "SWING_LOW", 60.0, 4_000),
           ("5m", "SWING_HIGH", 100.2, 5_000), ("1h", "SWING_HIGH", 140.0, 6_000),
           ("5m", "SWING_LOW", 59.9, 7_000)]
    a, b = LevelBook(), LevelBookR1()
    for i, (tf, kind, px, at) in enumerate(seq):
        for book in (a, b):
            book.offer(sw(tf, kind, px, at), 90.0, 40.0, 4.0, at, bar_5m=i)
    assert len(a.levels) == len(b.levels)
    for k in a.levels:
        x, y = a.levels[k], b.levels[k]
        for f in ("id", "tf", "price", "origin_kind", "pivot_at", "created_at", "side",
                  "touches", "state", "merges"):
            assert getattr(x, f) == getattr(y, f), f
        assert x.sources == y.sources
        assert x.strength(9_000) == y.strength(9_000)
        assert x.confirm_tf == y.confirm_tf
    assert a.cross_tf_merges == b.cross_tf_merges and a.promotions == b.promotions


# ---------------------------------------------------------------- the repaint contract
@pytest.fixture(scope="module")
def real_bars():
    try:
        return psdata.load()
    except RuntimeError as exc:
        pytest.skip(f"research raw data absent: {exc}")


@pytest.mark.parametrize("families", [{"enable_d": True}, {"enable_range": True},
                                      {"enable_flip": True},
                                      {"enable_d": True, "enable_range": True,
                                       "enable_flip": True}])
def test_no_variant_repaints_over_a_short_window(real_bars, families):
    start = psdata.REPLAY_START_MS
    eng = Engine(real_bars, EngineConfig(3.0, 6.0, start, start + 4 * DAY, **families))
    eng.run()
    assert eng.audit.violations == []
    assert eng.audit.vanished == []
    assert max(eng.counters["published"]) <= 10
    for t in eng.book.transitions:
        assert (t["from"], t["to"]) in ALLOWED_TRANSITIONS
    for l in eng.book.levels.values():
        assert l.sources and set(l.sources) <= set(TOKENS)


def test_removing_the_future_changes_nothing_with_every_family_on(real_bars):
    b1 = real_bars["1m"]
    T = psdata.REPLAY_START_MS + 14 * DAY
    cut = int((T - int(b1.ts[0])) // MIN)
    sl = lambda x: x[:cut]
    t1 = Bars("1m", MIN, sl(b1.ts), sl(b1.open), sl(b1.high), sl(b1.low), sl(b1.close),
              sl(b1.volume), sl(b1.turnover), wilder_atr(sl(b1.high), sl(b1.low), sl(b1.close)))
    trunc = {"1m": t1, "5m": aggregate(t1, "5m", 5), "1h": aggregate(t1, "1h", 60)}
    kw = dict(enable_d=True, enable_range=True, enable_flip=True)
    a = Engine(trunc, EngineConfig(3.0, 6.0, T, T, **kw)); a.run()
    b = Engine(real_bars, EngineConfig(3.0, 6.0, T, T, **kw)); b.run()
    assert len(a.hourly_snapshots) == 1 and len(b.hourly_snapshots) == 1
    assert a.hourly_snapshots[0] == b.hourly_snapshots[0]


def test_a_published_level_is_always_publishable_and_never_predates_its_creation(real_bars):
    start = psdata.REPLAY_START_MS
    eng = Engine(real_bars, EngineConfig(3.0, 6.0, start, start + 3 * DAY,
                                         enable_d=True, enable_range=True, enable_flip=True))
    eng.run()
    for snap in eng.hourly_snapshots:
        for p in snap["published_levels"]:
            assert p["created_at"] <= snap["info_ts"]
            assert p["lifecycle"] in ("CONFIRMED", "WEAKENED")
            if not p["major"]:
                assert p["sources"] == ["5m"]


# ------------------------------------------------- the admission rule, section 9
def _core(m1=0, m2med=9, m2p95=13, m3=0.04, m4=0.26, hits=7, m5=1.0, m6=6):
    return {"M1_repaint": {"total": m1},
            "M2_active_levels": {"publishable": {"median": m2med, "p95": m2p95}},
            "M3_duplicate_density": {"dup_set_share": m3},
            "M4_coverage": {"coverage": m4, "hits": hits},
            "M5_liveness": {"fraction": m5},
            "M6_event_rate": {"breakouts_per_day_median": m6}}


def test_a_family_must_add_a_hit_and_keep_every_non_m4_bound():
    base = _core(hits=7)
    assert r2metrics.admission(base, _core(hits=8))["admitted"] is True
    assert r2metrics.admission(base, _core(hits=7))["failed"] == ["adds_an_M4_hit"]
    assert r2metrics.admission(base, _core(hits=8, m2med=11))["failed"] == ["M2_median_le_10"]
    assert r2metrics.admission(base, _core(hits=8, m2p95=17))["failed"] == ["M2_p95_le_16"]
    assert r2metrics.admission(base, _core(hits=8, m3=0.11))["failed"] == ["M3_share_le_0.10"]
    assert r2metrics.admission(base, _core(hits=8, m6=13))["failed"] == ["M6_median_le_12"]
    assert r2metrics.admission(base, _core(hits=8, m1=1))["failed"] == ["M1_zero"]
    assert r2metrics.admission(base, _core(hits=8, m5=0.99))["failed"] == ["M5_liveness_eq_1"]


def test_the_admission_bounds_are_r1s_b_bounds_on_the_non_m4_axes():
    assert r2metrics.ADMISSION_BOUNDS == {"M2_publishable_median_max": 10,
                                          "M2_publishable_p95_max": 16,
                                          "M3_dup_set_share_max": 0.10,
                                          "M5_liveness_exact": 1.0,
                                          "M6_breakouts_per_day_median_max": 12}


def test_the_verdict_function_is_r1s_and_the_third_branch_keeps_its_bounds():
    assert r2metrics.verdict(_core(m4=0.70, m2med=9))[0] == "B_USABLE_FOR_STRUCTURE_LAYER"
    assert r2metrics.verdict(_core(m4=0.26))[0] == "A_NOT_USABLE"
    assert r2metrics.verdict(_core(m4=0.50))[0] == "C_NEEDS_NEW_LEVEL_SOURCE"
    assert r2metrics.verdict(_core(m4=0.70, m2med=12))[0] == "C_NEEDS_NEW_LEVEL_SOURCE"


def test_the_attribution_separates_gained_from_lost_coverage():
    base = [{"pivot_at": 1, "hit": False, "kind": "SWING_LOW", "price": 10.0, "move_pct": 3.0,
             "nearest_published_delta": 9.0, "matched": []},
            {"pivot_at": 2, "hit": True, "kind": "SWING_HIGH", "price": 20.0, "move_pct": 4.0,
             "nearest_published_delta": 0.0, "matched": [{"id": 5, "price": 20.0}]}]
    var = [{"pivot_at": 1, "hit": True, "kind": "SWING_LOW", "price": 10.0, "move_pct": 3.0,
            "nearest_published_delta": 0.0, "matched": [{"id": 9, "price": 10.0}]},
           {"pivot_at": 2, "hit": False, "kind": "SWING_HIGH", "price": 20.0, "move_pct": 4.0,
            "nearest_published_delta": 7.0, "matched": []}]
    a = r2metrics.m4_attribution(base, var)
    assert a["hits_gained"] == 1 and a["hits_lost"] == 1 and a["hits_shared"] == 0
    assert a["gained"][0]["covered_by"][0]["id"] == 9
    assert a["lost"][0]["baseline_covered_by"][0]["id"] == 5
