"""The frozen continuity rule as the viewer applies it, and what it refuses to do.

The rule's whole job is to answer one question - which `first_seen` a wall's observed span is
measured from - and the dangerous answers are all on the permissive side. So the tests are
mostly refusals:

* a wall whose bin still holds a wall but whose members are all new does **not** carry, because
  matching on `(side, bin)` alone would claim identity for an order never seen resting,
* a candidate the collector did not positively mark carried does **not** carry, including every
  candidate from a collector that publishes no ledger at all,
* a carried span can only ever **extend** R4's input, so the rule cannot drop a wall that
  `lm-wall.v2` would have kept.

The one positive case is the defect V1.2 exists to fix, and it is measured here as a
before-and-after on the same candidate: identical in every field except the ledger, refused by
R4 without it and selected with it.
"""
from __future__ import annotations

from decimal import Decimal

from app.crypto.liquidity_map import continuity as CN
from app.crypto.liquidity_map import view as V
from app.crypto.liquidity_map import wallrule as R
from app.crypto.liquidity_map.wallstate import DEFAULT_WALL_FILTER, WallSet
from app.crypto.market_structure_v0 import walls as W

MID = Decimal("85000")
NOW = 600_000


def candidate(side="ASK", price="85100", notional="1000000", multiple="9",
              first_seen_ms=NOW - 2_000, persistence_ms=0, coverage="COMPLETE", **extra):
    """A candidate that passes R1, R2 and R3 and fails R4 on its own span of 2 s."""
    payload = {"side": side, "price": price, "qty": "11.7", "notional": notional,
               "multiple": multiple, "local_average": "1", "neighbours": 10,
               "first_seen_ms": first_seen_ms, "persistence_ms": persistence_ms,
               "samples": 2, "coverage": coverage, "generation": 2}
    payload.update(extra)
    return payload


def carried(payload, *, origin_ms=NOW - 300_000, refreshes=1, samples=300):
    """The same candidate with the collector's carry proof attached."""
    payload.update({
        "continuity_status": W.CARRY_CARRIED,
        "continuity_first_seen_ms": origin_ms,
        "continuity_persistence_ms": NOW - origin_ms,
        "continuity_samples": samples,
        "continuity_refreshes": refreshes,
        "continuity_origin_generation": 1,
        "continuity_proof": W.CARRY_PROOF,
    })
    return payload


def new(payload):
    """And with the ledger present but saying there is nothing to carry."""
    payload.update({
        "continuity_status": W.CARRY_NEW, "continuity_first_seen_ms": payload["first_seen_ms"],
        "continuity_persistence_ms": 0, "continuity_samples": payload["samples"],
        "continuity_refreshes": 0, "continuity_origin_generation": 2,
        "continuity_proof": None,
    })
    return payload


def select(rows, side="ASK", **kwargs):
    """The viewer's own call: both halves of the continuity rule injected, as `view.py` does."""
    return R.select(rows, side=side, mid=MID, latest_sample_ms=NOW,
                    carried_span=CN.carried_span_ms, wall_continuity=CN.wall_continuity,
                    **kwargs)


# --- the rule is the document -----------------------------------------------------------------

def test_the_frozen_document_is_present_and_its_recorded_hash_still_agrees():
    identity = CN.rule_identity()
    assert identity["status"] == "PRESENT"
    assert identity["sha256_agrees"] is True, "the frozen rule changed without a new version"
    assert identity["rule_version"] == "lm-continuity.v5"
    assert len(identity["rule_sha256"]) == 64


def test_the_document_states_the_gates_the_code_requires():
    text = " ".join(CN.rule_path().read_text(encoding="utf-8").split())
    for gate in ("S1 VOLUNTARY", "S2 CONTINUITY", "S3 NEWER", "S4 BOUNDED WINDOW", "S5 OVERLAP"):
        assert gate in text, gate
    assert "at most **300 ms**" in text
    assert W.SOFT_WINDOW_MAX_MS == 300
    # The ceiling is below the sampling granularity, which is the bound that makes the argument
    # work, and the document says which measurement put it where it is.
    assert W.SOFT_WINDOW_MAX_MS < 1_000
    assert "87, 99, 102, 119 and 143 ms" in text
    assert list(W.SOFT_GATES) == ["VOLUNTARY", "CONTINUITY", "NEWER", "BOUNDED_WINDOW", "OVERLAP"]


def test_the_document_records_what_each_version_bump_changed():
    """A version that moved without saying what moved is a silent edit with extra steps."""
    text = " ".join(CN.rule_path().read_text(encoding="utf-8").split())
    assert "Supersedes `lm-continuity.v4`" in text
    assert "0ed46edcc0a58957b99cc14bf5b8bba92a4111eb94026b7353311042e58327b3" in text
    assert "9637ef1be41e9eb679eb50634990801b28677fc5122bcca35ba956bd4d17231d" in text
    assert "596339b66cc170b4bd1550b93e44f63e35f81c13e28f833dc9a446e67b691576" in text
    assert "d68a26ce2a170d5549f5e8bec5db02b8fc91d7bfd489f31d97e39653a0148656" in text
    assert "S4 BOUNDED WINDOW: 1000 ms to 300 ms" in text
    assert "S3 NEWER became S3 CHAIN" in text
    assert "S4 BOUNDED WINDOW gains one exemption" in text
    assert "Snapshot request ownership" in text
    assert "The coverage-edge floor is 10 s, not 300 s" in text
    assert ("Unchanged in v2" in text and "Unchanged in v3" in text
            and "Unchanged in v4" in text and "Unchanged in v5" in text)


def test_the_document_states_the_staged_install_and_what_it_preserves():
    text = " ".join(CN.rule_path().read_text(encoding="utf-8").split())
    assert "the same `last_update_id`" in text
    assert "immediate successor" in text and "straddles" in text
    assert "that window contains nothing at all" in text
    assert "A staged refresh that cannot reach S3 is abandoned" in text
    view = CN.rule_view()
    assert view["install"] == "STAGED_BUFFER_AND_REPLAY_ATOMIC_SWAP"
    assert view["chain_gate"] == "SWAP_ONLY_AT_AN_IDENTICAL_UPDATE_ID"
    # The things v3 was forbidden to move.
    assert view["hard_carries_nothing"] is True
    assert view["soft_window_max_ms"] == 300
    assert view["changes_v2_thresholds"] is False


def test_the_document_refuses_a_carry_rebuilt_from_the_journal():
    text = " ".join(CN.rule_path().read_text(encoding="utf-8").split())
    assert "A set reconstructed from the journal's `wall` transitions carries nothing" in text
    assert "deliberately not done" in text


def test_a_journal_reconstruction_carries_nothing_at_all():
    """The decision, as behaviour rather than as a sentence.

    A journal-reconstructed set is built from `wall` payloads, which never carry the ledger, so
    every wall is NEW and says which of the two "not carried" cases it is in.
    """
    from app.crypto.market_structure_v0.walls import Candidate
    from decimal import Decimal as D
    opened = Candidate(side="ASK", price=D("85100"), generation=2, first_seen_ms=NOW - 300_000,
                       first_seen_ns=0, last_seen_ms=NOW, last_seen_ns=0,
                       current_size=D("11.7"), local_average=D("1"), multiple=D("9"),
                       neighbours=10)
    row = opened.payload(status="ACTIVE", event="OPENED")
    assert not any(key.startswith("continuity_") for key in row)
    assert CN.member_status(row) == W.CARRY_NEW
    assert CN.carried_span_ms(row, NOW) is None
    row["notional"] = "1000000"
    wall = select([row]).walls[0]
    assert wall["continuity_status"] == W.CARRY_NEW
    assert wall["not_carried_reason"] == CN.NOT_CARRIED_NO_LEDGER
    assert wall["carried_persistence_ms"] is None
    assert wall["persistence_source"] == CN.SOURCE_OWN


def test_the_document_states_that_it_moves_no_v2_threshold_and_no_contract():
    text = " ".join(CN.rule_path().read_text(encoding="utf-8").split())
    assert "R1 to R5 and all five thresholds are unchanged" in text
    assert "neither is changed by this one" in text
    assert "It can only ever *extend* a span" in text
    view = CN.rule_view()
    assert view["changes_v2_thresholds"] is False
    assert view["changes_data_contract"] is False
    assert view["can_only_extend_a_span"] is True
    assert view["hard_carries_nothing"] is True


def test_the_v2_thresholds_are_exactly_what_they_were():
    """V1.2 is only allowed to change R4's input. A moved threshold would be a new rule."""
    assert R.MIN_NOTIONAL_USDT == Decimal("250000")
    assert R.MIN_MULTIPLE == Decimal("5")
    assert R.MIN_DISTANCE_BPS == Decimal("1.0")
    assert R.MIN_PERSISTENCE_MS == 10_000
    assert R.BIN_WIDTH_USDT == Decimal("5.0") and R.BIN_WIDTH_TICKS == 50
    assert R.rule_identity()["sha256_agrees"] is True
    assert DEFAULT_WALL_FILTER.min_notional_usdt == Decimal("500000")


def test_the_two_modules_use_one_vocabulary_rather_than_two():
    assert (R.SOURCE_OWN, R.SOURCE_CARRIED) == (CN.SOURCE_OWN, CN.SOURCE_CARRIED)
    assert CN.CARRY_CARRIED == W.CARRY_CARRIED and CN.CARRY_NEW == W.CARRY_NEW
    assert (CN.HARD, CN.SOFT) == (W.HARD, W.SOFT)


def test_a_missing_document_is_reported_rather_than_raised(tmp_path):
    identity = CN.rule_identity(tmp_path / "gone.md")
    assert identity["status"] == "MISSING" and identity["rule_sha256"] is None


# --- persistence before and after -------------------------------------------------------------

def test_the_same_candidate_is_refused_without_the_ledger_and_selected_with_it():
    """The defect and the fix, on one candidate that differs only in the carry proof.

    Two seconds after a refresh the candidate's own span is 2,000 ms, under R4's 10,000, so the
    wall is gone from the screen for the next eight seconds even though nothing about the level
    changed. With the proof, R4 sees the span the level was actually observed for.
    """
    before = select([new(candidate())])
    assert before.walls == []
    assert before.rejected[R.REJECT_PERSISTENCE] == 1

    after = select([carried(candidate())])
    assert len(after.walls) == 1
    wall = after.walls[0]
    assert wall["observed_persistence_ms"] == 300_000
    assert wall["own_persistence_ms"] == 2_000
    assert wall["carried_persistence_ms"] == 300_000
    assert wall["persistence_source"] == CN.SOURCE_CARRIED
    assert wall["continuity_status"] == W.CARRY_CARRIED
    assert wall["continuity_first_seen_ms"] == NOW - 300_000
    assert wall["continuity_refreshes"] == 1
    # And the claim is not strengthened by any of it.
    assert wall["order_identity_proven"] is False
    assert wall["persistence_is_sampled_span"] is True


def test_a_wall_publishes_both_spans_so_a_carry_can_never_be_shown_without_saying_so():
    wall = select([carried(candidate())]).walls[0]
    assert {"observed_persistence_ms", "own_persistence_ms", "carried_persistence_ms",
            "persistence_source", "continuity_status"} <= set(wall)
    own = select([new(candidate(first_seen_ms=NOW - 40_000))]).walls[0]
    assert own["persistence_source"] == CN.SOURCE_OWN
    assert own["carried_persistence_ms"] is None
    assert own["continuity_status"] == W.CARRY_NEW
    assert own["not_carried_reason"] == CN.NOT_CARRIED_NEW


def test_a_carried_span_can_only_extend_the_r4_input_never_shorten_it():
    """A ledger that somehow reported a shorter span must not be able to remove a wall."""
    payload = carried(candidate(first_seen_ms=NOW - 90_000), origin_ms=NOW - 20_000)
    wall = select([payload]).walls[0]
    assert wall["own_persistence_ms"] == 90_000
    assert wall["carried_persistence_ms"] == 20_000
    assert wall["observed_persistence_ms"] == 90_000
    assert wall["persistence_source"] == CN.SOURCE_OWN


def test_a_carry_cannot_rescue_a_candidate_that_fails_any_other_rule():
    """R1, R2, R3 and R5 take no input from this rule."""
    assert select([carried(candidate(notional="100000"))]).rejected[R.REJECT_NOTIONAL] == 1
    assert select([carried(candidate(multiple="2"))]).rejected[R.REJECT_MULTIPLE] == 1
    assert select([carried(candidate(price="85000.05"))]).rejected[R.REJECT_DISTANCE] == 1


# --- wall identity at the bin -----------------------------------------------------------------

def test_a_bin_carries_when_one_of_its_members_is_carried():
    """Two members in the 85100.0 bin; only the larger one was proven."""
    lead = carried(candidate(price="85100", notional="1000000"))
    other = new(candidate(price="85102", notional="600000", first_seen_ms=NOW - 40_000))
    selection = select([lead, other])
    assert len(selection.walls) == 1
    wall = selection.walls[0]
    assert wall["bin_members"] == 2
    assert wall["continuity_status"] == W.CARRY_CARRIED
    assert wall["carried_members"] == 1
    assert wall["continuity_first_seen_ms"] == NOW - 300_000


def test_a_bin_whose_members_are_all_new_does_not_carry():
    """Bin-only matching is the rejected design: the bin is occupied, the identity is not."""
    members = [new(candidate(price="85100", notional="1000000", first_seen_ms=NOW - 40_000)),
               new(candidate(price="85103", notional="700000", first_seen_ms=NOW - 40_000))]
    wall = select(members).walls[0]
    assert wall["bin_members"] == 2
    assert wall["continuity_status"] == W.CARRY_NEW
    assert wall["carried_members"] == 0
    assert wall["continuity_first_seen_ms"] is None
    assert wall["continuity_proof"] is None


def test_the_carried_origin_is_the_earliest_among_the_members_that_were_proven():
    members = [carried(candidate(price="85100", notional="1000000"), origin_ms=NOW - 120_000),
               carried(candidate(price="85104", notional="900000"), origin_ms=NOW - 500_000)]
    wall = select(members).walls[0]
    assert wall["carried_members"] == 2
    assert wall["continuity_first_seen_ms"] == NOW - 500_000
    assert wall["carried_persistence_ms"] == 500_000


def test_a_carried_member_is_in_the_same_bin_by_construction():
    """The collector certifies an exact price, so `(side, bin)` cannot have moved under it."""
    payload = carried(candidate(price="85102.4"))
    assert R.price_bin(Decimal("85102.4")) == Decimal("85100.0")
    wall = select([payload]).walls[0]
    assert wall["bin_low"] == "85100" and wall["continuity_status"] == W.CARRY_CARRIED


def test_the_lead_member_is_still_the_largest_notional_not_the_longest_carried():
    """R5's representative is part of the frozen V2 rule and this rule does not touch it."""
    big_new = new(candidate(price="85101", notional="2000000", first_seen_ms=NOW - 40_000))
    small_carried = carried(candidate(price="85103", notional="600000"))
    wall = select([big_new, small_carried]).walls[0]
    assert wall["notional_usdt"] == "2000000"
    assert wall["continuity_status"] == W.CARRY_CARRIED


# --- nothing positive means nothing carried ---------------------------------------------------

def test_a_candidate_with_no_ledger_at_all_is_treated_as_new():
    """A `v1-1` checkpoint, or any journal reconstruction. V1.1 behaviour, exactly."""
    payload = candidate(first_seen_ms=NOW - 40_000)
    assert "continuity_status" not in payload
    assert CN.member_status(payload) == W.CARRY_NEW
    assert CN.carried_span_ms(payload, NOW) is None
    wall = select([payload]).walls[0]
    assert wall["continuity_status"] == W.CARRY_NEW
    assert wall["not_carried_reason"] == CN.NOT_CARRIED_NO_LEDGER


def test_a_status_this_reader_does_not_recognize_is_not_a_carry():
    for status in ("CARRIED_SOFT", "carried", "MAYBE", "", None, True, 1):
        payload = candidate()
        payload["continuity_status"] = status
        payload["continuity_first_seen_ms"] = NOW - 300_000
        assert CN.carried_span_ms(payload, NOW) is None
        assert select([payload]).walls == []


def test_a_carry_with_an_unusable_origin_carries_nothing():
    payload = carried(candidate())
    payload["continuity_first_seen_ms"] = None
    payload["continuity_persistence_ms"] = None
    assert CN.carried_span_ms(payload, NOW) is None
    assert select([payload]).walls == []


def test_the_rule_is_absent_unless_the_viewer_injects_it():
    """`wallrule.py` stays the implementation of `lm-wall.v2` alone."""
    without = R.select([carried(candidate())], side="ASK", mid=MID, latest_sample_ms=NOW)
    assert without.walls == []
    assert without.rejected[R.REJECT_PERSISTENCE] == 1


# --- the published panel ----------------------------------------------------------------------

def ledger(**overrides):
    payload = {"proof": W.CARRY_PROOF, "window_max_ms": W.SOFT_WINDOW_MAX_MS,
               "gates_required": list(W.SOFT_GATES), "active_carried": 3, "soft_refreshes": 2,
               "hard_transitions": 1, "wall_carried_total": 7, "wall_ended_total": 2,
               "wall_unknown_total": 5, "proofs_superseded": 0, "pending_proof": None,
               "last_transition": {"refresh_type": W.SOFT,
                                   "continuity_reason": W.REASON_SOFT_PROVEN, "cause": None,
                                   "generation_from": 1, "generation_to": 2,
                                   "candidates_before": 9, "carried_lost": 0, "eligible": 7,
                                   "wall_carried": 7, "wall_ended": 2, "wall_unknown": 0,
                                   "carried_truncated": False,
                                   "proof": {"overlap_check": {"passed": True},
                                             "gates": {gate: True for gate in W.SOFT_GATES},
                                             "window_ms": 130, "reason": "coverage_edge"}}}
    payload.update(overrides)
    return payload


def test_the_panel_reports_the_three_counts_together():
    """A reader shown only the carries cannot tell a quiet book from a rule carrying everything."""
    view = V.continuity_view(WallSet(continuity=ledger()))
    assert view["available"] is True
    assert view["wall_carried_total"] == 7
    assert view["wall_ended_total"] == 2 and view["wall_unknown_total"] == 5
    last = view["last_transition"]
    assert last["refresh_type"] == W.SOFT and last["wall_ended"] == 2
    assert last["overlap_check"]["passed"] is True
    assert last["window_ms"] == 130 and last["refresh_trigger"] == "coverage_edge"
    assert view["rule"]["identity"]["sha256_agrees"] is True


def test_the_panel_says_so_when_the_collector_publishes_no_ledger():
    view = V.continuity_view(WallSet())
    assert view["available"] is False
    assert view["unavailable_reason"] == CN.NOT_CARRIED_NO_LEDGER
    assert view["active_carried_in_view"] == 0
    # The rule identity is still published: a drifted rule has to be visible either way.
    assert view["rule"]["rule_version"] == "lm-continuity.v5"


def test_the_panel_counts_the_carried_candidates_actually_in_the_view():
    """The collector's count is of its whole dictionary; a truncated list shows fewer."""
    resting = {("ASK", "85100"): carried(candidate()),
               ("ASK", "85200"): new(candidate(price="85200"))}
    view = V.continuity_view(WallSet(continuity=ledger(active_carried=99), resting=resting))
    assert view["active_carried"] == 99
    assert view["active_carried_in_view"] == 1 and view["candidates_in_view"] == 2


def test_a_hard_transition_names_what_ended_the_history():
    view = V.continuity_view(WallSet(continuity=ledger(last_transition={
        "refresh_type": W.HARD, "continuity_reason": W.REASON_INTERRUPTED, "cause": "RECONNECT",
        "candidates_before": 12, "carried_lost": 4, "eligible": 0, "wall_carried": 0,
        "wall_ended": 0, "wall_unknown": 12, "generation_from": 2, "generation_to": 2,
        "carried_truncated": False, "proof": {}})))
    last = view["last_transition"]
    assert last["refresh_type"] == W.HARD and last["cause"] == "RECONNECT"
    assert last["wall_unknown"] == 12 and last["wall_carried"] == 0
    # What the resync destroyed is a number on screen, not a silence.
    assert last["carried_lost"] == 4


def test_the_candidate_table_shows_the_evidence_the_rule_acted_on():
    row = V.wall_row(carried(candidate()), mid=MID, latest_sample_ms=NOW)
    assert row["continuity_status"] == W.CARRY_CARRIED
    assert row["continuity_first_seen_ms"] == NOW - 300_000
    assert row["carried_persistence_ms"] == 300_000
    assert row["continuity_samples"] == 300 and row["continuity_refreshes"] == 1
    assert row["observed_persistence_ms"] == 2_000, "the own span stays the own span here"


# --- end to end, through a journal a real collector wrote -------------------------------------

def end_to_end(tmp_path, *, soft_refresh_after):
    """A real session, a real voluntary refresh, read back the way the API reads it."""
    from app.crypto.liquidity_map import journal as J
    from app.crypto.liquidity_map.wallstate import WallFollower
    from tests.crypto.liquidity_map_fixtures import written_journal

    root = tmp_path / "journal"
    info = written_journal(root, samples=16, end_session=False, seal=False,
                           soft_refresh_after=soft_refresh_after)
    session = J.latest_session(root)
    now = info["last_sample_ms"]
    wall_set = WallFollower().refresh(root, session, now_ms=now)
    derived = J.last_record(root, "derived", session)[0]
    return V.snapshot_view(root=str(root), session=session, derived_record=derived,
                           wall_set=wall_set, wall_filter=DEFAULT_WALL_FILTER, now_ms=now)


def test_a_wall_survives_a_real_voluntary_refresh_in_the_middle_of_a_session(tmp_path):
    """Sixteen samples with a coverage-edge refresh at the twelfth.

    Without the carry, the four samples after the refresh leave every candidate under R4's
    10,000 ms and both ladders are empty. This is that same session, end to end, with the
    continuity rule in place.
    """
    view = end_to_end(tmp_path, soft_refresh_after=12)
    ledger = view["continuity"]
    assert ledger["available"] is True
    assert ledger["soft_refreshes"] == 1 and ledger["hard_transitions"] == 0
    assert ledger["last_transition"]["refresh_type"] == W.SOFT
    assert ledger["last_transition"]["continuity_reason"] == W.REASON_SOFT_PROVEN
    assert ledger["last_transition"]["wall_carried"] == 2
    assert ledger["active_carried"] == 2

    walls = view["sides"]["ASK"]["walls"] + view["sides"]["BID"]["walls"]
    assert walls, "the carry is what keeps these on screen four seconds after a refresh"
    for wall in walls:
        assert wall["continuity_status"] == W.CARRY_CARRIED
        assert wall["persistence_source"] == CN.SOURCE_CARRIED
        assert wall["own_persistence_ms"] < R.MIN_PERSISTENCE_MS
        assert wall["observed_persistence_ms"] >= R.MIN_PERSISTENCE_MS
        assert wall["order_identity_proven"] is False
    assert view["walls"]["carried"] == 2
    assert view["walls"]["continuity_rule"]["identity"]["sha256_agrees"] is True


def test_the_same_session_without_a_refresh_shows_the_same_walls_uncarried(tmp_path):
    """The control: nothing about the carry changes a session that never refreshed."""
    view = end_to_end(tmp_path, soft_refresh_after=None)
    assert view["continuity"]["soft_refreshes"] == 0
    assert view["continuity"]["last_transition"] is None
    walls = view["sides"]["ASK"]["walls"] + view["sides"]["BID"]["walls"]
    assert len(walls) == 2
    for wall in walls:
        assert wall["continuity_status"] == W.CARRY_NEW
        assert wall["persistence_source"] == CN.SOURCE_OWN
        assert wall["carried_persistence_ms"] is None
        assert wall["observed_persistence_ms"] >= R.MIN_PERSISTENCE_MS
