"""On-demand SEC fundamentals for new-candidate intake: a rolling per-CIK cache, never a full copy.

The research run of 2026-09-28 fetched companyfacts for every CIK in the universe (4,906 documents,
692 MB on the local tree). The production host has 1.9 GB free and no such store, and the intake
contract only needs each issuer's facts to be at most ``FACTS_MAX_AGE_DAYS`` old when it is scanned.
So this module keeps a *rolling* cache under the executor's own dated roots
(``<H store>/auto/sec/<ET day>/``, the directory ``inputs.fetch_sec`` already writes a candidate's
submissions and companyfacts into):

* each run refreshes at most ``cap`` CIKs - the ones with no fresh copy, oldest first, then by CIK -
  through ``inputs.fetch_sec`` (``sec_store`` + ``xbrl_store``, the same User-Agent, the same
  5 req/s limiter, raw bytes plus ledger, never rewritten);
* a CIK is *fresh* when its newest cached ``companyfacts`` ledger is at most ``max_age_days`` old;
  an older copy is treated exactly like no copy (E1 then says FUNDAMENTALS_NOT_FETCHED), which is
  the fail-closed reading of the contract's "fetched within 7 days";
* dated roots beyond ``keep_roots`` are dropped whole, so disk is bounded by about one copy per
  market-eligible CIK (measured median 125 KB per companyfacts document).

A SEC transport failure ends the slice (``SEC_DEGRADED``) and the rest waits for the next run; a
``NOT_FOUND`` CIK is recorded for the scan as ``SEC_NOT_READY``. No model call, no A or E file.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from app.strategies.h_auto import inputs as IN
from app.strategies.h_auto import store as AS

FACTS_MAX_AGE_DAYS = 7
KEEP_DATED_ROOTS = 6
DEFAULT_FETCH_CAP = 700
FRESH, STALE, MISSING, NOT_FOUND = "FRESH", "STALE", "MISSING", "NOT_FOUND"


@dataclass(frozen=True)
class CacheEntry:
    cik: str
    day: str                       # dated root (ET day of the fetch)
    fetched_at: datetime
    has_facts: bool


def sec_dir() -> Path:
    return AS.root() / "sec"


def dated_roots() -> list[Path]:
    base = sec_dir()
    if not base.is_dir():
        return []
    out = []
    for path in base.iterdir():
        try:
            date.fromisoformat(path.name)
        except ValueError:
            continue
        if path.is_dir():
            out.append(path)
    return sorted(out)


def _ledger_time(path: Path) -> datetime | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return datetime.fromisoformat(str(raw["fetched_at"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError):
        return None


def index() -> dict[str, CacheEntry]:
    """Newest cached copy per CIK across the dated roots (submissions primary page is the key)."""
    out: dict[str, CacheEntry] = {}
    for root in dated_roots():                          # oldest first: a newer root overwrites
        subs = root / "submissions"
        if not subs.is_dir():
            continue
        for folder in subs.iterdir():
            cik = folder.name.removeprefix("CIK")
            ledger = folder / f"CIK{cik}.json.gz.request.json"
            if not ledger.is_file():
                continue
            fetched = _ledger_time(ledger)
            if fetched is None:
                continue
            facts_ledger = root / "companyfacts" / f"CIK{cik}.json.gz.request.json"
            out[cik] = CacheEntry(cik, root.name, fetched, facts_ledger.is_file())
    return out


def state_of(entry: CacheEntry | None, today: date, *, max_age_days: int = FACTS_MAX_AGE_DAYS) -> str:
    if entry is None:
        return MISSING
    return FRESH if (today - entry.fetched_at.date()).days <= max_age_days else STALE


def plan(needed: Iterable[str], today: date, *, cap: int, entries: Mapping[str, CacheEntry] | None = None,
         max_age_days: int = FACTS_MAX_AGE_DAYS) -> list[str]:
    """Which CIKs this run fetches: missing first, then stale oldest-first, then by CIK; at most ``cap``."""
    entries = index() if entries is None else entries
    ranked = []
    for cik in sorted(set(needed)):
        entry = entries.get(cik)
        state = state_of(entry, today, max_age_days=max_age_days)
        if state == FRESH:
            continue
        age = 10 ** 6 if entry is None else (today - entry.fetched_at.date()).days
        ranked.append((0 if state == MISSING else 1, -age, cik))
    return [cik for _, _, cik in sorted(ranked)[:max(0, cap)]]


def refresh(ciks: Sequence[str], *, day: str, client_factory: Callable[[], Any],
            log: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    """Fetch submissions (back to 2022) and companyfacts for ``ciks`` into ``auto/sec/<day>``."""
    report: dict[str, Any] = {"day": day, "requested": len(ciks), "fetched": [], "not_found": [],
                              "degraded": None, "http_requests": None}
    if not ciks:
        return report
    client = client_factory()
    try:
        for n, cik in enumerate(ciks, 1):
            try:
                IN.fetch_sec(cik, day, client=client)
                report["fetched"].append(cik)
            except IN.InputsNotReady as exc:
                report["not_found"].append({"cik": cik, "why": exc.code})
            except IN.SecUnavailable as exc:
                report["degraded"] = {"after": n - 1, "cik": cik, "why": str(exc)}
                break
            if n % 100 == 0:
                log(f"facts_cache {n}/{len(ciks)} fetched={len(report['fetched'])}")
        accounting = getattr(client, "accounting", None)
        if accounting is not None:
            report["http_requests"] = getattr(accounting, "requests", None) or getattr(accounting, "http_requests", None)
            report["bytes_downloaded"] = getattr(accounting, "bytes_downloaded", None)
    finally:
        close = getattr(client, "close", None)
        if close:
            close()
    return report


def load(cik: str, entry: CacheEntry) -> IN.SecInputs:
    """Read a cached CIK back through the same path the executor uses (``client=None`` = cache only)."""
    return IN.fetch_sec(cik, entry.day, client=None)


def prune(keep: int = KEEP_DATED_ROOTS) -> list[str]:
    """Drop the oldest dated roots beyond ``keep``. Only ever H's own ``auto/sec`` cache."""
    dropped = []
    for old in dated_roots()[:-keep] if keep > 0 else []:
        shutil.rmtree(old, ignore_errors=True)
        dropped.append(old.name)
    return dropped


def compact(keep_ciks: Iterable[str]) -> dict[str, int]:
    """Drop the document bodies of every CIK not in ``keep_ciks``, keeping each request ledger.

    The scan reads a body once (E1->E3) and records the result in ``intake_index.json``; only a CIK
    that became a candidate needs its body again, and the executor fetches its own copy anyway. The
    ledgers stay, so freshness (``index``) is unchanged, and a body that is gone is simply refetched
    by ``sec_store``/``xbrl_store`` the next time it is planned. Measured reason: one copy per
    market-eligible CIK is about 3,100 x 156 KB = 480 MB, and trader-j has about 1.9 GB free.
    """
    keep = set(keep_ciks)
    dropped = kept = 0
    for root in dated_roots():
        for body in list(root.rglob("CIK*.json.gz")):
            cik = body.name[3:13]
            if cik in keep:
                kept += 1
                continue
            body.unlink(missing_ok=True)
            dropped += 1
    return {"bodies_dropped": dropped, "bodies_kept": kept}


def summary(today: date, entries: Mapping[str, CacheEntry] | None = None) -> dict[str, Any]:
    entries = index() if entries is None else entries
    states = {FRESH: 0, STALE: 0}
    for entry in entries.values():
        states[state_of(entry, today)] += 1
    roots = dated_roots()
    return {"cached_ciks": len(entries), **states, "dated_roots": [r.name for r in roots],
            "max_age_days": FACTS_MAX_AGE_DAYS, "keep_roots": KEEP_DATED_ROOTS,
            "bytes": sum(p.stat().st_size for r in roots for p in r.rglob("*") if p.is_file())}
