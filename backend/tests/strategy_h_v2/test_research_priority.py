from __future__ import annotations

import dataclasses

from app.backtest.strategy_h_v2.change_detection import ChangeEvidence, ChangeState, Confidence
from app.backtest.strategy_h_v2.research_priority import (
    PriorityInputs,
    PriorityState,
    compute_priority,
)

FORBIDDEN_FIELD_NAMES = {
    "return", "forward_return", "future_return", "expected_return", "price_target",
    "excess_return", "performance",
}


def _evidence(metric: str, state: ChangeState, confidence: Confidence) -> ChangeEvidence:
    return ChangeEvidence(
        metric=metric, state=state, confidence=confidence, current_value=None, points=(),
        data_cutoff="2026-09-28T00:00:00+00:00",
    )


def test_no_return_field_accepted():
    field_names = {f.name for f in dataclasses.fields(PriorityInputs)}
    assert field_names.isdisjoint(FORBIDDEN_FIELD_NAMES)


def test_priority_is_deterministic():
    inputs = PriorityInputs(
        change_evidence=(_evidence("revenue", ChangeState.ACCELERATING, Confidence.HIGH),),
        days_since_latest_filing=5, resolved_field_count=10, total_field_count=12,
    )
    assert compute_priority(inputs) == compute_priority(inputs)


def test_high_confidence_material_change_with_recent_filing_is_p1():
    inputs = PriorityInputs(
        change_evidence=(_evidence("revenue", ChangeState.ACCELERATING, Confidence.HIGH),),
        days_since_latest_filing=3, resolved_field_count=10, total_field_count=12,
    )
    assert compute_priority(inputs) == PriorityState.P1_HIGH


def test_two_high_confidence_changes_is_p1_even_without_recent_filing():
    inputs = PriorityInputs(
        change_evidence=(
            _evidence("revenue", ChangeState.ACCELERATING, Confidence.HIGH),
            _evidence("operating_income", ChangeState.INFLECTION_NEGATIVE, Confidence.HIGH),
        ),
        days_since_latest_filing=None, resolved_field_count=10, total_field_count=12,
    )
    assert compute_priority(inputs) == PriorityState.P1_HIGH


def test_single_material_change_without_filing_is_p2():
    inputs = PriorityInputs(
        change_evidence=(_evidence("revenue", ChangeState.DECELERATING, Confidence.MEDIUM),),
        days_since_latest_filing=None, resolved_field_count=10, total_field_count=12,
    )
    assert compute_priority(inputs) == PriorityState.P2_MEDIUM


def test_no_material_change_is_p3():
    inputs = PriorityInputs(
        change_evidence=(_evidence("revenue", ChangeState.STABLE, Confidence.MEDIUM),),
        days_since_latest_filing=None, resolved_field_count=10, total_field_count=12,
    )
    assert compute_priority(inputs) == PriorityState.P3_LOW


def test_low_data_completeness_holds_regardless_of_change_signal():
    inputs = PriorityInputs(
        change_evidence=(_evidence("revenue", ChangeState.ACCELERATING, Confidence.HIGH),),
        days_since_latest_filing=1, resolved_field_count=1, total_field_count=12,
    )
    assert compute_priority(inputs) == PriorityState.HOLD


def test_priority_is_not_a_quality_rank():
    """A candidate with weaker apparent fundamentals but a fresh, high-confidence change signal
    outranks one with more resolved fields but no material change - priority tracks research
    urgency, not investment quality."""
    urgent_but_thin_data = PriorityInputs(
        change_evidence=(_evidence("revenue", ChangeState.INFLECTION_POSITIVE, Confidence.HIGH),),
        days_since_latest_filing=2, resolved_field_count=5, total_field_count=12,
    )
    complete_but_stable = PriorityInputs(
        change_evidence=(_evidence("revenue", ChangeState.STABLE, Confidence.HIGH),),
        days_since_latest_filing=None, resolved_field_count=12, total_field_count=12,
    )
    assert compute_priority(urgent_but_thin_data) == PriorityState.P1_HIGH
    assert compute_priority(complete_but_stable) == PriorityState.P3_LOW
