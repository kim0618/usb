from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.backtest.strategy_h_v2.evidence_bundle import (
    CandidateEvidenceStub,
    CONTRACT_VERSION,
    NotResearched,
    SCHEMA_VERSION,
)

AWARE_NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _base_kwargs(**overrides):
    kwargs = dict(
        run_id="RUN-1", generated_at=AWARE_NOW, data_cutoff=AWARE_NOW, ticker="AAPL",
        identity={}, market={}, fundamentals={}, fundamental_changes={}, balance_sheet={},
        cashflow={}, price_context={}, earnings={}, eligibility={}, research_priority={},
        data_quality={}, unknown_fields=[],
    )
    kwargs.update(overrides)
    return kwargs


def test_schema_validates_minimal_stub():
    stub = CandidateEvidenceStub(**_base_kwargs())
    assert stub.schema_version == SCHEMA_VERSION
    assert stub.contract_version == CONTRACT_VERSION


def test_aware_datetime_required():
    with pytest.raises(ValidationError):
        CandidateEvidenceStub(**_base_kwargs(generated_at=datetime(2026, 9, 28, 12, 0)))


def test_aware_datetime_required_for_data_cutoff_too():
    with pytest.raises(ValidationError):
        CandidateEvidenceStub(**_base_kwargs(data_cutoff=datetime(2026, 9, 28, 12, 0)))


def test_extra_field_rejected():
    with pytest.raises(ValidationError):
        CandidateEvidenceStub(**_base_kwargs(valuation_target=123))


def test_ai_fields_are_not_researched_by_default():
    stub = CandidateEvidenceStub(**_base_kwargs())
    assert stub.future_business == NotResearched.NOT_RESEARCHED
    assert stub.catalysts == NotResearched.NOT_RESEARCHED
    assert stub.expectation_gap == NotResearched.NOT_RESEARCHED
    assert stub.competitive_position == NotResearched.NOT_RESEARCHED
    assert stub.thesis == NotResearched.NOT_RESEARCHED


def test_ai_fields_cannot_be_set_to_a_fabricated_value():
    with pytest.raises(ValidationError):
        CandidateEvidenceStub(**_base_kwargs(thesis="this looks like a great buy"))


def test_unknown_fields_deduplicated_and_sorted():
    stub = CandidateEvidenceStub(**_base_kwargs(unknown_fields=["revenue", "revenue", " margin ", ""]))
    assert stub.unknown_fields == ["margin", "revenue"]
