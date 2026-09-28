"""D2 orchestration: Research Candidate -> Evidence Bundle V2.

Pure function of already-local data; no network access happens here (`app.dev.
run_strategy_h_v2_d2` supplies real repository data: D1.1's cached submissions and D1's candidate
evidence stubs). No interpretation is produced - see `bundle.py`'s banned-language validator for
the enforced half of that claim.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.backtest.strategy_h_v2.evidence.bundle import (
    CandidateSource,
    CollectionDepth,
    EvidenceBundleV2,
    EvidenceCompleteness,
    MaterialEventRef,
)
from app.backtest.strategy_h_v2.evidence.filing_selection import FilingSelection, select_filings
from app.backtest.strategy_h_v2.evidence.sources import (
    EARNINGS_RELEASE_CATEGORY,
    IR_PRESENTATION_CATEGORY,
    DatePrecision,
    SourceConfidence,
    SourceProvenance,
    SourceType,
    classify_8k_items,
)

FORM_TO_SOURCE_TYPE = {
    "10-K": SourceType.SEC_10K, "10-K/A": SourceType.SEC_10K,
    "10-Q": SourceType.SEC_10Q, "10-Q/A": SourceType.SEC_10Q,
    "8-K": SourceType.SEC_8K, "8-K/A": SourceType.SEC_8K,
}


def _parse_acceptance(row: dict[str, Any]) -> datetime | None:
    raw = row.get("acceptanceDateTime")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _filing_url(cik: str, row: dict[str, Any]) -> str | None:
    accession = row.get("accessionNumber")
    document = row.get("primaryDocument")
    if not accession or not document:
        return None
    accession_no_dashes = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/{document}"


def _filing_source(
    cik: str, row: dict[str, Any], *, fetched_at: datetime, checksum: str | None,
) -> SourceProvenance:
    accepted = _parse_acceptance(row)
    form = row.get("form", "")
    accession = row.get("accessionNumber", "")
    return SourceProvenance(
        source_id=f"SEC:{cik}:{accession}",
        source_type=FORM_TO_SOURCE_TYPE.get(form, SourceType.OTHER),
        publisher="U.S. Securities and Exchange Commission",
        title=f"{form} - accession {accession}",
        url=_filing_url(cik, row),
        published_at=accepted,
        date_precision=DatePrecision.DATETIME if accepted else DatePrecision.UNKNOWN,
        available_at=accepted,
        fetched_at=fetched_at,
        checksum=checksum,
        accession=accession,
        confidence=SourceConfidence.HIGH,
        pit_eligible=accepted is not None,
    )


def _not_available_marker(
    source_type: SourceType, note: str, *, fetched_at: datetime,
) -> SourceProvenance:
    return SourceProvenance(
        source_id=f"UNAVAILABLE:{source_type.value}",
        source_type=source_type, publisher="N/A", title=note, url=None,
        published_at=None, date_precision=DatePrecision.UNKNOWN, available_at=None,
        fetched_at=fetched_at, checksum=None, accession=None,
        confidence=SourceConfidence.UNKNOWN, pit_eligible=False,
    )


def _events_from_8k(cik: str, row: dict[str, Any]) -> tuple[list[MaterialEventRef], list[MaterialEventRef], list[MaterialEventRef]]:
    """Returns (company_releases, investor_materials, material_events) refs for one 8-K row."""
    accession = row.get("accessionNumber", "")
    source_id = f"SEC:{cik}:{accession}"
    filing_date = str(row.get("filingDate") or "")
    releases, ir_materials, events = [], [], []
    for item in classify_8k_items(row.get("items", "")):
        ref = MaterialEventRef(
            source_id=source_id, item_code=item["code"], item_label=item["label"],
            item_category=item["category"], filing_date=filing_date,
        )
        if item["category"] == EARNINGS_RELEASE_CATEGORY:
            releases.append(ref)
        elif item["category"] == IR_PRESENTATION_CATEGORY:
            ir_materials.append(ref)
        else:
            events.append(ref)
    return releases, ir_materials, events


def _evidence_completeness(
    candidate_stub: dict[str, Any], selection: FilingSelection,
) -> EvidenceCompleteness:
    identity_ok = bool(candidate_stub.get("identity", {}).get("cik"))
    fundamentals_ok = candidate_stub.get("data_quality", {}).get("resolved_canonical_fields", 0) >= 4
    filings_ok = selection.latest_10k is not None or bool(selection.recent_10q)
    changes = candidate_stub.get("fundamental_changes", {}) or {}
    e2_ok = any(c.get("state") not in (None, "UNKNOWN") for c in changes.values())
    market_ok = candidate_stub.get("market", {}).get("latest_close") is not None

    checks = [identity_ok, fundamentals_ok, filings_ok, e2_ok, market_ok]
    passed = sum(checks)
    if passed == len(checks):
        return EvidenceCompleteness.COMPLETE
    if passed <= 1:
        return EvidenceCompleteness.INSUFFICIENT
    return EvidenceCompleteness.PARTIAL


def assemble_evidence_bundle_v2(
    candidate_stub: dict[str, Any],
    *,
    submissions_rows: list[dict[str, Any]],
    cik: str,
    run_id: str,
    generated_at: datetime,
    data_cutoff: datetime,
    candidate_source: CandidateSource,
    collection_depth: CollectionDepth,
    submissions_checksum: str | None,
    submissions_fetched_at: datetime,
) -> EvidenceBundleV2:
    if data_cutoff.tzinfo is None or generated_at.tzinfo is None:
        raise ValueError("generated_at/data_cutoff must be timezone-aware")

    selection = select_filings(submissions_rows, data_cutoff=data_cutoff)

    filings: list[SourceProvenance] = []
    if selection.latest_10k is not None:
        filings.append(_filing_source(cik, selection.latest_10k, fetched_at=submissions_fetched_at,
                                       checksum=submissions_checksum))
    for row in selection.recent_10q:
        filings.append(_filing_source(cik, row, fetched_at=submissions_fetched_at,
                                       checksum=submissions_checksum))
    company_releases: list[MaterialEventRef] = []
    investor_materials: list[MaterialEventRef] = []
    material_events: list[MaterialEventRef] = []
    for row in selection.recent_8k:
        filings.append(_filing_source(cik, row, fetched_at=submissions_fetched_at,
                                       checksum=submissions_checksum))
        releases, ir_mat, events = _events_from_8k(cik, row)
        company_releases.extend(releases)
        investor_materials.extend(ir_mat)
        material_events.extend(events)

    earnings_materials: list[SourceProvenance] = []
    extra_manifest: list[SourceProvenance] = []
    if collection_depth == CollectionDepth.FULL:
        earnings_materials.append(_not_available_marker(
            SourceType.EARNINGS_CALL,
            "SOURCE_NOT_AVAILABLE: no earnings-call transcript provider is configured in this "
            "repository (D1.1 brief §12); not required for D2 PASS.",
            fetched_at=generated_at,
        ))
        extra_manifest.append(_not_available_marker(
            SourceType.MAJOR_NEWS,
            "MAJOR_NEWS = NOT_CONNECTED: no news provider is configured in this repository "
            "(D1.1 brief §14).",
            fetched_at=generated_at,
        ))

    source_manifest = list({s.source_id: s for s in (*filings, *earnings_materials, *extra_manifest)}.values())

    unknown_fields = list(candidate_stub.get("unknown_fields", []))
    if selection.latest_10k is None:
        unknown_fields.append("latest_10k")
    if not selection.recent_10q:
        unknown_fields.append("recent_10q")

    data_quality = {
        **candidate_stub.get("data_quality", {}),
        "filings_found": len(filings),
        "tenq_found": len(selection.recent_10q),
        "eightk_found": len(selection.recent_8k),
        "excluded_future_filings": selection.excluded_future,
    }

    return EvidenceBundleV2(
        run_id=run_id, generated_at=generated_at, data_cutoff=data_cutoff,
        ticker=candidate_stub["ticker"], candidate_source=candidate_source,
        collection_depth=collection_depth,
        evidence_completeness=_evidence_completeness(candidate_stub, selection),
        identity=candidate_stub.get("identity", {}), market=candidate_stub.get("market", {}),
        fundamentals=candidate_stub.get("fundamentals", {}),
        fundamental_changes=candidate_stub.get("fundamental_changes", {}),
        balance_sheet=candidate_stub.get("balance_sheet", {}),
        cashflow=candidate_stub.get("cashflow", {}),
        price_context=candidate_stub.get("price_context", {}),
        earnings=candidate_stub.get("earnings", {}),
        filings=filings, company_releases=company_releases,
        earnings_materials=earnings_materials, investor_materials=investor_materials,
        material_events=material_events, source_manifest=source_manifest,
        data_quality=data_quality, unknown_fields=sorted(set(unknown_fields)),
    )
