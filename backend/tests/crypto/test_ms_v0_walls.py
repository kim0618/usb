"""Wall candidate detection and lifecycle.

The persistence tests are the ones that matter for V1: `persistence_ms` is a *sampled span* and
these tests pin that it never pretends to be more. A resync or a loss of freshness ends a
candidate as UNKNOWN rather than ENDED, because an ending that was not observed is not an ending.
"""
from __future__ import annotations

from decimal import Decimal

from app.crypto.market_structure_v0 import book as B
from app.crypto.market_structure_v0 import walls as W
from app.crypto.market_structure_v0.contract import COMPLETE, PARTIAL

from tests.crypto.ms_v0_fixtures import S, snapshot, synced_book, wide_snapshot


def book_with_wall(*, size: str = "20", at: int = 4, levels: int = 13) -> B.DepthBook:
    """A book whose bid `at` levels down carries `size` while its neighbours carry 1."""
    payload = snapshot(levels=levels)
    payload["bids"][at][1] = size
    return synced_book(snapshot_payload=payload)


def tick(tracker: W.WallTracker, book: B.DepthBook, second: int) -> list[dict]:
    """Sample at `second`, with the book kept fresh as a live stream would keep it."""
    book.last_mono_ns = second * S
    book.last_receive_ms = 1_000 + second * 1_000
    return tracker.sample(book, at_ns=second * S, receive_ms=1_000 + second * 1_000)


def one(records: list[dict]) -> dict:
    assert len(records) == 1, [record["event"] for record in records]
    return records[0]


# --- detection --------------------------------------------------------------------------------

def test_a_level_three_times_its_neighbourhood_opens_a_candidate():
    tracker = W.WallTracker()
    record = one(tracker.sample(book_with_wall(), at_ns=0, receive_ms=1_000))
    assert record["event"] == W.OPENED and record["status"] == W.ACTIVE
    assert record["side"] == "BID" and record["price"] == "84710"
    assert record["qty"] == "20" and record["local_average"] == "1"
    assert record["multiple"] == "20"
    assert record["bin_rule"] == "EXACT_PRICE" and record["bin"] == record["price"]


def test_a_level_below_the_multiple_is_not_a_candidate():
    assert tracker_samples(book_with_wall(size="2.9")) == []
    assert tracker_samples(book_with_wall(size="3")) != []


def tracker_samples(book: B.DepthBook) -> list[dict]:
    return W.WallTracker().sample(book, at_ns=0, receive_ms=1_000)


def test_the_comparison_set_excludes_the_level_itself():
    """With neighbours of 1 and a level of 3, including itself would give a mean above 1."""
    record = one(tracker_samples(book_with_wall(size="3")))
    assert record["local_average"] == "1" and record["multiple"] == "3"


def test_fewer_than_three_neighbours_yields_no_candidate():
    """A level with too small a neighbourhood has no local average to be a multiple of."""
    book = B.DepthBook()
    book.apply_snapshot({"lastUpdateId": 1, "E": 1_000,
                         "bids": [["84750", "1"], ["84740", "99"]],
                         "asks": [["84760", "1"], ["84770", "1"]]}, 1_000, 0)
    assert tracker_samples(book) == []


def test_neighbours_are_occupied_levels_and_not_adjacent_ticks():
    """A sparse book's neighbourhood is its nearest resting levels, gaps and all."""
    bids = [["84750", "1"], ["84700", "1"], ["84650", "1"], ["84600", "30"], ["84550", "1"],
            ["84500", "1"], ["84450", "1"]]
    book = B.DepthBook()
    book.apply_snapshot({"lastUpdateId": 1, "E": 1_000, "bids": bids,
                         "asks": [["84760", "1"], ["84770", "1"], ["84780", "1"]]}, 1_000, 0)
    record = one([r for r in tracker_samples(book) if r["side"] == "BID"])
    assert record["price"] == "84600" and record["neighbours"] == 6


def test_candidates_are_found_on_both_sides():
    payload = snapshot()
    payload["bids"][3][1] = "20"
    payload["asks"][3][1] = "20"
    records = tracker_samples(synced_book(snapshot_payload=payload))
    assert sorted(record["side"] for record in records) == ["ASK", "BID"]


def test_a_level_outside_one_percent_of_mid_is_not_a_candidate():
    payload = wide_snapshot()
    payload["bids"][100][1] = "99"  # 1000 ticks below, about 1.18% from mid
    records = tracker_samples(synced_book(snapshot_payload=payload))
    assert all(record["price"] != str(Decimal("84750") - Decimal("1000")) for record in records)


def test_notional_accompanies_the_quantity():
    record = one(tracker_samples(book_with_wall()))
    assert record["notional"] == str(Decimal("84710") * 20)


# --- lifecycle --------------------------------------------------------------------------------

def test_persistence_accumulates_across_samples_and_is_labelled_as_sampled():
    """With `emit_updates`, every sample of a live candidate is written."""
    tracker = W.WallTracker(emit_updates=True)
    book = book_with_wall()
    tracker.sample(book, at_ns=0, receive_ms=1_000)
    tracker.sample(book, at_ns=1 * S, receive_ms=2_000)
    record = one(tracker.sample(book, at_ns=2 * S, receive_ms=3_000))
    assert record["event"] == W.UPDATED
    assert record["persistence_ms"] == 2_000 and record["samples"] == 3
    assert record["first_seen_ms"] == 1_000 and record["last_seen_ms"] == 3_000
    # V0 never claims the order was the same order throughout.
    assert record["persistence_is_sampled_span"] is True
    assert record["order_identity_proven"] is False


def test_by_default_only_the_open_and_the_end_of_a_candidate_are_written():
    """Measured: a row per candidate per sample made walls 88.5% of all bytes."""
    tracker = W.WallTracker()
    book = book_with_wall()
    assert one(tick(tracker, book, 0))["event"] == W.OPENED
    for second in range(1, 5):
        assert tick(tracker, book, second) == []
    # Still observed every second, so the span is intact when it ends.
    book.bids[Decimal("84710")] = Decimal("1")
    record = one(tick(tracker, book, 5))
    assert record["event"] == W.ENDED
    assert record["samples"] == 5 and record["persistence_ms"] == 4_000
    assert record["last_seen_ms"] == 5_000


def test_an_ended_candidate_summarizes_the_trajectory_it_did_not_write_row_by_row():
    tracker = W.WallTracker()
    book = book_with_wall(size="20")
    tick(tracker, book, 0)
    book.bids[Decimal("84710")] = Decimal("40")
    tick(tracker, book, 1)
    book.bids[Decimal("84710")] = Decimal("9")
    tick(tracker, book, 2)
    book.bids[Decimal("84710")] = Decimal("1")
    record = one(tick(tracker, book, 3))
    assert record["event"] == W.ENDED
    assert record["current_size"] == "9"       # the last sample at which it still qualified
    assert record["max_size"] == "40" and record["min_size"] == "9"
    assert record["max_multiple"] == "40"


def test_a_candidate_that_stops_qualifying_ends():
    tracker = W.WallTracker()
    book = book_with_wall()
    tracker.sample(book, at_ns=0, receive_ms=1_000)
    book.bids[Decimal("84710")] = Decimal("1")
    record = one(tracker.sample(book, at_ns=1 * S, receive_ms=2_000))
    assert record["event"] == W.ENDED and record["status"] == W.ENDED
    assert record["persistence_ms"] == 0
    assert {key: tracker.counters()[key]
            for key in ("active", "opened", "ended", "interrupted")} == {
        "active": 0, "opened": 1, "ended": 1, "interrupted": 0}


def test_a_candidate_whose_price_disappears_ends():
    tracker = W.WallTracker()
    book = book_with_wall()
    tracker.sample(book, at_ns=0, receive_ms=1_000)
    del book.bids[Decimal("84710")]
    assert one(tracker.sample(book, at_ns=1 * S, receive_ms=2_000))["event"] == W.ENDED


def test_current_size_tracks_the_level_while_it_still_qualifies():
    tracker = W.WallTracker(emit_updates=True)
    book = book_with_wall(size="20")
    tracker.sample(book, at_ns=0, receive_ms=1_000)
    book.bids[Decimal("84710")] = Decimal("10")
    record = one(tracker.sample(book, at_ns=1 * S, receive_ms=2_000))
    assert record["event"] == W.UPDATED and record["current_size"] == "10"
    assert record["multiple"] == "10"


def test_a_resync_ends_continuity_as_unknown_and_not_as_ended():
    tracker = W.WallTracker()
    book = book_with_wall()
    tracker.sample(book, at_ns=0, receive_ms=1_000)
    payload = snapshot(last_update_id=2_000)
    payload["bids"][4][1] = "20"
    book.apply_snapshot(payload, 2_000, 1 * S)
    records = tracker.sample(book, at_ns=1 * S, receive_ms=2_000)
    assert [record["event"] for record in records] == [W.INTERRUPTED, W.OPENED]
    # The reopened candidate belongs to the new generation and starts its persistence over.
    assert records[0]["generation"] == 1 and records[1]["generation"] == 2
    assert records[1]["persistence_ms"] == 0
    assert tracker.counters()["interrupted"] == 1


def test_losing_freshness_ends_continuity_as_unknown():
    tracker = W.WallTracker()
    book = book_with_wall()
    tracker.sample(book, at_ns=0, receive_ms=1_000)
    record = one(tracker.sample(book, at_ns=10 * S, receive_ms=11_000))
    assert record["event"] == W.INTERRUPTED and record["status"] == W.INTERRUPTED


def test_an_unsynced_book_produces_no_candidates():
    tracker = W.WallTracker()
    book = book_with_wall()
    tracker.sample(book, at_ns=0, receive_ms=1_000)
    book.invalidate(B.GAP_PU_MISMATCH)
    assert one(tracker.sample(book, at_ns=1 * S, receive_ms=2_000))["event"] == W.INTERRUPTED
    assert tracker.sample(book, at_ns=2 * S, receive_ms=3_000) == []


def test_shutdown_closes_open_candidates_as_unknown():
    tracker = W.WallTracker()
    tracker.sample(book_with_wall(), at_ns=0, receive_ms=1_000)
    assert one(tracker.shutdown())["status"] == W.INTERRUPTED
    assert tracker.candidates == {}


# --- coverage ---------------------------------------------------------------------------------

def test_a_candidate_records_partial_coverage_when_the_band_exceeds_the_snapshot_bounds():
    """The measured live case: +-1% runs past a `limit=1000` snapshot, so the neighbourhood is
    only partly known and the candidate says so."""
    assert one(tracker_samples(book_with_wall()))["coverage"] == PARTIAL


def test_a_candidate_records_complete_coverage_when_the_band_fits_inside_the_bounds():
    payload = wide_snapshot()
    payload["bids"][4][1] = "20"
    assert one(tracker_samples(synced_book(snapshot_payload=payload)))["coverage"] == COMPLETE


def test_v0_carries_no_spoofing_or_absorption_vocabulary():
    record = one(tracker_samples(book_with_wall()))
    text = " ".join(f"{key} {value}" for key, value in record.items()).lower()
    for banned in ("spoof", "absorb", "absorption", "iceberg", "fake", "manipul"):
        assert banned not in text
