"""Deterministic chunking: same text in -> same chunk boundaries, IDs, and checksums out
(D2.1 brief §13). No overlap (overlap would make "same input -> same chunks" harder to reason
about and is not required by any D3 contract yet); splits prefer a line boundary near the size
target so a chunk never cuts mid-sentence at an arbitrary byte offset.
"""

from __future__ import annotations

from datetime import datetime
import hashlib

from app.backtest.strategy_h_v2.evidence.chunk_schema import EvidenceChunk, MaterializationStatus
from app.backtest.strategy_h_v2.evidence.sources import SourceProvenance, SourceType
from app.backtest.strategy_h_v2.evidence.text_extraction import SectionSpan

CHUNK_SIZE_CHARS = 4000
"""~1,000-token estimate (4 chars/token, a standard rough English-text ratio) - large enough that a
chunk usually holds a complete paragraph or two of filing prose, small enough that a single chunk
is a reasonable LLM-context unit rather than an entire filing."""

ESTIMATED_CHARS_PER_TOKEN = 4


def _split_into_chunks(text: str, *, size: int = CHUNK_SIZE_CHARS) -> list[str]:
    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        if current_len + len(line) + 1 > size and current:
            chunks.append("\n".join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks or ([text] if text else [])


def chunk_document(
    text: str,
    *,
    source: SourceProvenance,
    candidate_id: str,
    sections: list[SectionSpan] | None = None,
) -> list[EvidenceChunk]:
    """Section boundaries, when known, are preferred chunk boundaries (a chunk never spans two
    named sections); within a section (or the whole document when no section was resolved) text is
    split by `CHUNK_SIZE_CHARS`."""
    lines = text.splitlines()
    if sections:
        covered = sorted(sections, key=lambda s: s.start_line)
        segments: list[tuple[str | None, str]] = []
        cursor = 0
        for span in covered:
            if span.start_line > cursor:
                segments.append((None, "\n".join(lines[cursor:span.start_line])))
            segments.append((span.section, "\n".join(lines[span.start_line:span.end_line])))
            cursor = span.end_line
        if cursor < len(lines):
            segments.append((None, "\n".join(lines[cursor:])))
    else:
        segments = [(None, text)]

    pieces: list[tuple[str | None, str]] = []
    for section_name, segment_text in segments:
        for piece in _split_into_chunks(segment_text):
            if piece.strip():
                pieces.append((section_name, piece))

    total = len(pieces)
    chunks: list[EvidenceChunk] = []
    for index, (section_name, piece_text) in enumerate(pieces):
        checksum = hashlib.sha256(piece_text.encode("utf-8")).hexdigest()
        chunks.append(EvidenceChunk(
            evidence_id=f"{source.source_id}:CHUNK:{index}",
            source_id=source.source_id, candidate_id=candidate_id, source_type=source.source_type,
            section=section_name, text=piece_text, published_at=source.published_at,
            available_at=source.available_at, fetched_at=source.fetched_at,
            content_checksum=checksum, chunk_index=index, chunk_count=total,
            pit_eligible=source.pit_eligible, extraction_status=MaterializationStatus.EXTRACTED,
        ))
    return chunks
