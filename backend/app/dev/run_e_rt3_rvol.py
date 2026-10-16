"""E-RT3 CLI: audit, bootstrap, daily-append and serve the Kiwoom-native RVOL denominator store.

    coverage   what the store holds for a session's canonical universe (read-only, no API calls)
    bootstrap  one missing-only pass: collect only what the frozen window is short of
    append     one session's row per symbol, after 09:31 ET when the 09:30 bar is complete
    serve      the long-running worker: wait for the allowed window, append, then bootstrap
    once       serve for a timer: the same cycles, exiting where serve would sleep
    inventory  E-RUNTIME-R1: list rows stored before their own session closed (read-only, no API)
    repair     E-RUNTIME-R1: re-fetch exactly the inventoried (symbol, session) pairs and replace
               each row explicitly, with an audit row (``--apply``; without it nothing is written)

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
        if error is None and result.conflicts and staged < target:
            # The chart had what the store needs but the store kept other rows for those sessions:
            # walking again cannot change that, so the symbol is parked until it is repaired.
            error = "STORE_CONFLICT"
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
    parked = store.conflicted_symbols()                 # re-walking a CONFLICT never converges
    need = [s for s in mapped if s not in exhausted and s not in parked
            and store.staged_count(s, before) < target]
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


def _appended_sessions(out_dir: Path) -> set[str]:
    """ET dates that already have an ``append_*`` report, from the stamp serve gives the file."""
    sessions = set()
    for path in out_dir.glob("append_*.json"):
        try:
            sessions.add(datetime.strptime(path.stem[len("append_"):][:8], "%Y%m%d").date().isoformat())
        except ValueError:
            continue
    return sessions


def serve(args) -> int:
    """The long-running worker: append the closed session, then bootstrap until the window closes."""
    return _serve(args, once=False)


def once(args) -> int:
    """serve for a timer: the same cycles, but exit where serve would sleep.

    The day's append is skipped when its report already exists, which is what serve's in-memory
    ``appended`` set does across its own cycles.
    """
    return _serve(args, once=True)


def _serve(args, *, once: bool) -> int:
    out_dir = args.out or Path("data/runtime/strategy_e_max/rvol/passes")
    out_dir.mkdir(parents=True, exist_ok=True)
    appended: set[str] = _appended_sessions(out_dir) if once else set()
    log(f"{'once' if once else 'serve'}: window {GUARD_END:%H:%M}-{GUARD_START:%H:%M} ET, target={args.target}, "
        f"workers={args.workers}, rate={args.rate}/s, store={args.store}")
    while True:
        if in_guard():
            if once:
                log("guard window: exiting (E premarket path and A own the API)")
                return 0
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
            if once:
                return 0
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


def _sessions(store: RvolStore, *, until: date) -> list[date]:
    from app.market.calendar import MarketCalendar
    cal = MarketCalendar("America/New_York")
    days = sorted({date.fromisoformat(d) for (d,) in store.connection.execute(
        "SELECT DISTINCT session FROM kiwoom_premarket_sessions")})
    return [d for d in days if d <= until and cal.session(d) is not None]


def inventory(args) -> int:
    """Every row written before its own session closed, split into trading and non-trading dates."""
    store = RvolStore(args.store)
    today = datetime.now(ET).date()
    trading = set(_sessions(store, until=today))
    every = store.premature_rows()
    rows = [r for r in every if date.fromisoformat(r["session"]) in trading]
    off = [r for r in every if date.fromisoformat(r["session"]) not in trading]
    by_session: dict[str, int] = {}
    for r in rows:
        by_session[r["session"]] = by_session.get(r["session"], 0) + 1
    report = {"artifact": "E_RVOL_CORRUPTED_ROWS_V1", "generated_at": datetime.now(ET).isoformat(),
              "store": str(args.store), "rule": "complete=1 AND collected_at < session 09:35 ET, XNYS sessions only",
              "reason": "PREMATURE_DAILY_APPEND_V1", "count": len(rows),
              "symbols": len({r["symbol"] for r in rows}), "by_session": by_session,
              "staged_true": sum(1 for r in rows if r["staged"]),
              "non_trading_dates_excluded": len(off), "rows": rows}
    store.close()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1) + "\n")
        csv_path = args.out.with_suffix(".csv")
        import csv
        with csv_path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["symbol", "session", "pm_dollar_volume", "staged", "pm_bars", "has_open_0930",
                             "collected_at", "session_closed_at", "checksum", "reason_corrupted"])
            for r in rows:
                writer.writerow([r["symbol"], r["session"], r["pm_dollar_volume"], int(r["staged"]), r["pm_bars"],
                                 int(r["has_open_0930"]), r["collected_at"], r["session_closed_at"], r["checksum"],
                                 r["reason_corrupted"]])
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=1))
    return 0


def repair(args) -> int:
    """Targeted repair: walk each inventoried symbol back to its oldest bad session, nothing more."""
    from app.strategy_e_max_rt.rvol_store import REPAIR_PREMATURE_DAILY_APPEND
    body = json.loads(args.inventory.read_text(encoding="utf-8"))
    pairs: dict[str, list[dict]] = {}
    for row in body["rows"]:
        pairs.setdefault(row["symbol"], []).append(row)
    if args.only:
        wanted = set(args.only.split(","))
        pairs = {s: v for s, v in pairs.items() if s in wanted}
    if args.limit:
        pairs = dict(list(pairs.items())[:args.limit])
    store = RvolStore(args.store)
    client = _client(args.rate)
    exch = _listing(client)
    lane = BS.Lane("RVOL_REPAIR", FZ.MINUTE_API, client)
    outcomes: dict[str, int] = {}
    per_symbol, pages_total, started = [], 0, datetime.now(ET)
    log(f"repair: {sum(len(v) for v in pairs.values())} rows over {len(pairs)} symbols, "
        f"apply={args.apply}, store={args.store}")
    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock = threading.Lock()

    def one(symbol: str) -> dict:
        rows = pairs[symbol]
        code = FZ.kiwoom_code(symbol, exch)
        if code not in exch:
            return {"symbol": symbol, "error": "UNMAPPED", "pages": 0, "rows": {r["session"]: "UNMAPPED" for r in rows}}
        oldest = min(date.fromisoformat(r["session"]) for r in rows)
        records, pages, exhausted, error = BS.walk_sessions(lane, code, exch[code], older_than=oldest)
        result = {"symbol": symbol, "pages": pages, "exhausted": exhausted, "error": error, "rows": {}}
        for r in rows:
            day = date.fromisoformat(r["session"])
            fresh = records.get(day)
            if fresh is None:
                # the walk stopped before this session became complete, or Kiwoom has no bar dated
                # with it; nothing is invented
                result["rows"][r["session"]] = "NOT_REACHED" if error or not exhausted else "NO_CHART_ROWS"
                continue
            record = BS.SessionRecord(symbol, day, fresh.pm_dollar_volume, fresh.pm_bars, fresh.has_open_0930)
            if not args.apply:
                result["rows"][r["session"]] = ("WOULD_REPAIR" if record.checksum() != r["checksum"]
                                                else "WOULD_BE_UNCHANGED")
                result.setdefault("new", {})[r["session"]] = {"pm_dollar_volume": record.pm_dollar_volume,
                                                              "pm_bars": record.pm_bars, "staged": record.staged}
                continue
            with lock:
                result["rows"][r["session"]] = store.repair(record, reason=REPAIR_PREMATURE_DAILY_APPEND,
                                                            expected_old_checksum=r["checksum"])
        if args.apply:
            with lock:
                before = datetime.now(ET).date() + timedelta(days=1)
                store.note_pass(symbol, staged_count=store.staged_count(symbol, before),
                                oldest_session=None, history_exhausted=False, error=None)
        return result

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for count, result in enumerate(pool.map(one, list(pairs)), start=1):
            per_symbol.append(result)
            pages_total += result["pages"]
            for outcome in result["rows"].values():
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
            if count % 25 == 0:
                log(f"repair {count}/{len(pairs)} pages={pages_total} outcomes={outcomes}")
    elapsed = (datetime.now(ET) - started).total_seconds()
    report = {"mode": "E_RVOL_TARGETED_REPAIR_V1", "apply": bool(args.apply), "store": str(args.store),
              "inventory": str(args.inventory), "symbols": len(pairs),
              "rows": sum(len(v) for v in pairs.values()), "outcomes": outcomes, "pages": pages_total,
              "elapsed_s": round(elapsed, 1), "requests_per_second": round(pages_total / max(elapsed, 1e-9), 2),
              "started_at": started.isoformat(), "finished_at": datetime.now(ET).isoformat(),
              "per_symbol": per_symbol}
    store.close()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "per_symbol"}, indent=1))
    return 0


def repair_replay(args) -> int:
    """Apply repairs another store already made (its ``kiwoom_repair_log``) without any API call.

    Each row is applied only when this store still holds the exact old row the repair replaced;
    anything else is reported and left alone.
    """
    from app.strategy_e_max_rt.rvol_store import SessionRecord
    source = RvolStore(args.source)
    target = RvolStore(args.store)
    outcomes: dict[str, int] = {}
    rows = source.repair_log()
    for row in rows:
        record = SessionRecord(row["symbol"], date.fromisoformat(row["session"]), row["new_pm_dollar_volume"],
                               int(row["new_pm_bars"]), bool(row["new_has_open_0930"]))
        if record.checksum() != row["new_checksum"]:
            outcome = "SOURCE_LOG_INCONSISTENT"
        elif not args.apply:
            current = target.row(row["symbol"], row["session"])
            outcome = ("WOULD_REPAIR" if current and current["checksum"] == row["old_checksum"]
                       else "ALREADY_REPAIRED" if current and current["checksum"] == row["new_checksum"]
                       else "OLD_ROW_CHANGED")
        else:
            outcome = target.repair(record, reason=row["repair_reason"], expected_old_checksum=row["old_checksum"])
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    if args.apply:
        before = datetime.now(ET).date() + timedelta(days=1)
        for symbol in sorted({row["symbol"] for row in rows}):
            target.note_pass(symbol, staged_count=target.staged_count(symbol, before), oldest_session=None,
                             history_exhausted=False, error=None)
    report = {"mode": "E_RVOL_REPAIR_REPLAY_V1", "apply": bool(args.apply), "source": str(args.source),
              "store": str(args.store), "log_rows": len(rows), "outcomes": outcomes}
    source.close()
    target.close()
    print(json.dumps(report, indent=1))
    if args.out:
        args.out.write_text(json.dumps(report, indent=1) + "\n")
    return 0


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
    sub.add_parser("once").set_defaults(func=once)
    sub.add_parser("inventory").set_defaults(func=inventory)
    rp = sub.add_parser("repair")
    rp.add_argument("--inventory", type=Path, required=True)
    rp.add_argument("--apply", action="store_true", help="write the repairs (default: dry run, no write)")
    rp.add_argument("--only", default="", help="comma-separated symbols")
    rp.add_argument("--limit", type=int, default=0)
    rp.set_defaults(func=repair)
    rr = sub.add_parser("repair-replay")
    rr.add_argument("--source", type=Path, required=True, help="the store whose repair log is replayed")
    rr.add_argument("--apply", action="store_true")
    rr.set_defaults(func=repair_replay)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
