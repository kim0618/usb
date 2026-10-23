"""Stand-ins for the common producer suite: a thread-safe virtual ET clock and a Kiwoom client that
goes through the lane's limiter exactly as ``KiwoomMarketDataClient._send_with_transport_retries``
does (``acquire`` immediately before the request). No network, nothing sleeps."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import threading
from zoneinfo import ZoneInfo

from app.ae_common.deadline import DeadlineRateLimiter
from app.integrations.kiwoom.client import KiwoomPage
from app.strategy_e_max_rt import finalizer as FZ

ET = ZoneInfo("America/New_York")
SESSION = date(2026, 9, 15)


class Clock:
    def __init__(self, start: datetime) -> None:
        self._now = start
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self._now

    def tick(self, seconds: float) -> None:
        with self._lock:
            self._now += timedelta(seconds=seconds)

    def monotonic(self) -> float:
        return self().timestamp()

    def at(self, moment: time, session: date = SESSION) -> datetime:
        return datetime.combine(session, moment, tzinfo=ET)


def at(moment: time, session: date = SESSION) -> datetime:
    return datetime.combine(session, moment, tzinfo=ET)


def minute_rows(session: date, last_minute: int, *, first_minute: int = 240, price: float = 10.0,
                volume: float = 1000.0, every: int = 1) -> list[dict]:
    """Kiwoom-shaped minute rows ``first..last``, newest first; prices drift so rows differ."""
    stamp = session.strftime("%Y%m%d")
    rows = []
    for minute in range(last_minute, first_minute - 1, -1):
        if (minute - first_minute) % every:
            continue
        p = price + (minute % 17) * 0.01
        rows.append({"bus_dt": stamp, "cntr_tm": f"{stamp}{minute // 60:02d}{minute % 60:02d}00",
                     "open_pric": f"{p:.4f}", "high_pric": f"{p + 0.2:.4f}", "low_pric": f"{p - 0.2:.4f}",
                     "cur_prc": f"{p + 0.1:.4f}", "trde_qty": f"{volume + minute:.0f}"})
    return rows


@dataclass
class LimitedClient:
    """One page per request; ``limiter.acquire()`` first, then the call takes ``seconds`` of clock."""

    pages: Mapping[str, list[dict]]
    clock: Clock
    limiter: DeadlineRateLimiter
    seconds: float = 0.2
    sent: list[tuple[datetime, str, str]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    fail: frozenset[str] = frozenset()

    def request(self, api_id: str, path: str, body: Mapping[str, str], continuation=None) -> KiwoomPage:
        self.limiter.acquire()
        symbol = str(body["stk_cd"])
        with self._lock:
            self.sent.append((self.clock(), api_id, symbol))
        self.clock.tick(self.seconds)
        if symbol in self.fail:
            from app.core.exceptions import MarketDataError
            from app.ae_common.sweep import REJECTED_MESSAGE
            raise MarketDataError("MARKET_DATA_UNAVAILABLE", REJECTED_MESSAGE,
                                  provider="KIWOOM", error_category="SOURCE_REFUSAL")    # the source refusing it
        return KiwoomPage(body={"result_list": list(self.pages.get(symbol, []))}, continuation=False, next_key=None)


def limiter(clock: Clock, rate: float = 4.9) -> DeadlineRateLimiter:
    return DeadlineRateLimiter(rate, clock, monotonic=clock.monotonic, sleeper=clock.tick)


def lanes(pages: Mapping[str, list[dict]], clock: Clock, *, seconds: float = 0.2, fail: Sequence[str] = ()):
    limiters = [limiter(clock), limiter(clock)]
    clients = [LimitedClient(pages, clock, limiters[0], seconds, fail=frozenset(fail)),
               LimitedClient(pages, clock, limiters[1], seconds, fail=frozenset(fail))]
    return (FZ.Lane("A", FZ.MINUTE_API, clients[0]), FZ.Lane("B", FZ.TICK_API, clients[1]),  # type: ignore[arg-type]
            clients, limiters)
