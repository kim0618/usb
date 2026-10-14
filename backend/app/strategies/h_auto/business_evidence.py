"""R3.1 V2: business evidence for the deterministic D3, under ``H_D3D4_DETERMINISTIC_PRODUCTION_V2``.

V1 read "business supported" as "the LATEST_10K has a chunk labelled BUSINESS". D2.1's section
detector is deliberately narrow (``text_extraction._SECTION_PATTERNS``), so a real Item 1 headed
``ITEM 1. Business.`` or ``Item 1.  BUSINESS.`` was chunked without a label, and V1 turned that
collection artifact into D6's ``thesis_unsupported`` REJECT (7 of 22 stored packages, DORM in the
replay).

V2 (contract ``d3.R3.1_V2_business_evidence``, operator decision A):

1. the explicit BUSINESS chunk, as before;
2. otherwise Item 1 located in the same 10-K's text by a fixed heading grammar and cut at Item 1A;
3. otherwise ``BUSINESS_EVIDENCE_NOT_READY``, which the D3 generator turns into a refusal, never into
   an empty business model. Extraction failure is not "business unsupported".

Nothing outside the LATEST_10K the frozen D2 selection chose is read: a Part III 10-K/A has no Item 1
and stays NOT_READY rather than reaching for another filing.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

EXTRACTION_METHOD = "H_ITEM1_FALLBACK_V1"
P1, P2 = "P1_EXPLICIT_BUSINESS_CHUNK", "P2_ITEM1_FALLBACK"
NOT_READY = "BUSINESS_EVIDENCE_NOT_READY"

_BUSINESS_TITLE = r"(?:description\s+of\s+(?:the\s+)?)?business(?:\s+and\s+properties)?\s*\.?"
_HEADING_ONE_LINE = re.compile(r"^items?\s*1(?:\s*and\s*2)?\s*[.:\-–—]?\s*" + _BUSINESS_TITLE + r"$",
                               re.IGNORECASE)
_HEADING_BARE = re.compile(r"^item\s*1\s*[.:\-–—]?$", re.IGNORECASE)
_TITLE_LINE = re.compile(r"^" + _BUSINESS_TITLE + r"$", re.IGNORECASE)
_TERMINATOR = re.compile(r"^item\s*(?:1a|1b|1c|2)\b", re.IGNORECASE)
_LETTER = re.compile(r"[A-Za-z]")


def _norm(line: str) -> str:
    return " ".join(line.replace("\xa0", " ").split())


def _is_table(line: str) -> bool:
    return "|" in line


def latest_10k(package: Any) -> Any | None:
    docs = [d for d in package.materialization.documents if d.role == "LATEST_10K" and d.chunk_count > 0]
    return docs[0] if docs else None


def _source(package: Any, source_id: str) -> Any | None:
    return next((s for s in package.source_manifest if s.source_id == source_id), None)


def _form(source: Any) -> str | None:
    head = str(getattr(source, "title", "") or "").split(" - ", 1)[0].strip()
    return head if re.fullmatch(r"10-K(?:/A)?", head) else None


def locate_item1(text: str) -> dict[str, Any]:
    """Item 1 span in ``text`` (contract heading grammar / terminator / qualifying rule). Pure."""
    raw_lines = text.split("\n")
    starts, pos = [], 0
    for line in raw_lines:
        starts.append(pos)
        pos += len(line) + 1
    lines = [_norm(line) for line in raw_lines]
    headings: list[tuple[int, int]] = []          # (heading line, first body line)
    for i, line in enumerate(lines):
        if not line or _is_table(line):
            continue
        if _HEADING_ONE_LINE.match(line):
            headings.append((i, i + 1))
        elif _HEADING_BARE.match(line):
            j = next((k for k in range(i + 1, len(lines)) if lines[k]), None)
            if j is not None and not _is_table(lines[j]) and _TITLE_LINE.match(lines[j]):
                headings.append((i, j + 1))
    if not headings:
        return {"state": NOT_READY, "reason": "NO_ITEM_1_HEADING"}
    malformed = 0
    for head, body in headings:
        end = next((k for k in range(body, len(lines))
                    if lines[k] and not _is_table(lines[k]) and _TERMINATOR.match(lines[k])), None)
        if end is None:
            malformed += 1
            continue
        prose = [k for k in range(body, end) if lines[k] and not _is_table(lines[k]) and _LETTER.search(lines[k])
                 and not _HEADING_ONE_LINE.match(lines[k]) and not _HEADING_BARE.match(lines[k])
                 and not _TITLE_LINE.match(lines[k])]
        if not prose:
            continue
        start_offset, end_offset = starts[head], starts[end]
        return {"state": "FOUND", "heading": lines[head], "terminator": lines[end],
                "start_offset": start_offset, "end_offset": end_offset, "body_offset": starts[prose[0]]}
    return {"state": NOT_READY, "reason": "ITEM_1_HEADING_WITHOUT_TERMINATOR" if malformed == len(headings)
            else "ITEM_1_HEADING_WITHOUT_BODY"}


def business_evidence(package: Any) -> dict[str, Any]:
    """The R3.1 V2 evidence for ``package`` (an ``AIResearchInputV1``), with its provenance."""
    doc = latest_10k(package)
    if doc is None:
        return {"state": NOT_READY, "reason": "NO_LATEST_10K_CHUNKS", "source_priority": None}
    chunks = sorted((c for c in package.chunks if c.source_id == doc.source_id), key=lambda c: c.chunk_index)
    source = _source(package, doc.source_id)
    base = {"source_id": doc.source_id, "accession": getattr(source, "accession", None),
            "form": _form(source) if source is not None else None,
            "filing_date": (source.published_at.date().isoformat()
                            if source is not None and source.published_at is not None else None)}
    labelled = [c for c in chunks if c.section == "BUSINESS"]
    if labelled:
        return base | {"state": "FOUND", "source_priority": P1, "cited_evidence_id": labelled[0].evidence_id,
                       "evidence_ids_spanned": [c.evidence_id for c in labelled],
                       "extraction_method": "D2_1_SECTION_LABEL", "start_offset": None, "end_offset": None,
                       "sha256": hashlib.sha256("\n".join(c.text for c in labelled).encode()).hexdigest()}
    text = "\n".join(c.text for c in chunks)
    found = locate_item1(text)
    if found["state"] != "FOUND":
        reason = found["reason"]
        if base["form"] == "10-K/A" and reason == "NO_ITEM_1_HEADING":
            reason = "AMENDMENT_WITHOUT_ITEM_1"
        return base | {"state": NOT_READY, "reason": reason, "source_priority": None,
                       "extraction_method": EXTRACTION_METHOD}
    bounds, pos = [], 0
    for c in chunks:
        bounds.append((pos, pos + len(c.text), c))
        pos += len(c.text) + 1
    spanned = [c.evidence_id for s, e, c in bounds if s < found["end_offset"] and e > found["start_offset"]]
    cited = next(c.evidence_id for s, e, c in bounds if s <= found["body_offset"] < e + 1)
    span = text[found["start_offset"]:found["end_offset"]]
    return base | {"state": "FOUND", "source_priority": P2, "cited_evidence_id": cited,
                   "evidence_ids_spanned": spanned, "extraction_method": EXTRACTION_METHOD,
                   "start_offset": found["start_offset"], "end_offset": found["end_offset"],
                   "heading": found["heading"], "terminator": found["terminator"],
                   "sha256": hashlib.sha256(span.encode()).hexdigest()}


def summary(evidence: Mapping[str, Any]) -> str:
    return f"{evidence.get('state')}:{evidence.get('source_priority') or evidence.get('reason')}"
