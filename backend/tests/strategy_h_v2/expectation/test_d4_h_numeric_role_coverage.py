"""H-V2-D4-H R1/R2: the numeric-role coverage repair, frozen before the offline replay ran.

R1 removed the `STATE_TOKEN` question from `fact_is_restated` (it has its own gate now -
`test_d4_h_state_fidelity.py`). R2 widened `_SESSION_COUNT` to cover a coordinated window pair whose
unit word is elided: "1- and 3-session", "over 3 and 6 months", "1-, 3-, and 6-month".

The two properties that make these a coverage repair rather than a loosened tolerance are each a
test below: no tolerance moved (`_fact_candidates` is untouched, and a genuine mis-restatement is
still caught at every scale), and no digit whose unit word is a window length could ever have been a
reading of a D4 code fact in the first place, because no code fact is a count of sessions, days,
months or years.
"""

from __future__ import annotations

import pytest

from app.backtest.strategy_h_v2.expectation.numeric_roles import (
    NumericRole,
    classify_roles,
    fact_is_restated,
)

#: SCCO's real `return_1d` and `return_3d` from D4.3A, used so the R2 fixtures are measured against
#: the facts the false positives were actually raised against rather than round numbers.
RETURN_1D = -0.012378378378378296
RETURN_3D = 0.05491891891891898
DRAWDOWN = -0.13573054164770137
RELATIVE_STRENGTH_6M = -0.051475145039365344


def _roles(text: str) -> dict[str, str]:
    return {f.raw: f.role.value for f in classify_roles(text)}


def _value_restatements(text: str) -> list[str]:
    return [f.raw for f in classify_roles(text)
            if f.role == NumericRole.VALUE_RESTATEMENT and f.token is not None]


# --- R1: a state fact is no longer answered by the numeric matcher at all ------------------------

@pytest.mark.parametrize("text", [
    "Fundamentals continued improving on the operating line.",
    "The stock is below its 252-session high, and fundamentals continued improving.",
    "D3 records durable, steady top-line growth.",
    "Per rule C1 the read does not change.",
    "Revenue is ACCELERATING.",
    "Revenue is ACCELERATING over 3 quarters.",
])
def test_m8_no_longer_adjudicates_a_state_token_fact(text):
    """M8 compares numbers. A STATE_TOKEN fact has none, so M8 reports no numeric defect for it -
    with or without a digit in the sentence, and whether the state is right or wrong. Whether the
    state is right is `CODE_OWNED_STATE_FIDELITY`'s question, and `test_d4_h_state_fidelity.py`
    asserts that the last two of these FAIL there."""
    assert fact_is_restated(text, "STABLE", "STATE_TOKEN") is True


def test_r1_the_252_session_case_is_closed():
    """Compound audit case 1: a correct qualitative statement about `operating_income.state`, failed
    only because "252-session" put a digit in the sentence."""
    text = ("The stock is below its 252-session high, and fundamentals continued improving on the "
            "operating line.")
    assert _roles(text)["252-session"] == NumericRole.SESSION_COUNT.value
    assert _value_restatements(text) == []
    assert fact_is_restated(text, "ACCELERATING", "STATE_TOKEN") is True


def test_r1_the_d3_identifier_case_is_closed():
    """Compound audit case 6: the digit was the stage identifier "D3"."""
    text = "D3 records durable, steady top-line growth alongside code-owned DECELERATING income."
    assert _roles(text)["D3"] == NumericRole.IDENTIFIER.value
    assert _value_restatements(text) == []
    assert fact_is_restated(text, "STABLE", "STATE_TOKEN") is True


def test_r1_did_not_touch_the_non_numeric_guard_for_any_other_unit():
    """The branch R1 removed was specific to STATE_TOKEN; the general "a fact with no number has
    nothing to compare" guard it fell through to is unchanged."""
    assert fact_is_restated("anything at all, 123", None, "RETURN_FRACTION") is True
    assert fact_is_restated("anything at all, 123", "NOT_A_NUMBER", "USD") is True


# --- R2: every window-label variant the brief's §6 lists ------------------------------------------

@pytest.mark.parametrize("text,expected_span", [
    ("The 1-session reaction was unremarkable.", "1-session"),
    ("The 3-session window went the other way.", "3-session"),
    ("Across the 1- and 3-session windows the reactions diverge.", "1- and 3-session"),
    ("Relative strength over 3 months is negative.", "3 months"),
    ("Relative strength over 6 months is negative.", "6 months"),
    ("Relative strength over 3 and 6 months is negative.", "3 and 6 months"),
    ("The 1-, 3-, and 6-month windows all point the same way.", "1-, 3-, and 6-month"),
    ("Over 63 sessions the stock modestly outperformed.", "63 sessions"),
    ("Over 63 trading sessions the stock modestly outperformed.", "63 trading sessions"),
    ("Below its 252-session closing high.", "252-session"),
    ("The 3-day reaction faded.", "3-day"),
    ("Over 1 and 3 years the picture is the same.", "1 and 3 years"),
])
def test_every_window_label_variant_is_a_session_count(text, expected_span):
    assert _roles(text)[expected_span] == NumericRole.SESSION_COUNT.value
    assert _value_restatements(text) == []


@pytest.mark.parametrize("fact", [RETURN_1D, RETURN_3D, DRAWDOWN, RELATIVE_STRENGTH_6M])
def test_r2_the_four_matcher_false_positives_are_closed(fact):
    """The two real claim texts behind compound audit cases 2-5, against all four cited facts."""
    price = ("Across the 1- and 3-session windows the 10-Q reactions point in opposite directions, "
             "so no clear reaction can be attributed to the small production guidance increase.")
    gap = ("The shares are below their 252-session closing high, and relative strength over 3 and "
           "6 months is negative.")
    assert _value_restatements(price) == []
    assert _value_restatements(gap) == []
    assert fact_is_restated(price, fact, "RETURN_FRACTION") is True
    assert fact_is_restated(gap, fact, "RETURN_FRACTION") is True


def test_the_elided_and_the_written_out_form_now_agree():
    """The point of R2 in one assertion: the finding used to track English ellipsis rather than
    anything about numeric ownership, so the two spellings of one sentence disagreed."""
    elided = "Across the 1- and 3-session windows the reactions diverge."
    written = "Across the 1-session and 3-session windows the reactions diverge."
    assert _value_restatements(elided) == _value_restatements(written) == []
    for fact in (RETURN_1D, RETURN_3D):
        assert fact_is_restated(elided, fact, "RETURN_FRACTION") is True
        assert fact_is_restated(written, fact, "RETURN_FRACTION") is True


# --- R2 must not swallow a real value that happens to sit next to a window ------------------------

def test_a_coordinator_must_sit_immediately_after_the_number():
    """What stops the window run from absorbing an adjacent real figure: the coordinator has to
    follow the digits directly, so a unit ("%") between them ends the run."""
    text = "Revenue rose 12.3% and 6 months later growth slowed."
    roles = _roles(text)
    assert roles["12.3"] == NumericRole.VALUE_RESTATEMENT.value
    assert roles["6 months"] == NumericRole.SESSION_COUNT.value


@pytest.mark.parametrize("text,fact", [
    ("The 1-session reaction was -1.24%.", RETURN_1D),
    ("Relative strength over 3 and 6 months was -5.15%.", RELATIVE_STRENGTH_6M),
    ("The 1-, 3-, and 6-month returns end at -13.57% from the high.", DRAWDOWN),
])
def test_a_true_restatement_beside_a_window_label_is_still_compared_and_accepted(text, fact):
    assert _value_restatements(text) != [], "the real figure must survive role classification"
    assert fact_is_restated(text, fact, "RETURN_FRACTION") is True


@pytest.mark.parametrize("text,fact", [
    ("The 1-session reaction was -4.10%.", RETURN_1D),
    ("Relative strength over 3 and 6 months was -18.40%.", RELATIVE_STRENGTH_6M),
    ("The 1-, 3-, and 6-month returns end at -31.20% from the high.", DRAWDOWN),
])
def test_a_mismatched_restatement_beside_a_window_label_is_still_caught(text, fact):
    """R2 is coverage, not tolerance. The numbers that ARE readings of the fact are still compared,
    and a wrong one is still a defect."""
    assert fact_is_restated(text, fact, "RETURN_FRACTION") is False


def test_no_code_fact_unit_is_ever_a_window_length():
    """Why masking a window count cannot hide a real restatement: `build_code_fact_index` emits only
    these four units, and none of them is a count of sessions, days, months or years. Asserted
    against the real builder rather than a list, so a new fact unit breaks this test."""
    import inspect

    from app.backtest.strategy_h_v2.expectation import code_facts

    source = inspect.getsource(code_facts.build_code_fact_index)
    emitted = {u for u in ("RETURN_FRACTION", "ANNUALIZED_STDEV", "USD", "STATE_TOKEN")
               if f'"{u}"' in source}
    assert emitted == {"RETURN_FRACTION", "ANNUALIZED_STDEV", "USD", "STATE_TOKEN"}
    for line in source.splitlines():
        for window in ("SESSION_COUNT", "DAY_COUNT", "MONTH_COUNT", "YEAR_COUNT"):
            assert window not in line


# --- the rest of the role classifier is unchanged --------------------------------------------------

def test_a_range_of_window_lengths_is_a_known_residual_not_a_repair():
    """"3-6 months" is a RANGE of window lengths, not an ellipsis, and R2 deliberately did not add
    "-" or "to" as a coordinator - that would conflate two roles the module keeps apart. Pinned so
    the residual is a recorded limitation rather than an assumption."""
    roles = _roles("Relative strength over 3-6 months is negative.")
    assert roles["6 months"] == NumericRole.SESSION_COUNT.value
    assert roles["3"] == NumericRole.VALUE_RESTATEMENT.value


def test_fiscal_period_identifier_and_range_roles_are_untouched():
    assert _roles("Positioning into 2Q26 looks unremarkable.")["2Q26"] == \
        NumericRole.FISCAL_PERIOD.value
    assert _roles("Per rule C1 nothing changes.")["C1"] == NumericRole.IDENTIFIER.value
    ranges = _roles("Guidance implied 20-30% growth for the segment.")
    assert ranges["20"] == ranges["30"] == NumericRole.RANGE.value


def test_an_unrelated_bare_number_is_still_a_violation():
    """D4.3R's own regression guard, restated: R1/R2 narrow WHICH digits are candidates, they do not
    stop comparing the ones that remain."""
    assert fact_is_restated("The reaction moved 82 basis points against a prior print of 34.",
                            -0.090487, "RETURN_FRACTION") is False
