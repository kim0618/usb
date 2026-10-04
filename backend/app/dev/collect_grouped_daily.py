"""Keep Strategy A's grouped daily store continuous, one session per night.

A's union universe reads the market-wide grouped daily store for every session in its
forty-five-calendar-day window, and ``mover_scanner_v1.daily.load_panel`` refuses the whole
build when a *prior* session's file is absent (``NO_GROUPED_DAILY``). The session's own file is
allowed to be absent, because it is only published after that session closes. So A needs one
new file per trading day, delivered before it runs, and nothing in production produced one:
the store was filled by hand and stopped at 2026-10-02.

What this oneshot does, and nothing else:

* the target is the newest session Stocks Basic publishes - the XNYS previous trading day of
  *now in America/New_York*, from ``collector.range.previous_trading_day``, so the schedule is
  DST-safe and no wall-clock hour is written down here;
* it then lists every session A will read for the next trading session and collects the ones
  whose file is missing, oldest first, which repairs a missed night instead of leaving a hole
  that refuses A forever;
* collection itself is ``strategy_c_selection.raw_fetch.fetch_grouped``, unchanged, writing to
  ``data/runtime/strategy_c/raw/grouped/<D>.json.gz`` - the first path A's own ``grouped_path``
  looks at. No second store is created.

Three refusals are deliberate. A file that is already there is validated and *reused*, never
rewritten, so a re-run of the same session costs zero requests. A file that fails validation -
including the ``error`` stub ``fetch_grouped`` writes when the plan refuses a timeframe - is
moved to ``quarantine`` and kept, because leaving it in place would hand A a session with no
rows and A would silently reject every symbol against it. And a session Massive will not serve
is recorded as a collection failure; no earlier session's bytes are ever promoted in its place.

    PYTHONPATH=backend .venv/bin/python -m app.dev.collect_grouped_daily --repo . --plan-only
    PYTHONPATH=backend .venv/bin/python -m app.dev.collect_grouped_daily --repo .
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

from app.backtest.collector.range import previous_trading_day, sessions_between
from app.backtest.mover_scanner_v1 import daily as D
from app.backtest.strategy_c_selection import raw_fetch as RF
from app.market.calendar import MarketCalendar

ET = ZoneInfo("America/New_York")
#: A's authority store, relative to a repository root. Named once; never a second location.
RAW_ROOT = "data/runtime/strategy_c/raw"
#: Where a run's evidence is kept, so a failure survives a vacuumed journal.
LEDGER_DIR = "data/runtime/ops/grouped_daily"
#: Every grouped daily body must carry the benchmark; its absence means a partial session.
BENCHMARK = "SPY"
#: A runaway guard, not a budget: one window's worth of sessions is the most a repair needs.
MAX_SESSIONS = 40


def log(message: str) -> None:
    print(f"[{datetime.now(ET):%Y-%m-%d %H:%M:%S} ET] {message}", flush=True)


class CollectionFailure(RuntimeError):
    """A session A needs could not be made valid. Recorded, never papered over."""


# -- validation ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Validation:
    session: date
    path: Path
    ok: bool
    problems: tuple[str, ...]
    rows: int = 0
    duplicates: int = 0
    benchmark: bool = False
    sha256: str = ""
    bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"session": self.session.isoformat(), "path": str(self.path), "ok": self.ok,
                "problems": list(self.problems), "rows": self.rows, "duplicates": self.duplicates,
                "benchmark_present": self.benchmark, "sha256": self.sha256, "bytes": self.bytes}


def validate(path: Path, session: date) -> Validation:
    """Everything that can be checked from the file alone: identity, schema, rows, benchmark."""
    problems: list[str] = []
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        body = json.loads(gzip.decompress(raw))
    except (OSError, ValueError) as error:
        return Validation(session, path, False, (f"UNREADABLE: {type(error).__name__}",),
                          sha256=digest, bytes=len(raw))
    if not isinstance(body, dict):
        return Validation(session, path, False, ("MALFORMED_TOP_LEVEL",), sha256=digest, bytes=len(raw))
    if body.get("format") != RF.RAW_FORMAT_VERSION:
        problems.append(f"FORMAT_MISMATCH: {body.get('format')!r}")
    if body.get("session") != session.isoformat():
        problems.append(f"SESSION_IDENTITY_MISMATCH: {body.get('session')!r}")
    if body.get("error") is not None:
        problems.append(f"PROVIDER_STUB: {body.get('error')}")
    inner = body.get("body")
    if not isinstance(inner, dict):
        problems.append("NO_BODY")
        return Validation(session, path, False, tuple(problems), sha256=digest, bytes=len(raw))
    if inner.get("adjusted") is not False:
        problems.append(f"NOT_UNADJUSTED: adjusted={inner.get('adjusted')!r}")
    results = inner.get("results")
    if not isinstance(results, list):
        problems.append("NO_RESULTS")
        return Validation(session, path, False, tuple(problems), sha256=digest, bytes=len(raw))
    tickers = [row.get("T") for row in results if isinstance(row, dict)]
    usable = sum(1 for row in results
                 if isinstance(row, dict) and isinstance(row.get("T"), str)
                 and isinstance(row.get("c"), (int, float)) and isinstance(row.get("v"), (int, float)))
    duplicates = len(tickers) - len(set(tickers))
    if not results:
        problems.append("ZERO_ROWS")
    if usable != len(results):
        problems.append(f"UNUSABLE_ROWS: {len(results) - usable}")
    if duplicates:
        problems.append(f"DUPLICATE_TICKERS: {duplicates}")
    declared = inner.get("resultsCount")
    if isinstance(declared, int) and declared != len(results):
        problems.append(f"RESULTS_COUNT_MISMATCH: declared {declared}, rows {len(results)}")
    benchmark = BENCHMARK in set(tickers)
    if not benchmark:
        problems.append(f"NO_{BENCHMARK}")
    return Validation(session, path, not problems, tuple(problems), rows=len(results),
                      duplicates=duplicates, benchmark=benchmark, sha256=digest, bytes=len(raw))


def quarantine(root: Path, validation: Validation, *, now: datetime) -> Path:
    """Keep the rejected bytes out of A's reach and on disk, the forward store's own policy."""
    target = root / "quarantine" / "grouped" / (
        f"{validation.session.isoformat()}.json.gz.{now:%Y%m%dT%H%M%SZ}")
    target.parent.mkdir(parents=True, exist_ok=True)
    validation.path.replace(target)
    return target


# -- mirroring ----------------------------------------------------------------------------------

def mirror(session: date, source: Path, repos: tuple[Path, ...], *, digest: str,
           dry_run: bool = False) -> list[dict[str, Any]]:
    """Hardlink the file into the other repository trees that already carry this store.

    Production keeps two trees of the same store (``/root/usb`` and the paper runner's own
    checkout) and today they hold byte-identical copies. A hardlink keeps them identical at
    zero bytes, which matters on a disk that is 92% full, and because ``fetch_grouped`` never
    rewrites a file that exists the shared inode cannot drift. An existing file is compared,
    never replaced.
    """
    outcomes: list[dict[str, Any]] = []
    for repo in repos:
        target = Path(repo) / RAW_ROOT / "grouped" / f"{session.isoformat()}.json.gz"
        if target.exists():
            same = hashlib.sha256(target.read_bytes()).hexdigest() == digest
            outcomes.append({"repo": str(repo), "status": "PRESENT_IDENTICAL" if same
                             else "MIRROR_CONFLICT_LEFT_ALONE"})
            continue
        if dry_run:
            outcomes.append({"repo": str(repo), "status": "WOULD_LINK"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target)
            status = "LINKED"
        except OSError:
            target.write_bytes(source.read_bytes())
            status = "COPIED"
        outcomes.append({"repo": str(repo), "status": status})
    return outcomes


# -- the run ------------------------------------------------------------------------------------

@dataclass
class Plan:
    now: datetime
    target: date
    next_session: date
    grid: tuple[date, ...]
    required: tuple[date, ...]
    missing: tuple[date, ...]
    invalid: tuple[date, ...]
    planned: tuple[date, ...]
    capped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"now_et": self.now.isoformat(), "target_session": self.target.isoformat(),
                "next_trading_session": self.next_session.isoformat(),
                "grid_sessions": len(self.grid),
                "grid": [self.grid[0].isoformat(), self.grid[-1].isoformat()],
                "required_prior_sessions": len(self.required),
                "missing": [day.isoformat() for day in self.missing],
                "invalid": [day.isoformat() for day in self.invalid],
                "planned": [day.isoformat() for day in self.planned], "capped": self.capped}


def plan(repo: Path, calendar: MarketCalendar, *, now: datetime, session: date | None = None,
         max_sessions: int = MAX_SESSIONS, revalidate: bool = False) -> Plan:
    """What A will read next, which of those files are absent, and which are not usable.

    The target session's file is validated on every run even when it is already there, because
    this is the one file the run is answerable for and a stub left by an earlier night would
    otherwise be read by A as a session with no rows. The other grid files are checked only
    under ``--revalidate``: parsing the whole window costs a few seconds and, worse, a defect
    found in bulk would turn one night into a forty-request refetch.
    """
    target = session or previous_trading_day(calendar, now)
    publishable = previous_trading_day(calendar, now)
    if target > publishable:
        raise CollectionFailure(
            f"{target.isoformat()} is not published yet: Stocks Basic publishes through "
            f"{publishable.isoformat()} and a session is never requested before it closes")
    next_session = calendar.next_trading_day(target)
    start = next_session - timedelta(days=D.DAILY_LOOKBACK_DAYS + 10)
    grid = tuple(window.session_date for window in sessions_between(calendar, start, next_session))
    required = tuple(day for day in grid if day < next_session)
    missing, present = [], []
    for day in required:
        (present if D.grouped_path(repo, day) is not None else missing).append(day)
    checked = present if revalidate else [day for day in present if day == target]
    invalid = tuple(day for day in checked
                    if not validate(D.grouped_path(repo, day), day).ok)
    candidates = sorted(set(missing) | set(invalid))
    planned = tuple(candidates[:max_sessions])
    return Plan(now, target, next_session, grid, tuple(required), tuple(missing), invalid, planned,
                capped=len(planned) < len(candidates))


@dataclass
class Run:
    plan: Plan
    results: list[dict[str, Any]] = field(default_factory=list)
    http_requests: int = 0

    def record(self, session: date, status: str, **extra: Any) -> None:
        self.results.append({"session": session.isoformat(), "status": status, **extra})
        log(f"{session.isoformat()}: {status}")


def collect_one(run: Run, repo: Path, session: date, *, client_factory: Callable[[], Any],
                client_box: list[Any], mirrors: tuple[Path, ...], dry_run: bool) -> bool:
    """Make one session valid on disk. Returns whether A can read it afterwards."""
    root = repo / RAW_ROOT
    existing = D.grouped_path(repo, session)
    if existing is not None:
        checked = validate(existing, session)
        if checked.ok:
            run.record(session, "REUSED", validation=checked.as_dict())
            return True
        if dry_run:
            run.record(session, "WOULD_QUARANTINE", validation=checked.as_dict())
            return False
        moved = quarantine(root, checked, now=run.plan.now)
        run.record(session, "QUARANTINED", validation=checked.as_dict(), quarantined_to=str(moved))
    if dry_run:
        run.record(session, "WOULD_COLLECT")
        return False
    if not client_box:
        client_box.append(client_factory())
    client = client_box[0]
    before = client.accounting.http_requests
    outcome = RF.fetch_grouped(client, root, session, log=log)
    run.http_requests += client.accounting.http_requests - before
    written = RF.grouped_path(root, session)
    if not written.is_file():
        run.record(session, "NOT_COLLECTED", outcome=outcome)
        return False
    checked = validate(written, session)
    if not checked.ok:
        moved = quarantine(root, checked, now=run.plan.now)
        run.record(session, "COLLECTED_INVALID", outcome=outcome, validation=checked.as_dict(),
                   quarantined_to=str(moved))
        return False
    run.record(session, "COLLECTED", outcome=outcome, validation=checked.as_dict(),
               mirrors=mirror(session, written, mirrors, digest=checked.sha256))
    return True


def write_ledger(repo: Path, payload: dict[str, Any], *, now: datetime) -> Path:
    directory = repo / LEDGER_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"run_{now:%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    with (directory / "runs.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": payload["plan"]["now_et"], "status": payload["status"],
                                 "target": payload["plan"]["target_session"],
                                 "collected": payload["collected"],
                                 "prior_missing_after": payload["prior_missing_after"],
                                 "http_requests": payload["http_requests"]}, sort_keys=True) + "\n")
    return path


def _client_factory(spacing: float) -> Callable[[], Any]:
    def build() -> Any:
        from app.core.config import get_settings
        from app.integrations.kiwoom.rate_limit import RequestRateLimiter
        from app.integrations.massive.client import MassiveAggregatesClient, MassiveConfigurationError
        key = get_settings().massive_api_key
        if key is None or not key.get_secret_value().strip():
            raise MassiveConfigurationError("MISSING_API_KEY", "MASSIVE_API_KEY is not configured")
        return MassiveAggregatesClient(key, limiter=RequestRateLimiter(1.0 / spacing))
    return build


def run(repo: Path, *, now: datetime | None = None, session: date | None = None,
        mirrors: tuple[Path, ...] = (), max_sessions: int = MAX_SESSIONS,
        spacing_seconds: float = 13.0, dry_run: bool = False, revalidate: bool = False,
        client_factory: Callable[[], Any] | None = None,
        ledger: bool = True) -> dict[str, Any]:
    repo = Path(repo).resolve()
    calendar = MarketCalendar("America/New_York")
    now = now or datetime.now(ET)
    the_plan = plan(repo, calendar, now=now, session=session, max_sessions=max_sessions,
                    revalidate=revalidate)
    log(f"target {the_plan.target} -> next session {the_plan.next_session}; "
        f"grid {the_plan.grid[0]}..{the_plan.grid[-1]} ({len(the_plan.grid)} sessions); "
        f"missing {len(the_plan.missing)}; invalid {len(the_plan.invalid)}; "
        f"planned {len(the_plan.planned)}")
    active = Run(the_plan)
    box: list[Any] = []
    for day in the_plan.planned:
        collect_one(active, repo, day, client_factory=client_factory or _client_factory(spacing_seconds),
                    client_box=box, mirrors=mirrors, dry_run=dry_run)
    still_missing = tuple(day for day in the_plan.required if D.grouped_path(repo, day) is None)
    collected = [item["session"] for item in active.results if item["status"] == "COLLECTED"]
    reused = [item["session"] for item in active.results if item["status"] == "REUSED"]
    target_readable = D.grouped_path(repo, the_plan.target) is not None
    status = ("DRY_RUN" if dry_run else
              "NO_WORK" if not the_plan.planned and not still_missing else
              "OK" if not still_missing else "INCOMPLETE")
    payload = {"collector": "a-grouped-daily-refresh/2026-10-05.a", "status": status,
               "plan": the_plan.as_dict(), "results": active.results,
               "collected": collected, "reused": reused,
               "http_requests": active.http_requests,
               "prior_missing_after": [day.isoformat() for day in still_missing],
               "target_session_readable": target_readable,
               "a_next_session_readable": not still_missing,
               "authority_root": str(repo / RAW_ROOT / "grouped"),
               "mirror_repos": [str(path) for path in mirrors]}
    if ledger and not dry_run:
        payload["ledger"] = str(write_ledger(repo, payload, now=now))
    log(f"status {status}; collected {len(collected)}; prior_missing_after {len(still_missing)}; "
        f"http {active.http_requests}; A next session {the_plan.next_session} "
        f"{'READY' if not still_missing else 'NOT READY'}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh Strategy A's grouped daily store")
    parser.add_argument("--repo", type=Path, required=True,
                        help="repository root whose data/runtime A reads")
    parser.add_argument("--mirror-repo", type=Path, action="append", default=[],
                        help="another repository tree that carries the same store (hardlinked)")
    parser.add_argument("--session", type=date.fromisoformat,
                        help="collect as if this were the newest published session")
    parser.add_argument("--max-sessions", type=int, default=MAX_SESSIONS)
    parser.add_argument("--spacing-seconds", type=float, default=13.0,
                        help="seconds between HTTP attempts (Stocks Basic allows 5/min)")
    parser.add_argument("--plan-only", action="store_true", help="no network, no writes")
    parser.add_argument("--revalidate", action="store_true",
                        help="validate every prior grid file, not only the target session")
    parser.add_argument("--json", action="store_true", help="print the run payload")
    args = parser.parse_args(argv)
    try:
        payload = run(args.repo, session=args.session, mirrors=tuple(args.mirror_repo),
                      max_sessions=args.max_sessions, spacing_seconds=args.spacing_seconds,
                      dry_run=args.plan_only, revalidate=args.revalidate)
    except CollectionFailure as error:
        log(f"REFUSED {error}")
        return 2
    if args.json or args.plan_only:
        print(json.dumps(payload, indent=1, sort_keys=True))
    return 0 if payload["status"] in {"OK", "NO_WORK", "DRY_RUN"} else 1


if __name__ == "__main__":
    sys.exit(main())
