"""H-V2-D4-H1 S2: metric-level authority and the provenance/authority separation. Frozen before the
offline replay ran.

The repair S2 needs is "stop deriving categorical truth from `claim.evidence_ids`". The repair it must
NOT be is "allow any state the candidate holds anywhere", which would let a fabricated state through
whenever some other metric happened to hold it. Metric binding is what separates the two, and
`test_the_wrong_metric_fails_even_though_the_candidate_owns_that_state` is the test that says so.
"""

from __future__ import annotations

import pytest

from app.backtest.strategy_h_v2.expectation.code_facts import CodeFact
from app.backtest.strategy_h_v2.expectation.d4_2_contract import MechanicalGateStatus
from app.backtest.strategy_h_v2.expectation.state_fidelity import (
    BindingStatus,
    METRIC_PROSE_ALIASES,
    NotEvaluatedReason,
    authoritative_states,
    claim_state_fidelity,
    metrics_named,
    state_assertions,
    state_fact_inventory,
    state_fidelity_report,
    state_fidelity_status,
)

#: GOOG's real code-owned states, from its stored D4 package. Used because the two S2 findings this
#: repair is measured against are GOOG's, and because it is the shape that makes the hole obvious:
#: three of its metrics hold IMPROVING while `operating_income` does not.
GOOG = {"revenue": "IMPROVING", "operating_income": "ACCELERATING", "eps_diluted": "IMPROVING",
        "free_cash_flow": "INFLECTION_NEGATIVE", "operating_margin": "IMPROVING",
        "shares_outstanding": "UNKNOWN"}
#: BSY's, for the same reason on the other side.
BSY = {"revenue": "STABLE", "operating_income": "DECELERATING", "eps_diluted": "DETERIORATING",
       "free_cash_flow": "INFLECTION_NEGATIVE", "operating_margin": "STABLE",
       "shares_outstanding": "UNKNOWN"}


def _one(text: str, authority: dict[str, str]):
    assertions = state_assertions("root.claim", text, authority)
    assert len(assertions) == 1, [a.to_dict() for a in assertions]
    return assertions[0]


def _state_fact(metric: str, state: str) -> CodeFact:
    path = f"research_facts.fundamental_changes.{metric}.state"
    return CodeFact(f"CODE:D4:BUNDLE:CHUNK:{path}", path, state, "STATE_TOKEN",
                    f"code-owned D1/D2 fundamental change state for {metric}")


def _return_fact(value: float) -> CodeFact:
    path = "pre_event_price_context.return_3m"
    return CodeFact(f"CODE:D4:BUNDLE:CHUNK:{path}", path, value, "RETURN_FRACTION",
                    "candidate return over the last 63 sessions")


# --- brief §7: metric binding is mandatory ---------------------------------------------------------

def test_the_wrong_metric_fails_even_though_the_candidate_owns_that_state():
    """The brief's §7 example, and the single most important test in this file. GOOG holds IMPROVING
    under revenue, eps and margin. "Operating income is improving." is still a FAIL, because binding
    resolves to `operating_income` and its authoritative state is ACCELERATING."""
    assertion = _one("Operating income is improving.", GOOG)
    assert assertion.metric == "operating_income"
    assert assertion.binding is BindingStatus.BOUND
    assert assertion.authoritative_state == "ACCELERATING"
    assert assertion.status is MechanicalGateStatus.FAIL


def test_the_right_metric_passes():
    assertion = _one("Revenue is improving.", GOOG)
    assert assertion.metric == "revenue" and assertion.status is MechanicalGateStatus.PASS


@pytest.mark.parametrize("text,metric", [
    ("Revenue is improving.", "revenue"),
    ("Revenues are improving.", "revenue"),
    ("Operating income is ACCELERATING.", "operating_income"),
    ("EPS is improving.", "eps_diluted"),
    ("Diluted EPS is improving.", "eps_diluted"),
    ("Earnings per share is improving.", "eps_diluted"),
    ("Free cash flow is INFLECTION_NEGATIVE.", "free_cash_flow"),
    ("FCF is INFLECTION_NEGATIVE.", "free_cash_flow"),
    ("Operating margin is improving.", "operating_margin"),
])
def test_every_metric_identifier_rendering_binds(text, metric):
    assertion = _one(text, GOOG)
    assert assertion.metric == metric and assertion.status is MechanicalGateStatus.PASS


def test_the_state_may_precede_its_metric():
    """"DECELERATING operating income" - a clause is about one metric whichever order it is written
    in, which is how the D4-H corpus's bare-token sentences are worded."""
    assertion = _one("Code-owned DECELERATING operating income is the reality side.", BSY)
    assert assertion.metric == "operating_income" and assertion.status is MechanicalGateStatus.PASS


def test_a_coordinated_pair_of_metrics_is_separated_by_the_coordination():
    text = "EPS is deteriorating and free cash flow is INFLECTION_NEGATIVE."
    assertions = state_assertions("root.claim", text, BSY)
    assert [(a.state, a.metric, a.status.value) for a in assertions] == [
        ("DETERIORATING", "eps_diluted", "PASS"),
        ("INFLECTION_NEGATIVE", "free_cash_flow", "PASS")]


def test_a_coordinated_pair_still_catches_a_wrong_one():
    """Coordination separates the clauses; it does not excuse either of them."""
    text = "EPS is deteriorating and free cash flow is improving."
    assertions = state_assertions("root.claim", text, BSY)
    assert [(a.metric, a.status.value) for a in assertions] == [
        ("eps_diluted", "PASS"), ("free_cash_flow", "FAIL")]


# --- brief §8: unresolvable binding is NOT_EVALUATED, never a forced match -------------------------

@pytest.mark.parametrize("text", [
    "Fundamentals continued improving on the operating line.",
    "Top-line growth is improving.",
    "ARR growth is improving.",
    "The reality side is improving.",
    "Growth durability is improving.",
])
def test_prose_that_names_no_metric_identifier_is_not_evaluated(text):
    """§8: structured identifier first, and no regex guessing after it. "the operating line" could be
    operating income or operating margin; "fundamentals" is not a metric at all. Forcing either would
    be the match §8 forbids, and NOT_EVALUATED is the honest answer - not a PASS."""
    assertion = _one(text, GOOG)
    assert assertion.binding is BindingStatus.NO_METRIC_NAMED
    assert assertion.metric is None
    assert assertion.status is MechanicalGateStatus.NOT_EVALUATED
    assert assertion.reason is NotEvaluatedReason.NO_METRIC_NAMED


def test_two_metrics_in_one_unsplittable_clause_is_ambiguous_not_guessed():
    assertion = _one("Revenue versus operating income is improving", GOOG)
    assert assertion.binding is BindingStatus.AMBIGUOUS
    assert assertion.reason is NotEvaluatedReason.AMBIGUOUS_METRIC
    assert assertion.status is MechanicalGateStatus.NOT_EVALUATED


def test_a_metric_the_candidate_does_not_own_is_not_evaluated():
    assertion = _one("Operating margin is improving.", {"revenue": "IMPROVING"})
    assert assertion.metric == "operating_margin"
    assert assertion.reason is NotEvaluatedReason.METRIC_NOT_CODE_OWNED
    assert assertion.status is MechanicalGateStatus.NOT_EVALUATED


def test_an_unknown_authority_is_not_evaluated_rather_than_failed():
    """UNKNOWN is the ABSENCE of a code-owned categorical truth, not a competing one, so there is
    nothing for an assertion to contradict. Whether a directional claim over an UNKNOWN metric is
    properly sourced is E2's question."""
    assertion = _one("Shares outstanding is DECREASING.", GOOG)
    assert assertion.metric == "shares_outstanding"
    assert assertion.authoritative_state == "UNKNOWN", (
        "the authority is recorded, so the reader can see WHY it was not evaluated")
    assert assertion.reason is NotEvaluatedReason.AUTHORITY_UNKNOWN
    assert assertion.status is MechanicalGateStatus.NOT_EVALUATED


def test_no_authority_at_all_evaluates_nothing():
    for authority in (None, {}):
        assertion = _one("Revenue is improving.", authority)
        assert assertion.status is MechanicalGateStatus.NOT_EVALUATED
        assert assertion.reason is NotEvaluatedReason.METRIC_NOT_CODE_OWNED


# --- brief §6: provenance and authority are different questions ------------------------------------

def test_a_correct_state_passes_without_citing_that_states_evidence_id():
    """S2's repair, stated as its own test. The claim restates `eps_diluted = DETERIORATING`
    correctly while citing revenue's and free cash flow's state facts. V1 called that an unowned
    state; H1 calls it what it is."""
    finding = claim_state_fidelity(
        "root.gap_rationale[2]", "EPS is deteriorating.",
        [_state_fact("revenue", "STABLE"), _state_fact("free_cash_flow", "INFLECTION_NEGATIVE")],
        BSY)
    assert finding.cites_state_fact is True
    assert finding.d4_h_eligible is True
    assert finding.violated is False
    assert state_fidelity_status([finding]) is MechanicalGateStatus.PASS


def test_a_correct_state_passes_while_citing_no_state_fact_at_all():
    """The same separation taken to its edge: citation quality is E2's contract, not this gate's."""
    finding = claim_state_fidelity("root.gap_rationale[1]", "EPS is deteriorating.",
                                  [_return_fact(-0.09)], BSY)
    assert finding.cites_state_fact is False
    assert finding.d4_h_eligible is False, "D4-H's denominator required a cited state fact"
    assert state_fidelity_status([finding]) is MechanicalGateStatus.PASS


def test_citing_a_states_evidence_id_does_not_make_a_wrong_state_right():
    """The converse, so the separation cannot be read as a loosening: a claim may cite exactly the
    right fact and still contradict it."""
    finding = claim_state_fidelity("root.gap_rationale[0]", "Revenue is ACCELERATING.",
                                  [_state_fact("revenue", "STABLE")], BSY)
    assert finding.violated is True
    assert [a.authoritative_state for a in finding.violations] == ["STABLE"]
    assert state_fidelity_status([finding]) is MechanicalGateStatus.FAIL


# --- brief §2: the authority namespace is read, not restated ---------------------------------------

def test_the_authority_is_read_from_the_candidates_own_block():
    block = {"revenue": {"state": "improving", "confidence": "HIGH"},
             "operating_income": {"state": None},
             "free_cash_flow": {"confidence": "LOW"},
             "not_a_dict": "IMPROVING"}
    assert authoritative_states(block) == {"revenue": "IMPROVING"}
    assert authoritative_states(None) == {}


def test_the_state_fact_inventory_carries_candidate_metric_state_and_fact_id():
    facts = [_state_fact("revenue", "STABLE"), _state_fact("eps_diluted", "DETERIORATING"),
             _return_fact(0.12)]
    inventory = state_fact_inventory("BSY-BUNDLE", facts)
    assert inventory == [
        {"candidate_id": "BSY-BUNDLE", "metric": "eps_diluted", "state": "DETERIORATING",
         "fact_id": "CODE:D4:BUNDLE:CHUNK:research_facts.fundamental_changes.eps_diluted.state"},
        {"candidate_id": "BSY-BUNDLE", "metric": "revenue", "state": "STABLE",
         "fact_id": "CODE:D4:BUNDLE:CHUNK:research_facts.fundamental_changes.revenue.state"},
    ]


def test_every_alias_key_is_a_metric_the_pipeline_actually_produces():
    """`pipeline._candidate_result` builds `fundamental_changes` from exactly six metrics (cash and
    total_debt are excluded from the block). If that set changes, this test is where it surfaces."""
    assert set(METRIC_PROSE_ALIASES) == {
        "revenue", "operating_income", "eps_diluted", "free_cash_flow", "operating_margin",
        "shares_outstanding"}


@pytest.mark.parametrize("prose", ["the operating line", "top-line", "fundamentals", "ARR",
                                   "growth durability", "cash", "total debt", "margins",
                                   "Cloud growth", "the recurring engine"])
def test_no_prose_description_is_treated_as_a_metric_identifier(prose):
    assert metrics_named(prose) == set()


@pytest.mark.parametrize("metric", sorted(METRIC_PROSE_ALIASES))
def test_the_raw_metric_identifier_always_binds(metric):
    """§8 ranks the structured metric id above every other binding source, so the identifier written
    verbatim has to be recognized. It was not, in the first H1 implementation: `\beps\b` cannot match
    inside "eps_diluted" because the underscore is a word character, so five frozen-corpus claims
    writing "the code-owned fundamental change state for eps_diluted is DETERIORATING" came back
    NO_METRIC_NAMED. The alias set is now derived from the metric KEY rather than only listed, so this
    cannot recur for a metric added later."""
    assert metrics_named(f"the state for {metric} is IMPROVING") == {metric}


def test_the_verbatim_identifier_sentence_from_the_frozen_corpus_binds_and_compares():
    text = "The code-owned fundamental change state for eps_diluted is DETERIORATING."
    assertion = _one(text, BSY)
    assert assertion.metric == "eps_diluted"
    assert assertion.status is MechanicalGateStatus.PASS
    assert _one(text.replace("DETERIORATING", "ACCELERATING"), BSY).status is \
        MechanicalGateStatus.FAIL


def test_a_segment_level_statement_is_not_a_consolidated_metric_claim():
    """"accelerating Cloud growth" is about a segment. The code owns a consolidated `revenue` state
    and nothing about Cloud, so there is no authoritative state to compare this against - and reading
    it as a claim about consolidated revenue would be the forced binding §8 forbids."""
    assertion = _one("a negative reaction despite accelerating Cloud growth", GOOG)
    assert assertion.binding is BindingStatus.NO_METRIC_NAMED
    assert assertion.status is MechanicalGateStatus.NOT_EVALUATED


# --- brief §10: the injected regression, and its number-independence -------------------------------

@pytest.mark.parametrize("text", [
    "Revenue is ACCELERATING.",
    "Revenue is ACCELERATING over 3 quarters.",
    "Revenue is ACCELERATING and the 3-month return was -9.05%.",
    "Revenue is ACCELERATING, below its 252-session high.",
])
def test_injected_regression_stable_to_accelerating_fails_with_or_without_numbers(text):
    finding = claim_state_fidelity("root.claim", text, [_state_fact("revenue", "STABLE")], BSY)
    assert state_fidelity_status([finding]) is MechanicalGateStatus.FAIL


def test_the_clean_and_injected_forms_differ_only_in_the_state():
    clean = "Revenue is STABLE, and management gave no new quantitative target."
    injected = clean.replace("STABLE", "ACCELERATING")
    facts = [_state_fact("revenue", "STABLE")]
    assert state_fidelity_status(
        [claim_state_fidelity("p", clean, facts, BSY)]) is MechanicalGateStatus.PASS
    assert state_fidelity_status(
        [claim_state_fidelity("p", injected, facts, BSY)]) is MechanicalGateStatus.FAIL


# --- the report's two denominators ------------------------------------------------------------------

def test_zero_comparable_assertions_is_not_evaluated_not_pass():
    assert state_fidelity_report([])["status"] == MechanicalGateStatus.NOT_EVALUATED.value
    finding = claim_state_fidelity("p", "Fundamentals are improving.", [], GOOG)
    report = state_fidelity_report([finding])
    assert report["assertions_total"] == 1
    assert report["assertions_evaluated"] == 0
    assert report["status"] == MechanicalGateStatus.NOT_EVALUATED.value


def test_the_report_keeps_both_denominators_separate():
    """H1's unit is the assertion; D4-H's was the claim. Both are reported, because D4-H's frozen
    prediction is about its own 38-claim denominator and has to stay measurable on it."""
    facts = [_state_fact("revenue", "STABLE")]
    findings = [
        claim_state_fidelity("p1", "Revenue is STABLE.", facts, BSY),
        claim_state_fidelity("p2", "Revenue is ACCELERATING.", facts, BSY),
        claim_state_fidelity("p3", "Fundamentals are improving.", facts, BSY),
        claim_state_fidelity("p4", "EPS is deteriorating.", [], BSY),
    ]
    report = state_fidelity_report(findings)
    assert report["claims_naming_a_state"] == 4
    assert report["d4_h_eligible_claims"] == 3, "p4 cites no state fact"
    assert report["d4_h_violating_claims"] == 1
    assert report["assertions_total"] == 4
    assert report["assertions_evaluated"] == 3
    assert report["assertions_passed"] == 2 and report["assertions_failed"] == 1
    assert report["not_evaluated_by_reason"] == {"NO_METRIC_NAMED": 1}
    assert report["polarity_counts"]["ASSERTED"] == 4
    assert report["status"] == MechanicalGateStatus.FAIL.value


# --- brief §11's checklist, end to end --------------------------------------------------------------

@pytest.mark.parametrize("authority_state,text,expected", [
    ("STABLE", "Revenue is stable.", "PASS"),
    ("STABLE", "Revenue is not accelerating.", "NOT_EVALUATED"),
    ("STABLE", "Revenue is stable rather than accelerating.", "PASS"),
    ("STABLE", "Revenue is accelerating.", "FAIL"),
    ("IMPROVING", "Revenue is improving.", "PASS"),
    ("DECELERATING", "Revenue is improving.", "FAIL"),
    ("DECELERATING", "Revenue is not improving.", "NOT_EVALUATED"),
])
def test_the_required_examples(authority_state, text, expected):
    finding = claim_state_fidelity("p", text, [_state_fact("revenue", authority_state)],
                                   {"revenue": authority_state})
    assert state_fidelity_status([finding]).value == expected


def test_the_mixed_polarity_required_example_evaluates_the_asserted_half():
    finding = claim_state_fidelity("p", "Revenue is not stable and is accelerating.",
                                   [_state_fact("revenue", "STABLE")], {"revenue": "STABLE"})
    assert [(a.state, a.polarity.value, a.status.value) for a in finding.assertions] == [
        ("STABLE", "NEGATED", "NOT_EVALUATED"), ("ACCELERATING", "ASSERTED", "FAIL")]
