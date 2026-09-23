"""E-RT3: fill the Kiwoom RVOL store's missing sessions, and append one session a day.

Bootstrap walks the chart back per symbol until the frozen window's worth of *staged* sessions is
in hand or Kiwoom's own history runs out, and writes one compact row per session. It reuses the
E-RT2 lane structure (two independent API IDs, whose 5 req/s limits were measured to be
independent) and the E-RT2 code mapping, and it only asks for what the store is missing.

Two rules keep the rows honest:

* the oldest session on the walk is *incomplete* - the walk stopped inside it, so its premarket sum
  would be truncated. It is never written. A session is complete once a strictly older row is seen;
* a session with no 09:30 bar is written but not staged, which is exactly the frozen staging rule.

Nothing here computes a signal, an order or a performance number, and nothing writes Massive data.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
import threading
import time
from typing import Any

from app.core.exceptions import MarketDataError
from app.integrations.kiwoom.timestamps import minute_timestamp
from app.strategy_e_max_rt.finalizer import MINUTE_API, TICK_API, Lane, kiwoom_code  # noqa: F401
from app.strategy_e_max_rt.rvol_store import (
    RVOL_WINDOW, RvolStore, SessionRecord,
)
from app.backtest.strategy_e1_premarket.premarket import DECISION_LAST_BAR, OPEN_MIN, PREMARKET_START

MAX_PAGES = 400
RATE_RETRIES = 5          # a 429 pauses this symbol's walk; it never abandons the page


@dataclass
class _Acc:
    pm_dollar: float = 0.0
    pm_bars: int = 0
    has_open: bool = False


@dataclass
class SymbolResult:
    symbol: str
    pages: int = 0
    seconds: float = 0.0
    sessions_written: int = 0
    staged_written: int = 0
    exhausted: bool = False
    rate_limited: int = 0
    oldest_session: str | None = None
    error: str | None = None
    lane: str = ""

    def to_json(self) -> dict[str, Any]:
        return self.__dict__ | {}


def collect_symbol(lane: Lane, symbol: str, code: str, exchange: str, *, store: RvolStore,
                   before: date, target: int = RVOL_WINDOW, max_pages: int = MAX_PAGES) -> SymbolResult:
    """Page one symbol's chart back to ``target`` staged sessions strictly before ``before``."""
    result = SymbolResult(symbol, lane=lane.api_id)
    acc: dict[date, _Acc] = {}
    page, started = None, time.monotonic()
    try:
        while result.pages < max_pages:
            for attempt in range(RATE_RETRIES + 1):
                try:
                    page = lane.page(code, exchange, continuation=page)
                    break
                except MarketDataError as exc:
                    if getattr(exc, "code", "") != "RATE_LIMITED" or attempt == RATE_RETRIES:
                        raise
                    result.rate_limited += 1
                    time.sleep(1.0 + attempt)
            result.pages += 1
            rows = page.body.get("result_list", [])
            for row in rows:
                stamp = minute_timestamp(row)
                session, minute = stamp.date(), stamp.hour * 60 + stamp.minute
                if session >= before:
                    continue
                bucket = acc.setdefault(session, _Acc())
                if PREMARKET_START <= minute <= DECISION_LAST_BAR:
                    bucket.pm_dollar += float(row["cur_prc"]) * float(row["trde_qty"])
                    bucket.pm_bars += 1
                elif minute == OPEN_MIN:
                    bucket.has_open = True
            complete = sorted(acc)[1:] if len(acc) > 1 else []            # all but the oldest seen
            staged = [d for d in complete if acc[d].pm_bars > 0 and acc[d].has_open]
            if len(staged) >= target or not rows or not page.continuation:
                result.exhausted = not (rows and page.continuation)
                break
    except Exception as exc:                                   # a refusal ends this symbol, not the run
        result.error = getattr(exc, "code", None) or type(exc).__name__
    result.seconds = round(time.monotonic() - started, 2)
    ordered = sorted(acc)
    writable = ordered[1:] if (ordered and not result.exhausted) else ordered
    for session in writable:
        bucket = acc[session]
        record = SessionRecord(symbol, session, bucket.pm_dollar, bucket.pm_bars, bucket.has_open)
        if store.put(record) == "WRITTEN":
            result.sessions_written += 1
            result.staged_written += int(record.staged)
    result.oldest_session = writable[0].isoformat() if writable else None
    return result


def bootstrap(lanes: Sequence[Lane], work: Sequence[tuple[str, str, str]], *, store: RvolStore,
              before: date, target: int = RVOL_WINDOW, store_lock: threading.Lock | None = None,
              deadline: Callable[[], bool] | None = None,
              on_result: Callable[[SymbolResult], None] | None = None) -> list[SymbolResult]:
    """Run ``work`` (symbol, code, exchange) over the lanes in parallel, one thread per lane.

    Each lane holds its own API ID, so the two limiters do not contend. ``deadline`` is polled
    between symbols: the collector stops cleanly before a window it must not touch (03:55 ET, when
    the E-RT2 rolling cache and A's session start).
    """
    lock = store_lock or threading.Lock()
    results: list[SymbolResult] = []
    queue = list(work)
    index = 0
    queue_lock = threading.Lock()

    def worker(lane: Lane) -> None:
        nonlocal index
        while True:
            if deadline is not None and deadline():
                return
            with queue_lock:
                if index >= len(queue):
                    return
                symbol, code, exchange = queue[index]
                index += 1
            result = collect_symbol(lane, symbol, code, exchange, store=_Locked(store, lock),
                                    before=before, target=target)
            with queue_lock:
                results.append(result)
            if on_result is not None:
                on_result(result)

    threads = [threading.Thread(target=worker, args=(lane,), daemon=True) for lane in lanes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


class _Locked:
    """The store behind one lock; SQLite writes from two lanes stay serialized."""

    def __init__(self, store: RvolStore, lock: threading.Lock):
        self._store, self._lock = store, lock

    def put(self, record: SessionRecord, **kwargs: Any) -> str:
        with self._lock:
            return self._store.put(record, **kwargs)


def append_session(lane: Lane, symbol: str, code: str, exchange: str, *, store: RvolStore,
                   session: date, max_pages: int = 8) -> SymbolResult:
    """The daily step: one session's row, taken after the 09:30 bar is complete (>= 09:31 ET)."""
    return collect_symbol(lane, symbol, code, exchange, store=store,
                          before=session + _ONE_DAY, target=1, max_pages=max_pages)


from datetime import timedelta as _td  # noqa: E402

_ONE_DAY = _td(days=1)
