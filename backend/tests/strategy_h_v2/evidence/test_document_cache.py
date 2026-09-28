from __future__ import annotations

from pathlib import Path

from app.backtest.strategy_h_v2.evidence.document_cache import cache_path, fetch_document, ledger_path


class FakeClient:
    def __init__(self, responses: dict[str, bytes]):
        self.responses = responses
        self.calls = 0

    def get(self, url: str, *, accept: str = "*/*") -> bytes:
        self.calls += 1
        return self.responses[url]


def test_first_fetch_writes_cache_and_ledger(tmp_path: Path):
    client = FakeClient({"https://www.sec.gov/Archives/edgar/data/1/000001/doc.htm": b"hello"})
    body, checksum, outcome = fetch_document(
        client, tmp_path, cik="0000000001", accession_no_dashes="000001", document_name="doc.htm",
        document_path="/Archives/edgar/data/1/000001/doc.htm",
    )
    assert outcome == "FETCHED"
    assert body == b"hello"
    assert cache_path(tmp_path, "0000000001", "000001", "doc.htm").exists()
    assert ledger_path(cache_path(tmp_path, "0000000001", "000001", "doc.htm")).exists()
    assert client.calls == 1


def test_second_fetch_is_cached_and_does_not_call_client_again(tmp_path: Path):
    client = FakeClient({"https://www.sec.gov/Archives/edgar/data/1/000001/doc.htm": b"hello"})
    fetch_document(client, tmp_path, cik="0000000001", accession_no_dashes="000001",
                    document_name="doc.htm", document_path="/Archives/edgar/data/1/000001/doc.htm")
    body, checksum, outcome = fetch_document(
        client, tmp_path, cik="0000000001", accession_no_dashes="000001", document_name="doc.htm",
        document_path="/Archives/edgar/data/1/000001/doc.htm",
    )
    assert outcome == "CACHED"
    assert body == b"hello"
    assert client.calls == 1  # not called again


def test_checksum_is_stable_across_fetch_and_cache_hit(tmp_path: Path):
    client = FakeClient({"https://www.sec.gov/Archives/edgar/data/1/000001/doc.htm": b"hello"})
    _, checksum_first, _ = fetch_document(
        client, tmp_path, cik="0000000001", accession_no_dashes="000001", document_name="doc.htm",
        document_path="/Archives/edgar/data/1/000001/doc.htm",
    )
    _, checksum_second, _ = fetch_document(
        client, tmp_path, cik="0000000001", accession_no_dashes="000001", document_name="doc.htm",
        document_path="/Archives/edgar/data/1/000001/doc.htm",
    )
    assert checksum_first == checksum_second


def test_no_client_and_no_cache_is_missing_not_an_error(tmp_path: Path):
    body, checksum, outcome = fetch_document(
        None, tmp_path, cik="0000000001", accession_no_dashes="000001", document_name="doc.htm",
        document_path="/Archives/edgar/data/1/000001/doc.htm",
    )
    assert outcome == "MISSING"
    assert body is None
