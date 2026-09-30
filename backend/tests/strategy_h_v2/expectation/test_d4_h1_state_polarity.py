"""H-V2-D4-H1 S1: assertion polarity. Frozen before the offline replay ran.

Naming a state token is not asserting it. This file is the contract for telling the three apart, and
its most important test is `test_a_negation_does_not_leak_across_a_coordination` - the brief's §4 -
because the easy version of this repair ("the sentence contains 'not', ignore its states") would make
the gate trivially satisfiable by adding a negation anywhere.
"""

from __future__ import annotations

import pytest

from app.backtest.strategy_h_v2.expectation.state_fidelity import (
    Polarity,
    state_assertions,
    state_occurrences,
)

AUTHORITY = {"revenue": "STABLE", "operating_income": "DECELERATING",
             "eps_diluted": "DETERIORATING", "free_cash_flow": "INFLECTION_NEGATIVE"}


def _polarities(text: str) -> list[tuple[str, str]]:
    return [(a.state, a.polarity.value) for a in state_assertions("root.claim", text, AUTHORITY)]


# --- brief §3: the four worked examples, verbatim -------------------------------------------------

def test_case_asserted():
    assert _polarities("Revenue is accelerating.") == [("ACCELERATING", "ASSERTED")]


def test_case_negated():
    assert _polarities("Revenue is not accelerating.") == [("ACCELERATING", "NEGATED")]


def test_case_contrast_asserts_one_and_rejects_the_other():
    assert _polarities("Revenue is stable rather than accelerating.") == [
        ("STABLE", "ASSERTED"), ("ACCELERATING", "REJECTED_OR_CONTRASTED")]


def test_case_contrast_alone():
    assert _polarities("Mixed rather than improving.") == [
        ("IMPROVING", "REJECTED_OR_CONTRASTED")]


# --- brief §4: no blanket negation exemption -------------------------------------------------------

def test_a_negation_does_not_leak_across_a_coordination():
    """The whole of §4. `and` ends a polarity scope, so the negation binds STABLE and leaves
    ACCELERATING asserted - and the asserted half is then compared and fails against a STABLE
    authority."""
    text = "Revenue is not stable and is accelerating."
    assert _polarities(text) == [("STABLE", "NEGATED"), ("ACCELERATING", "ASSERTED")]
    accelerating = state_assertions("root.claim", text, AUTHORITY)[1]
    assert accelerating.metric == "revenue"
    assert accelerating.status.value == "FAIL"


@pytest.mark.parametrize("text,expected", [
    ("Revenue is not stable, and operating income is decelerating.",
     [("STABLE", "NEGATED"), ("DECELERATING", "ASSERTED")]),
    ("Revenue is not stable while operating income is decelerating.",
     [("STABLE", "NEGATED"), ("DECELERATING", "ASSERTED")]),
    ("Revenue is not stable; operating income is decelerating.",
     [("STABLE", "NEGATED"), ("DECELERATING", "ASSERTED")]),
    ("Revenue is not stable. Operating income is decelerating.",
     [("STABLE", "NEGATED"), ("DECELERATING", "ASSERTED")]),
    ("Revenue is not stable, so operating income is decelerating.",
     [("STABLE", "NEGATED"), ("DECELERATING", "ASSERTED")]),
])
def test_every_clause_boundary_ends_the_negation_scope(text, expected):
    assert _polarities(text) == expected


def test_a_negation_inside_the_same_clause_still_binds():
    """The other direction: the boundaries must not be so eager that a real negation is lost."""
    assert _polarities("Operating income is certainly not accelerating this quarter.") == [
        ("ACCELERATING", "NEGATED")]


# --- mixed polarity for the same token in one text -------------------------------------------------

def test_the_same_token_can_be_denied_in_one_clause_and_asserted_in_another():
    """Why polarity is read per OCCURRENCE and not per distinct state."""
    text = "Revenue is not accelerating, but operating income is accelerating."
    assert _polarities(text) == [("ACCELERATING", "NEGATED"), ("ACCELERATING", "ASSERTED")]
    second = state_assertions("root.claim", text, AUTHORITY)[1]
    assert second.metric == "operating_income" and second.status.value == "FAIL"


def test_occurrences_are_reported_in_document_order():
    text = "Free cash flow is INFLECTION_NEGATIVE while revenue is STABLE."
    assert [s for s, _ in state_occurrences(text)] == ["INFLECTION_NEGATIVE", "STABLE"]


# --- the D4-H corpus sentences this repair was built from ------------------------------------------

@pytest.mark.parametrize("text,state", [
    ("The top-line evidence shows the existing recurring engine continuing at its historical pace, "
     "not accelerating, so there is no material evidenced improvement.", "ACCELERATING"),
    ("The reality side is mixed rather than improving.", "IMPROVING"),
    ("The reality side is mixed rather than uniformly improving.", "IMPROVING"),
    ("Top-line growth is steady rather than improving.", "IMPROVING"),
])
def test_the_three_d4_h_s1_false_positives_are_no_longer_positive_assertions(text, state):
    """`H_V2_D4_H_PRE_TIER_B_INTEGRITY_HARDENING_V1.md` §H findings 1, 2, 4 and 6. Each names a state
    only to deny or contrast it, and V1 counted all of them as restatements."""
    assertions = state_assertions("root.claim", text, AUTHORITY)
    named = [a for a in assertions if a.state == state]
    assert named, f"{state} must still be FOUND - polarity is a reading, not a filter"
    assert all(a.polarity is not Polarity.ASSERTED for a in named)
    assert all(a.status.value == "NOT_EVALUATED" for a in named)
    assert all(a.reason.value == "POLARITY_NOT_ASSERTED" for a in named)


def test_a_contrast_marker_reaches_past_an_adverb():
    """"rather than uniformly improving" - the marker and the token are not adjacent."""
    assert _polarities("mixed rather than uniformly improving") == [
        ("IMPROVING", "REJECTED_OR_CONTRASTED")]


# --- polarity is a classification, never an escape hatch -------------------------------------------

def test_an_ordinary_assertion_beside_a_negated_one_is_still_compared():
    text = "There is no dilution, and revenue is accelerating."
    assertions = state_assertions("root.claim", text, AUTHORITY)
    assert [(a.state, a.polarity.value, a.status.value) for a in assertions] == [
        ("ACCELERATING", "ASSERTED", "FAIL")]


def test_prose_paraphrase_is_not_a_state_token_at_all():
    """"steady", "slowing", "picking up" are not the bare tokens, so they are not restatements to
    hold to the enum. The existing contract asks the model to copy the bare token exactly; a
    paraphrase makes the sentence silent about the code-owned state, not wrong about it."""
    for text in ("Revenue is steady.", "Growth is slowing.", "Margins are picking up."):
        assert state_assertions("root.claim", text, AUTHORITY) == []
