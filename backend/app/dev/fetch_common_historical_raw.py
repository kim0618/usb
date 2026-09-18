"""Fetch only the missing minute / per-symbol daily raw of MINUTE_UNIVERSE_V1 into the Common Raw store.

    PYTHONPATH=backend .venv/bin/python -m app.dev.fetch_common_historical_raw --plan-only
    PYTHONPATH=backend .venv/bin/python -m app.dev.fetch_common_historical_raw

Sessions already COMPLETE in the legacy collector manifest or in a Common Raw ledger are never
requested. Serial, oldest first, resumable. Holds the workspace writer lock while it runs.
Run it only when no other process is using the same Massive key.
"""

import argparse
from datetime import date, datetime, timezone
import os
from pathlib import Path
import time

from app.backtest.historical_store.coverage import existing_sessions, minute_universe_v1
from app.backtest.historical_store.raw_fetch import fetch_request, ordered, plan_requests
from app.backtest.workspace.discovery import resolve_workspace_root
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.lock import heartbeat, writer_lock
from app.core.config import get_settings
from app.dev.fetch_strategy_c_selection_raw import sessions_between
from app.integrations.kiwoom.rate_limit import RequestRateLimiter
from app.integrations.massive.client import MassiveAggregatesClient, MassiveConfigurationError, MassiveError
from app.market.calendar import MarketCalendar

AVG_BYTES = {"minute": 420_000, "per_symbol_daily": 9_000}  # gz per request, from the first pages


def build_plan(root: Path, start: date, end: date) -> tuple[list, dict]:
    calendar = MarketCalendar()
    sessions = sessions_between(calendar, start, end)
    universe = minute_universe_v1()
    symbols = universe["symbols"]
    plan, summary = [], {}
    for kind in ("per_symbol_daily", "minute"):
        legacy, common = existing_sessions(root, kind, sessions, symbols)
        have = {s: legacy[s] | common[s] for s in symbols}
        requests = plan_requests(kind, sessions, have, symbols)
        plan += requests
        summary[kind] = {"symbols": len(symbols), "expected_symbol_sessions": len(sessions) * len(symbols),
                         "legacy": sum(map(len, legacy.values())), "common": sum(map(len, common.values())),
                         "missing_symbol_sessions": sum(r.sessions for r in requests),
                         "requests": len(requests)}
    return ordered(plan), {"sessions": len(sessions), "range": [str(sessions[0]), str(sessions[-1])],
                           "universe": universe, "kinds": summary}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 9, 17))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 16))
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--spacing-seconds", type=float, default=13.0)
    parser.add_argument("--max-requests", type=int, default=None)
    args = parser.parse_args()
    root = resolve_workspace_root(args.workspace_root)
    plan, summary = build_plan(root, args.start, args.end)
    print(f"FETCH PLAN range={summary['range'][0]}..{summary['range'][1]} sessions={summary['sessions']} "
          f"symbols={len(summary['universe']['symbols'])}", flush=True)
    for kind, row in summary["kinds"].items():
        calls = row["requests"]
        print(f"  {kind}: expected={row['expected_symbol_sessions']} legacy={row['legacy']} "
              f"common={row['common']} missing={row['missing_symbol_sessions']} requests={calls} "
              f"est_minutes={calls * args.spacing_seconds / 60:.1f} "
              f"est_disk_mb={calls * AVG_BYTES[kind] / 1e6:.1f}", flush=True)
    if args.plan_only:
        for request in plan[:12]:
            print(f"  next {request}", flush=True)
        return
    key = get_settings().massive_api_key
    if key is None or not key.get_secret_value().strip():
        raise MassiveConfigurationError("MISSING_API_KEY", "MASSIVE_API_KEY is not configured")
    capture: list[bytes] = []
    client = MassiveAggregatesClient(key, limiter=RequestRateLimiter(1.0 / args.spacing_seconds),
                                     page_observer=capture.append)
    workspace = Workspace(root)
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731
    failures = 0
    with writer_lock(workspace, purpose=f"fetch_common_historical_raw pid={os.getpid()}") as lock:
        for number, request in enumerate(plan[:args.max_requests], start=1):
            try:
                outcome = fetch_request(client, capture, root, request, secret=key.get_secret_value(),
                                        log=lambda m: print(m, flush=True), sleeper=time.sleep, now=now)
            except MassiveError as error:
                failures += 1
                outcome = f"FAILED {getattr(error, 'code', 'MASSIVE_ERROR')}"
            lock = heartbeat(workspace, lock)
            print(f"[{number}/{len(plan)}] {request.kind} {request.symbol} {request.start}..{request.end} "
                  f"({request.sessions}) {outcome} http_total={client.accounting.http_requests} "
                  f"statuses={dict(client.accounting.status_codes)}", flush=True)
    print(f"done http_total={client.accounting.http_requests} statuses={dict(client.accounting.status_codes)} "
          f"retries={client.accounting.retries} failures={failures}", flush=True)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
