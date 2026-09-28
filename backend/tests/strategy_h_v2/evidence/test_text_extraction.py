from __future__ import annotations

from app.backtest.strategy_h_v2.evidence.text_extraction import (
    ContentType,
    ExtractionStatus,
    detect_content_type,
    detect_sections,
    extract_text,
    html_to_text,
)


def test_html_tags_stripped_and_script_style_dropped():
    html = "<html><head><style>.x{color:red}</style></head><body><p>Hello</p>" \
           "<script>alert(1)</script><p>World</p></body></html>"
    text = html_to_text(html)
    assert "Hello" in text and "World" in text
    assert "alert" not in text and "color:red" not in text


def test_inline_xbrl_hidden_header_block_is_dropped():
    """Regression test for a real defect found on the D2.1 full run: inline-XBRL documents wrap
    their machine-readable header in `<div style="display:none"><ix:header>...`, and without
    stripping it, XBRL concept/context noise leaked into the first (highest-priority) chunk of
    every 10-K/10-Q."""
    html = (
        '<html><body><div style="display:none"><ix:header><ix:hidden>'
        '<ix:nonNumeric contextRef="c-1" name="dei:AmendmentFlag">false</ix:nonNumeric>'
        "</ix:hidden></ix:header></div>"
        "<p>Item 1. Business</p><p>We make useful things.</p></body></html>"
    )
    text = html_to_text(html)
    assert "AmendmentFlag" not in text
    assert "dei:" not in text
    assert "We make useful things" in text


def test_display_none_with_spaced_css_is_also_dropped():
    html = '<div style="display: none;">hidden text</div><p>visible text</p>'
    text = html_to_text(html)
    assert "hidden text" not in text
    assert "visible text" in text


def test_nested_visible_content_inside_non_hidden_siblings_is_preserved():
    html = (
        '<div style="display:none">hidden</div>'
        "<div>visible outer <span>visible inner</span></div>"
    )
    text = html_to_text(html)
    assert "hidden" not in text
    assert "visible outer" in text and "visible inner" in text


def test_self_closing_void_tags_do_not_corrupt_the_skip_stack():
    html = (
        '<div style="display:none">hidden<br/>more hidden</div>'
        "<p>visible after<br/>still visible</p>"
    )
    text = html_to_text(html)
    assert "hidden" not in text
    assert "visible after" in text and "still visible" in text


def test_table_cells_joined_with_separator():
    html = "<table><tr><td>Revenue</td><td>100</td></tr></table>"
    text = html_to_text(html)
    assert "Revenue" in text and "100" in text
    assert "|" in text


def test_extract_text_html_success():
    body = b"<html><body><p>Some filing content.</p></body></html>"
    text, status, ctype = extract_text("doc.htm", body)
    assert status == ExtractionStatus.EXTRACTED
    assert ctype == ContentType.HTML
    assert "Some filing content" in text


def test_extract_text_empty_content():
    body = b"<html><body>   </body></html>"
    text, status, ctype = extract_text("doc.htm", body)
    assert status == ExtractionStatus.EMPTY_CONTENT
    assert text == ""


def test_extract_text_pdf_is_not_extractable_no_ocr_attempted():
    body = b"%PDF-1.4 fake pdf bytes"
    text, status, ctype = extract_text("doc.pdf", body)
    assert status == ExtractionStatus.CONTENT_NOT_EXTRACTABLE
    assert ctype == ContentType.PDF
    assert text == ""


def test_detect_content_type_by_extension_and_magic_bytes():
    assert detect_content_type("a.htm", b"") == ContentType.HTML
    assert detect_content_type("a.txt", b"") == ContentType.TXT
    assert detect_content_type("a.bin", b"%PDF-1.4") == ContentType.PDF
    assert detect_content_type("a.bin", b"random") == ContentType.UNKNOWN


def test_clean_heading_on_its_own_line_is_detected():
    text = "Table of Contents\nItem 1. Business\nWe design products.\nItem 1A. Risk Factors\nRisks here."
    spans = detect_sections(text)
    names = [s.section for s in spans]
    assert "BUSINESS" in names
    assert "RISK_FACTORS" in names


def test_inline_cross_reference_does_not_falsely_trigger_a_section():
    text = "As discussed in Item 1A. Risk Factors above, we face risks. No heading line here."
    spans = detect_sections(text)
    assert spans == []


def test_no_recognizable_headings_yields_no_sections_not_a_guess():
    assert detect_sections("Just some ordinary prose with no headings at all.") == []
