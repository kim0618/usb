"""E-MAX-F1 forward data collection (DATA COLLECTION ONLY; computes no return).

    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_max_f1 plan      [--start D] [--end D]
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_max_f1 collect   [--start D] [--end D]
                                                                                   [--max-minute N] [--spacing-seconds 13]
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_max_f1 verify
    PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e_max_f1 readiness [--end D]

Resume-safe and idempotent: a verified file is never fetched again, a corrupt one stops the run,
a file that fails validation is quarantined (kept) and fetched on the next run. Only closed sessions
(ET date ended) are requested. Order: reference -> grouped daily -> splits_asof -> D-1 universe ->
SPY, RVOL context, minute -> readiness.
"""

import argparse
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys

from app.backtest.workspace.discovery import resolve_workspace_root
from app.core.config import get_settings
from app.integrations.kiwoom.rate_limit import RequestRateLimiter
from app.integrations.massive.client import MassiveAggregatesClient, MassiveConfigurationError
from app.market.calendar import MarketCalendar
from app.strategy_e_max_forward import collect as C, collection_rules as CR, forward_daily as FD
from app.strategy_e_max_forward import readiness as RD, rules as F, storage as ST
from app.strategy_e_v1_1 import context

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCAL = REPO_ROOT / "data/runtime/strategy_e_max/forward/collection"
BOUNDARY_END = date(2026, 9, 16)


def log(text: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {text}", flush=True)


def _preflight() -> None:
    rules = F.load_rules()
    CR.load_rules()
    closure = F.provenance_closure(rules)
    if not closure["checks"]["pass"]:
        raise SystemExit(f"E-MAX-F1 BLOCKED — upstream integrity: {closure}")


def _sessions_before(day: date, n: int, calendar: MarketCalendar) -> list[date]:
    out, cur = [], day
    while len(out) < n:
        if calendar.session(cur) is not None:
            out.append(cur)
        cur -= timedelta(days=1)
    return sorted(out)


def build_plan(root: Path, start: date, end: date, today: date, calendar: MarketCalendar, *, need_universe: bool = True):
    closed, open_ = C.closed_sessions(start, end, today, calendar)
    plan = {"closed": closed, "not_closed": open_,
            "reference": C.reference_dates_due(closed, today, calendar),
            "grouped": [d for d in closed if not ST.verified(ST.grouped_path(root, d))],
            "splits": [d for d in closed if not ST.verified(ST.splits_asof_path(root, d))]}
    if need_universe:
        base = FD.load_base(root)
        universes, missing = {}, {}
        for d in closed:
            u, m = FD.eligible_universe(base, root, d, calendar)
            (universes.__setitem__(d, u) if u is not None else missing.__setitem__(d.isoformat(), m))
        symbols = sorted(set().union(*universes.values()) | {C.SPY}) if universes else [C.SPY]
        raw = RD._raw_minute_ranges(root)
        context_symbols = [s for s in symbols if s not in raw]
        context_sessions = _sessions_before(BOUNDARY_END, C.RVOL_CONTEXT_SESSIONS, calendar)
        plan.update(universes=universes, universe_missing=missing, symbols=symbols,
                    context=C.minute_requests(root, context_symbols, context_sessions, calendar, tree=ST.RVOL_CONTEXT),
                    minute=C.minute_requests(root, symbols, closed, calendar))
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E-MAX-F1 forward data collection")
    parser.add_argument("command", choices=("plan", "collect", "verify", "readiness"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 9, 17))
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--max-minute", type=int, default=None)
    parser.add_argument("--spacing-seconds", type=float, default=C.DEFAULT_SPACING_SECONDS)
    parser.add_argument("--workspace-root", type=Path, default=None)
    args = parser.parse_args(argv)
    _preflight()
    calendar = MarketCalendar("America/New_York")
    today = datetime.now(context.ET).date()
    end = args.end or today
    root = resolve_workspace_root(args.workspace_root)

    if args.command == "verify":
        bad, good = [], 0
        for side in (root / ST.ROOT).rglob("*.validation.json"):
            body = json.loads(side.read_text(encoding="utf-8"))
            target = side.with_name(side.name[:-len(".validation.json")])
            if not target.exists():
                target = side.with_name(side.name[:-len(".validation.json")] + ".request.json")
            ok = target.exists() and body.get("sha256") == ST.sha256_file(target)
            for f in body.get("files", []):
                page = side.with_name(f["file"])
                ok = ok and page.exists() and ST.sha256_file(page) == f["sha256"]
            good += ok
            if not ok:
                bad.append(str(side.relative_to(root)))
        print(json.dumps({"verified": good, "corrupt": bad}, indent=1))
        return 1 if bad else 0

    if args.command == "readiness":
        sessions = RD.forward_sessions(end + timedelta(days=1), calendar)
        inventory, notes = RD.probe(root, calendar=calendar, sessions=sessions)
        rows = RD.matrix(sessions, inventory, today_et=today, calendar=calendar)
        payload = {"today_et": today.isoformat(), "inventory": notes, "sessions": rows, "summary": RD.summary(rows)}
        (LOCAL / "readiness").mkdir(parents=True, exist_ok=True)
        path = LOCAL / "readiness" / f"readiness_{today.isoformat()}_{datetime.now().strftime('%H%M%S')}.json"
        path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"summary": payload["summary"], "inventory": {k: v for k, v in notes.items()}}, indent=1))
        for row in rows:
            print(row["session"], row["state"], "|", " ; ".join(f"{k}: {v}" for k, v in row["reasons"].items()))
        print(f"written {path}")
        return 0

    if args.command == "plan":
        plan = build_plan(root, args.start, end, today, calendar)
        print(json.dumps({"closed": [str(d) for d in plan["closed"]], "not_closed": [str(d) for d in plan["not_closed"]],
                          "reference": [str(d) for d in plan["reference"]], "grouped": [str(d) for d in plan["grouped"]],
                          "splits": [str(d) for d in plan["splits"]],
                          "universe": {str(d): len(u) for d, u in plan["universes"].items()},
                          "universe_missing": plan["universe_missing"], "symbols": len(plan["symbols"]),
                          "rvol_context_requests": [f"{r.symbol} {r.start}..{r.end}" for r in plan["context"]],
                          "minute_requests": len(plan["minute"]),
                          "estimated_hours": round((len(plan["minute"]) + len(plan["context"])) * args.spacing_seconds / 3600, 2)},
                         indent=1))
        return 0

    # collect
    key = get_settings().massive_api_key
    if key is None or not key.get_secret_value().strip():
        raise MassiveConfigurationError("MISSING_API_KEY", "MASSIVE_API_KEY is not configured")
    handle = C.process_lock(LOCAL / "collector.lock")  # noqa: F841 - held until exit
    capture: list[bytes] = []
    client = MassiveAggregatesClient(key, limiter=RequestRateLimiter(1.0 / args.spacing_seconds),
                                     page_observer=capture.append)
    run = C.new_run(root, log)
    log(f"run {run.run_id} today_et={today} spacing={args.spacing_seconds}s")
    status = "COMPLETE"
    first = None
    try:
        first = build_plan(root, args.start, end, today, calendar, need_universe=False)
        small = ([("reference", d) for d in first["reference"]] + [("grouped", d) for d in first["closed"]]
                 + [("splits", d) for d in first["closed"]])
        for kind, day in C.locked(root, small, log):
            fn = {"reference": C.collect_reference, "grouped": C.collect_grouped, "splits": C.collect_splits}[kind]
            outcome = C.with_retries(run, f"{kind} {day}", lambda fn=fn, day=day: fn(run, client, day))
            log(f"{kind} {day}: {outcome}")
        plan = build_plan(root, args.start, end, today, calendar)
        log(f"universe {({str(d): len(u) for d, u in plan['universes'].items()})} missing {plan['universe_missing']}")
        spy_first = sorted(plan["minute"], key=lambda r: (r.symbol != C.SPY, r.symbol, r.start))
        queue = [("context", r) for r in plan["context"]] + [("minute", r) for r in spy_first]
        if args.max_minute is not None:
            queue = queue[:args.max_minute]
        log(f"minute queue {len(queue)} requests (~{len(queue) * args.spacing_seconds / 3600:.1f} h)")
        secret = key.get_secret_value()
        for number, (kind, request) in enumerate(C.locked(root, queue, log), start=1):
            outcome = C.with_retries(run, f"{kind} {request.symbol}", lambda request=request: C.collect_minute(
                run, client, capture, request, secret=secret, calendar=calendar))
            log(f"[{number}/{len(queue)}] {kind} {request.symbol} {request.start}..{request.end}: {outcome} "
                f"http={client.accounting.http_requests}")
        if args.max_minute is not None and args.max_minute < len(plan["context"]) + len(plan["minute"]):
            status = "PARTIAL_MAX_MINUTE"
    except KeyboardInterrupt:
        status = "INTERRUPTED"
    except Exception as error:  # the manifest must still record what was done
        status = f"FAILED {type(error).__name__}: {error}"
        raise
    finally:
        sessions = {"closed": [str(d) for d in first["closed"]], "not_closed": [str(d) for d in first["not_closed"]]} \
            if first else {}
        manifest = C.write_manifest(run, sessions=sessions,
                                    requested={"http_requests": client.accounting.http_requests,
                                               "status_codes": {str(k): v for k, v in client.accounting.status_codes.items()},
                                               "client_retries": client.accounting.retries},
                                    status=status, local_copy=LOCAL / "manifests")
        C.cleanup_staging(run)
        log(f"manifest {manifest} status {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
