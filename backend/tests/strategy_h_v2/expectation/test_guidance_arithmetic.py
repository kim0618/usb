"""Guidance arithmetic: the code side of the code/AI split. Every case here is a comparison the AI
is forbidden to make, so a wrong answer here silently licenses a wrong AI state downstream."""

from __future__ import annotations

import pytest

from app.backtest.strategy_h_v2.expectation.gap_contract import GuidanceState
from app.backtest.strategy_h_v2.expectation.guidance_arithmetic import (
    GuidanceRange,
    GuidanceUnit,
    RangeMovement,
    classify_range_movement,
    result_vs_range,
    state_disagrees_with_arithmetic,
)

USD = GuidanceUnit.USD_PER_SHARE


def r(low: float, high: float, unit: GuidanceUnit = USD) -> GuidanceRange:
    return GuidanceRange(low, high, unit)


def test_both_bounds_up_is_raised():
    assert classify_range_movement(r(1.0, 1.2), r(1.1, 1.3)).movement == RangeMovement.RAISED


def test_both_bounds_down_is_lowered():
    assert classify_range_movement(r(1.0, 1.2), r(0.9, 1.1)).movement == RangeMovement.LOWERED


def test_unchanged_bounds_is_maintained():
    assert classify_range_movement(r(1.0, 1.2), r(1.0, 1.2)).movement == RangeMovement.MAINTAINED


def test_a_narrowed_range_is_mixed_not_maintained():
    """Midpoint unchanged, but the company narrowed the range - a statement about confidence. A
    midpoint-only comparison would report MAINTAINED and erase the only thing that happened."""
    comparison = classify_range_movement(r(1.0, 1.2), r(1.05, 1.15))
    assert comparison.movement == RangeMovement.MIXED_BOUNDS
    assert comparison.midpoint_change_abs == pytest.approx(0.0)
    assert comparison.width_change_abs == pytest.approx(-0.1)


def test_a_one_sided_raise_is_mixed():
    assert classify_range_movement(r(1.0, 1.2), r(1.0, 1.3)).movement == RangeMovement.MIXED_BOUNDS


def test_different_units_are_not_comparable_not_coerced():
    comparison = classify_range_movement(r(1.0, 1.2), r(1.0, 1.2, GuidanceUnit.PERCENT))
    assert comparison.movement == RangeMovement.NOT_COMPARABLE
    assert comparison.midpoint_change_pct is None


def test_a_missing_side_is_not_comparable():
    assert classify_range_movement(None, r(1.0, 1.2)).movement == RangeMovement.NOT_COMPARABLE


def test_midpoint_percent_is_none_when_the_prior_midpoint_crosses_zero():
    """A percentage change through zero is not a percentage change. Reporting one would be an
    arithmetic artifact presented as a fact about guidance."""
    comparison = classify_range_movement(r(-0.1, 0.1), r(0.2, 0.4))
    assert comparison.midpoint_change_pct is None
    assert comparison.midpoint_change_abs == pytest.approx(0.3)


def test_a_high_below_its_low_is_rejected_at_construction():
    with pytest.raises(ValueError):
        GuidanceRange(1.2, 1.0, USD)


def test_reported_raised_against_lowered_arithmetic_is_a_disagreement():
    comparison = classify_range_movement(r(1.0, 1.2), r(0.9, 1.1))
    assert state_disagrees_with_arithmetic(GuidanceState.RAISED, comparison) is True


def test_reported_state_matching_the_arithmetic_is_accepted():
    comparison = classify_range_movement(r(1.0, 1.2), r(0.9, 1.1))
    assert state_disagrees_with_arithmetic(GuidanceState.LOWERED, comparison) is False


def test_code_never_overrules_a_comparison_it_could_not_make():
    """NOT_COMPARABLE must never produce a disagreement: if code could not compare the two ranges,
    code has no standing to reject the model's reading of them."""
    comparison = classify_range_movement(None, r(1.0, 1.2))
    for state in GuidanceState:
        assert state_disagrees_with_arithmetic(state, comparison) is False


def test_result_at_a_bound_is_within_the_company_s_own_range():
    assert result_vs_range(1.2, r(1.0, 1.2)) == "WITHIN_COMPANY_GUIDANCE"
    assert result_vs_range(1.0, r(1.0, 1.2)) == "WITHIN_COMPANY_GUIDANCE"


def test_result_outside_the_range_is_above_or_below():
    assert result_vs_range(1.3, r(1.0, 1.2)) == "ABOVE_COMPANY_GUIDANCE"
    assert result_vs_range(0.9, r(1.0, 1.2)) == "BELOW_COMPANY_GUIDANCE"
