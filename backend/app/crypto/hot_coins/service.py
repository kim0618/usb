"""One scan at a time, per process, and never more often than the floor allows.

Two browser tabs, or a double click that slipped past the button's own guard, must still cost
Binance one scan. A request that arrives while a scan is running waits for that scan and gets its
answer (`coalesced`). A request that arrives within `min_interval_s` of the last success gets
that success back (`cached`) instead of a new scan. A failed scan changes nothing that was held.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Callable

from .lock import ExposureSource, GuardedOutcome, guarded_scan
from .scanner import HotScanner, HttpTransport, ScanResult

MIN_INTERVAL_S = 10.0

#: V0 has no HOT order path, so the server never holds a current HOT symbol and never reads an
#: account. Phase 2 replaces both with the HOT symbol it trades and an account-backed source.
CURRENT_HOT_SYMBOL: str | None = None
EXPOSURE_SOURCE: ExposureSource | None = None


@dataclass(frozen=True)
class ServiceAnswer:
    outcome: GuardedOutcome
    cached: bool
    coalesced: bool

    @property
    def result(self) -> ScanResult | None:
        return self.outcome.result  # type: ignore[return-value]


class HotCoinService:
    def __init__(self, scanner_factory: Callable[[], HotScanner] | None = None, *,
                 min_interval_s: float = MIN_INTERVAL_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._factory = scanner_factory or (lambda: HotScanner(HttpTransport()))
        self._scanner: HotScanner | None = None
        self._min_interval_s = min_interval_s
        self._clock = clock
        self._lock = threading.Lock()
        self._inflight: Future[GuardedOutcome] | None = None
        self._last: GuardedOutcome | None = None
        self._last_at: float | None = None

    def _scanner_instance(self) -> HotScanner:
        if self._scanner is None:
            self._scanner = self._factory()
        return self._scanner

    def scan(self) -> ServiceAnswer:
        with self._lock:
            waiting = self._inflight
            if waiting is None:
                if (self._last is not None and self._last_at is not None
                        and self._clock() - self._last_at < self._min_interval_s):
                    return ServiceAnswer(self._last, cached=True, coalesced=False)
                mine: Future[GuardedOutcome] = Future()
                self._inflight = mine
        if waiting is not None:
            return ServiceAnswer(waiting.result(), cached=False, coalesced=True)
        try:
            outcome = guarded_scan(CURRENT_HOT_SYMBOL, EXPOSURE_SOURCE,
                                   self._scanner_instance().scan)
        except BaseException as exc:
            mine.set_exception(exc)
            with self._lock:
                self._inflight = None
            raise
        with self._lock:
            self._inflight = None
            if outcome.applied:
                self._last = outcome
                self._last_at = self._clock()
        mine.set_result(outcome)
        return ServiceAnswer(outcome, cached=False, coalesced=False)


__all__ = ["HotCoinService", "ServiceAnswer", "MIN_INTERVAL_S"]
