"""USB-HIST-V2: plan (dry run + capacity gate) and fetch the data A/B/C/D need that V1 lacks.

    PYTHONPATH=backend .venv/bin/python -m app.dev.historical_v2 plan
    PYTHONPATH=backend .venv/bin/python -m app.dev.historical_v2 fetch [--max-requests N]

``plan`` makes no API call. It computes the B fetch universe, subtracts what V1 / legacy / earlier
ledgers already hold, estimates calls, rows and bytes per kind, and runs the capacity gate against
the free space of the workspace volume (need x 1.5, in priority order). ``fetch`` re-plans, then
requests only kinds that passed the gate, in ``PRIORITY`` order and oldest sessions first. Each
aggregate request is clipped to the current Basic window just before it is sent, because a range
that starts outside the window is truncated silently rather than refused. The workspace writer
lock is held per batch of requests, so backtest writers are not locked out for the whole run.

B minute (10.3 GB) does not fit the Drive workspace, so it goes to the local staging root
``data/runtime/common_hist/v2_staging`` under the same Common Raw layout and ledger contract
(storage tier LOCAL_STAGING, capacity gate x2 against the local disk). Priority is what leaves
the rolling window first and is largest: B minute, then B per-symbol daily, then A's settlement
session, then the daily reference (not windowed). Sessions a clipped request can no longer get are
kept in ``unavailable_rolling_window.json`` and never reported as covered. One collector at a time
(``collector.lock``).
"""

import argparse
from collections.abc import Callable
import fcntl
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import shutil
import time

from app.backtest.historical_store import b_universe, reference_fetch, requirements
from app.backtest.historical_store.coverage import existing_sessions, minute_universe_v1
from app.backtest.historical_store.raw_fetch import RawRequest, fetch_request, ordered, plan_requests
from app.backtest.workspace.discovery import resolve_workspace_root
from app.backtest.workspace.errors import WriterLockHeld, WriterLockStale
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.lock import writer_lock
from app.core.config import get_settings
from app.dev.fetch_strategy_c_selection_raw import quarter_snapshot_dates, sessions_between
from app.integrations.kiwoom.rate_limit import RequestRateLimiter
from app.integrations.massive.client import (
    PAGE_LIMIT, MassiveAggregatesClient, MassiveConfigurationError, MassiveError,
)
from app.integrations.massive.minute_bars import ET
from app.market.calendar import MarketCalendar

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT = REPO_ROOT / "data/runtime/common_hist/v2"
STAGING = REPO_ROOT / "data/runtime/common_hist/v2_staging"
COLLECTOR_VERSION = "historical_v2/2026-09-18.b3"
LOCAL_KINDS = frozenset({"b_minute"})
LOCAL_SAFETY = 2.0
# Transport and server trouble retries the same item (bounded) instead of skipping it: a skipped
# windowed item would wait for the next run, days later, while its oldest sessions leave the window.
# NOT_AUTHORIZED (401/403) and every payload/pagination error are never retried here.
TRANSIENT_CODES = frozenset({"NETWORK_ERROR", "PROVIDER_TIMEOUT", "PROVIDER_ERROR", "RATE_LIMITED"})
TRANSIENT_BACKOFF = (60, 120, 300, 600, 900)
GRID = (date(2024, 9, 17), date(2026, 9, 16))
A_SETTLEMENT = date(2026, 9, 17)
SAFETY = 1.5
LOCK_BATCH = 20
# Measured, not assumed. Minute: 20 B-scope symbols x 21 sessions (2026-08-03..08-31), fetched
# 2026-09-18, median rows per session by grouped-daily trade count n, and gzip bytes per row.
ROWS_BY_TRADES = ((0, 64), (100, 64), (300, 117), (1_000, 73), (2_000, 114), (4_000, 212), (8_000, 286),
                  (16_000, 366), (32_000, 416), (64_000, 638), (128_000, 936))
MINUTE_GZ_BYTES_PER_ROW = 20.0
# Per-symbol daily: V1 common raw, 274,763 bytes over 6,750 symbol-sessions.
DAILY_GZ_BYTES_PER_SESSION = 41.0
# Reference: the quarterly C snapshots hold 5,133-5,305 CS tickers = 6 pages of 1,000.
REFERENCE_PAGES = 6
REFERENCE_GZ_BYTES_PER_PAGE = 75_000
PRIORITY = ("b_minute", "b_per_symbol_daily", "a_settlement_minute", "reference_daily")


def configure_b_minute_tier(tier: str) -> None:
    """``local`` (default): B minute in the local staging. ``drive``: B minute in the Drive workspace,
    with only Drive/legacy coverage subtracted, so the Drive copy is complete on its own."""
    global LOCAL_KINDS
    LOCAL_KINDS = frozenset({"b_minute"}) if tier == "local" else frozenset()


def kind_root(kind: str, root: Path) -> Path:
    return STAGING if kind in LOCAL_KINDS else root


def window_start(now: datetime | None = None) -> date:
    """Oldest session Stocks Basic serves: the ET date two years back, taken 30 minutes ahead."""
    moment = (now or datetime.now(timezone.utc)).astimezone(ET) + timedelta(minutes=30)
    day = moment.date()
    try:
        return day.replace(year=day.year - 2)
    except ValueError:  # 29 February
        return day.replace(year=day.year - 2, day=28)


def rows_for_trades(trades: float) -> float:
    rows = ROWS_BY_TRADES[0][1]
    for floor, value in ROWS_BY_TRADES:
        if trades >= floor:
            rows = value
    return min(rows, 960.0)


def minute_window_ranges(member, names: list[str], sessions: list[date], start: date,
                         warmup: int) -> dict[str, tuple[date, date]]:
    """B minute ranges restricted to a recent scope window: symbols in S(D) for some D >= ``start``,
    each [first such scope day - ``warmup``, last scope day]. Same PIT membership, shorter window."""
    first = next(i for i, d in enumerate(sessions) if d >= start)
    out = {}
    for j, name in enumerate(names):
        days = [i for i in range(first, len(sessions)) if member[i, j]]
        if days:
            out[name] = (sessions[max(0, days[0] - warmup)], sessions[days[-1]])
    return out


def build_plan(root: Path, *, b_minute_from: date | None = None) -> dict:
    calendar = MarketCalendar()
    sessions = sessions_between(calendar, *GRID)
    index = {s: i for i, s in enumerate(sessions)}
    snapshots = quarter_snapshot_dates(calendar, date(2024, 9, 16), GRID[1])
    universe, member, names, trades = b_universe.build(root, sessions, snapshots)
    col = {t: j for j, t in enumerate(names)}
    ranges = {r["symbol"]: (date.fromisoformat(r["start"]), date.fromisoformat(r["end"])) for r in universe["symbols"]}
    b_symbols = sorted(ranges)
    kinds: dict[str, dict] = {}
    plans: dict[str, list[RawRequest]] = {}

    legacy, common = existing_sessions(root, "per_symbol_daily", sessions, b_symbols)
    have = {s: legacy[s] | common[s] for s in b_symbols}
    requests = plan_requests("per_symbol_daily", sessions, have, b_symbols, required=ranges)
    need = sum(r.sessions for r in requests)
    plans["b_per_symbol_daily"] = requests
    kinds["b_per_symbol_daily"] = {
        "required_symbol_sessions": universe["range_symbol_sessions"],
        "existing_symbol_sessions": universe["range_symbol_sessions"] - need, "missing_symbol_sessions": need,
        "requests": len(requests), "http_calls": len(requests), "expected_rows": need,
        "bytes": int(need * DAILY_GZ_BYTES_PER_SESSION), "windowed": True}

    minute_ranges = ranges if b_minute_from is None else minute_window_ranges(
        member, names, sessions, b_minute_from, b_universe.RvolConfig().lookback_sessions)
    m_symbols = sorted(minute_ranges)
    legacy, common = existing_sessions(root, "minute", sessions, m_symbols)
    if "b_minute" in LOCAL_KINDS:
        _, staged = existing_sessions(root, "minute", sessions, m_symbols, raw_root=STAGING)
    else:
        staged = {s: set() for s in m_symbols}
    have = {s: legacy[s] | common[s] | staged[s] for s in m_symbols}
    first_scope = {r["symbol"]: r["first_scope"] for r in universe["symbols"]}
    requests = sorted(plan_requests("minute", sessions, have, m_symbols, required=minute_ranges, chunk=len(sessions)),
                      key=lambda r: (r.start, first_scope[r.symbol], r.symbol))
    rows_total, calls = 0.0, 0
    for request in requests:
        j = col[request.symbol]
        rows = sum(rows_for_trades(trades[i, j]) for i in range(index[request.start], index[request.end] + 1)
                   if trades[i, j] == trades[i, j])
        rows_total += rows
        calls += max(1, math.ceil(rows / PAGE_LIMIT))
    need = sum(r.sessions for r in requests)
    plans["b_minute"] = requests
    required = sum(len([d for d in sessions if lo <= d <= hi]) for lo, hi in minute_ranges.values())
    kinds["b_minute"] = {
        "scope_window_from": str(b_minute_from) if b_minute_from else None, "symbols": len(m_symbols),
        "required_symbol_sessions": required,
        "existing_symbol_sessions": required - need, "missing_symbol_sessions": need,
        "requests": len(requests), "http_calls": calls, "expected_rows": int(rows_total),
        "bytes": int(rows_total * MINUTE_GZ_BYTES_PER_ROW), "windowed": True}

    a_symbols = [s for s in minute_universe_v1()["symbols"] if s != "SPY"]
    settle = [A_SETTLEMENT]
    legacy, common = existing_sessions(root, "minute", settle, a_symbols)
    requests = plan_requests("minute", settle, {s: legacy[s] | common[s] for s in a_symbols}, a_symbols)
    plans["a_settlement_minute"] = requests
    kinds["a_settlement_minute"] = {
        "required_symbol_sessions": len(a_symbols), "existing_symbol_sessions": len(a_symbols) - len(requests),
        "missing_symbol_sessions": len(requests), "requests": len(requests), "http_calls": len(requests),
        "expected_rows": 960 * len(requests), "bytes": int(960 * len(requests) * MINUTE_GZ_BYTES_PER_ROW),
        "windowed": True, "available_from_et_date": "2026-09-18 (T-1)"}

    as_of = [calendar.previous_trading_day(sessions[0])] + sessions[:-1]
    have_ref = reference_fetch.existing_dates(root, as_of)
    missing_ref = [d for d in as_of if d not in have_ref]
    plans["reference_daily"] = missing_ref
    kinds["reference_daily"] = {
        "required_dates": len(as_of), "existing_dates": len(have_ref), "missing_dates": len(missing_ref),
        "requests": len(missing_ref), "http_calls": len(missing_ref) * REFERENCE_PAGES,
        "expected_rows": len(missing_ref) * 5_250, "bytes": len(missing_ref) * REFERENCE_PAGES * REFERENCE_GZ_BYTES_PER_PAGE,
        "windowed": False}

    free = shutil.disk_usage(root).free
    STAGING.mkdir(parents=True, exist_ok=True)
    local_free = shutil.disk_usage(STAGING).free
    reserved = local_reserved = 0
    for kind in PRIORITY:
        row = kinds[kind]
        row["minutes_at_5_per_min"] = round(row["http_calls"] / 5, 1)
        row["hours_at_13s"] = round(row["http_calls"] * 13 / 3600, 1)
        if kind in LOCAL_KINDS:
            row["storage_tier"], row["storage_root"] = "LOCAL_STAGING", str(STAGING)
            ok = (local_reserved + row["bytes"]) * LOCAL_SAFETY <= local_free
            local_reserved += row["bytes"] if ok else 0
        else:
            row["storage_tier"], row["storage_root"] = "DRIVE", str(root)
            ok = (reserved + row["bytes"]) * SAFETY <= free
            reserved += row["bytes"] if ok else 0
        row["capacity_gate"] = "PASS" if ok else "BLOCKED"
    return {"grid": [str(sessions[0]), str(sessions[-1]), len(sessions)], "free_bytes": free,
            "reserved_bytes": reserved, "safety": SAFETY, "local_free_bytes": local_free,
            "local_reserved_bytes": local_reserved, "local_safety": LOCAL_SAFETY,
            "kinds": kinds, "b_universe": universe,
            "requirements": requirements.document(), "plans": plans}


def _save(plan: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "b_fetch_universe_q1.json").write_text(json.dumps(plan["b_universe"], indent=1) + "\n")
    (OUT / "strategy_requirements.json").write_text(json.dumps(plan["requirements"], indent=1) + "\n")
    summary = {k: v for k, v in plan.items() if k not in {"plans", "b_universe", "requirements"}}
    summary["b_universe_digest"] = plan["b_universe"]["digest"]
    summary["requirements_digest"] = plan["requirements"]["digest"]
    summary["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (OUT / "plan.json").write_text(json.dumps(summary, indent=1) + "\n")


def _locked_batches(workspace: Workspace, items: list, log: Callable[[str], None]):
    """Yield items while holding the writer lock, releasing it every LOCK_BATCH items. Items of a
    LOCAL_STAGING kind do not write the workspace and are yielded without its lock."""
    for start in range(0, len(items), LOCK_BATCH):
        batch = items[start:start + LOCK_BATCH]
        if all(kind in LOCAL_KINDS for kind, _ in batch):
            yield from batch
            continue
        while True:
            try:
                with writer_lock(workspace, purpose=f"historical_v2 fetch pid={os.getpid()}"):
                    yield from batch
                break
            except (WriterLockHeld, WriterLockStale) as error:
                log(f"writer lock busy ({type(error).__name__}); waiting 60s")
                time.sleep(60)


UNAVAILABLE = OUT / "unavailable_rolling_window.json"


def _record_unavailable(kind: str, symbol: str, lost: list[date]) -> None:
    """Merge sessions the window already dropped into the durable ledger (never shrinks)."""
    book = json.loads(UNAVAILABLE.read_text()) if UNAVAILABLE.is_file() else {}
    have = set(book.setdefault(kind, {}).get(symbol, []))
    book[kind][symbol] = sorted(have | {d.isoformat() for d in lost})
    _atomic_json(UNAVAILABLE, book)


def _atomic_json(path: Path, body: dict) -> None:
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n")
    os.replace(partial, path)


def _collector_lock():
    """Exclusive for the life of the process; a second collector exits instead of double-fetching."""
    OUT.mkdir(parents=True, exist_ok=True)
    handle = open(OUT / "collector.lock", "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another historical_v2 collector holds collector.lock")
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()} started={datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
    handle.flush()
    return handle


def fetch(root: Path, plan: dict, *, spacing: float, max_requests: int | None) -> int:
    key = get_settings().massive_api_key
    if key is None or not key.get_secret_value().strip():
        raise MassiveConfigurationError("MISSING_API_KEY", "MASSIVE_API_KEY is not configured")
    secret = key.get_secret_value()
    capture: list[bytes] = []
    client = MassiveAggregatesClient(key, limiter=RequestRateLimiter(1.0 / spacing), page_observer=capture.append)
    calendar = MarketCalendar()
    log = lambda m: print(m, flush=True)  # noqa: E731
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731
    queue: list[tuple[str, object]] = []
    for kind in PRIORITY:
        if plan["kinds"][kind]["capacity_gate"] != "PASS":
            log(f"skip {kind}: {plan['kinds'][kind]['capacity_gate']}")
            continue
        items = plan["plans"][kind]
        if kind == "b_per_symbol_daily":
            items = ordered(items)
        queue += [(kind, item) for item in items]  # b_minute is already oldest-first, reference by date
    queue = queue[:max_requests]
    per_kind = {kind: {"planned": sum(1 for k, _ in queue if k == kind), "done": 0, "cached": 0, "failed": 0,
                       "skipped": 0, "symbol_sessions": 0, "rows": 0, "file_bytes": 0, "http": 0,
                       "unavailable_sessions": 0, "window_clipped": 0,
                       "symbols": set()} for kind in PRIORITY}
    progress = {"collector_version": COLLECTOR_VERSION, "pid": os.getpid(), "priority": list(PRIORITY),
                "total": len(queue), "done": 0, "cached": 0, "failed": 0, "window_clipped": 0,
                "lost_sessions": [], "started_at": now()}
    state = OUT / "fetch_progress.json"
    failures = 0
    for number, (kind, item) in enumerate(_locked_batches(Workspace(root), queue, log), start=1):
        counters = per_kind[kind]
        http_before = client.accounting.http_requests
        clipped_once: set[str] = set()

        def attempt() -> str:
            if kind == "reference_daily":
                return reference_fetch.fetch_reference_day(client, capture, root, item, secret=secret, log=log,
                                                           sleeper=time.sleep, now=now)
            if kind == "a_settlement_minute" and \
                    calendar.previous_trading_day(datetime.now(ET).date()) < A_SETTLEMENT:
                return f"skipped: {A_SETTLEMENT} is not T-1 yet"
            required = request = item
            oldest = window_start()  # re-clipped on every attempt: a backoff may cross the ET date
            lost: list[date] = []
            if request.start < oldest:
                lost = sessions_between(calendar, request.start, min(request.end, oldest - timedelta(days=1)))
                kept = sessions_between(calendar, oldest, request.end) if oldest <= request.end else []
                _record_unavailable(kind, request.symbol, lost)
                new_lost = [d for d in lost if str(d) not in clipped_once]
                if new_lost:
                    counters["unavailable_sessions"] += len(new_lost)
                    progress["lost_sessions"].append([request.symbol, request.kind, [str(d) for d in new_lost]])
                if not clipped_once:
                    progress["window_clipped"] += 1
                    counters["window_clipped"] += 1
                clipped_once.update(str(d) for d in lost)
                if not kept:
                    return "skipped: lost to the rolling window"
                request = RawRequest(request.kind, request.symbol, kept[0], request.end, len(kept))
            target = kind_root(kind, root)
            extra = {"collector_version": COLLECTOR_VERSION, "plan_kind": kind,
                     "storage_tier": "LOCAL_STAGING" if kind in LOCAL_KINDS else "DRIVE",
                     "requested_start": required.start.isoformat(), "requested_end": required.end.isoformat(),
                     "requested_sessions": required.sessions,
                     "clipped_request_start": request.start.isoformat(),
                     "clipped_request_end": request.end.isoformat(), "window_start_at_request": oldest.isoformat(),
                     "unavailable_rolling_window": [d.isoformat() for d in lost],
                     "coverage_status": "COMPLETE_CLIPPED" if lost else "COMPLETE"}
            result = fetch_request(client, capture, target, request, secret=secret, log=log,
                                   sleeper=time.sleep, now=now, extra=extra)
            ledger = json.loads(request.ledger_path(target).read_text())
            if result != "cached" and ledger.get("status") == "COMPLETE":
                counters["symbol_sessions"] += request.sessions
                counters["rows"] += ledger["rows"]
                counters["file_bytes"] += ledger.get("file_bytes", 0)
                counters["symbols"].add(request.symbol)
            return result

        fatal = False
        for retry in range(len(TRANSIENT_BACKOFF) + 1):
            try:
                outcome = attempt()
                if outcome.startswith("skipped"):
                    counters["skipped"] += 1
                else:
                    progress["cached" if outcome == "cached" else "done"] += 1
                    counters["cached" if outcome == "cached" else "done"] += 1
                break
            except MassiveError as error:
                code = getattr(error, "code", "MASSIVE_ERROR")
                if code in TRANSIENT_CODES and retry < len(TRANSIENT_BACKOFF):
                    wait = TRANSIENT_BACKOFF[retry]
                    counters["transient_retries"] = counters.get("transient_retries", 0) + 1
                    log(f"[{number}/{len(queue)}] {kind} transient {code}; same item again in {wait}s "
                        f"({retry + 1}/{len(TRANSIENT_BACKOFF)})")
                    time.sleep(wait)
                    continue
                failures += 1
                progress["failed"] += 1
                counters["failed"] += 1
                outcome = f"FAILED {code}"
                fatal = code in {"NOT_AUTHORIZED", "SECRET_IN_BODY"}
                break
        counters["http"] += client.accounting.http_requests - http_before
        if fatal:
            log(f"fail-closed on {outcome}")
            break
        label = item if kind == "reference_daily" else f"{item.symbol} {item.start}..{item.end} ({item.sessions})"
        log(f"[{number}/{len(queue)}] {kind} {label} {outcome} http_total={client.accounting.http_requests} "
            f"statuses={dict(client.accounting.status_codes)}")
        progress.update(remaining=len(queue) - number, http_total=client.accounting.http_requests,
                        status_codes={str(k): v for k, v in client.accounting.status_codes.items()},
                        retries=client.accounting.retries, current={"kind": kind, "item": label},
                        response_bytes=client.accounting.response_bytes, updated_at=now(),
                        kinds={k: {**{n: v for n, v in c.items() if n != "symbols"}, "symbols": len(c["symbols"]),
                                   "remaining": c["planned"] - c["done"] - c["cached"] - c["failed"] - c["skipped"]}
                               for k, c in per_kind.items() if c["planned"]})
        if kind == "b_minute" or number % LOCK_BATCH == 0:
            m = progress["kinds"].get("b_minute")
            if m:
                expected = plan["kinds"]["b_minute"]
                _atomic_json(OUT / "minute_coverage.json", {
                    "storage_root": str(STAGING), "planned_requests": m["planned"],
                    "planned_symbol_sessions": expected["missing_symbol_sessions"],
                    "planned_http_calls_estimate": expected["http_calls"],
                    "completed_requests": m["done"] + m["cached"], "completed_symbols": m["symbols"],
                    "completed_symbol_sessions": m["symbol_sessions"],
                    "remaining_requests": m["remaining"],
                    "remaining_symbol_sessions": expected["missing_symbol_sessions"] - m["symbol_sessions"]
                    - m["unavailable_sessions"],
                    "downloaded_rows": m["rows"], "downloaded_bytes": m["file_bytes"], "http_calls": m["http"],
                    "failed_requests": m["failed"], "unavailable_sessions_this_run": m["unavailable_sessions"],
                    "eta_requests": m["remaining"], "updated_at": now()})
        _atomic_json(state, progress)
    log(f"done http_total={client.accounting.http_requests} statuses={dict(client.accounting.status_codes)} "
        f"retries={client.accounting.retries} failures={failures}")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "fetch"))
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--spacing-seconds", type=float, default=13.0)
    parser.add_argument("--max-requests", type=int, default=None)
    parser.add_argument("--b-minute-from", type=date.fromisoformat, default=None,
                        help="only B scope days on/after this date (+ RVOL warmup) for B minute")
    parser.add_argument("--b-minute-tier", choices=("local", "drive"), default="local")
    parser.add_argument("--only", default=None, help="comma-separated kinds to fetch (default: all)")
    args = parser.parse_args()
    root = resolve_workspace_root(args.workspace_root)
    lock = _collector_lock() if args.command == "fetch" else None  # noqa: F841 - held until exit
    configure_b_minute_tier(args.b_minute_tier)
    plan = build_plan(root, b_minute_from=args.b_minute_from)
    if args.only:
        wanted = set(args.only.split(","))
        for kind in PRIORITY:
            if kind not in wanted:
                plan["kinds"][kind]["capacity_gate"] = "SKIPPED_BY_ONLY"
    _save(plan)
    print(json.dumps({"free_gb": round(plan["free_bytes"] / 1e9, 2), "kinds": plan["kinds"]}, indent=1), flush=True)
    if args.command == "fetch":
        raise SystemExit(fetch(root, plan, spacing=args.spacing_seconds, max_requests=args.max_requests))


if __name__ == "__main__":
    main()
