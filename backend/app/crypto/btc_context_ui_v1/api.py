"""GET-only API for the BTC context strip: `GET /health` and `GET /snapshot`. Nothing else.

    CTX_V1_ROOT=/path python -m app.crypto.btc_context_ui_v1 --port 8014

A separate process from the terminal (8100), the Context Collector and its API, so this one can
fail, restart or be overloaded without any of them noticing. The terminal treats it the same way:
when it does not answer, the strip shows dashes and the order ticket is untouched.

How each request is answered, and why it never waits on a venue:

* LIQUIDITY and FLOW are read from the collector's current-state file on every request - one small
  local file, through `context_collector_v1.api.judged`, which floors an old file to STALE.
* STRUCTURE and DERIVATIVES are served from the last completed refresh. A request that finds them
  due starts one refresh in a worker thread and returns at once; a refresh already running is not
  started twice. Nobody watching means no venue traffic at all.

Nothing here writes a file, holds a lock on the collector's root or opens a socket to anything but
Binance's public market-data endpoints.
"""
from __future__ import annotations

import argparse
import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..context_collector_v1 import api as CTX
from ..market_context_v1 import derivatives as DRV
from ..market_context_v1 import structure as STR
from ..market_context_v1 import venue as VN
from . import view as V

DEFAULT_PORT = 8014
ROOT_ENV = CTX.ROOT_ENV
#: Floor between two derivatives reads. The open interest figures move on the venue's 5 m buckets
#: and funding on its 8 h schedule; 15 s keeps the premium current without polling the venue at
#: the screen's own pace.
DERIVATIVES_MIN_INTERVAL_MS = 15_000
#: The structure series publishes only its newest minutes: the live reading is the last closed
#: minute whatever this is, and a short window keeps the process small. The anchor, the series and
#: the R1 book are untouched by it.
PUBLISH_WINDOW_MS = 10 * 60_000


class Sources:
    """The structure series and the last derivatives read, refreshed off the request path."""

    def __init__(self, *, anchor_days: int = 30, opener: Any = None) -> None:
        self.opener = opener
        self.anchor_ms = STR.default_anchor_ms(int(time.time() * 1000), anchor_days)
        self.series = STR.Series(V.SYMBOL, anchor_ms=self.anchor_ms)
        self.run: STR.StructureRun | None = None
        self.structure_error: str | None = None
        self.derivatives: DRV.DerivativesRead | None = None
        self._busy = threading.Lock()

    # -- due checks ---------------------------------------------------------------------------
    def structure_due(self, now_ms: int) -> bool:
        return self.run is None or self.series.last_open_ms != STR.last_closed_open_ms(now_ms)

    def derivatives_due(self, now_ms: int) -> bool:
        return (self.derivatives is None
                or now_ms - self.derivatives.read_at_ms >= DERIVATIVES_MIN_INTERVAL_MS)

    # -- refresh ------------------------------------------------------------------------------
    def refresh(self, now_ms: int) -> None:
        """One refresh of whatever is due. Never raises: a failed read keeps the last good one."""
        if self.structure_due(now_ms):
            try:
                added = self.series.extend(now_ms, opener=self.opener)
                if self.series.rows and (added or self.run is None):
                    self.run = self.series.run(publish_window_ms=PUBLISH_WINDOW_MS, now_ms=now_ms)
                self.structure_error = None
            except VN.VenueError as caught:
                self.structure_error = str(caught)
            except Exception as caught:                # the worker must outlive any bad series
                self.structure_error = f"SERIES_UNUSABLE:{type(caught).__name__}"
        if self.derivatives_due(now_ms):
            try:
                self.derivatives = DRV.read(V.SYMBOL, now_ms, opener=self.opener)
            except Exception:                          # a venue fault must not end the worker
                pass

    def kick(self, now_ms: int) -> bool:
        """Start one background refresh if something is due and none is running."""
        if not (self.structure_due(now_ms) or self.derivatives_due(now_ms)):
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

    # -- layers -------------------------------------------------------------------------------
    def structure_layer(self, now_ms: int) -> dict[str, Any]:
        reasons = [self.structure_error] if self.structure_error else None
        return STR.structure_view(self.run, symbol=V.SYMBOL, now_ms=now_ms, reasons=reasons)

    def derivatives_layer(self, now_ms: int) -> dict[str, Any]:
        return DRV.derivatives_view(self.derivatives, symbol=V.SYMBOL, now_ms=now_ms)


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
    return V.display(collector_view=collector_view(now_ms),
                     structure_layer=sources.structure_layer(now_ms),
                     derivatives_layer=sources.derivatives_layer(now_ms),
                     now_ms=now_ms)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.crypto.btc_context_ui_v1")
    parser.add_argument("--root", default=os.environ.get(ROOT_ENV))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if args.root:
        os.environ[ROOT_ENV] = str(Path(args.root).expanduser().resolve())
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)
    return 0


__all__ = ["app", "state", "Sources", "collector_view", "main", "DEFAULT_PORT",
           "DERIVATIVES_MIN_INTERVAL_MS", "PUBLISH_WINDOW_MS"]
