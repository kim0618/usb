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
* a CIK is *fresh* when its newest *usable* cached copy was fetched at most ``max_age_days`` ago;
  an older copy is treated exactly like no copy (E1 then says FUNDAMENTALS_NOT_FETCHED), which is
  the fail-closed reading of the contract's "fetched within 7 days";
* a copy is *usable* only when every stored document has both its body and its request ledger - the
  stores' own cache-hit rule (``sec_store._page``, ``xbrl_store.fetch_cik``). A ledger whose body was
  dropped by ``compact`` (or lost any other way) is ``EVICTED``: planned for refetch like a stale copy,
  never scanned. A fresh ledger alone is not a reusable copy (2026-10-16: 691 such CIKs were read
  back cache-only and the SCAN died on SEC_SUBMISSIONS_NOT_FOUND);
* dated roots beyond ``keep_roots`` are dropped whole, so disk is bounded by about one copy per
  market-eligible CIK (measured median 125 KB per companyfacts document).

A SEC transport failure ends the slice (``SEC_DEGRADED``) and the rest waits for the next run; a
``NOT_FOUND`` CIK is recorded for the scan as ``SEC_NOT_READY``. No model call, no A or E file.
"""

from __future__ import annotations

import gzip
import json
import re
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
EVICTED = "EVICTED"                # ledger kept, a body gone: not readable cache-only, so refetched


@dataclass(frozen=True)
class CacheEntry:
    cik: str
    day: str                       # dated root (ET day of the fetch)
    fetched_at: datetime
    has_facts: bool
    usable: bool = True            # every document has body + ledger (see ``_usable``)


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


def _pair(path: Path) -> Path:
    """Body <-> ledger: ``X.json.gz`` <-> ``X.json.gz.request.json`` (``sec_store.ledger_path``)."""
    name = path.name
    return path.with_name(name.removesuffix(".request.json")) if name.endswith(".request.json") \
        else path.with_name(name + ".request.json")


def _usable(folder: Path, facts_body: Path) -> bool:
    """Whether ``fetch_sec(client=None)`` can serve this copy as fetched: every submissions page and the
    companyfacts document stored here has both its body and its ledger. A ledger without a body reads
    back as NOT_FOUND / MISSING (the stores' cache-only answer), a body without a ledger as never fetched;
    either would make the SCAN fail or read a truncated issuer. Only names are checked - a body whose
    bytes are damaged still fails closed in the reader, as before."""
    for path in folder.iterdir():
        if path.name.endswith(".json.gz") or path.name.endswith(".json.gz.request.json"):
            if not _pair(path).is_file():
                return False
    return facts_body.is_file() == _pair(facts_body).is_file()


def index() -> dict[str, CacheEntry]:
    """Newest cached copy per CIK across the dated roots (submissions primary page is the key); a usable
    copy is preferred over a newer one that is not."""
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
            facts_body = root / "companyfacts" / f"CIK{cik}.json.gz"
            entry = CacheEntry(cik, root.name, fetched, _pair(facts_body).is_file(), _usable(folder, facts_body))
            if entry.usable or not out.get(cik, entry).usable:
                out[cik] = entry
    return out


def state_of(entry: CacheEntry | None, today: date, *, max_age_days: int = FACTS_MAX_AGE_DAYS) -> str:
    if entry is None:
        return MISSING
    if not entry.usable:
        # ranked by ``plan`` with the stale copies (oldest fetch first), not as MISSING: compact evicts
        # every scanned non-candidate each run, and a MISSING tie breaks by CIK, so the same lowest CIKs
        # would take the whole fetch cap every day while the rest never got a turn
        return EVICTED
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
                # files only: parsing every companyfacts document here (and dropping it) was the
                # scan's 253 MB peak (2026-10-14); E1-E3 loads each issuer later, 20 at a time.
                IN.fetch_sec_files(cik, day, client=client)
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


_WS = re.compile(r"[ \t\n\r]*")


def _object(text: str, i: int, decoder: json.JSONDecoder, keep) -> tuple[dict[str, Any], int]:
    """Decode the JSON object opening at ``text[i]`` member by member. ``keep(key)`` returns False (drop),
    True (keep whole) or a callable (descend with it). A dropped member is decoded alone and released, so
    the peak is the document text plus the largest single member, never the whole DOM."""
    out: dict[str, Any] = {}
    i = _WS.match(text, i + 1).end()
    if text[i] == "}":
        return out, i + 1
    while True:
        key, i = decoder.raw_decode(text, i)
        i = _WS.match(text, i).end()
        if text[i] != ":":
            raise ValueError(f"companyfacts: ':' expected at {i}")
        i = _WS.match(text, i + 1).end()
        rule = keep(key)
        if callable(rule) and text[i] == "{":
            value, i = _object(text, i, decoder, rule)
        else:
            value, i = decoder.raw_decode(text, i)
        if rule:
            out[key] = value
        del value
        i = _WS.match(text, i).end()
        if text[i] == "}":
            return out, i + 1
        if text[i] != ",":
            raise ValueError(f"companyfacts: ',' expected at {i}")
        i = _WS.match(text, i + 1).end()


def read_facts_lean(root: Path, cik: str, tags: Iterable[str]) -> dict[str, Any] | None:
    """``xbrl_store.read_facts`` restricted to ``facts[<taxonomy>][<tag>]`` for ``tags`` (every other
    top-level member kept). ``extract_companyfacts`` reads nothing else, so its output is identical; the
    process no longer holds the largest issuer's whole companyfacts DOM (the SCAN peak, 2026-10-14)."""
    from app.backtest.strategy_eqm_v0 import xbrl_store
    path = xbrl_store.facts_path(root, cik)
    if not path.exists() or not xbrl_store.ledger_path(path).exists():
        return None
    raw = gzip.decompress(path.read_bytes())
    text = raw.decode("utf-8")
    del raw
    wanted = frozenset(tags)
    decoder = json.JSONDecoder()
    start = _WS.match(text, 0).end()
    concept = lambda tag: tag in wanted                                     # noqa: E731
    doc, _ = _object(text, start, decoder, lambda key: (lambda _tax: concept) if key == "facts" else True)
    return doc


def load(cik: str, entry: CacheEntry, *, lean: bool = False) -> IN.SecInputs:
    """Read a cached CIK back through the same path the executor uses (``client=None`` = cache only).

    ``lean`` (the SCAN): companyfacts is read with ``read_facts_lean`` over the canonical ``FIELD_SPECS``
    tags, the only ones ``extract_companyfacts`` reads there, and the raw document is not kept."""
    if not lean:
        return IN.fetch_sec(cik, entry.day, client=None)
    from app.backtest.strategy_h0.facts import FIELD_SPECS
    tags = {tag for spec in FIELD_SPECS.values() for tag in spec.tags}
    sec = IN.fetch_sec(cik, entry.day, client=None,
                       facts_reader=lambda root, c: read_facts_lean(root, c, tags))
    sec.facts_document = None
    return sec


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
    ledgers stay as the fetch record; ``index`` reads such a copy as ``EVICTED`` (not FRESH), so ``plan``
    schedules it and ``sec_store``/``xbrl_store`` refetch exactly the missing bodies. Measured reason:
    one copy per market-eligible CIK is about 3,100 x 156 KB = 480 MB, and trader-j has about 1.9 GB free.
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
    states = {FRESH: 0, STALE: 0, EVICTED: 0}
    for entry in entries.values():
        states[state_of(entry, today)] += 1
    roots = dated_roots()
    return {"cached_ciks": len(entries), **states, "dated_roots": [r.name for r in roots],
            "max_age_days": FACTS_MAX_AGE_DAYS, "keep_roots": KEEP_DATED_ROOTS,
            "bytes": sum(p.stat().st_size for r in roots for p in r.rglob("*") if p.is_file())}
