"""D4.3R2: consensus_language's referent list named "consensus", "analysts", "sell-side", "wall
street" and "the street", but not bare "market" - D4.3R's audit-alignment pass (§D) found this and
deliberately left it open as a disclosed, not-yet-authorized gap. "The market expects X." named no
other consensus referent and carried no absence language, so it classified NEUTRAL: a real coverage
miss in the same, unchanged prohibition (D4 brief: analyst/consensus/market expectation cannot be
asserted without a source). This file proves the closure without proving anything new: the same
sentence forms that were always supposed to be prohibited or always supposed to be allowed keep
their intended verdict, and the vocabulary this closure genuinely adds is the only thing that moves.
"""

from __future__ import annotations

import json

import pytest
from d4_helpers import D3_OUTPUT, NOW, d4_content, expectation_bundle, package

from app.backtest.strategy_h_v2.expectation.code_facts import build_code_fact_index
from app.backtest.strategy_h_v2.expectation.consensus_language import (
    ConsensusVerdict,
    asserted_consensus_findings,
    classify_sentence,
)
from app.backtest.strategy_h_v2.expectation import consensus_language, validate
from app.backtest.strategy_h_v2.expectation.validate import assemble_and_validate_d4
from app.dev import audit_strategy_h_v2_d4_1

PKG = package()
BUNDLE = expectation_bundle()
FACTS = build_code_fact_index(BUNDLE)


def run(content: dict):
    return assemble_and_validate_d4(
        json.dumps(content), package=PKG, bundle=BUNDLE, research_output=D3_OUTPUT,
        code_facts=FACTS, analysis_id="A-1", version=1, model_name="claude-opus-5-5",
        model_version="opus", prompt_version="h_v2_d4_expectation_gap_v2", created_at=NOW,
    )


# --- newly closed: an asserted market expectation ------------------------------------------------

@pytest.mark.parametrize("sentence", [
    "The market expects 20% growth.",
    "The market is expecting stronger margins.",
    "Market expectations imply stronger demand.",
    "The market is expecting EPS of $5.",
    "The market expects commercialization next year.",
])
def test_asserting_a_market_expectation_is_now_rejected(sentence: str):
    assert classify_sentence(sentence).verdict == ConsensusVerdict.ASSERTED
    _, errors = run(d4_content(limitations=[sentence]))
    assert any("attributes an expectation to analysts or the market" in e for e in errors)


# --- must stay allowed: honest absence, phrased with "market" -------------------------------------

@pytest.mark.parametrize("sentence", [
    "Available evidence does not establish what the market expects.",
    "Market expectation evidence is unavailable.",
    "The current source set does not establish market expectations.",
    "There is insufficient evidence to determine what the market expects.",
])
def test_an_absence_statement_naming_the_market_is_still_allowed(sentence: str):
    """The closure must not regress into the exact failure D4.3R's audit-alignment pass fixed once
    already: an honest "no evidence" sentence read as an assertion because it happens to share
    vocabulary with one."""
    assert classify_sentence(sentence).verdict == ConsensusVerdict.ABSENCE
    _, errors = run(d4_content(limitations=[sentence]))
    assert errors == []


# --- must stay allowed: "market" used with no expectation attached --------------------------------

@pytest.mark.parametrize("sentence", [
    "The company serves the US market.",
    "Market share increased.",
    "The addressable market was described as $500 million.",
    "Market conditions weakened.",
])
def test_a_neutral_use_of_market_is_not_a_consensus_assertion(sentence: str):
    assert classify_sentence(sentence).verdict == ConsensusVerdict.NEUTRAL
    _, errors = run(d4_content(limitations=[sentence]))
    assert errors == []


# --- existing V2 contract must survive the closure untouched --------------------------------------

def test_the_existing_prescribed_absence_sentence_is_still_accepted():
    assert classify_sentence(
        "Available evidence does not establish consensus expectations."
    ).verdict == ConsensusVerdict.ABSENCE


@pytest.mark.parametrize("sentence", [
    "Consensus expects 20% growth.",
    "Analysts expect EPS of $5.",
    "Wall Street expects margins to rise.",
    "The quarter beat consensus by $0.12.",
])
def test_existing_non_market_assertions_are_still_rejected(sentence: str):
    assert classify_sentence(sentence).verdict == ConsensusVerdict.ASSERTED


@pytest.mark.parametrize("sentence", [
    "The disclosure appears partially priced given the muted 3-session reaction.",
    "The market has already reacted strongly to this specific disclosure.",
    "The reported result was above the company's own prior guided range.",
    "Management now expects full-year copper output of 917,000 tonnes.",
])
def test_legitimate_d4_vocabulary_naming_the_market_is_still_not_an_assertion(sentence: str):
    """"The market" appears in D4's own approved price-reaction vocabulary (priced-in readings). The
    closure must not turn every sentence that happens to say "market" into a rule violation."""
    assert classify_sentence(sentence).verdict != ConsensusVerdict.ASSERTED


# --- single source of truth: production and audit must never diverge ------------------------------

def test_production_and_audit_share_the_identical_classifier_function():
    """D4.3R (§D) unified the audit's E3/M4 detector onto `asserted_consensus_findings` directly,
    the same function `validate.check_consensus_not_fabricated` calls, instead of a second regex
    list. This closure adds vocabulary to `consensus_language.py` only; it must not grow a second
    call site anywhere that could drift from it. Identity, not merely equal output on today's
    fixtures, is what "single source of truth" (brief §2) means."""
    assert (validate.asserted_consensus_findings is consensus_language.asserted_consensus_findings)
    assert (audit_strategy_h_v2_d4_1.asserted_consensus_findings
            is consensus_language.asserted_consensus_findings)


@pytest.mark.parametrize("sentence", [
    "The market expects 20% growth.",
    "Market expectations imply stronger demand.",
    "There is insufficient evidence to determine what the market expects.",
    "The company serves the US market.",
    "Consensus expects 20% growth.",
    "Available evidence does not establish consensus expectations.",
])
def test_asserted_consensus_findings_matches_classify_sentence(sentence: str):
    """Both public entry points - `classify_sentence` (validate.py's per-sentence use) and
    `asserted_consensus_findings` (the audit's bulk-prose use) - must agree, since they are built
    from the same `split_sentences` + `classify_sentence` pipeline."""
    production_verdict = classify_sentence(sentence).verdict == ConsensusVerdict.ASSERTED
    assert production_verdict == bool(asserted_consensus_findings(sentence))
