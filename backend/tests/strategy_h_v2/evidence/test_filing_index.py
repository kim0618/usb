from __future__ import annotations

from app.backtest.strategy_h_v2.evidence.filing_index import find_exhibits_by_type_prefix, parse_filing_index

SAMPLE_INDEX_HTML = """
<table class="tableFile" summary="Document Format Files">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr>
  <td>1</td><td>8-K</td>
  <td><a href="/ix?doc=/Archives/edgar/data/1/000001/main.htm">main.htm</a></td>
  <td>8-K</td><td>38350</td>
</tr>
<tr class="evenRow">
  <td>2</td><td>EX-99.1</td>
  <td><a href="/Archives/edgar/data/1/000001/ex991.htm">ex991.htm</a></td>
  <td>EX-99.1</td><td>173484</td>
</tr>
<tr>
  <td>3</td><td>Investor Presentation Slides</td>
  <td><a href="/Archives/edgar/data/1/000001/ex992.htm">ex992.htm</a></td>
  <td>EX-99.2</td><td>50000</td>
</tr>
</table>
"""


def test_parses_document_rows_with_expected_fields():
    docs = parse_filing_index(SAMPLE_INDEX_HTML)
    assert len(docs) == 3
    assert docs[0].document_name == "main.htm"
    assert docs[0].doc_type == "8-K"


def test_ix_viewer_wrapped_href_resolved_to_real_document_path():
    docs = parse_filing_index(SAMPLE_INDEX_HTML)
    assert docs[0].document_path == "/Archives/edgar/data/1/000001/main.htm"


def test_find_exhibits_by_type_prefix_matches_case_insensitively():
    docs = parse_filing_index(SAMPLE_INDEX_HTML)
    matches = find_exhibits_by_type_prefix(docs, "ex-99")
    assert [d.document_name for d in matches] == ["ex991.htm", "ex992.htm"]


def test_no_match_returns_empty_list():
    docs = parse_filing_index(SAMPLE_INDEX_HTML)
    assert find_exhibits_by_type_prefix(docs, "EX-10") == []


def test_malformed_html_yields_empty_list_not_a_crash():
    assert parse_filing_index("<html>not a filing index</html>") == []


def test_row_with_wrong_cell_count_is_skipped():
    bad = "<tr><td>only</td><td>two</td></tr>"
    assert parse_filing_index(bad) == []
