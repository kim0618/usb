"""A-MOVER-LIVE-V1 in its own process (E-RUNTIME-R1, 2026-10-16).

    PYTHONPATH=backend python -m app.dev.run_a_mover_live run --out <run dir>

Until 2026-10-16 A's mover cut ran inside E's paper worker (``attach_isolated`` + ``run_cut``) and
E waited for the whole of it - cut, scan, GPT handoff - before its own 09:25 finalization. This
worker runs the same A code (``app.strategy_a_mover_live.integration``, unchanged: the same union,
the same 09:15 cut, the same pass, snapshot, baseline, scan and handoff) in a process of its own:

* 04:00 - 09:15 it rolls A's union on the minute API at A's share of the key;
* 09:15 it runs A's cut through ``IsolatedAttach.run_cut`` exactly as E's worker did;
* the minute API is A's alone 09:15 - 09:20:40 and the tick API until 09:25:00, after which this
  process sends no Kiwoom request at all (``lane_share``). Its scan and handoff after the cut make
  no Kiwoom call, so E's 09:25 finalization has both lanes to itself.

E's worker never reads anything this worker writes and this worker never reads E's runtime state.
A disabled flag (``A_MOVER_LIVE_ENABLED`` off) makes the run a no-op that touches no file.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import resource
import time


def now():
    from app.integrations.kiwoom.lane_share import ET
    return datetime.now(ET)


def log(text: str) -> None:
    print(f"[{now().strftime('%H:%M:%S')} ET] {text}", flush=True)


def _write(path: Path, body: dict) -> None:
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(body, indent=1, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def standalone_handle(handle, hash_lanes) -> None:
    """Turn the shared-collector handle into A's own: every union symbol is A's cache.

    In E's worker the handle held only A's *extra* symbols and borrowed E's caches and E's tick
    shard. Here there is nothing to borrow, so A's caches become the primary dictionary and A's
    own outcome-free hash split gives the tick lane its half - the same split E uses for its own.
    """
    handle.caches = dict(handle.extra_caches)
    handle.extra_caches = {}
    minute, tick = hash_lanes(sorted(handle.caches))
    handle.shard_minute, handle.shard_tick = tuple(minute), tuple(tick)


def run(out_root: Path, *, environ=os.environ, repo: Path | None = None) -> int:
    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.integrations.kiwoom import lane_share as SHARE
    from app.integrations.kiwoom.auth import KiwoomAuthClient
    from app.integrations.kiwoom.rate_limit import KiwoomRateLimits
    from app.market.calendar import MarketCalendar
    from app.strategy_a_mover_live import config as A_CFG, integration as A_LIVE
    from app.strategy_e_max_rt import finalizer as FZ

    if not A_CFG.enabled(environ):
        log("A_MOVER_LIVE_ENABLED is off: nothing to do")
        return 0
    cal = MarketCalendar("America/New_York")
    session = now().date()
    if cal.session(session) is None:
        log(f"{session}: not a trading session")
        return 0
    at = lambda t: datetime.combine(session, t, tzinfo=SHARE.ET)  # noqa: E731
    run_dir = out_root / session.isoformat()
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = open(run_dir / "a_mover_live.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another A mover worker holds this session's lock")
    report_path = run_dir / "a_mover_live.json"
    if report_path.exists():
        log("a_mover_live.json exists for this session; nothing to do")
        return 0
    report = {"session": session.isoformat(), "worker": "A_MOVER_LIVE_STANDALONE", "runtime_version": "E-RUNTIME-R1",
              "started_at": now().isoformat(), "pid": os.getpid(),
              "lane_share": {"minute": [[a.isoformat(), b.isoformat(), r] for a, b, r in SHARE.A_MINUTE],
                             "tick": [[a.isoformat(), b.isoformat(), r] for a, b, r in SHARE.A_TICK],
                             "kiwoom_hard_stop": SHARE.E_FINALIZE.isoformat()}}
    if now() >= at(SHARE.A_CUT):
        report["status"] = "RESTARTED_AFTER_A_CUT"
        _write(report_path, report)
        log("started after 09:15 ET: no cut")
        return 0

    s = get_settings()
    auth = KiwoomAuthClient(base_url=s.kiwoom_base_url, app_key=s.kiwoom_app_key.get_secret_value(),
                            app_secret=s.kiwoom_app_secret.get_secret_value(), limiter=KiwoomRateLimits().auth)
    clients = []
    for windows, hold in ((SHARE.A_MINUTE, SHARE.E_FINALIZE), (SHARE.A_TICK, None)):
        limits = KiwoomRateLimits()
        limits.chart = SHARE.ScheduledRateLimiter(session, windows, clock=now, hold_until=hold)
        clients.append(FZ.ChartLaneClient(base_url=s.kiwoom_base_url, auth=auth, rate_limits=limits, max_retries=1))
    lane_minute, lane_tick = FZ.Lane("A_MINUTE", FZ.MINUTE_API, clients[0]), FZ.Lane("A_TICK", FZ.TICK_API, clients[1])
    exch: dict[str, str] = {}
    for ex in ("ND", "NY", "NA"):
        for row in clients[0]._collect("usa10099", "/api/us/stkinfo", {"stex_tp": ex}, row_key="list", max_pages=50):  # noqa: SLF001
            exch.setdefault(str(row["stk_cd"]).strip(), ex)
    repo_root = repo or Path(__file__).resolve().parents[3]
    a_live = A_LIVE.attach_isolated(session=session, repo=repo_root, caches={}, shard_minute=(), shard_tick=(),
                                    lane_minute=lane_minute, lane_tick=lane_tick, now=now, exchanges=exch,
                                    log=log, session_factory=SessionLocal, calendar=cal, environ=dict(environ))
    if a_live.handle is None:
        report |= {"status": "ATTACH_REFUSED", "attach": getattr(a_live, "status", None)}
        _write(report_path, report)
        log("A attach refused; recorded")
        return 0
    standalone_handle(a_live.handle, FZ.hash_lanes)
    handle = a_live.handle
    report["union"] = {"symbols": len(handle.caches), "shard_minute": len(handle.shard_minute),
                       "shard_tick": len(handle.shard_tick), "unmapped": len(handle.unmapped)}
    while now() < at(SHARE.ROLLING_START) + timedelta(seconds=5):
        time.sleep(5)
    report["rolling_started_at"] = now().isoformat()
    order = [handle.caches[name] for name in handle.pass_order() if name in handle.caches]
    cycles, errors = 0, 0
    while now() < at(SHARE.A_CUT):
        cycles += 1
        for cache in order:
            if now() >= at(SHARE.A_CUT):
                break
            try:
                FZ.refresh(lane_minute, cache, session, now)
            except Exception as error:
                errors += 1
                cache.error = type(error).__name__
            else:
                cache.error = None
        log(f"A rolling cycle {cycles}: minute calls {lane_minute.calls} errors {errors}")
    report["rolling"] = {"cycles": cycles, "minute_calls": lane_minute.calls, "errors": errors,
                         "ended_at": now().isoformat()}
    report["a_mover_live"] = a_live.run_cut()
    report["lane_calls"] = {"minute": lane_minute.calls, "tick": lane_tick.calls,
                            "minute_errors": lane_minute.errors, "tick_errors": lane_tick.errors}
    report["finished_at"] = now().isoformat()
    report["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    report["status"] = "COMPLETE"
    _write(report_path, report)
    log(f"A cut done: {report['a_mover_live'].get('a_pass', {}).get('usable')} usable; "
        f"handoff {report['a_mover_live'].get('handoff', {}).get('status')}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A-MOVER-LIVE-V1 standalone worker")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
