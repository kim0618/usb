"""Snapshot request ownership: a response may only be installed by the attempt that asked.

This file exists because of one measurement. Under a forced 1,400 ms REST delay on 2026-10-04,
a staged refresh was abandoned at its 1,000 ms deadline, the response arrived to find no refresh
in progress, and the generic recovery path installed it onto a **healthy** book with no
newer-check. The book moved back about 141,000 update ids and the next frame took
`GAP_FIRST_DELTA`. The collector manufactured a gap that had not happened on the wire, and then
recovered from it.

So every request now carries an id, a purpose and a state, and the tests below are about the one
property that follows: a response that is not owned by a live attempt changes **nothing**. Not
the levels, not the bounds, not `last_update_id`, not the generation, and not `snapshot_wanted` -
because asking for a recovery is itself a change, and the fault that invalidated a book is the
only thing entitled to ask for one.

`observable()` is compared before and after every late arrival rather than asserting on single
fields: a test that checks the three fields somebody thought of is a test that passes when the
fourth one moves.
"""
from __future__ import annotations

import time

import pytest

from app.crypto.market_structure_v0 import book as B
from app.crypto.market_structure_v0 import collector as C
from app.crypto.market_structure_v0.envelope import Session
from app.crypto.market_structure_v0.store import Store

from tests.crypto.ms_v0_fixtures import RecordingSink, depth_frame
from tests.crypto.liquidity_map_fixtures import wall_snapshot

S = 1_000_000_000
MS = 1_000_000

#: The measured rollback. Used as the late snapshot's distance behind the live chain so the
#: number in the telemetry is the number from the incident.
MEASURED_ROLLBACK_IDS = 141_000


@pytest.fixture
def live(tmp_path):
    """A synchronized book that has applied a delta, with its startup snapshot owned."""
    session = Session()
    store = Store.open(tmp_path, session.session_id, started_ns=session.started_ns)
    sink = RecordingSink()
    collector = C.Collector(store=store, session=session, sink=sink)
    base_ms, base_ns = int(time.time() * 1000), time.monotonic_ns()
    collector.write_session_record(config={})
    collector.on_depth_connect("d1", receive_ms=base_ms, mono_ns=base_ns)
    request = collector.mark_snapshot_requested(receive_ms=base_ms, mono_ns=base_ns)
    assert request.purpose == C.PURPOSE_INITIAL_SYNC
    collector.on_snapshot(wall_snapshot(), receive_ms=base_ms, mono_ns=base_ns,
                          request_ms=base_ms, request_mono_ns=base_ns,
                          request_id=request.request_id)
    frame(collector, 1, base_ms, base_ns)
    collector.sample(at_ns=base_ns + S, at_ms=base_ms + 1_000)
    assert collector.depth.state == B.SYNCED and collector.depth.first_delta_applied
    yield collector, base_ms, base_ns
    store.close()


def frame(collector, second: int, base_ms: int, base_ns: int, *, price: str = "84700",
          qty: str = "1", gap: bool = False) -> str:
    book = collector.depth
    if not book.first_delta_applied:
        first, last = book.snapshot_update_id - 5, book.snapshot_update_id + 5
        previous = first - 1
    elif gap:
        first, last, previous = (book.last_update_id + 500, book.last_update_id + 510,
                                 book.last_update_id + 499)
    else:
        first, last, previous = (book.last_update_id + 1, book.last_update_id + 10,
                                 book.last_update_id)
    return collector.on_depth_frame(
        depth_frame(first=first, last=last, previous=previous, bids=[[price, qty]],
                    event_ms=base_ms + second * 1_000 - 5),
        receive_ms=base_ms + second * 1_000, mono_ns=base_ns + second * S)


def want_refresh(collector, second: int, base_ms: int, base_ns: int,
                 reason: str = C.REFRESH_COVERAGE_EDGE):
    """Ask for a voluntary refresh and send the request, exactly as the runner does."""
    at_ms, at_ns = base_ms + second * 1_000, base_ns + second * S
    collector._want_refresh(reason, at_ns=at_ns, at_ms=at_ms)
    request = collector.mark_snapshot_requested(receive_ms=at_ms, mono_ns=at_ns)
    return request, at_ms, at_ns


def deliver(collector, request, at_ms: int, at_ns: int, *, round_trip_ms: int,
            last_update_id: int, request_id: str | None = ...) -> str:
    body = dict(wall_snapshot(), lastUpdateId=last_update_id)
    return collector.on_snapshot(
        body, receive_ms=at_ms + round_trip_ms, mono_ns=at_ns + round_trip_ms * MS,
        request_ms=at_ms, request_mono_ns=at_ns,
        request_id=(request.request_id if request_id is ... else request_id))


def observable(collector) -> dict:
    """Everything a consumer of the live book can see, including why it is in this state."""
    book = collector.depth
    return {"state": book.state, "generation": book.generation,
            "last_update_id": book.last_update_id, "snapshot_update_id": book.snapshot_update_id,
            "known_low": book.known_low, "known_high": book.known_high,
            "bids": dict(book.bids), "asks": dict(book.asks), "gaps": book.gaps,
            "resyncs": book.resyncs, "snapshots_rejected": book.snapshots_rejected,
            "first_delta_applied": book.first_delta_applied,
            "last_invalidation": book.last_invalidation}


def discards(sink) -> list[dict]:
    return sink.telemetry(C.SNAPSHOT_DISCARDED)


# --- the measured defect ----------------------------------------------------------------------

def test_a_snapshot_that_arrives_after_its_deadline_abort_is_discarded(live):
    """The incident, reproduced: the late response must not reach the recovery path."""
    collector, base_ms, base_ns = live
    sink = collector.sink
    request, at_ms, at_ns = want_refresh(collector, 2, base_ms, base_ns)
    assert request.purpose == C.PURPOSE_SOFT_REFRESH
    assert request.refresh_attempt_id == collector.refresh.attempt_id

    # The deadline passes with the read still outstanding, exactly as the sampler finds it.
    collector.check_refresh_deadline(at_ns=at_ns + 1_100 * MS, at_ms=at_ms + 1_100)
    assert collector.refresh is None
    assert request.state == C.REQUEST_ABORTED
    assert request.closed_reason == C.ABANDON_DEADLINE

    before = observable(collector)
    live_u = collector.depth.last_update_id
    deliver(collector, request, at_ms, at_ns, round_trip_ms=1_400,
            last_update_id=live_u - MEASURED_ROLLBACK_IDS)

    assert observable(collector) == before, "a late response may not change the live book"
    assert collector.depth.last_update_id == live_u
    row = discards(sink)[-1]
    assert row["response_disposition"] == C.RESPONSE_DISCARDED_ABORTED
    assert row["snapshot_request_id"] == request.request_id
    assert row["refresh_attempt_id"] == request.refresh_attempt_id
    assert row["purpose"] == C.PURPOSE_SOFT_REFRESH
    assert row["response_age_ms"] == 1_400
    assert row["would_have_rolled_back_ids"] == MEASURED_ROLLBACK_IDS
    # And the next frame chains on as if nothing had happened, which is the thing the incident
    # actually broke: v1.4 left the book demanding a first delta it had already had.
    assert frame(collector, 4, base_ms, base_ns) == B.APPLIED
    assert collector.depth.gaps == 0


def test_the_late_response_does_not_ask_for_a_recovery_of_its_own(live):
    collector, base_ms, base_ns = live
    request, at_ms, at_ns = want_refresh(collector, 2, base_ms, base_ns)
    collector.check_refresh_deadline(at_ns=at_ns + 1_100 * MS, at_ms=at_ms + 1_100)
    deliver(collector, request, at_ms, at_ns, round_trip_ms=1_400,
            last_update_id=collector.depth.last_update_id - 5_000)
    assert collector.snapshot_wanted is False, "the book is healthy; nothing is wanted"
    # But the collector is not wedged either: the read has been answered, so the policy may ask
    # again once its backoff has elapsed.
    assert collector.snapshot_in_flight is False
    assert collector.refresh_retry_after_ns is not None


def test_a_refresh_whose_book_faulted_mid_read_does_not_donate_its_snapshot(live):
    """V1.3 and V1.4 fell through to the recovery path here. V1.5 refuses the promotion.

    The cost of refusing is one extra round trip of UNSYNCED time. The cost of allowing it is
    that a response can be installed for a purpose it was not requested for, which is the hole
    the 141,000-id rollback came through.
    """
    collector, base_ms, base_ns = live
    sink = collector.sink
    request, at_ms, at_ns = want_refresh(collector, 2, base_ms, base_ns)
    # A gap on the live chain while the read is in flight: a HARD fault.
    assert frame(collector, 3, base_ms, base_ns, gap=True) == B.GAP
    assert collector.depth.state != B.SYNCED
    assert collector.snapshot_wanted is True, "the fault asked for the recovery, not the refresh"
    assert request.state == C.REQUEST_ABORTED
    assert request.closed_reason == C.ABANDON_FAULT

    generation = collector.depth.generation
    deliver(collector, request, at_ms, at_ns, round_trip_ms=150,
            last_update_id=20_000)
    assert collector.depth.state != B.SYNCED, "the SOFT response may not install the recovery"
    assert collector.depth.generation == generation
    assert discards(sink)[-1]["response_disposition"] == C.RESPONSE_DISCARDED_ABORTED
    # The recovery then runs on its own request, with its own purpose.
    recovery = collector.mark_snapshot_requested(receive_ms=base_ms + 4_000,
                                                 mono_ns=base_ns + 4 * S)
    assert recovery.purpose == C.PURPOSE_HARD_RECOVERY
    assert deliver(collector, recovery, base_ms + 4_000, base_ns + 4 * S, round_trip_ms=100,
                   last_update_id=30_000) == B.SYNCED
    assert recovery.state == C.REQUEST_APPLIED
    assert recovery.disposition == C.RESPONSE_APPLIED


def test_a_previous_soft_response_cannot_be_used_by_a_hard_recovery_in_progress(live):
    """Rule B: a HARD attempt installs only what it asked for itself."""
    collector, base_ms, base_ns = live
    sink = collector.sink
    soft, soft_ms, soft_ns = want_refresh(collector, 2, base_ms, base_ns)
    collector.check_refresh_deadline(at_ns=soft_ns + 1_100 * MS, at_ms=soft_ms + 1_100)
    assert frame(collector, 4, base_ms, base_ns, gap=True) == B.GAP

    hard = collector.mark_snapshot_requested(receive_ms=base_ms + 5_000, mono_ns=base_ns + 5 * S)
    assert hard.purpose == C.PURPOSE_HARD_RECOVERY
    # Now the abandoned refresh's read finally returns, while the HARD read is still out.
    deliver(collector, soft, soft_ms, soft_ns, round_trip_ms=3_100, last_update_id=10_000)
    assert collector.depth.state != B.SYNCED
    assert discards(sink)[-1]["response_disposition"] == C.RESPONSE_DISCARDED_ABORTED
    # The HARD request is untouched by the arrival of somebody else's response.
    assert hard.state == C.REQUEST_ACTIVE
    assert collector.snapshot_in_flight is True
    assert collector.snapshot_request is hard
    assert deliver(collector, hard, base_ms + 5_000, base_ns + 5 * S, round_trip_ms=120,
                   last_update_id=40_000) == B.SYNCED


# --- expired, superseded, wrong owner ---------------------------------------------------------

def test_a_soft_response_older_than_the_staged_deadline_is_expired(live):
    """Belt to the deadline's braces: the age is checked at arrival, not only by the sampler."""
    collector, base_ms, base_ns = live
    sink = collector.sink
    request, at_ms, at_ns = want_refresh(collector, 2, base_ms, base_ns)
    # Nothing aborted the attempt - no sample ran - so the request is still ACTIVE.
    assert collector.refresh is not None and request.state == C.REQUEST_ACTIVE
    before = observable(collector)
    deliver(collector, request, at_ms, at_ns, round_trip_ms=C.REFRESH_DEADLINE_MS + 1,
            last_update_id=collector.depth.last_update_id)
    assert observable(collector) == before
    row = discards(sink)[-1]
    assert row["response_disposition"] == C.RESPONSE_DISCARDED_EXPIRED
    assert row["closed_reason"] == C.EXPIRE_PAST_DEADLINE
    assert request.state == C.REQUEST_EXPIRED
    assert collector.refreshes_applied == 0


def test_a_soft_response_whose_attempt_was_replaced_is_expired(live):
    """The orphan case: the request lives, the attempt that owns it does not."""
    collector, base_ms, base_ns = live
    sink = collector.sink
    request, at_ms, at_ns = want_refresh(collector, 2, base_ms, base_ns)
    # A second attempt object, as a bug or a hand-rolled caller would produce.
    collector.refresh_attempts_started += 1
    collector.refresh = C.RefreshAttempt(
        attempt_id=f"refresh-{collector.refresh_attempts_started}",
        reason=C.REFRESH_COVERAGE_EDGE, requested_ms=at_ms, requested_ns=at_ns,
        generation=collector.depth.generation, faults=collector._fault_counters(),
        live_update_id=collector.depth.last_update_id)
    before = observable(collector)
    deliver(collector, request, at_ms, at_ns, round_trip_ms=100,
            last_update_id=collector.depth.last_update_id)
    assert observable(collector) == before
    row = discards(sink)[-1]
    assert row["response_disposition"] == C.RESPONSE_DISCARDED_EXPIRED
    assert row["closed_reason"] == C.EXPIRE_ORPHANED


def test_an_unknown_request_id_is_never_installed_onto_a_usable_book(live):
    collector, base_ms, base_ns = live
    sink = collector.sink
    before = observable(collector)
    collector.on_snapshot(dict(wall_snapshot(), lastUpdateId=10),
                          receive_ms=base_ms + 3_000, mono_ns=base_ns + 3 * S,
                          request_ms=base_ms + 2_900, request_mono_ns=base_ns + 29 * 10 ** 8,
                          request_id="snapshot-does-not-exist")
    assert observable(collector) == before
    row = discards(sink)[-1]
    assert row["response_disposition"] == C.RESPONSE_DISCARDED_WRONG_OWNER
    assert row["snapshot_request_id"] == "snapshot-does-not-exist"
    assert row["purpose"] is None


def test_a_second_delivery_of_an_answered_request_is_wrong_owner(live):
    collector, base_ms, base_ns = live
    sink = collector.sink
    request, at_ms, at_ns = want_refresh(collector, 2, base_ms, base_ns)
    deliver(collector, request, at_ms, at_ns, round_trip_ms=100,
            last_update_id=collector.depth.last_update_id)
    assert collector.refreshes_applied == 1 and request.state == C.REQUEST_APPLIED
    before = observable(collector)
    deliver(collector, request, at_ms, at_ns, round_trip_ms=100,
            last_update_id=collector.depth.last_update_id - 7_000)
    assert observable(collector) == before
    assert discards(sink)[-1]["response_disposition"] == C.RESPONSE_DISCARDED_WRONG_OWNER
    assert collector.refreshes_applied == 1


def test_a_newer_request_expires_the_one_that_was_still_outstanding(live):
    """Concurrency: two outstanding requests, and only the newer one is installable."""
    collector, base_ms, base_ns = live
    assert frame(collector, 2, base_ms, base_ns, gap=True) == B.GAP
    first = collector.mark_snapshot_requested(receive_ms=base_ms + 2_100,
                                              mono_ns=base_ns + 21 * 10 ** 8)
    second = collector.mark_snapshot_requested(receive_ms=base_ms + 2_200,
                                               mono_ns=base_ns + 22 * 10 ** 8)
    assert first.state == C.REQUEST_EXPIRED
    assert first.closed_reason == C.EXPIRE_SUPERSEDED
    assert second.state == C.REQUEST_ACTIVE
    active = [r for r in collector.snapshot_requests if r.state == C.REQUEST_ACTIVE]
    assert active == [second], "exactly one request may be installable at a time"


def test_responses_that_arrive_out_of_order_install_the_newer_and_discard_the_older(live):
    collector, base_ms, base_ns = live
    sink = collector.sink
    assert frame(collector, 2, base_ms, base_ns, gap=True) == B.GAP
    first = collector.mark_snapshot_requested(receive_ms=base_ms + 2_100,
                                              mono_ns=base_ns + 21 * 10 ** 8)
    second = collector.mark_snapshot_requested(receive_ms=base_ms + 2_200,
                                               mono_ns=base_ns + 22 * 10 ** 8)
    assert deliver(collector, second, base_ms + 2_200, base_ns + 22 * 10 ** 8,
                   round_trip_ms=100, last_update_id=90_000) == B.SYNCED
    installed = observable(collector)
    # The first read, which was taken earlier and is therefore behind, finally lands.
    deliver(collector, first, base_ms + 2_100, base_ns + 21 * 10 ** 8, round_trip_ms=400,
            last_update_id=50_000)
    assert observable(collector) == installed
    assert collector.depth.last_update_id == 90_000
    assert discards(sink)[-1]["response_disposition"] == C.RESPONSE_DISCARDED_EXPIRED


# --- the invariant, across a sequence ---------------------------------------------------------

def test_no_sequence_of_late_responses_can_move_the_chain_backwards(live):
    """The property, rather than one of its instances: the chain only ever goes forward."""
    collector, base_ms, base_ns = live
    seen = [collector.depth.last_update_id]
    for round_index in range(1, 6):
        second = 2 * round_index
        request, at_ms, at_ns = want_refresh(collector, second, base_ms, base_ns)
        collector.check_refresh_deadline(at_ns=at_ns + 1_100 * MS, at_ms=at_ms + 1_100)
        deliver(collector, request, at_ms, at_ns, round_trip_ms=1_500,
                last_update_id=max(1, collector.depth.last_update_id - 50_000 * round_index))
        seen.append(collector.depth.last_update_id)
        frame(collector, second + 1, base_ms, base_ns)
        seen.append(collector.depth.last_update_id)
    assert seen == sorted(seen), "last_update_id decreased"
    assert collector.depth.gaps == 0
    assert collector.depth.resyncs == 1, "only the startup snapshot installed"
    assert sum(collector.snapshot_responses_discarded.values()) == 5
    assert collector.snapshot_responses_discarded == {C.RESPONSE_DISCARDED_ABORTED: 5}


# --- what is published ------------------------------------------------------------------------

def test_the_raw_snapshot_record_names_its_owner_and_its_disposition(live):
    collector, base_ms, base_ns = live
    sink = collector.sink
    request, at_ms, at_ns = want_refresh(collector, 2, base_ms, base_ns)
    deliver(collector, request, at_ms, at_ns, round_trip_ms=90,
            last_update_id=collector.depth.last_update_id)
    row = sink.last("snapshot")
    assert row["snapshot_request_id"] == request.request_id
    assert row["refresh_attempt_id"] == request.refresh_attempt_id
    assert row["purpose"] == C.PURPOSE_SOFT_REFRESH
    assert row["response_disposition"] == C.RESPONSE_APPLIED
    assert row["response_age_ms"] == 90


def test_a_refused_raw_record_says_a_replay_must_not_install_it(live):
    """The raw stream is the replay authority, so a refusal has to be legible in it.

    A replay tool that applied every `snapshot` record it found would reproduce the defect this
    whole mechanism exists to prevent, so the record that was refused says so in it.
    """
    collector, base_ms, base_ns = live
    sink = collector.sink
    request, at_ms, at_ns = want_refresh(collector, 2, base_ms, base_ns)
    collector.check_refresh_deadline(at_ns=at_ns + 1_100 * MS, at_ms=at_ms + 1_100)
    deliver(collector, request, at_ms, at_ns, round_trip_ms=1_400,
            last_update_id=collector.depth.last_update_id - 10_000)
    refused = sink.last("snapshot")
    assert refused["response_disposition"] == C.RESPONSE_DISCARDED_ABORTED
    assert "a replay must honour response_disposition" in refused["not_applied_note"]
    # The response itself is still there in full: refusing to install it is not a reason to stop
    # recording what the exchange said.
    assert refused["response"]["lastUpdateId"] == collector.depth.last_update_id - 10_000
    # An installed record carries no such note, so the note's presence is the signal.
    assert "not_applied_note" not in [row for row in sink.of("snapshot")
                                      if row["response_disposition"] == C.RESPONSE_APPLIED][0]


def test_the_policy_view_publishes_the_ownership_and_refuses_late_installs(live):
    collector, base_ms, base_ns = live
    request, at_ms, at_ns = want_refresh(collector, 2, base_ms, base_ns)
    collector.check_refresh_deadline(at_ns=at_ns + 1_100 * MS, at_ms=at_ms + 1_100)
    deliver(collector, request, at_ms, at_ns, round_trip_ms=1_400,
            last_update_id=collector.depth.last_update_id - 1_000)
    view = collector.resnapshot_view(at_ns=at_ns + 2 * S)
    assert view["late_response_can_install"] is False
    assert view["snapshot_requests_issued"] == 2
    assert view["snapshot_request"]["snapshot_request_id"] == request.request_id
    assert view["snapshot_request"]["request_state"] == C.REQUEST_ABORTED
    assert view["snapshot_request"]["response_disposition"] == C.RESPONSE_DISCARDED_ABORTED
    assert view["snapshot_responses_discarded"] == {C.RESPONSE_DISCARDED_ABORTED: 1}


def test_the_purposes_are_decided_at_request_time_and_are_exhaustive(live):
    collector, base_ms, base_ns = live
    assert C.PURPOSE_SOFT_REFRESH == "SOFT_REFRESH"
    assert C.PURPOSE_HARD_RECOVERY == "HARD_RECOVERY"
    assert C.PURPOSE_INITIAL_SYNC == "INITIAL_SYNC"
    assert {C.REQUEST_ACTIVE, C.REQUEST_APPLIED, C.REQUEST_ABORTED,
            C.REQUEST_EXPIRED} == {"ACTIVE", "APPLIED", "ABORTED", "EXPIRED"}
    assert {C.RESPONSE_APPLIED, C.RESPONSE_DISCARDED_ABORTED, C.RESPONSE_DISCARDED_EXPIRED,
            C.RESPONSE_DISCARDED_WRONG_OWNER} == {
        "APPLIED", "DISCARDED_ABORTED", "DISCARDED_EXPIRED", "DISCARDED_WRONG_OWNER"}
    # The startup snapshot is an INITIAL_SYNC, and once a book exists a recovery is a recovery.
    assert collector.snapshot_requests[0].purpose == C.PURPOSE_INITIAL_SYNC
    assert frame(collector, 2, base_ms, base_ns, gap=True) == B.GAP
    assert collector.mark_snapshot_requested(
        receive_ms=base_ms + 2_100, mono_ns=base_ns + 21 * 10 ** 8
    ).purpose == C.PURPOSE_HARD_RECOVERY
