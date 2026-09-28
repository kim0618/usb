from __future__ import annotations

from helpers import utc

from app.backtest.strategy_h_v2.evidence.bundle import (
    CandidateSource,
    CollectionDepth,
    EvidenceBundleV2,
    EvidenceCompleteness,
    MaterialEventRef,
)
from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1, MaterializationStatus
from app.backtest.strategy_h_v2.evidence.materialize import is_presentation_confirmed, materialize_candidate
from app.backtest.strategy_h_v2.evidence.sources import DatePrecision, SourceConfidence, SourceProvenance, SourceType

NOW = utc(2026, 9, 28)
CIK = "1"


class FakeClient:
    def __init__(self, responses: dict[str, bytes]):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, *, accept: str = "*/*") -> bytes:
        self.calls.append(url)
        if url not in self.responses:
            from app.backtest.strategy_c_e0.sec_store import SecFetchError
            raise SecFetchError("NOT_FOUND", url)
        return self.responses[url]


def _filing_source(role: str, accession: str, url: str, source_type: SourceType,
                    pit_eligible: bool = True) -> SourceProvenance:
    return SourceProvenance(
        source_id=f"SEC:{CIK}:{accession}", source_type=source_type, publisher="SEC", title=role,
        url=url, published_at=NOW, date_precision=DatePrecision.DATETIME, available_at=NOW,
        fetched_at=NOW, accession=accession, confidence=SourceConfidence.HIGH,
        pit_eligible=pit_eligible,
    )


TENK = _filing_source("10-K", "A-1", "https://www.sec.gov/Archives/edgar/data/1/A1/tenk.htm", SourceType.SEC_10K)
TENQ1 = _filing_source("10-Q", "A-2", "https://www.sec.gov/Archives/edgar/data/1/A2/tenq1.htm", SourceType.SEC_10Q)
TENQ2 = _filing_source("10-Q", "A-3", "https://www.sec.gov/Archives/edgar/data/1/A3/tenq2.htm", SourceType.SEC_10Q)
EIGHTK_202 = _filing_source("8-K", "A-4", "https://www.sec.gov/Archives/edgar/data/1/A4/8k1.htm", SourceType.SEC_8K)
EIGHTK_701 = _filing_source("8-K", "A-5", "https://www.sec.gov/Archives/edgar/data/1/A5/8k2.htm", SourceType.SEC_8K)
EIGHTK_OTHER = _filing_source("8-K", "A-6", "https://www.sec.gov/Archives/edgar/data/1/A6/8k3.htm", SourceType.SEC_8K)
FUTURE_10Q = _filing_source("10-Q", "A-9", "https://www.sec.gov/Archives/edgar/data/1/A9/future.htm",
                             SourceType.SEC_10Q, pit_eligible=False)


def _index_html(rows: list[tuple[str, str, str, str]]) -> bytes:
    trs = "".join(
        f'<tr><td>{i+1}</td><td>{desc}</td><td><a href="{path}">{name}</a></td><td>{dtype}</td><td>1</td></tr>'
        for i, (desc, path, name, dtype) in enumerate(rows)
    )
    return f'<table class="tableFile">{trs}</table>'.encode()


RESPONSES = {
    str(TENK.url): b"<html><body>Item 1. Business\nWe make things.</body></html>",
    str(TENQ1.url): b"<html><body>Quarterly results text one.</body></html>",
    str(TENQ2.url): b"<html><body>Quarterly results text two.</body></html>",
    "https://www.sec.gov/Archives/edgar/data/1/A4/A-4-index.htm": _index_html([
        ("8-K", "/Archives/edgar/data/1/A4/8k1.htm", "8k1.htm", "8-K"),
        ("EX-99.1", "/Archives/edgar/data/1/A4/ex991.htm", "ex991.htm", "EX-99.1"),
    ]),
    "https://www.sec.gov/Archives/edgar/data/1/A4/ex991.htm": b"<html><body>Q3 earnings release text.</body></html>",
    "https://www.sec.gov/Archives/edgar/data/1/A5/A-5-index.htm": _index_html([
        ("8-K", "/Archives/edgar/data/1/A5/8k2.htm", "8k2.htm", "8-K"),
        ("Investor Presentation", "/Archives/edgar/data/1/A5/ex992.htm", "ex992.htm", "EX-99.2"),
    ]),
    "https://www.sec.gov/Archives/edgar/data/1/A5/ex992.htm": b"<html><body>Deck slides text.</body></html>",
    str(EIGHTK_OTHER.url): b"<html><body>Executive change announcement.</body></html>",
}


def _bundle(depth: CollectionDepth) -> EvidenceBundleV2:
    filings = [TENK, TENQ1, TENQ2, EIGHTK_202, EIGHTK_701, EIGHTK_OTHER]
    return EvidenceBundleV2(
        run_id="RUN-1", generated_at=NOW, data_cutoff=NOW, ticker="ACME",
        candidate_source=CandidateSource.E3_P1_HIGH if depth == CollectionDepth.FULL
                          else CandidateSource.E3_P2_MEDIUM,
        collection_depth=depth, evidence_completeness=EvidenceCompleteness.COMPLETE,
        identity={"cik": CIK}, market={}, fundamentals={}, fundamental_changes={}, balance_sheet={},
        cashflow={}, price_context={}, earnings={}, filings=filings,
        company_releases=[MaterialEventRef(source_id=EIGHTK_202.source_id, item_code="2.02",
                                            item_label="Results", item_category="EARNINGS_RESULTS",
                                            filing_date="2026-06-01")],
        investor_materials=[MaterialEventRef(source_id=EIGHTK_701.source_id, item_code="7.01",
                                              item_label="Reg FD", item_category="REGULATION_FD_DISCLOSURE",
                                              filing_date="2026-06-02")],
        material_events=[MaterialEventRef(source_id=EIGHTK_OTHER.source_id, item_code="5.02",
                                           item_label="Exec change", item_category="EXECUTIVE_CHANGES",
                                           filing_date="2026-06-03")],
        source_manifest=[], data_quality={}, unknown_fields=[],
    )


def test_full_depth_materializes_10k_two_10q_release_and_ir_presentation(tmp_path):
    client = FakeClient(RESPONSES)
    result = materialize_candidate(client, tmp_path, _bundle(CollectionDepth.FULL), candidate_id="C1")
    roles = {d.role: d.status for d in result.materialization.documents}
    assert roles["LATEST_10K"] == MaterializationStatus.EXTRACTED
    assert sum(1 for d in result.materialization.documents if d.role == "RECENT_10Q") == 2
    assert roles["EARNINGS_RELEASE"] == MaterializationStatus.EXTRACTED
    assert roles["IR_PRESENTATION"] == MaterializationStatus.EXTRACTED
    assert any(c.text for c in result.chunks)


def test_core_depth_only_materializes_required_documents(tmp_path):
    client = FakeClient(RESPONSES)
    result = materialize_candidate(client, tmp_path, _bundle(CollectionDepth.CORE), candidate_id="C1")
    roles = [d.role for d in result.materialization.documents]
    assert roles.count("RECENT_10Q") == 1
    assert "IR_PRESENTATION" not in roles
    assert "REG_FD_MATERIAL" not in roles  # not even attempted at CORE depth
    assert "EARNINGS_RELEASE" in roles


def test_2_02_exhibit_correctly_resolved_via_filing_index_not_assumed():
    assert is_presentation_confirmed("Investor Presentation") is True
    assert is_presentation_confirmed("EX-99.1") is False


def test_7_01_without_presentation_keyword_stays_generic_reg_fd_material(tmp_path):
    responses = dict(RESPONSES)
    responses["https://www.sec.gov/Archives/edgar/data/1/A5/A-5-index.htm"] = _index_html([
        ("8-K", "/Archives/edgar/data/1/A5/8k2.htm", "8k2.htm", "8-K"),
        ("EX-99.1", "/Archives/edgar/data/1/A5/ex992.htm", "ex992.htm", "EX-99.2"),  # no "presentation" keyword
    ])
    client = FakeClient(responses)
    result = materialize_candidate(client, tmp_path, _bundle(CollectionDepth.FULL), candidate_id="C1")
    roles = [d.role for d in result.materialization.documents]
    assert "REG_FD_MATERIAL" in roles
    assert "IR_PRESENTATION" not in roles


def test_future_source_is_never_fetched(tmp_path):
    """A self-consistent bundle (no material-event refs dangling) with only a 10-K and a
    not-yet-PIT-eligible 10-Q - `EvidenceBundleV2`'s own orphan-reference validator would reject a
    bundle that dropped a filing still referenced elsewhere, so this test does not reuse `_bundle()`
    and strip filings out from under it; it builds the minimal valid case directly."""
    client = FakeClient(RESPONSES)
    bundle = EvidenceBundleV2(
        run_id="RUN-1", generated_at=NOW, data_cutoff=NOW, ticker="ACME",
        candidate_source=CandidateSource.E3_P1_HIGH, collection_depth=CollectionDepth.FULL,
        evidence_completeness=EvidenceCompleteness.COMPLETE,
        identity={"cik": CIK}, market={}, fundamentals={}, fundamental_changes={}, balance_sheet={},
        cashflow={}, price_context={}, earnings={}, filings=[TENK, FUTURE_10Q],
        company_releases=[], investor_materials=[], material_events=[], source_manifest=[],
        data_quality={}, unknown_fields=[],
    )
    result = materialize_candidate(client, tmp_path, bundle, candidate_id="C1")
    assert str(FUTURE_10Q.url) not in client.calls
    tenq_docs = [d for d in result.materialization.documents if d.role == "RECENT_10Q"]
    assert tenq_docs and tenq_docs[0].status == MaterializationStatus.NOT_FETCHED


def test_unresolved_exhibit_when_index_has_no_matching_type(tmp_path):
    responses = dict(RESPONSES)
    responses["https://www.sec.gov/Archives/edgar/data/1/A4/A-4-index.htm"] = _index_html([
        ("8-K", "/Archives/edgar/data/1/A4/8k1.htm", "8k1.htm", "8-K"),
    ])  # no EX-99.* present
    client = FakeClient(responses)
    result = materialize_candidate(client, tmp_path, _bundle(CollectionDepth.FULL), candidate_id="C1")
    release_doc = next(d for d in result.materialization.documents if d.role == "EARNINGS_RELEASE")
    assert release_doc.status == MaterializationStatus.EXHIBIT_UNRESOLVED


def test_result_validates_as_ai_research_input_v1(tmp_path):
    client = FakeClient(RESPONSES)
    result = materialize_candidate(client, tmp_path, _bundle(CollectionDepth.CORE), candidate_id="C1")
    assert isinstance(result, AIResearchInputV1)
    # round-trips through the schema without extra/missing fields
    AIResearchInputV1.model_validate(result.model_dump())


def test_rerun_against_cache_produces_identical_chunks(tmp_path):
    client_a = FakeClient(RESPONSES)
    first = materialize_candidate(client_a, tmp_path, _bundle(CollectionDepth.CORE), candidate_id="C1")
    client_b = FakeClient(RESPONSES)
    second = materialize_candidate(client_b, tmp_path, _bundle(CollectionDepth.CORE), candidate_id="C1")
    assert [c.content_checksum for c in first.chunks] == [c.content_checksum for c in second.chunks]
    assert client_b.calls == []  # everything served from cache the second time
