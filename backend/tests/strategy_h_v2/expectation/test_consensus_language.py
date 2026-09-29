"""D4.2 Defect 1: an asserted consensus expectation is rejected, an honest absence is not.

The case that matters most is the first one. D4.1's two live candidates both ended on a rule that
fired on the sentence the contract ordered them to write, so the repair loop had nowhere to go.
Every other test here exists to make sure fixing that did not turn the rule off.
"""

from __future__ import annotations

import json

import pytest
from d4_helpers import D3_OUTPUT, NOW, d4_content, excerpt, expectation_bundle, package

from app.backtest.strategy_h_v2.expectation.code_facts import build_code_fact_index
from app.backtest.strategy_h_v2.expectation.consensus_language import (
    PRESCRIBED_ABSENCE_SENTENCE,
    V1_BANNED_SUBSTRINGS,
    ConsensusVerdict,
    asserts_consensus_expectation,
    classify_sentence,
)
from app.backtest.strategy_h_v2.expectation.validate import assemble_and_validate_d4

PKG = package()
BUNDLE = expectation_bundle()
FACTS = build_code_fact_index(BUNDLE)


def run(content: dict):
    return assemble_and_validate_d4(
        json.dumps(content), package=PKG, bundle=BUNDLE, research_output=D3_OUTPUT,
        code_facts=FACTS, analysis_id="A-1", version=1, model_name="claude-opus-5-5",
        model_version="opus", prompt_version="h_v2_d4_expectation_gap_v2", created_at=NOW,
    )


# --- the sentence D4.1 could not write ---------------------------------------------------------

def test_the_contracts_own_prescribed_sentence_is_accepted():
    """This is the whole defect. V1 scanned for "consensus expect", which is a prefix of
    "consensus expectations", so the §18 remedy sentence tripped the rule it was the remedy for."""
    assert classify_sentence(PRESCRIBED_ABSENCE_SENTENCE).verdict == ConsensusVerdict.ABSENCE
    analysis, errors = run(d4_content(limitations=[PRESCRIBED_ABSENCE_SENTENCE]))
    assert errors == []
    assert analysis is not None


@pytest.mark.parametrize("sentence", [
    "consensus expectations unavailable",
    "Available evidence does not establish consensus expectations.",
    "Consensus data is unavailable.",
    "Analyst expectation evidence is not available from the current source set.",
    "The available evidence does not support a claim about analyst expectations.",
    "No analyst consensus source is connected to this pipeline.",
    "There is no consensus coverage for this issuer in the collected evidence.",
])
def test_stating_that_consensus_evidence_is_absent_is_allowed(sentence: str):
    assert classify_sentence(sentence).verdict == ConsensusVerdict.ABSENCE
    _, errors = run(d4_content(limitations=[sentence]))
    assert errors == []


# --- what stays prohibited ---------------------------------------------------------------------

@pytest.mark.parametrize("sentence", [
    "Consensus expects 20% growth.",
    "Analysts expect EPS of $5.",
    "Wall Street expects margins to rise.",
    "The market consensus assumes a 15% operating margin.",
    "The quarter beat consensus by $0.12.",
    "Revenue missed consensus estimates.",
    "Results came in above consensus.",
    "Analyst estimates of $5.00 per share imply a re-rating.",
    "Investors are pricing a full recovery.",
    "The consensus forecast is for flat margins.",
    "Sell-side analysts see the stock re-rating.",
])
def test_asserting_an_analyst_expectation_is_rejected(sentence: str):
    assert classify_sentence(sentence).verdict == ConsensusVerdict.ASSERTED
    _, errors = run(d4_content(limitations=[sentence]))
    assert any("attributes an expectation to analysts or the market" in e for e in errors)


def test_negating_the_expectations_content_is_still_an_assertion():
    """The carve-out is about the EVIDENCE being unavailable, never about which way an expectation
    points. A blanket negation rule would have admitted this sentence, which asserts a consensus
    view as confidently as its positive form does."""
    assert classify_sentence("Consensus does not expect growth.").verdict == (
        ConsensusVerdict.ASSERTED)


@pytest.mark.parametrize("phrase", V1_BANNED_SUBSTRINGS)
def test_every_v1_banned_phrase_is_still_rejected_in_an_asserting_sentence(phrase: str):
    """V2 discriminates by context; it does not relax. Each of V1's eleven substrings, placed in a
    sentence that asserts rather than disclaims, must still fail."""
    assert asserts_consensus_expectation(f"The filing shows that {phrase} a stronger second half.")


# --- scope and false positives -------------------------------------------------------------------

def test_an_absence_sentence_does_not_license_an_assertion_beside_it():
    """Scope is the sentence. A paragraph that opens honestly and then invents a number is not
    excused by its opening."""
    text = ("Consensus data is unavailable. Analysts expect EPS of $5.")
    assert asserts_consensus_expectation(text)


@pytest.mark.parametrize("sentence", [
    "The disclosure appears partially priced given the muted 3-session reaction.",
    "The market has already reacted strongly to this specific disclosure.",
    "The reported result was above the company's own prior guided range.",
    "Revenue guidance was raised while the production milestone slipped.",
    "The pre-event run-up suggests expectations going in may already have been high.",
    "Management now expects full-year copper output of 917,000 tonnes.",
])
def test_legitimate_d4_vocabulary_is_not_a_consensus_assertion(sentence: str):
    """Priced-in readings, price-reaction readings and the company's OWN guidance are first-class
    D4 content. A consensus rule that flagged them would recreate the D4.1 failure in a new place."""
    assert classify_sentence(sentence).verdict != ConsensusVerdict.ASSERTED


def test_a_decimal_point_is_not_a_sentence_boundary():
    assert asserts_consensus_expectation("Analysts expect revenue of $1.05 billion this year.")


def test_the_rule_only_applies_when_the_bundle_has_no_consensus_source():
    """Unchanged from V1: the prohibition is a consequence of the bundle's own status, not a
    standing ban on the word. This is the one piece of V1 semantics V2 deliberately did not touch."""
    from app.backtest.strategy_h_v2.expectation.evidence_schema import EvidenceBlock
    from app.backtest.strategy_h_v2.expectation.gap_contract import EvidenceAvailability

    available = expectation_bundle(
        consensus=EvidenceBlock(status=EvidenceAvailability.AVAILABLE,
                                excerpts=[excerpt("A consensus table.")]))
    content = d4_content()
    content["market_expectation_evidence"]["consensus_status"] = "AVAILABLE"
    content["limitations"] = ["Analysts expect EPS of $5."]
    _, errors = assemble_and_validate_d4(
        json.dumps(content), package=PKG, bundle=available, research_output=D3_OUTPUT,
        code_facts=build_code_fact_index(available), analysis_id="A-1", version=1,
        model_name="claude-opus-5-5", model_version="opus",
        prompt_version="h_v2_d4_expectation_gap_v2", created_at=NOW,
    )
    assert not any("attributes an expectation" in e for e in errors)


def test_the_rejection_message_does_not_demand_the_sentence_it_rejects():
    """D4.1's error told the model to write "Available evidence does not establish consensus
    expectations." - which the same rule then rejected. The loop could not converge, and that is
    what made the failure structural rather than a model problem."""
    _, errors = run(d4_content(limitations=["Consensus expects 20% growth."]))
    assert errors
    message = errors[0]
    assert not asserts_consensus_expectation(message.split("@")[0].replace(
        "Consensus expects 20% growth.", ""))
