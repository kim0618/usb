"""E-MAX-F1 forward collector: reference -> grouped daily -> splits_asof -> universe -> SPY + minute.

Existing collectors are reused unchanged and only wrapped:

* ``strategy_c_selection.raw_fetch.fetch_grouped`` / ``fetch_splits`` / ``fetch_tickers`` write into
  a staging directory inside the forward store; a validated file is then moved to its E1 layout name
  (``grouped_daily/<year>/<D>``, ``splits/splits_asof_<D>``, ``reference/CS_<as_of>``);
* ``historical_store.raw_fetch.fetch_request`` fetches minute range pages (04:00-20:00 ET, exact
  provider bytes, ledger written last, cooldown on 429, 403 -> NOT_AVAILABLE) through a request
  subclass whose paths point at the forward tree and the ``_<SYMBOL>`` directory mapping.

Only closed sessions are requested (Massive Stocks Basic serves D once its ET date has ended); a
session that has not closed stays SESSION_NOT_CLOSED and nothing is written for it. Every write is
under ``market_data/forward``. The collector computes no return and reads no bar after the fetch
beyond validation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any
import uuid

from app.backtest.historical_store import raw_fetch as RF
from app.backtest.strategy_c_selection import raw_fetch as CF
from app.backtest.strategy_e1_forward import layout
from app.backtest.workspace.errors import WriterLockHeld, WriterLockStale
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.lock import writer_lock
from app.integrations.massive.client import MassiveError
from app.market.calendar import MarketCalendar
from app.strategy_e_max_forward import forward_daily as FD, storage as ST

SPY = "SPY"
SPLITS_START = date(2026, 9, 1)            # overlap with the frozen list (executions <= 2026-09-16)
REFERENCE_QUARTERS = ((1, 2), (4, 1), (7, 1), (10, 1))   # development cadence: first session of a quarter
RVOL_CONTEXT_SESSIONS = 50                 # one raw_fetch chunk of pre-boundary history
TRANSIENT_CODES = frozenset({"NETWORK_ERROR", "PROVIDER_TIMEOUT", "PROVIDER_ERROR", "RATE_LIMITED"})
TRANSIENT_BACKOFF = (60, 120, 300, 600, 900)
LOCK_BATCH = 20
DEFAULT_SPACING_SECONDS = 13.0             # client BASIC_CALLS_PER_MINUTE = 5 -> 12 s; 13 s as historical_v2


@dataclass(frozen=True)
class ForwardMinuteRequest(RF.RawRequest):
    """A raw_fetch minute request whose files live in a forward tree under the storage directory."""

    tree: str = ST.MINUTE

    def stem(self, root: Path) -> Path:
        return ST.minute_stem(root, self.tree, self.symbol, self.start, self.end)


@dataclass
class Run:
    root: Path
    run_id: str
    started_at: str
    log: Callable[[str], None]
    entries: list[dict[str, Any]] = field(default_factory=list)
    retries: int = 0

    def record(self, **entry: Any) -> None:
        self.entries.append(entry)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def closed_sessions(start: date, end: date, today_et: date, calendar: MarketCalendar) -> tuple[list[date], list[date]]:
    """(closed, not closed) XNYS sessions in [max(start, boundary), end]."""
    day, closed, open_ = max(start, layout.FORWARD_HOLDOUT_START), [], []
    while day <= end:
        if calendar.session(day) is not None:
            (closed if day < today_et else open_).append(day)
        day += timedelta(days=1)
    return closed, open_


def reference_dates_due(sessions: Sequence[date], today_et: date, calendar: MarketCalendar) -> list[date]:
    """Quarter-start snapshot dates (development cadence) needed as 'latest <= D-1', after 2026-07-01."""
    out = []
    for d in sessions:
        prev = calendar.previous_trading_day(d)
        for month, day in REFERENCE_QUARTERS:
            q = date(prev.year, month, day)
            while calendar.session(q) is None:
                q += timedelta(days=1)
            if date(2026, 7, 1) < q <= prev and q < today_et and q not in out:
                out.append(q)
    return sorted(out)


# -- small-file kinds: stage with the unchanged C fetchers, validate, publish -----------------------

def _publish(run: Run, kind: str, staged: Path, final: Path, validate: Callable[[Path], dict[str, Any]],
             meta: Mapping[str, Any]) -> str:
    report = validate(staged)
    if report["status"] != ST.PASS:
        moved = ST.quarantine(run.root, [staged], reason=f"{kind} {final.name}: {report['errors']}", run_id=run.run_id)
        run.record(kind=kind, file=str(final.relative_to(run.root)), status="QUARANTINED", validation=report,
                   quarantined=moved)
        return f"quarantined {report['errors']}"
    ST.require_forward_tree(run.root, final)
    if final.exists():
        raise ST.ForwardStoreError(f"{final.name} appeared during the run; refusing to overwrite")
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged, final)
    digest = ST.sha256_file(final)
    ST.write_sidecar(run.root, final, {"kind": kind, "file": final.name, "sha256": digest,
                                       "bytes": final.stat().st_size, "run_id": run.run_id,
                                       "retrieved_at": now_iso(), "status": ST.PASS, "validation": report, **meta})
    run.record(kind=kind, file=str(final.relative_to(run.root)), status="VERIFIED", sha256=digest,
               rows=report.get("rows"), symbols=report.get("symbols"))
    return f"ok rows={report.get('rows')}"


def _existing(run: Run, kind: str, final: Path) -> str | None:
    """'verified' for a good existing file, raises on a corrupt one, None when absent."""
    if not final.exists():
        return None
    side = ST.read_sidecar(final)
    if side is None:
        raise ST.ForwardStoreError(f"{final} exists without a validation sidecar; inspect before re-running")
    if side.get("sha256") != ST.sha256_file(final):
        raise ST.ForwardStoreError(f"{final} no longer matches its sidecar (CORRUPT); refusing to overwrite")
    run.record(kind=kind, file=str(final.relative_to(run.root)), status="ALREADY_VERIFIED", sha256=side["sha256"])
    return "verified"


def _stage(run: Run) -> Path:
    path = run.root / ST.STAGING / run.run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def collect_grouped(run: Run, client, session: date) -> str:
    final = ST.grouped_path(run.root, session)
    if (done := _existing(run, "grouped_daily", final)):
        return done
    stage = _stage(run)
    outcome = CF.fetch_grouped(client, stage, session, log=run.log)
    staged = CF.grouped_path(stage, session)
    if not outcome.startswith("ok"):
        staged.unlink(missing_ok=True)       # a provider refusal is recorded, never published
        run.record(kind="grouped_daily", session=session.isoformat(), status="NOT_AVAILABLE", error=outcome)
        return outcome
    return _publish(run, "grouped_daily", staged, final, lambda p: ST.validate_grouped(p, session),
                    {"session": session.isoformat(), "endpoint": "/v2/aggs/grouped/locale/us/market/stocks/{D}",
                     "adjusted": False})


def collect_splits(run: Run, client, session: date) -> str:
    final = ST.splits_asof_path(run.root, session)
    if (done := _existing(run, "splits_asof", final)):
        return done
    stage = _stage(run)
    CF.fetch_splits(client, stage, SPLITS_START, session, log=run.log)
    staged = CF.splits_path(stage, SPLITS_START, session)
    return _publish(run, "splits_asof", staged, final, lambda p: ST.validate_splits(p, session, SPLITS_START),
                    {"session": session.isoformat(), "execution_range": [SPLITS_START.isoformat(), session.isoformat()],
                     "endpoint": "/v3/reference/splits (market-wide)"})


def collect_reference(run: Run, client, as_of: date) -> str:
    final = ST.reference_path(run.root, as_of)
    if (done := _existing(run, "reference", final)):
        return done
    stage = _stage(run)
    CF.fetch_tickers(client, stage, as_of, log=run.log)
    staged = CF.tickers_path(stage, as_of)
    return _publish(run, "reference", staged, final, lambda p: ST.validate_reference(p, as_of),
                    {"as_of": as_of.isoformat(), "endpoint": "/v3/reference/tickers?type=CS&active=true&date="})


# -- minute --------------------------------------------------------------------------------------------

def _ledgers(root: Path, tree: str, symbol: str) -> list[Path]:
    directory = root / tree / ST.symbol_to_storage(symbol)
    return sorted(directory.glob(f"{symbol}_*.request.json")) if directory.exists() else []


def covered_sessions(root: Path, tree: str, symbol: str, calendar: MarketCalendar) -> set[date]:
    """Sessions inside COMPLETE ledgers whose validation sidecar is PASS."""
    out: set[date] = set()
    for ledger_path in _ledgers(root, tree, symbol):
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        side = ST.read_sidecar(ledger_path)
        if ledger.get("status") != "COMPLETE" or side is None or side.get("status") != ST.PASS:
            continue
        day = date.fromisoformat(ledger["start"])
        while day <= date.fromisoformat(ledger["end"]):
            if calendar.session(day) is not None:
                out.add(day)
            day += timedelta(days=1)
    return out


def _sessions(start: date, end: date, calendar: MarketCalendar) -> list[date]:
    out, day = [], start
    while day <= end:
        if calendar.session(day) is not None:
            out.append(day)
        day += timedelta(days=1)
    return out


def minute_requests(root: Path, symbols: Iterable[str], sessions: Sequence[date],
                    calendar: MarketCalendar, *, tree: str = ST.MINUTE) -> list[ForwardMinuteRequest]:
    out = []
    for symbol in sorted(set(symbols)):
        have = covered_sessions(root, tree, symbol, calendar)
        for run in RF.contiguous_runs(list(sessions), [d for d in sessions if d not in have]):
            for i in range(0, len(run), RF.CHUNK_SESSIONS):
                chunk = run[i:i + RF.CHUNK_SESSIONS]
                out.append(ForwardMinuteRequest("minute", symbol, chunk[0], chunk[-1], len(chunk), tree))
    return out


def collect_minute(run: Run, client, capture: list[bytes], request: ForwardMinuteRequest, *, secret: str,
                   calendar: MarketCalendar) -> str:
    ledger_path = request.ledger_path(run.root)
    ST.require_forward_tree(run.root, ledger_path)
    if ledger_path.is_file() and ST.read_sidecar(ledger_path) is None:
        # an earlier run fetched but did not validate (interrupted); validate it now
        pass
    elif ledger_path.is_file():
        run.record(kind=request.tree, symbol=request.symbol, range=[str(request.start), str(request.end)],
                   status="ALREADY_VERIFIED")
        return "verified"
    else:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        outcome = RF.fetch_request(client, capture, run.root, request, secret=secret, log=run.log,
                                   sleeper=time.sleep, now=now_iso,
                                   extra={"collector_version": ST.COLLECTOR_VERSION, "source": ST.SOURCE,
                                          "mode": ST.MODE, "evidence_class": ST.EVIDENCE_CLASS,
                                          "forward_tree": request.tree,
                                          "storage_directory": ST.symbol_to_storage(request.symbol)})
        if not outcome.startswith("ok"):
            run.record(kind=request.tree, symbol=request.symbol, range=[str(request.start), str(request.end)],
                       status="NOT_AVAILABLE", error=outcome)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    pages = [ledger_path.with_name(p["file"]) for p in ledger.get("pages", [])]
    if ledger.get("status") != "COMPLETE":
        # a refused request (e.g. 403 for a session the plan did not serve yet) must not block a retry
        moved = ST.quarantine(run.root, [ledger_path, *pages], reason=f"minute {request.symbol}: {ledger.get('status')}",
                              run_id=run.run_id)
        run.record(kind=request.tree, symbol=request.symbol, status="NOT_AVAILABLE", quarantined=moved)
        return ledger.get("status", "UNKNOWN")
    report = ST.validate_minute(pages, request.symbol, request.start, request.end,
                                _sessions(request.start, request.end, calendar))
    files = [{"file": p.name, "sha256": ST.sha256_file(p)} for p in pages]
    if report["status"] != ST.PASS:
        moved = ST.quarantine(run.root, [ledger_path, *pages], reason=f"minute {request.symbol}: {report['errors']}",
                              run_id=run.run_id)
        run.record(kind=request.tree, symbol=request.symbol, status="QUARANTINED", validation=report, quarantined=moved)
        return f"quarantined {report['errors']}"
    ST.write_sidecar(run.root, ledger_path, {
        "kind": request.tree, "symbol": request.symbol, "storage_directory": ST.symbol_to_storage(request.symbol),
        "range": [request.start.isoformat(), request.end.isoformat()], "files": files,
        "sha256": ST.sha256_file(ledger_path), "run_id": run.run_id, "retrieved_at": ledger.get("collected_at"),
        "status": ST.PASS, "validation": {k: v for k, v in report.items() if k != "sessions_with_bars"},
        "sessions_with_bars": report["sessions_with_bars"]})
    run.record(kind=request.tree, symbol=request.symbol, range=[str(request.start), str(request.end)],
               status="VERIFIED", rows=report["bars"], files=files)
    return f"ok bars={report['bars']}"


# -- locks, retries, manifest ---------------------------------------------------------------------------

def process_lock(path: Path):
    """One forward collector per machine; a second one exits instead of double-fetching."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f"another forward collector holds {path}")
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()} started={now_iso()}\n")
    handle.flush()
    return handle


def locked(root: Path, items: list, log: Callable[[str], None], *, sleeper: Callable[[float], None] = time.sleep):
    """Hold the shared workspace writer lock per batch, as historical_v2 does."""
    workspace = Workspace(root)
    for start in range(0, len(items), LOCK_BATCH):
        batch = items[start:start + LOCK_BATCH]
        while True:
            try:
                with writer_lock(workspace, purpose=f"e-max forward collector pid={os.getpid()}"):
                    yield from batch
                break
            except (WriterLockHeld, WriterLockStale) as error:
                log(f"writer lock busy ({type(error).__name__}); waiting 60s")
                sleeper(60)


def with_retries(run: Run, label: str, call: Callable[[], str], *,
                 sleeper: Callable[[float], None] = time.sleep) -> str:
    for attempt in range(len(TRANSIENT_BACKOFF) + 1):
        try:
            return call()
        except MassiveError as error:
            code = getattr(error, "code", "MASSIVE_ERROR")
            if code in TRANSIENT_CODES and attempt < len(TRANSIENT_BACKOFF):
                run.retries += 1
                run.log(f"{label} transient {code}; retry in {TRANSIENT_BACKOFF[attempt]}s")
                sleeper(TRANSIENT_BACKOFF[attempt])
                continue
            run.record(kind="error", item=label, status="FAILED", error=code)
            if code in {"NOT_AUTHORIZED", "SECRET_IN_BODY"}:
                raise
            return f"FAILED {code}"
    raise AssertionError("bounded retry loop exhausted")


def write_manifest(run: Run, *, sessions: Mapping[str, Any], requested: Mapping[str, Any], status: str,
                   local_copy: Path | None = None) -> Path:
    path = ST.require_forward_tree(run.root, run.root / ST.MANIFESTS / f"run_{run.run_id}.json")
    ok = [e for e in run.entries if e.get("status") in ("VERIFIED", "ALREADY_VERIFIED")]
    failed = [e for e in run.entries if e.get("status") in ("FAILED", "QUARANTINED", "NOT_AVAILABLE")]
    minute = [e for e in run.entries if e.get("kind") in (ST.MINUTE, ST.RVOL_CONTEXT)]
    body = {"format": "e-max-forward-collection-manifest-v1", "collector_version": ST.COLLECTOR_VERSION,
            "source": ST.SOURCE, "mode": ST.MODE, "evidence_class": ST.EVIDENCE_CLASS, "run_id": run.run_id,
            "started_at": run.started_at, "completed_at": now_iso(), "status": status, "sessions": sessions,
            "requested": requested,
            "successful_symbols": sorted({e["symbol"] for e in minute if e.get("status") in ("VERIFIED", "ALREADY_VERIFIED")}),
            "failed_symbols": sorted({e["symbol"] for e in minute if e.get("status") not in ("VERIFIED", "ALREADY_VERIFIED")}),
            "files_verified": len(ok), "failures": failed, "retry_count": run.retries, "entries": run.entries}
    if path.exists():
        raise ST.ForwardStoreError(f"{path.name} exists; run manifests are immutable")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(body, indent=1, sort_keys=True, default=str) + "\n"
    path.write_text(text, encoding="utf-8")
    if local_copy is not None:
        local_copy.mkdir(parents=True, exist_ok=True)
        (local_copy / path.name).write_text(text, encoding="utf-8")
    return path


def new_run(root: Path, log: Callable[[str], None]) -> Run:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Run(root, f"{stamp}-{uuid.uuid4().hex[:6]}", now_iso(), log)


def cleanup_staging(run: Run) -> None:
    stage = run.root / ST.STAGING / run.run_id
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)
