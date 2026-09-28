"""D2.1 orchestration: EvidenceBundleV2 -> AIResearchInputV1 (bundle + text chunks + materialization
record). Bounded by the deterministic P1/P2 depth policy below - never all filings, never an
unlimited exhibit search (D2.1 brief §17/§20).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.backtest.strategy_c_e0.sec_store import SecClient
from app.backtest.strategy_h_v2.evidence.bundle import CollectionDepth, EvidenceBundleV2
from app.backtest.strategy_h_v2.evidence.chunk_schema import (
    AIResearchInputV1,
    CandidateMaterializationRecord,
    DocumentMaterialization,
    MaterializationStatus,
)
from app.backtest.strategy_h_v2.evidence.chunking import chunk_document
from app.backtest.strategy_h_v2.evidence.document_cache import fetch_document, fetch_filing_index
from app.backtest.strategy_h_v2.evidence.filing_index import (
    IndexDocument,
    find_exhibits_by_type_prefix,
    parse_filing_index,
)
from app.backtest.strategy_h_v2.evidence.sources import SourceProvenance, SourceType
from app.backtest.strategy_h_v2.evidence.text_extraction import detect_sections, extract_text

# Bounded, documented, not performance-tuned (D2.1 brief §5/§17): how many of each already-
# selected D2 filing this stage actually fetches full text for, per collection depth.
CORE_MAX_10Q = 1
FULL_MAX_10Q = 2
CORE_MAX_MATERIAL_8K = 1
FULL_MAX_MATERIAL_8K = 3


@dataclass(frozen=True)
class DepthPolicy:
    max_10q: int
    max_material_8k: int
    include_ir_presentation: bool


DEPTH_POLICY = {
    CollectionDepth.FULL: DepthPolicy(FULL_MAX_10Q, FULL_MAX_MATERIAL_8K, True),
    CollectionDepth.CORE: DepthPolicy(CORE_MAX_10Q, CORE_MAX_MATERIAL_8K, False),
}


def _cik_and_accession(source: SourceProvenance) -> tuple[str, str]:
    # source_id is "SEC:{cik}:{accession}" (collector.py's convention).
    _, cik, accession = source.source_id.split(":", 2)
    return cik, accession


def _materialize_source(
    client: SecClient | None, cache_root: Path, source: SourceProvenance, *, role: str,
    candidate_id: str,
) -> tuple[DocumentMaterialization, list]:
    if not source.pit_eligible:
        # Defensive: D2 never emits a non-PIT-eligible filings[] entry, but this stage must not
        # materialize one if it somehow saw it (D2.1 brief §15).
        return DocumentMaterialization(
            source_id=source.source_id, role=role, status=MaterializationStatus.NOT_FETCHED,
            content_type="UNKNOWN", chunk_count=0, raw_checksum=None,
        ), []
    cik, accession = _cik_and_accession(source)
    accession_no_dashes = accession.replace("-", "")
    document_name = str(source.url).rsplit("/", 1)[-1] if source.url else accession
    document_path = str(source.url).split("sec.gov", 1)[-1] if source.url else None
    if document_path is None:
        return DocumentMaterialization(
            source_id=source.source_id, role=role, status=MaterializationStatus.NOT_FETCHED,
            content_type="UNKNOWN", chunk_count=0, raw_checksum=None,
        ), []
    body, checksum, outcome = fetch_document(
        client, cache_root, cik=cik, accession_no_dashes=accession_no_dashes,
        document_name=document_name, document_path=document_path,
    )
    if outcome in ("MISSING", "FAILED") or body is None:
        return DocumentMaterialization(
            source_id=source.source_id, role=role, status=MaterializationStatus.NOT_FETCHED,
            content_type="UNKNOWN", chunk_count=0, raw_checksum=None,
        ), []
    text, status, content_type = extract_text(document_name, body)
    if status != "EXTRACTED":
        mat_status = MaterializationStatus(status.value)
        return DocumentMaterialization(
            source_id=source.source_id, role=role, status=mat_status,
            content_type=content_type.value, chunk_count=0, raw_checksum=checksum,
        ), []
    sections = detect_sections(text) if role in ("LATEST_10K", "RECENT_10Q") else None
    chunks = chunk_document(text, source=source, candidate_id=candidate_id, sections=sections)
    return DocumentMaterialization(
        source_id=source.source_id, role=role, status=MaterializationStatus.EXTRACTED,
        content_type=content_type.value, chunk_count=len(chunks), raw_checksum=checksum,
    ), chunks


_IR_PRESENTATION_KEYWORDS = ("presentation", "investor", "slide", "deck")


def is_presentation_confirmed(description: str) -> bool:
    """D2.1 brief §8: `7.01 exists != IR presentation confirmed`. Only the filing's own exhibit
    Description text (SEC's own metadata, never inferred content) can upgrade a generic Reg FD
    exhibit to a confirmed `IR_PRESENTATION`; anything else stays the generic `REG_FD_MATERIAL`
    role even though the content is still materialized and available to D3."""
    lowered = description.lower()
    return any(keyword in lowered for keyword in _IR_PRESENTATION_KEYWORDS)


def _resolve_exhibit(
    client: SecClient | None, cache_root: Path, filing_source: SourceProvenance, *, type_prefix: str,
) -> tuple[SourceProvenance | None, IndexDocument | None]:
    cik, accession = _cik_and_accession(filing_source)
    accession_no_dashes = accession.replace("-", "")
    index_html, outcome = fetch_filing_index(
        client, cache_root, cik=cik, accession=accession, accession_no_dashes=accession_no_dashes,
    )
    if index_html is None:
        return None, None
    documents = parse_filing_index(index_html)
    matches = find_exhibits_by_type_prefix(documents, type_prefix)
    if not matches:
        return None, None
    exhibit = matches[0]
    source = SourceProvenance(
        source_id=f"{filing_source.source_id}:{exhibit.doc_type}",
        source_type=filing_source.source_type, publisher=filing_source.publisher,
        title=f"{exhibit.doc_type} ({exhibit.description}) - accession {accession}",
        url=f"https://www.sec.gov{exhibit.document_path}" if exhibit.document_path.startswith("/")
            else exhibit.document_path,
        published_at=filing_source.published_at, date_precision=filing_source.date_precision,
        available_at=filing_source.available_at, fetched_at=filing_source.fetched_at,
        checksum=None, accession=accession, confidence=filing_source.confidence,
        pit_eligible=filing_source.pit_eligible,
    )
    return source, exhibit


def materialize_candidate(
    client: SecClient | None, cache_root: Path, bundle: EvidenceBundleV2, *, candidate_id: str,
) -> AIResearchInputV1:
    policy = DEPTH_POLICY[bundle.collection_depth]
    by_id = {s.source_id: s for s in bundle.filings}

    documents: list[DocumentMaterialization] = []
    all_chunks: list = []
    extra_manifest: list[SourceProvenance] = []

    def _add(source: SourceProvenance | None, role: str) -> MaterializationStatus:
        if source is None:
            documents.append(DocumentMaterialization(
                source_id=f"UNRESOLVED:{role}", role=role,
                status=MaterializationStatus.EXHIBIT_UNRESOLVED, content_type="UNKNOWN",
                chunk_count=0, raw_checksum=None,
            ))
            return MaterializationStatus.EXHIBIT_UNRESOLVED
        record, chunks = _materialize_source(client, cache_root, source, role=role,
                                              candidate_id=candidate_id)
        documents.append(record)
        all_chunks.extend(chunks)
        if source not in bundle.filings:
            extra_manifest.append(source)
        return record.status

    tenk = next((s for s in bundle.filings if s.source_type == SourceType.SEC_10K), None)
    if tenk is not None:
        tenk_status = _add(tenk, "LATEST_10K")
    else:
        tenk_status = MaterializationStatus.NOT_FETCHED
        documents.append(DocumentMaterialization(
            source_id="ABSENT:LATEST_10K", role="LATEST_10K", status=tenk_status,
            content_type="UNKNOWN", chunk_count=0, raw_checksum=None,
        ))

    tenqs = [s for s in bundle.filings if s.source_type == SourceType.SEC_10Q][:policy.max_10q]
    tenq_statuses = [_add(s, "RECENT_10Q") for s in tenqs]

    release_statuses: list[MaterializationStatus] = []
    if bundle.company_releases:
        release_event = bundle.company_releases[0]
        filing_source = by_id.get(release_event.source_id)
        exhibit, _doc = _resolve_exhibit(client, cache_root, filing_source, type_prefix="EX-99") \
            if filing_source else (None, None)
        release_statuses.append(_add(exhibit, "EARNINGS_RELEASE"))

    ir_statuses: list[MaterializationStatus] = []
    if policy.include_ir_presentation and bundle.investor_materials:
        ir_event = bundle.investor_materials[0]
        filing_source = by_id.get(ir_event.source_id)
        exhibit, exhibit_doc = _resolve_exhibit(client, cache_root, filing_source, type_prefix="EX-99") \
            if filing_source else (None, None)
        # §8: a 7.01 exhibit is only labeled IR_PRESENTATION when SEC's own exhibit description
        # text confirms it; otherwise it is materialized under the generic REG_FD_MATERIAL role.
        ir_role = "IR_PRESENTATION" if exhibit_doc and is_presentation_confirmed(exhibit_doc.description) \
            else "REG_FD_MATERIAL"
        ir_statuses.append(_add(exhibit, ir_role))

    used_ids = {tenk.source_id} if tenk else set()
    used_ids |= {s.source_id for s in tenqs}
    if bundle.company_releases:
        used_ids.add(bundle.company_releases[0].source_id)
    material_events = [e for e in bundle.material_events if e.source_id not in used_ids]
    seen: set[str] = set()
    eightk_statuses: list[MaterializationStatus] = []
    for event in material_events:
        if event.source_id in seen or len(eightk_statuses) >= policy.max_material_8k:
            continue
        seen.add(event.source_id)
        source = by_id.get(event.source_id)
        if source:
            eightk_statuses.append(_add(source, "MATERIAL_8K"))

    # Each of the three "required" documents counts toward the readiness fraction only when D2
    # actually identified one for this candidate - a 10-Q or earnings release that never existed is
    # not a materialization shortfall, the same "absence is not a failure" principle D1.1 already
    # applied to eligibility (H_V2_D1_1 SSC).
    required_targets = (1 if tenk else 0) + (1 if tenqs else 0) + (1 if bundle.company_releases else 0)
    required_ok = (
        (1 if tenk and tenk_status == MaterializationStatus.EXTRACTED else 0)
        + (1 if tenqs and tenq_statuses[0] == MaterializationStatus.EXTRACTED else 0)
        + (1 if bundle.company_releases and release_statuses
           and release_statuses[0] == MaterializationStatus.EXTRACTED else 0)
    )
    full_extra_ok = (
        len(tenqs) >= policy.max_10q
        and all(s == MaterializationStatus.EXTRACTED for s in tenq_statuses)
        and (not eightk_statuses or any(s == MaterializationStatus.EXTRACTED for s in eightk_statuses))
    )

    if required_targets == 0:
        # No 10-K, no 10-Q, and no earnings release were ever identified for this candidate - there
        # is nothing D2.1 could have materialized regardless of fetch success.
        readiness = "INSUFFICIENT"
    elif required_ok == required_targets:
        readiness = "FULL" if bundle.collection_depth == CollectionDepth.FULL and full_extra_ok else "CORE"
    elif required_ok == 0:
        attempted = any(d.status in (MaterializationStatus.NOT_FETCHED,
                                      MaterializationStatus.CONTENT_NOT_EXTRACTABLE,
                                      MaterializationStatus.EXHIBIT_UNRESOLVED) for d in documents)
        readiness = "REFERENCE_ONLY" if attempted else "INSUFFICIENT"
    else:
        readiness = "PARTIAL"

    generated_at = datetime.now(timezone.utc)
    return AIResearchInputV1(
        run_id=bundle.run_id, generated_at=generated_at, evidence_bundle=bundle, chunks=all_chunks,
        materialization=CandidateMaterializationRecord(
            ticker=bundle.ticker, candidate_id=candidate_id, content_readiness=readiness,
            documents=documents,
        ),
        source_manifest=bundle.source_manifest + extra_manifest,
    )
