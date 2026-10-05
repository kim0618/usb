"""Keep Strategy A's splits calendar ahead of the trading day, one rolling window per night.

A drops a symbol whose split executes that morning, because the stored previous close is on the
old basis while the premarket prints are on the new one and the mismatch would read as a huge
false gap. ``mover_scanner_v1.universe.split_sessions`` builds that map from *every*
``splits_*.json.gz`` file under ``data/runtime/strategy_c/raw/splits`` (a union by ticker and
execution date). The store was filled by hand and ends at 2026-10-09: after that date nothing is
dropped and the failure is silent, a bad candidate rather than a refusal.

Refresh policy, registered before the first run and not tuned afterwards:

* window ``[today_ET - 7d, today_ET + 14d]``, one ``fetch_splits`` request (one page, the store
  holds ~25 events a week);
* the forward edge keeps the calendar two weeks ahead, so a missed night or two never reaches the
  trading day; the back edge re-reads a week that is already stored, which is the parity check
  against the older files;
* one new file per night, named by ``fetch_splits`` (``splits_<start>_<end>``). Files are never
  rewritten: the reader unions them, so overlapping windows are harmless and a later
  announcement inside an old window is picked up by the next night's file.

Collection is ``strategy_c_selection.raw_fetch.fetch_splits``, unchanged. This module adds only
the window, validation, a quarantine for a body that fails it, the ops ledger and a coverage
report. No fail rule is added to A: ``days_ahead_covered`` is recorded so a stall is visible.

    PYTHONPATH=backend .venv/bin/python -m app.dev.collect_splits --repo . --plan-only
    PYTHONPATH=backend .venv/bin/python -m app.dev.collect_splits --repo .
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import date, datetime, timedelta
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

from app.backtest.strategy_c_selection import raw_fetch as RF
from app.market.calendar import MarketCalendar

ET = ZoneInfo("America/New_York")
#: A's authority store, relative to a repository root. Named once; never a second location.
RAW_ROOT = "data/runtime/strategy_c/raw"
LEDGER_DIR = "data/runtime/ops/splits"
#: Pre-registered window. Calendar days, ET.
WINDOW_BACK_DAYS = 7
WINDOW_AHEAD_DAYS = 14


def log(message: str) -> None:
    print(f"[{datetime.now(ET):%Y-%m-%d %H:%M:%S} ET] {message}", flush=True)


# -- window and coverage ------------------------------------------------------------------------

def window_for(now: datetime) -> tuple[date, date]:
    """The rolling window from *now in America/New_York*; no wall-clock hour is assumed."""
    today = now.astimezone(ET).date()
    return today - timedelta(days=WINDOW_BACK_DAYS), today + timedelta(days=WINDOW_AHEAD_DAYS)


def current_session(calendar: MarketCalendar, today: date) -> date:
    """The session A runs for on ``today``: today if the exchange trades, else the next one."""
    return today if calendar.is_trading_day(today) else calendar.next_trading_day(today)


def _splits_dir(repo: Path) -> Path:
    return repo / RAW_ROOT / "splits"


def _rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Same two shapes ``split_sessions`` reads, so coverage and A can never disagree."""
    pages = body.get("pages")
    return ([row for page in pages for row in (page.get("results") or [])]
            if isinstance(pages, list) else list(body.get("results") or []))


def _event(row: dict[str, Any]) -> tuple[str, str, float, float]:
    return (row["ticker"], row["execution_date"], float(row["split_from"]), float(row["split_to"]))


# -- validation ---------------------------------------------------------------------------------

def validate(path: Path, start: date, end: date) -> dict[str, Any]:
    """Everything checkable from the file alone: shape, requested range, rows, duplicates."""
    problems: list[str] = []
    raw = path.read_bytes()
    out: dict[str, Any] = {"path": str(path), "ok": False, "problems": problems, "events": 0,
                           "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    try:
        body = json.loads(gzip.decompress(raw))
    except (OSError, ValueError) as error:
        problems.append(f"UNREADABLE: {type(error).__name__}")
        return out
    if not isinstance(body, dict):
        problems.append("MALFORMED_TOP_LEVEL")
        return out
    if body.get("format") != RF.RAW_FORMAT_VERSION:
        problems.append(f"FORMAT_MISMATCH: {body.get('format')!r}")
    if body.get("start") != start.isoformat() or body.get("end") != end.isoformat():
        problems.append(f"RANGE_MISMATCH: {body.get('start')!r}..{body.get('end')!r}")
    results = body.get("results")
    if not isinstance(results, list):
        problems.append("NO_RESULTS")
        return out
    if not results:
        # a three-week window with no split at all is a provider fault, not a calm market
        problems.append("ZERO_EVENTS")
    bad, seen, duplicates = 0, set(), 0
    for row in results:
        try:
            ticker, executed = row["ticker"], row["execution_date"]
            ratio = (float(row["split_from"]), float(row["split_to"]))
            ok = (isinstance(ticker, str) and bool(ticker) and isinstance(row.get("id"), str)
                  and start <= date.fromisoformat(executed) <= end
                  and ratio[0] > 0 and ratio[1] > 0
                  and not isinstance(row["split_from"], bool) and not isinstance(row["split_to"], bool))
        except (KeyError, TypeError, ValueError):
            ok = False
        if not ok:
            bad += 1
            continue
        key = row["id"]
        if key in seen:
            duplicates += 1
        seen.add(key)
    if bad:
        problems.append(f"MALFORMED_EVENTS: {bad}")
    if duplicates:
        problems.append(f"DUPLICATE_EVENT_IDS: {duplicates}")
    out["events"] = len(results)
    out["duplicates"] = duplicates
    out["ok"] = not problems
    return out


def quarantine(root: Path, path: Path, *, now: datetime) -> Path:
    target = root / "quarantine" / "splits" / f"{path.name}.{now:%Y%m%dT%H%M%SZ}"
    target.parent.mkdir(parents=True, exist_ok=True)
    path.replace(target)
    return target


def overlap_parity(repo: Path, new_path: Path, start: date, end: date) -> dict[str, Any]:
    """Compare the new window with every other stored file inside the dates both cover.

    Informational, never a gate: A reads (ticker, execution_date) only and the reader unions, so
    a disagreement cannot corrupt the store; it is recorded so a provider revision is visible.
    """
    new_events = {_event(row) for row in _rows(json.loads(gzip.decompress(new_path.read_bytes())))}
    only_old: set[tuple] = set()
    only_new: set[tuple] = set()
    compared: list[str] = []
    for other in sorted(_splits_dir(repo).glob("splits_*.json.gz")):
        if other == new_path:
            continue
        body = json.loads(gzip.decompress(other.read_bytes()))
        try:
            lo, hi = max(start, date.fromisoformat(body["start"])), min(end, date.fromisoformat(body["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if lo > hi:
            continue
        compared.append(other.name)
        old = {_event(row) for row in _rows(body) if lo <= date.fromisoformat(row["execution_date"]) <= hi}
        new = {item for item in new_events if lo <= date.fromisoformat(item[1]) <= hi}
        only_old |= old - new
        only_new |= new - old
    return {"compared_files": compared, "only_in_existing": sorted(only_old)[:20],
            "only_in_existing_count": len(only_old), "only_in_new": sorted(only_new)[:20],
            "only_in_new_count": len(only_new)}


def coverage(repo: Path, calendar: MarketCalendar, now: datetime) -> dict[str, Any]:
    """What A can see right now, measured with A's own reader over the same files."""
    from app.backtest.mover_scanner_v1 import universe as U
    today = now.astimezone(ET).date()
    session = current_session(calendar, today)
    ranges: list[tuple[date, date]] = []
    for path in sorted(_splits_dir(repo).glob("splits_*.json.gz")):
        try:
            body = json.loads(gzip.decompress(path.read_bytes()))
            ranges.append((date.fromisoformat(body["start"]), date.fromisoformat(body["end"])))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    # contiguous reach forward from the session: the horizon A can rely on, gaps excluded
    reach = None
    cursor = session
    changed = True
    while changed:
        changed = False
        for lo, hi in ranges:
            if lo <= cursor <= hi and (reach is None or hi > reach):
                reach, cursor, changed = hi, hi + timedelta(days=1), True
    mapping = U.split_sessions(repo)
    events_on_session = sorted(sym for sym, days in mapping.items() if session in days)
    return {"files": len(ranges),
            "earliest_file_start": min(lo for lo, _ in ranges).isoformat() if ranges else None,
            "latest_split_coverage_date": max(hi for _, hi in ranges).isoformat() if ranges else None,
            "contiguous_through_from_session": reach.isoformat() if reach else None,
            "current_session": session.isoformat(), "today_et": today.isoformat(),
            "days_ahead_covered": (reach - today).days if reach else None,
            "covers_current_session": reach is not None,
            "split_events_on_current_session": events_on_session}


# -- mirror -------------------------------------------------------------------------------------

def mirror(path: Path, repos: tuple[Path, ...], *, digest: str) -> list[dict[str, Any]]:
    """Hardlink into the other tree that carries the same store (disk is 92% full).

    Production keeps two trees of this store as separate inodes. An existing file of the same
    name is compared, never replaced.
    """
    outcomes = []
    for repo in repos:
        target = _splits_dir(Path(repo)) / path.name
        if target.exists():
            same = hashlib.sha256(target.read_bytes()).hexdigest() == digest
            outcomes.append({"repo": str(repo), "status": "PRESENT_IDENTICAL" if same
                             else "MIRROR_CONFLICT_LEFT_ALONE"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(path, target)
            status = "LINKED"
        except OSError:
            target.write_bytes(path.read_bytes())
            status = "COPIED"
        outcomes.append({"repo": str(repo), "status": status})
    return outcomes


# -- the run ------------------------------------------------------------------------------------

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


def write_ledger(repo: Path, payload: dict[str, Any], *, now: datetime) -> Path:
    directory = repo / LEDGER_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"run_{now:%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    cov = payload["coverage"]
    with (directory / "runs.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "at": payload["now_et"], "status": payload["status"], "window": payload["window"],
            "events": payload["events"], "http_requests": payload["http_requests"],
            "latest_split_coverage_date": cov["latest_split_coverage_date"],
            "current_session": cov["current_session"],
            "days_ahead_covered": cov["days_ahead_covered"]}, sort_keys=True) + "\n")
    return path


def run(repo: Path, *, now: datetime | None = None, mirrors: tuple[Path, ...] = (),
        spacing_seconds: float = 13.0, dry_run: bool = False,
        client_factory: Callable[[], Any] | None = None, ledger: bool = True) -> dict[str, Any]:
    repo = Path(repo).resolve()
    calendar = MarketCalendar("America/New_York")
    now = now or datetime.now(ET)
    start, end = window_for(now)
    root = repo / RAW_ROOT
    path = RF.splits_path(root, start, end)
    payload: dict[str, Any] = {
        "collector": "a-splits-refresh/2026-10-05.a", "provider": "massive /v3/reference/splits",
        "now_et": now.astimezone(ET).isoformat(), "window": [start.isoformat(), end.isoformat()],
        "policy": {"back_days": WINDOW_BACK_DAYS, "ahead_days": WINDOW_AHEAD_DAYS},
        "output_file": str(path), "http_requests": 0, "events": 0, "outcome": None,
        "validation": None, "quarantined_to": None, "mirrors": [], "parity": None,
        "mirror_repos": [str(item) for item in mirrors]}
    log(f"window {start}..{end}; target {path.name}")
    status = None
    if path.exists():
        checked = validate(path, start, end)
        payload["validation"] = checked
        if checked["ok"]:
            payload["outcome"] = "REUSED"
            status = "NO_WORK"
        elif dry_run:
            payload["outcome"] = "WOULD_QUARANTINE"
            status = "DRY_RUN"
        else:
            payload["quarantined_to"] = str(quarantine(root, path, now=now))
            log(f"existing file failed validation {checked['problems']}; quarantined")
    elif dry_run:
        payload["outcome"] = "WOULD_COLLECT"
        status = "DRY_RUN"
    if status is None:
        client = (client_factory or _client_factory(spacing_seconds))()
        before = client.accounting.http_requests
        try:
            payload["outcome"] = RF.fetch_splits(client, root, start, end, log=log)
        except Exception as error:  # the file is written atomically, so none exists after this
            payload["outcome"] = f"FETCH_FAILED: {type(error).__name__}: {error}"
            status = "INCOMPLETE"
        payload["http_requests"] = client.accounting.http_requests - before
        if status is None:
            checked = validate(path, start, end)
            payload["validation"] = checked
            if not checked["ok"]:
                payload["quarantined_to"] = str(quarantine(root, path, now=now))
                payload["outcome"] += f"; INVALID {checked['problems']}"
                status = "INCOMPLETE"
            else:
                status = "OK"
    checked = payload["validation"]
    if checked and checked["ok"]:
        payload["events"] = checked["events"]
        payload["parity"] = overlap_parity(repo, path, start, end)
        if status in {"OK", "NO_WORK"} and not dry_run:
            payload["mirrors"] = mirror(path, mirrors, digest=checked["sha256"])
    payload["status"] = status
    payload["coverage"] = coverage(repo, calendar, now)
    if ledger and not dry_run:
        payload["ledger"] = str(write_ledger(repo, payload, now=now))
    cov = payload["coverage"]
    log(f"status {status}; events {payload['events']}; http {payload['http_requests']}; "
        f"coverage through {cov['contiguous_through_from_session']} "
        f"({cov['days_ahead_covered']}d ahead of {cov['today_et']}); session {cov['current_session']}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh Strategy A's splits calendar")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--mirror-repo", type=Path, action="append", default=[])
    parser.add_argument("--spacing-seconds", type=float, default=13.0)
    parser.add_argument("--plan-only", action="store_true", help="no network, no writes")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = run(args.repo, mirrors=tuple(args.mirror_repo), spacing_seconds=args.spacing_seconds,
                  dry_run=args.plan_only)
    if args.json or args.plan_only:
        print(json.dumps(payload, indent=1, sort_keys=True, default=str))
    return 0 if payload["status"] in {"OK", "NO_WORK", "DRY_RUN"} else 1


if __name__ == "__main__":
    sys.exit(main())
