"""H's own SEC submissions cache for the material-event refresh, and the queue's guarded write.

The refresh queue says which cohort issuers have filed something the frozen thesis has not seen.
It is only as good as the submissions it reads, and on 2026-10-10 it showed what happens when those
are absent: the production host had no cache, the scan rewrote all eight issuers as
``NO_SUBMISSIONS_CACHE``, and AEYE's real ``REFRESH_DUE`` (its 2026-09-18 8-K) was destroyed by a run
that exited 0. Two failure domains were sharing one write, so this module separates them.

**Prices and outcomes never wait on SEC.** The daily update collects prices and matures outcomes
first; everything here runs afterwards, and nothing here can raise into that step.

**The issuer's research state and the refresh infrastructure's state are different facts.** AEYE can
be ``REFRESH_DUE`` (research) while the SEC cache is ``DATA_NOT_READY`` (infrastructure). The first
lives in ``refresh_queue.json`` and changes only through a successful scan; the second lives in
``refresh_status.json`` and is rewritten by every run.

**The queue is replaced only by a scan that read a complete, fresh cache, and only atomically.** No
cache, a stale cache, a partial fetch or a scan that would make a known filing disappear all leave
the existing queue byte-identical.

Fetching reuses ``app.backtest.strategy_c_e0.sec_store`` unchanged - its User-Agent check, serial
5 req/s limiter, bounded retry and raw-bytes-plus-ledger immutability - with the User-Agent the D1.1
acquisition already declares. Because that store never overwrites a file, freshness is obtained by
writing each day's fetch under its own dated root, never by rewriting an older one. Only the
primary submissions page is requested (one call per cohort CIK): the decision session lies inside
SEC's ``recent`` block, so no older page is needed to see a filing made after it. No filing body,
no companyfacts, no other issuer.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from app.backtest.strategy_c_e0 import sec_store
from app.strategies.h_forward import store as ST

SEC_DIR = "sec_submissions"
STATUS = "refresh_status.json"
STATUS_SCHEMA = "h_v2_d7_refresh_status_v1"

#: A cache older than this is not evidence about "now". Four calendar days lets a Friday fetch serve
#: a hand-run scan through a holiday Monday; the timer fetches every weekday, so it never relies on it.
MAX_CACHE_AGE_DAYS = 4
#: Dated roots kept after a complete fetch. Each holds eight primary pages (~180 KB in all).
KEEP_DATED_ROOTS = 10

# Infrastructure states. They describe the cache and the run, never an issuer's research state.
DATA_READY = "READY"
DATA_NOT_READY = "REFRESH_DATA_NOT_READY"
RUN_OK = "OK"
RUN_DEGRADED = "REFRESH_DEGRADED"

_MARKET_TZ = ZoneInfo("America/New_York")


def sec_root() -> Path:
    """Resolved from the store root (module-relative), so it does not depend on the working directory."""
    return ST.root() / SEC_DIR


def fetch_day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(_MARKET_TZ).date().isoformat()


def dated_roots() -> list[Path]:
    base = sec_root()
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and len(p.name) == 10)


def primary_path(root: Path, cik: str) -> Path:
    cik = sec_store.cik10(cik)
    return sec_store.submissions_path(root, cik, f"CIK{cik}.json")


def cohort_ciks(launch_rows: Sequence[Mapping[str, Any]]) -> dict[str, str | None]:
    """ticker -> CIK, exactly as the launch snapshot recorded it."""
    return {row["ticker"]: (sec_store.cik10(row["cik"]) if row.get("cik") else None)
            for row in launch_rows}


# -------------------------------------------------------------------------------------------------
# Fetch
# -------------------------------------------------------------------------------------------------

def _default_client() -> sec_store.SecClient:
    from app.dev.acquire_strategy_h_v2_fundamentals import USER_AGENT
    return sec_store.SecClient(USER_AGENT)


def fetch(launch_rows: Sequence[Mapping[str, Any]], *, required_from: str,
          client_factory: Callable[[], sec_store.SecClient] | None = None,
          now: datetime | None = None) -> dict[str, Any]:
    """Fetch today's primary submissions page for every cohort CIK into today's dated root.

    Never raises: a refused User-Agent, a transport failure or a 403 becomes a per-issuer error and
    the report says the fetch is incomplete. A page already fetched today is reused, not re-requested.
    """
    day = fetch_day(now)
    root = sec_root() / day
    ciks = cohort_ciks(launch_rows)
    issuers: dict[str, dict[str, Any]] = {}
    client = None
    try:
        client = (client_factory or _default_client)()
    except Exception as exc:                                    # e.g. SecUserAgentMissing
        return {"day": day, "root": str(root), "complete": False, "http_requests": 0,
                "issuers": {t: {"cik": c, "status": "NOT_ATTEMPTED"} for t, c in ciks.items()},
                "error": f"{type(exc).__name__}: {exc}"}
    try:
        for ticker, cik in sorted(ciks.items()):
            if cik is None:
                issuers[ticker] = {"cik": None, "status": "NO_CIK_IN_SNAPSHOT"}
                continue
            try:
                result = sec_store.fetch_cik(client, root, cik,
                                             required_from=date.fromisoformat(required_from))
                issuers[ticker] = {"cik": cik, "status": result.status, "rows": result.rows,
                                   "latest_filing_date": result.latest_filing_date,
                                   "pages": list(result.pages)}
            except Exception as exc:                            # SecFetchError, OSError, bad JSON
                issuers[ticker] = {"cik": cik, "status": "FAILED",
                                   "error": getattr(exc, "code", type(exc).__name__)}
    finally:
        accounting = client.accounting
        client.close()
    complete = bool(issuers) and all(i["status"] == "OK" for i in issuers.values())
    if complete:
        prune()
    return {"day": day, "root": str(root), "complete": complete,
            "http_requests": accounting.http_requests,
            "status_codes": {str(k): v for k, v in accounting.status_codes.items()},
            "issuers": issuers}


def prune(keep: int = KEEP_DATED_ROOTS) -> list[str]:
    """Drop the oldest dated roots beyond ``keep``. Only ever this module's own SEC cache."""
    removed = []
    for old in dated_roots()[:-keep] if keep > 0 else []:
        shutil.rmtree(old)
        removed.append(old.name)
    return removed


# -------------------------------------------------------------------------------------------------
# Readiness: may a scan be trusted to replace the queue?
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Cache:
    rows: list[dict[str, Any]]
    path: str
    fetched_on: str          # UTC date of the fetch, from the request ledger, never the file mtime


def read_cache(root: Path, cik: str) -> Cache | None:
    path = primary_path(root, cik)
    ledger = sec_store.ledger_path(path)
    if not (path.is_file() and ledger.is_file()):
        return None
    try:
        fetched_at = json.loads(ledger.read_text(encoding="utf-8"))["fetched_at"]
        rows = sec_store.rows_of(sec_store.read_gz_json(path))
    except (OSError, ValueError, KeyError):
        return None
    if not rows:
        return None
    return Cache(rows=rows, path=str(path), fetched_on=str(fetched_at)[:10])


def readiness(launch_rows: Sequence[Mapping[str, Any]], *, today: str | None = None) -> dict[str, Any]:
    """The newest dated root that holds every cohort CIK, if it is fresh enough to scan.

    One root for all issuers, so the queue states one as-of date rather than a mixture.
    """
    today = today or datetime.now(timezone.utc).date().isoformat()
    ciks = cohort_ciks(launch_rows)
    reasons: list[str] = []
    if not ciks:
        reasons.append("NO_LAUNCH_COHORT")
    missing_cik = sorted(t for t, c in ciks.items() if c is None)
    if missing_cik:
        reasons.append(f"NO_CIK_IN_SNAPSHOT:{','.join(missing_cik)}")
    chosen: Path | None = None
    caches: dict[str, Cache] = {}
    if not reasons:
        for root in reversed(dated_roots()):
            found = {t: read_cache(root, c) for t, c in ciks.items()}
            if all(found.values()):
                chosen, caches = root, found          # type: ignore[assignment]
                break
        if chosen is None:
            reasons.append("NO_COMPLETE_SUBMISSIONS_CACHE")
    as_of = min((c.fetched_on for c in caches.values()), default=None)
    if chosen is not None and as_of is not None:
        age = (date.fromisoformat(today) - date.fromisoformat(as_of)).days
        if age > MAX_CACHE_AGE_DAYS:
            reasons.append(f"CACHE_STALE:{as_of}:{age}d>{MAX_CACHE_AGE_DAYS}d")
    return {"state": DATA_READY if not reasons else DATA_NOT_READY, "reasons": reasons,
            "root": None if chosen is None else str(chosen), "as_of": as_of,
            "caches": caches if not reasons else {}}


# -------------------------------------------------------------------------------------------------
# The queue's guarded, atomic replacement
# -------------------------------------------------------------------------------------------------

def file_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def validate(candidate: Mapping[str, Any], previous: Mapping[str, Any] | None,
             launch_rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Why ``candidate`` may not replace ``previous``; empty when it may.

    A filing that is in the existing queue cannot vanish from SEC's record, so a candidate that
    loses one is reading something wrong - it is refused rather than allowed to clear a REFRESH_DUE.
    A REFRESH_DUE is cleared by a D3->D6 re-evaluation, never by a scan.
    """
    problems: list[str] = []
    issuers = {r.get("ticker"): r for r in candidate.get("issuers") or ()}
    expected = {r["ticker"] for r in launch_rows}
    if set(issuers) != expected:
        problems.append(f"COHORT_MISMATCH:{sorted(set(issuers) ^ expected)}")
    blind = sorted(t for t, r in issuers.items() if r.get("state") == "NO_SUBMISSIONS_CACHE")
    if blind:
        problems.append(f"SCAN_WITHOUT_CACHE:{blind}")
    for row in (previous or {}).get("issuers") or ():
        before = {f.get("accession") for f in row.get("new_material_filings") or ()}
        after = {f.get("accession") for f in (issuers.get(row.get("ticker")) or {})
                 .get("new_material_filings") or ()}
        if before - after:
            problems.append(f"KNOWN_FILING_LOST:{row.get('ticker')}:{sorted(before - after)}")
        if row.get("state") == "REFRESH_DUE" and (issuers.get(row.get("ticker")) or {}).get("state") != "REFRESH_DUE":
            problems.append(f"REFRESH_DUE_CLEARED_BY_SCAN:{row.get('ticker')}")
    return problems


def atomic_write_json(target: Path, body: Mapping[str, Any]) -> str:
    """temp file in the same directory -> fsync -> re-read check -> os.replace. Returns the sha256."""
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(body, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if json.loads(temp.read_text(encoding="utf-8")) != json.loads(text):
            raise OSError(f"{temp} did not read back as written")
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_status(body: Mapping[str, Any]) -> None:
    atomic_write_json(ST.path(STATUS), {"schema_version": STATUS_SCHEMA, **body})


def read_status() -> dict[str, Any] | None:
    return read_json(ST.path(STATUS))
