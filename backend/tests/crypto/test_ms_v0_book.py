"""Binance USDⓈ-M local book synchronization.

These tests exist because the two plausible ways to write this are both wrong in ways that do not
raise: using Bybit's `u == previous u + 1`, and using spot's `U <= lastUpdateId + 1 <= u`. Each
test that pins a futures-specific rule says which wrong rule it is excluding.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.crypto.market_structure_v0 import book as B

from tests.crypto.ms_v0_fixtures import depth_frame, snapshot, synced_book


def apply(book: B.DepthBook, message: dict, receive_ms: int = 6_000, mono_ns: int = 0) -> str:
    return book.apply_delta(B.DepthFrame.parse(message), receive_ms, mono_ns)


# --- snapshot + delta sync --------------------------------------------------------------------

def test_snapshot_establishes_levels_bounds_and_mid():
    book = synced_book()
    assert book.state == B.SYNCED
    assert book.mid() == Decimal("84755")
    assert book.known_low == Decimal("84630") and book.known_high == Decimal("84880")
    assert book.snapshot_update_id == 1000 and book.last_update_id == 1000
    assert book.generation == 1


def test_first_delta_uses_the_futures_rule_not_the_spot_rule():
    """`U <= lastUpdateId <= u`. The spot form would reject a frame that must be applied."""
    book = synced_book()
    # U < lastUpdateId < u, and U is far from pu+1: the only frame shape futures guarantees.
    assert apply(book, depth_frame(first=900, last=1100, previous=850,
                                   bids=[["84750", "7"]])) == B.APPLIED
    assert book.bids[Decimal("84750")] == Decimal("7")
    assert book.last_update_id == 1100


def test_first_delta_spanning_the_snapshot_exactly_at_the_edges_is_applied():
    book = synced_book()
    assert apply(book, depth_frame(first=1000, last=1000, previous=999)) == B.APPLIED


def test_first_delta_that_starts_after_the_snapshot_is_a_gap():
    book = synced_book()
    assert apply(book, depth_frame(first=1001, last=1100, previous=1000)) == B.GAP
    assert book.state == B.UNSYNCED
    assert book.last_invalidation == B.GAP_FIRST_DELTA


def test_frames_older_than_the_snapshot_are_discarded_not_treated_as_faults():
    book = synced_book()
    assert apply(book, depth_frame(first=900, last=999, previous=890)) == B.DISCARDED_OLD
    assert book.state == B.SYNCED and book.gaps == 0


def test_continuity_is_pu_only_and_never_the_U_minus_pu_arithmetic():
    """Measured live: `U - pu` took values 51, 55, 66, 80 and 119. Asserting it would resync forever."""
    book = synced_book()
    apply(book, depth_frame(first=900, last=1100, previous=850))
    assert apply(book, depth_frame(first=1219, last=1300, previous=1100)) == B.APPLIED
    assert apply(book, depth_frame(first=1355, last=1400, previous=1300)) == B.APPLIED
    assert book.gaps == 0 and book.last_update_id == 1400


def test_pu_mismatch_is_a_gap_and_the_book_is_dropped_whole():
    book = synced_book()
    apply(book, depth_frame(first=900, last=1100, previous=850, bids=[["84750", "9"]]))
    assert apply(book, depth_frame(first=1200, last=1300, previous=1099)) == B.GAP
    assert book.state == B.UNSYNCED
    assert book.last_invalidation == B.GAP_PU_MISMATCH
    # Never stitch across a gap: levels, bounds and ids all go.
    assert book.bids == {} and book.asks == {}
    assert book.known_low is None and book.last_update_id is None


def test_a_frame_without_pu_cannot_prove_continuity():
    book = synced_book()
    apply(book, depth_frame(first=900, last=1100, previous=850))
    assert apply(book, depth_frame(first=1101, last=1200, previous=None)) == B.GAP
    assert book.last_invalidation == B.GAP_PU_MISMATCH


def test_redelivered_frame_is_a_duplicate_and_not_a_gap():
    book = synced_book()
    apply(book, depth_frame(first=900, last=1100, previous=850))
    assert apply(book, depth_frame(first=900, last=1100, previous=850)) == B.DISCARDED_DUPLICATE
    assert book.state == B.SYNCED and book.gaps == 0 and book.discarded_duplicate == 1


def test_a_frame_that_chains_on_but_does_not_advance_is_a_gap():
    """`pu` matches so it claims to follow us, yet `u` does not move: self-contradictory."""
    book = synced_book()
    apply(book, depth_frame(first=900, last=1100, previous=850))
    assert apply(book, depth_frame(first=1050, last=1100, previous=1100)) == B.GAP
    assert book.last_invalidation == B.GAP_NON_INCREASING


def test_u_below_U_is_rejected_as_malformed():
    book = synced_book()
    assert apply(book, depth_frame(first=1200, last=1100, previous=1000)) == B.GAP
    assert book.last_invalidation == B.GAP_MALFORMED_IDS


# --- level semantics --------------------------------------------------------------------------

def test_absolute_quantities_replace_and_zero_deletes():
    book = synced_book()
    apply(book, depth_frame(first=900, last=1100, previous=850,
                            bids=[["84750", "3"], ["84740", "0"]]))
    assert book.bids[Decimal("84750")] == Decimal("3")
    assert Decimal("84740") not in book.bids


def test_zero_for_a_level_we_never_held_is_accepted_silently():
    book = synced_book()
    before = len(book.bids)
    assert apply(book, depth_frame(first=900, last=1100, previous=850,
                                   bids=[["84625", "0"]])) == B.APPLIED
    assert len(book.bids) == before


def test_levels_outside_the_known_interval_are_not_retained():
    """Seeing a level change beyond the snapshot is not evidence of knowing that region."""
    book = synced_book()
    apply(book, depth_frame(first=900, last=1100, previous=850,
                            bids=[["84000", "50"]], asks=[["85500", "50"]]))
    assert Decimal("84000") not in book.bids
    assert Decimal("85500") not in book.asks


def test_a_crossed_book_invalidates_rather_than_reporting_a_negative_spread():
    book = synced_book()
    apply(book, depth_frame(first=900, last=1100, previous=850, bids=[["84770", "1"]]))
    assert book.state == B.UNSYNCED
    assert book.last_invalidation == B.CROSSED_BOOK


def test_level_overflow_invalidates_instead_of_growing_without_bound(monkeypatch):
    monkeypatch.setattr(B, "MAX_BOOK_LEVELS", 10)
    book = synced_book()
    assert apply(book, depth_frame(first=900, last=1100, previous=850,
                                   bids=[["84745", "1"], ["84735", "1"]])) == B.GAP
    assert book.last_invalidation == B.LEVEL_OVERFLOW


# --- buffering and resync ---------------------------------------------------------------------

def test_frames_buffered_before_a_snapshot_are_replayed_behind_it():
    book = B.DepthBook()
    book.on_connect("c1")
    for message in (depth_frame(first=900, last=1100, previous=850, bids=[["84750", "4"]]),
                    depth_frame(first=1101, last=1200, previous=1100, bids=[["84740", "5"]])):
        assert book.buffer_frame(B.DepthFrame.parse(message), 1, 1, limit=10) == B.BUFFERED
    assert book.apply_snapshot(snapshot(), 2, 2) == B.SYNCED
    assert book.bids[Decimal("84750")] == Decimal("4")
    assert book.bids[Decimal("84740")] == Decimal("5")
    assert book.last_update_id == 1200
    assert book.buffer == []


def test_a_gap_inside_the_buffered_replay_leaves_the_book_unsynced():
    book = B.DepthBook()
    book.on_connect("c1")
    for message in (depth_frame(first=900, last=1100, previous=850),
                    depth_frame(first=1300, last=1400, previous=1250)):
        book.buffer_frame(B.DepthFrame.parse(message), 1, 1, limit=10)
    assert book.apply_snapshot(snapshot(), 2, 2) == B.UNSYNCED
    assert book.last_invalidation == B.GAP_PU_MISMATCH


def test_buffer_overflow_demands_a_new_snapshot_instead_of_dropping_the_oldest_frame():
    book = B.DepthBook()
    book.on_connect("c1")
    frame = B.DepthFrame.parse(depth_frame(first=1, last=2, previous=0))
    assert book.buffer_frame(frame, 1, 1, limit=1) == B.BUFFERED
    assert book.buffer_frame(frame, 1, 1, limit=1) == B.GAP
    assert book.buffer == [] and book.last_invalidation == B.QUEUE_OVERFLOW


def test_reconnect_invalidates_and_bumps_nothing_until_a_snapshot_arrives():
    book = synced_book()
    book.on_connect("c2")
    assert book.state == B.UNSYNCED and book.last_invalidation == B.RECONNECT
    assert book.generation == 1
    book.apply_snapshot(snapshot(last_update_id=2000), 3, 3)
    assert book.generation == 2 and book.resyncs == 2


def test_an_empty_or_malformed_snapshot_is_refused():
    book = B.DepthBook()
    assert book.apply_snapshot({"lastUpdateId": 1, "bids": [], "asks": []}, 1, 1) == B.UNSYNCED
    assert book.last_invalidation == B.SNAPSHOT_REJECTED
    assert book.apply_snapshot({"bids": [["1", "1"]]}, 1, 1) == B.UNSYNCED
    assert book.snapshots_rejected == 2


def test_a_mid_outside_the_known_interval_is_not_a_usable_price():
    book = synced_book()
    book.known_low = Decimal("84760")
    assert book.mid() is None


def test_age_and_lag_are_reported_separately():
    book = synced_book(receive_ms=5_000, mono_ns=0)
    assert book.age_ms(3 * 10**9) == 3_000
    assert book.lag_ms() == 4_000  # receive 5000 - E 1000


@pytest.mark.parametrize("missing", ["U", "u", "b", "a"])
def test_a_frame_missing_a_required_field_raises_rather_than_being_guessed(missing):
    message = depth_frame(first=1, last=2, previous=0)
    del message[missing]
    with pytest.raises((KeyError, TypeError, ValueError)):
        B.DepthFrame.parse(message)
