"""A's consumer of ``snapshot_0915``: the existing cut downstream, fed from the sealed file.

It runs in its own process from the A/E operations tree and makes **no Kiwoom call**: the
handle's two lanes refuse any request, and the only market input is the sealed snapshot. Neither
the producer nor E waits for it.

What it runs is A's existing post-acquisition code, in the order ``integration.run_cut`` runs it
after ``acquisition.run_a_pass`` returns - snapshots, raw persistence, forward observation,
baselines, features, the live scan through ``LiveRuntimeSource`` and the GPT handoff - through
the handle's own helpers and behind A's own failure boundary (``isolation.IsolatedAttach``), so A
official paper sees the same ``ScannerRun`` it sees today. :func:`complete_cut` mirrors those
lines; ``tests/ae_common/test_a_consumer.py`` pins them to ``integration.run_cut``.

Fail closed, never partial: the scan does not run when the snapshot is missing, does not verify,
belongs to another session, was planned over another A union, or leaves any A-union symbol
``NOT_REACHED`` / ``INTERRUPTED``. A per-symbol STALE or FAILED after a completed read is A's
existing per-symbol exclusion and is recorded, not hidden.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
import fcntl
import json
import os
from pathlib import Path
import resource
import time
from typing import Any
from zoneinfo import ZoneInfo

from app.ae_common import snapshot as SS

ET = ZoneInfo("America/New_York")
#: How long the consumer waits for the producer's seal. A's review window runs to 10:30 and entry
#: opens at 09:45; a snapshot later than this is not used.
WAIT_UNTIL = dtime(9, 40)
REASON_UNAVAILABLE = "COMMON_SNAPSHOT_UNAVAILABLE"
REASON_INVALID = "COMMON_SNAPSHOT_INVALID"
REASON_UNIVERSE = "COMMON_SNAPSHOT_UNIVERSE_MISMATCH"
REASON_INCOMPLETE = "COMMON_SNAPSHOT_INCOMPLETE"


def now() -> datetime:
    return datetime.now(ET)


def log(text: str) -> None:
    print(f"[{now().strftime('%H:%M:%S')} ET] {text}", flush=True)


class NoKiwoomClient:
    """The consumer's lanes. Any request is a defect and is refused before it can be sent."""

    def __init__(self) -> None:
        self.attempts = 0

    def request(self, *args: Any, **kwargs: Any):
        self.attempts += 1
        raise RuntimeError("the A snapshot consumer makes no Kiwoom request")


@dataclass
class SnapshotCut:
    """``IsolatedAttach`` calls ``handle.run_cut()``; this is that handle for a sealed snapshot."""

    handle: Any
    report: Any
    observed: datetime

    def run_cut(self) -> dict[str, Any]:
        return complete_cut(self.handle, self.report, self.observed)


def complete_cut(handle: Any, passed: Any, observed: datetime) -> dict[str, Any]:
    """``integration.run_cut`` after ``run_a_pass``, with ``passed`` and the caches from the file."""
    from app.services.mover_scanner_source import LiveRuntimeSource
    from app.strategy_a_mover_live import acquisition as AQ
    from app.strategy_a_mover_live import baseline as B
    from app.strategy_a_mover_live import contract as LC
    from app.strategy_a_mover_live import features as FEAT
    from app.strategy_a_mover_live import gpt_handoff as GPT
    from app.strategy_a_mover_live import isolation as ISO
    from app.strategy_a_mover_live import raw_store as RAW
    from app.strategy_a_mover_live import scanner as SCAN
    from app.strategy_a_mover_live.schedule import SharedSchedule

    schedule = SharedSchedule(handle.session)
    schedule.validate()
    caches = handle.all_caches()
    handle.log(f"A cut: {len(passed.usable)}/{len(passed.outcomes)} usable, "
               f"T1 {passed.t1.time() if passed.t1 else None}")
    snapshots = AQ.snapshots_from(caches, passed, session=handle.session, observed_at=observed)
    raw = AQ.raw_session_from(caches, passed, session=handle.session, observed_at=observed)
    raw_path = RAW.write_session(handle.repo, raw)
    body: dict[str, Any] = {
        "contract": LC.current().metadata(),
        "schedule": schedule.declaration(),
        "a_pass": passed.declaration(schedule),
        "raw_persistence": {"path": str(raw_path), "bars": len(raw), "symbols": len(raw.symbols()),
                            "accepted": raw.accepted, "deduped": raw.deduped,
                            "quarantined": len(raw.quarantine)},
        "universe": handle.union.declaration(),
        "unmapped_union_symbols": len(handle.unmapped),
        "snapshots": len(snapshots),
    }
    # what the scan below is fed, per symbol, for the shadow gate's comparison with the producer
    body["snapshot_row_digests"] = SS.row_digests({s: snap.row() for s, snap in snapshots.items()})
    body["forward_observation"] = handle._record_forward(snapshots, observed)  # noqa: SLF001
    baselines = handle._baselines(snapshots)  # noqa: SLF001
    if hasattr(FEAT, "baseline_readiness"):        # present where integration.run_cut records it
        body["baseline_readiness"] = FEAT.baseline_readiness(
            handle.session, handle.union.a_symbols, baselines, snapshots=snapshots)
    body["baseline"] = {
        "identity": B.BASELINE_IDENTITY,
        "rule": handle.baseline_rule.declaration(),
        "requested": len(snapshots),
        "available": sum(1 for item in baselines.values() if item.available),
        "database_available": handle.session_factory is not None,
        "provider_mix": B.provider_mix(baselines, rule=handle.baseline_rule),
    }
    try:
        panels = FEAT.assemble(handle.repo, handle.session, snapshots, baselines, handle.union)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        failure = ISO.classify(error, phase=ISO.Phase.CUT)
        if not failure.expected:
            raise
        body["scan"] = {"status": str(failure.refusal), "reason": str(failure.reason),
                        "detail": failure.detail, "error": failure.error, "gpt_calls": 0, "candidates": 0}
        handle.report = body
        return body
    body["features"] = panels.declaration()
    body["live_feed"] = {"registered_as": FEAT.register(panels),
                         "source": "mover_scanner_source.LIVE_PREMARKET_FEEDS"}
    try:
        live = SCAN.run_from_source(LiveRuntimeSource(), handle.session, observed_at=observed)
    except SCAN.LiveScanRefused as refusal:
        body["scan"] = {"status": str(refusal.refusal),
                        "reason": str(ISO.classify(refusal, phase=ISO.Phase.CUT).reason),
                        "detail": refusal.detail, "gpt_calls": 0, "candidates": 0}
        handle.report = body
        return body
    body["scan"] = live.metadata() | {"candidates": live.candidate_count}
    body["candidates"] = GPT.candidate_metadata(live)
    body["handoff"] = handle._handoff(live)  # noqa: SLF001
    handle.report = body
    return body


def coverage(body: Mapping[str, Any], a_symbols: tuple[str, ...], unmapped: tuple[str, ...]) -> dict[str, Any]:
    """Whether the sealed file covers exactly the union A plans today, with nothing unfinished."""
    summary = SS.summary_0915(body)
    same_union = sorted(a_symbols) == list(body["a_symbols"])
    same_unmapped = sorted(unmapped) == list(body["unmapped"])
    complete = bool(summary["complete_for_a"]) and same_union and same_unmapped
    return summary | {"a_union_matches": same_union, "unmapped_matches": same_unmapped,
                      "complete": complete,
                      "reason": None if complete else (REASON_UNIVERSE if not (same_union and same_unmapped)
                                                       else REASON_INCOMPLETE)}


def _failure(reason: str, detail: str):
    from app.strategy_a_mover_live import config as CFG
    from app.strategy_a_mover_live import isolation as ISO
    return ISO.Failure(ISO.Phase.CUT, CFG.Refusal.SCANNER_NOT_RUN, reason, detail, True, detail)


def wait_for(directory: Path, deadline: datetime, *, clock: Callable[[], datetime] = now,
             sleeper: Callable[[float], None] = time.sleep, poll: float = 2.0) -> bool:
    while not SS.exists(directory, SS.SNAPSHOT_0915):
        if clock() >= deadline:
            return False
        sleeper(poll)
    return True


def run(snapshot_root: Path, out_root: Path, *, repo: Path | None = None, environ: Mapping[str, str] = os.environ,
        session_factory: Any = None, calendar: Any = None, clock: Callable[[], datetime] = now,
        sleeper: Callable[[float], None] = time.sleep, wait_until: dtime = WAIT_UNTIL) -> dict[str, Any]:
    from app.market.calendar import MarketCalendar
    from app.strategy_a_mover_live import config as CFG
    from app.strategy_a_mover_live import integration as A_LIVE
    from app.strategy_a_mover_live import isolation as ISO
    from app.strategy_e_max_rt import finalizer as FZ

    if not CFG.enabled(dict(environ)):
        log("A_MOVER_LIVE_ENABLED is off: nothing to do")
        return {"status": "DISABLED"}
    calendar = calendar or MarketCalendar("America/New_York")
    session = clock().date()
    if calendar.session(session) is None:
        return {"status": "NOT_A_SESSION", "session": session.isoformat()}
    repo = Path(repo or Path(__file__).resolve().parents[3])
    run_dir = Path(out_root) / session.isoformat()
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = open(run_dir / "a_consumer.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another A snapshot consumer holds this session's lock")
    report_path = run_dir / "a_consumer.json"
    if report_path.exists():
        log("a_consumer.json exists for this session; nothing to do")
        return json.loads(report_path.read_text(encoding="utf-8"))
    snapshots_dir = Path(snapshot_root) / session.isoformat()
    report: dict[str, Any] = {"session": session.isoformat(), "worker": "A_SNAPSHOT_CONSUMER",
                              "started_at": clock().isoformat(), "pid": os.getpid(),
                              "snapshot_dir": str(snapshots_dir), "kiwoom_requests": 0}
    boundary = ISO.IsolatedAttach(active=True, handle=None, session=session, repo=repo, log=log)

    def finish(status: str, body: dict[str, Any]) -> dict[str, Any]:
        report.update(body)
        report["status"] = status
        report["finished_at"] = clock().isoformat()
        report["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        temporary = report_path.with_name(report_path.name + ".partial")
        temporary.write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
        os.replace(temporary, report_path)
        log(f"A consumer {status}")
        return report

    deadline = datetime.combine(session, wait_until, tzinfo=ET)
    if not wait_for(snapshots_dir, deadline, clock=clock, sleeper=sleeper):
        return finish("FAILED_CLOSED", {"a_mover_live": boundary.fail(_failure(
            REASON_UNAVAILABLE, f"snapshot_0915 not sealed by {deadline.isoformat()}"))})
    report["snapshot_seen_at"] = clock().isoformat()
    try:
        sealed = SS.load(snapshots_dir, SS.SNAPSHOT_0915, fmt=SS.FORMAT_0915, session=session)
    except SS.SnapshotInvalid as error:
        return finish("FAILED_CLOSED", {"a_mover_live": boundary.fail(_failure(REASON_INVALID, str(error)))})
    body = sealed.body
    report["snapshot_manifest"] = dict(sealed.manifest)
    if session_factory is None:
        from app.core.database import SessionLocal
        session_factory = SessionLocal
    client = NoKiwoomClient()
    lane_minute, lane_tick = FZ.Lane("A", FZ.MINUTE_API, client), FZ.Lane("B", FZ.TICK_API, client)  # type: ignore[arg-type]
    attached = A_LIVE.attach_isolated(session=session, repo=repo, caches={}, shard_minute=(), shard_tick=(),
                                      lane_minute=lane_minute, lane_tick=lane_tick, now=clock,
                                      exchanges=body["exchanges"], log=log, session_factory=session_factory,
                                      calendar=calendar, environ=dict(environ))
    if attached.handle is None:
        return finish("ATTACH_REFUSED", {"a_mover_live": attached.run_cut()})
    handle = attached.handle
    cover = coverage(body, tuple(handle.union.a_symbols), tuple(handle.unmapped))
    report["coverage"] = cover
    if not cover["complete"]:
        detail = (f"A union {len(handle.union.a_symbols)} vs file {len(body['a_symbols'])}, unmapped "
                  f"{len(handle.unmapped)} vs {len(body['unmapped'])}, statuses {cover['a_status_counts']}")
        return finish("FAILED_CLOSED", {"a_mover_live": boundary.fail(_failure(cover["reason"], detail))})
    caches, passed, observed = SS.a_caches_from(body)
    handle.extra_caches = caches            # every A pass symbol, rebuilt; ``caches`` stays empty
    attached.handle = SnapshotCut(handle, passed, observed)
    result = attached.run_cut()
    report["kiwoom_requests"] = client.attempts
    return finish("COMPLETE" if client.attempts == 0 else "DEFECT_KIWOOM_REQUEST", {"a_mover_live": result})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A-MOVER-LIVE-V1 consumer of the common snapshot_0915")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--snapshots", type=Path, required=True, help="producer snapshot root (<root>/<session>/)")
    r.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    run(args.snapshots, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
