"""Read E's runtime files and turn them into UI-friendly values. Read-only, and never raises.

E does not live in Strategy A's database: the paper session writes JSON under its own run root and
the RVOL worker keeps its own store. This module is the only place that knows those paths, so the
API layer (and therefore the browser) never touches a server file directly.

Every accessor degrades to ``None`` or an empty list when a file is missing, half-written or from an
older schema. A screen that shows "no data yet" is correct while the bootstrap is still running; a
screen that shows a zero it invented is not.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
PAPER_RUN_ENV = "STRATEGY_E_PAPER_RUN_DIR"
RVOL_ENV = "STRATEGY_E_RVOL_DIR"
UNIVERSE_ENV = "STRATEGY_E_UNIVERSE_DIR"
DEFAULT_PAPER_RUN = REPO_ROOT / "data/runtime/strategy_e_max/paper/run"
DEFAULT_RVOL = REPO_ROOT / "data/runtime/strategy_e_max/rvol"
DEFAULT_UNIVERSE = REPO_ROOT / "data/runtime/strategy_e_max/rt2"
H5_RULES = REPO_ROOT / "docs/backtest/strategy_e_candidate/e1_h5_confirmation_rules_v1.json"
STRATEGY_ID = "STRATEGY_E_MAX_V1"
PROVISIONAL = "PROVISIONAL_RVOL_BOOTSTRAP"
OFFICIAL = "OFFICIAL_KIWOOM_PAPER"
BOOK_STATUSES = (PROVISIONAL, OFFICIAL)


def paper_run_dir() -> Path:
    return Path(os.environ.get(PAPER_RUN_ENV, str(DEFAULT_PAPER_RUN)))


def rvol_dir() -> Path:
    return Path(os.environ.get(RVOL_ENV, str(DEFAULT_RVOL)))


def universe_dir() -> Path:
    return Path(os.environ.get(UNIVERSE_ENV, str(DEFAULT_UNIVERSE)))


def frozen_rvol_threshold() -> float | None:
    """The H5 RVOL bound as the frozen rules state it, so no screen has to hardcode 3.0."""
    body = _json(H5_RULES) or {}
    text = ((body.get("hypothesis") or {}).get("thresholds_frozen") or {}).get("premarket_rvol") or ""
    match = re.fullmatch(r">=\s*([0-9.]+)", text.strip())
    return float(match.group(1)) if match else None


def _json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return None


# -- sessions -------------------------------------------------------------------------------------

def session_dirs() -> list[Path]:
    root = paper_run_dir()
    try:
        return sorted((p for p in root.iterdir() if p.is_dir() and len(p.name) == 10), key=lambda p: p.name)
    except OSError:
        return []


def session_record(session: str | None = None) -> dict[str, Any] | None:
    """The newest session record, or a named session's."""
    for directory in reversed(session_dirs()):
        if session and directory.name != session:
            continue
        body = _json(directory / "session_record.json")
        if body is not None:
            return body | {"_dir": str(directory), "_updated_at": _mtime(directory / "session_record.json")}
        run = _json(directory / "run.json")
        if run is not None and session is None:
            return {"session": directory.name, "status": run.get("status"), "reason": run.get("reason"),
                    "_dir": str(directory), "_updated_at": _mtime(directory / "run.json")}
        if session:
            return None
    return None


def evidence_status(record: dict[str, Any] | None) -> str | None:
    return None if record is None else record.get("paper_evidence_status")


# -- the books ------------------------------------------------------------------------------------

def book_dir(status: str) -> Path:
    return paper_run_dir() / "paper_state" / status / STRATEGY_ID


def book(status: str) -> dict[str, Any] | None:
    return _json(book_dir(status) / "book.json")


def engine_session(status: str, session: str) -> dict[str, Any] | None:
    return _json(book_dir(status) / "sessions" / f"{session}.json")


def engine_sessions(status: str) -> list[dict[str, Any]]:
    directory = book_dir(status) / "sessions"
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return []
    return [body for body in (_json(p) for p in paths) if body]


# -- the RVOL bootstrap ---------------------------------------------------------------------------

def bootstrap() -> dict[str, Any]:
    """Coverage as the collector last reported it; the store is only counted if the file is absent."""
    root = rvol_dir()
    body = _json(root / "passes" / "coverage_latest.json")
    if body is None:
        # The collector has not finished a pass yet; the store knows how many symbols it has walked,
        # but not how many the canonical universe holds, so the denominator comes from the artifact
        # and completion is never claimed from this side.
        body = _store_coverage(root / "kiwoom_premarket.sqlite3")
        if body is not None:
            body["symbols"] = universe().get("symbol_count") or body.get("symbols")
    if body is None:
        return {"available": False, "status": "UNKNOWN", "reason": "no coverage report and no store yet"}
    total = int(body.get("symbols") or 0)
    ready = int(body.get("fully_ready") or 0)
    complete = (body.get("source") != "store" and total > 0
                and ready >= total - int(body.get("history_exhausted") or 0))
    return {"available": True, "status": "COMPLETE" if complete else "RUNNING",
            "symbols": total, "ready": ready,
            "partial": int(body.get("partially_ready") or 0), "zero": int(body.get("zero_history") or 0),
            "history_exhausted": int(body.get("history_exhausted") or 0),
            "target_sessions": int(body.get("window") or 0), "minimum_sessions": int(body.get("minimum") or 0),
            "percent": round(100.0 * ready / total, 1) if total else None,
            "last_update": body.get("at") or _mtime(root / "passes" / "coverage_latest.json"),
            "source": body.get("source") or "collector_report",
            "estimated_completion": None,
            "estimate_note": "런타임이 신뢰할 수 있는 완료 예정 시각을 제공하지 않는다"}


def _store_coverage(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        symbols = connection.execute("SELECT COUNT(*) FROM kiwoom_collection_state").fetchone()[0]
        ready = connection.execute(
            "SELECT COUNT(*) FROM kiwoom_collection_state WHERE staged_count>=20").fetchone()[0]
        exhausted = connection.execute(
            "SELECT COUNT(*) FROM kiwoom_collection_state WHERE history_exhausted=1").fetchone()[0]
        connection.close()
    except (sqlite3.Error, OSError):
        return None
    return {"symbols": symbols, "fully_ready": ready, "partially_ready": max(symbols - ready - exhausted, 0),
            "zero_history": None, "history_exhausted": exhausted, "window": 20, "minimum": 5,
            "at": _mtime(path), "source": "store"}


# -- the canonical universe -----------------------------------------------------------------------

def universe(session: date | None = None) -> dict[str, Any]:
    """What universe artifact is staged, and whether it is really the D-1 universe of the session."""
    directory = universe_dir()
    try:
        files = sorted(directory.glob("universe_*.json"))
    except OSError:
        files = []
    if not files:
        return {"available": False, "status": "UNIVERSE_NOT_AVAILABLE", "reason": "no artifact staged"}
    newest = files[-1]
    body = _json(newest) or {}
    target = body.get("target_session") or body.get("session")
    out = {"available": True, "file": newest.name, "target_session": target,
           "asof_session": body.get("asof_session") or body.get("d_minus_1"),
           "symbol_count": body.get("symbol_count") or len(body.get("symbols") or []),
           "source": body.get("source") or "MASSIVE", "digest": body.get("digest"),
           "generated_at": body.get("generated_at") or _mtime(newest),
           "rules_version": body.get("rules_version")}
    if session is not None:
        try:
            from app.market.calendar import MarketCalendar
            from app.strategy_e_max_rt import universe_build as UB
            ok, why = UB.d_minus_1_identity(body, session, MarketCalendar("America/New_York"))
        except Exception as error:                       # a bad artifact is a UI state, not a 500
            ok, why = False, f"{type(error).__name__}: {error}"
        out |= {"status": "READY" if ok else "UNIVERSE_NOT_AVAILABLE", "identity": why,
                "for_session": session.isoformat()}
    else:
        out["status"] = "READY"
    return out
