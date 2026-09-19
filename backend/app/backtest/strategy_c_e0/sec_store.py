"""SEC EDGAR raw store for C-E0: provider bytes, one ledger per request, nothing derived.

Two endpoint families are read, both public and free:

* `data.sec.gov/submissions/CIK##########.json` and its older pages (`filings.files[].name`) -
  the event source. Only `accessionNumber`, `form`, `filingDate`, `acceptanceDateTime` and
  `items` are ever used; no document text is fetched.
* `www.sec.gov/Archives/.../<accession>-index-headers.html` - the `ACCEPTANCE-DATETIME` header
  of a sampled accession, for the timezone audit only.

SEC asks every automated reader to declare a real contact in the User-Agent. The identifier is
supplied by the caller; this module refuses to invent one and never defaults it.
"""

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any

import httpx

SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
HEADERS_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{plain}/{accession}-index-headers.html"
DEFAULT_ROOT = Path("data/runtime/strategy_c/e0/raw")
REQUESTS_PER_SECOND = 5.0  # SEC allows 10/s; half of it, serial, is the polite ceiling
TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 3
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_OLDER_PAGES = 12  # a filer with more than ~12k filings inside the window does not exist
FIELDS = ("accessionNumber", "form", "filingDate", "acceptanceDateTime", "items", "primaryDocument")
ACCEPTANCE_HEADER = re.compile(r"ACCEPTANCE-DATETIME>\s*(\d{14})")
USER_AGENT_PATTERN = re.compile(r"^[^@\s]+.*\s[^@\s]+@[^@\s]+\.[^@\s]+$")


class SecUserAgentMissing(RuntimeError):
    """No SEC User-Agent identifier was supplied; C-E0 never invents one."""


class SecFetchError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass
class FetchAccounting:
    http_requests: int = 0
    status_codes: Counter = field(default_factory=Counter)
    retries: int = 0
    bytes_downloaded: int = 0


def check_user_agent(user_agent: str | None) -> str:
    """SEC wants 'Name or org contact@domain'. Reject anything that is not that shape."""
    text = (user_agent or "").strip()
    if not text:
        raise SecUserAgentMissing(
            "pass --user-agent 'Organisation or name contact@domain' (SEC requires a real contact)")
    if not USER_AGENT_PATTERN.match(text):
        raise SecUserAgentMissing(f"user agent {text!r} must end in a contact email address")
    return text


def cik10(cik: int | str) -> str:
    return f"{int(str(cik).lstrip('CIK').lstrip('0') or 0):010d}"


class SecClient:
    """Serial, rate-limited, bounded-retry reader. One HTTP attempt per limiter slot."""

    def __init__(self, user_agent: str, *, requests_per_second: float = REQUESTS_PER_SECOND,
                 client: httpx.Client | None = None, sleeper: Callable[[float], None] = time.sleep) -> None:
        self.user_agent = check_user_agent(user_agent)
        self._min_spacing = 1.0 / requests_per_second
        self._last = 0.0
        self._sleeper = sleeper
        self._client = client or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True)
        self.accounting = FetchAccounting()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SecClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self._min_spacing:
            self._sleeper(self._min_spacing - elapsed)
        self._last = time.monotonic()

    def get(self, url: str, *, accept: str = "application/json") -> bytes:
        headers = {"User-Agent": self.user_agent, "Accept": accept,
                   "Accept-Encoding": "gzip, deflate", "Host": httpx.URL(url).host}
        for attempt in range(MAX_RETRIES + 1):
            self._wait()
            self.accounting.http_requests += 1
            try:
                response = self._client.get(url, headers=headers)
            except httpx.HTTPError as error:  # network failure: bounded retry, then typed error
                if attempt == MAX_RETRIES:
                    raise SecFetchError("TRANSPORT", f"{type(error).__name__}") from error
                self.accounting.retries += 1
                self._sleeper(2.0 * (attempt + 1))
                continue
            self.accounting.status_codes[response.status_code] += 1
            if response.status_code == 200:
                self.accounting.bytes_downloaded += len(response.content)
                return response.content
            if response.status_code == 404:
                raise SecFetchError("NOT_FOUND", url)
            if response.status_code == 403:
                raise SecFetchError("FORBIDDEN", "SEC refused the request; check the User-Agent")
            if response.status_code in RETRY_STATUSES and attempt < MAX_RETRIES:
                self.accounting.retries += 1
                self._sleeper(float(response.headers.get("Retry-After") or 0) or 2.0 * (attempt + 1))
                continue
            raise SecFetchError(f"HTTP_{response.status_code}", url)
        raise SecFetchError("EXHAUSTED", url)


def _write_gz(path: Path, body: bytes) -> str:
    """Provider bytes, gzip with mtime 0, written through a .partial name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with gzip.GzipFile(filename="", mode="wb", fileobj=open(partial, "wb"), mtime=0) as handle:
        handle.write(body)
    partial.replace(path)
    return hashlib.sha256(body).hexdigest()


def submissions_path(root: Path, cik: str, name: str) -> Path:
    """Stored gzipped, so the name carries `.gz`: `.../CIK<cik>/<page>.json.gz`."""
    return root / "submissions" / f"CIK{cik}" / f"{name.replace('/', '_')}.gz"


def ledger_path(path: Path) -> Path:
    return path.with_name(path.name + ".request.json")


def read_gz_json(path: Path) -> dict[str, Any]:
    return json.loads(gzip.decompress(path.read_bytes()))


def rows_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten one submissions payload (primary `filings.recent` or an older page) to rows."""
    block = payload.get("filings", {}).get("recent") if "filings" in payload else payload
    if not block:
        return []
    keys = [f for f in FIELDS if f in block]
    n = len(block[keys[0]]) if keys else 0
    return [{k: block[k][i] for k in keys} for i in range(n)]


def older_pages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return list(payload.get("filings", {}).get("files") or ())


@dataclass(frozen=True)
class CikFetchResult:
    cik: str
    pages: tuple[str, ...]
    rows: int
    earliest_filing_date: str | None
    latest_filing_date: str | None
    covered: bool
    status: str


def fetch_cik(client: SecClient | None, root: Path, cik: str, *, required_from: date,
              log: Callable[[str], None] = lambda _m: None) -> CikFetchResult:
    """Primary page plus as many older pages as `required_from` needs. Cached pages cost nothing."""
    primary_name = f"CIK{cik}.json"
    pages: list[str] = []
    rows: list[dict[str, Any]] = []
    payload = _page(client, root, cik, primary_name, log=log)
    if payload is None:
        return CikFetchResult(cik, (), 0, None, None, False, "NOT_FOUND")
    pages.append(primary_name)
    rows.extend(rows_of(payload))
    for entry in sorted(older_pages(payload), key=lambda e: str(e.get("filingTo")), reverse=True):
        if _earliest(rows) is not None and _earliest(rows) <= required_from.isoformat():
            break
        if str(entry.get("filingTo") or "") < required_from.isoformat():
            break
        if len(pages) > MAX_OLDER_PAGES:
            return CikFetchResult(cik, tuple(pages), len(rows), _earliest(rows), _latest(rows), False,
                                  "TRUNCATED_PAGES")
        older = _page(client, root, cik, str(entry["name"]), log=log)
        if older is None:
            return CikFetchResult(cik, tuple(pages), len(rows), _earliest(rows), _latest(rows), False,
                                  "PAGE_NOT_FOUND")
        pages.append(str(entry["name"]))
        rows.extend(rows_of(older))
    earliest = _earliest(rows)
    # A filer whose whole history starts later than `required_from` is covered by exhaustion:
    # coverage means "no unread page could hold a filing inside the window", not a date floor.
    remaining = [e for e in older_pages(payload)
                 if str(e.get("name")) not in pages and str(e.get("filingTo") or "") >= required_from.isoformat()]
    covered = bool(rows) and (earliest is not None and earliest <= required_from.isoformat() or not remaining)
    return CikFetchResult(cik, tuple(pages), len(rows), earliest, _latest(rows), covered,
                          "OK" if covered else "NOT_COVERED")


def _earliest(rows: Sequence[dict[str, Any]]) -> str | None:
    dates = [str(r.get("filingDate")) for r in rows if r.get("filingDate")]
    return min(dates) if dates else None


def _latest(rows: Sequence[dict[str, Any]]) -> str | None:
    dates = [str(r.get("filingDate")) for r in rows if r.get("filingDate")]
    return max(dates) if dates else None


def _page(client: SecClient | None, root: Path, cik: str, name: str,
          log: Callable[[str], None]) -> dict[str, Any] | None:
    path = submissions_path(root, cik, name)
    if path.exists() and ledger_path(path).exists():
        return read_gz_json(path)
    if client is None:
        return None
    try:
        body = client.get(SUBMISSIONS_URL.format(name=name))
    except SecFetchError as error:
        if error.code == "NOT_FOUND":
            log(f"cik={cik} page={name} NOT_FOUND")
            return None
        raise
    digest = _write_gz(path, body)
    ledger_path(path).write_text(json.dumps({
        "url": SUBMISSIONS_URL.format(name=name), "cik": cik, "page": name,
        "raw_sha256": digest, "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": len(body), "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": 200}, sort_keys=True) + "\n", encoding="utf-8")
    log(f"cik={cik} page={name} bytes={len(body)}")
    return json.loads(body)


def acceptance_header_path(root: Path, accession: str) -> Path:
    return root / "acceptance_headers" / f"{accession}.txt.gz"


def fetch_acceptance_header(client: SecClient, root: Path, cik: str, accession: str) -> str | None:
    """The filing's SGML `ACCEPTANCE-DATETIME` (ET, YYYYMMDDHHMMSS) for the timezone audit."""
    path = acceptance_header_path(root, accession)
    if not path.exists():
        url = HEADERS_URL.format(cik=int(cik), plain=accession.replace("-", ""), accession=accession)
        try:
            body = client.get(url, accept="text/html")
        except SecFetchError as error:
            if error.code in {"NOT_FOUND", "FORBIDDEN"}:
                return None
            raise
        digest = _write_gz(path, body)
        ledger_path(path).write_text(json.dumps({
            "url": url, "accession": accession, "raw_sha256": digest, "bytes": len(body),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": 200},
            sort_keys=True) + "\n", encoding="utf-8")
    text = gzip.decompress(path.read_bytes()).decode("utf-8", errors="replace")
    found = ACCEPTANCE_HEADER.search(text)
    return found.group(1) if found else None


def store_digest(root: Path) -> str:
    """sha256 over (relative path, file sha256) of every stored file, sorted by path."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.endswith(".partial")):
        digest.update(f"{path.relative_to(root)}\t{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode())
    return digest.hexdigest()
