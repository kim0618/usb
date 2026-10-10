"""Read-only API over a context root: `GET /health`, `GET /snapshot`, `GET /status`. Nothing else.

A separate process from the collector, for the same reason the Liquidity Map preview is: a reader
that can be restarted, crash or be overloaded without touching the thing that writes, and a writer
whose disk problems cannot reach the process that answers the screen. The two share only files.

What this process may do is narrow and checked by tests rather than promised:

* every route is a GET, and the app registers no other method;
* it opens no socket to any venue and imports no order, account, live, paper or terminal module;
* it writes nothing - not the root, not a cursor file, not a cache;
* **it never serves an old reading as a current one.** The latest file carries the moment it was
  written. Past `READ_STALE_MS` (the viewer's own three-sample bound), or once the session has
  ended, every state inside the payload is floored to STALE before it is returned - collector,
  layers, flow windows and wall states alike - and the floor is published with its reason. An
  absent or unreadable file is a 200 with UNKNOWN, because "the collector is not running" is a
  thing the screen must show, not an error page.

    CTX_V1_ROOT=/path python -m app.crypto.context_collector_v1 serve --port 8013
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..liquidity_map import journal as J
from ..market_structure_v0.store import STATE_DIRNAME, WriterLock
from . import COLLECTOR_VERSION, VERSION
from .contract import (COMPRESSED_SUFFIX, CONTEXT_KINDS, FEED_STALE, FEED_UNKNOWN,
                       LATEST_FILENAME, PERSISTED_V0_KINDS, READ_STALE_MS, SUPPORTED_SYMBOL)
from .mcv1_vendored import contract as MCC
from .mcv1_vendored import flow as MCF
from .mcv1_vendored import liquidity as MCL

ROOT_ENV = "CTX_V1_ROOT"
DEFAULT_PORT = 8013
MODE = "READ_ONLY_CONTEXT_API"

#: Reasons the read floor applied.
FLOOR_AGE = "LATEST_OLDER_THAN_READ_STALE_MS"
FLOOR_ENDED = "SESSION_ENDED"
NO_FILE = "NO_CONTEXT_FILE"
UNREADABLE = "CONTEXT_FILE_UNREADABLE"
NOT_SET = f"{ROOT_ENV}_NOT_SET"


def context_root() -> Path | None:
    raw = os.environ.get(ROOT_ENV)
    return None if not raw else Path(raw).expanduser()


def read_latest(root: Path) -> tuple[dict[str, Any] | None, str | None, int]:
    """(payload, reason it is unusable, bytes read). Never raises."""
    path = root / STATE_DIRNAME / LATEST_FILENAME
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None, NO_FILE, 0
    except OSError:
        return None, UNREADABLE, 0
    try:
        payload = json.loads(data)
    except ValueError:
        return None, UNREADABLE, len(data)
    if not isinstance(payload, dict) or payload.get("schema") != VERSION:
        return None, UNREADABLE, len(data)
    return payload, None, len(data)


def _floor_state(value: Any) -> Any:
    """LIVE and PARTIAL become STALE; UNKNOWN, UNAVAILABLE and STALE stay what they are."""
    return MCC.STALE if value in (MCC.LIVE, MCC.PARTIAL) else value


def _floor_states_everywhere(node: Any) -> None:
    """Any `state` or `*_state` key anywhere that still says LIVE or PARTIAL becomes STALE.

    The named fields below are floored with their reasons; this sweep is what guarantees that a
    nested description written at the time - a journal's `feed_state`, a stream's `journal_state`
    - cannot carry the word LIVE out of a payload that is no longer current. Coverage words are
    left alone: COMPLETE and PARTIAL there are measurements of the sample, not claims about now.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if (key == "state" or key.endswith("_state")) and value in (MCC.LIVE, MCC.PARTIAL):
                node[key] = MCC.STALE
            else:
                _floor_states_everywhere(value)
    elif isinstance(node, list):
        for item in node:
            _floor_states_everywhere(item)


def floor_payload(payload: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    """Every state in the payload floored to STALE. Values stay, labelled, as the contract asks."""
    out = copy.deepcopy(payload)
    collector = out.get("collector") or {}
    if collector.get("state") != FEED_UNKNOWN:
        collector["state"] = FEED_STALE
    collector["reasons"] = list(reasons) + list(collector.get("reasons") or [])
    out["collector"] = collector
    liquidity = out.get("liquidity")
    if isinstance(liquidity, dict):
        liquidity["state"] = _floor_state(liquidity.get("state"))
        liquidity["reasons"] = list(reasons) + list(liquidity.get("reasons") or [])
        for side in (liquidity.get("sides") or {}).values():
            if isinstance(side, dict) and side.get("wall_state") in (MCC.WALL_OK, MCC.WALL_NONE,
                                                                     MCC.PARTIAL):
                side["wall_state"] = MCC.STALE
                side["wall_state_reason"] = reasons[0]
    flow = out.get("flow")
    if isinstance(flow, dict):
        flow["state"] = _floor_state(flow.get("state"))
        flow["reasons"] = list(reasons) + list(flow.get("reasons") or [])
        for window in (flow.get("windows") or {}).values():
            if isinstance(window, dict):
                window["state"] = _floor_state(window.get("state"))
        stream = flow.get("trade_stream")
        if isinstance(stream, dict):
            stream["state"] = _floor_state(stream.get("state"))
        absorption = flow.get("absorption")
        if isinstance(absorption, dict) and absorption.get("state") == MCC.ABSORPTION_CANDIDATE:
            # A candidate is a statement about an interval that has ended; on a stale reading the
            # row reads NONE with the reason, as the panel does for a non-LIVE journal.
            flow["absorption"] = {"state": MCC.WALL_NONE, "reason": reasons[0],
                                  "order_identity_proven": False, "note": MCC.ABSORPTION_NOTE}
    _floor_states_everywhere(out)
    return out


def judged(root: Path, *, now_ms: int) -> dict[str, Any]:
    """The latest payload as it may be served right now, with how that was decided."""
    payload, reason, size = read_latest(root)
    if payload is None:
        return {"collector": {"state": FEED_UNKNOWN, "reasons": [reason],
                              "is_rollup_of_layers": False},
                "liquidity": {"state": MCC.UNKNOWN, "reasons": [reason]},
                "flow": {"state": MCC.UNKNOWN, "reasons": [reason]},
                "read": {"age_ms": None, "floored": False, "bytes": size,
                         "read_stale_ms": READ_STALE_MS}}
    written = payload.get("written_ms")
    age = None if not isinstance(written, int) else max(0, now_ms - written)
    reasons: list[str] = []
    if payload.get("session_ended"):
        reasons.append(FLOOR_ENDED)
    if age is None or age > READ_STALE_MS:
        reasons.append(FLOOR_AGE)
    out = floor_payload(payload, reasons) if reasons else payload
    out["read"] = {"age_ms": age, "floored": bool(reasons), "floor_reasons": reasons,
                   "bytes": size, "read_stale_ms": READ_STALE_MS}
    return out


def writer_holder(root: Path) -> dict[str, Any] | None:
    """What the collector's lock file says about who holds it. A hint, never a lock."""
    holder = WriterLock(root)._read_holder()
    if not isinstance(holder, dict):
        return None
    pid = holder.get("pid")
    alive = None
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
        except OSError:
            alive = None
    return {**holder, "pid_alive": alive}


def _disk(root: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kind in (*PERSISTED_V0_KINDS, *CONTEXT_KINDS):
        directory = root / kind
        files = sorted(directory.glob("*")) if directory.exists() else []
        out[kind] = {"files": len(files), "bytes": sum(p.stat().st_size for p in files),
                     "compressed_files": sum(1 for p in files
                                             if p.name.endswith(COMPRESSED_SUFFIX)),
                     "open_files": sum(1 for p in files if p.name.endswith(".open"))}
    return out


app = FastAPI(title="US-B Production Context Collector V1 API", version=COLLECTOR_VERSION,
              description="Read-only. GET only. No order, account or venue access.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"],
                   allow_headers=["*"])


@app.get("/health")
async def health() -> Any:
    root = context_root()
    now_ms = int(time.time() * 1000)
    if root is None:
        return {"mode": MODE, "version": VERSION, "root": None, "state": FEED_UNKNOWN,
                "reasons": [NOT_SET], "server_time_ms": now_ms}
    view = judged(root, now_ms=now_ms)
    collector = view.get("collector") or {}
    return {"mode": MODE, "version": VERSION, "root": str(root), "root_exists": root.exists(),
            "symbol": SUPPORTED_SYMBOL, "state": collector.get("state"),
            "reasons": collector.get("reasons"), "read": view.get("read"),
            "writer": writer_holder(root), "server_time_ms": now_ms}


@app.get("/snapshot")
async def snapshot(symbol: str = SUPPORTED_SYMBOL) -> Any:
    now_ms = int(time.time() * 1000)
    if symbol != SUPPORTED_SYMBOL:
        # Built by a path that never opens the BTC root, so no BTC figure can leak.
        return {"mode": MODE, "symbol": symbol, "server_time_ms": now_ms,
                "collector": {"state": MCC.UNAVAILABLE,
                              "reasons": [MCC.REASON_COLLECTOR_BTC_ONLY]},
                "liquidity": MCL.unavailable_view(symbol, MCC.REASON_COLLECTOR_BTC_ONLY),
                "flow": MCF.unavailable_view(symbol, MCC.REASON_COLLECTOR_BTC_ONLY)}
    root = context_root()
    if root is None:
        return {"mode": MODE, "symbol": symbol, "server_time_ms": now_ms,
                "collector": {"state": FEED_UNKNOWN, "reasons": [NOT_SET]},
                "liquidity": {"state": MCC.UNKNOWN, "reasons": [NOT_SET]},
                "flow": {"state": MCC.UNKNOWN, "reasons": [NOT_SET]}}
    view = judged(root, now_ms=now_ms)
    view["mode"] = MODE
    view["server_time_ms"] = now_ms
    return view


@app.get("/status")
async def status() -> Any:
    root = context_root()
    now_ms = int(time.time() * 1000)
    if root is None or not root.exists():
        return {"mode": MODE, "root": None if root is None else str(root), "sessions": [],
                "server_time_ms": now_ms}
    session_view = None
    stats = None
    try:
        session = J.latest_session(root)
        session_view = {"session_id": session.session_id, "started_ms": session.started_ms,
                        "ended": session.ended, "end": session.end_payload,
                        "config": session.start_payload.get("config")}
        record, _ = J.last_record(root, "storage_stats", session)
        stats = None if record is None else record.get("payload")
    except J.JournalEmpty:
        pass
    view = judged(root, now_ms=now_ms)
    link = root / STATE_DIRNAME
    cache = {"volatile": link.is_symlink(),
             "target": os.readlink(link) if link.is_symlink() else None,
             "target_present": link.exists()}
    return {"mode": MODE, "version": VERSION, "root": str(root), "session": session_view,
            "collector": view.get("collector"), "read": view.get("read"),
            "writer": writer_holder(root), "disk": _disk(root), "state_cache": cache,
            "storage_stats": stats, "server_time_ms": now_ms}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.crypto.context_collector_v1 serve")
    parser.add_argument("--root", default=os.environ.get(ROOT_ENV))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if args.root:
        os.environ[ROOT_ENV] = str(Path(args.root).expanduser().resolve())
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)
    return 0


__all__ = ["app", "judged", "floor_payload", "read_latest", "writer_holder", "context_root",
           "ROOT_ENV", "DEFAULT_PORT", "MODE", "FLOOR_AGE", "FLOOR_ENDED", "NO_FILE",
           "UNREADABLE"]
