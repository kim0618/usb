"""Deterministic parser for SEC's own per-filing document index page
(`.../{accession}-index.htm`), the structured "Document Format Files" table SEC serves for every
filing. This is the mechanism the D2.1 brief requires for exhibit resolution: never assume
"EX-99.1 = earnings release", read the filing's own Type/Description columns instead.

Pure text parsing, no network. Regex-based rather than a full HTML parser because EDGAR's index
table markup is a fixed, simple template (`<tr>` with five `<td>` cells); if a page's structure
does not match, this module returns an empty list rather than guessing at a looser pattern.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>([^<]*)</a>', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


@dataclass(frozen=True)
class IndexDocument:
    seq: str
    description: str
    document_name: str
    document_path: str
    doc_type: str
    size: str


def _resolve_href(href: str) -> str:
    """The primary document in a filing's own row is often wrapped in the inline-XBRL viewer link
    (`/ix?doc=/Archives/...`); the real document path is the `doc=` query value."""
    marker = "doc="
    if marker in href:
        return href.split(marker, 1)[1]
    return href


def parse_filing_index(html: str) -> list[IndexDocument]:
    documents: list[IndexDocument] = []
    for row_html in _ROW_RE.findall(html):
        cells = _CELL_RE.findall(row_html)
        if len(cells) != 5:
            continue
        seq, description, document_cell, doc_type, size = cells
        link_match = _LINK_RE.search(document_cell)
        if not link_match:
            continue
        href, link_text = link_match.groups()
        document_path = _resolve_href(href)
        documents.append(IndexDocument(
            seq=_clean(seq), description=_clean(description),
            document_name=_clean(link_text) or document_path.rsplit("/", 1)[-1],
            document_path=document_path, doc_type=_clean(doc_type), size=_clean(size),
        ))
    return documents


def find_exhibits_by_type_prefix(documents: list[IndexDocument], prefix: str) -> list[IndexDocument]:
    """`prefix` matched case-insensitively against the filing's own `Type` column (e.g. `EX-99`),
    ordered by sequence as SEC listed them - never re-sorted by any content heuristic."""
    prefix_lower = prefix.lower()
    matches = [doc for doc in documents if doc.doc_type.lower().startswith(prefix_lower)]
    return sorted(matches, key=lambda d: (len(d.seq), d.seq))
