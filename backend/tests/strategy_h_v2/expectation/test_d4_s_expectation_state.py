"""D4-S: the structured expectation knowledge state is authoritative and the prose layer is a guard.

What these tests are for. Two consecutive live runs (D4.3A, Final Tier A V3) had their first-attempt
validity set by one rule rejecting sentences that DENIED having consensus knowledge. Every previous
closure lengthened a phrase list and opened a new false-positive surface. So the thing to prove here
is not that a particular sentence now passes - it is that the MECHANISM changed: availability is
decided by code from the bundle, and polarity is decided by claim structure, so a sentence that
denies knowledge passes regardless of the words it uses to do so.

Nothing in this file may be satisfied by adding a phrase to a list. The absence sentences below are
deliberately worded in ways no list in the repository carries.
"""

from __future__ import annotations

import json

import pytest
from d4_helpers import D3_OUTPUT, NOW, SOURCE_ID, claim, d4_content, expectation_bundle, package

from app.backtest.strategy_h_v2.expectation import consensus_language, expectation_state, validate
from app.backtest.strategy_h_v2.expectation.code_facts import build_code_fact_index
from app.backtest.strategy_h_v2.expectation.evidence_schema import EvidenceBlock, LocatedExcerpt
from app.backtest.strategy_h_v2.expectation.expectation_state import (
    EXPECTATION_STATE_CONTRACT_VERSION,
    ExpectationKnowledgeStatus,
    affirmative_expectation_findings,
    derive_expectation_knowledge_state,
    is_affirmative_expectation_sentence,
    suppressed_absence_findings,
)
from app.backtest.strategy_h_v2.expectation.gap_contract import EvidenceAvailability
from app.backtest.strategy_h_v2.expectation.validate import assemble_and_validate_d4
from app.dev import audit_strategy_h_v2_d4_1

PKG = package()
BUNDLE = expectation_bundle()
FACTS = build_code_fact_index(BUNDLE)
UNKNOWN_STATE = derive_expectation_knowledge_state(BUNDLE)


def run(content: dict):
    return assemble_and_validate_d4(
        json.dumps(content), package=PKG, bundle=BUNDLE, research_output=D3_OUTPUT,
        code_facts=FACTS, analysis_id="A-1", version=1, model_name="claude-opus-5-5",
        model_version="opus", prompt_version="h_v2_d4_expectation_gap_v2", created_at=NOW,
    )


def excerpt(text: str) -> LocatedExcerpt:
    return LocatedExcerpt(
        source_id=SOURCE_ID, evidence_id=f"{SOURCE_ID}:CHUNK:0", source_type="SEC_8K",
        published_at=NOW, text=text, matched_terms=["consensus"],
    )


# --- the structured state itself ----------------------------------------------------------------

def test_the_current_data_layer_derives_an_unknown_state_that_forbids_the_claim():
    """`earnings.status` is UNKNOWN for 2,010 of 2,010 candidates, so this is every issuer's state -
    not a conservative default chosen here."""
    assert UNKNOWN_STATE.status == ExpectationKnowledgeStatus.UNKNOWN
    assert UNKNOWN_STATE.market_expectation_claim_allowed is False
    assert UNKNOWN_STATE.basis == ()
    assert UNKNOWN_STATE.consensus_status == EvidenceAvailability.SOURCE_NOT_AVAILABLE.value
    assert UNKNOWN_STATE.limitations, "an UNKNOWN state must say why, not merely be empty"
    assert UNKNOWN_STATE.contract_version == EXPECTATION_STATE_CONTRACT_VERSION


def test_one_readable_block_is_partial_and_two_is_established():
    partial = derive_expectation_knowledge_state(expectation_bundle(
        consensus=EvidenceBlock(status=EvidenceAvailability.AVAILABLE,
                                excerpts=[excerpt("A consensus table.")])))
    assert partial.status == ExpectationKnowledgeStatus.PARTIAL
    assert partial.basis == ("CONSENSUS",)
    assert partial.market_expectation_claim_allowed is True
    assert partial.source_ids == (SOURCE_ID,)

    established = derive_expectation_knowledge_state(expectation_bundle(
        consensus=EvidenceBlock(status=EvidenceAvailability.AVAILABLE,
                                excerpts=[excerpt("A consensus table.")]),
        estimate_revisions=EvidenceBlock(status=EvidenceAvailability.AVAILABLE,
                                         excerpts=[excerpt("A revisions table.")])))
    assert established.status == ExpectationKnowledgeStatus.ESTABLISHED
    assert established.basis == ("CONSENSUS", "ESTIMATE_REVISIONS")


def test_a_source_type_that_is_simply_empty_for_this_issuer_is_not_a_basis():
    """NOT_FOUND_FOR_CANDIDATE means "we can read this source and it is empty here". An absence
    cannot be the evidence that the market expects something, so it licenses no claim."""
    state = derive_expectation_knowledge_state(expectation_bundle(
        consensus=EvidenceBlock(status=EvidenceAvailability.NOT_FOUND_FOR_CANDIDATE,
                                note="searched for this issuer, nothing located")))
    assert state.status == ExpectationKnowledgeStatus.UNKNOWN
    assert state.market_expectation_claim_allowed is False


def test_the_state_is_serializable_for_the_record():
    payload = UNKNOWN_STATE.to_dict()
    assert json.loads(json.dumps(payload))["market_expectation_claim_allowed"] is False
    assert payload["status"] == "UNKNOWN"


# --- structured contradiction (brief §9) --------------------------------------------------------

def test_source_not_available_plus_an_asserted_consensus_is_rejected():
    analysis, errors = run(d4_content(
        supporting_claims=[claim("Consensus expects 20% growth.")], sources=[SOURCE_ID]))
    assert analysis is None
    assert any("SOURCE_NOT_AVAILABLE" in e for e in errors)


def test_source_not_available_plus_uncertainty_text_is_allowed():
    analysis, errors = run(d4_content(
        limitations=["The evidence pool settles nothing about how the market is positioned."]))
    assert errors == []
    assert analysis is not None


def test_unknown_state_with_no_numeric_expectation_claim_is_allowed():
    analysis, errors = run(d4_content())
    assert errors == []
    assert analysis is not None
    assert analysis.expectation_gap.value == "UNKNOWN"


def test_a_quantified_expectation_figure_is_named_as_such_in_the_rejection():
    """A repair prompt that says WHICH shape broke converges faster than one that says
    "an attribution" - the D3.1 §I.2 finding, applied to the one case where the number is the
    fabrication."""
    _, errors = run(d4_content(limitations=["Consensus estimates of $5.00 imply flat margins."]))
    assert any("attributes an expectation to analysts or the market by stating a figure" in e
               for e in errors)


def test_reporting_a_status_the_state_does_not_hold_is_still_rejected():
    content = d4_content()
    content["market_expectation_evidence"]["consensus_status"] = "AVAILABLE"
    analysis, errors = run(content)
    assert analysis is None
    assert any("contradicts the code-owned expectation bundle" in e for e in errors)


def test_when_a_source_is_readable_the_prose_guard_has_no_opinion():
    """The prohibition was always a consequence of the bundle's status, never a ban on the word.
    D4-S makes that structural: with a readable source the guard returns nothing at all."""
    available = derive_expectation_knowledge_state(expectation_bundle(
        consensus=EvidenceBlock(status=EvidenceAvailability.AVAILABLE,
                                excerpts=[excerpt("A consensus table.")])))
    assert affirmative_expectation_findings("Consensus expects 20% growth.",
                                            state=available) == []


# --- free prose: what must still be rejected ----------------------------------------------------

@pytest.mark.parametrize("sentence", [
    "The market expects 20% growth.",
    "Market expectations imply stronger margins.",
    "Consensus expects 20%.",
    "Analysts expect EPS of $5.",
    "Wall Street expects a beat.",
    "Investors expect margin expansion.",
    "The market is expecting margin expansion.",
    "Revenue came in above consensus estimates.",
    "Consensus estimates of $5.00 imply flat margins.",
])
def test_an_affirmative_expectation_assertion_is_rejected(sentence: str):
    assert affirmative_expectation_findings(sentence, state=UNKNOWN_STATE)
    assert is_affirmative_expectation_sentence(sentence) is True


def test_negating_the_expectations_content_is_still_an_assertion():
    """The one carve-out that would gut the rule. "does not expect" is a claim ABOUT consensus, and
    `expect` is deliberately absent from the epistemic-predicate list so this cannot pass."""
    assert affirmative_expectation_findings("Consensus does not expect growth.",
                                            state=UNKNOWN_STATE)


def test_an_honest_clause_does_not_license_a_fabrication_beside_it():
    sentence = "It is unclear whether the market expects X, but consensus expects 22%."
    assert affirmative_expectation_findings(sentence, state=UNKNOWN_STATE)


def test_a_negation_that_is_not_about_the_evidence_does_not_rescue_an_assertion():
    """"Without consensus data" contains `without ... consensus`, which the frozen classifier's
    absence list matches, so R2 routes this whole sentence to ABSENCE. The structural rule sees that
    the negation governs nothing epistemic and rejects it - D4-S closes an R2 false NEGATIVE here,
    not only the false positives."""
    sentence = "Without consensus data, it is clear the market expects 20%."
    assert consensus_language.classify_sentence(sentence).verdict == "ABSENCE"
    assert affirmative_expectation_findings(sentence, state=UNKNOWN_STATE)


def test_a_frame_that_merely_trails_the_assertion_does_not_rescue_it():
    sentence = "The market expects 20% growth despite management being silent."
    assert affirmative_expectation_findings(sentence, state=UNKNOWN_STATE)


# --- free prose: what must be allowed, without any phrase being added ---------------------------

@pytest.mark.parametrize("sentence", [
    # The two sentences that actually cost Final Tier A V3 its initial validity (result doc §K).
    "Taken together, the evidence gives no basis for saying market expectations lag the evidenced "
    "progress, and none for saying they run ahead of it.",
    "The non-price expectation evidence consists of capex expectations, backlog disclosures and a "
    "conversion-timing statement that did not change, and none of these shows whether the market's "
    "expectation lags or leads the evidenced progress.",
    # Brief §8's allowed forms.
    "It is unclear whether the market expects X.",
    "The evidence does not establish whether market expectations lag or lead.",
    "There is no basis for determining current market expectations.",
    # Wordings no list in this repository carries, to prove the mechanism rather than the vocabulary.
    "Nothing in the pool is capable of telling us where the market's expectation sits.",
    "The evidence pool is silent on what the market expects.",
    "What the market expects about margins, backlog and capex cannot be observed here.",
    "The market's expectation is not something this evidence could substantiate.",
    "Price alone cannot disentangle whether the market's expectation lags the evidenced progress.",
    "No analyst-consensus or estimate-revision source is connected, so nothing establishes what "
    "the market expects.",
])
def test_denying_knowledge_of_an_expectation_is_allowed_however_it_is_worded(sentence: str):
    assert affirmative_expectation_findings(sentence, state=UNKNOWN_STATE) == []
    assert is_affirmative_expectation_sentence(sentence) is False


@pytest.mark.parametrize("sentence", [
    "The company serves the US market.",
    "Market share increased.",
    "The reported result was above the company's own prior guidance.",
])
def test_legitimate_d4_vocabulary_is_untouched(sentence: str):
    assert affirmative_expectation_findings(sentence, state=UNKNOWN_STATE) == []


def test_the_rejection_message_does_not_demand_a_particular_form_of_words():
    """D4.1's message told the model to write a sentence the same rule then rejected. D4-S's message
    cannot recreate that: it names no approved wording, because there is no longer a list to name."""
    _, errors = run(d4_content(limitations=["Consensus expects 20% growth."]))
    assert errors
    message = errors[0]
    body = message.split("@")[0].replace("Consensus expects 20% growth.", "")
    assert not affirmative_expectation_findings(body, state=UNKNOWN_STATE)
    assert "you do not need to guess an approved form of words" in message


def test_what_the_guard_dropped_is_reported_rather_than_discarded():
    sentence = ("Taken together, the evidence gives no basis for saying market expectations lag "
                "the evidenced progress, and none for saying they run ahead of it.")
    assert consensus_language.classify_sentence(sentence).verdict == "ASSERTED"
    suppressed = suppressed_absence_findings(sentence, state=UNKNOWN_STATE)
    assert [f.sentence for f in suppressed] == [sentence]


# --- the frozen classifier is still frozen ------------------------------------------------------

def test_classify_sentence_keeps_its_r2_verdicts():
    """D4-S reduces the classifier's ROLE, not its behaviour. `attribution_triggers` was extracted
    out of `classify_sentence`, so the verdicts it produced must be untouched."""
    for sentence, expected in (
        ("Consensus expects 20% growth.", "ASSERTED"),
        ("Available evidence does not establish consensus expectations.", "ABSENCE"),
        ("The company serves the US market.", "NEUTRAL"),
        ("Market expectations imply stronger demand.", "ASSERTED"),
        ("There is insufficient evidence to determine what the market expects.", "ABSENCE"),
    ):
        assert consensus_language.classify_sentence(sentence).verdict == expected


def test_the_guard_never_invents_a_trigger_the_frozen_patterns_do_not_carry():
    """The guard can only ever SUBTRACT from what the trigger patterns found. A sentence with no
    attribution at all cannot be rejected by it, whatever its polarity."""
    for sentence in ("Revenue grew 20%.", "The filing was published on 2026-07-01.",
                     "Operating income is DECELERATING."):
        assert consensus_language.attribution_triggers(sentence) == []
        assert affirmative_expectation_findings(sentence, state=UNKNOWN_STATE) == []


def test_the_v1_substring_span_covers_the_whole_word_it_matched():
    """"consensus expect" is a PREFIX of "consensus expectations" - the original D4.1 collision. A
    span that stopped mid-token would leave "ations" looking like a neighbouring content word."""
    triggers = consensus_language.attribution_triggers("consensus expectations unavailable")
    assert triggers
    matched = "consensus expectations unavailable"[triggers[0].start:triggers[0].end]
    assert matched == "consensus expectations"


def test_runtime_and_both_audits_share_one_guard_function():
    """D4.3R's single-source-of-truth property, carried forward onto D4-S's layer. Identity, not
    equal output on today's fixtures, is what this has to mean."""
    assert (validate.affirmative_expectation_findings
            is expectation_state.affirmative_expectation_findings)
    assert (audit_strategy_h_v2_d4_1.affirmative_expectation_findings
            is expectation_state.affirmative_expectation_findings)
    assert (validate.derive_expectation_knowledge_state
            is expectation_state.derive_expectation_knowledge_state)
    assert (audit_strategy_h_v2_d4_1.derive_expectation_knowledge_state
            is expectation_state.derive_expectation_knowledge_state)
