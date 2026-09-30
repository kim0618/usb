"""H-V2-D4-H: the `CODE_OWNED_STATE_FIDELITY` cases, carried forward to the D4-H1 contract.

Every fixture the D4-H version of this file froze is kept, and every test that changed says what it
used to assert. What changed is the contract underneath them (`H_V2_D4_H1_STATE_FIDELITY_SEMANTIC_
REPAIR_V1.md`):

    D4-H V1   per CLAIM, set containment: is every state token NAMED owned by a state fact CITED?
    D4-H1 V2  per ASSERTION: polarity, then metric binding, then comparison against the CANDIDATE's
              authoritative state for that metric.

The five §9 cases of the D4-H brief and the injected regression are unchanged in outcome, which is the
point of keeping them here: the semantic repair was allowed to change WHY a case lands where it lands,
never the five cases the gate was preregistered on. `test_d4_h1_state_polarity.py` and
`test_d4_h1_metric_authority.py` hold the new contract's own cases.
"""

from __future__ import annotations

import pytest

from app.backtest.strategy_h_v2.change_detection import ChangeState
from app.backtest.strategy_h_v2.expectation.code_facts import CodeFact
from app.backtest.strategy_h_v2.expectation.d4_2_contract import MechanicalGateStatus
from app.backtest.strategy_h_v2.expectation.state_fidelity import (
    GATE_ID,
    NOT_NAMED_IN_PROSE,
    RECOGNIZED_STATES,
    claim_state_fidelity,
    named_states,
    owned_states,
    state_fidelity_report,
    state_fidelity_status,
)

PATH = "root.gap_rationale[0]"

#: BSY's real code-owned states, which is where four of D4-H's six findings came from.
BSY = {"revenue": "STABLE", "operating_income": "DECELERATING", "eps_diluted": "DETERIORATING",
       "free_cash_flow": "INFLECTION_NEGATIVE", "operating_margin": "STABLE",
       "shares_outstanding": "UNKNOWN"}


def _state_fact(metric: str, state: str) -> CodeFact:
    path = f"research_facts.fundamental_changes.{metric}.state"
    return CodeFact(f"CODE:D4:BUNDLE:CHUNK:{path}", path, state, "STATE_TOKEN",
                    f"code-owned D1/D2 fundamental change state for {metric}")


def _return_fact(value: float) -> CodeFact:
    path = "pre_event_price_context.return_3m"
    return CodeFact(f"CODE:D4:BUNDLE:CHUNK:{path}", path, value, "RETURN_FRACTION",
                    "candidate return over the last 63 sessions")


def _finding(text: str, *facts: CodeFact, authority: dict[str, str] | None = None):
    return claim_state_fidelity(PATH, text, list(facts), BSY if authority is None else authority)


def _status(text: str, *facts: CodeFact,
            authority: dict[str, str] | None = None) -> MechanicalGateStatus:
    finding = _finding(text, *facts, authority=authority)
    return state_fidelity_status([finding] if finding is not None else [])


# --- the D4-H brief's §9: the five cases, verbatim, unchanged in outcome ---------------------------
#
# Written against `revenue` so that the metric binding the H1 contract requires is present. The D4-H
# versions said "Revenue is ..." already, so the fixtures themselves did not have to move.

def test_case_1_correct_state_passes():
    assert _status("Revenue is STABLE.", _state_fact("revenue", "STABLE")) == \
        MechanicalGateStatus.PASS


def test_case_2_wrong_state_fails():
    assert _status("Revenue is ACCELERATING.", _state_fact("revenue", "STABLE")) == \
        MechanicalGateStatus.FAIL


def test_case_3_wrong_state_with_a_number_still_fails():
    assert _status("Revenue is ACCELERATING over 3 quarters.",
                   _state_fact("revenue", "STABLE")) == MechanicalGateStatus.FAIL


def test_case_4_lowercase_restatement_of_the_right_state_passes():
    """The bare token written as ordinary prose is the same token. A lowercase rendering counts; a
    SYNONYM does not."""
    assert _status("Revenue is accelerating.", _state_fact("revenue", "ACCELERATING"),
                   authority={"revenue": "ACCELERATING"}) == MechanicalGateStatus.PASS


def test_case_5_no_state_restatement_is_not_evaluated():
    finding = _finding("The reaction was muted and short-lived.",
                       _state_fact("revenue", "DECELERATING"))
    assert finding is None
    assert _status("The reaction was muted and short-lived.",
                   _state_fact("revenue", "DECELERATING")) == MechanicalGateStatus.NOT_EVALUATED


# --- the property the D4-H brief states twice: numbers are irrelevant to this gate ----------------

@pytest.mark.parametrize("suffix", [
    "", " over 3 quarters.", " Below its 252-session closing high.", " Per rule C1.",
    " The 3-session reaction was -1.24%.", " Relative strength over 3 and 6 months is negative.",
])
def test_the_verdict_never_depends_on_an_unrelated_number(suffix):
    """R1's whole subject. The identical state claim, with arbitrary numeric content appended, keeps
    its verdict - which is exactly what the old `STATE_TOKEN` branch could not do."""
    fact = _state_fact("revenue", "STABLE")
    assert _status("Revenue is STABLE." + suffix, fact) == MechanicalGateStatus.PASS
    assert _status("Revenue is ACCELERATING." + suffix, fact) == MechanicalGateStatus.FAIL


# --- eligibility, and the zero that is not a PASS --------------------------------------------------

def test_zero_comparable_assertions_is_not_evaluated_not_pass():
    """Was `test_zero_eligible_is_not_evaluated_not_pass`. The unit moved from the claim to the
    assertion; the rule that a zero denominator is NOT_EVALUATED did not."""
    report = state_fidelity_report([])
    assert report["gate"] == GATE_ID
    assert report["status"] == MechanicalGateStatus.NOT_EVALUATED.value
    assert report["assertions_evaluated"] == 0 and report["assertions_failed"] == 0
    assert report["status"] != MechanicalGateStatus.PASS.value


def test_ordinary_english_about_no_particular_metric_is_not_evaluated():
    """Was `test_a_claim_citing_no_state_fact_is_not_eligible`, which used the CITATION as the filter
    that kept ordinary English out. H1 §6 took that filter away - provenance is not authority - so
    the protection now comes from where it belongs: "growth is improving" names no code-owned metric,
    so there is nothing to compare it to, with or without a state fact cited."""
    for facts in ([_return_fact(-0.0905)], [], [_state_fact("revenue", "STABLE")]):
        finding = _finding("Growth is improving on the operating line.", *facts)
        assert finding is not None, "the occurrence is still SEEN"
        assert [a.reason.value for a in finding.assertions] == ["NO_METRIC_NAMED"]
        assert state_fidelity_status([finding]) == MechanicalGateStatus.NOT_EVALUATED


def test_a_state_assertion_beside_numeric_facts_is_still_compared():
    """Was `test_a_state_fact_cited_alongside_numeric_facts_is_still_eligible`."""
    finding = _finding("Revenue is ACCELERATING and the 3-month return was -9.05%.",
                       _return_fact(-0.090487), _state_fact("revenue", "STABLE"))
    assert finding is not None
    assert [a.authoritative_state for a in finding.violations] == ["STABLE"]
    assert finding.violated


# --- the compound corpus claim, and the limitation that is now closed ------------------------------

def test_a_compound_claim_restating_several_metrics_passes_on_each_of_them():
    """Was `test_a_compound_claim_naming_a_subset_of_its_cited_states_passes`, which passed by SET
    CONTAINMENT: nothing unowned was introduced, and the silent fourth metric was ignored.

    The fixture is D4-H's finding 6, BSY's Final Tier A V3 `gap_rationale[2]`. Under H1 it passes for
    a stronger reason - each bare token is bound to its own metric and compared to that metric's own
    authoritative state - and the claim's citations no longer enter into it at all.
    """
    text = ("The reality side is mixed: D3 records durable, steady top-line growth alongside "
            "code-owned DECELERATING operating income, DETERIORATING EPS and INFLECTION_NEGATIVE "
            "free cash flow.")
    finding = _finding(text, _state_fact("revenue", "STABLE"),
                       _state_fact("free_cash_flow", "INFLECTION_NEGATIVE"))
    assert finding is not None
    assert [(a.state, a.metric, a.status.value) for a in finding.assertions] == [
        ("DECELERATING", "operating_income", "PASS"),
        ("DETERIORATING", "eps_diluted", "PASS"),
        ("INFLECTION_NEGATIVE", "free_cash_flow", "PASS")]
    assert state_fidelity_status([finding]) == MechanicalGateStatus.PASS


def test_a_wrong_state_among_correct_ones_still_fails():
    """Was `test_one_unowned_state_among_owned_ones_still_fails`. Same outcome, now decided per
    metric rather than by membership in the cited set."""
    text = "Operating income is DECELERATING and revenue is ACCELERATING."
    finding = _finding(text, _state_fact("revenue", "STABLE"),
                       _state_fact("operating_income", "DECELERATING"))
    assert [(a.metric, a.status.value) for a in finding.assertions] == [
        ("operating_income", "PASS"), ("revenue", "FAIL")]
    assert state_fidelity_status([finding]) == MechanicalGateStatus.FAIL


def test_closed_limitation_a_metric_swap_is_now_caught():
    """Was `test_known_limitation_containment_does_not_bind_a_state_to_a_metric`, which asserted that
    this claim PASSES - a declared hole in V1's set containment, since both states named were in the
    cited set whichever metric held which. H1's metric binding closes it, and this test is kept
    inverted rather than deleted so the closure is visible where the limitation was recorded."""
    text = "Revenue is DECELERATING and operating income is STABLE."
    finding = _finding(text, _state_fact("revenue", "STABLE"),
                       _state_fact("operating_income", "DECELERATING"))
    assert [(a.metric, a.authoritative_state, a.status.value) for a in finding.assertions] == [
        ("revenue", "STABLE", "FAIL"), ("operating_income", "DECELERATING", "FAIL")]
    assert state_fidelity_status([finding]) == MechanicalGateStatus.FAIL


# --- the enum is read from the authoritative source, and is not extended ---------------------------

def test_the_recognized_states_are_exactly_the_code_owned_enum_minus_unknown():
    assert RECOGNIZED_STATES == frozenset(s.value for s in ChangeState) - NOT_NAMED_IN_PROSE
    assert NOT_NAMED_IN_PROSE == frozenset({ChangeState.UNKNOWN.value})
    assert "UNKNOWN" not in RECOGNIZED_STATES


def test_unknown_in_prose_is_not_read_as_a_restatement():
    """`unknown` is the contract's own honesty marker (`unknown_fields`, the UNKNOWN claim type,
    "the driver is unknown"). Reading it as a code-owned state would turn that discipline into a
    defect."""
    assert named_states("The driver is unknown and not disclosed.") == set()
    assert _finding("The revenue driver is unknown.", _state_fact("revenue", "STABLE")) is None


def test_no_state_outside_the_enum_is_ever_recognized():
    for invented in ("REACCELERATING", "SLOWING", "FLAT", "MIXED", "APPROVE"):
        assert named_states(f"Revenue is {invented}.") == set()


def test_a_state_token_is_matched_as_a_whole_word_only():
    assert named_states("the stableness of the margin") == set()
    assert named_states("margins were stable") == {"STABLE"}


@pytest.mark.parametrize("rendering", [
    "INFLECTION_NEGATIVE", "inflection_negative", "inflection negative", "Inflection-Negative",
])
def test_a_two_part_token_may_be_written_with_underscore_space_or_hyphen(rendering):
    """The separator is the only latitude, because those are renderings of one token."""
    assert named_states(f"Free cash flow is {rendering}.") == {"INFLECTION_NEGATIVE"}


def test_no_recognized_state_is_a_substring_of_another():
    """What `_STATE_PATTERNS`' longest-first ordering is insurance against. If this ever fails, a
    shorter member could be reported in place of the longer one that was actually written."""
    for a in RECOGNIZED_STATES:
        for b in RECOGNIZED_STATES:
            assert a == b or a not in b


def test_owned_states_ignores_facts_that_have_no_state():
    """`owned_states` is no longer the comparison authority (H1 §6) but is still how D4-H's
    claim-level denominator is computed, so it keeps its own test."""
    assert owned_states([_return_fact(0.123)]) == set()
    assert owned_states([_return_fact(0.123), _state_fact("revenue", "STABLE")]) == {"STABLE"}


# --- the injected regression -----------------------------------------------------------------------

def test_injected_regression_stable_to_accelerating_fails():
    """The D4-H brief's §14 criterion and the H1 brief's §10, stated as its own test so it is
    impossible to satisfy the gate while failing it."""
    fact = _state_fact("revenue", "STABLE")
    clean = "Revenue is STABLE, and management gave no new quantitative target."
    injected = clean.replace("STABLE", "ACCELERATING")
    assert _status(clean, fact) == MechanicalGateStatus.PASS
    assert _status(injected, fact) == MechanicalGateStatus.FAIL


def test_the_report_carries_its_denominators_beside_its_numerator():
    """Was `test_the_report_carries_its_denominator_beside_its_numerator`. There are two denominators
    now - see `test_d4_h1_metric_authority.py::test_the_report_keeps_both_denominators_separate`."""
    fact = _state_fact("revenue", "STABLE")
    findings = [_finding("Revenue is STABLE.", fact),
                _finding("Revenue is ACCELERATING.", fact)]
    report = state_fidelity_report(findings)
    assert report["assertions_evaluated"] == 2 and report["assertions_failed"] == 1
    assert report["d4_h_eligible_claims"] == 2 and report["d4_h_violating_claims"] == 1
    assert report["status"] == MechanicalGateStatus.FAIL.value
    assert [a["state"] for f in report["violating_claims"] for a in f["assertions"]
            if a["status"] == "FAIL"] == ["ACCELERATING"]
