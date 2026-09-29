"""D4.3R M8 repair tests (brief §21). Every fixture here is one of D4.3A's own six false positives
(`H_V2_D4_3A_TIER_A_V2_MECHANICAL_RESULT_V1.md` §M8 diagnosis) or the true/mismatched restatement
pair the brief's §9/§21 ask for directly.
"""

from __future__ import annotations

from app.backtest.strategy_h_v2.expectation.numeric_roles import (
    NumericRole,
    classify_roles,
    fact_is_restated,
)


def _roles(text: str) -> dict[str, str]:
    return {f.raw: f.role.value for f in classify_roles(text)}


# --- D4.3A's own six false positives: none of these restates the cited fact ----------------------

def test_q2_is_a_fiscal_period_not_a_restatement():
    text = "The Q2 print showed nothing unusual for the segment."
    assert fact_is_restated(text, -0.024464, "RETURN_FRACTION") is True
    assert not any(r == "VALUE_RESTATEMENT" for r in _roles(text).values())


def test_reversed_fiscal_period_2q26_is_not_a_restatement():
    text = "Positioning into 2Q26 looks unremarkable."
    roles = _roles(text)
    assert roles.get("2Q26") == NumericRole.FISCAL_PERIOD.value
    assert fact_is_restated(text, -0.010005, "RETURN_FRACTION") is True


def test_session_count_is_not_a_restatement():
    text = "Over 63 sessions the stock modestly outperformed."
    roles = _roles(text)
    assert roles.get("63 sessions") == NumericRole.SESSION_COUNT.value
    assert fact_is_restated(text, -0.090487, "RETURN_FRACTION") is True


def test_hyphenated_session_count_and_rule_id_are_not_restatements():
    """BSY `gap_rationale[3]`: digits '1, 6' were 'C1' (a rule id) and '6-month' (a window length),
    for a fact of -0.303109. Neither is a candidate restatement."""
    text = "Per rule C1 the 6-month positioning view does not change the read."
    roles = _roles(text)
    assert roles.get("C1") == NumericRole.IDENTIFIER.value
    assert roles.get("6-month") == NumericRole.SESSION_COUNT.value
    assert fact_is_restated(text, -0.303109, "RETURN_FRACTION") is True


def test_session_count_and_reversed_fiscal_period_together():
    """SCCO `pre_event_positioning_reading[1]`: digits '20, 2, 26' were '20 sessions' and '2Q26',
    for a fact of -0.010005."""
    text = "The 20 sessions leading into 2Q26 were unremarkable for positioning."
    roles = _roles(text)
    assert roles.get("20 sessions") == NumericRole.SESSION_COUNT.value
    assert roles.get("2Q26") == NumericRole.FISCAL_PERIOD.value
    assert fact_is_restated(text, -0.010005, "RETURN_FRACTION") is True


# --- brief §21's true/mismatched restatement pair, plus §9's bare-fraction form -------------------

def test_true_percentage_restatement_is_accepted():
    text = "The 3-month return was 12.3 percent."
    assert _roles(text).get("12.3") == NumericRole.VALUE_RESTATEMENT.value
    assert fact_is_restated(text, 0.123, "RETURN_FRACTION") is True


def test_mismatched_percentage_restatement_is_rejected():
    text = "The 3-month return was 15.0 percent."
    assert _roles(text).get("15.0") == NumericRole.VALUE_RESTATEMENT.value
    assert fact_is_restated(text, 0.123, "RETURN_FRACTION") is False


def test_bare_fraction_restatement_is_compared():
    """Brief §9: 'return was 0.123' - a contract-allowed bare-fraction representation - normalizes
    and compares rather than being treated as a non-restatement."""
    assert fact_is_restated("Over the window the return was 0.123.", 0.123,
                            "RETURN_FRACTION") is True
    assert fact_is_restated("Over the window the return was 0.150.", 0.123,
                            "RETURN_FRACTION") is False


# --- a genuine numeric-ownership violation must still be caught -----------------------------------

def test_unrelated_bare_numbers_still_flag_a_real_violation():
    """GOOG's real M8 violation (D4.3A §M): a claim citing a code-owned fact while stating unrelated
    bare numbers is still a mismatch, not a purely-qualitative pass - the role fix narrows WHICH
    digits count as candidates, it does not stop comparing the ones that remain candidates."""
    text = "The reaction moved 82 basis points against a prior print of 34."
    assert fact_is_restated(text, -0.090487, "RETURN_FRACTION") is False


def test_purely_qualitative_claim_is_not_a_restatement():
    assert fact_is_restated("The reaction was muted and short-lived.", -0.090487,
                            "RETURN_FRACTION") is True


# --- STATE_TOKEN is untouched by the numeric-role repair ------------------------------------------

def test_state_token_matching_is_unchanged():
    """STATE_TOKEN handling is untouched by the M8 numeric-role repair (it was never the source of
    D4.3A's false positives) and keeps its pre-existing contract exactly: a digit-free sentence
    naming the wrong state is read as qualitative prose, not a restatement, because the whole
    point of this branch is telling a NUMBER mismatch apart from ordinary text - a digit is what
    makes a mismatch detectable at all here."""
    assert fact_is_restated("growth durability is IMPROVING", "IMPROVING", "STATE_TOKEN") is True
    assert fact_is_restated("the tier 2 read shows DETERIORATING durability", "IMPROVING",
                            "STATE_TOKEN") is False
    assert fact_is_restated("durability looks weaker now", "IMPROVING", "STATE_TOKEN") is True


# --- range bounds are excluded, not compared -------------------------------------------------------

def test_range_bounds_are_not_individually_compared_as_restatements():
    text = "Guidance implied 20-30% growth for the segment."
    roles = _roles(text)
    assert roles.get("20") == NumericRole.RANGE.value
    assert roles.get("30") == NumericRole.RANGE.value
