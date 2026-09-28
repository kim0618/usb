from __future__ import annotations

from helpers import utc

from app.backtest.strategy_h_v2.evidence.chunk_schema import MaterializationStatus
from app.backtest.strategy_h_v2.evidence.chunking import chunk_document
from app.backtest.strategy_h_v2.evidence.sources import DatePrecision, SourceConfidence, SourceProvenance, SourceType
from app.backtest.strategy_h_v2.evidence.text_extraction import SectionSpan

NOW = utc(2026, 9, 28)


def _source(source_id: str = "SEC:1:A-1") -> SourceProvenance:
    return SourceProvenance(
        source_id=source_id, source_type=SourceType.SEC_10K, publisher="SEC", title="10-K",
        url=None, published_at=NOW, date_precision=DatePrecision.DATETIME, available_at=NOW,
        fetched_at=NOW, confidence=SourceConfidence.HIGH, pit_eligible=True,
    )


def test_same_text_produces_same_chunks_and_checksums():
    text = "line one\nline two\nline three\n" * 200
    first = chunk_document(text, source=_source(), candidate_id="C1")
    second = chunk_document(text, source=_source(), candidate_id="C1")
    assert [c.content_checksum for c in first] == [c.content_checksum for c in second]
    assert [c.evidence_id for c in first] == [c.evidence_id for c in second]


def test_chunks_never_exceed_size_target_by_much():
    text = "x" * 50 + "\n"
    text = text * 500  # ~25,500 chars
    chunks = chunk_document(text, source=_source(), candidate_id="C1")
    assert all(len(c.text) <= 4100 for c in chunks)  # small slack for the trailing line


def test_all_chunks_carry_the_same_single_source_id():
    text = "para one.\n" * 1000
    chunks = chunk_document(text, source=_source(), candidate_id="C1")
    assert len({c.source_id for c in chunks}) == 1
    assert chunks[0].source_id == "SEC:1:A-1"


def test_section_boundaries_are_never_split_across_two_chunks_worth_of_section_name():
    text = "\n".join(["intro"] + ["body line"] * 5 + ["ITEM_A_HEADING"] + ["a line"] * 5)
    spans = [SectionSpan(section="RISK_FACTORS", start_line=6, end_line=12)]
    chunks = chunk_document(text, source=_source(), candidate_id="C1", sections=spans)
    sectioned = [c for c in chunks if c.section == "RISK_FACTORS"]
    unsectioned = [c for c in chunks if c.section is None]
    assert sectioned and unsectioned
    assert all("intro" not in c.text for c in sectioned)


def test_empty_text_produces_no_chunks():
    assert chunk_document("", source=_source(), candidate_id="C1") == []


def test_chunk_index_and_count_are_consistent():
    text = "y" * 20000
    chunks = chunk_document(text, source=_source(), candidate_id="C1")
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.chunk_count == len(chunks) for c in chunks)


def test_extraction_status_is_extracted_for_every_chunk():
    chunks = chunk_document("hello world", source=_source(), candidate_id="C1")
    assert all(c.extraction_status == MaterializationStatus.EXTRACTED for c in chunks)
