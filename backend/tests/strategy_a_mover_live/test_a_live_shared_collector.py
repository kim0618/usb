"""The shared collector: A's pass, the ordering, and E's behaviour when A is off.

Section S tests covered here: 1 (A off changes nothing on E's side), 2 (the E-only path is
unchanged) and 24 (E's measured SLA inputs are not degraded by A's pass). The E fixture suite
itself (``tests/strategy_e_max/test_e_rt2_finalizer.py``) is run unchanged as the regression.
"""

from datetime import date, datetime, time, timedelta, timezone

import pytest

from app.strategy_e_max_rt import finalizer as FZ
from app.strategy_a_mover_live import acquisition as AQ
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import integration as INT
from app.strategy_a_mover_live import schedule as SCHED
from tests.strategy_a_mover_live.fixtures import (
    ET, SESSION, RecordingClient, VirtualClock, cache, lane, minute_rows,
    recorded_premarket_rows, redate,
)

ON = {CFG.ENV_FLAG: "true"}
A_CUT_AT = datetime.combine(SESSION, time(9, 15), tzinfo=ET)
E_CUT_AT = datetime.combine(SESSION, time(9, 25), tzinfo=ET)


def recorded_pages(symbols) -> dict[str, list[dict]]:
    rows = redate(recorded_premarket_rows(), SESSION)
    return {symbol: list(rows) for symbol in symbols}


def two_lanes(symbols, clock: VirtualClock):
    client_minute = RecordingClient(recorded_pages(symbols), clock)
    client_tick = RecordingClient(recorded_pages(symbols), clock)
    return (lane("A", FZ.MINUTE_API, client_minute), lane("B", FZ.TICK_API, client_tick),
            client_minute, client_tick)


# -- A off: nothing on E's side moves (section S tests 1, 2) ---------------------------------

def test_attach_returns_none_when_the_flag_is_off(tmp_path):
    assert INT.attach(session=SESSION, repo=tmp_path, caches={}, shard_minute=(),
                      shard_tick=(), lane_minute=None, lane_tick=None, now=lambda: None,
                      exchanges={}, environ={}) is None


def test_the_e_rolling_deadline_and_order_are_unchanged_when_a_is_off():
    """The worker's two guarded expressions, evaluated with a None handle."""
    a_live = None
    order = ["SPY", "AAA", "BBB"]
    resolved = a_live.rolling_order(order) if a_live is not None else order
    deadline = (a_live.rolling_until() if a_live is not None
                else datetime.combine(SESSION, FZ.REFRESH_B_AT, tzinfo=ET))
    assert resolved is order
    assert deadline.time() == FZ.REFRESH_B_AT == time(9, 20, 40)


def test_e_own_constants_and_cutoff_are_untouched():
    assert (FZ.MINUTE_API, FZ.TICK_API) == ("usa06011", "usa06010")
    assert FZ.CUTOFF_LAST == time(9, 24)
    assert FZ.FINALIZE_AT == time(9, 25)
    assert FZ.DEADLINE == time(9, 29, 45)
    assert FZ.REFRESH_B_AT == time(9, 20, 40)
    assert SCHED.E_CUTOFF_LAST_MINUTE == 9 * 60 + 24


# -- A on: ordering and isolation -------------------------------------------------------------

def handle(tmp_path, caches, shard_minute, shard_tick, clock, union_symbols=None):
    from app.strategy_a_mover_live import universe as UNI
    lane_minute, lane_tick, client_minute, client_tick = two_lanes(
        list(caches) + list(union_symbols or []), clock)
    union = UNI.UnionUniverse(
        session=SESSION, a_symbols=tuple(sorted(set(caches) | set(union_symbols or ()))),
        e_symbols=tuple(sorted(caches)),
        union=tuple(sorted(set(caches) | set(union_symbols or ()))),
        reference_as_of=date(2026, 7, 1), reference_checksum="x",
        reference_active_rows=len(caches), pruned_no_daily_baseline=0,
        pruned_split_session=0, e_artifact=None)
    item = INT.SharedCollectorIntegration(
        session=SESSION, repo=tmp_path, caches=caches, shard_minute=tuple(shard_minute),
        shard_tick=tuple(shard_tick), lane_minute=lane_minute, lane_tick=lane_tick,
        now=clock, union=union, exchanges={name: "ND" for name in union.union},
        log=lambda text: None)
    for name in union.union:
        if name not in caches:
            item.extra_caches[name] = cache(name)
    return item, client_minute, client_tick


def test_a_symbols_are_never_put_into_e_cache_dictionary(tmp_path):
    clock = VirtualClock(A_CUT_AT)
    caches = {"EEE": cache("EEE")}
    item, _, _ = handle(tmp_path, caches, ["EEE"], [], clock, union_symbols=["AAA", "BBB"])
    assert set(caches) == {"EEE"}                        # E's dictionary is untouched
    assert set(item.extra_caches) == {"AAA", "BBB"}
    assert set(item.all_caches()) == {"EEE", "AAA", "BBB"}


def test_the_rolling_order_adds_a_extras_and_moves_the_e_tick_shard_last(tmp_path):
    clock = VirtualClock(A_CUT_AT)
    caches = {name: cache(name) for name in ("M1", "T1", "T2")}
    item, _, _ = handle(tmp_path, caches, ["M1"], ["T1", "T2"], clock,
                        union_symbols=["X1"])
    spy = cache("SPY")
    order = item.rolling_order([spy, caches["M1"], caches["T1"], caches["T2"]])
    names = [getattr(entry, "symbol", entry) for entry in order]
    assert names == ["SPY", "M1", "X1", "T1", "T2"]
    assert names[-2:] == ["T1", "T2"]                    # E's tick shard, in E's own order


def test_the_rolling_phase_stops_at_a_cut_not_at_e_refresh(tmp_path):
    clock = VirtualClock(A_CUT_AT)
    item, _, _ = handle(tmp_path, {"EEE": cache("EEE")}, ["EEE"], [], clock)
    assert item.rolling_until() == A_CUT_AT
    assert item.rolling_until() < datetime.combine(SESSION, FZ.REFRESH_B_AT, tzinfo=ET)


def test_the_a_pass_order_is_the_union_with_the_tick_shard_last(tmp_path):
    clock = VirtualClock(A_CUT_AT)
    caches = {name: cache(name) for name in ("M1", "T1")}
    item, _, _ = handle(tmp_path, caches, ["M1"], ["T1"], clock, union_symbols=["X1"])
    assert item.pass_order()[-1] == "T1"


# -- A's pass uses E's own walk, and leaves the cache fresher ---------------------------------

def test_the_a_pass_reuses_e_refresh_on_the_minute_lane(tmp_path):
    clock = VirtualClock(A_CUT_AT + timedelta(seconds=30))
    symbols = ["AAA", "BBB"]
    caches = {name: cache(name) for name in symbols}
    lane_minute, lane_tick, client_minute, client_tick = two_lanes(symbols, clock)
    report = AQ.run_a_pass(lane_minute, lane_tick, caches, session=SESSION,
                           order=tuple(symbols), tick_shard=(), now=clock,
                           deadline=E_CUT_AT)
    assert set(report.usable) == set(symbols)
    assert client_tick.request_counts == {}              # no tick call for a minute-lane pass
    assert client_minute.request_counts[FZ.MINUTE_API] >= len(symbols)
    for name in symbols:
        assert caches[name].complete_through is not None
        assert caches[name].complete_through >= SCHED.A_CUTOFF_LAST_MINUTE


def test_the_a_pass_leaves_complete_through_at_or_past_e_own_refresh(tmp_path):
    """E's tick ``need_from`` is ``complete_through + 1``, so A must not lower it."""
    clock = VirtualClock(A_CUT_AT + timedelta(seconds=30))
    caches = {"AAA": cache("AAA")}
    lane_minute, lane_tick, *_ = two_lanes(["AAA"], clock)
    AQ.run_a_pass(lane_minute, lane_tick, caches, session=SESSION, order=("AAA",),
                  tick_shard=(), now=clock, deadline=E_CUT_AT)
    after_a = caches["AAA"].complete_through
    # what E's own rolling refresh would have left at the same instant
    fresh = cache("AAA")
    lane_e, _, *_ = two_lanes(["AAA"], clock)
    FZ.refresh(lane_e, fresh, SESSION, clock)
    assert after_a >= fresh.complete_through
    assert after_a > SCHED.A_CUTOFF_LAST_MINUTE - 1


def test_the_a_pass_does_not_write_e_verdict_fields(tmp_path):
    clock = VirtualClock(A_CUT_AT + timedelta(seconds=30))
    caches = {"AAA": cache("AAA")}
    lane_minute, lane_tick, *_ = two_lanes(["AAA"], clock)
    AQ.run_a_pass(lane_minute, lane_tick, caches, session=SESSION, order=("AAA",),
                  tick_shard=(), now=clock, deadline=E_CUT_AT)
    assert caches["AAA"].finalized_at is None
    assert caches["AAA"].data_source is None
    assert caches["AAA"].contiguous is None              # E's own cut decides these


def test_e_finalization_after_the_a_pass_still_reaches_its_own_cutoff(tmp_path):
    """E's own ``finalize_minute``, run unchanged after A's pass, finalizes as it does today."""
    clock = VirtualClock(A_CUT_AT + timedelta(seconds=30))
    caches = {"AAA": cache("AAA")}
    lane_minute, lane_tick, *_ = two_lanes(["AAA"], clock)
    AQ.run_a_pass(lane_minute, lane_tick, caches, session=SESSION, order=("AAA",),
                  tick_shard=(), now=clock, deadline=E_CUT_AT)
    clock.now = E_CUT_AT + timedelta(seconds=1)
    lane_e, _, *_ = two_lanes(["AAA"], clock)
    FZ.finalize_minute(lane_e, caches["AAA"], SESSION, clock)
    assert caches["AAA"].contiguous is True
    assert caches["AAA"].data_source == "KIWOOM_usa06011"
    assert max(caches["AAA"].bars) <= SCHED.E_CUTOFF_LAST_MINUTE


def test_the_a_pass_claims_each_symbol_once_across_both_lanes(tmp_path):
    clock = VirtualClock(A_CUT_AT + timedelta(seconds=30))
    symbols = ["M1", "M2", "T1", "T2"]
    caches = {name: cache(name) for name in symbols}
    lane_minute, lane_tick, client_minute, client_tick = two_lanes(symbols, clock)
    report = AQ.run_a_pass(lane_minute, lane_tick, caches, session=SESSION,
                           order=("M1", "M2", "T1", "T2"), tick_shard=("T1", "T2"),
                           now=clock, deadline=E_CUT_AT)
    assert len(report.outcomes) == 4
    asked = [symbol for _, symbol in client_minute.calls + client_tick.calls]
    for name in symbols:
        assert asked.count(name) >= 1
    lanes_used = {item.lane for item in report.outcomes.values()}
    assert lanes_used <= {"A", "B"}


def test_the_a_pass_stops_at_the_deadline(tmp_path):
    clock = VirtualClock(E_CUT_AT + timedelta(seconds=1))
    caches = {"AAA": cache("AAA")}
    lane_minute, lane_tick, client_minute, _ = two_lanes(["AAA"], clock)
    report = AQ.run_a_pass(lane_minute, lane_tick, caches, session=SESSION, order=("AAA",),
                           tick_shard=(), now=clock, deadline=E_CUT_AT)
    assert report.deadline_hit
    assert report.outcomes == {}
    assert client_minute.request_counts == {}


def test_a_symbol_whose_history_does_not_reach_0400_is_not_usable(tmp_path):
    clock = VirtualClock(A_CUT_AT + timedelta(seconds=30))
    caches = {"AAA": cache("AAA")}
    client = RecordingClient({"AAA": minute_rows(SESSION, range(500, 555))}, clock,
                             always_continue=True)   # older history is still behind every page
    short_lane = lane("A", FZ.MINUTE_API, client)
    report = AQ.run_a_pass(short_lane, lane("B", FZ.TICK_API, RecordingClient({}, clock)),
                           caches, session=SESSION, order=("AAA",), tick_shard=(),
                           now=clock, deadline=E_CUT_AT)
    assert caches["AAA"].complete_through is None
    assert report.usable == ()
    assert not AQ.a_usable(caches["AAA"])


# -- the cut is enforced at the read (section S test 3, collector side) ----------------------

def test_the_shared_cache_may_hold_later_minutes_but_a_never_reads_them(tmp_path):
    clock = VirtualClock(A_CUT_AT + timedelta(seconds=30))
    caches = {"AAA": cache("AAA")}
    lane_minute, lane_tick, *_ = two_lanes(["AAA"], clock)
    report = AQ.run_a_pass(lane_minute, lane_tick, caches, session=SESSION, order=("AAA",),
                           tick_shard=(), now=clock, deadline=E_CUT_AT)
    snapshots = AQ.snapshots_from(caches, report, session=SESSION,
                                  observed_at=datetime(2026, 9, 15, 13, 15, tzinfo=timezone.utc))
    assert snapshots["AAA"].last_complete_minute is not None
    assert snapshots["AAA"].last_complete_minute <= SCHED.A_CUTOFF_LAST_MINUTE
    raw = AQ.raw_session_from(caches, report, session=SESSION,
                              observed_at=datetime(2026, 9, 15, 13, 15, tzinfo=timezone.utc))
    minutes = {bar.bar_timestamp.hour * 60 + bar.bar_timestamp.minute for bar in raw.bars()}
    assert minutes and max(minutes) <= SCHED.A_CUTOFF_LAST_MINUTE
    assert report.declaration()["a_cut_respected"] is True


# -- rate and the measured projection (section I) ---------------------------------------------

def test_one_process_fits_both_cuts_and_two_do_not():
    body = AQ.estimate(SESSION, 4915).declaration()
    assert body["a_fits_before_e_cut"] is True
    assert body["e_t1_before_deadline"] is True
    assert body["e_t1_before_open"] is True
    assert body["rate_within_limit"] is True
    assert body["per_lane_rate_per_s"] <= body["per_api_id_limit_per_s"]
    assert body["separate_a_process_possible"] is False
    body_stale = body["e_tick_shard_staleness"]
    assert body_stale["preserves_measured_tick_cost"] is True
    assert body_stale["mechanism"] in ("E_OWN_REFRESH_STILL_FITS",
                                       "A_PASS_ORDERED_TICK_SHARD_LAST")


@pytest.mark.parametrize("union", [2561, 3847, 4567, 4915, 5226])
def test_every_measured_union_size_fits_between_the_two_cuts(union):
    estimate = AQ.estimate(SESSION, union)
    assert estimate.fits_before_e_cut
    assert estimate.staleness().preserves_measured_cost


@pytest.mark.parametrize("union,mechanism", [(2561, "E_OWN_REFRESH_STILL_FITS"),
                                             (3847, "E_OWN_REFRESH_STILL_FITS"),
                                             (4915, "A_PASS_ORDERED_TICK_SHARD_LAST"),
                                             (5226, "A_PASS_ORDERED_TICK_SHARD_LAST")])
def test_which_mechanism_preserves_e_depends_on_the_union_size(union, mechanism):
    """A small union is covered by E's own refresh; a large one by A's ordering. Not one rule."""
    assert AQ.estimate(SESSION, union).staleness().mechanism == mechanism


def test_neither_mechanism_covers_a_union_that_would_degrade_e():
    """A hypothetical pass ending after E's cut has no mechanism, and says so."""
    schedule = SCHED.SharedSchedule(SESSION)
    bound = SCHED.StalenessBound(E_CUT_AT + timedelta(seconds=30), schedule.e_cut,
                                 133.1, 260.0, 133.1)
    assert not bound.preserves_measured_cost
    assert bound.mechanism == "NONE"


def test_the_e_side_of_the_projection_is_e_own_measurement():
    estimate = AQ.estimate(SESSION, 4915)
    body = estimate.declaration()
    assert body["e_finalization_calls"] == 2609
    assert body["e_finalization_seconds"] == 266.7
    assert estimate.e_t1.time() < time(9, 29, 45)
