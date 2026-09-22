"""The US-B forward market store: layout, symbol path mapping, validation sidecars, quarantine.

Raw forward market data is strategy-independent and lives on the workspace (Drive) beside the
frozen snapshot tree, never inside it (E1 forward layout):

    market_data/forward/massive/
      grouped_daily/<year>/<D>.json.gz            + .validation.json
      splits/splits_asof_<D>.json.gz              + .validation.json
      reference/CS_<as_of>.json.gz                + .validation.json
      minute/<DIR>/<SYM>_<a>_<b>.pNN.json.gz      + .request.json (raw_fetch ledger) + .validation.json
      rvol_context/<DIR>/...                      pre-boundary minute history for RVOL only
      manifests/run_<run_id>.json                 one immutable manifest per collection run
      quarantine/                                 files that failed validation, kept, never deleted

``<DIR>`` is the storage directory of a symbol: the symbol itself, or ``_<SYMBOL>`` for a DOS device
name (``CON`` cannot be a directory on the Windows-hosted store). The strategy symbol never changes;
only the directory does. File names keep the real symbol.

A sidecar is written once. A file whose bytes no longer match its sidecar is CORRUPT and is never
overwritten silently; a file that fails validation is moved to quarantine so the next run can fetch
it again, and the quarantined copy is kept.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.backtest.strategy_e1_forward import layout

ET = ZoneInfo("America/New_York")
SOURCE, MODE, EVIDENCE_CLASS = "MASSIVE", "RECONSTRUCTED", "SECONDARY"
COLLECTOR_VERSION = "e-max-f1-forward-collector/2026-09-22.a"
ROOT = layout.FORWARD_MARKET_DATA
MINUTE = f"{ROOT}/minute"
RVOL_CONTEXT = f"{ROOT}/rvol_context"
MANIFESTS = f"{ROOT}/manifests"
QUARANTINE = f"{ROOT}/quarantine"
STAGING = f"{ROOT}/.staging"
PASS, FAIL = "PASS", "FAIL"
RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                      *(f"COM{d}" for d in "0123456789"), *(f"LPT{d}" for d in "0123456789")})
MINUTE_WINDOW = (time(4, 0), time(20, 0))


class ForwardStoreError(RuntimeError):
    """A write that would break the forward store's append-only contract."""


# -- symbol <-> storage directory -------------------------------------------------------------------

def symbol_to_storage(symbol: str) -> str:
    return f"_{symbol}" if symbol.upper() in RESERVED else symbol


def storage_to_symbol(directory: str) -> str:
    if directory.startswith("_") and directory[1:].upper() in RESERVED:
        return directory[1:]
    return directory


# -- paths -------------------------------------------------------------------------------------------

def grouped_path(root: Path, session: date) -> Path:
    return layout.grouped_daily_path(root, session)


def splits_asof_path(root: Path, session: date) -> Path:
    return layout.splits_path(root, session)


def reference_path(root: Path, as_of: date) -> Path:
    return layout.reference_path(root, as_of)


def minute_stem(root: Path, tree: str, symbol: str, start: date, end: date) -> Path:
    return root / tree / symbol_to_storage(symbol) / f"{symbol}_{start.isoformat()}_{end.isoformat()}"


def sidecar_path(path: Path) -> Path:
    name = path.name[:-len(".request.json")] if path.name.endswith(".request.json") else path.name
    return path.with_name(name + ".validation.json")


def require_forward_tree(root: Path, path: Path) -> Path:
    """Every forward write must land under market_data/forward, never the frozen raw tree."""
    try:
        path.resolve().relative_to((root / ROOT).resolve())
    except ValueError as error:
        raise ForwardStoreError(f"{path} is outside the forward store") from error
    return path


# -- sidecars ----------------------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_sidecar(path: Path) -> dict[str, Any] | None:
    side = sidecar_path(path)
    return json.loads(side.read_text(encoding="utf-8")) if side.is_file() else None


def write_sidecar(root: Path, path: Path, payload: Mapping[str, Any]) -> Path:
    side = require_forward_tree(root, sidecar_path(path))
    if side.exists():
        raise ForwardStoreError(f"{side.name} exists; a validation sidecar is written once")
    body = {"source": SOURCE, "mode": MODE, "evidence_class": EVIDENCE_CLASS,
            "collector_version": COLLECTOR_VERSION, **payload}
    tmp = side.with_name(side.name + ".partial")
    tmp.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, side)
    return side


def verified(path: Path, *, recheck: bool = False) -> bool:
    """Present, validated PASS, and (with ``recheck``) still byte-identical to its sidecar."""
    side = read_sidecar(path)
    if not path.is_file() or side is None or side.get("status") != PASS:
        return False
    return not recheck or side.get("sha256") == sha256_file(path)


def quarantine(root: Path, paths: Sequence[Path], *, reason: str, run_id: str) -> list[str]:
    """Move failed files aside (kept, never deleted) so a later run may fetch them again."""
    target = root / QUARANTINE / run_id
    target.mkdir(parents=True, exist_ok=True)
    moved = []
    for path in paths:
        if path.exists():
            require_forward_tree(root, path)
            dest = target / path.name
            os.replace(path, dest)
            moved.append(str(dest.relative_to(root)))
    (target / "REASON.txt").open("a", encoding="utf-8").write(f"{reason}\n")
    return moved


# -- validation --------------------------------------------------------------------------------------

def _read_json_gz(path: Path) -> Any:
    return json.loads(gzip.decompress(path.read_bytes()))


def _et(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(ET)


def _finite_positive(*values: Any) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0
               for v in values)


def validate_grouped(path: Path, session: date) -> dict[str, Any]:
    """Grouped daily for session D: one row per ticker, all bars dated D, sane OHLCV, adjusted=false."""
    payload = _read_json_gz(path)
    errors: list[str] = []
    if "error" in payload:
        return {"status": FAIL, "errors": [f"provider error {payload['error']}"], "rows": 0, "symbols": 0}
    if payload.get("session") != session.isoformat():
        errors.append(f"wrapper session {payload.get('session')!r} != {session}")
    body = payload.get("body") or {}
    if body.get("adjusted") is not False:
        errors.append("adjusted is not false")
    rows = body.get("results") or []
    seen, dup, bad_ohlc, neg_vol, wrong_date = set(), [], [], [], []
    for item in rows:
        ticker = item.get("T")
        if ticker in seen:
            dup.append(ticker)
        seen.add(ticker)
        o, h, l, c, v = (item.get(k) for k in ("o", "h", "l", "c", "v"))
        if not _finite_positive(o, h, l, c) or h < max(o, c, l) or l > min(o, c, h):
            bad_ohlc.append(ticker)
        if not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            neg_vol.append(ticker)
        t = item.get("t")
        if not isinstance(t, (int, float)) or _et(int(t)).date() != session:
            wrong_date.append(ticker)
    if not rows:
        errors.append("no rows")
    if dup:
        errors.append(f"{len(dup)} duplicate ticker rows")
    if wrong_date:
        errors.append(f"{len(wrong_date)} rows not dated {session} (future/past contamination)")
    return {"status": PASS if not errors else FAIL, "errors": errors, "rows": len(rows),
            "symbols": len(seen), "duplicate_tickers": dup[:20],
            "invalid_ohlc_rows": len(bad_ohlc), "invalid_ohlc_examples": bad_ohlc[:20],
            "invalid_volume_rows": len(neg_vol), "wrong_date_rows": len(wrong_date),
            "note": "rows with invalid OHLC or volume are counted, not repaired; E0 daily eligibility "
                    "reads close and volume only, as in development"}


def validate_splits(path: Path, session: date, start: date) -> dict[str, Any]:
    payload = _read_json_gz(path)
    rows = payload.get("results") or []
    errors, future, malformed, keys, dup = [], 0, 0, set(), 0
    for item in rows:
        try:
            executed = date.fromisoformat(item["execution_date"])
            ok = float(item["split_from"]) > 0 and float(item["split_to"]) > 0 and isinstance(item["ticker"], str)
        except (KeyError, TypeError, ValueError):
            malformed += 1
            continue
        if not ok:
            malformed += 1
        if executed > session or executed < start:
            future += executed > session
        key = (item["ticker"], executed)
        dup += key in keys
        keys.add(key)
    if future:
        errors.append(f"{future} splits executed after {session}")
    if payload.get("end") != session.isoformat() or payload.get("start") != start.isoformat():
        errors.append("request range does not match the snapshot name")
    return {"status": PASS if not errors else FAIL, "errors": errors, "rows": len(rows),
            "malformed_rows": malformed, "duplicate_ticker_dates": dup,
            "pit_semantics": "Massive /v3/reference/splits filters by execution_date only; it has no "
                             "publication-time as-of view. The snapshot is 'executions <= D as retrieved at "
                             "retrieved_at', admissible for RECONSTRUCTED (E-R1 contract), not a LIVE list"}


def validate_reference(path: Path, as_of: date) -> dict[str, Any]:
    payload = _read_json_gz(path)
    rows = payload.get("results") or []
    tickers = [r.get("ticker") for r in rows]
    errors = []
    if payload.get("as_of") != as_of.isoformat():
        errors.append("as_of mismatch")
    if not rows:
        errors.append("no rows")
    if len(set(tickers)) != len(tickers):
        errors.append(f"{len(tickers) - len(set(tickers))} duplicate tickers")
    return {"status": PASS if not errors else FAIL, "errors": errors, "rows": len(rows)}


def validate_minute(pages: Sequence[Path], symbol: str, start: date, end: date,
                    sessions: Sequence[date]) -> dict[str, Any]:
    """Every bar: this symbol, a session in [start, end], 04:00-20:00 ET, strictly increasing
    (no duplicate minute), finite positive OHLC with high >= low, volume >= 0."""
    allowed = {d.isoformat() for d in sessions}
    errors: list[str] = []
    counts = {"bars": 0, "duplicate_or_unsorted": 0, "out_of_session": 0, "out_of_window": 0,
              "non_finite_ohlc": 0, "negative_volume": 0, "symbol_mismatch": 0}
    days: set[str] = set()
    last = None
    for page in pages:
        body = json.loads(gzip.decompress(page.read_bytes()))
        if body.get("ticker") not in (None, symbol):
            counts["symbol_mismatch"] += 1
        if body.get("adjusted") is not False:
            errors.append(f"{page.name} adjusted is not false")
        for bar in body.get("results") or ():
            counts["bars"] += 1
            t = bar.get("t")
            if not isinstance(t, (int, float)):
                counts["out_of_session"] += 1
                continue
            if last is not None and t <= last:
                counts["duplicate_or_unsorted"] += 1
            last = t
            moment = _et(int(t))
            day = moment.date().isoformat()
            days.add(day)
            if day not in allowed:
                counts["out_of_session"] += 1
            if not (MINUTE_WINDOW[0] <= moment.time() < MINUTE_WINDOW[1]):
                counts["out_of_window"] += 1
            o, h, l, c, v = (bar.get(k) for k in ("o", "h", "l", "c", "v"))
            if not _finite_positive(o, h, l, c) or h < l:
                counts["non_finite_ohlc"] += 1
            if not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                counts["negative_volume"] += 1
    for key in ("duplicate_or_unsorted", "out_of_session", "out_of_window", "non_finite_ohlc",
                "negative_volume", "symbol_mismatch"):
        if counts[key]:
            errors.append(f"{counts[key]} {key}")
    return {"status": PASS if not errors else FAIL, "errors": errors, **counts,
            "sessions_with_bars": sorted(days), "requested_sessions": sorted(allowed)}
