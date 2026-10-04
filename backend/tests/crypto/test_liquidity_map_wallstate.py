"""The resting candidate set, and the proof that it is whole.

The failure this file is really about: the journal stores wall candidates by transition, so a
candidate that opened before the reader's window is invisible, and the candidates that have
lasted longest are exactly the ones most worth seeing. An unverified reconstruction is therefore
not "approximately right", and these tests pin down when the code is allowed to claim COMPLETE.
"""
from __future__ import annotations

from decimal import Decimal

from app.crypto.liquidity_map import journal as J
from app.crypto.liquidity_map import wallstate as W

from tests.crypto.liquidity_map_fixtures import Handwriter, written_journal


def session_of(root):
    return J.latest_session(root)


def test_a_candidate_that_opened_and_never_ended_is_resting(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.wall(side="BID", price="84700", event="OPENED", status="ACTIVE", receive_ms=1_100)
    state = W.WallFollower().refresh(tmp_path, session_of(tmp_path), now_ms=2_000)
    assert sorted(state.resting) == [("BID", "84700")]
    assert state.coverage == "COMPLETE" and state.verified_by == W.VERIFIED_BY_STREAM_START


def test_a_candidate_that_ended_is_not_resting(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.wall(side="BID", price="84700", event="OPENED", status="ACTIVE", receive_ms=1_100)
    writer.wall(side="BID", price="84700", event="ENDED", status="ENDED", receive_ms=1_200)
    state = W.WallFollower().refresh(tmp_path, session_of(tmp_path), now_ms=2_000)
    assert state.resting == {} and state.coverage == "COMPLETE"


def test_an_interrupted_candidate_is_not_resting_either(tmp_path):
    """A resync or a loss of freshness closes continuity as UNKNOWN. It is still closed."""
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.wall(side="ASK", price="84800", event="OPENED", status="ACTIVE", receive_ms=1_100)
    writer.wall(side="ASK", price="84800", event="UNKNOWN", status="UNKNOWN", receive_ms=1_200)
    state = W.WallFollower().refresh(tmp_path, session_of(tmp_path), now_ms=2_000)
    assert state.resting == {}


def test_a_candidate_that_reopened_after_ending_is_resting_again(tmp_path):
    """Going backwards, the newest row for a key decides. A stale ENDED must not win."""
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.wall(side="BID", price="84700", event="OPENED", status="ACTIVE", receive_ms=1_100)
    writer.wall(side="BID", price="84700", event="ENDED", status="ENDED", receive_ms=1_200)
    writer.wall(side="BID", price="84700", event="OPENED", status="ACTIVE", receive_ms=1_300)
    state = W.WallFollower().refresh(tmp_path, session_of(tmp_path), now_ms=2_000)
    assert sorted(state.resting) == [("BID", "84700")]


def test_the_two_sides_are_separate_keys_at_the_same_price(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.wall(side="BID", price="84750", event="OPENED", status="ACTIVE", receive_ms=1_100)
    writer.wall(side="ASK", price="84750", event="OPENED", status="ACTIVE", receive_ms=1_100)
    writer.wall(side="BID", price="84750", event="ENDED", status="ENDED", receive_ms=1_200)
    state = W.WallFollower().refresh(tmp_path, session_of(tmp_path), now_ms=2_000)
    assert sorted(state.resting) == [("ASK", "84750")]


def test_no_wall_file_is_a_complete_empty_set_not_an_unknown_one(tmp_path):
    """A young session, or a book that never synchronized, has genuinely no candidate."""
    Handwriter(tmp_path).session_start()
    state = W.WallFollower().refresh(tmp_path, session_of(tmp_path), now_ms=2_000)
    assert state.count == 0 and state.coverage == "COMPLETE"
    assert state.missing_count == 0


# --- verification -----------------------------------------------------------------------------

def test_a_truncated_walk_without_an_authority_row_refuses_to_claim_completeness(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    for index in range(300):
        writer.wall(side="BID", price=str(84_700 - index), event="OPENED", status="ACTIVE",
                    receive_ms=1_100 + index)
    state = W.WallFollower(scan_budget_bytes=2_000).refresh(tmp_path, session_of(tmp_path),
                                                            now_ms=5_000)
    assert state.coverage == "PARTIAL"
    assert state.unverified_reason == W.NOT_VERIFIED_NO_AUTHORITY
    assert 0 < state.count < 300


def test_a_truncated_walk_whose_count_matches_the_collector_is_proven_complete(tmp_path):
    """`storage_stats.walls.active` is the collector's own dictionary size. The walk can only
    undercount, so equality is a proof rather than a coincidence."""
    writer = Handwriter(tmp_path)
    writer.session_start()
    for index in range(200):
        price = str(84_700 - index)
        writer.wall(side="BID", price=price, event="OPENED", status="ACTIVE",
                    receive_ms=1_100 + index)
        writer.wall(side="BID", price=price, event="ENDED", status="ENDED",
                    receive_ms=1_101 + index)
    writer.stats(active=0, receive_ms=4_000)
    writer.wall(side="ASK", price="84900", event="OPENED", status="ACTIVE", receive_ms=4_500)
    state = W.WallFollower(scan_budget_bytes=3_000).refresh(tmp_path, session_of(tmp_path),
                                                            now_ms=5_000)
    assert state.verified_by == W.VERIFIED_BY_AUTHORITY_COUNT
    assert state.coverage == "COMPLETE"
    assert state.reconstructed_at_authority == 0 and state.authority_active == 0
    # The candidate opened after the authority row is still in the live set.
    assert sorted(state.resting) == [("ASK", "84900")]


def test_a_truncated_walk_that_is_short_of_the_collector_reports_how_many_are_missing(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    for index in range(200):
        writer.wall(side="BID", price=str(84_700 - index), event="OPENED", status="ACTIVE",
                    receive_ms=1_100 + index)
    writer.stats(active=200, receive_ms=4_000)
    state = W.WallFollower(scan_budget_bytes=4_000).refresh(tmp_path, session_of(tmp_path),
                                                            now_ms=5_000)
    assert state.coverage == "PARTIAL"
    assert state.unverified_reason == W.NOT_VERIFIED_BUDGET
    assert state.missing_count is not None and state.missing_count > 0
    assert state.reconstructed_at_authority is not None
    assert state.reconstructed_at_authority + state.missing_count == 200


def test_the_authority_row_age_is_reported_so_the_count_is_not_read_as_current(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.wall(side="BID", price="84700", event="OPENED", status="ACTIVE", receive_ms=1_100)
    writer.stats(active=1, receive_ms=4_000)
    state = W.WallFollower().refresh(tmp_path, session_of(tmp_path), now_ms=64_000)
    assert state.authority_active == 1 and state.authority_age_ms == 60_000


# --- following --------------------------------------------------------------------------------

def test_following_applies_new_transitions_without_rereading_history(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    for index in range(50):
        writer.wall(side="BID", price=str(84_700 - index), event="OPENED", status="ACTIVE",
                    receive_ms=1_100 + index, open_file=True)
    follower = W.WallFollower()
    first = follower.refresh(tmp_path, session_of(tmp_path), now_ms=2_000)
    initial_bytes = first.scanned_bytes
    assert first.count == 50 and initial_bytes > 0

    writer.wall(side="BID", price="84700", event="ENDED", status="ENDED", receive_ms=3_000,
                open_file=True)
    writer.wall(side="ASK", price="84900", event="OPENED", status="ACTIVE", receive_ms=3_001,
                open_file=True)
    second = follower.refresh(tmp_path, session_of(tmp_path), now_ms=4_000)
    assert second.count == 50
    assert ("BID", "84700") not in second.resting and ("ASK", "84900") in second.resting
    assert second.verified_by == W.VERIFIED_BY_FOLLOWING
    # Following costs the new bytes, not the history again.
    assert second.scanned_bytes < initial_bytes / 10
    assert second.transitions_applied == 2


def test_a_new_session_resets_the_set_instead_of_inheriting_it(tmp_path):
    old = Handwriter(tmp_path, session_id="11111111-0000-0000-0000-000000000000")
    old.session_start(started_ms=1_000)
    old.wall(side="BID", price="84700", event="OPENED", status="ACTIVE", receive_ms=1_100)
    follower = W.WallFollower()
    assert follower.refresh(tmp_path, session_of(tmp_path), now_ms=2_000).count == 1

    new = Handwriter(tmp_path, session_id="22222222-0000-0000-0000-000000000000")
    new.session_start(started_ms=9_000)
    assert follower.refresh(tmp_path, session_of(tmp_path), now_ms=10_000).count == 0


def test_the_head_of_the_wall_stream_is_tracked_so_the_skew_can_be_published(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.wall(side="BID", price="84700", event="OPENED", status="ACTIVE", receive_ms=1_100)
    state = W.WallFollower().refresh(tmp_path, session_of(tmp_path), now_ms=2_000)
    assert state.last_receive_ms == 1_100


# --- the real writer --------------------------------------------------------------------------

def test_the_collector_s_own_journal_reconstructs_to_the_count_it_recorded(tmp_path):
    root = tmp_path / "journal"
    written_journal(root, samples=4, end_session=False, seal=False)
    state = W.WallFollower().refresh(root, session_of(root), now_ms=0)
    assert state.coverage == "COMPLETE"
    assert state.count == state.authority_active
    assert {side for side, _ in state.resting} == {"BID", "ASK"}


def test_a_finished_session_holds_no_resting_candidate(tmp_path):
    """Shutdown closes every candidate as UNKNOWN: a clean stop did not observe them ending."""
    root = tmp_path / "journal"
    written_journal(root, samples=4, end_session=True, seal=True)
    state = W.WallFollower().refresh(root, session_of(root), now_ms=0)
    assert state.resting == {} and state.coverage == "COMPLETE"


# --- the display filter -----------------------------------------------------------------------

def test_the_display_filter_is_notional_only_and_says_it_is_not_the_rule():
    """The multiple floor belongs to the frozen rule, so it is not also a movable knob here."""
    narrow = W.WallFilter(min_notional_usdt=Decimal("500000"))
    assert narrow.keeps({"notional_usdt": "600000"}) is True
    assert narrow.keeps({"notional_usdt": "400000"}) is False
    assert narrow.view()["is_display_filter_not_rule"] is True
    assert narrow.view()["applies_after"] == "lm-wall.v2"
    assert "min_multiple" not in narrow.view()


def test_the_filter_drops_a_wall_whose_notional_cannot_be_read():
    assert W.DEFAULT_WALL_FILTER.keeps({"notional_usdt": None}) is False
    assert W.DEFAULT_WALL_FILTER.keeps({"notional_usdt": "nonsense"}) is False


def test_the_default_filter_is_the_measured_one_and_sits_above_the_rule_floor():
    from app.crypto.liquidity_map import wallrule as R

    assert W.DEFAULT_WALL_FILTER.min_notional_usdt == Decimal("500000")
    # The rule decides what a wall is; the filter decides how much of it is drawn. Keeping the
    # filter above the rule's floor is what lets the response say how many walls exist beyond
    # the ones on screen.
    assert W.DEFAULT_WALL_FILTER.min_notional_usdt > R.MIN_NOTIONAL_USDT


# --- the authority stays current, and a PARTIAL set can become proven ---------------------------

def test_the_authority_row_is_re_read_on_every_poll(tmp_path):
    """Read once and left on screen, a count and an age stop moving while still reading current."""
    writer = Handwriter(tmp_path)
    writer.session_start()
    writer.wall(side="BID", price="84700", event="OPENED", status="ACTIVE", receive_ms=1_100,
                open_file=True)
    writer.stats(active=1, receive_ms=2_000)
    follower = W.WallFollower()
    first = follower.refresh(tmp_path, session_of(tmp_path), now_ms=3_000)
    assert first.authority_active == 1 and first.authority_age_ms == 1_000

    writer.wall(side="ASK", price="84900", event="OPENED", status="ACTIVE", receive_ms=4_000,
                open_file=True)
    writer.stats(active=2, receive_ms=5_000)
    second = follower.refresh(tmp_path, session_of(tmp_path), now_ms=6_000)
    assert second.authority_active == 2 and second.authority_age_ms == 1_000


def test_an_unproven_set_becomes_proven_once_every_missing_candidate_has_closed(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    written = [("BID", str(84_600 + index)) for index in range(120)]
    for index, (side, price) in enumerate(written):
        writer.wall(side=side, price=price, event="OPENED", status="ACTIVE",
                    receive_ms=1_000 + index, open_file=True)
    writer.stats(active=len(written), receive_ms=2_000)

    follower = W.WallFollower(scan_budget_bytes=20_000)
    state = follower.refresh(tmp_path, session_of(tmp_path), now_ms=3_000)
    assert state.coverage == "PARTIAL"
    missing = state.missing_count
    assert missing is not None and missing > 0

    # Each close of a candidate the scan never reached retires one of the missing ones.
    unseen = [key for key in written if key not in state.resting]
    assert len(unseen) == missing
    for index, (side, price) in enumerate(unseen):
        writer.wall(side=side, price=price, event="ENDED", status="ENDED",
                    receive_ms=4_000 + index, open_file=True)
    state = follower.refresh(tmp_path, session_of(tmp_path), now_ms=5_000)
    assert state.missing_retired == missing
    assert state.missing_count == 0
    assert state.coverage == "COMPLETE"
    assert state.verified_by == W.VERIFIED_BY_MISSING_RETIRED


def test_closing_a_candidate_the_follower_does_hold_does_not_retire_a_missing_one(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    for index in range(120):
        writer.wall(side="ASK", price=str(84_700 + index), event="OPENED", status="ACTIVE",
                    receive_ms=1_000 + index, open_file=True)
    writer.stats(active=120, receive_ms=2_000)
    follower = W.WallFollower(scan_budget_bytes=20_000)
    state = follower.refresh(tmp_path, session_of(tmp_path), now_ms=3_000)
    before = state.missing_count
    assert state.coverage == "PARTIAL" and before

    held = next(price for side, price in state.resting)
    writer.wall(side="ASK", price=held, event="ENDED", status="ENDED", receive_ms=4_000,
                open_file=True)
    state = follower.refresh(tmp_path, session_of(tmp_path), now_ms=5_000)
    assert state.missing_count == before and state.missing_retired == 0
    assert state.coverage == "PARTIAL"
