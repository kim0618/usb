from __future__ import annotations

from helpers import utc

from app.backtest.strategy_h_v2.evidence.bundle import CandidateSource, CollectionDepth, EvidenceBundleV2, EvidenceCompleteness
from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1, CandidateMaterializationRecord
from app.backtest.strategy_h_v2.evidence.sources import DatePrecision, SourceConfidence, SourceProvenance, SourceType
from app.backtest.strategy_h_v2.research.validate import (
    assemble_and_validate,
    cross_check_fundamental_change_states,
    extract_json_object,
    valid_source_ids,
)

NOW = utc(2026, 9, 28)


def _package(fundamental_changes: dict | None = None) -> AIResearchInputV1:
    source = SourceProvenance(
        source_id="SEC:1:A-1", source_type=SourceType.SEC_10K, publisher="SEC", title="10-K",
        url=None, published_at=NOW, date_precision=DatePrecision.DATETIME, available_at=NOW,
        fetched_at=NOW, confidence=SourceConfidence.HIGH, pit_eligible=True,
    )
    bundle = EvidenceBundleV2(
        run_id="RUN-1", generated_at=NOW, data_cutoff=NOW, ticker="ACME",
        candidate_source=CandidateSource.E3_P1_HIGH, collection_depth=CollectionDepth.FULL,
        evidence_completeness=EvidenceCompleteness.COMPLETE,
        identity={"cik": "1"}, market={}, fundamentals={},
        fundamental_changes=fundamental_changes or {}, balance_sheet={}, cashflow={},
        price_context={}, earnings={}, filings=[source], company_releases=[],
        investor_materials=[], material_events=[], source_manifest=[], data_quality={},
        unknown_fields=[],
    )
    return AIResearchInputV1(
        run_id="RUN-1", generated_at=NOW, evidence_bundle=bundle, chunks=[],
        materialization=CandidateMaterializationRecord(
            ticker="ACME", candidate_id="ACME", content_readiness="FULL", documents=[],
        ),
        source_manifest=[],
    )


VALID_CONTENT = {
    "business_model": {}, "fundamental_change": [], "growth_durability":
        {"state": "UNKNOWN", "rationale": [], "evidence_checklist": {}},
    "future_business": [], "competitive_position": [], "management_execution": [],
    "catalyst_candidates": [], "why_now_candidate": {"summary": "worth checking", "reasons": []},
    "risks": [], "invalidation_candidates": [], "open_questions": [], "unknown_fields": [],
    "sources": [], "research_completeness": "INSUFFICIENT_EVIDENCE",
}


def test_extract_json_object_parses_bare_json():
    obj = extract_json_object('{"a": 1}')
    assert obj == {"a": 1}


def test_extract_json_object_strips_code_fence():
    obj = extract_json_object('```json\n{"a": 1}\n```')
    assert obj == {"a": 1}


def test_extract_json_object_raises_on_garbage():
    import pytest
    with pytest.raises(Exception):
        extract_json_object("not json at all")


def test_valid_source_ids_includes_filings():
    package = _package()
    assert "SEC:1:A-1" in valid_source_ids(package)


def test_cross_check_passes_when_state_matches():
    content = {"fundamental_change": [{"metric": "revenue", "code_owned_state": "ACCELERATING"}]}
    package = _package({"revenue": {"state": "ACCELERATING"}})
    assert cross_check_fundamental_change_states(content, package) == []


def test_cross_check_catches_mutated_state():
    """The core numeric-mutation-detection test: the model must not be able to silently claim a
    different code-owned state than what D1's E2 actually computed."""
    content = {"fundamental_change": [{"metric": "revenue", "code_owned_state": "DETERIORATING"}]}
    package = _package({"revenue": {"state": "ACCELERATING"}})
    errors = cross_check_fundamental_change_states(content, package)
    assert errors and "DETERIORATING" in errors[0] and "ACCELERATING" in errors[0]


def test_assemble_and_validate_succeeds_on_well_formed_output():
    package = _package()
    raw = __import__("json").dumps(VALID_CONTENT)
    output, errors = assemble_and_validate(
        raw, package, research_id="R-1", version=1, model="claude-opus-5-5",
        model_version="claude-opus-5-5", prompt_version="v1", created_at=NOW,
    )
    assert errors == []
    assert output is not None
    assert output.model == "claude-opus-5-5"
    assert output.prompt_version == "v1"
    assert output.ticker == "ACME"


def test_assemble_and_validate_fails_soft_on_malformed_json():
    output, errors = assemble_and_validate(
        "not json", _package(), research_id="R-1", version=1, model="claude-opus-5-5",
        model_version=None, prompt_version="v1", created_at=NOW,
    )
    assert output is None
    assert errors


def test_assemble_and_validate_fails_soft_on_mutated_numeric_state():
    content = {**VALID_CONTENT, "fundamental_change": [
        {"metric": "revenue", "code_owned_state": "DETERIORATING"},
    ]}
    package = _package({"revenue": {"state": "ACCELERATING"}})
    output, errors = assemble_and_validate(
        __import__("json").dumps(content), package, research_id="R-1", version=1,
        model="claude-opus-5-5", model_version=None, prompt_version="v1", created_at=NOW,
    )
    assert output is None
    assert any("DETERIORATING" in e for e in errors)


def test_metadata_fields_in_raw_output_are_ignored_not_trusted():
    """Even if the model outputs a ticker/company_id, the orchestration layer's own values win -
    the model's echo is discarded, never trusted (prompt_builder.METADATA_FIELDS)."""
    content = {**VALID_CONTENT, "ticker": "WRONG", "company_id": "999"}
    output, errors = assemble_and_validate(
        __import__("json").dumps(content), _package(), research_id="R-1", version=1,
        model="claude-opus-5-5", model_version=None, prompt_version="v1", created_at=NOW,
    )
    assert output is not None
    assert output.ticker == "ACME"
    assert output.company_id == "1"
