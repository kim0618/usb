"""H-V2-D4-H1 §12/§13: the frozen prediction, measured.

`H_V2_D4_H_PRE_TIER_B_INTEGRITY_HARDENING_V1.md` §K wrote this down before S1/S2 existed:

    Predicted post-revision measurement on the same corpus: 0 violations of 38 eligible.

That is the only number this file is allowed to be about, and it is asserted against
`replay_d4_h1_state_fidelity.FROZEN_PREDICTION` - the literal the replay carries - rather than against
a figure retyped here, so the prediction and the check cannot drift apart.

These tests read stored, already-paid-for records and make zero live calls. They skip on a clean
checkout, which is why the contract itself is asserted on inline fixtures in
`test_d4_h1_state_polarity.py` and `test_d4_h1_metric_authority.py`.
"""

from __future__ import annotations

import pytest

from app.backtest.strategy_h_v2.expectation.d4_2_contract import D4_2_ROOT
from app.dev.replay_d4_h1_state_fidelity import (
    D4_H_FLAGGED_TOKENS,
    FROZEN_PREDICTION,
    replay,
)
from app.dev.replay_d4_h_integrity_hardening import AUTHORITATIVE_RUNS

ANALYSES_ROOT = D4_2_ROOT / "analyses"
NEEDS_STORED_RUNS = pytest.mark.skipif(
    not all((ANALYSES_ROOT / run).exists() for run, _, _ in AUTHORITATIVE_RUNS),
    reason="the stored D4 runs are gitignored runtime data",
)


@pytest.fixture(scope="module")
def report():
    return replay()


@NEEDS_STORED_RUNS
def test_the_replay_is_free(report):
    assert report["live_calls"] == 0 and report["live_cost_usd"] == 0.0


@NEEDS_STORED_RUNS
def test_the_corpus_is_the_same_frozen_one(report):
    """§1: the same stored outputs D4-H used. No new live output."""
    assert [run["run_id"] for run in report["runs"]] == [r for r, _, _ in AUTHORITATIVE_RUNS]
    assert sum(len(run["candidates"]) for run in report["runs"]) == 7


# --- §12: the frozen prediction ---------------------------------------------------------------------

@NEEDS_STORED_RUNS
def test_the_frozen_prediction_is_unedited():
    """The prediction is a literal in the replay module. If a future change to the gate moves the
    measurement, the prediction must stay put and the measurement must be reported as it falls."""
    assert FROZEN_PREDICTION == {"d4_h_eligible_claims": 38, "d4_h_violating_claims": 0}


@NEEDS_STORED_RUNS
def test_the_frozen_prediction_is_confirmed(report):
    assert report["measured_on_the_frozen_prediction_denominator"] == FROZEN_PREDICTION
    assert report["frozen_prediction_confirmed"] is True


@NEEDS_STORED_RUNS
def test_the_prediction_is_measured_on_d4_hs_own_denominator(report):
    """The prediction was made about D4-H's 38 ELIGIBLE CLAIMS. H1 adjudicates per ASSERTION, so the
    denominator could have been quietly swapped for a friendlier one. It was not: the claim-level rule
    is kept verbatim in `StateFidelityFinding.d4_h_eligible`, it still returns 38, and H1's own
    assertion-level figures are reported beside it rather than in place of it."""
    assert report["measured_on_the_frozen_prediction_denominator"]["d4_h_eligible_claims"] == 38
    assert report["assertion_level"]["claims_naming_a_state"] >= 38, (
        "H1's own denominator is wider, because it no longer requires a cited state fact")
    assert report["assertion_level"]["assertions_total"] >= \
        report["assertion_level"]["claims_naming_a_state"]


# --- the result is not a vacuous zero ----------------------------------------------------------------

@NEEDS_STORED_RUNS
def test_the_zero_violations_rests_on_a_real_evaluated_denominator(report):
    """The failure mode this guards against is a gate that declines to evaluate everything and reports
    0 violations. 41 of 49 assertions were actually bound to a metric and compared."""
    level = report["assertion_level"]
    assert level["assertions_failed"] == 0
    assert level["assertions_evaluated"] == 41
    assert level["assertions_passed"] == 41
    assert level["assertions_total"] == 49
    assert level["assertions_evaluated"] / level["assertions_total"] > 0.8


@NEEDS_STORED_RUNS
def test_every_not_evaluated_assertion_has_a_named_structural_reason(report):
    """8 of the 49, and each for one of two reasons that are properties of the sentence rather than of
    its verdict: the state is denied or contrasted rather than asserted, or the clause names no
    code-owned metric identifier ("fundamentals ... on the operating line", "accelerating Cloud
    growth"). Pinned so a later change cannot grow this set unnoticed."""
    assert report["assertion_level"]["not_evaluated_by_reason"] == {
        "NO_METRIC_NAMED": 3, "POLARITY_NOT_ASSERTED": 5}


@NEEDS_STORED_RUNS
def test_every_candidate_reaches_a_real_pass(report):
    for run in report["runs"]:
        for candidate in run["candidates"]:
            assert candidate["status"] == "PASS", (run["label"], candidate["ticker"])
            assert candidate["assertions_evaluated"] > 0, "a PASS needs something evaluated"
            assert candidate["assertions_failed"] == 0


# --- §13: D4-H's eight, each resolved individually ---------------------------------------------------

@NEEDS_STORED_RUNS
def test_all_eight_d4_h_flagged_tokens_are_located(report):
    """A token that cannot be found is UNRESOLVED, not closed. The first version of the replay keyed
    this lookup on the document's prose label instead of the run id and silently missed five of the
    eight, reporting them as non-violations because nothing had been found to violate anything."""
    assert len(D4_H_FLAGGED_TOKENS) == 8
    assert report["d4_h_flagged_tokens_located"] == 8
    assert report["d4_h_flagged_tokens_not_located"] == 0
    assert all(row["located"] for row in report["d4_h_flagged_tokens_resolved"])


@NEEDS_STORED_RUNS
def test_none_of_the_eight_is_still_a_violation(report):
    assert report["d4_h_flagged_tokens_still_violating"] == 0


@NEEDS_STORED_RUNS
def test_each_of_the_eight_closed_through_the_mechanism_it_was_attributed_to(report):
    """The substantive check, and the reason this is a semantic repair rather than a suppression: a
    token attributed to S1 must close because its polarity is not an assertion, and a token attributed
    to S2 must close because the metric it is bound to actually holds the state it names - not because
    something stopped looking at it."""
    by_mechanism: dict[str, list[dict]] = {}
    for row in report["d4_h_flagged_tokens_resolved"]:
        assert len(row["outcomes"]) == 1, row
        by_mechanism.setdefault(row["d4_h_mechanism"], []).append(row)

    for row in by_mechanism["S1"] + by_mechanism["S1+S2"]:
        outcome = row["outcomes"][0]
        assert outcome["polarity"] in ("NEGATED", "REJECTED_OR_CONTRASTED"), row
        assert outcome["reason"] == "POLARITY_NOT_ASSERTED"

    for row in by_mechanism["S2"]:
        outcome = row["outcomes"][0]
        assert outcome["polarity"] == "ASSERTED", "an S2 finding WAS a real assertion"
        if outcome["status"] == "PASS":
            assert outcome["metric"] is not None
            assert outcome["authoritative_state"] == row["state"], (
                "it passes because the metric it names holds exactly this state")
        else:
            assert outcome["reason"] == "NO_METRIC_NAMED", row
            assert row["path"] == "root.gap_rationale[6]", (
                "the only S2 token whose prose names no metric identifier is GOOG's "
                "'fundamentals continued improving on the operating line'")


@NEEDS_STORED_RUNS
def test_the_three_s2_passes_are_real_metric_matches(report):
    """Named explicitly so the three are readable without the artifact: BSY's `eps_diluted` twice and
    its `operating_income` once, each restated correctly by a claim that did not cite that metric's
    own state fact - which is precisely what V1 reported as an unowned state."""
    passes = {(row["ticker"], row["state"], row["outcomes"][0]["metric"])
              for row in report["d4_h_flagged_tokens_resolved"]
              if row["outcomes"][0]["status"] == "PASS"}
    assert passes == {("BSY", "DETERIORATING", "eps_diluted"),
                      ("BSY", "DECELERATING", "operating_income")}


# --- §13: nothing survives, so nothing needs classifying --------------------------------------------

@NEEDS_STORED_RUNS
def test_no_violation_survives_and_none_needed_classification(report):
    assert report["surviving_violations"] == []
    assert report["surviving_violation_classification_required"] is False


# --- §2: the authority side is recorded, not re-derived ---------------------------------------------

@NEEDS_STORED_RUNS
def test_every_candidate_reports_its_state_fact_inventory(report):
    """§2: candidate_id, metric, state, fact_id for every code-owned state fact, so the authority half
    of every comparison can be read off the artifact."""
    for run in report["runs"]:
        for candidate in run["candidates"]:
            inventory = candidate["state_fact_inventory"]
            assert inventory, (run["label"], candidate["ticker"])
            for row in inventory:
                assert set(row) == {"candidate_id", "metric", "state", "fact_id"}
                assert row["fact_id"].endswith(
                    f"research_facts.fundamental_changes.{row['metric']}.state")
            # The inventory and the authority map are two views of one source and must agree.
            authority = candidate["authority"]
            assert {row["metric"]: row["state"] for row in inventory} == authority
