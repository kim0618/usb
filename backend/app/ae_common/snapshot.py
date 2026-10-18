"""Sealed, digest-checked premarket snapshots: the only thing the producer hands a consumer.

A snapshot is written once and never again:

* the body is canonical JSON (sorted keys, no whitespace), gzip'd with a zero mtime, so the same
  content is the same bytes; its sha256 over the *uncompressed* canonical bytes is the digest;
* the file is created under a temporary name and hard-linked to its final name, which fails if the
  final name exists, so a second seal of the same snapshot is refused instead of overwriting it;
  both files are then made read-only (0444);
* the manifest is written the same way and names the digest; :func:`load` refuses a body whose
  digest is not the manifest's.

Bars are stored as ``[minute, open, high, low, close, volume]`` with the cache's own floats. JSON
round-trips a Python float exactly, so a cache rebuilt from the file holds the same values the
producer's cache held. ``snapshot_0915`` keeps only minutes before A's cut (09:15) and
``snapshot_0924`` only minutes up to E's cutoff (09:24): a consumer cannot read past its cut
because the minutes are not in its file.

This module imports only code that is byte-identical in the production A/E operations tree.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.strategy_a_mover_live import acquisition as AQ
from app.strategy_e_max_rt import finalizer as FZ

FORMAT_0915 = "ae-common-snapshot-0915-v1"
FORMAT_0924 = "ae-common-snapshot-0924-v1"
FORMAT_A_PARITY = "ae-common-a-parity-0915-v1"
SNAPSHOT_0915, SNAPSHOT_0924, A_PARITY = "snapshot_0915", "snapshot_0924", "a_parity_0915"
A_CUT_MINUTE = 9 * 60 + 15
E_CUTOFF_MINUTE = 9 * 60 + 24

USABLE, STALE, FAILED, INTERRUPTED, NOT_REACHED, UNMAPPED = (
    "USABLE", "STALE", "FAILED", "INTERRUPTED", "NOT_REACHED", "UNMAPPED")
MARKET_DATA_UNAVAILABLE, DOCUMENTED_REFUSAL = "MARKET_DATA_UNAVAILABLE", "DOCUMENTED_REFUSAL"
#: Documented permanent exceptions: the only statuses outside A's expected-processable universe.
#:
#: * ``UNMAPPED`` - not in Kiwoom's listing (``usa10099``), known before 04:00;
#: * ``MARKET_DATA_UNAVAILABLE`` - E-RT2.1's contract state: Kiwoom *rejected* the chart query
#:   (non-zero return_code, code MARKET_DATA_UNAVAILABLE / INVALID_SYMBOL) and no page for the symbol
#:   succeeded all morning. A transport failure, a 5xx or a malformed answer is never this;
#: * ``DOCUMENTED_REFUSAL`` - a permanent source refusal already on record in E's RVOL store
#:   (E-RUNTIME-R1 ``DOCUMENTED_REFUSALS``, e.g. PS since 2026-09-23) that was not served today.
PERMANENT_EXCEPTIONS = (UNMAPPED, MARKET_DATA_UNAVAILABLE, DOCUMENTED_REFUSAL)
#: The outcome error string of a source refusal (``sweep.error_label``).
SOURCE_REFUSAL_PREFIX = "SOURCE_REFUSAL:"


class SnapshotExists(RuntimeError):
    """A sealed snapshot of that name exists for the session; it is never rewritten."""


class SnapshotInvalid(ValueError):
    """The file is not the sealed snapshot its manifest names."""


def canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(payload: Any) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _dt(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _write_once(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    with open(temporary, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)                         # fails if the sealed name exists
    except FileExistsError as error:
        raise SnapshotExists(str(path)) from error
    finally:
        temporary.unlink(missing_ok=True)
    os.chmod(path, 0o444)


def paths(directory: Path, name: str) -> tuple[Path, Path]:
    return Path(directory) / f"{name}.json.gz", Path(directory) / f"{name}.manifest.json"


def seal(directory: Path, name: str, body: Mapping[str, Any], *, sealed_at: datetime,
         summary: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Write ``body`` once under ``name`` and return its manifest."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    body_path, manifest_path = paths(directory, name)
    if body_path.exists() or manifest_path.exists():
        raise SnapshotExists(str(body_path))
    raw = canonical(body)
    manifest = {"name": name, "format": body.get("format"), "session": body.get("session"),
                "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                "sealed_at": sealed_at.isoformat(), "file": body_path.name, "summary": dict(summary or {})}
    _write_once(body_path, gzip.compress(raw, compresslevel=6, mtime=0))
    _write_once(manifest_path, (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode())
    return manifest


def exists(directory: Path, name: str) -> bool:
    return paths(directory, name)[1].is_file()


@dataclass(frozen=True)
class Sealed:
    manifest: Mapping[str, Any]
    body: Mapping[str, Any]


def load(directory: Path, name: str, *, fmt: str | None = None, session: date | None = None) -> Sealed:
    body_path, manifest_path = paths(directory, name)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw = gzip.decompress(body_path.read_bytes())
    except (OSError, ValueError) as error:
        raise SnapshotInvalid(f"{name}: {type(error).__name__}: {error}") from error
    if hashlib.sha256(raw).hexdigest() != manifest.get("sha256"):
        raise SnapshotInvalid(f"{name}: digest does not match its manifest")
    body = json.loads(raw)
    if fmt is not None and body.get("format") != fmt:
        raise SnapshotInvalid(f"{name}: format {body.get('format')!r}, expected {fmt!r}")
    if session is not None and body.get("session") != session.isoformat():
        raise SnapshotInvalid(f"{name}: session {body.get('session')!r}, expected {session.isoformat()}")
    return Sealed(manifest, body)


# -- bars ------------------------------------------------------------------------------------------

def bars_before(cache: FZ.SymbolCache, end_minute: int) -> list[list[float]]:
    """``[[minute, o, h, l, c, v], ...]`` for minutes in ``[04:00, end_minute)``, oldest first."""
    return [[m, *values] for m, values in sorted(cache.bars.items()) if 4 * 60 <= m < end_minute]


def _bars_map(rows: Sequence[Sequence[float]]) -> dict[int, list[float]]:
    return {int(row[0]): [float(v) for v in row[1:6]] for row in rows}


# -- snapshot_0915 (A) -----------------------------------------------------------------------------

def a_status(symbol: str, outcomes: Mapping[str, AQ.APassOutcome], unmapped: set[str], *,
             documented: frozenset[str] = frozenset(), cache: FZ.SymbolCache | None = None) -> str:
    """The symbol's A status at the seal. Only ``PERMANENT_EXCEPTIONS`` leave the expected set."""
    if symbol in unmapped:
        return UNMAPPED
    outcome = outcomes.get(symbol)
    if outcome is None:
        status = NOT_REACHED
    elif outcome.error == "KIWOOM_DEADLINE_REACHED":
        status = INTERRUPTED
    elif outcome.error is not None:
        never_served = cache is not None and cache.first_fetch is None
        status = (MARKET_DATA_UNAVAILABLE if outcome.error.startswith(SOURCE_REFUSAL_PREFIX) and never_served
                  else FAILED)
    else:
        status = USABLE if outcome.usable else STALE
    if status != USABLE and symbol in documented:
        return DOCUMENTED_REFUSAL
    return status


@dataclass(frozen=True)
class FrozenA:
    """What the producer's cache said for A the instant the sweep ended (09:25:00 at the latest),
    copied before E's finalization touches the cache again. Full bar maps, so the legacy
    filter-at-read path can be replayed against the pre-filtered file."""

    session: date
    observed_at: datetime
    caches: Mapping[str, FZ.SymbolCache]
    a_pass: AQ.APassReport


def freeze_a(caches: Mapping[str, FZ.SymbolCache], a_pass: AQ.APassReport, symbols: Iterable[str], *,
             session: date, observed_at: datetime) -> FrozenA:
    copies = {}
    for symbol in symbols:
        cache = caches.get(symbol)
        if cache is None:
            continue
        copy = FZ.SymbolCache(cache.symbol, cache.exchange, cache.code)
        copy.bars = {m: list(v) for m, v in cache.bars.items()}
        copy.complete_through, copy.first_fetch, copy.calls = cache.complete_through, cache.first_fetch, cache.calls
        copy.error = cache.error
        copies[symbol] = copy
    passed = AQ.APassReport(session=a_pass.session, t0=a_pass.t0, t1=a_pass.t1,
                            outcomes={k: AQ.APassOutcome(**vars(v)) for k, v in a_pass.outcomes.items()},
                            lane_calls=dict(a_pass.lane_calls), order=tuple(a_pass.order),
                            tick_shard=tuple(a_pass.tick_shard), cutoff_minute=a_pass.cutoff_minute,
                            deadline_hit=a_pass.deadline_hit)
    return FrozenA(session, observed_at, copies, passed)


def body_0915(frozen: FrozenA, *, a_symbols: Sequence[str], union_symbols: Sequence[str],
              union_checksum: str, unmapped: Sequence[str], exchanges: Mapping[str, str],
              sweep: Mapping[str, Any], documented_refusals: Sequence[str] = ()) -> dict[str, Any]:
    """The A snapshot. Every A-union symbol has an entry, so its status set is the coverage proof."""
    gone = set(unmapped)
    documented = frozenset(documented_refusals)
    outcomes = frozen.a_pass.outcomes
    symbols: dict[str, Any] = {}
    for symbol in sorted(set(a_symbols) | set(frozen.caches)):
        cache = frozen.caches.get(symbol)
        status = a_status(symbol, outcomes, gone, documented=documented, cache=cache)
        outcome = outcomes.get(symbol)
        entry: dict[str, Any] = {"status": status, "a_member": symbol in set(a_symbols)}
        if cache is not None:
            entry |= {"exchange": cache.exchange, "code": cache.code,
                      "complete_through": cache.complete_through,
                      "bars": bars_before(cache, A_CUT_MINUTE)}
        if outcome is not None:
            entry |= {"lane": outcome.lane, "pages": outcome.pages,
                      "a_window_complete": outcome.a_window_complete,
                      "fetched_at": _iso(outcome.finalized_at),
                      "last_complete_minute": outcome.last_complete_minute, "error": outcome.error}
        symbols[symbol] = entry
    passed = frozen.a_pass
    return {"format": FORMAT_0915, "session": frozen.session.isoformat(), "cut_minute": A_CUT_MINUTE,
            "bars_rule": "minutes in [04:00, 09:15) only", "observed_at": frozen.observed_at.isoformat(),
            "a_pass": {"t0": passed.t0.isoformat(), "t1": _iso(passed.t1), "deadline_hit": passed.deadline_hit,
                       "lane_calls": dict(passed.lane_calls), "order": list(passed.order),
                       "tick_shard": list(passed.tick_shard), "cutoff_minute": passed.cutoff_minute},
            "sweep": dict(sweep), "a_symbols": sorted(a_symbols), "union_symbols": sorted(union_symbols),
            "union_checksum": union_checksum, "unmapped": sorted(gone),
            "documented_refusals": sorted(documented), "permanent_exceptions": list(PERMANENT_EXCEPTIONS),
            "exchanges": dict(sorted(exchanges.items())), "symbols": symbols}


def summary_0915(body: Mapping[str, Any]) -> dict[str, Any]:
    """Coverage over A's expected-processable universe: every A-union symbol except the documented
    permanent exceptions. Complete means every expected symbol is USABLE - one unexpected STALE,
    FAILED, INTERRUPTED or NOT_REACHED symbol and A does not scan from this file."""
    counts: dict[str, int] = {}
    unexpected: dict[str, str] = {}
    for symbol, entry in body["symbols"].items():
        if not entry["a_member"]:
            continue
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        if entry["status"] not in PERMANENT_EXCEPTIONS and entry["status"] != USABLE:
            unexpected[symbol] = entry["status"]
    exceptions = sum(counts.get(s, 0) for s in PERMANENT_EXCEPTIONS)
    return {"a_symbols": len(body["a_symbols"]), "a_status_counts": counts,
            "permanent_exceptions": exceptions, "expected_processable": len(body["a_symbols"]) - exceptions,
            "expected_usable": counts.get(USABLE, 0), "unexpected": len(unexpected),
            "unexpected_examples": dict(sorted(unexpected.items())[:200]),
            "complete_for_a": not unexpected,
            "bars": sum(len(e.get("bars", ())) for e in body["symbols"].values())}


def a_caches_from(body: Mapping[str, Any]) -> tuple[dict[str, FZ.SymbolCache], AQ.APassReport, datetime]:
    """The caches, the A pass report and the observation instant, rebuilt from the file."""
    session = date.fromisoformat(body["session"])
    caches: dict[str, FZ.SymbolCache] = {}
    outcomes: dict[str, AQ.APassOutcome] = {}
    for symbol, entry in body["symbols"].items():
        if "bars" in entry:
            cache = FZ.SymbolCache(symbol, entry["exchange"], entry["code"])
            cache.bars = _bars_map(entry["bars"])
            cache.complete_through = entry["complete_through"]
            caches[symbol] = cache
        if "lane" in entry:
            outcomes[symbol] = AQ.APassOutcome(symbol, entry["lane"], entry["pages"], entry["a_window_complete"],
                                               _dt(entry["fetched_at"]), entry["last_complete_minute"],
                                               entry["error"])
    passed = body["a_pass"]
    report = AQ.APassReport(session=session, t0=_dt(passed["t0"]), t1=_dt(passed["t1"]), outcomes=outcomes,
                            lane_calls=dict(passed["lane_calls"]), order=tuple(passed["order"]),
                            tick_shard=tuple(passed["tick_shard"]), cutoff_minute=passed["cutoff_minute"],
                            deadline_hit=passed["deadline_hit"])
    return caches, report, _dt(body["observed_at"])


def a_snapshot_rows(caches: Mapping[str, FZ.SymbolCache], report: AQ.APassReport, *, session: date,
                    observed_at: datetime) -> dict[str, dict[str, Any]]:
    """A's snapshot rows through A's own ``snapshots_from`` - the comparison unit for parity."""
    return {symbol: snap.row() for symbol, snap in
            AQ.snapshots_from(caches, report, session=session, observed_at=observed_at).items()}


def row_digests(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """Per-symbol digest of canonical JSON; NaN compares equal here, unlike in a dict compare."""
    return {symbol: digest(row) for symbol, row in sorted(rows.items())}


def compare_digests(expected: Mapping[str, str], actual: Mapping[str, str]) -> dict[str, Any]:
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    differ = sorted(s for s in set(expected) & set(actual) if expected[s] != actual[s])
    return {"exact": not (missing or extra or differ), "compared": len(set(expected) & set(actual)),
            "missing": len(missing), "extra": len(extra), "different": len(differ),
            "examples": {"missing": missing[:20], "extra": extra[:20], "different": differ[:20]},
            "set_digest_expected": digest(dict(sorted(expected.items()))),
            "set_digest_actual": digest(dict(sorted(actual.items())))}


# -- snapshot_0924 (E) -----------------------------------------------------------------------------

def body_0924(session: date, caches: Mapping[str, FZ.SymbolCache], spy: FZ.SymbolCache, *,
              universe: Sequence[str], unmapped: Sequence[str], universe_digest: str,
              finalization: Mapping[str, Any]) -> dict[str, Any]:
    """E's snapshot: every verdict field ``availability.outcome_from_cache`` reads, and bars <= 09:24."""
    def entry(cache: FZ.SymbolCache) -> dict[str, Any]:
        return {"exchange": cache.exchange, "code": cache.code, "complete_through": cache.complete_through,
                "finalized_at": _iso(cache.finalized_at), "data_source": cache.data_source,
                "contiguous": cache.contiguous, "final_pages": cache.final_pages, "error": cache.error,
                "bars": bars_before(cache, E_CUTOFF_MINUTE + 1)}
    return {"format": FORMAT_0924, "session": session.isoformat(), "cutoff_last_minute": E_CUTOFF_MINUTE,
            "bars_rule": "minutes in [04:00, 09:24] only", "universe": list(universe),
            "universe_digest": universe_digest, "unmapped": sorted(unmapped),
            "finalization": dict(finalization), "spy": entry(spy),
            "symbols": {s: entry(caches[s]) for s in universe if s in caches}}


def e_caches_from(body: Mapping[str, Any]) -> tuple[dict[str, FZ.SymbolCache], FZ.SymbolCache]:
    def cache(symbol: str, entry: Mapping[str, Any]) -> FZ.SymbolCache:
        item = FZ.SymbolCache(symbol, entry["exchange"], entry["code"])
        item.bars = _bars_map(entry["bars"])
        item.complete_through, item.finalized_at = entry["complete_through"], _dt(entry["finalized_at"])
        item.data_source, item.contiguous = entry["data_source"], entry["contiguous"]
        item.final_pages, item.error = entry["final_pages"], entry["error"]
        return item
    return ({s: cache(s, e) for s, e in body["symbols"].items()}, cache("SPY", body["spy"]))
