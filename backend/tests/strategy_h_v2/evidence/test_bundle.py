from __future__ import annotations

import pytest
from helpers import utc
from pydantic import ValidationError

from app.backtest.strategy_h_v2.evidence.bundle import (
    CandidateSource,
    CollectionDepth,
    EvidenceBundleV2,
    EvidenceCompleteness,
    MaterialEventRef,
    NotResearched,
)
from app.backtest.strategy_h_v2.evidence.sources import (
    DatePrecision,
    SourceConfidence,
    SourceProvenance,
    SourceType,
)

NOW = utc(2026, 9, 28)


def _source(source_id: str = "SEC:1:A-1") -> SourceProvenance:
    return SourceProvenance(
        source_id=source_id, source_type=SourceType.SEC_10K, publisher="SEC", title="10-K",
        url=None, published_at=NOW, date_precision=DatePrecision.DATETIME, available_at=NOW,
        fetched_at=NOW, confidence=SourceConfidence.HIGH, pit_eligible=True,
    )


def _base_kwargs(**overrides):
    kwargs = dict(
        run_id="RUN-1", generated_at=NOW, data_cutoff=NOW, ticker="ACME",
        candidate_source=CandidateSource.E3_P1_HIGH, collection_depth=CollectionDepth.FULL,
        evidence_completeness=EvidenceCompleteness.COMPLETE,
        identity={}, market={}, fundamentals={}, fundamental_changes={}, balance_sheet={},
        cashflow={}, price_context={}, earnings={}, filings=[], company_releases=[],
        earnings_materials=[], investor_materials=[], material_events=[], source_manifest=[],
        data_quality={}, unknown_fields=[],
    )
    kwargs.update(overrides)
    return kwargs


def test_minimal_bundle_validates():
    bundle = EvidenceBundleV2(**_base_kwargs())
    assert bundle.thesis == NotResearched.NOT_RESEARCHED
    assert bundle.decision == NotResearched.NOT_RESEARCHED


def test_extra_field_rejected():
    with pytest.raises(ValidationError):
        EvidenceBundleV2(**_base_kwargs(valuation_target=100))


def test_aware_datetime_required():
    with pytest.raises(ValidationError):
        EvidenceBundleV2(**_base_kwargs(generated_at=NOW.replace(tzinfo=None)))


def test_ai_fields_cannot_be_fabricated():
    with pytest.raises(ValidationError):
        EvidenceBundleV2(**_base_kwargs(thesis="this is clearly undervalued"))


def test_orphan_material_event_rejected():
    orphan = MaterialEventRef(source_id="SEC:1:DOES-NOT-EXIST", item_code="2.02",
                               item_label="Results", item_category="EARNINGS_RESULTS",
                               filing_date="2026-06-01")
    with pytest.raises(ValidationError):
        EvidenceBundleV2(**_base_kwargs(company_releases=[orphan]))


def test_material_event_referencing_a_real_filing_is_accepted():
    source = _source()
    event = MaterialEventRef(source_id=source.source_id, item_code="2.02", item_label="Results",
                              item_category="EARNINGS_RESULTS", filing_date="2026-06-01")
    bundle = EvidenceBundleV2(**_base_kwargs(filings=[source], company_releases=[event]))
    assert bundle.company_releases[0].source_id == source.source_id


def test_material_event_referencing_source_manifest_only_is_also_accepted():
    source = _source("SEC:1:MANIFEST-ONLY")
    event = MaterialEventRef(source_id=source.source_id, item_code="7.01", item_label="Reg FD",
                              item_category="REGULATION_FD_DISCLOSURE", filing_date="2026-06-01")
    bundle = EvidenceBundleV2(**_base_kwargs(source_manifest=[source], investor_materials=[event]))
    assert bundle.investor_materials[0].item_code == "7.01"


@pytest.mark.parametrize("banned", [
    "undervalued", "strong moat", "APPROVE", "REJECT", "good company",
])
def test_banned_interpretive_language_rejected(banned):
    with pytest.raises(ValidationError):
        EvidenceBundleV2(**_base_kwargs(data_quality={"note": f"this looks {banned}"}))


def test_manual_candidate_source_is_distinguishable_from_e3_output():
    manual = EvidenceBundleV2(**_base_kwargs(candidate_source=CandidateSource.MANUAL))
    e3 = EvidenceBundleV2(**_base_kwargs(candidate_source=CandidateSource.E3_P2_MEDIUM))
    assert manual.candidate_source != e3.candidate_source
    assert manual.candidate_source == CandidateSource.MANUAL
