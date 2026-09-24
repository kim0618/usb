"""E-RT3 CLI: audit, bootstrap, daily-append and serve the Kiwoom-native RVOL denominator store.

    coverage   what the store holds for a session's canonical universe (read-only, no API calls)
    bootstrap  one missing-only pass: collect only what the frozen window is short of
    append     one session's row per symbol, after 09:31 ET when the 09:30 bar is complete
    serve      the long-running worker: wait for the allowed window, append, then bootstrap

Collection is allowed 09:50 - 03:55 ET. The blocked window covers the E premarket path (04:00
rolling cache, 09:25 cutoff, 09:30 entry, 09:34 exit) and Strategy A's own session start, so this
worker can never contend with either. The single minute API ID is the only lane that carries
history, so the worker holds one rate limiter and a few threads that share it.

No signal, no order and no performance number is computed here, and Strategy A is never touched.
The store is a file; nothing in this CLI enables E realtime trading.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time as dtime, timedelta
import json
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.integrations.kiwoom.auth import KiwoomAuthClient
from app.integrations.kiwoom.rate_limit import KiwoomRateLimits, RequestRateLimiter
from app.strategy_e_max_rt import finalizer as FZ, rvol_bootstrap as BS
from app.strategy_e_max_rt.rvol_store import RVOL_MINIMUM, RVOL_WINDOW, RvolStore

ET = ZoneInfo("America/New_York")
DEFAULT_STORE = Path("data/runtime/strategy_e_max/rvol/kiwoom_premarket.sqlite3")
#: The collector stays out of this window: E's premarket path and A's session start own the API then.
GUARD_START, GUARD_END = dtime(3, 55), dtime(9, 50)


def log(message: str) -> None:
    print(f"[{datetime.now(ET):%Y-%m-%d %H:%M:%S} ET] {message}", flush=True)


def in_guard(now: datetime | None = None) -> bool:
    clock = (now or datetime.now(ET)).time()
    return GUARD_START <= clock < GUARD_END


def _universe(path: Path) -> tuple[list[str], dict[str, float], str]:
    art = json.loads(path.read_text(encoding="utf-8"))
    return art["symbols"], art.get("dollar_volume_d_minus_1", {}), art["digest"]


def _latest_universe(path: Path) -> Path:
    """A file, or the newest ``universe_*.json`` in a directory."""
    if path.is_dir():
        files = sorted(path.glob("universe_*.json"))
        if not files:
            raise SystemExit(f"no universe artifact in {path}")
        return files[-1]
    return path


def _client(rate: float):
    s = get_settings()
    auth = KiwoomAuthClient(base_url=s.kiwoom_base_url, app_key=s.kiwoom_app_key.get_secret_value(),
                            app_secret=s.kiwoom_app_secret.get_secret_value(), limiter=KiwoomRateLimits().auth)
    limits = KiwoomRateLimits()
    limits.chart = RequestRateLimiter(rate)     # one limiter: the 5/s cap is per API ID, shared by the workers
    return FZ.ChartLaneClient(base_url=s.kiwoom_base_url, auth=auth, rate_limits=limits, max_retries=1)


def _listing(client) -> dict[str, str]:
    exch: dict[str, str] = {}
    for ex in ("ND", "NY", "NA"):
        for row in client._collect("usa10099", "/api/us/stkinfo", {"stex_tp": ex}, row_key="list", max_pages=50):  # noqa: SLF001
            exch.setdefault(str(row["stk_cd"]).strip(), ex)
    return exch


def _pass(store: RvolStore, lane: BS.Lane, work: list[tuple[str, str, str]], *, before: date, target: int,
          workers: int, deadline, label: str) -> dict:
    """One collection pass; every symbol's outcome is recorded so the next pass skips finished work."""
    started, results = datetime.now(ET), []

    def progress(result: BS.SymbolResult) -> None:
        results.append(result)
        staged = store.staged_count(result.symbol, before)
        # A walk that ended at the page cap without reaching the target cannot get further on the
        # next pass either: it would start from today again and stop at the same place. Record it
        # so the symbol stops being re-queued; the frozen rule already has a denominator for it
        # once it holds the minimum number of staged sessions.
        error = result.error or ("HISTORY_PAGE_LIMIT" if result.page_limited and staged < target else None)
        store.note_pass(result.symbol, staged_count=staged, oldest_session=result.oldest_session,
                        history_exhausted=bool(result.exhausted and staged < target), error=error)
        if len(results) % 25 == 0:
            done = sum(r.pages for r in results)
            log(f"{label} {len(results)}/{len(work)} pages={done} last={result.symbol} staged={staged}")

    BS.bootstrap([lane] * workers, work, store=store, before=before, target=target,
                 deadline=deadline, on_result=progress)
    elapsed = (datetime.now(ET) - started).total_seconds()
    pages = sum(r.pages for r in results)
    return {"label": label, "symbols": len(results), "queued": len(work), "pages": pages,
            "sessions_written": sum(r.sessions_written for r in results),
            "staged_written": sum(r.staged_written for r in results),
            "rate_limited_pauses": sum(r.rate_limited for r in results),
            "errors": {r.symbol: r.error for r in results if r.error},
            "exhausted": [r.symbol for r in results if r.exhausted],
            "elapsed_s": round(elapsed, 1), "requests_per_second": round(pages / max(elapsed, 1e-9), 2),
            "started_at": started.isoformat(), "finished_at": datetime.now(ET).isoformat()}


def coverage(args) -> int:
    symbols, _, digest = _universe(_latest_universe(args.universe))
    store = RvolStore(args.store)
    state = store.collection_state()
    report = store.coverage(symbols, args.session) | {
        "session": args.session.isoformat(), "universe_digest": digest, "store": str(args.store),
        "sessions_needed_to_window": store.bootstrap_plan(symbols, args.session)["sessions_needed_total"],
        "symbols_below_minimum": sum(1 for s in symbols if store.staged_count(s, args.session) < RVOL_MINIMUM),
        "history_exhausted": sorted(store.exhausted_symbols()),
        "symbols_passed": len(state),
    }
    print(json.dumps(report, indent=1))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1) + "\n")
    return 0


def _prepare(args):
    path = _latest_universe(args.universe)
    symbols, liquidity, digest = _universe(path)
    store = RvolStore(args.store)
    client = _client(args.rate)
    exch = _listing(client)
    codes = {s: FZ.kiwoom_code(s, exch) for s in symbols}
    mapped = [s for s in symbols if codes[s] in exch]
    log(f"universe {path.name} rows={len(symbols)} mapped={len(mapped)} digest={digest[:12]}")
    return store, BS.Lane("RVOL", FZ.MINUTE_API, client), symbols, mapped, liquidity, codes, exch, digest


def _queue(store, mapped, liquidity, codes, exch, *, before: date, target: int, skip_exhausted: bool):
    exhausted = store.unreachable_symbols() if skip_exhausted else set()
    need = [s for s in mapped if s not in exhausted and store.staged_count(s, before) < target]
    need.sort(key=lambda s: -float(liquidity.get(s, 0.0)))          # most liquid first: the likely candidates
    fresh = [s for s in mapped if s not in need]                    # only today's row is missing for these
    return ([(s, codes[s], exch[codes[s]]) for s in need],
            [(s, codes[s], exch[codes[s]]) for s in fresh])


def bootstrap(args) -> int:
    store, lane, symbols, mapped, liquidity, codes, exch, digest = _prepare(args)
    before = args.session
    work, _ = _queue(store, mapped, liquidity, codes, exch, before=before, target=args.target,
                     skip_exhausted=not args.include_exhausted)
    if args.limit:
        work = work[:args.limit]
    stop_at = time.monotonic() + args.max_seconds if args.max_seconds else None
    report = _pass(store, lane, work, before=before, target=args.target, workers=args.workers,
                   deadline=lambda: in_guard() or (stop_at is not None and time.monotonic() > stop_at),
                   label="bootstrap") | {"mode": "E_RT3_RVOL_BOOTSTRAP", "universe_digest": digest,
                                         "api_id": FZ.MINUTE_API, "target_staged_sessions": args.target}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "per_symbol"}, indent=1))
    return 0


def append(args) -> int:
    store, lane, symbols, mapped, liquidity, codes, exch, digest = _prepare(args)
    before = args.session + timedelta(days=1)                 # include the session itself
    work = [(s, codes[s], exch[codes[s]]) for s in mapped]
    if args.limit:
        work = work[:args.limit]
    report = _pass(store, lane, work, before=before, target=1, workers=args.workers,
                   deadline=lambda: in_guard(), label="append") | {
        "mode": "E_RT3_RVOL_DAILY_APPEND", "session": args.session.isoformat(), "universe_digest": digest}
    if args.out:
        args.out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


def serve(args) -> int:
    """The long-running worker: append the closed session, then bootstrap until the window closes."""
    out_dir = args.out or Path("data/runtime/strategy_e_max/rvol/passes")
    out_dir.mkdir(parents=True, exist_ok=True)
    appended: set[str] = set()
    log(f"serve: window {GUARD_END:%H:%M}-{GUARD_START:%H:%M} ET, target={args.target}, "
        f"workers={args.workers}, rate={args.rate}/s, store={args.store}")
    while True:
        if in_guard():
            log("guard window: sleeping (E premarket path and A own the API)")
            while in_guard():
                time.sleep(60)
        store, lane, symbols, mapped, liquidity, codes, exch, digest = _prepare(args)
        today = datetime.now(ET).date()
        before = today + timedelta(days=1)
        work, fresh = _queue(store, mapped, liquidity, codes, exch, before=before, target=args.target,
                             skip_exhausted=True)
        stamp = datetime.now(ET).strftime("%Y%m%dT%H%M%S")
        if today.isoformat() not in appended and fresh:
            # symbols already at the window only need the session that just closed; the rest pick it
            # up inside their own bootstrap walk, which starts from today.
            report = _pass(store, lane, fresh, before=before, target=1, workers=args.workers,
                           deadline=in_guard, label="append") | {"mode": "E_RT3_RVOL_DAILY_APPEND",
                                                                 "session": today.isoformat()}
            (out_dir / f"append_{stamp}.json").write_text(json.dumps(report, indent=1) + "\n")
            log(f"append done: {report['symbols']} symbols, {report['pages']} pages, "
                f"{report['staged_written']} staged, {report['requests_per_second']}/s")
        appended.add(today.isoformat())
        if not work:
            cov = store.coverage(symbols, before)
            log(f"bootstrap complete: fully_ready={cov['fully_ready']}/{cov['symbols']} "
                f"exhausted={len(store.exhausted_symbols())}; idling until the next session")
            store.close()
            time.sleep(1800)
            continue
        report = _pass(store, lane, work, before=before, target=args.target, workers=args.workers,
                       deadline=in_guard, label="bootstrap") | {"mode": "E_RT3_RVOL_BOOTSTRAP",
                                                                "target_staged_sessions": args.target,
                                                                "universe_digest": digest}
        (out_dir / f"bootstrap_{stamp}.json").write_text(json.dumps(report, indent=1) + "\n")
        cov = store.coverage(symbols, before)
        log(f"pass done: {report['symbols']}/{report['queued']} symbols, {report['pages']} pages, "
            f"{report['requests_per_second']}/s, errors={len(report['errors'])}; "
            f"coverage ready={cov['fully_ready']} partial={cov['partially_ready']} zero={cov['zero_history']}")
        (out_dir / "coverage_latest.json").write_text(json.dumps(cov | {
            "at": datetime.now(ET).isoformat(), "history_exhausted": len(store.exhausted_symbols())}, indent=1) + "\n")
        store.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="E-RT3 Kiwoom RVOL denominator store")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--universe", type=Path, required=True, help="artifact file or a directory of them")
    parser.add_argument("--session", type=date.fromisoformat, default=datetime.now(ET).date())
    parser.add_argument("--out", type=Path)
    parser.add_argument("--rate", type=float, default=4.6)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--target", type=int, default=RVOL_WINDOW)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("coverage").set_defaults(func=coverage)
    b = sub.add_parser("bootstrap")
    b.add_argument("--limit", type=int, default=0)
    b.add_argument("--max-seconds", type=float, default=0.0)
    b.add_argument("--include-exhausted", action="store_true")
    b.set_defaults(func=bootstrap)
    a = sub.add_parser("append")
    a.add_argument("--limit", type=int, default=0)
    a.set_defaults(func=append)
    sub.add_parser("serve").set_defaults(func=serve)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
