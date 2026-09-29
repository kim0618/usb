"""The frozen contract's own rules. These tests are the record that C1-C7 were decided before any
D4 output existed - if a rule here is ever changed to make a run pass, this file changes with it
and the change is visible in the diff."""

from __future__ import annotations

from app.backtest.strategy_h_v2.expectation.gap_contract import (
    C3_UNRESOLVED_MATERIAL_CONFLICT_CEILING,
    C4_NO_CONSENSUS_CONFIDENCE_CEILING,
    ContractRule,
    D6ApprovePrecondition,
    ExpectationGapState,
    NEGATIVE_STATES,
    POSITIVE_STATES,
    apply_confidence_ceiling,
    confidence_ceiling,
    d6_approve_precondition,
)
from app.backtest.strategy_h_v2.research.schema import Confidence


def test_the_frozen_states_are_exactly_d0_section_l():
    assert {s.value for s in ExpectationGapState} == {
        "WIDE_POSITIVE", "POSITIVE", "NEUTRAL", "NEGATIVE", "WIDE_NEGATIVE", "UNKNOWN",
    }


def test_positive_and_negative_sets_do_not_overlap_and_exclude_neutral():
    assert not (POSITIVE_STATES & NEGATIVE_STATES)
    assert ExpectationGapState.NEUTRAL not in POSITIVE_STATES | NEGATIVE_STATES


def test_no_consensus_caps_confidence_at_medium():
    ceiling, rules = confidence_ceiling(
        consensus_available=False, estimate_revisions_available=False,
        has_unresolved_material_conflict=False,
    )
    assert ceiling == C4_NO_CONSENSUS_CONFIDENCE_CEILING == Confidence.MEDIUM
    assert ContractRule.C4_NO_CONSENSUS_CONFIDENCE_CEILING in rules


def test_a_connected_consensus_source_would_lift_the_c4_ceiling():
    """Measured today as unreachable (consensus is UNKNOWN for 2,010 of 2,010 candidates), but the
    rule is about the data, not about the number - so connecting a provider must lift it."""
    ceiling, rules = confidence_ceiling(
        consensus_available=True, estimate_revisions_available=False,
        has_unresolved_material_conflict=False,
    )
    assert ceiling == Confidence.HIGH
    assert rules == ()


def test_a_material_unresolved_conflict_caps_confidence_even_with_consensus():
    ceiling, rules = confidence_ceiling(
        consensus_available=True, estimate_revisions_available=True,
        has_unresolved_material_conflict=True,
    )
    assert ceiling == C3_UNRESOLVED_MATERIAL_CONFLICT_CEILING
    assert ContractRule.C3_UNRESOLVED_MATERIAL_CONFLICT_CEILING in rules


def test_both_ceilings_firing_reports_both_rules():
    _, rules = confidence_ceiling(
        consensus_available=False, estimate_revisions_available=False,
        has_unresolved_material_conflict=True,
    )
    assert set(rules) == {ContractRule.C3_UNRESOLVED_MATERIAL_CONFLICT_CEILING,
                          ContractRule.C4_NO_CONSENSUS_CONFIDENCE_CEILING}


def test_a_ceiling_only_ever_lowers_a_confidence():
    assert apply_confidence_ceiling(Confidence.HIGH, Confidence.MEDIUM) == Confidence.MEDIUM
    assert apply_confidence_ceiling(Confidence.LOW, Confidence.MEDIUM) == Confidence.LOW
    assert apply_confidence_ceiling(Confidence.LOW, Confidence.HIGH) == Confidence.LOW


def test_unknown_confidence_is_a_floor_no_ceiling_promotes_it():
    for ceiling in Confidence:
        assert apply_confidence_ceiling(Confidence.UNKNOWN, ceiling) == Confidence.UNKNOWN


def test_d6_precondition_follows_d0_section_l_exactly():
    for state in POSITIVE_STATES:
        for confidence in (Confidence.HIGH, Confidence.MEDIUM):
            assert d6_approve_precondition(state, confidence) == D6ApprovePrecondition.SATISFIED
        for confidence in (Confidence.LOW, Confidence.UNKNOWN):
            assert d6_approve_precondition(state, confidence) == D6ApprovePrecondition.BLOCKED


def test_a_non_positive_gap_blocks_the_precondition_at_every_confidence():
    for state in (ExpectationGapState.NEUTRAL, ExpectationGapState.NEGATIVE,
                  ExpectationGapState.WIDE_NEGATIVE, ExpectationGapState.UNKNOWN):
        for confidence in Confidence:
            assert d6_approve_precondition(state, confidence) == D6ApprovePrecondition.BLOCKED


def test_the_precondition_enum_contains_no_decision_vocabulary():
    """D4 produces no decision. The precondition record exists so D6 can check that D0's frozen
    rule was applied, and its values must not read as one."""
    assert {v.value for v in D6ApprovePrecondition} == {"SATISFIED", "BLOCKED"}
    assert "APPROVE" not in {v.value for v in D6ApprovePrecondition}
