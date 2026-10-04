"""HARD and SOFT resync, and what each is allowed to do to a wall's observed span.

The thing these tests exist to protect is a *refusal*. A resnapshot that followed a gap, a
reconnect, a stale socket, an overflow or a crossed book was blind for an interval nothing in the
data can bound, and no amount of evidence after the fact may reconnect a wall's history across
it. So most of what is asserted below is that nothing was carried.

The one case where something is carried is a voluntary refresh of a healthy book, and it is
carried only when five gates all held. Those gates are the test subject: each one is checked by
breaking it on its own and watching the transition fall back to HARD, because a gate that is
only ever tested while the others also pass is a gate nobody has tested.

Two invariants run through the whole file:

* **the journal does not change.** `btc-ms.v0.1` says a resync ends wall continuity as UNKNOWN,
  and every `wall` record still says exactly that, with exactly its frozen fields. The carry
  lives in a separate ledger that travels in the state checkpoint and in a `telemetry` record.
* **the three counts always add up.** `wall_carried + wall_ended + wall_unknown` equals the
  number of candidates held before the transition. A reader can therefore never be shown carries
  without being shown what was not carried.
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
#: The oversized bid in `wall_snapshot`: mid 84750, tick 10, offset 4.
BIG_BID = "84710"
#: And the oversized ask: offset 6 on the ask side.
BIG_ASK = "84820"


@pytest.fixture
def live(tmp_path):
    """A collector with a synchronized book, a recording sink and one wall per side."""
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
    assert collector.depth.state == B.SYNCED
    yield collector, base_ms, base_ns
    store.close()


def quiet_delta(collector, *, second: int, base_ms: int, base_ns: int) -> None:
    """One delta that changes nothing material, so the book is fully synchronized.

    A voluntary refresh of a book that never applied a delta is not a refresh of a synchronized
    book, so the continuity gate requires `first_delta_applied`. Getting there needs a frame, and
    this is the smallest one that leaves every wall where it was.

    The ids are derived from the book rather than written down, because the two sync rules are
    different: the first delta after a snapshot has to straddle `lastUpdateId`, and every one
    after that has to chain onto the previous `u`. A fixture with literal ids silently stops
    applying the moment a refresh moves the snapshot id, and the book then drifts out of the test.
    """
    book = collector.depth
    if not book.first_delta_applied:
        assert book.snapshot_update_id is not None
        first = book.snapshot_update_id - 5
        last = book.snapshot_update_id + 5
        previous = first - 1
    else:
        assert book.last_update_id is not None
        first, last, previous = book.last_update_id + 1, book.last_update_id + 10, \
            book.last_update_id
    outcome = collector.on_depth_frame(
        depth_frame(first=first, last=last, previous=previous, bids=[["84700", "1"]],
                    event_ms=base_ms + second * 1_000 - 5),
        receive_ms=base_ms + second * 1_000, mono_ns=base_ns + second * S)
    assert outcome == B.APPLIED, outcome


def soft_refresh(collector, *, second: int, base_ms: int, base_ns: int,
                 payload: dict | None = None, round_trip_ms: int = 100,
                 last_update_id: int | None = None,
                 reason: str = C.REFRESH_COVERAGE_EDGE):
    """Request and stage a voluntary refresh the way the runner would.

    The snapshot's `lastUpdateId` defaults to the live book's current one, which is the case
    where the staged book reaches the live chain the moment the buffer is replayed and the swap
    happens inside `on_snapshot`. The two interesting variants - a snapshot behind the live chain
    and one ahead of it - have their own tests and pass `last_update_id` explicitly.
    """
    request_ms, request_ns = base_ms + second * 1_000, base_ns + second * S
    if payload is None:
        payload = wall_snapshot()
    payload = dict(payload)
    payload["lastUpdateId"] = (collector.depth.last_update_id if last_update_id is None
                               else last_update_id)
    collector._want_refresh(reason, at_ns=request_ns, at_ms=request_ms)
    collector.mark_snapshot_requested(receive_ms=request_ms, mono_ns=request_ns)
    collector.on_snapshot(payload, receive_ms=request_ms + round_trip_ms,
                          mono_ns=request_ns + round_trip_ms * 1_000_000,
                          request_ms=request_ms, request_mono_ns=request_ns)
    return collector.wall.pending_proof


def sample(collector, *, second: int, base_ms: int, base_ns: int) -> dict:
    collector.sample(at_ns=base_ns + second * S, at_ms=base_ms + second * 1_000)
    return collector.state_payload(derived=collector.last_derived, at_ns=base_ns + second * S,
                                   at_ms=base_ms + second * 1_000)


def rows(payload: dict) -> dict[tuple[str, str], dict]:
    return {(row["side"], row["price"]): row for row in payload["walls"]["items"]}


def wall_records(sink: RecordingSink) -> list[dict]:
    return sink.of("wall")


def counts_add_up(summary: dict) -> bool:
    return (summary["wall_carried"] + summary["wall_ended"] + summary["wall_unknown"]
            == summary["candidates_before"])


# --- the contract that is not changing --------------------------------------------------------

def test_a_soft_refresh_still_writes_every_candidate_as_unknown_in_the_journal(live):
    """The frozen contract's wall clause. The ledger is beside the journal, never inside it."""
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    soft_refresh(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    before = len(wall_records(collector.sink))
    sample(collector, second=4, base_ms=base_ms, base_ns=base_ns)
    written = wall_records(collector.sink)[before:]
    closed = [row for row in written if row["status"] == W.INTERRUPTED]
    opened = [row for row in written if row["event"] == W.OPENED]
    assert len(closed) == 2 and len(opened) == 2
    # And the closing rows carry no hint of a carry: the dataset is byte-for-byte what V1.1 wrote.
    for row in written:
        assert not any(key.startswith("continuity_") for key in row)


def test_a_wall_journal_payload_has_exactly_the_fields_the_contract_lists(live):
    """A guard on the line above: the ledger must never leak into a `wall` record."""
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    assert set(wall_records(collector.sink)[0]) == {
        "side", "price", "bin", "bin_rule", "qty", "current_size", "notional", "local_average",
        "multiple", "max_size", "min_size", "max_multiple", "neighbours", "first_seen_ms",
        "last_seen_ms", "persistence_ms", "samples", "status", "event", "coverage",
        "generation", "persistence_is_sampled_span", "order_identity_proven"}


# --- a soft refresh carries what it can prove -------------------------------------------------

def test_a_wall_that_was_in_the_refreshed_snapshot_keeps_its_first_seen_and_its_span(live):
    collector, base_ms, base_ns = live
    opening = sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    origin = rows(opening)[("BID", BIG_BID)]["first_seen_ms"]
    # A frame every second, because a book nothing arrives on goes stale in two and the
    # staleness path is a HARD interruption - which is a different test.
    for second in (2, 3, 4, 5, 6):
        quiet_delta(collector, second=second, base_ms=base_ms, base_ns=base_ns)
        sample(collector, second=second, base_ms=base_ms, base_ns=base_ns)
    proof = soft_refresh(collector, second=6, base_ms=base_ms, base_ns=base_ns)
    assert proof is not None and proof.refresh_type == W.SOFT
    assert proof.continuity_reason == W.REASON_SOFT_PROVEN

    after = sample(collector, second=7, base_ms=base_ms, base_ns=base_ns)
    row = rows(after)[("BID", BIG_BID)]
    assert row["continuity_status"] == W.CARRY_CARRIED
    assert row["continuity_first_seen_ms"] == origin
    assert row["continuity_refreshes"] == 1
    assert row["continuity_proof"] == W.CARRY_PROOF
    # The candidate's own fields still describe this generation's candidate, unchanged.
    assert row["first_seen_ms"] == base_ms + 7_000
    assert row["persistence_ms"] == 0 and row["samples"] == 1
    # The ledger describes the whole proven life: six samples over six seconds.
    assert row["continuity_persistence_ms"] == 6_000
    assert row["continuity_samples"] == 7
    assert row["continuity_origin_generation"] == 1


def test_the_transition_reports_three_counts_that_add_up(live):
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    state = sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    summary = state["continuity"]["last_transition"]
    assert summary["refresh_type"] == W.SOFT
    assert summary["wall_carried"] == 2 and summary["candidates_before"] == 2
    assert summary["wall_ended"] == 0 and summary["wall_unknown"] == 0
    assert counts_add_up(summary)
    assert summary["carried_lost"] == 0


def test_a_wall_that_is_gone_from_the_refreshed_snapshot_ends_rather_than_carrying(live):
    """It was in a region we can see and it is not there, which is an observation."""
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    # The refreshed snapshot keeps the ask wall and flattens the bid wall to its neighbours.
    flat_bid = wall_snapshot()
    for level in flat_bid["bids"]:
        if level[0] == BIG_BID:
            level[1] = "0"
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns, payload=flat_bid)
    state = sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    summary = state["continuity"]["last_transition"]
    assert summary["refresh_type"] == W.SOFT
    assert summary["wall_carried"] == 1 and summary["wall_ended"] == 1
    assert summary["wall_unknown"] == 0 and counts_add_up(summary)
    assert ("BID", BIG_BID) not in rows(state)
    assert rows(state)[("ASK", BIG_ASK)]["continuity_status"] == W.CARRY_CARRIED


def test_a_level_still_present_but_no_longer_a_candidate_ends_rather_than_carrying(live):
    """Eligible is a statement about the snapshot; carried is one about the candidate rule."""
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    # The bid wall survives at 40, and so does everything around it, so it is no longer 3x its
    # neighbourhood and stops qualifying without having moved.
    thick = wall_snapshot()
    thick["bids"] = [[price, "40"] for price, _ in thick["bids"]]
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns, payload=thick)
    state = sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    summary = state["continuity"]["last_transition"]
    assert summary["eligible"] == 2 and summary["wall_carried"] == 1
    assert summary["wall_ended"] == 1 and counts_add_up(summary)


def test_a_wall_outside_the_new_known_interval_is_unknown_and_never_ended(live):
    """An unobservable level has not been observed to end. That distinction is the whole point."""
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    # A snapshot whose bounds no longer reach the bid wall, but still overlap around mid.
    narrow = wall_snapshot()
    narrow["bids"] = [level for level in narrow["bids"] if Decimal(level[0]) > Decimal(BIG_BID)]
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns, payload=narrow)
    state = sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    summary = state["continuity"]["last_transition"]
    assert summary["refresh_type"] == W.SOFT
    assert summary["wall_unknown"] == 1 and summary["wall_ended"] == 0
    assert summary["wall_carried"] == 1 and counts_add_up(summary)


def test_a_carry_can_be_repeated_across_several_soft_refreshes(live):
    collector, base_ms, base_ns = live
    opening = sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    origin = rows(opening)[("BID", BIG_BID)]["first_seen_ms"]
    for second in (3, 6, 9):
        quiet_delta(collector, second=second, base_ms=base_ms, base_ns=base_ns)
        soft_refresh(collector, second=second, base_ms=base_ms, base_ns=base_ns)
        state = sample(collector, second=second + 1, base_ms=base_ms, base_ns=base_ns)
    row = rows(state)[("BID", BIG_BID)]
    assert row["continuity_refreshes"] == 3
    assert row["continuity_first_seen_ms"] == origin
    assert row["continuity_persistence_ms"] == 9_000
    assert collector.wall.soft_refreshes == 3 and collector.wall.hard_transitions == 0


# --- a hard resync carries nothing ------------------------------------------------------------

def test_a_gap_ends_every_wall_and_the_next_snapshot_carries_nothing(live):
    collector, base_ms, base_ns = live
    opening = sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    assert rows(opening)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    # A frame that does not chain onto the last one: `pu` mismatch, which is a gap.
    collector.on_depth_frame(depth_frame(first=5_000, last=5_010, previous=4_999),
                             receive_ms=base_ms + 2_500, mono_ns=base_ns + 25 * 10**8)
    assert collector.depth.state == B.UNSYNCED
    collector.mark_snapshot_requested(receive_ms=base_ms + 2_600, mono_ns=base_ns + 26 * 10**8)
    collector.on_snapshot(wall_snapshot(last_update_id=6_000), receive_ms=base_ms + 2_700,
                          mono_ns=base_ns + 27 * 10**8, request_ms=base_ms + 2_600,
                          request_mono_ns=base_ns + 26 * 10**8)
    proof = collector.wall.pending_proof
    assert proof is not None and proof.refresh_type == W.HARD
    assert proof.continuity_reason == W.REASON_NOT_VOLUNTARY
    state = sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    for row in state["walls"]["items"]:
        assert row["continuity_status"] == W.CARRY_NEW
        assert row["continuity_first_seen_ms"] == row["first_seen_ms"]
        assert row["continuity_refreshes"] == 0
        assert row["continuity_proof"] is None
    assert collector.wall.carried == 0


def test_a_reconnect_carries_nothing_even_if_the_book_comes_back_identical(live):
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    collector.on_depth_connect("d2", receive_ms=base_ms + 2_200, mono_ns=base_ns + 22 * 10**8)
    collector.mark_snapshot_requested(receive_ms=base_ms + 2_300, mono_ns=base_ns + 23 * 10**8)
    collector.on_snapshot(wall_snapshot(last_update_id=7_000), receive_ms=base_ms + 2_400,
                          mono_ns=base_ns + 24 * 10**8, request_ms=base_ms + 2_300,
                          request_mono_ns=base_ns + 23 * 10**8)
    state = sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    assert collector.wall.carried == 0
    assert all(row["continuity_status"] == W.CARRY_NEW for row in state["walls"]["items"])
    assert state["continuity"]["hard_transitions"] >= 1


def test_a_gap_between_a_clean_refresh_and_the_next_sample_voids_the_proof(live):
    """The install proved SOFT, then the stream broke before anything could be carried."""
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    proof = soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    assert proof is not None and proof.refresh_type == W.SOFT
    collector.on_depth_frame(depth_frame(first=9_000, last=9_010, previous=8_999),
                             receive_ms=base_ms + 2_500, mono_ns=base_ns + 25 * 10**8)
    assert collector.depth.state == B.UNSYNCED
    # `wall_interrupt` ran on the gap, which is the one line that voids every pending proof.
    assert collector.wall.pending_proof is None
    collector.mark_snapshot_requested(receive_ms=base_ms + 2_600, mono_ns=base_ns + 26 * 10**8)
    collector.on_snapshot(wall_snapshot(last_update_id=9_500), receive_ms=base_ms + 2_700,
                          mono_ns=base_ns + 27 * 10**8, request_ms=base_ms + 2_600,
                          request_mono_ns=base_ns + 26 * 10**8)
    state = sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    assert collector.wall.carried == 0
    assert all(row["continuity_status"] == W.CARRY_NEW for row in state["walls"]["items"])


def test_a_stale_book_at_the_sample_carries_nothing_even_after_a_clean_refresh(live):
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    # Nothing arrives for long enough that the book is stale by the time it is sampled.
    state = sample(collector, second=20, base_ms=base_ms, base_ns=base_ns)
    assert collector.depth.state == B.UNSYNCED
    assert collector.wall.carried == 0
    summary = state["continuity"]["last_transition"]
    assert summary["refresh_type"] == W.HARD
    assert summary["continuity_reason"] == W.REASON_INTERRUPTED
    # Named with the book's own word for what ended it, not with a code invented here.
    assert summary["cause"] == B.STALE


def test_two_installs_between_two_samples_supersede_each_other_and_carry_nothing(live):
    """The transition in between was never observed, so the newer proof says nothing about it."""
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns,
                 payload=wall_snapshot())
    assert collector.wall.pending_proof is not None
    assert collector.wall.pending_proof.refresh_type == W.HARD
    assert collector.wall.pending_proof.continuity_reason == W.REASON_SUPERSEDED
    state = sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    assert collector.wall.carried == 0 and collector.wall.proofs_superseded == 1
    assert state["continuity"]["proofs_superseded"] == 1


def test_a_clean_stop_ends_continuity_and_publishes_an_empty_carried_set(live):
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    assert collector.wall.carried == 2
    collector.finish("duration", at_ns=base_ns + 4 * S, at_ms=base_ms + 4_000)
    assert collector.wall.pending_proof is None
    assert collector.wall.continuity_view()["active_carried"] == 0


# --- each gate, broken on its own -------------------------------------------------------------

def test_a_snapshot_behind_the_live_chain_is_replayed_forward_instead_of_refused(live):
    """The case V1.3 exists for. V1.2 refused this and measured a 100% refusal rate on a
    moving book; the snapshot is now the *base* of a second book rather than a replacement."""
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    generation = collector.depth.generation
    behind = collector.depth.last_update_id
    # Two more frames land while the REST read is in flight, so by the time the snapshot arrives
    # the live book is ahead of it - exactly the situation that used to be refused.
    request_ms, request_ns = base_ms + 3_000, base_ns + 3 * S
    collector._want_refresh(C.REFRESH_COVERAGE_EDGE, at_ns=request_ns, at_ms=request_ms)
    collector.mark_snapshot_requested(receive_ms=request_ms, mono_ns=request_ns)
    quiet_delta(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=4, base_ms=base_ms, base_ns=base_ns)
    assert collector.depth.last_update_id > behind
    assert len(collector.refresh.frames) == 2, "the round trip's frames are buffered"

    collector.on_snapshot(dict(wall_snapshot(), lastUpdateId=behind),
                          receive_ms=request_ms + 100, mono_ns=request_ns + 10 ** 8,
                          request_ms=request_ms, request_mono_ns=request_ns)
    assert collector.refreshes_rejected == 0
    assert collector.refreshes_applied == 1
    assert collector.depth.generation == generation + 1
    proof = collector.wall.pending_proof
    assert proof is not None and proof.refresh_type == W.SOFT
    assert proof.replayed_frames == 2, "both in-flight frames were replayed onto the staged book"
    assert proof.chain_preserved is True


def test_the_swap_leaves_the_update_id_chain_exactly_where_it_was(live):
    """The invariant the whole design rests on: a swap moves levels, never the chain."""
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    before_id = collector.depth.last_update_id
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    assert collector.depth.last_update_id == before_id
    assert collector.depth.first_delta_applied is True, (
        "the chain is unbroken, so the next frame is an ordinary pu link and not a first delta")
    # And the next frame chains on without a gap, which is what that flag buys.
    quiet_delta(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    assert collector.depth.state == B.SYNCED and collector.depth.gaps == 0


def test_a_refresh_window_exactly_at_the_limit_still_passes(live):
    """A boundary stated in the frozen rule as `at most`, pinned so it cannot drift to `below`."""
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    proof = soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns,
                         round_trip_ms=W.SOFT_WINDOW_MAX_MS)
    assert proof is not None and proof.refresh_type == W.SOFT
    assert proof.window_verdict == W.WINDOW_WITHIN_MAX
    assert proof.view()["window_exempt"] is False, (
        "a window inside the ceiling must never be reported as having used the exemption")


# --------------------------------------------------------------------------- V1.4 S4 exemption
#
# `lm-continuity.v4` exempts a staged refresh from the 300 ms ceiling, because in that one case
# the window bounds nothing: every frame between the snapshot and the swap was replayed onto the
# staged book individually and the swap happened at an identical update id. The ceiling itself,
# and every other gate, is unchanged. These tests are the exemption's five conditions, each one
# removed in turn, plus the thing that still bounds it.


def _window(generation, *, synced=True, first_delta=True, last_update_id=1_000,
            faults=(("gaps", 0),), invalidation=None, levels=None):
    """A `BookWindow` with just enough in it for `classify_refresh` to have an opinion.

    Used where the exemption has to be denied for a reason the collector's staged path cannot
    produce on purpose - an involuntary staged install, a chain that moved under a preserved
    chain - which is exactly where a hand-built window is the honest instrument.
    """
    return W.BookWindow(
        generation=generation, synced=synced, first_delta_applied=first_delta,
        last_update_id=last_update_id, known_low=Decimal("84000"),
        known_high=Decimal("86000"), mid=Decimal("85000"),
        levels=levels if levels is not None else {"BID": {Decimal("84900"): Decimal("12")},
                                                  "ASK": {Decimal("85100"): Decimal("12")}},
        faults=dict(faults), invalidation=invalidation)


def _classify(*, chain_preserved=True, voluntary=True, elapsed_ms=485, after=None, before=None,
              snapshot_update_id=1_000):
    before = before if before is not None else _window(1)
    after = after if after is not None else _window(2)
    return W.classify_refresh(before, after, voluntary=voluntary,
                              reason=C.REFRESH_COVERAGE_EDGE, elapsed_ms=elapsed_ms,
                              snapshot_update_id=snapshot_update_id, replayed_frames=2,
                              chain_preserved=chain_preserved)


def test_a_slow_rest_read_on_a_replayed_chain_is_carried_rather_than_discarded(live):
    """The case v3 was measured losing: 485 ms, book updated correctly, carry deleted anyway.

    On 2026-10-04 one natural refresh in three had a 485 ms round trip, restored the coverage
    margin from 0.38 bp to 5.27 bp, and had all 276 of its candidates turned to UNKNOWN by S4
    while its own proof said `chain_preserved: true`. Under v4 the same transition carries.
    """
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    proof = soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns,
                         round_trip_ms=485)
    assert proof is not None and proof.refresh_type == W.SOFT
    assert proof.chain_preserved is True
    assert proof.elapsed_ms is not None and proof.elapsed_ms > W.SOFT_WINDOW_MAX_MS
    assert proof.window_verdict == W.WINDOW_EXEMPT
    assert dict(proof.gates)[W.GATE_BOUNDED_WINDOW] is True
    assert proof.continuity_reason == W.REASON_SOFT_PROVEN
    state = sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    transition = state["continuity"]["last_transition"]
    assert transition["wall_carried"] == 2 and transition["wall_unknown"] == 0
    assert counts_add_up(transition)
    assert collector.wall.carried == 2


def test_an_exempt_carry_always_says_on_screen_that_it_used_the_exemption(live):
    """An exemption nobody can see on the transition is an exemption nobody can audit."""
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns, round_trip_ms=485)
    state = sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    continuity = state["continuity"]
    published = continuity["last_transition"]["proof"]
    assert published["window_verdict"] == W.WINDOW_EXEMPT
    assert published["window_exempt"] is True
    assert published["window_ms"] == 485 and published["window_max_ms"] == W.SOFT_WINDOW_MAX_MS
    assert published["basis"] == W.REPLAYED_CHAIN
    # And the share of carries resting on the exemption is a count, not an inference.
    assert continuity["soft_refreshes"] == 1 and continuity["soft_window_exempt"] == 1


def test_a_window_inside_the_ceiling_never_increments_the_exemption_count(live):
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns, round_trip_ms=100)
    state = sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    assert state["continuity"]["soft_refreshes"] == 1
    assert state["continuity"]["soft_window_exempt"] == 0
    assert state["continuity"]["last_transition"]["proof"]["window_verdict"] == W.WINDOW_WITHIN_MAX


def test_an_unstaged_slow_install_is_still_hard_exactly_as_it_was():
    """The ceiling is unchanged for the case it was written for.

    An install that handed the snapshot to the live book has an unenumerated window, so a slow
    read there is the thing v2 narrowed the ceiling to refuse, and v4 does not touch it.
    """
    proof = _classify(chain_preserved=False, elapsed_ms=W.SOFT_WINDOW_MAX_MS + 1,
                      snapshot_update_id=1_200, after=_window(2, last_update_id=1_200))
    assert dict(proof.gates)[W.GATE_NEWER] is True, "the unstaged S3 held, so S4 decided this"
    assert proof.refresh_type == W.HARD
    assert proof.continuity_reason == W.REASON_WINDOW_EXCEEDED
    assert proof.window_verdict == W.WINDOW_EXCEEDED
    assert dict(proof.gates)[W.GATE_BOUNDED_WINDOW] is False


def test_the_exemption_does_not_survive_a_stream_that_broke(live):
    """S2 is the clause that means no HARD fault happened, so losing it loses the exemption."""
    proof = _classify(after=_window(2, faults=(("gaps", 1),)))
    assert proof.refresh_type == W.HARD
    assert proof.continuity_reason == W.REASON_STREAM_BROKE
    assert proof.window_verdict == W.WINDOW_EXCEEDED, (
        "a window over the ceiling with a broken stream is exceeded, never exempt")
    assert dict(proof.gates)[W.GATE_BOUNDED_WINDOW] is False


def test_the_exemption_does_not_survive_an_invalidated_book():
    proof = _classify(after=_window(2, invalidation="GAP_PU_MISMATCH"))
    assert proof.refresh_type == W.HARD
    assert proof.continuity_reason == W.REASON_STREAM_BROKE
    assert proof.window_verdict == W.WINDOW_EXCEEDED


def test_the_exemption_does_not_survive_a_chain_that_moved():
    """`staging.last_update_id == live.last_update_id` is a condition of the exemption itself."""
    proof = _classify(after=_window(2, last_update_id=1_700))
    assert proof.refresh_type == W.HARD
    assert proof.continuity_reason == W.REASON_NOT_NEWER
    assert proof.window_verdict == W.WINDOW_EXCEEDED
    assert dict(proof.gates)[W.GATE_BOUNDED_WINDOW] is False


def test_the_exemption_does_not_survive_an_involuntary_install():
    proof = _classify(voluntary=False)
    assert proof.refresh_type == W.HARD
    assert proof.continuity_reason == W.REASON_NOT_VOLUNTARY
    assert proof.window_verdict == W.WINDOW_EXCEEDED


def test_the_exemption_does_not_survive_a_book_that_never_applied_a_delta():
    proof = _classify(before=_window(1, first_delta=False))
    assert proof.refresh_type == W.HARD
    assert proof.continuity_reason == W.REASON_NOT_VOLUNTARY
    assert proof.window_verdict == W.WINDOW_EXCEEDED


@pytest.mark.parametrize("elapsed", [None, -1])
def test_a_window_the_collector_could_not_time_is_never_exempt(elapsed):
    """Fail closed on the measurement itself: no timing, no exemption, whatever else held."""
    proof = _classify(elapsed_ms=elapsed)
    assert proof.refresh_type == W.HARD
    assert proof.continuity_reason == W.REASON_WINDOW_EXCEEDED
    assert proof.window_verdict == W.WINDOW_NOT_MEASURED
    assert proof.view()["window_exempt"] is False


def test_the_exemption_leaves_every_other_gate_exactly_where_it_was():
    """Five gates, all still required. The exemption changes one gate's input, not the set."""
    assert list(W.SOFT_GATES) == ["VOLUNTARY", "CONTINUITY", "NEWER", "BOUNDED_WINDOW", "OVERLAP"]
    assert W.SOFT_WINDOW_MAX_MS == 300, "v4 does not move the ceiling, only when it applies"
    # An overlap failure is not something the exemption can rescue either.
    far = _window(2, levels={"BID": {Decimal("99900"): Decimal("12")},
                             "ASK": {Decimal("100100"): Decimal("12")}})
    far = W.BookWindow(generation=2, synced=True, first_delta_applied=True,
                       last_update_id=1_000, known_low=Decimal("99000"),
                       known_high=Decimal("101000"), mid=Decimal("100000"),
                       levels=far.levels, faults={"gaps": 0}, invalidation=None)
    proof = _classify(after=far)
    assert proof.refresh_type == W.HARD
    assert proof.continuity_reason == W.REASON_NO_OVERLAP


def test_what_bounds_an_exempt_window_in_practice_is_the_staged_deadline(live):
    """The exemption removes the 300 ms ceiling, not every limit on how old a snapshot may be.

    A staged attempt that has not swapped within `REFRESH_DEADLINE_MS` is abandoned with the
    live book untouched, so no exempt carry can ever come from an attempt older than that. The
    frozen rule says this out loud; this is the behaviour behind the sentence.
    """
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    before_id, generation = collector.depth.last_update_id, collector.depth.generation
    request_ms, request_ns = base_ms + 2_000, base_ns + 2 * S
    collector._want_refresh(C.REFRESH_COVERAGE_EDGE, at_ns=request_ns, at_ms=request_ms)
    collector.mark_snapshot_requested(receive_ms=request_ms, mono_ns=request_ns)
    over = C.REFRESH_DEADLINE_MS + 50
    collector.check_refresh_deadline(at_ns=request_ns + over * 10 ** 6, at_ms=request_ms + over)
    assert collector.refresh is None
    assert collector.depth.generation == generation
    assert collector.depth.last_update_id == before_id
    assert collector.wall.pending_proof is None, "an abandoned attempt classifies nothing"
    assert C.REFRESH_DEADLINE_MS == 1_000


def test_a_snapshot_whose_interval_has_left_the_market_fails_closed(live):
    """Snapshot mismatch: the two known intervals no longer describe the same book."""
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    far = wall_snapshot()
    far["bids"] = [[str(Decimal(price) + Decimal("5000")), qty] for price, qty in far["bids"]]
    far["asks"] = [[str(Decimal(price) + Decimal("5000")), qty] for price, qty in far["asks"]]
    proof = soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns, payload=far)
    assert proof is not None and proof.refresh_type == W.HARD
    assert proof.continuity_reason == W.REASON_NO_OVERLAP
    assert proof.overlap["passed"] is False
    state = sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    assert collector.wall.carried == 0
    assert state["continuity"]["last_transition"]["wall_unknown"] == 2


def test_a_refresh_of_a_book_that_never_applied_a_delta_is_hard(live):
    """A book still warming up is not the synchronized book the gate is about."""
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    assert collector.depth.first_delta_applied is False
    proof = soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    assert proof is not None and proof.refresh_type == W.HARD
    assert proof.continuity_reason == W.REASON_NOT_VOLUNTARY


def test_a_recovery_snapshot_is_hard_however_clean_it_looks(live):
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    collector.snapshot_wanted = True
    collector.mark_snapshot_requested(receive_ms=base_ms + 2_100, mono_ns=base_ns + 21 * 10**8)
    assert collector.refresh_in_flight is False
    collector.on_snapshot(wall_snapshot(last_update_id=2_000), receive_ms=base_ms + 2_200,
                          mono_ns=base_ns + 22 * 10**8, request_ms=base_ms + 2_100,
                          request_mono_ns=base_ns + 21 * 10**8)
    proof = collector.wall.pending_proof
    assert proof is not None and proof.refresh_type == W.HARD
    assert dict(proof.gates)[W.GATE_VOLUNTARY] is False


# --- restart ----------------------------------------------------------------------------------

def test_a_restart_cannot_inherit_a_carry_from_the_process_that_died(tmp_path):
    """The ledger lives in memory. An origin from a dead session would be an invented fact."""
    session = Session()
    store = Store.open(tmp_path, session.session_id, started_ns=session.started_ns)
    collector = C.Collector(store=store, session=session, sink=RecordingSink())
    base_ms, base_ns = int(time.time() * 1000), time.monotonic_ns()
    collector.write_session_record(config={})
    collector.on_depth_connect("d1", receive_ms=base_ms, mono_ns=base_ns)
    collector.mark_snapshot_requested(receive_ms=base_ms, mono_ns=base_ns)
    collector.on_snapshot(wall_snapshot(), receive_ms=base_ms, mono_ns=base_ns,
                          request_ms=base_ms, request_mono_ns=base_ns)
    state = sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    assert state["continuity"]["soft_refreshes"] == 0
    assert state["continuity"]["active_carried"] == 0
    for row in state["walls"]["items"]:
        assert row["continuity_status"] == W.CARRY_NEW
        assert row["continuity_first_seen_ms"] == row["first_seen_ms"]
    store.close()


# --- the published ledger ---------------------------------------------------------------------

def test_the_state_file_publishes_the_ledger_beside_the_counts_it_could_not_prove(live):
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    state = sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    ledger = state["continuity"]
    assert ledger["active_carried"] == 2
    assert ledger["wall_carried_total"] == 2
    assert ledger["wall_ended_total"] == 0 and ledger["wall_unknown_total"] == 0
    assert ledger["gates_required"] == list(W.SOFT_GATES)
    assert ledger["window_max_ms"] == W.SOFT_WINDOW_MAX_MS
    assert state["state_version"] == "ms-v0-state.v1-2"


def test_the_classification_and_its_outcome_are_both_in_the_journal(live):
    """The carry is auditable from the journal alone, without a `wall` record changing shape."""
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    events = collector.sink.telemetry(C.WALL_CONTINUITY)
    phases = [event["phase"] for event in events]
    assert "CLASSIFIED" in phases and "APPLIED" in phases
    classified = [event for event in events if event["phase"] == "CLASSIFIED"][-1]
    assert classified["refresh_type"] == W.SOFT
    assert classified["refresh_trigger"] == C.REFRESH_COVERAGE_EDGE
    assert classified["overlap_check"]["passed"] is True
    assert classified["window_ms"] == 100
    applied = [event for event in events if event["phase"] == "APPLIED"][-1]
    assert applied["wall_carried"] == 2 and applied["generation_to"] == 2
    assert applied["carried_identities"][0]["side"] in ("BID", "ASK")
    # The resync record itself says which kind it was, so a reader grouping a day's faults does
    # not have to join two streams to find out.
    resync = collector.sink.telemetry(C.RESYNC)[-1]
    assert resync["refresh_type"] == W.SOFT


def test_a_transition_is_published_exactly_once(live):
    """Four more samples after the refresh, and the outcome is still one record.

    `last_transition` stays on the tracker so the screen can show it, so it is read every second
    and has to be published by identity rather than by presence.
    """
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    for second in (3, 4, 5, 6):
        quiet_delta(collector, second=second, base_ms=base_ms, base_ns=base_ns)
        sample(collector, second=second, base_ms=base_ms, base_ns=base_ns)
    applied = [event for event in collector.sink.telemetry(C.WALL_CONTINUITY)
               if event["phase"] == "APPLIED"]
    assert len(applied) == 1 and applied[0]["refresh_type"] == W.SOFT


def test_the_carried_identity_list_is_bounded_like_every_other_buffer():
    assert W.TRANSITION_MAX_CARRIED == 2_000
    tracker = W.WallTracker()
    summary = {"carried_identities": [{"side": "BID"}] * (W.TRANSITION_MAX_CARRIED + 5),
               "wall_ended": 0, "carried_lost": 0, "carried_truncated": False}
    tracker._finish_transition(summary, {})
    assert summary["carried_truncated"] is True
    assert len(summary["carried_identities"]) == W.TRANSITION_MAX_CARRIED


# --- repeated coverage-edge refreshes ---------------------------------------------------------

def test_repeated_coverage_edge_refreshes_no_longer_cap_how_long_a_wall_can_look(live):
    """The defect V1.2 exists to fix, as a measurement rather than as a claim.

    Five refreshes 20 s apart. Before, each one reset every span to zero, so the longest span any
    wall could ever show was the gap between two refreshes. After, the proven life keeps running
    and the span crosses all five.
    """
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    spans = []
    for refresh in range(5):
        second = 2 + refresh * 20
        quiet_delta(collector, second=second, base_ms=base_ms, base_ns=base_ns)
        soft_refresh(collector, second=second, base_ms=base_ms, base_ns=base_ns)
        state = sample(collector, second=second + 1, base_ms=base_ms, base_ns=base_ns)
        row = rows(state)[("BID", BIG_BID)]
        spans.append((row["persistence_ms"], row["continuity_persistence_ms"]))
    # The candidate's own span is reset by every refresh, exactly as the contract says.
    assert [own for own, _ in spans] == [0, 0, 0, 0, 0]
    # The proven span is not.
    assert [carried for _, carried in spans] == [2_000, 22_000, 42_000, 62_000, 82_000]
    assert collector.wall.soft_refreshes == 5 and collector.wall.hard_transitions == 0


def test_a_soft_refresh_does_not_report_a_carry_it_renewed_as_a_loss(live):
    """`carried_lost` warns on screen, so on a healthy refresh it has to be zero.

    Counting every candidate that arrived carried as lost would fire the warning on every single
    SOFT refresh, which is how an operator learns to ignore the one that matters.
    """
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    state = sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    assert state["continuity"]["last_transition"]["carried_lost"] == 0

    # A second refresh: both walls arrive already carried, and both carry again.
    quiet_delta(collector, second=4, base_ms=base_ms, base_ns=base_ns)
    soft_refresh(collector, second=4, base_ms=base_ms, base_ns=base_ns,
                 payload=wall_snapshot())
    state = sample(collector, second=5, base_ms=base_ms, base_ns=base_ns)
    summary = state["continuity"]["last_transition"]
    assert summary["carried_entering"] == 2 and summary["wall_carried"] == 2
    assert summary["carried_lost"] == 0

    # Now one of them is gone from the refreshed snapshot, and that one *is* a loss.
    quiet_delta(collector, second=6, base_ms=base_ms, base_ns=base_ns)
    flat = wall_snapshot()
    for level in flat["bids"]:
        if level[0] == BIG_BID:
            level[1] = "0"
    soft_refresh(collector, second=6, base_ms=base_ms, base_ns=base_ns, payload=flat)
    state = sample(collector, second=7, base_ms=base_ms, base_ns=base_ns)
    summary = state["continuity"]["last_transition"]
    assert summary["carried_entering"] == 2
    assert summary["wall_carried"] == 1 and summary["wall_ended"] == 1
    assert summary["carried_lost"] == 1


def test_a_hard_resync_reports_every_carried_wall_as_lost(live):
    collector, base_ms, base_ns = live
    sample(collector, second=1, base_ms=base_ms, base_ns=base_ns)
    quiet_delta(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    soft_refresh(collector, second=2, base_ms=base_ms, base_ns=base_ns)
    sample(collector, second=3, base_ms=base_ms, base_ns=base_ns)
    assert collector.wall.carried == 2
    quiet_delta(collector, second=4, base_ms=base_ms, base_ns=base_ns)
    collector.on_depth_frame(depth_frame(first=9_000, last=9_010, previous=8_999),
                             receive_ms=base_ms + 4_500, mono_ns=base_ns + 45 * 10**8)
    summary = collector.wall.last_transition
    assert summary["refresh_type"] == W.HARD
    assert summary["carried_entering"] == 2 and summary["carried_lost"] == 2
    assert summary["wall_unknown"] == 2
