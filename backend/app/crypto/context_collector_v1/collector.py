"""The V0 collector, unchanged, with the context step appended to each sample.

`ContextCollector` subclasses `market_structure_v0.collector.Collector` and overrides nothing that
decides a market value. Every depth frame, trade, resnapshot, staleness check, wall candidate and
continuity proof runs through the parent exactly as it does in a research run; the persistence
policy lives in `ContextStore` and the parent is never told that most of its records go nowhere.
The one thing added is what happens *after* the parent's `sample()` has written the compact state
file: the context engine reads that file through the viewer's own path and the result is emitted
as `wall_v2` and `context` records in the same envelope sequence, so a reader can merge the two on
`seq` exactly as Market Context R0 merged `wall` rows into `derived` samples.

`ContextRunner` subclasses the V0 runner for one reason. The V0 sampler stops the run on
`StoreAuthorityLost` and on nothing else, so any other error from `store.tick` - a full disk on
the once-a-second flush - ends the sampler task without ending the process. V0 still stops within
a minute, because raw depth fills the persist buffer and the persist worker fails on the next
write; with the raw kinds dropped that buffer takes the better part of an hour to fill, so here a
dead sampler would leave a live process publishing nothing. The override stops the run instead,
with the error as the stop reason.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..market_structure_v0 import collector as V0
from ..market_structure_v0.envelope import Session, now_ms, now_ns
from ..market_structure_v0.store import StoreAuthorityLost, StoreLocked
from . import COLLECTOR_VERSION, CONTRACT_SHA256, VERSION
from .context import ContextEngine
from .contract import (CONTEXT_KIND, DISPLAY_MIN_NOTIONAL_USDT, DISPLAY_WALL_LIMIT,
                       DROPPED_V0_KINDS, FEED_STALE, FEED_UNKNOWN, PERSISTED_V0_KINDS,
                       STATE_CACHE_ENV, SUPPORTED_SYMBOL, WALL_R0_KIND, WALL_ROLES,
                       WALL_V2_KIND)
from .store import ContextStore, StorageFailed
from .wallr0 import OpenRowFilter

#: The output root. Read here and in the API, nowhere else.
ROOT_ENV = "CTX_V1_ROOT"
#: The context step failed for this sample. The record is still written, as UNKNOWN.
COMPUTE_ERROR = "CONTEXT_COMPUTE_ERROR"


class UnsupportedSymbol(ValueError):
    """CONTRACT_CTX_V1_1 section 5: this collector exists for BTCUSDT and nothing else."""


def require_btc(symbol: str) -> str:
    """Refuse every symbol but BTCUSDT, before a lock, a socket or a file is touched."""
    if symbol != SUPPORTED_SYMBOL:
        raise UnsupportedSymbol(
            f"{symbol!r} is not supported: the context collector is BTCUSDT only (the V0 "
            f"stream URLs, the wall rule and every validated layer are BTC). ETH and SOL manual "
            f"trading is a separate system and is not served by this collector.")
    return symbol


@dataclass
class ContextCollector(V0.Collector):
    """`Collector` plus the context step. See the module docstring for what is not overridden."""

    engine: ContextEngine | None = None
    context_errors: int = 0
    context_last_error: str | None = None
    #: R0's slice of the V0 wall rows. None turns the `wall_r0` stream off.
    r0_rows: OpenRowFilter | None = field(default_factory=OpenRowFilter)
    #: Envelope `seq` of the newest V0 `derived` record, which R0's merge rule is keyed on.
    last_derived_seq: int | None = None

    def emit(self, kind: str, payload: dict[str, Any], *, receive_ms: int | None = None,
             mono_ns: int | None = None, connection_id: str | None = None) -> None:
        """The parent's emit, then two notes taken on the way past. Nothing is changed or held.

        The V0 record is published first and unchanged; the store decides it is not persisted.
        """
        super().emit(kind, payload, receive_ms=receive_ms, mono_ns=mono_ns,
                     connection_id=connection_id)
        if kind == "derived":
            self.last_derived_seq = self.session.seq
        elif kind == "wall" and self.r0_rows is not None:
            row = self.r0_rows.admit(payload, self.session.seq)
            if row is not None:
                super().emit(WALL_R0_KIND, row, receive_ms=receive_ms, mono_ns=mono_ns)

    def sample(self, *, at_ns: int, at_ms: int) -> dict[str, Any]:
        payload = super().sample(at_ns=at_ns, at_ms=at_ms)
        if self.engine is None:
            return payload
        try:
            events, compact, latest = self.engine.step(
                now_ms=at_ms, session_started_ms=self.session.started_ms,
                sample_index=self.samples)
        except Exception as exc:  # noqa: BLE001 - a broken reading must be visible, not fatal
            self.context_errors += 1
            self.context_last_error = f"{type(exc).__name__}: {exc}"
            state = {"state": FEED_UNKNOWN, "reasons": [COMPUTE_ERROR, self.context_last_error],
                     "is_rollup_of_layers": False}
            events = []
            compact = {"record": "CTX_V1_SNAPSHOT", "schema": VERSION,
                       "symbol": SUPPORTED_SYMBOL, "sample_index": self.samples,
                       "sample_ms": at_ms, "collector": state}
            latest = {**compact, "written_ms": at_ms, "liquidity": None, "flow": None}
        for event in events:
            self.emit(WALL_V2_KIND, event, receive_ms=at_ms, mono_ns=at_ns)
        # Which V0 sample this reading is, by the envelope sequence R0's merge rule uses.
        compact["v0_derived_seq"] = self.last_derived_seq
        self.emit(CONTEXT_KIND, compact, receive_ms=at_ms, mono_ns=at_ns)
        self.store.write_latest(latest)
        return payload

    def emit_stats(self, *, at_ns: int, at_ms: int, queue_backlog: int = 0) -> dict[str, Any]:
        stats = self.store.stats(now_ns=at_ns, queue_backlog=queue_backlog, extra={
            "samples": self.samples,
            "records_published": self.records_published,
            "depth_stream": self.depth_stream.counters(),
            "trade_stream": self.trade_stream.counters(),
            "book": self.depth.counters(),
            "tape": self.tape.counters(),
            "flow": self.flow.counters(),
            "walls": self.wall.counters(),
            "context": None if self.engine is None else self.engine.counters(),
            "wall_r0": None if self.r0_rows is None else self.r0_rows.counters(),
            "context_errors": self.context_errors,
            "context_last_error": self.context_last_error,
            "cpu_s": round(time.process_time(), 3),
        })
        self.stats_emitted += 1
        self.emit("storage_stats", stats, receive_ms=at_ms, mono_ns=at_ns)
        return stats

    def finish(self, reason: str, *, at_ns: int, at_ms: int) -> dict[str, Any]:
        if self.engine is not None:
            for event in self.engine.close_all(sample_index=self.samples, sample_ms=at_ms):
                self.emit(WALL_V2_KIND, event, receive_ms=at_ms, mono_ns=at_ns)
        stats = super().finish(reason, at_ns=at_ns, at_ms=at_ms)
        if self.engine is not None:
            last = dict(self.engine.last_latest or {"record": "CTX_V1_SNAPSHOT",
                                                    "schema": VERSION,
                                                    "symbol": SUPPORTED_SYMBOL})
            last["written_ms"] = at_ms
            last["collector"] = {"state": FEED_STALE, "reasons": ["SESSION_ENDED", reason],
                                 "is_rollup_of_layers": False}
            last["session_ended"] = True
            self.store.write_latest(last)
        return stats


@dataclass
class ContextRunner(V0.Runner):
    """`Runner` whose sampler also stops the run on a storage failure."""

    storage_stops: int = 0

    async def _sampler(self) -> None:
        assert self._stop is not None
        next_sample = time.monotonic() + self.sample_interval_s
        next_stats = time.monotonic() + self.stats_interval_s
        while not self._stop.is_set():
            delay = max(0.0, next_sample - time.monotonic())
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                pass
            at_ms, at_ns_ = now_ms(), now_ns()
            self.collector.sample(at_ns=at_ns_, at_ms=at_ms)
            try:
                self.collector.store.tick(at_ns_, at_ms)
            except StoreAuthorityLost as exc:
                self._stop_reason = f"writer_authority_lost: {exc}"
                self._stop.set()
                return
            except (StorageFailed, OSError) as exc:
                # The journal can no longer be written. Serving context without a journal would
                # leave the forward record with a hole nobody is told about, so the run stops and
                # the state files go stale, which every reader already treats as STALE.
                self.storage_stops += 1
                self._stop_reason = f"storage_error: {exc}"
                self._stop.set()
                return
            next_sample += self.sample_interval_s
            if time.monotonic() >= next_stats:
                backlog = self._persist_queue.qsize() if self._persist_queue else 0
                self.collector.emit_stats(at_ns=at_ns_, at_ms=at_ms, queue_backlog=backlog)
                next_stats += self.stats_interval_s

    def config_view(self) -> dict[str, Any]:
        out = super().config_view()
        out.update({"context_version": VERSION, "context_collector_version": COLLECTOR_VERSION,
                    "persisted_v0_kinds": list(PERSISTED_V0_KINDS),
                    "dropped_v0_kinds": list(DROPPED_V0_KINDS),
                    "own_kinds": [CONTEXT_KIND, WALL_V2_KIND, WALL_R0_KIND],
                    "display_min_notional_usdt": DISPLAY_MIN_NOTIONAL_USDT,
                    "display_wall_limit": DISPLAY_WALL_LIMIT,
                    "shadow_bytes": getattr(self.collector.store, "shadow_bytes", False),
                    "contract_sha256": CONTRACT_SHA256, "symbol": SUPPORTED_SYMBOL,
                    "wall_roles": WALL_ROLES,
                    "state_cache": dict(getattr(self.collector.store, "state_cache", {}))})
        return out


def build(root: Path, *, duration_s: float, shadow_bytes: bool = False,
          session: Session | None = None, symbol: str = SUPPORTED_SYMBOL,
          state_cache_dir: Path | None = None) -> ContextRunner:
    """Open the store (which takes the writer lock) and wire the collector and runner."""
    require_btc(symbol)
    session = session or Session()
    store = ContextStore.open(root, session.session_id, started_ns=session.started_ns,
                              shadow_bytes=shadow_bytes, state_cache_dir=state_cache_dir)
    collector = ContextCollector(store=store, session=session, engine=ContextEngine(root=root))
    return ContextRunner(collector=collector, duration_s=duration_s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.crypto.context_collector_v1 collect",
        description="BTCUSDT Production Context Collector V1 (public market data only)")
    parser.add_argument("--root", default=os.environ.get(ROOT_ENV),
                        help=f"output directory (or {ROOT_ENV})")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="seconds to run; 0 runs until stopped")
    parser.add_argument("--symbol", default=SUPPORTED_SYMBOL,
                        help="must be BTCUSDT; anything else is refused before startup")
    parser.add_argument("--state-cache-dir", default=os.environ.get(STATE_CACHE_ENV),
                        help=f"volatile directory for the current-state cache (or "
                             f"{STATE_CACHE_ENV}); omitted keeps it on disk under the root")
    parser.add_argument("--shadow-bytes", action="store_true",
                        help="also serialize every dropped record to count what a research "
                             "collector would have written; measurement only")
    args = parser.parse_args(argv)
    try:
        require_btc(args.symbol)
    except UnsupportedSymbol as exc:
        print(json.dumps({"event": "REFUSED", "reason": "UNSUPPORTED_SYMBOL",
                          "symbol": args.symbol, "detail": str(exc)}, indent=2), flush=True)
        return 2
    if not args.root:
        parser.error(f"--root or {ROOT_ENV} is required")
    root = Path(args.root).expanduser().resolve()
    cache = None if not args.state_cache_dir else Path(args.state_cache_dir).expanduser()
    session = Session()
    try:
        runner = build(root, duration_s=args.duration, shadow_bytes=args.shadow_bytes,
                       session=session, symbol=args.symbol, state_cache_dir=cache)
    except StoreLocked as exc:
        print(json.dumps({"event": "REFUSED", "reason": "WRITER_LOCK_HELD", "root": str(root),
                          "detail": str(exc)}, indent=2), flush=True)
        return 3
    print(json.dumps({"event": "START", "session_id": session.session_id, "root": str(root),
                      "duration_s": args.duration, "version": VERSION, "pid": os.getpid(),
                      "shadow_bytes": args.shadow_bytes}, indent=2), flush=True)
    try:
        stats = asyncio.run(runner.run())
    except Exception:
        runner.collector.store.close()
        raise
    policy = stats.get("persistence_policy") or {}
    print(json.dumps({"event": "END", "session_id": session.session_id,
                      "stop_reason": stats.get("stop_reason"),
                      "authority_lost": stats.get("authority_lost"),
                      "elapsed_s": stats["elapsed_s"], "total_rows": stats["total_rows"],
                      "total_bytes": stats["total_bytes"],
                      "projected_bytes_per_day": stats["projected_bytes_per_day"],
                      "shadow_dropped_bytes_per_day": policy.get("shadow_dropped_bytes_per_day"),
                      "dropped_records": stats["dropped_records"],
                      "queue_backlog_max": stats["queue_backlog_max"]}, indent=2), flush=True)
    return 0 if not str(stats.get("stop_reason") or "").startswith("storage_error") else 4


__all__ = ["ContextCollector", "ContextRunner", "build", "main", "ROOT_ENV", "COMPUTE_ERROR",
           "UnsupportedSymbol", "require_btc"]
