from __future__ import annotations

from helpers import utc

from app.backtest.strategy_h_v2.evidence.bundle import CandidateSource, CollectionDepth, EvidenceBundleV2, EvidenceCompleteness
from app.backtest.strategy_h_v2.evidence.chunk_schema import (
    AIResearchInputV1,
    CandidateMaterializationRecord,
    EvidenceChunk,
    MaterializationStatus,
)
from app.backtest.strategy_h_v2.evidence.sources import SOURCE_BOUNDARY, DatePrecision, SourceConfidence, SourceProvenance, SourceType
from app.backtest.strategy_h_v2.research.prompt_builder import (
    METADATA_FIELDS,
    build_research_prompt,
    content_only_schema,
    select_evidence_chunks,
)

NOW = utc(2026, 9, 28)


def _source(source_id: str, source_type: SourceType) -> SourceProvenance:
    return SourceProvenance(
        source_id=source_id, source_type=source_type, publisher="SEC", title="doc", url=None,
        published_at=NOW, date_precision=DatePrecision.DATETIME, available_at=NOW, fetched_at=NOW,
        confidence=SourceConfidence.HIGH, pit_eligible=True,
    )


def _chunk(source_id: str, source_type: SourceType, *, section: str | None, index: int,
           length: int = 100) -> EvidenceChunk:
    return EvidenceChunk(
        evidence_id=f"{source_id}:CHUNK:{index}", source_id=source_id, candidate_id="C1",
        source_type=source_type, section=section, text="x" * length, published_at=NOW,
        available_at=NOW, fetched_at=NOW, content_checksum="abc", chunk_index=index,
        chunk_count=1, pit_eligible=True, extraction_status=MaterializationStatus.EXTRACTED,
    )


def _package(chunks: list[EvidenceChunk]) -> AIResearchInputV1:
    bundle = EvidenceBundleV2(
        run_id="RUN-1", generated_at=NOW, data_cutoff=NOW, ticker="ACME",
        candidate_source=CandidateSource.E3_P1_HIGH, collection_depth=CollectionDepth.FULL,
        evidence_completeness=EvidenceCompleteness.COMPLETE,
        identity={"cik": "1"}, market={}, fundamentals={}, fundamental_changes={}, balance_sheet={},
        cashflow={}, price_context={}, earnings={}, filings=[], company_releases=[],
        investor_materials=[], material_events=[], source_manifest=[], data_quality={},
        unknown_fields=[],
    )
    return AIResearchInputV1(
        run_id="RUN-1", generated_at=NOW, evidence_bundle=bundle, chunks=chunks,
        materialization=CandidateMaterializationRecord(
            ticker="ACME", candidate_id="ACME", content_readiness="FULL", documents=[],
        ),
        source_manifest=[],
    )


def test_non_periodic_chunks_prioritized_over_periodic():
    chunks = [
        _chunk("SEC:1:10K", SourceType.SEC_10K, section=None, index=0),
        _chunk("SEC:1:8K", SourceType.SEC_8K, section=None, index=0),
    ]
    selected = select_evidence_chunks(chunks, budget_chars=10_000)
    assert selected[0].source_type == SourceType.SEC_8K


def test_priority_sections_beat_unsectioned_periodic_chunks():
    chunks = [
        _chunk("SEC:1:10K", SourceType.SEC_10K, section=None, index=0),
        _chunk("SEC:1:10K", SourceType.SEC_10K, section="MD_AND_A", index=1),
    ]
    selected = select_evidence_chunks(chunks, budget_chars=10_000)
    assert selected[0].section == "MD_AND_A"


def test_budget_is_respected():
    chunks = [_chunk("SEC:1:8K", SourceType.SEC_8K, section=None, index=i, length=1000) for i in range(50)]
    selected = select_evidence_chunks(chunks, budget_chars=5_000)
    assert sum(len(c.text) for c in selected) <= 5_000
    assert len(selected) < 50


def test_content_only_schema_excludes_metadata_fields():
    schema = content_only_schema()
    assert set(schema["properties"].keys()).isdisjoint(METADATA_FIELDS)
    assert set(schema.get("required", [])).isdisjoint(METADATA_FIELDS)


def test_prompt_wraps_every_chunk_with_source_boundary():
    package = _package([_chunk("SEC:1:8K", SourceType.SEC_8K, section=None, index=0)])
    system, user = build_research_prompt(package)
    assert SOURCE_BOUNDARY in user
    assert "[SOURCE SEC:1:8K" in user
    assert "[END SOURCE]" in user


def test_prompt_includes_data_cutoff_for_pit_discipline():
    package = _package([])
    system, user = build_research_prompt(package)
    assert package.evidence_bundle.data_cutoff.isoformat() in user


def test_prompt_is_deterministic_for_same_package():
    package = _package([_chunk("SEC:1:8K", SourceType.SEC_8K, section=None, index=0)])
    first = build_research_prompt(package)
    second = build_research_prompt(package)
    assert first == second


def test_system_prompt_forbids_investment_decisions_explicitly():
    package = _package([])
    system, _ = build_research_prompt(package)
    assert "APPROVE" in system and "do not" in system.lower() or "never" in system.lower()
    assert "fair value" in system.lower()
