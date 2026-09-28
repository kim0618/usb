"""Deterministic content extraction: raw bytes -> plain text -> (best-effort) section split.

No AI summarization anywhere in this module (D2.1 brief §10): every transformation here is
mechanical - tag stripping, whitespace normalization, a fixed table-cell join, and a conservative
regex section-heading match that prefers `SECTION_UNRESOLVED` over a wrong label. Uses only the
Python standard library (`html.parser`) - no new HTML/PDF dependency was added, matching the
brief's instruction not to build a new heavy parsing system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from html.parser import HTMLParser
import re


class ExtractionStatus(StrEnum):
    EXTRACTED = "EXTRACTED"
    EMPTY_CONTENT = "EMPTY_CONTENT"
    CONTENT_NOT_EXTRACTABLE = "CONTENT_NOT_EXTRACTABLE"


class ContentType(StrEnum):
    HTML = "HTML"
    TXT = "TXT"
    PDF = "PDF"
    UNKNOWN = "UNKNOWN"


def detect_content_type(document_name: str, body: bytes) -> ContentType:
    lower = document_name.lower()
    if lower.endswith((".htm", ".html")):
        return ContentType.HTML
    if lower.endswith(".txt"):
        return ContentType.TXT
    if lower.endswith(".pdf") or body[:5] == b"%PDF-":
        return ContentType.PDF
    return ContentType.UNKNOWN


_SKIP_TAGS = frozenset({"script", "style", "head"})
_VOID_TAGS = frozenset({
    "br", "img", "hr", "meta", "link", "input", "col", "area", "base", "embed", "source",
    "track", "wbr",
})


def _is_display_none(attrs: list[tuple[str, str | None]]) -> bool:
    for name, value in attrs:
        if name == "style" and value and "display:none" in value.replace(" ", "").lower():
            return True
    return False


class _HTMLTextExtractor(HTMLParser):
    """Strips markup, drops script/style content, joins table cells with ' | ', and inserts a
    newline at block-level boundaries so section-heading regexes can anchor on line starts.

    Also drops any element (and everything inside it) whose own `style` attribute says
    `display:none` - inline-XBRL documents wrap their entire machine-readable header/hidden-facts
    block this way (`<div style="display:none"><ix:header>...`), and without this check that block
    (XBRL concept names, context IDs, unit references) leaks into extracted text as the very first,
    highest-priority chunk of every filing. This was caught by inspecting real materialized output
    from the D2.1 full run, not assumed in advance.
    """

    _BLOCK_TAGS = frozenset({
        "p", "div", "br", "tr", "table", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []
        self._stack: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        triggers_skip = tag in _SKIP_TAGS or _is_display_none(attrs)
        if triggers_skip:
            self._skip_depth += 1
        if tag not in _VOID_TAGS:
            self._stack.append((tag, triggers_skip))
        if self._skip_depth == 0:
            if tag in self._BLOCK_TAGS:
                self._parts.append("\n")
            elif tag == "td":
                self._parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return  # never pushed in handle_starttag, so there is nothing to pop for it
        while self._stack:
            name, triggered = self._stack.pop()
            if triggered:
                self._skip_depth = max(0, self._skip_depth - 1)
            if name == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    raw = parser.text()
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_text(document_name: str, body: bytes) -> tuple[str, ExtractionStatus, ContentType]:
    content_type = detect_content_type(document_name, body)
    if content_type == ContentType.PDF:
        return "", ExtractionStatus.CONTENT_NOT_EXTRACTABLE, content_type
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError:
        decoded = body.decode("latin-1", errors="replace")
    text = html_to_text(decoded) if content_type == ContentType.HTML else decoded.strip()
    if not text.strip():
        return "", ExtractionStatus.EMPTY_CONTENT, content_type
    return text, ExtractionStatus.EXTRACTED, content_type


#: Conservative section-heading patterns for 10-K/10-Q text. Deliberately narrow: a heading line
#: must consist of (almost) only the item number and title, matching how EDGAR documents render a
#: real heading on its own line - a table-of-contents entry or an inline cross-reference
#: ("as discussed in Item 1A") will not match this pattern and correctly falls through to
#: SECTION_UNRESOLVED rather than producing a false section boundary (D2.1 brief §11).
_SECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("BUSINESS", re.compile(r"^item\s+1\.?\s*[:.]?\s*business\s*$", re.IGNORECASE)),
    ("RISK_FACTORS", re.compile(r"^item\s+1a\.?\s*[:.]?\s*risk\s+factors\s*$", re.IGNORECASE)),
    ("MD_AND_A", re.compile(
        r"^item\s+(?:7|2)\.?\s*[:.]?\s*management.?s\s+discussion\s+and\s+analysis", re.IGNORECASE)),
    ("RESULTS_OF_OPERATIONS", re.compile(r"^results\s+of\s+operations\s*$", re.IGNORECASE)),
    ("LIQUIDITY_AND_CAPITAL_RESOURCES", re.compile(
        r"^liquidity\s+and\s+capital\s+resources\s*$", re.IGNORECASE)),
)


@dataclass(frozen=True)
class SectionSpan:
    section: str
    start_line: int
    end_line: int


def detect_sections(text: str) -> list[SectionSpan]:
    lines = text.splitlines()
    hits: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        for name, pattern in _SECTION_PATTERNS:
            if pattern.match(line.strip()):
                hits.append((index, name))
                break
    spans = []
    for i, (start, name) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(lines)
        spans.append(SectionSpan(section=name, start_line=start, end_line=end))
    return spans
