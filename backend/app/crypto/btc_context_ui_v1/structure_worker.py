"""One STRUCTURE refresh, then exit. Run once a minute by a systemd timer.

    python -m app.crypto.btc_context_ui_v1.structure_worker --state-dir DIR

1. Load the checkpointed R1 engine from `DIR`, or build it from the venue if there is none (or it
   was written by other code): 30 days back, day-floored, R1's own full run.
2. Fetch the closed 1m bars after the last one processed (normally one) and step the engine over
   them. A bar that has not closed is never fetched.
3. Write `DIR/structure_snapshot.json` (atomic), then the checkpoint (atomic).

Nothing is written when there is no new closed minute. A venue failure writes nothing either: the
snapshot already on disk ages, the API reads it as stale, and the strip shows `--`. Two runs never
overlap: the second one finds the lock held and exits without touching anything.

Public Binance klines only. No key, no account, no order path is imported.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from ..market_context_v1 import contract as C
from ..market_context_v1 import structure as STR
from ..market_context_v1 import venue as VN
from . import structure_state as S

LOCK_NAME = ".structure_worker.lock"


def refresh(state_dir: Path, *, now_ms: int, opener: Any = None,
            anchor_days: int = S.ANCHOR_DAYS) -> dict[str, Any]:
    """One refresh under the caller's lock. Returns what happened, for the log line."""
    snapshot_path = state_dir / S.SNAPSHOT_NAME
    checkpoint_path = state_dir / S.CHECKPOINT_NAME
    target = STR.last_closed_open_ms(now_ms)
    engine = S.load_checkpoint(checkpoint_path)
    rebuilt = engine is None
    if engine is None:
        anchor = STR.default_anchor_ms(now_ms, anchor_days)
        arrays = S.fetch_arrays(S.SYMBOL, anchor, target + C.MINUTE_MS, opener=opener)
        if len(arrays["ts"]) == 0 or int(arrays["ts"][0]) != anchor:
            raise VN.VenueError("SERIES_DOES_NOT_START_AT_ANCHOR")
        engine = S.IncrementalEngine.build(S.bars_from_arrays(arrays), anchor_ms=anchor)
        stepped = engine.processed
    else:
        start = engine.last_open_ms + C.MINUTE_MS
        if start > target:
            return {"result": "UP_TO_DATE", "last_open_ms": engine.last_open_ms}
        tail = S.fetch_arrays(S.SYMBOL, start, target + C.MINUTE_MS, opener=opener)
        if len(tail["ts"]) == 0:
            return {"result": "NO_NEW_BAR", "last_open_ms": engine.last_open_ms}
        if int(tail["ts"][0]) != start:
            raise VN.VenueError("SERIES_TAIL_DOES_NOT_JOIN")
        stepped = engine.step_to(S.bars_from_arrays(S.concat_arrays(S.arrays_of(engine.b1), tail)))
    S.write_snapshot(snapshot_path, S.snapshot_payload(engine, written_ms=int(time.time() * 1000)))
    S.save_checkpoint(checkpoint_path, engine)
    return {"result": "REBUILT" if rebuilt else "STEPPED", "stepped": stepped,
            "last_open_ms": engine.last_open_ms, "series_rows": engine.processed}


def run_once(state_dir: Path, *, now_ms: int | None = None, opener: Any = None,
             anchor_days: int = S.ANCHOR_DAYS) -> dict[str, Any]:
    state_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(state_dir / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"result": "LOCKED"}
        try:
            return refresh(state_dir, now_ms=int(time.time() * 1000) if now_ms is None else now_ms,
                           opener=opener, anchor_days=anchor_days)
        except VN.VenueError as caught:
            return {"result": "VENUE_ERROR", "error": str(caught)}
        except S.StructureStateError as caught:
            # A series that does not extend the checkpoint: drop it, the next run rebuilds.
            (state_dir / S.CHECKPOINT_NAME).unlink(missing_ok=True)
            return {"result": "CHECKPOINT_DISCARDED", "error": str(caught)}
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.crypto.btc_context_ui_v1.structure_worker")
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args(argv)
    started = time.monotonic()
    outcome = run_once(Path(args.state_dir).expanduser().resolve())
    outcome["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    print(json.dumps(outcome, sort_keys=True), flush=True)
    return 0 if outcome["result"] in ("REBUILT", "STEPPED", "UP_TO_DATE", "NO_NEW_BAR",
                                      "LOCKED") else 1


if __name__ == "__main__":
    sys.exit(main())
