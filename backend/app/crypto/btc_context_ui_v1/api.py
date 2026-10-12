"""GET-only API for the BTC context strip: `GET /health`, `GET /snapshot` and the preview-only
`GET /entry-analysis` (a read of stored research results; 관망 unless a family passed its gate).

    CTX_V1_ROOT=/path python -m app.crypto.btc_context_ui_v1 --port 8014

A separate process from the terminal (8100), the Context Collector and its API, so this one can
fail, restart or be overloaded without any of them noticing. The terminal treats it the same way:
when it does not answer, the strip shows dashes and the order ticket is untouched.

How each request is answered, and why it never waits on a venue:

* LIQUIDITY and FLOW are read from the collector's current-state file on every request - one small
  local file, through `context_collector_v1.api.judged`, which floors an old file to STALE.
* STRUCTURE is read from the snapshot file the structure worker (`structure_worker`, a separate
  oneshot run once a minute) writes atomically. This process never holds the 30-day series or the
  R1 engine and does not import numpy: a missing, unreadable, foreign or old file is a dash.
* ENTRY is the Manual Trade Snapshot Recorder's entry snapshot of the open BTC episode, read only
  from its store (`BTC_CONTEXT_MANUAL_STORE`; unset means none) and re-read only when a store file
  changed. A store row the recorder did not write is refused unless the process was started with
  `--allow-fixture-entry` (a preview). There is no other source and no fallback: no recorder row,
  no entry context.
* DERIVATIVES are served from the last completed read. A request that finds them due starts one
  read in a worker thread and returns at once; a read already running is not started twice.
  Nobody watching means no venue traffic from this process at all.

Nothing here writes a file, holds a lock on the collector's root or opens a socket to anything but
Binance's public market-data endpoints.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..context_collector_v1 import api as CTX
from ..market_context_v1 import contract as MC
from ..market_context_v1 import derivatives as DRV
from . import entry_context as EC
from . import view as V
from ..research.wall_lifecycle_r1 import preview as EA

DEFAULT_PORT = 8014
ROOT_ENV = CTX.ROOT_ENV
#: Path of the structure worker's snapshot file. Unset means STRUCTURE reads `--`.
STRUCTURE_SNAPSHOT_ENV = "BTC_CONTEXT_STRUCTURE_SNAPSHOT"
#: The snapshot is a few hundred bytes; anything far larger is not one.
STRUCTURE_SNAPSHOT_MAX_BYTES = 64 * 1024
#: Floor between two derivatives reads. The open interest figures move on the venue's 5 m buckets
#: and funding on its 8 h schedule; 15 s keeps the premium current without polling the venue at
#: the screen's own pace.
DERIVATIVES_MIN_INTERVAL_MS = 15_000
class Sources:
    """The last derivatives read, refreshed off the request path."""

    def __init__(self, *, opener: Any = None) -> None:
        self.opener = opener
        self.derivatives: DRV.DerivativesRead | None = None
        self._busy = threading.Lock()

    def derivatives_due(self, now_ms: int) -> bool:
        return (self.derivatives is None
                or now_ms - self.derivatives.read_at_ms >= DERIVATIVES_MIN_INTERVAL_MS)

    def refresh(self, now_ms: int) -> None:
        """One derivatives read if due. Never raises: a failed read keeps the last good one."""
        if self.derivatives_due(now_ms):
            try:
                self.derivatives = DRV.read(V.SYMBOL, now_ms, opener=self.opener)
            except Exception:                          # a venue fault must not end the worker
                pass

    def kick(self, now_ms: int) -> bool:
        """Start one background read if due and none is running."""
        if not self.derivatives_due(now_ms):
            return False
        if not self._busy.acquire(blocking=False):
            return False

        def work() -> None:
            try:
                self.refresh(int(time.time() * 1000))
            finally:
                self._busy.release()

        threading.Thread(target=work, name="btc-context-refresh", daemon=True).start()
        return True

    def derivatives_layer(self, now_ms: int) -> dict[str, Any]:
        return DRV.derivatives_view(self.derivatives, symbol=V.SYMBOL, now_ms=now_ms)


def structure_snapshot_path() -> Path | None:
    raw = os.environ.get(STRUCTURE_SNAPSHOT_ENV, "").strip()
    return Path(raw) if raw else None


def read_structure_snapshot(path: Path | None) -> dict[str, Any] | None:
    """The worker's last snapshot, or `None` if it is missing, oversized, not JSON or not a BTC
    snapshot. The worker replaces the file by rename, so a read sees a whole file or none."""
    if path is None:
        return None
    try:
        with open(path, "rb") as handle:
            raw = handle.read(STRUCTURE_SNAPSHOT_MAX_BYTES + 1)
        if len(raw) > STRUCTURE_SNAPSHOT_MAX_BYTES:
            return None
        snap = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(snap, dict) or snap.get("symbol") != V.SYMBOL:
        return None
    close_ms = snap.get("bar_close_ms")
    if not isinstance(close_ms, int) or isinstance(close_ms, bool):
        return None
    return snap


def structure_layer(snap: dict[str, Any] | None, now_ms: int) -> dict[str, Any]:
    """The snapshot as the STRUCTURE layer `view.structure_section` reads: LIVE while the last
    closed minute is within Market Context V1's own grace (`STRUCTURE_GRACE_MS`), STALE after -
    the same rule `market_context_v1.structure.structure_view` applies to the in-process run."""
    if snap is None:
        return {"state": "UNKNOWN", "levels": None}
    age_ms = max(0, now_ms - int(snap["bar_close_ms"]))
    state = "LIVE" if age_ms <= MC.MINUTE_MS + MC.STRUCTURE_GRACE_MS else "STALE"

    def level(side: str) -> dict[str, Any]:
        item = snap.get(side) if isinstance(snap.get(side), dict) else {}
        price = item.get("price")
        return {"price": price, "distance_pct": item.get("distance_pct"),
                "available": price is not None}

    return {"state": state, "levels": {"resistance": level("resistance"),
                                       "support": level("support")}}


def collector_view(now_ms: int) -> dict[str, Any] | None:
    """The collector's current reading, floored by its own API rules. `None` if not configured."""
    root = CTX.context_root()
    if root is None:
        return None
    try:
        return CTX.judged(root, now_ms=now_ms)
    except Exception:                                  # a reader fault must not blank the strip
        return None


class _State:
    sources: Sources | None = None
    entry: EC.EntryReader = EC.EntryReader()

    def get(self) -> Sources:
        if self.sources is None:
            self.sources = Sources()
        return self.sources


state = _State()

# No /docs, /redoc or /openapi.json: the only routes served are the two below.
app = FastAPI(title="US-B BTC manual context strip", description="Read-only. GET only.",
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"],
                   allow_headers=["*"])


@app.get("/health")
async def health() -> Any:
    root = CTX.context_root()
    return {"ok": True, "symbol": V.SYMBOL, "collector_root_set": root is not None,
            "server_time_ms": int(time.time() * 1000)}


@app.get("/snapshot")
async def snapshot() -> Any:
    now_ms = int(time.time() * 1000)
    sources = state.get()
    sources.kick(now_ms)
    snap = read_structure_snapshot(structure_snapshot_path())
    return V.display(collector_view=collector_view(now_ms),
                     structure_layer=structure_layer(snap, now_ms),
                     derivatives_layer=sources.derivatives_layer(now_ms),
                     now_ms=now_ms, entry=entry_reading())


@app.get("/entry-analysis")
async def entry_analysis() -> Any:
    """PREVIEW: the entry advisor's verdict for this second (CONTRACT_ENTRY_ADVISOR_R0 section
    10). Read only; with no gate-passing family it is NO_TRADE (관망), which is the case today."""
    now_ms = int(time.time() * 1000)
    sources = state.get()
    snap = read_structure_snapshot(structure_snapshot_path())
    view = collector_view(now_ms) or {}
    body = V.display(collector_view=view, structure_layer=structure_layer(snap, now_ms),
                     derivatives_layer=sources.derivatives_layer(now_ms), now_ms=now_ms,
                     entry=entry_reading())
    session8 = str(view.get("session_id") or "")[:8]
    return EA.analysis(body, EA.load_results(EA.results_path()), session8=session8, sample_ms=now_ms)


def entry_reading() -> dict[str, Any]:
    """The recorder's entry context, read only. A reader fault is "not available", never an error."""
    try:
        return state.entry.read(EC.store_root(), allow_fixture=EC.fixture_allowed())
    except Exception:                                  # must not blank the rest of the payload
        return {"available": False, "reason": EC.NO_STORE}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.crypto.btc_context_ui_v1")
    parser.add_argument("--root", default=os.environ.get(ROOT_ENV))
    parser.add_argument("--structure-snapshot", default=os.environ.get(STRUCTURE_SNAPSHOT_ENV))
    parser.add_argument("--manual-store", default=os.environ.get(EC.STORE_ENV))
    parser.add_argument("--allow-fixture-entry", action="store_true",
                        help="PREVIEW ONLY: serve a hand-built entry store. Never in production.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if args.root:
        os.environ[ROOT_ENV] = str(Path(args.root).expanduser().resolve())
    if args.structure_snapshot:
        os.environ[STRUCTURE_SNAPSHOT_ENV] = str(Path(args.structure_snapshot).expanduser().resolve())
    if args.manual_store:
        os.environ[EC.STORE_ENV] = str(Path(args.manual_store).expanduser().resolve())
    if args.allow_fixture_entry:
        os.environ[EC.ALLOW_FIXTURE_ENV] = "1"
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)
    return 0


__all__ = ["app", "state", "Sources", "collector_view", "read_structure_snapshot",
           "structure_layer", "structure_snapshot_path", "main", "DEFAULT_PORT",
           "DERIVATIVES_MIN_INTERVAL_MS", "STRUCTURE_SNAPSHOT_ENV"]
