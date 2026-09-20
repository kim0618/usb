"""EQM-V0 raw XBRL store: `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`, provider bytes.

One file per CIK plus one request ledger, the same write contract as the C-E0 submissions store
(`app.backtest.strategy_c_e0.sec_store`), whose rate-limited client this module reuses so that the
declared SEC contact and the 5 req/s ceiling stay in one place.

Nothing here is derived: a fact is read back exactly as SEC served it, and a fact is only ever
usable through the accession that carries it, which is what makes the point-in-time claim checkable
against the C-E0 submissions store's `acceptanceDateTime`.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from app.backtest.strategy_c_e0.sec_store import SecClient, SecFetchError, ledger_path

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
DEFAULT_ROOT = Path("data/runtime/strategy_eqm/v0/raw")
STORE_ID = "EQM_V0_XBRL_FACTS_STORE_V1"


class FactsUnavailable(RuntimeError):
    """SEC has no companyfacts document for this CIK (a filer that never filed XBRL)."""


@dataclass
class FetchReport:
    fetched: int = 0
    cached: int = 0
    missing: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    bytes_downloaded: int = 0


def facts_path(root: Path, cik: str) -> Path:
    return root / "companyfacts" / f"CIK{cik}.json.gz"


def read_facts(root: Path, cik: str) -> dict[str, Any] | None:
    path = facts_path(root, cik)
    if not path.exists() or not ledger_path(path).exists():
        return None
    return json.loads(gzip.decompress(path.read_bytes()))


def _write_gz(path: Path, body: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with gzip.GzipFile(filename="", mode="wb", fileobj=open(partial, "wb"), mtime=0) as handle:
        handle.write(body)
    partial.replace(path)
    return hashlib.sha256(body).hexdigest()


def fetch_cik(client: SecClient | None, root: Path, cik: str) -> str:
    """CACHED | FETCHED | MISSING. A missing document is a fact about the filer, not an error."""
    path = facts_path(root, cik)
    if path.exists() and ledger_path(path).exists():
        return "CACHED"
    if client is None:
        return "MISSING"
    url = COMPANYFACTS_URL.format(cik=cik)
    try:
        body = client.get(url)
    except SecFetchError as error:
        if error.code == "NOT_FOUND":
            return "MISSING"
        raise
    digest = _write_gz(path, body)
    ledger_path(path).write_text(json.dumps({
        "url": url, "cik": cik, "raw_sha256": digest, "raw_bytes": len(body),
        "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": 200},
        sort_keys=True) + "\n", encoding="utf-8")
    return "FETCHED"


def fetch_all(client: SecClient, root: Path, ciks: Sequence[str],
              *, log: Callable[[str], None] = lambda _m: None) -> FetchReport:
    report = FetchReport()
    for n, cik in enumerate(ciks, start=1):
        outcome = fetch_cik(client, root, cik)
        if outcome == "CACHED":
            report.cached += 1
        elif outcome == "FETCHED":
            report.fetched += 1
        else:
            report.missing.append(cik)
        if n % 50 == 0 or n == len(ciks):
            log(f"companyfacts {n}/{len(ciks)} fetched={report.fetched} cached={report.cached} "
                f"missing={len(report.missing)}")
    report.bytes_downloaded = client.accounting.bytes_downloaded
    return report


def store_digest(root: Path) -> str:
    """sha256 over (relative path, file sha256) of every stored document and ledger, sorted by path.

    The manifest is deliberately outside the digest: C-E0 ended with two digests for one store
    because the collector hashed before writing its manifest and the run hashed after
    (`C_STATUS_CLOSED_V1.md` §5-2). Hashing only `companyfacts/` makes the collector and every
    later reader agree by construction.
    """
    root = root / "companyfacts" if (root / "companyfacts").is_dir() else root
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.endswith(".partial")):
        digest.update(f"{path.relative_to(root)}\t{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode())
    return digest.hexdigest()
