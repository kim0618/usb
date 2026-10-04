"""The resnapshot policy, and the compact state file the collector publishes.

The policy exists because of a measurement and is tested against it. A `limit=1000` snapshot's
bounds sat at -15.86 and +14.44 bps of mid, so the margin over the +-0.1% band the preview is
allowed to call COMPLETE was **4.44 bps at birth**, and the bounds are fixed prices - the margin
erodes one-for-one with mid. Nothing in the frozen contract covers that: it lists the faults that
force a snapshot, all of which still fire immediately, and none of them is "the book is fine but
the interval has stopped describing where the market is".

What is expensive about acting on it is not the API. `apply_snapshot` increments the book
generation and a new generation ends **every** wall candidate as UNKNOWN, so a resnapshot costs
the whole accumulated observation history. That is why the voluntary triggers are rate limited and
why the tests below are mostly about *not* resnapshotting.
"""
from __future__ import annotations

import time
from decimal import Decimal

import pytest

from app.crypto.market_structure_v0 import book as B
from app.crypto.market_structure_v0 import collector as C
from app.crypto.market_structure_v0.envelope import Session
from app.crypto.market_structure_v0.store import Store

from tests.crypto.ms_v0_fixtures import depth_frame
from tests.crypto.liquidity_map_fixtures import wall_snapshot

S = 1_000_000_000


@pytest.fixture
def running(tmp_path):
    """A collector with a synchronized book, driven by hand. Returns it and its clock base."""
    session = Session()
    store = Store.open(tmp_path, session.session_id, started_ns=session.started_ns)
    collector = C.Collector(store=store, session=session)
    base_ms, base_ns = int(time.time() * 1000), time.monotonic_ns()
    collector.write_session_record(config={})
    collector.on_depth_connect("d1", receive_ms=base_ms, mono_ns=base_ns)
    collector.mark_snapshot_requested(receive_ms=base_ms, mono_ns=base_ns)
    collector.on_snapshot(wall_snapshot(), receive_ms=base_ms + 10, mono_ns=base_ns + 10**7,
                          request_ms=base_ms, request_mono_ns=base_ns)
    assert collector.depth.state == B.SYNCED
    yield collector, base_ms, base_ns
    store.close()


def move_mid(collector, *, to: str, receive_ms: int, mono_ns: int, last_u: int = 1_000) -> int:
    """Walk the touch to a new price with one delta, keeping the book synchronized.

    Levels between the old touch and the new one are deleted with zero quantities, which is how a
    real move arrives: mid is `(best bid + best ask) / 2`, so adding a level on one side moves
    nothing until the levels in the way are gone.
    """
    price = Decimal(to)
    bids = [[str(price - Decimal("0.05")), "1"]]
    bids += [[str(level), "0"] for level in sorted(collector.depth.bids) if level > price]
    asks = [[str(price + Decimal("0.05")), "1"]]
    asks += [[str(level), "0"] for level in sorted(collector.depth.asks) if level < price]
    frame = depth_frame(first=last_u - 5, last=last_u + 10, previous=last_u - 6,
                        bids=bids, asks=asks, event_ms=receive_ms - 5)
    outcome = collector.on_depth_frame(frame, receive_ms=receive_ms, mono_ns=mono_ns)
    assert outcome == B.APPLIED, outcome
    mid = collector.depth.mid()
    assert mid is not None and abs(mid - price) < Decimal("1"), mid
    return last_u + 10


# --- the policy does not fire when it should not ----------------------------------------------

def test_a_healthy_book_with_room_to_spare_is_not_resnapshotted(running):
    collector, base_ms, base_ns = running
    margin = collector.coverage_margin_bps()
    assert margin is not None and margin > C.COVERAGE_MARGIN_TRIGGER_BPS
    assert collector.check_resnapshot_policy(at_ns=base_ns + S, at_ms=base_ms + 1_000) is None
    assert collector.refresh_wanted is False


def test_there_is_no_fixed_interval_polling_at_all(running):
    """Ten minutes of healthy samples, and not one voluntary request."""
    collector, base_ms, base_ns = running
    for second in range(1, 600):
        collector.check_resnapshot_policy(at_ns=base_ns + second * S,
                                          at_ms=base_ms + second * 1_000)
    assert collector.coverage_refreshes == 0 and collector.safety_refreshes == 0
    assert collector.resnapshot_view(at_ns=base_ns)["fixed_interval_polling"] is False


def test_an_unsynchronized_book_is_left_to_the_fault_path(running):
    """By the time the policy runs, a faulted book already has `snapshot_wanted` set."""
    collector, base_ms, base_ns = running
    collector.depth.invalidate(B.GAP)
    assert collector.check_resnapshot_policy(at_ns=base_ns + S, at_ms=base_ms + 1_000) is None
    assert collector.coverage_margin_bps() is None


# --- the coverage edge ------------------------------------------------------------------------

def test_mid_drifting_towards_the_snapshot_bound_asks_for_a_refresh(running):
    collector, base_ms, base_ns = running
    # The snapshot spans 84630..84880 around a mid of 84755, so the margin over +-0.1% starts at
    # 4.75 bps. Walking mid up to 84800 leaves the band 9.4 bps of ask side and no margin.
    move_mid(collector, to="84800", receive_ms=base_ms + 1_000, mono_ns=base_ns + S)
    margin = collector.coverage_margin_bps()
    assert margin is not None and margin < C.COVERAGE_MARGIN_TRIGGER_BPS
    reason = collector.check_resnapshot_policy(at_ns=base_ns + S, at_ms=base_ms + 1_000)
    assert reason == C.REFRESH_COVERAGE_EDGE
    assert collector.refresh_wanted is True and collector.coverage_refreshes == 1


def test_the_cooldown_follows_the_refresh_that_installed_not_the_one_that_asked(running):
    """V1.3 moved the cooldown clock from the request to the swap.

    The 300 s floor exists to bound how often a *successful* refresh destroys wall observation.
    V1.1 started it when the refresh was requested, so an attempt that never installed still cost
    five minutes of unguarded band - measured twice in 23 minutes on a moving book, while the
    margin went negative. Here the request is made and nothing installs, so the only thing
    standing between this attempt and the next is the short failure backoff.
    """
    collector, base_ms, base_ns = running
    move_mid(collector, to="84800", receive_ms=base_ms + 1_000, mono_ns=base_ns + S)
    assert collector.check_resnapshot_policy(at_ns=base_ns + S,
                                             at_ms=base_ms + 1_000) == C.REFRESH_COVERAGE_EDGE
    assert collector.last_coverage_refresh_ns is None, "asking is not installing"

    collector.abandon_refresh("TEST", receive_ms=base_ms + 1_100, mono_ns=base_ns + 11 * 10 ** 8)
    # Inside the backoff, nothing is asked for again.
    for second in (2, 5, 9):
        assert collector.check_resnapshot_policy(at_ns=base_ns + second * S,
                                                 at_ms=base_ms + second * 1_000) is None
    # And once it has elapsed, the trigger is free to act - not five minutes later.
    after = int(C.REFRESH_RETRY_BACKOFF_S) + 2
    assert collector.check_resnapshot_policy(
        at_ns=base_ns + after * S, at_ms=base_ms + after * 1_000) == C.REFRESH_COVERAGE_EDGE
    assert collector.coverage_refreshes == 2
    assert collector.refresh_failures == 1


def test_an_installed_refresh_does_take_the_full_cooldown(running):
    """The other half: what the 300 s floor is actually for."""
    collector, base_ms, base_ns = running
    move_mid(collector, to="84800", receive_ms=base_ms + 500, mono_ns=base_ns + 5 * 10 ** 8)
    collector.check_resnapshot_policy(at_ns=base_ns + S, at_ms=base_ms + 1_000)
    collector.mark_snapshot_requested(receive_ms=base_ms + 1_000, mono_ns=base_ns + S)
    collector.on_snapshot(
        dict(wall_snapshot(), lastUpdateId=collector.depth.last_update_id),
        receive_ms=base_ms + 1_100, mono_ns=base_ns + 11 * 10 ** 8,
        request_ms=base_ms + 1_000, request_mono_ns=base_ns + S)
    assert collector.refreshes_applied == 1
    assert collector.last_coverage_refresh_ns is not None
    # `move_mid`'s frame has to chain onto the book, which the swap left where it was.
    move_mid(collector, to="84815", receive_ms=base_ms + 2_000, mono_ns=base_ns + 2 * S,
             last_u=collector.depth.last_update_id + 6)
    margin = collector.coverage_margin_bps()
    assert margin is not None and margin < C.COVERAGE_MARGIN_TRIGGER_BPS, (
        "the trigger has to be live, or the cooldown is not what is holding it back")
    for second in (30, 120, 299):
        assert collector.check_resnapshot_policy(at_ns=base_ns + second * S,
                                                 at_ms=base_ms + second * 1_000) is None


def test_the_trigger_is_recorded_with_the_margin_that_produced_it(running):
    collector, base_ms, base_ns = running
    move_mid(collector, to="84800", receive_ms=base_ms + 1_000, mono_ns=base_ns + S)
    collector.check_resnapshot_policy(at_ns=base_ns + S, at_ms=base_ms + 1_000)
    event = collector.recent_telemetry[-1]
    assert event["event"] == C.REFRESH_COVERAGE_EDGE
    assert event["reason"] == C.REFRESH_COVERAGE_EDGE


# --- the safety refresh -----------------------------------------------------------------------

def test_an_hour_on_one_snapshot_asks_for_a_fresh_one(running):
    collector, base_ms, base_ns = running
    just_under = int(C.SAFETY_REFRESH_S) - 1
    assert collector.check_resnapshot_policy(at_ns=base_ns + just_under * S,
                                             at_ms=base_ms + just_under * 1_000) is None
    over = int(C.SAFETY_REFRESH_S) + 1
    assert collector.check_resnapshot_policy(
        at_ns=base_ns + over * S, at_ms=base_ms + over * 1_000) == C.REFRESH_SAFETY
    assert collector.safety_refreshes == 1


def test_the_safety_interval_is_an_hour_and_the_cooldown_is_five_minutes():
    """Both are bounded by the wall history a resnapshot destroys, not by API weight."""
    assert C.SAFETY_REFRESH_S == 3_600.0
    assert C.COVERAGE_REFRESH_COOLDOWN_S == 300.0
    assert C.PROTECTED_BAND_BPS == Decimal("10")
    assert C.COVERAGE_MARGIN_TRIGGER_BPS == Decimal("1.0")


# --- the request, and refusing one that would make things worse -------------------------------

def test_a_voluntary_request_is_allowed_past_the_synchronized_book_guard(running):
    collector, base_ms, base_ns = running
    runner = C.Runner(collector=collector)
    assert runner._should_request_snapshot(None) is False
    collector.refresh_wanted = True
    assert runner._should_request_snapshot(None) is True


def test_a_fault_still_takes_priority_and_is_not_refusable(running):
    collector, base_ms, base_ns = running
    collector.refresh_wanted = True
    collector.depth.invalidate(B.GAP)
    collector.mark_snapshot_requested(receive_ms=base_ms + 10, mono_ns=base_ns + 10**7)
    # The book stopped being usable while the request was being made, so this is the recovery the
    # invalidation asked for and the arriving snapshot must be installed whatever its id.
    assert collector.refresh_in_flight is False


def test_a_refresh_snapshot_older_than_the_live_book_never_rolls_it_back(running):
    """The rollback V1.1 refused to perform, and V1.3 no longer has to refuse.

    A snapshot from before the live chain's position is not a replacement and is never used as
    one. Here there is no buffered frame that can carry the staged book forward to the live
    chain, so the attempt is abandoned - and the live book's levels, bounds, ids and generation
    are all exactly what they were.
    """
    collector, base_ms, base_ns = running
    move_mid(collector, to="84770", receive_ms=base_ms + 500, mono_ns=base_ns + 5 * 10**8)
    live_u = collector.depth.last_update_id
    generation = collector.depth.generation
    levels = (dict(collector.depth.bids), dict(collector.depth.asks))
    collector._want_refresh(C.REFRESH_SAFETY, at_ns=base_ns + S, at_ms=base_ms + 1_000)
    collector.mark_snapshot_requested(receive_ms=base_ms + 1_000, mono_ns=base_ns + S)
    assert collector.refresh_in_flight is True
    collector.on_snapshot(wall_snapshot(last_update_id=live_u - 5_000),
                          receive_ms=base_ms + 1_100,
                          mono_ns=base_ns + 11 * 10**8, request_ms=base_ms + 1_000,
                          request_mono_ns=base_ns + S)
    assert collector.depth.state == B.SYNCED
    assert collector.depth.last_update_id == live_u
    assert collector.depth.generation == generation
    assert (collector.depth.bids, collector.depth.asks) == levels
    assert collector.refreshes_rejected == 1 and collector.refreshes_applied == 0
    assert collector.recent_telemetry[-1]["event"] == C.REFRESH_REJECTED


def test_a_staged_refresh_installs_and_recentres_the_interval(running):
    collector, base_ms, base_ns = running
    move_mid(collector, to="84800", receive_ms=base_ms + 500, mono_ns=base_ns + 5 * 10**8)
    generation = collector.depth.generation
    collector._want_refresh(C.REFRESH_COVERAGE_EDGE, at_ns=base_ns + S, at_ms=base_ms + 1_000)
    collector.mark_snapshot_requested(receive_ms=base_ms + 1_000, mono_ns=base_ns + S)
    before = collector.coverage_margin_bps()
    collector.on_snapshot(dict(wall_snapshot(), lastUpdateId=collector.depth.last_update_id),
                          receive_ms=base_ms + 1_100,
                          mono_ns=base_ns + 11 * 10**8, request_ms=base_ms + 1_000,
                          request_mono_ns=base_ns + S)
    assert collector.depth.generation == generation + 1
    assert collector.refreshes_rejected == 0 and collector.refreshes_applied == 1
    after = collector.coverage_margin_bps()
    assert after is not None and before is not None and after > before


def test_a_failed_voluntary_refresh_does_not_ask_the_recovery_path_to_run(running):
    """A failed refresh leaves a usable book, so demanding a snapshot would be wrong."""
    collector, base_ms, base_ns = running
    collector._want_refresh(C.REFRESH_SAFETY, at_ns=base_ns + S, at_ms=base_ms + 1_000)
    collector.mark_snapshot_requested(receive_ms=base_ms + 1_000, mono_ns=base_ns + S)
    collector.on_snapshot_failed("TimeoutError", receive_ms=base_ms + 1_100,
                                 mono_ns=base_ns + 11 * 10**8)
    assert collector.snapshot_wanted is False
    assert collector.depth.state == B.SYNCED
    # And the attempt is gone, so nothing is left buffering frames for a read that never came.
    assert collector.refresh is None and collector.refresh_failures == 1


def test_a_failed_fault_snapshot_still_asks_again(running):
    collector, base_ms, base_ns = running
    collector.depth.invalidate(B.GAP)
    collector.mark_snapshot_requested(receive_ms=base_ms + 1_000, mono_ns=base_ns + S)
    collector.on_snapshot_failed("TimeoutError", receive_ms=base_ms + 1_100,
                                 mono_ns=base_ns + 11 * 10**8)
    assert collector.snapshot_wanted is True


def test_the_published_policy_states_what_it_costs(running):
    collector, base_ms, base_ns = running
    view = collector.resnapshot_view(at_ns=base_ns + S)
    assert view["policy"] == "FAULT_IMMEDIATE_PLUS_COVERAGE_EDGE_PLUS_HOURLY_SAFETY"
    assert "generation" in view["cost_note"] and "UNKNOWN" in view["cost_note"]
    assert view["coverage_margin_bps"] is not None
    assert view["snapshot_age_s"] is not None


# --- the compact state file -------------------------------------------------------------------

def test_a_sample_publishes_the_live_candidate_set(running):
    collector, base_ms, base_ns = running
    collector.sample(at_ns=base_ns + S, at_ms=base_ms + 1_000)
    payload = collector.state_payload(derived=collector.last_derived, at_ns=base_ns + S,
                                      at_ms=base_ms + 1_000)
    assert payload["state_version"] == C.STATE_VERSION
    assert payload["is_authority"] is False
    assert payload["walls"]["counters"]["active"] == len(payload["walls"]["items"]) > 0
    assert payload["walls"]["values_as_of"] == "COLLECTOR_CURRENT_SAMPLE"
    assert payload["derived"]["book"]["state"] == B.SYNCED
    assert payload["session"]["ended"] is False
    assert payload["resnapshot"]["fixed_interval_polling"] is False
    assert collector.store.state_path().exists()


def test_the_published_sizes_are_the_current_ones_not_the_ones_at_open(running):
    """A journal OPENED row keeps the size the level had when it opened. This does not."""
    collector, base_ms, base_ns = running
    collector.sample(at_ns=base_ns + S, at_ms=base_ms + 1_000)
    opened = {(row["side"], row["price"]): row["qty"]
              for row in collector.wall.state_payloads(limit=10)[0]}
    # Shrink the oversized ask level, keeping it a candidate, and sample again.
    big = max(collector.depth.asks, key=lambda price: collector.depth.asks[price])
    collector.depth.asks[big] = Decimal("20")
    collector.depth.last_mono_ns = base_ns + 2 * S
    collector.sample(at_ns=base_ns + 2 * S, at_ms=base_ms + 2_000)
    now = {(row["side"], row["price"]): row["qty"]
           for row in collector.wall.state_payloads(limit=10)[0]}
    key = ("ASK", str(big))
    assert opened[key] != now[key] and now[key] == "20"


def test_the_active_list_is_bounded_largest_first_and_says_when_it_was_cut(running):
    """Truncation that could hide the largest wall would be worse than no bound at all."""
    collector, base_ms, base_ns = running
    collector.sample(at_ns=base_ns + S, at_ms=base_ms + 1_000)
    items, truncated = collector.wall.state_payloads(limit=1)
    assert truncated is True and len(items) == 1
    notionals = [Decimal(row["notional"])
                 for row in collector.wall.state_payloads(limit=1_000)[0]]
    assert Decimal(items[0]["notional"]) == max(notionals)
    assert notionals == sorted(notionals, reverse=True)


def test_the_final_state_file_reports_an_ended_session_with_no_candidates(running):
    collector, base_ms, base_ns = running
    collector.sample(at_ns=base_ns + S, at_ms=base_ms + 1_000)
    collector.finish("test", at_ns=base_ns + 2 * S, at_ms=base_ms + 2_000)
    import json
    payload = json.loads(collector.store.state_path().read_bytes())
    assert payload["session"]["ended"] is True
    assert payload["session"]["end_reason"] == "test"
    assert payload["walls"]["items"] == []
    # The last sample's metrics survive; only the claim that they are current is withdrawn.
    assert payload["derived"]["book"]["mid"] is not None


def test_the_state_file_is_counted_in_the_storage_stats(running):
    collector, base_ms, base_ns = running
    collector.sample(at_ns=base_ns + S, at_ms=base_ms + 1_000)
    stats = collector.emit_stats(at_ns=base_ns + S, at_ms=base_ms + 1_000)
    assert stats["state_file"]["writes"] >= 1 and stats["state_file"]["failed"] == 0
    assert stats["state_file"]["bytes"] > 0
    assert stats["state_file"]["is_authority"] is False
