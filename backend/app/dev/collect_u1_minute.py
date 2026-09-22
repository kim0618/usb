"""Collect the U1 historical minute tape of the ranked candidates into the Common Raw store.

    # what would be requested, and which sessions are about to leave the rolling window
    PYTHONPATH=backend .venv/bin/python -m app.dev.collect_u1_minute --dry-run

    # the urgent slice only: one oldest chunk per candidate, nothing after it
    PYTHONPATH=backend .venv/bin/python -m app.dev.collect_u1_minute --fetch --max-requests 136

    # the per-symbol daily tape the U1 daily step reads (ticker aggregate, never grouped)
    PYTHONPATH=backend .venv/bin/python -m app.dev.collect_u1_minute --kind daily --fetch

The candidate list is not typed in. It is rule ``ru2-pit-adv-sector-cap-dataeligible-v2``'s own
liquidity rank, recomputed here from two dated inputs: the Massive CS reference as it stood on the
U1 reference session, and the grouped daily bars of the frozen ``USB-HIST-V1`` snapshot (read
through the snapshot's member list, each file re-hashed). ``--top`` members of that rank that the
snapshot has no minute tape for are what this collector fetches, so the set of requests is a
function of the rule and the store, never of a backtest result.

Storage is the Common Historical Store contract, unchanged (``raw_fetch``): provider bytes gzipped
with mtime 0, the ledger written last so an interrupted request is simply refetched, and one
writer lock over the workspace. No strategy-specific raw store is created, nothing is written into
the frozen snapshot, and no bar is synthesised, interpolated or forward filled - a session this
collector does not receive stays absent and the strict view keeps refusing it.

``collector.lock`` is the same file ``app.dev.historical_v2`` takes, so the two collectors can
never run at once and never double-fetch.
"""

import argparse
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
import time
from typing import Any

from app.backtest.collector.range import sessions_between
from app.backtest.historical_store.a_view import CommonSnapshot
from app.backtest.historical_store.raw_fetch import (
    DAILY_DIR, MINUTE_DIR, RawRequest, fetch_request, ledger_sessions, ordered, plan_requests,
    read_ledger, reserved_path_name,
)
from app.backtest.historical_store.snapshot import POINTER
from app.backtest.research.u1_eligibility import u1_ranges
from app.backtest.research.universe_selection import (
    HISTORY_SESSIONS, ReferenceTicker, SessionBar, rank_by_liquidity,
)
from app.backtest.baseline.contract import PREMARKET_HISTORY_SESSIONS, SETTLEMENT_SESSIONS
from app.backtest.workspace.discovery import resolve_workspace_root
from app.backtest.workspace.errors import WriterLockHeld, WriterLockStale
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.lock import writer_lock
from app.core.config import PROJECT_ROOT, Settings
from app.dev.build_research_universe import ReferenceCache
from app.dev.historical_v2 import _collector_lock, window_start
from app.dev.run_massive_spike import MARKET_TIMEZONE
from app.integrations.massive.client import (
    MassiveAggregatesClient, MassiveError, build_massive_client,
)
from app.integrations.kiwoom.rate_limit import RequestRateLimiter
from app.market.calendar import MarketCalendar

COLLECTOR_VERSION = "u1-minute/2026-09-21.a1"
SNAPSHOT_ID = "USB-HIST-V1"
#: The last entry session of the U1 segment: the session before U2's first entry (2025-09-15).
U1_LAST_ENTRY = date(2025, 9, 12)
DEFAULT_CACHE = PROJECT_ROOT / "data" / "runtime" / "research_universe_u1" / "reference"
OUT = PROJECT_ROOT / "data" / "runtime" / "research_universe_u1"
DEFAULT_TOP = 160
LOCK_BATCH = 25
TRANSIENT_BACKOFF = (60.0, 120.0, 300.0, 600.0, 900.0)
FATAL_CODES = frozenset({"NOT_AUTHORIZED", "SECRET_IN_BODY", "MISSING_API_KEY"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="U1 historical minute collection (Common Raw)")
    parser.add_argument("--workspace-root", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP,
                        help="how many liquidity-ranked candidates to consider (default 160)")
    parser.add_argument("--spacing-seconds", type=float, default=13.0)
    parser.add_argument("--max-requests", type=int, default=None,
                        help="stop after this many requests; the order is oldest-first, so the "
                             "first N are the sessions closest to the rolling window floor")
    parser.add_argument("--fetch", action="store_true",
                        help="actually request; without it nothing leaves the process")
    parser.add_argument("--dry-run", action="store_true", help="plan and print only")
    parser.add_argument("--kind", choices=("minute", "daily"), default="minute",
                        help="minute: the U1 minute window; daily: the per-symbol daily scan range "
                             "(a session the rolling window no longer serves is recorded as "
                             "unavailable, never filled)")
    return parser


# --- dated inputs --------------------------------------------------------------------------------


def u1_reference_session(snapshot: CommonSnapshot, calendar: MarketCalendar) -> tuple[date, date]:
    """The earliest reference session the rule's 25-session history supports, and its as_of."""
    grid = [window.session_date for window in
            sessions_between(calendar, snapshot.start_date, snapshot.end_date)]
    if len(grid) < HISTORY_SESSIONS + 1:
        raise SystemExit(f"NOT RUN: {snapshot.snapshot_id} holds {len(grid)} sessions, the rule "
                         f"needs {HISTORY_SESSIONS} of history plus a selection date")
    return grid[HISTORY_SESSIONS - 1], grid[HISTORY_SESSIONS]


def grouped_from_snapshot(snapshot: CommonSnapshot, sessions: Sequence[date]) -> dict:
    """The rule's grouped daily bars, read from the frozen snapshot's verified members."""
    grouped: dict[date, dict[str, SessionBar]] = {}
    index = {member["path"]: member for member in snapshot.members
             if member["kind"] == "GROUPED_DAILY"}
    for day in sessions:
        relative = f"market_data/raw/massive/grouped_daily/{day.year}/{day.isoformat()}.json.gz"
        member = index.get(relative)
        if member is None:
            raise SystemExit(f"NOT RUN: {snapshot.snapshot_id} has no grouped member for {day}")
        body = json.loads(gzip.decompress(snapshot.verified_path(member).read_bytes()))
        results = (body.get("body") or {}).get("results")
        if not results:
            raise SystemExit(f"NOT RUN: grouped {day} holds no results ({body.get('error')})")
        grouped[day] = {row["T"]: SessionBar(float(row["c"]), float(row["v"]))
                        for row in results
                        if isinstance(row.get("T"), str)
                        and isinstance(row.get("c"), (int, float))
                        and isinstance(row.get("v"), (int, float))}
    return grouped


def snapshot_minute_symbols(snapshot: CommonSnapshot) -> set[str]:
    """Every symbol the snapshot already holds a minute tape for (audit or member)."""
    return set(snapshot.audit) | {member["path"].split("/")[-2] for member in snapshot.members
                                  if member["kind"] in ("MINUTE_RAW", "LEGACY_MINUTE_STRICT")}


def drive_coverage(root: Path, symbols: Sequence[str], grid: Sequence[date], *,
                   base: str = MINUTE_DIR) -> dict[str, set[date]]:
    """Sessions of the grid already held by a COMPLETE Common Raw ledger."""
    coverage: dict[str, set[date]] = {}
    for symbol in symbols:
        folder = root / base / symbol
        days: set[date] = set()
        for path in sorted(folder.glob("*.request.json")) if folder.is_dir() else ():
            ledger = read_ledger(path)
            if ledger is not None and ledger.get("status") == "COMPLETE":
                days |= ledger_sessions(ledger, grid)
        if days:
            coverage[symbol] = days
    return coverage


# --- fetch ---------------------------------------------------------------------------------------


def _batches(workspace: Workspace, items: Sequence, log):  # type: ignore[no-untyped-def]
    """Yield items under the workspace writer lock, released every ``LOCK_BATCH`` items."""
    for start in range(0, len(items), LOCK_BATCH):
        batch = items[start:start + LOCK_BATCH]
        while True:
            try:
                with writer_lock(workspace, purpose=f"collect_u1_minute pid={__import__('os').getpid()}"):
                    yield from batch
                break
            except (WriterLockHeld, WriterLockStale) as error:
                log(f"writer lock busy ({type(error).__name__}); waiting 60s")
                time.sleep(60)


def clip_to_window(calendar: MarketCalendar, request: RawRequest,
                   oldest: date) -> tuple[RawRequest | None, list[date]]:
    """The part of ``request`` the rolling window still serves, and the sessions it lost.

    ``None`` means nothing is left. A request wholly inside the window is returned as is.
    """
    if request.start >= oldest:
        return request, []
    lost = [window.session_date for window in
            sessions_between(calendar, request.start, min(request.end, oldest - timedelta(days=1)))]
    kept = [window.session_date for window in sessions_between(calendar, oldest, request.end)] \
        if oldest <= request.end else []
    if not kept:
        return None, lost
    return RawRequest(request.kind, request.symbol, kept[0], request.end, len(kept)), lost


def run(root: Path, requests: Sequence[RawRequest], *, spacing: float,
        calendar: MarketCalendar, plan_kind: str = "u1_minute") -> int:
    settings = Settings()
    key = settings.massive_api_key
    if key is None or not key.get_secret_value().strip():
        raise SystemExit("NOT RUN: MASSIVE_API_KEY is not configured")
    secret = key.get_secret_value()
    capture: list[bytes] = []
    client = MassiveAggregatesClient(key, limiter=RequestRateLimiter(1.0 / spacing),
                                     page_observer=capture.append)
    log = lambda message: print(message, flush=True)  # noqa: E731
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731
    OUT.mkdir(parents=True, exist_ok=True)
    state = {"collector_version": COLLECTOR_VERSION, "total": len(requests), "done": 0,
             "cached": 0, "failed": 0, "skipped": 0, "rows": 0, "file_bytes": 0,
             "symbols": [], "clipped": [], "started_at": now()}
    symbols: set[str] = set()
    failures = 0
    for number, request in enumerate(_batches(Workspace(root), requests, log), start=1):
        device = reserved_path_name(request.symbol)
        if device:
            state["skipped"] += 1
            log(f"[{number}/{len(requests)}] {request.symbol} skipped: DOS device name {device}")
            continue
        outcome = "not attempted"
        for retry in range(len(TRANSIENT_BACKOFF) + 1):
            oldest = window_start()  # re-read on every attempt: a backoff can cross the ET date
            sent, lost = clip_to_window(calendar, request, oldest)
            if sent is None:
                outcome = "skipped: lost to the rolling window"
                break
            if lost:
                state["clipped"].append([request.symbol, [day.isoformat() for day in lost]])
            extra: dict[str, Any] = {
                "collector_version": COLLECTOR_VERSION, "plan_kind": plan_kind,
                "storage_tier": "DRIVE", "requested_start": request.start.isoformat(),
                "requested_end": request.end.isoformat(), "requested_sessions": request.sessions,
                "clipped_request_start": sent.start.isoformat(),
                "clipped_request_end": sent.end.isoformat(),
                "window_start_at_request": oldest.isoformat(),
                "unavailable_rolling_window": [day.isoformat() for day in lost],
                "coverage_status": "COMPLETE_CLIPPED" if lost else "COMPLETE"}
            try:
                outcome = fetch_request(client, capture, root, sent, secret=secret, log=log,
                                        sleeper=time.sleep, now=now, extra=extra)
            except MassiveError as error:
                code = getattr(error, "code", "MASSIVE_ERROR")
                if code in FATAL_CODES:
                    log(f"FATAL {code}: {error}")
                    return 2
                if retry == len(TRANSIENT_BACKOFF):
                    failures += 1
                    state["failed"] += 1
                    outcome = f"FAILED {code}: {error}"
                    break
                wait = TRANSIENT_BACKOFF[retry]
                log(f"  {request.symbol} {code}; retry in {wait:.0f}s")
                time.sleep(wait)
                continue
            ledger = read_ledger(sent.ledger_path(root)) or {}
            if outcome != "cached" and ledger.get("status") == "COMPLETE":
                state["rows"] += ledger.get("rows", 0)
                state["file_bytes"] += ledger.get("file_bytes", 0)
                symbols.add(request.symbol)
            state["cached" if outcome == "cached" else "done"] += 1
            break
        if outcome.startswith("skipped"):
            state["skipped"] += 1
        state["symbols"] = sorted(symbols)
        state["updated_at"] = now()
        (OUT / ("collect_progress.json" if plan_kind == "u1_minute"
                else f"collect_progress_{plan_kind}.json")).write_text(json.dumps(state, indent=1) + "\n")
        log(f"[{number}/{len(requests)}] {request.symbol} {request.start}..{request.end} "
            f"{outcome} http_total={client.accounting.http_requests} "
            f"statuses={dict(client.accounting.status_codes)}")
    log(f"done http_total={client.accounting.http_requests} failures={failures} "
        f"rows={state['rows']} bytes={state['file_bytes']}")
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    calendar = MarketCalendar(MARKET_TIMEZONE)
    root = resolve_workspace_root(args.workspace_root, must_exist=True)
    pointer = json.loads((root / POINTER).read_text(encoding="utf-8"))
    snapshot = CommonSnapshot.open(root, pointer["snapshot_id"])
    reference_session, selection_as_of = u1_reference_session(snapshot, calendar)
    first_entry = calendar.next_trading_day(selection_as_of)
    ranges = u1_ranges(calendar, entry_start=first_entry, entry_end=U1_LAST_ENTRY,
                       premarket_history_sessions=PREMARKET_HISTORY_SESSIONS,
                       settlement_sessions=SETTLEMENT_SESSIONS)
    daily = args.kind == "daily"
    grid = [window.session_date for window in
            (sessions_between(calendar, ranges.daily_start, ranges.daily_end) if daily else
             sessions_between(calendar, ranges.minute_start, ranges.minute_end))]
    print(f"snapshot={snapshot.snapshot_id} sha256={snapshot.snapshot_sha256}")
    print(f"reference_session={reference_session} selection_as_of={selection_as_of} "
          f"first_entry={first_entry} last_entry={U1_LAST_ENTRY}")
    print(f"{args.kind}_grid={grid[0]}..{grid[-1]} sessions={len(grid)}")

    history = [window.session_date for window in
               sessions_between(calendar, snapshot.start_date, reference_session)]
    grouped = grouped_from_snapshot(snapshot, history)
    cache = ReferenceCache(args.cache_dir,
                           (lambda: build_massive_client(Settings())) if args.fetch else None)
    pages = cache.get(f"tickers/CS_{reference_session.isoformat()}.json.gz",
                      lambda client: {"pages": list(client.reference_tickers(
                          reference_session, security_type="CS"))})
    reference = [ReferenceTicker.from_payload(row) for page in pages["pages"]
                 for row in page.get("results") or ()]
    ranking = rank_by_liquidity(reference, grouped, history,
                                reference_session=reference_session)
    print(f"reference_tickers={len(reference)} eligible={ranking.eligible_reference_size} "
          f"ranked={len(ranking.ranked)} cache_hits={cache.hits} requests={cache.requests}")

    have = snapshot_minute_symbols(snapshot)
    top = [item.symbol for item in ranking.ranked[:args.top]]
    candidates = [symbol for symbol in top if symbol not in have]
    existing = drive_coverage(root, candidates, grid, base=DAILY_DIR if daily else MINUTE_DIR)
    planned = ordered(plan_requests("per_symbol_daily" if daily else "minute", grid, existing,
                                    candidates))
    floor = window_start()
    at_risk = [day for day in grid if day < calendar.next_trading_day(floor)]
    print(f"top={args.top} already_in_snapshot={len(top) - len(candidates)} "
          f"candidates={len(candidates)} planned_requests={len(planned)}")
    print(f"rolling_window_floor={floor} earliest_required_session={grid[0]} "
          f"sessions_remaining_before_loss={len(sessions_between(calendar, floor, grid[0])) - 1}")
    oldest = [item for item in planned if item.start == grid[0]]
    covered = {item.symbol for item in oldest}
    # The earliest session of every candidate must be either held by a COMPLETE ledger already
    # (a resumed collection: the urgent slice is on disk) or in this plan's oldest slice.
    missing = [symbol for symbol in candidates
               if symbol not in covered and grid[0] not in existing.get(symbol, set())]
    print(f"oldest_slice_requests={len(oldest)} distinct_symbols={len(covered)} "
          f"candidates_without_an_oldest_request={len(missing)} at_risk_sessions={len(at_risk)}")
    if missing and not daily:  # the urgent-slice guard is a minute-preservation rule
        print(f"REFUSED: {len(missing)} candidates have no request covering {grid[0]}: "
              f"{','.join(missing[:10])}")
        return 1
    queue = planned[:args.max_requests] if args.max_requests else planned
    if args.max_requests and not daily and len(queue) < len(oldest):
        print(f"REFUSED: --max-requests {args.max_requests} is below the {len(oldest)} requests "
              "the oldest slice needs")
        return 1
    print(f"queue={len(queue)} first={queue[0].symbol} {queue[0].start}..{queue[0].end} "
          f"last={queue[-1].symbol} {queue[-1].start}..{queue[-1].end}")
    if args.dry_run or not args.fetch:
        print("fetched=NO (pass --fetch to request; --dry-run plans only)")
        return 0
    lock = _collector_lock()  # noqa: F841 - held until the process exits
    return run(root, queue, spacing=args.spacing_seconds, calendar=calendar,
               plan_kind="u1_per_symbol_daily" if daily else "u1_minute")


if __name__ == "__main__":
    raise SystemExit(main())
