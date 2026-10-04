"""The frozen wall rule, and the ways a selection rule goes wrong.

Grouped by the mistake each test prevents: a rule that drifted from the document that froze it,
a threshold nobody can see, the touch level masquerading as a wall, one structure drawn as nine
lines, nine ordinary levels adding up to a wall that no level is, a bin whose identity moves when
mid moves, and a refusal that is silent instead of counted.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.crypto.liquidity_map import wallrule as R

MID = Decimal("85000")


def candidate(side="ASK", price="85100", notional="1000000", multiple="9",
              first_seen_ms=0, persistence_ms=60_000, coverage="COMPLETE", **extra):
    payload = {"side": side, "price": price, "qty": "11.7", "notional": notional,
               "multiple": multiple, "local_average": "1", "neighbours": 10,
               "first_seen_ms": first_seen_ms, "persistence_ms": persistence_ms,
               "samples": 60, "coverage": coverage, "generation": 1}
    payload.update(extra)
    return payload


def select(rows, side="ASK", mid=MID, latest_sample_ms=60_000, **kwargs):
    return R.select(rows, side=side, mid=mid, latest_sample_ms=latest_sample_ms, **kwargs)


# --- the rule is the document -----------------------------------------------------------------

def test_the_frozen_document_is_present_and_its_recorded_hash_still_agrees():
    identity = R.rule_identity()
    assert identity["status"] == "PRESENT"
    assert identity["sha256_agrees"] is True, "the frozen rule changed without a new version"
    assert identity["rule_version"] == "lm-wall.v2"
    assert len(identity["rule_sha256"]) == 64


def test_every_threshold_in_the_code_appears_in_the_frozen_document():
    """The document is the specification. A figure only in the code would be unreviewable."""
    text = " ".join(R.rule_path().read_text(encoding="utf-8").split())
    assert "notional_usdt >= 250000" in text
    assert "multiple >= 5" in text
    assert "distance_bps >= 1.0" in text
    assert "observed_persistence_ms >= 10000" in text
    assert "5.0 USDT = 50 ticks" in text
    assert R.MIN_NOTIONAL_USDT == Decimal("250000")
    assert R.MIN_MULTIPLE == Decimal("5")
    assert R.MIN_DISTANCE_BPS == Decimal("1.0")
    assert R.MIN_PERSISTENCE_MS == 10_000
    assert R.BIN_WIDTH_USDT == Decimal("5.0") and R.BIN_WIDTH_TICKS == 50


def test_the_document_states_the_order_of_evaluation_and_that_it_changes_no_contract():
    text = " ".join(R.rule_path().read_text(encoding="utf-8").split())
    assert "R1–R4 filter members, *then* R5 groups" in text
    assert "unchanged by this document" in text
    assert R.rule_view()["evaluation_order"] == "FILTER_MEMBERS_THEN_GROUP"
    assert R.rule_view()["changes_data_contract"] is False


def test_a_missing_document_is_reported_rather_than_raised(tmp_path):
    identity = R.rule_identity(tmp_path / "gone.md")
    assert identity["status"] == "MISSING" and identity["rule_sha256"] is None


def test_an_edited_document_stops_agreeing_with_its_hash(tmp_path):
    edited = tmp_path / "WALL_RULE_V2.md"
    edited.write_text("not the frozen rule", encoding="utf-8")
    edited.with_suffix(".sha256").write_text("0" * 64 + "  WALL_RULE_V2.md\n", encoding="utf-8")
    assert R.rule_identity(edited)["sha256_agrees"] is False


# --- R1: absolute notional --------------------------------------------------------------------

def test_a_level_below_the_notional_floor_is_not_a_wall_however_large_its_multiple():
    """The 1,473x observed on a real book was a 0.0068 BTC neighbourhood, not a wall."""
    result = select([candidate(notional="43000", multiple="1473")])
    assert result.walls == []
    assert result.rejected[R.REJECT_NOTIONAL] == 1


def test_a_level_at_the_notional_floor_is_kept():
    result = select([candidate(notional="250000")])
    assert len(result.walls) == 1


# --- R2: local relative size ------------------------------------------------------------------

def test_a_level_that_does_not_stand_out_locally_is_not_a_wall():
    result = select([candidate(multiple="4.9")])
    assert result.walls == [] and result.rejected[R.REJECT_MULTIPLE] == 1


def test_the_v0_contract_multiple_alone_selects_nothing():
    """3x is below the median candidate, which is why the rule had to add its own floor."""
    assert Decimal(R.rule_view()["v0_rule"]["min_multiple"]) < R.MIN_MULTIPLE
    assert select([candidate(multiple="3")]).walls == []


# --- R3: distance from mid --------------------------------------------------------------------

def test_the_touch_level_is_not_a_wall_even_when_it_is_huge():
    result = select([candidate(price="85000.1", notional="5000000", multiple="97")])
    assert result.walls == [] and result.rejected[R.REJECT_DISTANCE] == 1


def test_the_distance_floor_is_published_as_a_count_so_it_is_never_invisible():
    """A region nobody is looking at must not read as a region with nothing in it."""
    result = select([candidate(price="85000.1"), candidate(price="85100")])
    assert len(result.walls) == 1
    assert result.view()["rejected"][R.REJECT_DISTANCE] == 1


def test_distance_is_measured_away_from_mid_on_both_sides():
    assert R.distance_bps(Decimal("85100"), MID, "ASK") > 0
    assert R.distance_bps(Decimal("84900"), MID, "BID") > 0
    # Through mid is negative, which the floor then refuses.
    assert R.distance_bps(Decimal("84900"), MID, "ASK") < 0


def test_a_bid_above_mid_is_refused_rather_than_measured_as_distant():
    result = select([candidate(side="BID", price="85100")], side="BID")
    assert result.walls == [] and result.rejected[R.REJECT_DISTANCE] == 1


# --- R4: persistence --------------------------------------------------------------------------

def test_a_candidate_observed_for_one_sample_is_not_resting():
    row = candidate(first_seen_ms=59_000, persistence_ms=0)
    result = select([row], latest_sample_ms=60_000)
    assert result.walls == [] and result.rejected[R.REJECT_PERSISTENCE] == 1


def test_the_span_is_measured_against_the_newest_sample_not_the_row():
    """A journal OPENED row says 0 forever, so the row's own field cannot be the answer."""
    row = candidate(first_seen_ms=0, persistence_ms=0)
    assert R.observed_persistence_ms(row, 60_000) == 60_000
    assert len(select([row]).walls) == 1


def test_the_collectors_live_figure_wins_when_it_is_larger():
    """In the checkpoint `persistence_ms` is live, and it can exceed this viewer's own span."""
    row = candidate(first_seen_ms=55_000, persistence_ms=120_000)
    assert R.observed_persistence_ms(row, 60_000) == 120_000


def test_a_candidate_with_no_measurable_span_at_all_is_refused_not_guessed():
    row = candidate(first_seen_ms=None, persistence_ms=None)
    result = select([row], latest_sample_ms=None)
    assert result.walls == [] and result.rejected[R.REJECT_UNUSABLE] == 1


# --- R5: price bins ---------------------------------------------------------------------------

def test_adjacent_qualifying_levels_become_one_wall():
    """At the measured distribution a five dollar bin held up to nine candidates."""
    rows = [candidate(price=str(85100 + step), notional="300000")
            for step in (0, Decimal("0.1"), Decimal("1.5"), Decimal("4.9"))]
    result = select(rows)
    assert len(result.walls) == 1
    assert result.walls[0]["bin_members"] == 4
    assert result.passed == 4 and result.grouped_away == 3


def test_levels_in_different_bins_stay_separate():
    result = select([candidate(price="85100"), candidate(price="85106")])
    assert len(result.walls) == 2


def test_the_bin_is_represented_by_its_largest_member():
    rows = [candidate(price="85100", notional="300000"),
            candidate(price="85102", notional="900000")]
    wall = select(rows).walls[0]
    assert wall["price"] == "85102" and wall["notional_usdt"] == "900000"


def test_the_bin_total_is_labelled_a_lower_bound_over_candidates_only():
    """Levels in the bin that did not qualify are not in it, so it is not the bin's liquidity."""
    rows = [candidate(price="85100", notional="300000"),
            candidate(price="85102", notional="400000")]
    wall = select(rows).walls[0]
    assert wall["bin_candidate_notional_usdt"] == "700000"
    assert wall["bin_candidate_notional_is_lower_bound"] is True


def test_grouping_happens_after_the_thresholds_not_before():
    """Otherwise nine ordinary levels add up to a wall that no level in the bin is."""
    rows = [candidate(price=str(85100 + step), notional="100000")
            for step in (0, 1, 2, 3, 4)]
    result = select(rows)
    assert result.walls == []
    assert result.rejected[R.REJECT_NOTIONAL] == 5


def test_bin_edges_are_anchored_in_absolute_price_so_a_bin_keeps_its_identity():
    """A bps-defined bin would move its own edges as mid moves, renaming an unchanged wall."""
    low = R.price_bin(Decimal("85103.7"))
    assert low == Decimal("85100")
    for mid in (Decimal("84000"), Decimal("85000"), Decimal("85090")):
        wall = select([candidate(price="85103.7")], mid=mid).walls[0]
        assert wall["bin_low"] == "85100" and wall["bin_high"] == "85105"


# --- coverage ---------------------------------------------------------------------------------

def test_a_bin_outside_the_observed_interval_is_partial_not_complete():
    rows = [candidate(price="85100")]
    inside = select(rows, known_low=Decimal("84000"), known_high=Decimal("86000"))
    straddling = select(rows, known_low=Decimal("84000"), known_high=Decimal("85102"))
    assert inside.walls[0]["coverage"] == "COMPLETE"
    assert straddling.walls[0]["coverage"] == "PARTIAL"


def test_a_candidate_the_collector_called_partial_is_never_promoted():
    result = select([candidate(coverage="PARTIAL")], known_low=Decimal("84000"),
                    known_high=Decimal("86000"))
    assert result.walls[0]["coverage"] == "PARTIAL"


def test_without_a_known_interval_a_bin_is_unknown_rather_than_assumed_complete():
    assert select([candidate()]).walls[0]["coverage"] == "UNKNOWN"


# --- ordering and accounting ------------------------------------------------------------------

def test_the_nearest_wall_is_the_lowest_ask_and_the_highest_bid():
    asks = select([candidate(price="85300"), candidate(price="85100")])
    bids = select([candidate(side="BID", price="84700"),
                   candidate(side="BID", price="84900")], side="BID")
    assert asks.walls[0]["price"] == "85100"
    assert bids.walls[0]["price"] == "84900"


def test_without_a_mid_nothing_is_selected_and_the_reason_says_why():
    result = select([candidate()], mid=None)
    assert result.walls == [] and result.rejected[R.REJECT_NO_MID] == 1


def test_the_other_side_is_not_considered_at_all():
    result = select([candidate(side="BID", price="84900")], side="ASK")
    assert result.considered == 0 and result.walls == []


def test_every_refusal_lands_in_exactly_one_counter():
    rows = [candidate(notional="1000"), candidate(multiple="1"), candidate(price="85000.1"),
            candidate(first_seen_ms=59_999, persistence_ms=0), candidate(price="oops"),
            candidate(price="85100")]
    result = select(rows, latest_sample_ms=60_000)
    assert result.considered == 6
    assert sum(result.rejected.values()) == 5 and result.passed == 1


def test_the_rule_names_no_direction_and_no_judgement():
    text = repr(R.rule_view()) + repr(select([candidate()]).walls)
    for banned in ("LONG", "SHORT", "spoof", "absorption", "iceberg", "signal"):
        assert banned not in text, banned


@pytest.mark.parametrize("field", ["persistence_is_sampled_span", "order_identity_proven"])
def test_every_wall_carries_the_contract_s_disclaimers(field):
    assert field in select([candidate()]).walls[0]
