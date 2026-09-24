"""E canonical universe CLI: export the base slice, build a session's universe, verify identity.

    export-base   (workstation) write the tail of the frozen daily panel the server needs
    build         (server) collect D-1 from Massive, then build and write the artifact
    verify        (workstation) prove a slice build equals a full-panel build for a session

The server path never reads Google Drive and never loads the frozen raw store: it collects the
Massive D-1 grouped daily, split list and CS reference into its own workspace, and reads the base
slice for the older window rows. Massive is used for the canonical universe only; E's realtime
premarket, RVOL, H5, R1, Top3 and paper execution stay on Kiwoom.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

from app.market.calendar import MarketCalendar
from app.strategy_e_max_rt import universe_build as UB

ET = ZoneInfo("America/New_York")


def log(message: str) -> None:
    print(f"[{datetime.now(ET):%Y-%m-%d %H:%M:%S} ET] {message}", flush=True)


def _root(args) -> Path:
    from app.backtest.workspace.discovery import resolve_workspace_root
    return resolve_workspace_root(args.workspace_root)


def _base(args, root: Path):
    from app.strategy_e_max_forward import forward_daily as FD
    if args.base:
        return UB.load_base_slice(args.base), f"slice {args.base.name}"
    return FD.load_base(root), "frozen panel"


def export_base(args) -> int:
    root = _root(args)
    summary = UB.export_base_slice(root, args.out, sessions=args.sessions)
    print(json.dumps(summary, indent=1))
    return 0


def _window_days(session: date, base, calendar: MarketCalendar) -> list[date]:
    """The eligibility window's sessions that the base slice does not already carry."""
    from app.strategy_e_v1_1 import context
    window, day = [], session
    for _ in range(context.REQUIRED_DAILY_SESSIONS):
        day = calendar.previous_trading_day(day)
        window.append(day)
    known = set(base.sessions)
    return sorted(d for d in window if d not in known)


def _collect(root: Path, session: date, base, calendar: MarketCalendar, *, spacing: float = 1.0) -> dict:
    """Fetch only the small per-session Massive files the build reads, and only the missing ones.

    The same publishing path the forward collector uses (``collect_grouped`` / ``collect_splits`` /
    ``collect_reference``, each with its validation sidecar). The collector's own ``collect`` command
    is not used here because it plans minute work from the frozen daily panel, which a server that
    carries the base slice does not have.
    """
    from app.core.config import get_settings
    from app.integrations.kiwoom.rate_limit import RequestRateLimiter
    from app.integrations.massive.client import MassiveAggregatesClient
    from app.strategy_e_max_forward import collect as C, storage as ST
    key = get_settings().massive_api_key
    if key is None or not key.get_secret_value().strip():
        raise RuntimeError("MASSIVE_API_KEY is not configured on this host")
    previous = calendar.previous_trading_day(session)
    days = [d for d in _window_days(session, base, calendar) if d <= previous]
    grouped = [d for d in days if not ST.verified(ST.grouped_path(root, d))]
    splits = [previous] if not ST.verified(ST.splits_asof_path(root, previous)) else []
    reference = [] if _has_reference(root, previous) else [previous]
    if not (grouped or splits or reference):
        return {"grouped": 0, "splits": 0, "reference": 0, "note": "inputs already present"}
    client = MassiveAggregatesClient(key, limiter=RequestRateLimiter(1.0 / spacing))
    run = C.new_run(root, log)
    for kind, fn, items in (("grouped", C.collect_grouped, grouped), ("splits", C.collect_splits, splits),
                            ("reference", C.collect_reference, reference)):
        for day in items:
            outcome = C.with_retries(run, f"{kind} {day}", lambda fn=fn, day=day: fn(run, client, day))
            log(f"{kind} {day}: {outcome}")
    return {"grouped": len(grouped), "splits": len(splits), "reference": len(reference)}


def _has_reference(root: Path, until: date) -> bool:
    from app.strategy_e_max_forward import storage as ST
    directory = root / ST.ROOT / "reference"
    return any(path.name[3:13] <= until.isoformat() and ST.verified(path)
               for path in (sorted(directory.glob("CS_*.json.gz")) if directory.exists() else ()))


def build(args) -> int:
    cal = MarketCalendar("America/New_York")
    session = args.session or datetime.now(ET).date()
    if cal.session(session) is None:
        log(f"{session} is not a trading session; nothing to build")
        return 0
    out = args.out if args.out.suffix == ".json" else args.out / f"universe_{session.isoformat()}.json"
    if out.exists() and not args.force:
        payload = UB.load(out)
        ok, why = UB.d_minus_1_identity(payload, session, cal)
        if ok:
            log(f"{out.name} already present and D-1 identity holds ({payload['symbol_count']} symbols)")
            return 0
        log(f"{out.name} exists but is not usable ({why}); rebuilding")
    if args.base and not args.base.exists():
        # Not transient: the base slice is exported once from the frozen panel on the workstation.
        log(f"BASE_SLICE_MISSING {args.base}: export it with "
            f"'run_e_universe export-base --out {args.base.name}' and stage it here. "
            f"E will record NO_DECISION (UNIVERSE_NOT_AVAILABLE); Strategy A keeps running.")
        return 1
    root = _root(args)
    previous = cal.previous_trading_day(session)
    deadline = time.monotonic() + args.max_seconds
    attempt = 0
    while True:
        attempt += 1
        try:
            base, origin = _base(args, root)
            if not args.no_collect:
                log(f"attempt {attempt}: collecting the Massive inputs this build reads (as of {previous})")
                log(f"collected {_collect(root, session, base, cal)}")
            payload = UB.build(session, base=base, root=root, calendar=cal)
            UB.write(payload, out)
            log(f"universe {payload['symbol_count']} symbols, asof {payload['asof_session']}, "
                f"digest {payload['digest'][:12]} ({origin}) -> {out}")
            for extra in args.also or []:
                UB.write(payload, extra / out.name)
                log(f"copied to {extra / out.name}")
            return 0
        except Exception as error:
            log(f"attempt {attempt} failed: {type(error).__name__}: {error}")
            if time.monotonic() + args.retry_seconds > deadline:
                log("giving up; E will record NO_DECISION (UNIVERSE_NOT_AVAILABLE) and A keeps running")
                return 1
            time.sleep(args.retry_seconds)


def verify(args) -> int:
    """A slice build must equal a full-panel build, field by field and by digest."""
    from app.strategy_e_max_forward import forward_daily as FD
    cal = MarketCalendar("America/New_York")
    root = _root(args)
    results = []
    for session in args.sessions_list:
        full = UB.build(session, base=FD.load_base(root), root=root, calendar=cal)
        sliced = UB.build(session, base=UB.load_base_slice(args.base), root=root, calendar=cal)
        same, problems = UB.identical(full, sliced)
        results.append({"session": session.isoformat(), "identical": same, "differing_fields": problems,
                        "symbols": full["symbol_count"], "digest": full["digest"]})
        log(f"{session}: {'MATCH' if same else 'MISMATCH ' + ','.join(problems)} "
            f"({full['symbol_count']} symbols, {full['digest'][:12]})")
    print(json.dumps(results, indent=1))
    return 0 if all(r["identical"] for r in results) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="E canonical universe builder")
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--base", type=Path, help="base slice (.npz); omit to use the frozen panel")
    sub = parser.add_subparsers(dest="command", required=True)
    e = sub.add_parser("export-base")
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--sessions", type=int, default=60)
    e.set_defaults(func=export_base)
    b = sub.add_parser("build")
    b.add_argument("--session", type=date.fromisoformat)
    b.add_argument("--out", type=Path, required=True, help="a .json path or a directory")
    b.add_argument("--also", type=Path, action="append", help="extra directory to copy the artifact into")
    b.add_argument("--no-collect", action="store_true")
    b.add_argument("--force", action="store_true")
    b.add_argument("--max-seconds", type=float, default=7200.0)
    b.add_argument("--retry-seconds", type=float, default=900.0)
    b.set_defaults(func=build)
    v = sub.add_parser("verify")
    v.add_argument("--session", dest="sessions_list", type=date.fromisoformat, action="append", required=True)
    v.set_defaults(func=verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
