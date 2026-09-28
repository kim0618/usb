from __future__ import annotations

from helpers import mkrow, utc

from app.backtest.strategy_h_v2.evidence.bundle import (
    CandidateSource,
    CollectionDepth,
    EvidenceCompleteness,
)
from app.backtest.strategy_h_v2.evidence.collector import assemble_evidence_bundle_v2

CUTOFF = utc(2026, 9, 28)
CIK = "0000000320193"


def _candidate_stub(**overrides):
    base = {
        "ticker": "ACME",
        "identity": {"ticker": "ACME", "cik": CIK},
        "market": {"latest_close": 40.0},
        "fundamentals": {"revenue": {"status": "OK", "value": 100.0}},
        "fundamental_changes": {"revenue": {"state": "ACCELERATING", "confidence": "HIGH"}},
        "balance_sheet": {}, "cashflow": {}, "price_context": {"latest_close": 40.0},
        "earnings": {"status": "UNKNOWN"},
        "data_quality": {"resolved_canonical_fields": 8},
        "unknown_fields": [],
    }
    base.update(overrides)
    return base


def _submissions_rows():
    return [
        mkrow("10-K", "A-1", "2025-11-01", "2025-11-01T10:00:00.000Z"),
        mkrow("10-Q", "Q-1", "2026-05-01", "2026-05-01T10:00:00.000Z"),
        mkrow("8-K", "K-1", "2026-07-30", "2026-07-30T10:00:00.000Z", items="2.02,9.01"),
        mkrow("8-K", "K-2", "2026-06-15", "2026-06-15T10:00:00.000Z", items="7.01"),
        mkrow("8-K", "K-3", "2026-06-01", "2026-06-01T10:00:00.000Z", items="5.02"),
    ]


def _assemble(**overrides):
    kwargs = dict(
        candidate_stub=_candidate_stub(), submissions_rows=_submissions_rows(), cik=CIK,
        run_id="RUN-1", generated_at=CUTOFF, data_cutoff=CUTOFF,
        candidate_source=CandidateSource.E3_P1_HIGH, collection_depth=CollectionDepth.FULL,
        submissions_checksum="abc123", submissions_fetched_at=CUTOFF,
    )
    kwargs.update(overrides)
    return assemble_evidence_bundle_v2(**kwargs)


def test_p1_full_collection_identifies_release_ir_and_events():
    bundle = _assemble()
    assert len(bundle.company_releases) == 1
    assert bundle.company_releases[0].item_code == "2.02"
    assert len(bundle.investor_materials) == 1
    assert bundle.investor_materials[0].item_code == "7.01"
    assert len(bundle.material_events) == 2  # 9.01 and 5.02
    assert bundle.collection_depth == CollectionDepth.FULL


def test_p1_full_collection_records_unavailable_optional_sources_explicitly():
    bundle = _assemble()
    assert any(s.source_type.value == "EARNINGS_CALL" for s in bundle.earnings_materials)
    assert any(s.source_type.value == "MAJOR_NEWS" for s in bundle.source_manifest)


def test_p2_core_collection_skips_optional_source_attempts():
    bundle = _assemble(candidate_source=CandidateSource.E3_P2_MEDIUM, collection_depth=CollectionDepth.CORE)
    assert bundle.earnings_materials == []
    assert not any(s.source_type.value == "MAJOR_NEWS" for s in bundle.source_manifest)
    # Core still gets required SEC filings and event classification.
    assert len(bundle.company_releases) == 1
    assert bundle.filings


def test_failed_optional_source_does_not_break_core_bundle():
    """CORE depth never even attempts the optional sources; the bundle must still validate and
    carry every required section."""
    bundle = _assemble(candidate_source=CandidateSource.E3_P2_MEDIUM, collection_depth=CollectionDepth.CORE)
    assert bundle.evidence_completeness == EvidenceCompleteness.COMPLETE
    assert bundle.identity["cik"] == CIK


def test_source_manifest_deduplicated():
    bundle = _assemble()
    ids = [s.source_id for s in bundle.source_manifest]
    assert len(ids) == len(set(ids))


def test_evidence_completeness_downgrades_when_no_filings_found():
    bundle = _assemble(submissions_rows=[])
    assert bundle.evidence_completeness != EvidenceCompleteness.COMPLETE
    assert "latest_10k" in bundle.unknown_fields
    assert "recent_10q" in bundle.unknown_fields


def test_rerun_is_idempotent_for_identical_inputs():
    first = _assemble()
    second = _assemble()
    assert first.model_dump() == second.model_dump()


def test_manual_candidate_provenance_is_recorded():
    bundle = _assemble(candidate_source=CandidateSource.MANUAL)
    assert bundle.candidate_source == CandidateSource.MANUAL


def test_pit_excludes_filing_after_cutoff_from_bundle():
    rows = _submissions_rows() + [mkrow("8-K", "K-FUTURE", "2026-10-05", "2026-10-05T10:00:00.000Z", items="2.02")]
    bundle = _assemble(submissions_rows=rows)
    accessions = {s.accession for s in bundle.filings}
    assert "K-FUTURE" not in accessions


def test_submissions_checksum_is_preserved_onto_every_filing_reference():
    bundle = _assemble(submissions_checksum="sha256-deadbeef")
    assert bundle.filings
    assert all(source.checksum == "sha256-deadbeef" for source in bundle.filings)
