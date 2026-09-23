"""E-RT3 CLI: audit, bootstrap and daily-append the Kiwoom-native RVOL denominator store.

    coverage   what the store holds for a session's canonical universe (read-only, no API calls)
    bootstrap  collect only the missing sessions, most liquid first, stopping at a wall-clock guard
    append     one session's row per symbol, after 09:31 ET when the 09:30 bar is complete

No signal, no order, no performance number is computed here, and Strategy A is never touched.
The store is a file; nothing in this CLI enables E realtime trading.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time as dtime, timedelta
import json
from pathlib import Path
import sys
import threading
import time
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.integrations.kiwoom.auth import KiwoomAuthClient
from app.integrations.kiwoom.rate_limit import KiwoomRateLimits, RequestRateLimiter
from app.strategy_e_max_rt import finalizer as FZ, rvol_bootstrap as BS
from app.strategy_e_max_rt.rvol_store import RVOL_MINIMUM, RVOL_WINDOW, RvolStore

ET = ZoneInfo("America/New_York")
DEFAULT_STORE = Path("data/runtime/strategy_e_max/rvol/kiwoom_premarket.sqlite3")
#: The collector must be out of the way before the E-RT2 rolling cache and A's session start.
GUARD_START, GUARD_END = dtime(3, 55), dtime(9, 35)


def log(message: str) -> None:
    print(f"[{datetime.now(ET):%H:%M:%S}] {message}", flush=True)


def _universe(path: Path) -> tuple[list[str], dict[str, float], str]:
    art = json.loads(path.read_text(encoding="utf-8"))
    return art["symbols"], art.get("dollar_volume_d_minus_1", {}), art["digest"]


def _clients(rate: float = 4.0):
    s = get_settings()
    auth = KiwoomAuthClient(base_url=s.kiwoom_base_url, app_key=s.kiwoom_app_key.get_secret_value(),
                            app_secret=s.kiwoom_app_secret.get_secret_value(), limiter=KiwoomRateLimits().auth)
    limits = KiwoomRateLimits()
    limits.chart = RequestRateLimiter(rate)        # one limiter: the 5/s cap is per API ID, shared by the workers
    return FZ.ChartLaneClient(base_url=s.kiwoom_base_url, auth=auth, rate_limits=limits, max_retries=1)


def _listing(client) -> dict[str, str]:
    exch: dict[str, str] = {}
    for ex in ("ND", "NY", "NA"):
        for row in client._collect("usa10099", "/api/us/stkinfo", {"stex_tp": ex}, row_key="list", max_pages=50):  # noqa: SLF001
            exch.setdefault(str(row["stk_cd"]).strip(), ex)
    return exch


def coverage(args) -> int:
    symbols, _, digest = _universe(args.universe)
    store = RvolStore(args.store)
    report = store.coverage(symbols, args.session) | {
        "session": args.session.isoformat(), "universe_digest": digest, "store": str(args.store),
        "plan_to_window": store.bootstrap_plan(symbols, args.session)["sessions_needed_total"],
        "symbols_below_minimum": sum(1 for s in symbols if store.staged_count(s, args.session) < RVOL_MINIMUM),
    }
    report.pop("distribution", None) if args.quiet else None
    print(json.dumps(report, indent=1))
    if args.out:
        args.out.write_text(json.dumps(report, indent=1) + "\n")
    return 0


def bootstrap(args) -> int:
    symbols, liquidity, digest = _universe(args.universe)
    store = RvolStore(args.store)
    client = _clients(args.rate)
    exch = _listing(client)
    codes = {s: FZ.kiwoom_code(s, exch) for s in symbols}
    need = [s for s in symbols if store.staged_count(s, args.session) < args.target and codes[s] in exch]
    need.sort(key=lambda s: -float(liquidity.get(s, 0.0)))         # most liquid first: the likely candidates
    if args.limit:
        need = need[:args.limit]
    lane = FZ.Lane("BOOT", FZ.MINUTE_API, client)
    stop_at = time.monotonic() + args.max_seconds if args.max_seconds else None
    started = datetime.now(ET)

    def past_guard() -> bool:
        clock = datetime.now(ET).time()
        return (GUARD_START <= clock <= GUARD_END) or (stop_at is not None and time.monotonic() > stop_at)

    results: list[BS.SymbolResult] = []
    done = threading.Semaphore(0)

    def progress(result: BS.SymbolResult) -> None:
        results.append(result)
        if len(results) % 10 == 0 or result.error:
            log(f"{len(results)}/{len(need)} {result.symbol} pages={result.pages} "
                f"staged={result.staged_written} err={result.error}")
        done.release()

    log(f"bootstrap: {len(need)} symbols short of {args.target} staged sessions "
        f"(universe {len(symbols)}, store {args.store})")
    out = BS.bootstrap([lane] * args.workers, [(s, codes[s], exch[codes[s]]) for s in need],
                       store=store, before=args.session, target=args.target,
                       deadline=past_guard, on_result=progress)
    report = {
        "mode": "E_RT3_RVOL_BOOTSTRAP", "session": args.session.isoformat(), "universe_digest": digest,
        "api_id": FZ.MINUTE_API, "workers": args.workers, "target_staged_sessions": args.target,
        "symbols_attempted": len(out), "symbols_short": len(need),
        "pages": sum(r.pages for r in out), "sessions_written": sum(r.sessions_written for r in out),
        "staged_written": sum(r.staged_written for r in out),
        "errors": {r.symbol: r.error for r in out if r.error},
        "rate_limited_pauses": sum(r.rate_limited for r in out),
        "exhausted_history": [r.symbol for r in out if r.exhausted],
        "started_at": started.isoformat(), "finished_at": datetime.now(ET).isoformat(),
        "elapsed_s": round((datetime.now(ET) - started).total_seconds(), 1),
        "per_symbol": {r.symbol: {"pages": r.pages, "seconds": r.seconds, "staged": r.staged_written,
                                  "oldest": r.oldest_session, "exhausted": r.exhausted} for r in out},
    }
    report["pages_per_symbol"] = round(report["pages"] / max(1, len(out)), 1)
    report["requests_per_second"] = round(report["pages"] / max(1e-9, report["elapsed_s"]), 2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "per_symbol"}, indent=1))
    return 0


def append(args) -> int:
    symbols, _, digest = _universe(args.universe)
    store = RvolStore(args.store)
    client = _clients()
    exch = _listing(client)
    codes = {s: FZ.kiwoom_code(s, exch) for s in symbols}
    lane = FZ.Lane("APPEND", FZ.MINUTE_API, client)
    todo = [(s, codes[s], exch[codes[s]]) for s in symbols if codes[s] in exch]
    if args.limit:
        todo = todo[:args.limit]
    started = datetime.now(ET)
    out = [BS.append_session(lane, s, c, e, store=store, session=args.session) for s, c, e in todo]
    report = {"mode": "E_RT3_RVOL_DAILY_APPEND", "session": args.session.isoformat(),
              "universe_digest": digest, "symbols": len(out), "pages": sum(r.pages for r in out),
              "sessions_written": sum(r.sessions_written for r in out),
              "staged_written": sum(r.staged_written for r in out),
              "errors": {r.symbol: r.error for r in out if r.error},
              "elapsed_s": round((datetime.now(ET) - started).total_seconds(), 1)}
    if args.out:
        args.out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="E-RT3 Kiwoom RVOL denominator store")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--session", type=date.fromisoformat, required=True)
    parser.add_argument("--out", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("coverage")
    c.add_argument("--quiet", action="store_true")
    c.set_defaults(func=coverage)
    b = sub.add_parser("bootstrap")
    b.add_argument("--target", type=int, default=RVOL_WINDOW)
    b.add_argument("--limit", type=int, default=0)
    b.add_argument("--workers", type=int, default=2)
    b.add_argument("--rate", type=float, default=4.0)
    b.add_argument("--max-seconds", type=float, default=0.0)
    b.set_defaults(func=bootstrap)
    a = sub.add_parser("append")
    a.add_argument("--limit", type=int, default=0)
    a.set_defaults(func=append)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
