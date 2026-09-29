"""Shared builders for the D4 expectation tests.

Deliberately minimal rather than realistic: a test that needs a real 10-K to exercise a contract
rule is a test about the 10-K. The one place real data IS used is `test_evidence_builder_real`,
which runs the builder against actual D2.1 packages, because a locator and a price aligner can only
be wrong in ways a synthetic fixture would never show.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.backtest.strategy_h_v2.evidence.bundle import (
    CandidateSource,
    CollectionDepth,
    EvidenceBundleV2,
    EvidenceCompleteness,
)
from app.backtest.strategy_h_v2.evidence.chunk_schema import (
    AIResearchInputV1,
    CandidateMaterializationRecord,
    DocumentMaterialization,
    EvidenceChunk,
    MaterializationStatus,
)
from app.backtest.strategy_h_v2.evidence.sources import (
    DatePrecision,
    SourceConfidence,
    SourceProvenance,
    SourceType,
)
from app.backtest.strategy_h_v2.expectation.evidence_schema import (
    EvidenceBlock,
    ExpectationEvidenceBundleV1,
    LocatedExcerpt,
)
from app.backtest.strategy_h_v2.expectation.gap_contract import EvidenceAvailability

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
SOURCE_ID = "SEC:1:A-1"
EVIDENCE_ID = f"{SOURCE_ID}:CHUNK:0"


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def sessions(count: int, start: date = date(2026, 1, 5), base: float = 100.0) -> dict[date, float]:
    """A contiguous run of pseudo-sessions. Calendar weekends are not modelled: every rule under
    test is about session ORDER, not about which weekday a session falls on."""
    return {start + timedelta(days=i): base + i for i in range(count)}


def source(source_id: str = SOURCE_ID, *, available_at: datetime | None = None,
           source_type: SourceType = SourceType.SEC_8K,
           precision: DatePrecision = DatePrecision.DATETIME) -> SourceProvenance:
    stamp = available_at or NOW
    return SourceProvenance(
        source_id=source_id, source_type=source_type, publisher="SEC", title="filing",
        url=None, published_at=stamp, date_precision=precision, available_at=stamp,
        fetched_at=NOW, confidence=SourceConfidence.HIGH, pit_eligible=True,
    )


def chunk(text: str, *, source_id: str = SOURCE_ID, index: int = 0,
          source_type: SourceType = SourceType.SEC_8K,
          published_at: datetime | None = None) -> EvidenceChunk:
    return EvidenceChunk(
        evidence_id=f"{source_id}:CHUNK:{index}", source_id=source_id, candidate_id="ACME",
        source_type=source_type, section=None, text=text,
        published_at=published_at or NOW, available_at=published_at or NOW, fetched_at=NOW,
        content_checksum="x", chunk_index=index, chunk_count=1, pit_eligible=True,
        extraction_status=MaterializationStatus.EXTRACTED,
    )


def package(
    *, chunks: list[EvidenceChunk] | None = None,
    fundamental_changes: dict[str, Any] | None = None,
    sources: list[SourceProvenance] | None = None,
    documents: list[DocumentMaterialization] | None = None,
    balance_sheet: dict[str, Any] | None = None,
) -> AIResearchInputV1:
    manifest = sources if sources is not None else [source()]
    bundle = EvidenceBundleV2(
        run_id="RUN-1", generated_at=NOW, data_cutoff=NOW, ticker="ACME",
        candidate_source=CandidateSource.E3_P1_HIGH, collection_depth=CollectionDepth.FULL,
        evidence_completeness=EvidenceCompleteness.COMPLETE,
        identity={"cik": "1"}, market={}, fundamentals={},
        fundamental_changes=fundamental_changes or {}, balance_sheet=balance_sheet or {},
        cashflow={}, price_context={}, earnings={"status": "UNKNOWN"},
        filings=manifest, company_releases=[], investor_materials=[], material_events=[],
        source_manifest=manifest, data_quality={}, unknown_fields=[],
    )
    return AIResearchInputV1(
        run_id="RUN-1", generated_at=NOW, evidence_bundle=bundle,
        # The default chunk belongs to the first manifest source and contains NO locator trigger -
        # an earlier default said "no guidance language", which of course contains "guidance".
        chunks=chunks if chunks is not None else [
            chunk("The registrant operates two reportable segments.",
                  source_id=manifest[0].source_id),
        ],
        materialization=CandidateMaterializationRecord(
            ticker="ACME", candidate_id="ACME", content_readiness="FULL",
            documents=documents or [],
        ),
        source_manifest=manifest,
    )


def excerpt(text: str = "We now expect full-year revenue of $1.05 to $1.15 billion.",
            *, source_id: str = SOURCE_ID, index: int = 0) -> LocatedExcerpt:
    return LocatedExcerpt(
        source_id=source_id, evidence_id=f"{source_id}:CHUNK:{index}",
        source_type="SEC_8K", published_at=NOW, text=text, matched_terms=["we now expect"],
    )


def expectation_bundle(
    *, guidance: EvidenceBlock | None = None,
    consensus: EvidenceBlock | None = None,
    price_reaction: list | None = None,
    pre_event_price_context: dict[str, Any] | None = None,
    bundle_id: str = "EB-1",
) -> ExpectationEvidenceBundleV1:
    absent = EvidenceBlock(status=EvidenceAvailability.SOURCE_NOT_AVAILABLE,
                           note="no provider connected")
    not_found = EvidenceBlock(status=EvidenceAvailability.NOT_FOUND_FOR_CANDIDATE,
                              note="nothing located")
    return ExpectationEvidenceBundleV1(
        bundle_id=bundle_id, company_id="1", ticker="ACME", decision_time=NOW, data_cutoff=NOW,
        generated_at=NOW, research_input_package_id="RUN-1:ACME",
        guidance=guidance or not_found,
        earnings_history=not_found,
        management_expectation_signals=not_found,
        consensus=consensus or absent,
        estimate_revisions=absent,
        price_reaction=price_reaction or [],
        pre_event_price_context=pre_event_price_context or {},
        valuation_context_stub={},
        source_manifest=[source()],
        unknown_fields=["consensus"],
    )


def claim(text: str = "Revenue improved.", *, evidence_id: str = EVIDENCE_ID,
          source_id: str = SOURCE_ID, claim_type: str = "FACT") -> dict[str, Any]:
    return {"text": text, "claim_type": claim_type, "source_id": source_id,
            "evidence_id": evidence_id, "evidence_ids": [], "confidence": "MEDIUM"}


def d4_content(**overrides: Any) -> dict[str, Any]:
    """A minimal schema-valid, contract-valid D4 response body: UNKNOWN everywhere it can be.

    UNKNOWN is the right default for a fixture precisely because it is the right default for a
    candidate with no expectation evidence - a fixture that defaulted to POSITIVE would need rule
    C1 disabled to exist, which is the opposite of what these tests are for.
    """
    content: dict[str, Any] = {
        "fundamental_reality_summary": {
            "d3_growth_durability_state": "UNKNOWN",
            "d3_future_business_max_stage": "UNKNOWN",
            "improvement_claims": [], "deterioration_claims": [], "durability_basis": [],
        },
        "market_expectation_evidence": {
            "overall_guidance_state": "UNKNOWN",
            "guidance_assessments": [], "result_vs_guidance": [],
            "management_signal_changes": [], "price_reaction_reading": [],
            "pre_event_positioning_reading": [],
            "consensus_status": "SOURCE_NOT_AVAILABLE",
            "estimate_revisions_status": "SOURCE_NOT_AVAILABLE",
        },
        "expectation_gap": "UNKNOWN",
        "expectation_gap_confidence": "UNKNOWN",
        "gap_rationale": [],
        "priced_in_assessment": {
            "state": "UNKNOWN", "confidence": "UNKNOWN", "evidence_ids": [],
            "limitations": [], "claims": [],
        },
        "why_now": [], "supporting_claims": [], "conflicts": [],
        "limitations": ["no consensus source is available"],
        "unknown_fields": ["consensus"], "sources": [SOURCE_ID],
    }
    content.update(overrides)
    return content


D3_OUTPUT: dict[str, Any] = {
    "research_id": "R-1",
    "growth_durability": {"state": "UNKNOWN"},
    "future_business": [],
}
