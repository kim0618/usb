"""Staged refresh: buffer, replay, catch up, swap - and every way it must decline instead.

The design exists because of a measurement. V1.2 handed the REST snapshot to the live book and
refused the install whenever the snapshot was not newer than the deltas already applied. On a
moving book that refused **every** attempt: two requests in 23 minutes, two refusals, zero
installs, while the ±0.1% band the preview may call COMPLETE left the known interval for 50
seconds. The snapshot was not the problem - an independent probe found it *newer* than the
newest stream frame in 10 reads out of 10 - the problem was comparing it to a live book that had
moved during the round trip.

So the snapshot is now the **base of a second book** rather than a replacement, and the one
invariant everything here protects is:

    staging.last_update_id == live.last_update_id, at the moment of the swap

which makes a swap a change of levels and bounds only. Two consequences the tests below pin:

* the live book is never rolled back, because its chain position is never assigned at all;
* every event between the snapshot and the swap was applied individually rather than absorbed,
  so the window `lm-continuity` bounds contains no unenumerated event.

Most of this file is about declining. A refresh that cannot reach the invariant has to leave a
book that is still serving, still synchronized, and still at the same generation - and the way
that is checked is by comparing the whole observable book before and after the attempt.
"""
from __future__ import annotations

import time
from decimal import Decimal

import pytest

from app.crypto.market_structure_v0 import book as B
from app.crypto.market_structure_v0 import collector as C
from app.crypto.market_structure_v0 import walls as W
from app.crypto.market_structure_v0.envelope import Session
from app.crypto.market_structure_v0.store import Store

from tests.crypto.ms_v0_fixtures import RecordingSink, depth_frame
from tests.crypto.liquidity_map_fixtures import wall_snapshot

S = 1_000_000_000


@pytest.fixture
def live(tmp_path):
    """A collector with a synchronized book that has already applied a delta."""
    session = Session()
    store = Store.open(tmp_path, session.session_id, started_ns=session.started_ns)
    sink = RecordingSink()
    collector = C.Collector(store=store, session=session, sink=sink)
    base_ms, base_ns = int(time.time() * 1000), time.monotonic_ns()
    collector.write_session_record(config={})
    collector.on_depth_connect("d1", receive_ms=base_ms, mono_ns=base_ns)
    collector.mark_snapshot_requested(receive_ms=base_ms, mono_ns=base_ns)
    collector.on_snapshot(wall_snapshot(), receive_ms=base_ms, mono_ns=base_ns,
                          request_ms=base_ms, request_mono_ns=base_ns)
    frame(collector, 1, base_ms, base_ns)
    # One sample, as the runner takes one every second. It also retires the startup snapshot's
    # classification, which no transition will ever consume.
    collector.sample(at_ns=base_ns + S, at_ms=base_ms + 1_000)
    assert collector.depth.state == B.SYNCED and collector.depth.first_delta_applied
    yield collector, base_ms, base_ns
    store.close()


def frame(collector, second: int, base_ms: int, base_ns: int, *, price: str = "84700",
          qty: str = "1", gap: bool = False, span: int = 10) -> str:
    """One depth frame that chains onto the live book, or deliberately does not."""
    book = collector.depth
    if not book.first_delta_applied:
        first, last = book.snapshot_update_id - 5, book.snapshot_update_id + 5
        previous = first - 1
    elif gap:
        first, last, previous = book.last_update_id + 500, book.last_update_id + 510, \
            book.last_update_id + 499
    else:
        first, last, previous = book.last_update_id + 1, book.last_update_id + span, \
            book.last_update_id
    return collector.on_depth_frame(
        depth_frame(first=first, last=last, previous=previous, bids=[[price, qty]],
                    event_ms=base_ms + second * 1_000 - 5),
        receive_ms=base_ms + second * 1_000, mono_ns=base_ns + second * S)


def request(collector, second: int, base_ms: int, base_ns: int,
            reason: str = C.REFRESH_COVERAGE_EDGE) -> tuple[int, int]:
    at_ms, at_ns = base_ms + second * 1_000, base_ns + second * S
    collector._want_refresh(reason, at_ns=at_ns, at_ms=at_ms)
    collector.mark_snapshot_requested(receive_ms=at_ms, mono_ns=at_ns)
    return at_ms, at_ns


def deliver(collector, request_ms: int, request_ns: int, *, last_update_id: int | None,
            payload: dict | None = None, round_trip_ms: int = 100) -> None:
    body = dict(payload or wall_snapshot())
    if last_update_id is not None:
        body["lastUpdateId"] = last_update_id
    collector.on_snapshot(body, receive_ms=request_ms + round_trip_ms,
                          mono_ns=request_ns + round_trip_ms * 1_000_000,
                          request_ms=request_ms, request_mono_ns=request_ns)


def observable(collector) -> dict:
    """Everything a consumer of the live book can see. Compared across a failed attempt."""
    book = collector.depth
    return {"state": book.state, "generation": book.generation,
            "last_update_id": book.last_update_id, "snapshot_update_id": book.snapshot_update_id,
            "known_low": book.known_low, "known_high": book.known_high,
            "mid": book.mid(), "bids": dict(book.bids), "asks": dict(book.asks),
            "gaps": book.gaps, "first_delta_applied": book.first_delta_applied}


# --- the swap -----------------------------------------------------------------------------

def test_a_snapshot_at_the_live_chain_position_swaps_straight_in(live):
    collector, base_ms, base_ns = live
    before = collector.depth.last_update_id
    generation = collector.depth.generation
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    deliver(collector, request_ms, request_ns, last_update_id=before)
    assert collector.refreshes_applied == 1 and collector.refreshes_rejected == 0
    assert collector.depth.generation == generation + 1
    assert collector.depth.last_update_id == before
    assert collector.refresh is None


def test_frames_that_arrive_during_the_round_trip_are_replayed_onto_the_staged_book(live):
    """The case that used to be refused: the live book moves while the REST read is in flight."""
    collector, base_ms, base_ns = live
    snapshot_at = collector.depth.last_update_id
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    for second in (3, 4, 5):
        assert frame(collector, second, base_ms, base_ns) == B.APPLIED
    assert len(collector.refresh.frames) == 3
    live_now = collector.depth.last_update_id
    assert live_now > snapshot_at

    deliver(collector, request_ms, request_ns, last_update_id=snapshot_at)
    assert collector.refreshes_applied == 1
    assert collector.depth.last_update_id == live_now, "the chain did not move"
    telemetry = collector.sink.telemetry(C.REFRESH_APPLIED)[-1]
    assert telemetry["replayed_frames"] == 3
    assert telemetry["buffered_at_snapshot"] == 3
    assert telemetry["outcome"] == "APPLIED"


def test_a_snapshot_taken_ahead_of_the_live_chain_waits_for_the_stream_to_catch_up(live):
    """The measured normal case: the snapshot is newer than the stream when it is asked for."""
    collector, base_ms, base_ns = live
    ahead = collector.depth.last_update_id + 5
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    deliver(collector, request_ms, request_ns, last_update_id=ahead)
    # Nothing has swapped: the staged book stands in front of the live one.
    assert collector.refreshes_applied == 0 and collector.refreshes_rejected == 0
    assert collector.refresh is not None
    assert collector.refresh.state == C.REFRESH_REPLAYING
    assert collector.depth.state == B.SYNCED, "and the live book kept serving throughout"

    # The next frame straddles the snapshot id and lands both books on the same number.
    assert frame(collector, 3, base_ms, base_ns) == B.APPLIED
    assert collector.refreshes_applied == 1
    assert collector.refresh is None
    telemetry = collector.sink.telemetry(C.REFRESH_APPLIED)[-1]
    assert telemetry["attachment"] == C.ATTACH_STRADDLE
    assert telemetry["frames_after_snapshot"] == 1


def test_a_snapshot_id_on_a_frame_boundary_attaches_as_the_immediate_successor(live):
    """`U <= lastUpdateId <= u` cannot attach here, and the frame is still provably the next one."""
    collector, base_ms, base_ns = live
    boundary = collector.depth.last_update_id
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    assert frame(collector, 3, base_ms, base_ns) == B.APPLIED   # U == boundary + 1
    deliver(collector, request_ms, request_ns, last_update_id=boundary)
    assert collector.refreshes_applied == 1
    telemetry = collector.sink.telemetry(C.REFRESH_APPLIED)[-1]
    assert telemetry["attachment"] == C.ATTACH_SUCCESSOR


def test_the_swap_takes_the_new_levels_and_bounds_and_keeps_the_chain(live):
    collector, base_ms, base_ns = live
    wide = wall_snapshot(levels=40)
    before = observable(collector)
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    deliver(collector, request_ms, request_ns, last_update_id=before["last_update_id"],
            payload=wide)
    after = observable(collector)
    assert after["known_low"] < before["known_low"], "the interval was re-centred"
    assert after["known_high"] > before["known_high"]
    assert after["last_update_id"] == before["last_update_id"]
    assert after["generation"] == before["generation"] + 1
    assert after["gaps"] == before["gaps"] == 0
    assert after["first_delta_applied"] is True


def test_the_swap_is_published_as_a_soft_transition_with_its_replay_evidence(live):
    collector, base_ms, base_ns = live
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    frame(collector, 3, base_ms, base_ns)
    deliver(collector, request_ms, request_ns,
            last_update_id=collector.refresh.live_update_id)
    proof = collector.wall.pending_proof
    assert proof is not None and proof.refresh_type == W.SOFT
    assert proof.continuity_reason == W.REASON_SOFT_PROVEN
    assert proof.chain_preserved is True and proof.replayed_frames == 1
    view = proof.view()
    assert view["basis"] == W.REPLAYED_CHAIN
    assert all(view["gates"].values())


def test_the_divergence_between_the_two_books_is_published(live):
    """Both stand at the same update id and applied the same frames, so they ought to agree."""
    collector, base_ms, base_ns = live
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    frame(collector, 3, base_ms, base_ns, price="84690", qty="7")
    deliver(collector, request_ms, request_ns,
            last_update_id=collector.refresh.live_update_id)
    divergence = collector.sink.telemetry(C.REFRESH_APPLIED)[-1]["divergence"]
    assert divergence["compared"] > 0
    assert divergence["differing"] == 0 and divergence["only_live"] == 0
    assert divergence["identical"] == divergence["compared"]


# --- declining, with the book untouched ---------------------------------------------------

def assert_untouched(collector, before: dict, failure: str) -> None:
    assert observable(collector) == before, "the live book must be exactly as it was"
    assert collector.refresh is None
    assert collector.refreshes_applied == 0
    assert collector.refreshes_rejected >= 1
    assert collector.sink.telemetry(C.REFRESH_REJECTED)[-1]["failure"] == failure
    assert collector.sink.telemetry(C.REFRESH_REJECTED)[-1]["cooldown_consumed"] is False


def test_a_gap_during_the_replay_window_abandons_the_refresh_and_not_the_book(live):
    """A gap is HARD for the book; the refresh built on that chain simply stops existing."""
    collector, base_ms, base_ns = live
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    assert frame(collector, 3, base_ms, base_ns, gap=True) == B.GAP
    assert collector.refresh is None
    assert collector.refreshes_applied == 0
    assert collector.sink.telemetry(C.REFRESH_REJECTED)[-1]["failure"] == C.ABANDON_FAULT
    # The book took the gap on its own terms: UNSYNCED and asking for a recovery snapshot.
    assert collector.depth.state == B.UNSYNCED and collector.snapshot_wanted is True
    # And the recovery snapshot that follows is HARD, never SOFT.
    collector.mark_snapshot_requested(receive_ms=base_ms + 4_000, mono_ns=base_ns + 4 * S)
    collector.on_snapshot(wall_snapshot(last_update_id=99_999), receive_ms=base_ms + 4_100,
                          mono_ns=base_ns + 41 * 10 ** 8, request_ms=base_ms + 4_000,
                          request_mono_ns=base_ns + 4 * S)
    assert collector.wall.pending_proof.refresh_type == W.HARD


def test_a_disconnect_during_the_replay_window_abandons_the_refresh(live):
    collector, base_ms, base_ns = live
    request(collector, 2, base_ms, base_ns)
    collector.on_depth_disconnect("closed", receive_ms=base_ms + 3_000, mono_ns=base_ns + 3 * S)
    assert collector.refresh is None
    assert collector.sink.telemetry(C.REFRESH_REJECTED)[-1]["failure"] == C.ABANDON_FAULT


def test_a_reconnect_during_the_replay_window_abandons_the_refresh(live):
    collector, base_ms, base_ns = live
    request(collector, 2, base_ms, base_ns)
    collector.on_depth_connect("d2", receive_ms=base_ms + 3_000, mono_ns=base_ns + 3 * S)
    assert collector.refresh is None
    assert collector.sink.telemetry(C.REFRESH_REJECTED)[-1]["failure"] == C.ABANDON_FAULT


def test_a_stale_book_during_the_replay_window_abandons_the_refresh(live):
    collector, base_ms, base_ns = live
    request(collector, 2, base_ms, base_ns)
    collector.check_staleness(at_ns=base_ns + 30 * S, at_ms=base_ms + 30_000)
    assert collector.depth.state == B.UNSYNCED
    assert collector.refresh is None
    assert collector.sink.telemetry(C.REFRESH_REJECTED)[-1]["failure"] == C.ABANDON_FAULT


def test_a_snapshot_the_buffer_cannot_bridge_to_the_live_chain_is_abandoned(live):
    """Old snapshot, and nothing held that would carry the staged book forward to the live id."""
    collector, base_ms, base_ns = live
    before = observable(collector)
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    deliver(collector, request_ms, request_ns,
            last_update_id=before["last_update_id"] - 10_000)
    assert_untouched(collector, before, C.ABANDON_REPLAY_INCOMPLETE)


def test_a_hole_between_the_snapshot_and_the_first_held_frame_is_abandoned(live):
    """Frames are held, and none of them provably follows the snapshot. Nothing may be stitched."""
    collector, base_ms, base_ns = live
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    frame(collector, 3, base_ms, base_ns)
    frame(collector, 4, base_ms, base_ns)
    # Taken after the frames, because the book is supposed to go on advancing during a refresh.
    before = observable(collector)
    # Older than the first held frame's `U` and not its `pu`: neither attachment rule can hold.
    deliver(collector, request_ms, request_ns,
            last_update_id=before["last_update_id"] - 10_000)
    assert_untouched(collector, before, C.ABANDON_REPLAY_GAP)


def test_a_malformed_snapshot_is_abandoned_without_touching_the_book(live):
    collector, base_ms, base_ns = live
    before = observable(collector)
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    collector.on_snapshot({"lastUpdateId": 1, "bids": [], "asks": []},
                          receive_ms=request_ms + 100, mono_ns=request_ns + 10 ** 8,
                          request_ms=request_ms, request_mono_ns=request_ns)
    assert_untouched(collector, before, C.ABANDON_SNAPSHOT_REJECTED)


def test_a_rest_read_that_never_arrives_is_abandoned(live):
    collector, base_ms, base_ns = live
    before = observable(collector)
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    collector.on_snapshot_failed("TimeoutError", receive_ms=request_ms + 500,
                                 mono_ns=request_ns + 5 * 10 ** 8)
    assert_untouched(collector, before, C.ABANDON_SNAPSHOT_FAILED)


def test_a_refresh_that_never_reaches_the_invariant_is_abandoned_at_the_deadline(live):
    """A snapshot ahead of a stream that then goes quiet. The attempt must not wait forever."""
    collector, base_ms, base_ns = live
    before = observable(collector)
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    deliver(collector, request_ms, request_ns, last_update_id=before["last_update_id"] + 5)
    assert collector.refresh is not None
    collector.check_refresh_deadline(at_ns=request_ns + (C.REFRESH_DEADLINE_MS + 50) * 10 ** 6,
                                     at_ms=request_ms + C.REFRESH_DEADLINE_MS + 50)
    assert_untouched(collector, before, C.ABANDON_DEADLINE)


def test_a_buffer_that_overflows_abandons_the_refresh_rather_than_the_book(live):
    collector, base_ms, base_ns = live
    collector_before = observable(collector)
    request(collector, 2, base_ms, base_ns)
    for index in range(C.REFRESH_BUFFER_MAX + 1):
        if collector.refresh is None:
            break
        frame(collector, 3, base_ms, base_ns, span=2)
    assert collector.refresh is None
    assert collector.sink.telemetry(C.REFRESH_REJECTED)[-1]["failure"] == C.ABANDON_BUFFER_OVERFLOW
    assert collector.depth.state == B.SYNCED, "the book carried on through all of it"
    assert collector.depth.generation == collector_before["generation"]


def test_duplicate_and_out_of_order_frames_never_enter_the_replay_buffer(live):
    """The buffer is the applied chain. A frame the live book refused is not part of it."""
    collector, base_ms, base_ns = live
    request(collector, 2, base_ms, base_ns)
    assert frame(collector, 3, base_ms, base_ns) == B.APPLIED
    held = collector.depth.last_update_id
    duplicate = depth_frame(first=held - 5, last=held, previous=held - 6)
    assert collector.on_depth_frame(duplicate, receive_ms=base_ms + 3_500,
                                    mono_ns=base_ns + 35 * 10 ** 8) == B.DISCARDED_DUPLICATE
    assert len(collector.refresh.frames) == 1
    assert collector.refresh.frames[0][0].last_update_id == held


# --- the live book keeps serving -----------------------------------------------------------

def test_the_live_book_answers_every_question_throughout_a_staged_refresh(live):
    """The point of staging, as a measurement over every step of one attempt."""
    collector, base_ms, base_ns = live
    seen = []

    def observe():
        seen.append((collector.depth.state, collector.depth.mid() is not None,
                     collector.depth.known_low is not None, len(collector.depth.bids) > 0))

    observe()
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    observe()
    for second in (3, 4, 5):
        frame(collector, second, base_ms, base_ns)
        observe()
    deliver(collector, request_ms, request_ns,
            last_update_id=collector.refresh.live_update_id)
    observe()
    assert collector.refreshes_applied == 1
    assert seen == [(B.SYNCED, True, True, True)] * len(seen)


def test_samples_keep_producing_candidates_across_a_staged_refresh(live):
    collector, base_ms, base_ns = live
    collector.sample(at_ns=base_ns + 2 * S, at_ms=base_ms + 2_000)
    before = collector.wall.counters()["active"]
    assert before > 0
    request_ms, request_ns = request(collector, 3, base_ms, base_ns)
    frame(collector, 3, base_ms, base_ns)
    collector.sample(at_ns=base_ns + 3 * S, at_ms=base_ms + 3_000)
    assert collector.wall.counters()["active"] == before, "mid-refresh, nothing changed"
    deliver(collector, request_ms, request_ns,
            last_update_id=collector.refresh.live_update_id)
    frame(collector, 4, base_ms, base_ns)
    collector.sample(at_ns=base_ns + 4 * S, at_ms=base_ms + 4_000)
    summary = collector.wall.last_transition
    assert summary["refresh_type"] == W.SOFT
    assert (summary["wall_carried"] + summary["wall_ended"] + summary["wall_unknown"]
            == summary["candidates_before"])
    assert summary["wall_carried"] == before


# --- cooldown, backoff, storms and concurrency ---------------------------------------------

def test_a_failed_refresh_consumes_no_cooldown_and_takes_the_short_backoff(live):
    collector, base_ms, base_ns = live
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    assert collector.last_coverage_refresh_ns is None
    collector.on_snapshot_failed("TimeoutError", receive_ms=request_ms + 100,
                                 mono_ns=request_ns + 10 ** 8)
    assert collector.last_coverage_refresh_ns is None, "a failure costs no cooldown"
    assert collector.refresh_retry_after_ns is not None
    backoff = (collector.refresh_retry_after_ns - (request_ns + 10 ** 8)) / 1e9
    assert backoff == pytest.approx(C.REFRESH_RETRY_BACKOFF_S)
    assert C.REFRESH_RETRY_BACKOFF_S < C.COVERAGE_REFRESH_COOLDOWN_S


def test_an_applied_refresh_starts_the_cooldown(live):
    collector, base_ms, base_ns = live
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    deliver(collector, request_ms, request_ns,
            last_update_id=collector.depth.last_update_id)
    assert collector.last_coverage_refresh_ns is not None
    assert collector.refresh_retry_after_ns is None
    assert collector.refresh_failures == 0


def test_the_backoff_holds_the_next_attempt_and_then_lets_it_through(live):
    collector, base_ms, base_ns = live
    collector.depth.known_low = collector.depth.mid() - Decimal("1")
    assert collector.coverage_margin_bps() < C.COVERAGE_MARGIN_TRIGGER_BPS
    assert collector.check_resnapshot_policy(at_ns=base_ns + 2 * S,
                                             at_ms=base_ms + 2_000) == C.REFRESH_COVERAGE_EDGE
    collector.mark_snapshot_requested(receive_ms=base_ms + 2_000, mono_ns=base_ns + 2 * S)
    collector.on_snapshot_failed("TimeoutError", receive_ms=base_ms + 2_100,
                                 mono_ns=base_ns + 21 * 10 ** 8)
    held = base_ns + int((2 + C.REFRESH_RETRY_BACKOFF_S - 1) * S)
    assert collector.check_resnapshot_policy(at_ns=held, at_ms=base_ms + 11_000) is None
    freed = base_ns + int((2 + C.REFRESH_RETRY_BACKOFF_S + 1) * S)
    assert collector.check_resnapshot_policy(
        at_ns=freed, at_ms=base_ms + 13_000) == C.REFRESH_COVERAGE_EDGE


def test_consecutive_failures_are_reported_as_a_storm(live):
    """A refresh that can never succeed is a pattern, and has to be visible as one."""
    collector, base_ms, base_ns = live
    for index in range(C.REFRESH_STORM_EVERY):
        request_ms, request_ns = request(collector, 2 + index, base_ms, base_ns)
        collector.on_snapshot_failed("TimeoutError", receive_ms=request_ms + 10,
                                     mono_ns=request_ns + 10 ** 7)
    storms = collector.sink.telemetry(C.REFRESH_STORM)
    assert len(storms) == 1
    assert storms[0]["consecutive_failures"] == C.REFRESH_STORM_EVERY
    assert storms[0]["backoff_s"] == C.REFRESH_RETRY_BACKOFF_S
    assert collector.refresh_failures == C.REFRESH_STORM_EVERY


def test_a_success_clears_the_failure_count(live):
    collector, base_ms, base_ns = live
    request_ms, request_ns = request(collector, 2, base_ms, base_ns)
    collector.on_snapshot_failed("TimeoutError", receive_ms=request_ms + 10,
                                 mono_ns=request_ns + 10 ** 7)
    assert collector.refresh_failures == 1
    request_ms, request_ns = request(collector, 3, base_ms, base_ns)
    deliver(collector, request_ms, request_ns,
            last_update_id=collector.depth.last_update_id)
    assert collector.refresh_failures == 0 and collector.refresh_retry_after_ns is None


def test_only_one_refresh_can_be_staged_at_a_time(live):
    """Two buffers racing to swap would leave the loser abandoning a book the winner replaced."""
    collector, base_ms, base_ns = live
    collector.depth.known_low = collector.depth.mid() - Decimal("1")
    assert collector.check_resnapshot_policy(at_ns=base_ns + 2 * S,
                                             at_ms=base_ms + 2_000) == C.REFRESH_COVERAGE_EDGE
    first = collector.refresh
    assert collector.check_resnapshot_policy(at_ns=base_ns + 3 * S, at_ms=base_ms + 3_000) is None
    assert collector.check_resnapshot_policy(at_ns=base_ns + 4 * S, at_ms=base_ms + 4_000) is None
    assert collector.refresh is first


def test_the_published_policy_states_how_a_refresh_is_installed(live):
    collector, base_ms, base_ns = live
    view = collector.resnapshot_view(at_ns=base_ns + 2 * S)
    assert view["install"] == "STAGED_BUFFER_AND_REPLAY_ATOMIC_SWAP"
    assert view["failed_refresh_consumes_cooldown"] is False
    assert view["refresh_retry_backoff_s"] == C.REFRESH_RETRY_BACKOFF_S
    assert view["refresh_deadline_ms"] == C.REFRESH_DEADLINE_MS
    assert "never rolls the live book back" in view["cost_note"]


def test_the_bounds_are_what_they_are_stated_to_be():
    assert C.REFRESH_RETRY_BACKOFF_S == 10.0
    assert C.REFRESH_DEADLINE_MS == 1_000
    assert C.REFRESH_BUFFER_MAX == 2_048
    assert W.SOFT_WINDOW_MAX_MS == 300
    # And the things this work was forbidden to move.
    from app.crypto.liquidity_map import wallrule as R
    assert (R.MIN_NOTIONAL_USDT, R.MIN_MULTIPLE, R.MIN_DISTANCE_BPS, R.MIN_PERSISTENCE_MS,
            R.BIN_WIDTH_USDT) == (Decimal("250000"), Decimal("5"), Decimal("1.0"), 10_000,
                                  Decimal("5.0"))
