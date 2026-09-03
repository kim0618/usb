"""Small shared token-spacing limiter for Kiwoom request classes."""

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
import time


@dataclass
class RequestRateLimiter:
    requests_per_second: float
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    _next_allowed: float = field(default=0.0, init=False)
    _lock: Lock = field(default_factory=Lock, init=False)

    def acquire(self) -> None:
        interval = 1.0 / self.requests_per_second
        with self._lock:
            now = self.clock()
            delay = max(0.0, self._next_allowed - now)
            if delay:
                self.sleeper(delay)
                now = self.clock()
            self._next_allowed = max(now, self._next_allowed) + interval


class KiwoomRateLimits:
    """Central buckets; conservative query rate also covers the official peak limit."""

    def __init__(self, *, mock: bool = False) -> None:
        query_rate = 1.0 if mock else 3.0
        self.auth = RequestRateLimiter(1.0)
        self.query = RequestRateLimiter(query_rate)
        self.chart = RequestRateLimiter(query_rate)
        self.realtime_subscription = RequestRateLimiter(1.0)
