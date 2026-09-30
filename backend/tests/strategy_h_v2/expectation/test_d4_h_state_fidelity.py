"""H-V2-D4-H: the `CODE_OWNED_STATE_FIDELITY` contract, frozen before the offline replay ran.

Every case in the D4-H brief's §9 and §11 is here as its own test, plus the §14 injected regression.
The five §9 cases are the whole contract; the rest of this file exists so that the two properties
the brief names explicitly cannot regress silently:

  the result must not depend on whether the sentence contains a number
  zero eligible claims is NOT_EVALUATED and is never counted as a PASS
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


def _state_fact(metric: str, state: str) -> CodeFact:
    path = f"research_facts.fundamental_changes.{metric}.state"
    return CodeFact(f"CODE:D4:BUNDLE:CHUNK:{path}", path, state, "STATE_TOKEN",
                    f"code-owned D1/D2 fundamental change state for {metric}")


def _return_fact(value: float) -> CodeFact:
    path = "pre_event_price_context.return_3m"
    return CodeFact(f"CODE:D4:BUNDLE:CHUNK:{path}", path, value, "RETURN_FRACTION",
                    "candidate return over the last 63 sessions")


def _finding(text: str, *facts: CodeFact):
    return claim_state_fidelity(PATH, text, list(facts))


def _status(text: str, *facts: CodeFact) -> MechanicalGateStatus:
    finding = _finding(text, *facts)
    return state_fidelity_status([finding] if finding is not None else [])


# --- brief §9: the five cases, verbatim ----------------------------------------------------------

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
    """The bare token written as ordinary prose is the same token. §10 forbids a SYNONYM, not a
    lowercase rendering, and the brief's own example spells this case out."""
    assert _status("Revenue is accelerating.", _state_fact("revenue", "ACCELERATING")) == \
        MechanicalGateStatus.PASS


def test_case_5_no_state_restatement_is_not_evaluated():
    finding = _finding("The reaction was muted and short-lived.",
                       _state_fact("revenue", "DECELERATING"))
    assert finding is None
    assert _status("The reaction was muted and short-lived.",
                   _state_fact("revenue", "DECELERATING")) == MechanicalGateStatus.NOT_EVALUATED


# --- the property the brief states twice: numbers are irrelevant to this gate --------------------

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


# --- brief §11: eligibility, and the zero that is not a PASS -------------------------------------

def test_zero_eligible_is_not_evaluated_not_pass():
    report = state_fidelity_report([])
    assert report["gate"] == GATE_ID
    assert report["status"] == MechanicalGateStatus.NOT_EVALUATED.value
    assert report["eligible"] == 0 and report["violations"] == 0
    assert report["status"] != MechanicalGateStatus.PASS.value


def test_a_claim_citing_no_state_fact_is_not_eligible():
    """Ordinary English uses these words. A claim that cites only a return and says "growth is
    improving" is restating nothing code-owned, and flagging it would manufacture the false-positive
    class D4.3R spent a whole stage removing."""
    assert _finding("Growth is improving on the operating line.", _return_fact(-0.0905)) is None
    assert _finding("Growth is improving on the operating line.") is None


def test_a_state_fact_cited_alongside_numeric_facts_is_still_eligible():
    finding = _finding("Revenue is ACCELERATING and the 3-month return was -9.05%.",
                       _return_fact(-0.090487), _state_fact("revenue", "STABLE"))
    assert finding is not None
    assert finding.owned == ("STABLE",)
    assert finding.violated


# --- set containment, and what it deliberately does not claim ------------------------------------

def test_a_compound_claim_naming_a_subset_of_its_cited_states_passes():
    """The shape `H_V2_D4_S_M8_COMPOUND_COVERAGE_AUDIT_V1.md`'s case 6 has: four state facts cited,
    three named as bare tokens, the fourth described in prose. Nothing unowned was introduced, so
    containment passes - which is how this gate stays out of M8's deferred R3 problem instead of
    reproducing it one gate over."""
    text = ("The reality side is mixed: D3 records durable, steady top-line growth alongside "
            "code-owned DECELERATING operating income, DETERIORATING EPS and INFLECTION_NEGATIVE "
            "free cash flow.")
    finding = _finding(text, _state_fact("revenue", "STABLE"),
                       _state_fact("operating_income", "DECELERATING"),
                       _state_fact("eps", "DETERIORATING"),
                       _state_fact("free_cash_flow", "INFLECTION_NEGATIVE"))
    assert finding is not None
    assert finding.named == ("DECELERATING", "DETERIORATING", "INFLECTION_NEGATIVE")
    assert finding.unowned == ()
    assert state_fidelity_status([finding]) == MechanicalGateStatus.PASS


def test_one_unowned_state_among_owned_ones_still_fails():
    text = "Operating income is DECELERATING and revenue is ACCELERATING."
    finding = _finding(text, _state_fact("revenue", "STABLE"),
                       _state_fact("operating_income", "DECELERATING"))
    assert finding.unowned == ("ACCELERATING",)
    assert state_fidelity_status([finding]) == MechanicalGateStatus.FAIL


def test_known_limitation_containment_does_not_bind_a_state_to_a_metric():
    """Declared, not discovered. A claim that swaps which cited metric holds which cited state
    passes containment. Binding a token to a metric needs per-metric attribution over prose, the
    same unit-of-comparison change M8's R3 is deferred for, and this gate does not attempt it."""
    text = "Revenue is DECELERATING and operating income is STABLE."
    finding = _finding(text, _state_fact("revenue", "STABLE"),
                       _state_fact("operating_income", "DECELERATING"))
    assert finding.unowned == ()
    assert state_fidelity_status([finding]) == MechanicalGateStatus.PASS


# --- brief §10: the enum is read from the authoritative source, and is not extended ---------------

def test_the_recognized_states_are_exactly_the_code_owned_enum_minus_unknown():
    assert RECOGNIZED_STATES == frozenset(s.value for s in ChangeState) - NOT_NAMED_IN_PROSE
    assert NOT_NAMED_IN_PROSE == frozenset({ChangeState.UNKNOWN.value})
    assert "UNKNOWN" not in RECOGNIZED_STATES


def test_unknown_in_prose_is_not_read_as_a_restatement():
    """`unknown` is the contract's own honesty marker (`unknown_fields`, the UNKNOWN claim type,
    "the driver is unknown"). Reading it as a code-owned state would turn that discipline into a
    defect."""
    assert named_states("The driver is unknown and not disclosed.") == set()
    assert _finding("The driver is unknown.", _state_fact("revenue", "STABLE")) is None


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
    """The separator is the only latitude §10 allows, because those are renderings of one token."""
    assert named_states(f"Free cash flow is {rendering}.") == {"INFLECTION_NEGATIVE"}


def test_no_recognized_state_is_a_substring_of_another():
    """What `_STATE_PATTERNS`' longest-first ordering is insurance against. If this ever fails, a
    shorter member could be reported in place of the longer one that was actually written."""
    for a in RECOGNIZED_STATES:
        for b in RECOGNIZED_STATES:
            assert a == b or a not in b


def test_owned_states_ignores_facts_that_have_no_state():
    assert owned_states([_return_fact(0.123)]) == set()
    assert owned_states([_return_fact(0.123), _state_fact("revenue", "STABLE")]) == {"STABLE"}


# --- brief §14: the injected regression ----------------------------------------------------------

def test_injected_regression_stable_to_accelerating_fails():
    """§14's acceptance criterion, stated as its own test so it is impossible to satisfy the gate
    while failing it."""
    fact = _state_fact("revenue", "STABLE")
    clean = "Revenue is STABLE, and management gave no new quantitative target."
    injected = clean.replace("STABLE", "ACCELERATING")
    assert _status(clean, fact) == MechanicalGateStatus.PASS
    assert _status(injected, fact) == MechanicalGateStatus.FAIL


def test_the_report_carries_its_denominator_beside_its_numerator():
    fact = _state_fact("revenue", "STABLE")
    findings = [_finding("Revenue is STABLE.", fact),
                _finding("Revenue is ACCELERATING.", fact)]
    report = state_fidelity_report(findings)
    assert report["eligible"] == 2 and report["violations"] == 1
    assert report["status"] == MechanicalGateStatus.FAIL.value
    assert [c["unowned"] for c in report["violating_claims"]] == [["ACCELERATING"]]
