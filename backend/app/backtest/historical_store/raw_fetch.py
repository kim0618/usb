"""Fetch missing Massive aggregates into the Common Raw store, keeping the provider's bytes.

Two request kinds, both ``adjusted=false``, both Stocks Basic:

* ``minute``: 1-minute aggregates 04:00-20:00 ET over a run of at most ``CHUNK_SESSIONS``
  sessions (one page for any symbol), under ``market_data/raw/massive/minute/<SYMBOL>/``;
* ``per_symbol_daily``: daily aggregates over a contiguous run of missing sessions, under
  ``market_data/raw/massive/per_symbol_daily/<SYMBOL>/``. Authority is the ticker aggregate,
  never merged with grouped daily.

Each page is stored as the exact response body (gzip, mtime 0), so nothing is re-serialized.
No completeness rule is applied: an incomplete session keeps every bar the provider returned,
and no silent minute is synthesized. A request is complete only when its ``.request.json``
ledger exists; the ledger is written last, so an interrupted request is fetched again and
a finished one costs zero calls. A plan-window refusal (403) is written as ``NOT_AVAILABLE``.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from app.backtest.workspace.guards import assert_no_secret_like
from app.integrations.massive.client import (
    MAX_PAGES_HARD_CEILING, PAGE_LIMIT, MassiveAggregatesClient, MassiveError, long_range_page_cap,
)
from app.integrations.massive.minute_bars import ET

RAW_FORMAT_VERSION = "usb-common-raw-v1"
DOS_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                              *(f"COM{d}" for d in "0123456789"),
                              *(f"LPT{d}" for d in "0123456789")})
"""Names a Windows filesystem refuses for any path component, whatever the extension."""


def reserved_path_name(symbol: str) -> str | None:
    """The DOS device name this symbol collides with, or ``None`` when the path is fine.

    Common Raw keeps one directory per symbol and both PCs hold the store on a Windows drive,
    where ``mkdir CON`` fails with EINVAL through the WSL mount. Such a symbol cannot be stored
    at all, so the collector records and skips it instead of dying mid-run, and the skip is the
    same on every machine (local staging included) so the two stores stay identical.
    """
    name = symbol.strip().upper()
    return name if name in DOS_DEVICE_NAMES else None


MINUTE_DIR = "market_data/raw/massive/minute"
DAILY_DIR = "market_data/raw/massive/per_symbol_daily"
CHUNK_SESSIONS = 50  # 50 x 960 extended-hours minutes < the 50,000-row page limit
EXTENDED_MINUTES = 960  # 04:00-20:00 ET
RATE_LIMIT_COOLDOWN_SECONDS = 120.0
RATE_LIMIT_MAX_COOLDOWNS = 5
NOT_AVAILABLE_CODES = frozenset({"PLAN_TIMEFRAME_NOT_INCLUDED", "NOT_AUTHORIZED"})
KINDS = ("per_symbol_daily", "minute")


@dataclass(frozen=True)
class RawRequest:
    kind: str
    symbol: str
    start: date
    end: date
    sessions: int

    def stem(self, root: Path) -> Path:
        base = MINUTE_DIR if self.kind == "minute" else DAILY_DIR
        return root / base / self.symbol / f"{self.symbol}_{self.start.isoformat()}_{self.end.isoformat()}"

    def ledger_path(self, root: Path) -> Path:
        stem = self.stem(root)
        return stem.with_name(stem.name + ".request.json")

    def page_path(self, root: Path, number: int) -> Path:
        stem = self.stem(root)
        return stem.with_name(f"{stem.name}.p{number:02d}.json.gz")


def contiguous_runs(sessions: Sequence[date], missing: Iterable[date]) -> list[list[date]]:
    """Missing sessions grouped into runs that are consecutive on the session grid."""
    index = {s: i for i, s in enumerate(sessions)}
    runs: list[list[date]] = []
    for day in sorted(missing, key=index.__getitem__):
        if runs and index[day] == index[runs[-1][-1]] + 1:
            runs[-1].append(day)
        else:
            runs.append([day])
    return runs


def plan_requests(kind: str, sessions: Sequence[date], existing: Mapping[str, set[date]],
                  symbols: Sequence[str], *, required: Mapping[str, tuple[date, date]] | None = None,
                  chunk: int | None = None) -> list[RawRequest]:
    """Missing sessions per symbol as contiguous requests. ``required`` limits a symbol to its
    ``(start, end)`` range (a strategy fetch universe); ``chunk`` caps the sessions per request."""
    chunk = chunk or (CHUNK_SESSIONS if kind == "minute" else len(sessions))
    requests = []
    for symbol in symbols:
        lo, hi = required[symbol] if required is not None else (sessions[0], sessions[-1])
        missing = [s for s in sessions if lo <= s <= hi and s not in existing.get(symbol, set())]
        for run in contiguous_runs(sessions, missing):
            for i in range(0, len(run), chunk):
                part = run[i:i + chunk]
                requests.append(RawRequest(kind, symbol, part[0], part[-1], len(part)))
    return requests


def minute_page_cap(start: datetime, end: datetime, sessions: int) -> int:
    """The calendar-minute cap while it is under the client's hard ceiling (every request up to
    about 17 months). A longer range is bounded by its sessions instead: 04:00-20:00 ET holds at
    most ``EXTENDED_MINUTES`` bars per session, plus one page of margin."""
    try:
        return long_range_page_cap(start, end)
    except ValueError:
        cap = math.ceil(sessions * EXTENDED_MINUTES / PAGE_LIMIT) + 1
        if cap > MAX_PAGES_HARD_CEILING:
            raise
        return cap


def ordered(requests: Iterable[RawRequest]) -> list[RawRequest]:
    """Oldest first: the oldest sessions are the next to leave the rolling Basic window."""
    return sorted(requests, key=lambda r: (r.start, KINDS.index(r.kind), r.symbol))


def read_ledger(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def ledger_sessions(ledger: Mapping[str, Any], sessions: Sequence[date]) -> set[date]:
    """Sessions a COMPLETE ledger's request covered (bars or not; the provider answered)."""
    if ledger.get("status") != "COMPLETE":
        return set()
    start, end = date.fromisoformat(ledger["start"]), date.fromisoformat(ledger["end"])
    return {s for s in sessions if start <= s <= end}


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with open(partial, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def gzip_bytes(raw: bytes) -> bytes:
    return gzip.compress(raw, compresslevel=6, mtime=0)


def _et_date(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone(ET).date().isoformat()


def _with_cooldown(call: Callable[[], Any], *, log: Callable[[str], None],
                   sleeper: Callable[[float], None]) -> Any:
    for attempt in range(RATE_LIMIT_MAX_COOLDOWNS + 1):
        try:
            return call()
        except MassiveError as error:
            if getattr(error, "code", None) != "RATE_LIMITED" or attempt == RATE_LIMIT_MAX_COOLDOWNS:
                raise
            log(f"rate limited after client retries; cooldown {RATE_LIMIT_COOLDOWN_SECONDS:.0f}s")
            sleeper(RATE_LIMIT_COOLDOWN_SECONDS)
    raise AssertionError("bounded cooldown loop exhausted")


def fetch_request(client: MassiveAggregatesClient, capture: list[bytes], root: Path,
                  request: RawRequest, *, secret: str, log: Callable[[str], None],
                  sleeper: Callable[[float], None], now: Callable[[], str],
                  extra: Mapping[str, Any] | None = None) -> str:
    """``extra`` is merged into the ledger (requested vs clipped range, collector version, tier)."""
    ledger_path = request.ledger_path(root)
    if ledger_path.is_file():
        return "cached"
    before = client.accounting.http_requests

    def call() -> None:
        capture.clear()
        if request.kind == "minute":
            start = datetime.combine(request.start, time(4, 0), tzinfo=ET)
            end = datetime.combine(request.end, time(20, 0), tzinfo=ET)
            client.minute_aggregates(request.symbol, start, end,
                                     max_pages=minute_page_cap(start, end, request.sessions), keep_pages=False)
        else:
            client.daily_aggregates(request.symbol, request.start, request.end)

    ledger: dict[str, Any] = {
        "format": RAW_FORMAT_VERSION, "provider": "massive", "kind": request.kind,
        "authority": "MASSIVE_TICKER_AGGREGATE", "symbol": request.symbol,
        "start": request.start.isoformat(), "end": request.end.isoformat(),
        "requested_sessions": request.sessions, "adjusted": False,
        "timespan": "minute" if request.kind == "minute" else "day",
        "window_et": "04:00-20:00" if request.kind == "minute" else None,
        "endpoint": "/v2/aggs/ticker/{symbol}/range/1/" + ("minute" if request.kind == "minute" else "day")
                    + "/{from}/{to}"}
    ledger.update(extra or {})
    try:
        _with_cooldown(call, log=log, sleeper=sleeper)
    except MassiveError as error:
        code = getattr(error, "code", "MASSIVE_ERROR")
        if code not in NOT_AVAILABLE_CODES:
            raise
        ledger.update(status="NOT_AVAILABLE", error=code, pages=[], rows=0,
                      http_requests=client.accounting.http_requests - before, collected_at=now())
        _atomic_bytes(ledger_path, (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode())
        return code
    pages, rows, days, first, last = [], 0, set(), None, None
    for number, raw in enumerate(capture, start=1):
        if secret and secret.encode() in raw:
            raise MassiveError("SECRET_IN_BODY", "a response body contained the API key")
        body = json.loads(raw)
        assert_no_secret_like(body, where=f"{request.kind} {request.symbol}")
        if body.get("adjusted") is not False:
            raise MassiveError("MALFORMED_PAYLOAD", f"{request.symbol} did not echo adjusted=false")
        results = body.get("results") or []
        rows += len(results)
        for bar in results:
            days.add(_et_date(bar["t"]))
        if results:
            if last is not None and results[0]["t"] <= last:
                raise MassiveError("PAGE_OVERLAP", f"{request.symbol} page {number} does not start after the last")
            first = first if first is not None else results[0]["t"]
            last = results[-1]["t"]
        packed = gzip_bytes(raw)
        path = request.page_path(root, number)
        _atomic_bytes(path, packed)
        pages.append({"file": path.name, "raw_sha256": hashlib.sha256(raw).hexdigest(),
                      "raw_bytes": len(raw), "file_sha256": hashlib.sha256(packed).hexdigest(),
                      "file_bytes": len(packed), "results": len(results)})
    if days and (min(days) < request.start.isoformat() or max(days) > request.end.isoformat()):
        raise MassiveError("MALFORMED_PAYLOAD", f"{request.symbol} bars outside {request.start}..{request.end}")
    ledger.update(status="COMPLETE", pages=pages, rows=rows, sessions_with_bars=len(days),
                  first_bar_ms=first, last_bar_ms=last, pagination_pages=len(pages),
                  first_session=min(days) if days else None, last_session=max(days) if days else None,
                  file_bytes=sum(p["file_bytes"] for p in pages),
                  http_requests=client.accounting.http_requests - before, collected_at=now())
    _atomic_bytes(ledger_path, (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode())
    return f"ok pages={len(pages)} rows={rows} sessions_with_bars={len(days)}/{request.sessions}"
