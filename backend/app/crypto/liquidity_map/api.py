"""The preview's own ASGI app: GET only, on its own port, registered on nothing else.

Why a separate app instead of routes on the deployed terminal. The terminal app owns the manual
order path, and this step is explicitly not allowed to disturb it. Adding routes there - even
read-only ones - puts this code in the same process as the order endpoints, inside the same
lifespan, behind the same deploy. A separate app on a separate port cannot reach the order path
even by accident, can be started and killed without touching the terminal, and is impossible to
deploy to production by forgetting something, because nothing in production imports it.

The app is deliberately small:

* every route is a GET, and the only state it mutates is a read cursor in its own memory;
* it imports the collector's contract constants and nothing from `paper`, `live` or `terminal`;
* it opens no socket to any venue - the collector is the only thing that talks to Binance;
* a missing or empty journal is a 200 with `NO_DATA` rather than an error, because "the collector
  is not running" is a thing the operator needs to see on the screen, not a stack trace.

    MS_V0_ROOT=/path/to/data python -m app.crypto.liquidity_map 8011
"""
from __future__ import annotations

import os
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import PREVIEW_VERSION
from . import checkpoint as CP
from . import journal as J
from . import view as V
from . import wallrule as R
from .wallstate import DEFAULT_MIN_NOTIONAL_USDT, WallFilter, WallFollower

#: The journal root. The same variable the collector writes to, read here and nowhere else.
ROOT_ENV = "MS_V0_ROOT"
#: Default port for the isolated preview. Chosen away from the terminal's 8000.
DEFAULT_PORT = 8011
#: Most candidates listed per side. The nearest few are the point; the rest is a count.
DEFAULT_WALL_LIMIT = 12
MAX_WALL_LIMIT = 50


def journal_root() -> Path | None:
    raw = os.environ.get(ROOT_ENV)
    if not raw:
        return None
    return Path(raw).expanduser()


class PreviewState:
    """One wall follower per root, kept for the life of the process.

    The follower is what makes a poll cheap: the first one reconstructs the candidate set, every
    later one reads only the bytes appended since. Keeping it here rather than rebuilding per
    request is the difference between a fixed cost and a cost that grows with session length.
    """

    def __init__(self) -> None:
        self.followers: dict[str, WallFollower] = {}

    def follower(self, root: Path) -> WallFollower:
        key = str(root)
        if key not in self.followers:
            self.followers[key] = WallFollower()
        return self.followers[key]


state = PreviewState()

app = FastAPI(title="US-B Liquidity Map V1 preview", version=PREVIEW_VERSION,
              description="Read-only viewer over the Market Structure V0 journal.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"],
                   allow_headers=["*"])


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": {"code": code, "message": message}})


def _decimal_param(raw: str | None, fallback: Decimal) -> Decimal | None:
    if raw is None:
        return fallback
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


@app.get("/api/liquidity-map/health")
async def health() -> Any:
    root = journal_root()
    now_ms = int(time.time() * 1000)
    state = None if root is None else CP.read(root, now_ms=now_ms)
    return {"preview_version": PREVIEW_VERSION, "mode": "READ_ONLY_JOURNAL_VIEWER",
            "root": None if root is None else str(root),
            "root_env": ROOT_ENV,
            "root_exists": bool(root is not None and root.exists()),
            "wall_rule": R.rule_identity(),
            "state_file": None if state is None else state.view(),
            "server_time_ms": now_ms}


def _record_from_state(file: CP.StateFile) -> dict[str, Any] | None:
    """The compact state file's metrics, shaped as the `derived` record the view expects.

    The view was written against a journal record and should not learn a second shape for the
    same payload, so the file is adapted here instead. `receive_ms` is the moment the file was
    written, which is what every age on the screen must be measured from: using now would make a
    collector that stopped writing look current, which is the failure this release is fixing.
    """
    derived = file.derived
    if not derived:
        return None
    collector = file.payload.get("collector") or {}
    session = file.payload.get("session") or {}
    return {"kind": "derived", "payload": derived, "receive_ms": file.written_ms,
            "seq": file.seq, "collector_version": collector.get("collector_version"),
            "exchange": collector.get("exchange"), "symbol": collector.get("symbol"),
            "session_id": session.get("session_id")}


@app.get("/api/liquidity-map/snapshot")
async def snapshot(min_notional_usdt: str | None = None,
                   wall_limit: int = DEFAULT_WALL_LIMIT) -> Any:
    """One poll: price, both sides, coverage, flow, walls, quality and the overlay lines.

    The read order is the point of V1.1. The compact state file is read first, and when it is
    usable it answers everything that used to cost a stream walk: the live candidate set, the
    latest metrics, freshness, coverage and the resnapshot policy. What is left is a bounded tail
    of wall transitions newer than the file's `seq`, which is normally empty. The journal walks
    below are the fallback for a session with no state file, and they are the only part of this
    endpoint whose cost depends on the session.
    """
    now_ms = int(time.time() * 1000)
    root = journal_root()
    if root is None:
        return V.empty_view(root="", now_ms=now_ms, reason=f"{ROOT_ENV}_NOT_SET")
    if not root.exists():
        return V.empty_view(root=str(root), now_ms=now_ms, reason="ROOT_NOT_FOUND")

    notional = _decimal_param(min_notional_usdt, DEFAULT_MIN_NOTIONAL_USDT)
    if notional is None:
        return error(400, "FILTER_INVALID", "min_notional_usdt must be a non-negative number")
    if wall_limit < 1 or wall_limit > MAX_WALL_LIMIT:
        return error(400, "WALL_LIMIT_INVALID", f"wall_limit must be 1..{MAX_WALL_LIMIT}")
    wall_filter = WallFilter(min_notional_usdt=notional)

    try:
        session = J.latest_session(root)
    except J.JournalEmpty:
        return V.empty_view(root=str(root), now_ms=now_ms, reason="NO_SESSION_RECORDED")

    state_file = CP.read(root, now_ms=now_ms, session_id=session.session_id)
    derived_bytes = telemetry_bytes = 0
    derived_record = _record_from_state(state_file) if state_file.present else None
    telemetry: list[dict[str, Any]] = []
    if derived_record is not None:
        telemetry = [{"seq": item.get("seq"), "receive_ms": item.get("receive_ms"),
                      "payload": item} for item in state_file.telemetry_recent]
    else:
        derived_record, derived_bytes = J.last_record(root, "derived", session)
        telemetry, telemetry_bytes = J.recent_records(root, "telemetry", session, limit=12)
    wall_set = state.follower(root).refresh(root, session, now_ms=now_ms, state=state_file)

    return V.snapshot_view(
        root=str(root), session=session, derived_record=derived_record, wall_set=wall_set,
        wall_filter=wall_filter, now_ms=now_ms, wall_limit=wall_limit, telemetry=telemetry,
        read_cost={"derived_bytes": derived_bytes, "telemetry_bytes": telemetry_bytes,
                   "state_bytes": state_file.bytes_read,
                   "state_usable": state_file.usable,
                   # `wall_bytes` already contains the state file plus its tail on the
                   # checkpoint path, and the backward scan on the fallback, so the total adds
                   # it once rather than adding the state file twice.
                   "wall_bytes": wall_set.scanned_bytes,
                   "wall_records": wall_set.scanned_records,
                   "wall_tail_bytes": wall_set.tail_bytes,
                   "wall_tail_records": wall_set.tail_records,
                   "total_bytes": derived_bytes + telemetry_bytes + wall_set.scanned_bytes,
                   "journal_walked": derived_bytes > 0 or not state_file.usable,
                   "elapsed_ms": max(0, int(time.time() * 1000) - now_ms)})


@app.get("/api/liquidity-map/sessions")
async def sessions() -> Any:
    """What the root holds. Useful when the screen says NO_DATA and the question is why."""
    root = journal_root()
    if root is None or not root.exists():
        return {"root": None if root is None else str(root), "sessions": [], "kinds": {}}
    kinds: dict[str, Any] = {}
    for kind in ("session", "derived", "wall", "telemetry", "storage_stats", "checkpoint"):
        files = J.stream_files(root, kind)
        kinds[kind] = {"files": len(files), "bytes": sum(item.size() for item in files),
                       "open_files": sum(1 for item in files if item.is_open)}
    try:
        session = J.latest_session(root)
    except J.JournalEmpty:
        return {"root": str(root), "sessions": [], "kinds": kinds}
    return {"root": str(root), "kinds": kinds,
            "state_file": CP.read(root, now_ms=int(time.time() * 1000)).view(),
            "sessions": [{"session_id": session.session_id, "started_ms": session.started_ms,
                          "ended": session.ended,
                          "contract": session.start_payload.get("contract"),
                          "config": session.start_payload.get("config")}]}


__all__ = ["app", "state", "journal_root", "ROOT_ENV", "DEFAULT_PORT", "DEFAULT_WALL_LIMIT",
           "MAX_WALL_LIMIT"]
