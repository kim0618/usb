"""D2.1 raw filing/exhibit document cache - separate from D1.1's submissions/companyfacts cache
(D2.1 brief §6/§16), mirroring the same raw-bytes-plus-ledger immutability convention as
`strategy_c_e0.sec_store`/`strategy_eqm_v0.xbrl_store` (an existing file plus its ledger is never
re-fetched or overwritten). Reuses `SecClient` unmodified for every actual HTTP request.
"""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

from app.backtest.strategy_c_e0.sec_store import SecClient, SecFetchError

SEC_ARCHIVES_HOST = "https://www.sec.gov"


def cache_path(root: Path, cik: str, accession_no_dashes: str, document_name: str) -> Path:
    return root / "raw_source_cache" / cik / accession_no_dashes / f"{document_name}.gz"


def ledger_path(path: Path) -> Path:
    return path.with_name(path.name + ".request.json")


def _write_gz(path: Path, body: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with gzip.GzipFile(filename="", mode="wb", fileobj=open(partial, "wb"), mtime=0) as handle:
        handle.write(body)
    partial.replace(path)
    return hashlib.sha256(body).hexdigest()


def fetch_document(
    client: SecClient | None,
    root: Path,
    *,
    cik: str,
    accession_no_dashes: str,
    document_name: str,
    document_path: str,
) -> tuple[bytes | None, str | None, str]:
    """Returns (body, checksum, outcome). `outcome` is `CACHED` / `FETCHED` / `MISSING` / `FAILED` -
    a 404 is `MISSING` (a fact about the document, not an error); a transport/HTTP failure after
    retry is `FAILED`, never silently treated as `MISSING`."""
    path = cache_path(root, cik, accession_no_dashes, document_name)
    ledger = ledger_path(path)
    if path.exists() and ledger.exists():
        body = gzip.decompress(path.read_bytes())
        info = json.loads(ledger.read_text())
        return body, info.get("raw_sha256"), "CACHED"
    if client is None:
        return None, None, "MISSING"
    url = f"{SEC_ARCHIVES_HOST}{document_path}" if document_path.startswith("/") else document_path
    try:
        body = client.get(url, accept="*/*")
    except SecFetchError as error:
        if error.code == "NOT_FOUND":
            return None, None, "MISSING"
        return None, None, "FAILED"
    checksum = _write_gz(path, body)
    ledger.write_text(json.dumps({
        "url": url, "cik": cik, "accession": accession_no_dashes, "document": document_name,
        "raw_sha256": checksum, "raw_bytes": len(body),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": 200,
    }, sort_keys=True) + "\n")
    return body, checksum, "FETCHED"


def fetch_filing_index(
    client: SecClient | None, root: Path, *, cik: str, accession: str, accession_no_dashes: str,
) -> tuple[str | None, str]:
    """Same cache/ledger convention, for the `-index.htm` document-table page itself."""
    name = f"{accession}-index.htm"
    body, _checksum, outcome = fetch_document(
        client, root, cik=cik, accession_no_dashes=accession_no_dashes, document_name=name,
        document_path=f"/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/{name}",
    )
    return (body.decode("utf-8", errors="replace") if body else None), outcome
