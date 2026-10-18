"""The producer's two chart lanes: one app key, a deadline limiter each, every HTTP status counted.

``CountingChartLaneClient`` is ``finalizer.ChartLaneClient`` (read-only allowlist, no order path)
whose HTTP transport is wrapped to count response statuses per call, so 429s and 5xx are counted
even when the client's bounded retry absorbs them. Nothing about the request changes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime
import threading
from typing import Any

from app.ae_common.deadline import DeadlineRateLimiter
from app.strategy_e_max_rt import finalizer as FZ

#: The per-lane rate production ran at (measured 5/s per API id; 429 seen at 12/s).
LANE_RATE = 4.9


class _CountingTransport:
    def __init__(self, inner: Any, statuses: Counter, lock: threading.Lock) -> None:
        self._inner, self._statuses, self._lock = inner, statuses, lock

    def post(self, *args: Any, **kwargs: Any):
        try:
            response = self._inner.post(*args, **kwargs)
        except Exception as error:
            with self._lock:
                self._statuses[type(error).__name__] += 1
            raise
        with self._lock:
            self._statuses[str(response.status_code)] += 1
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class CountingChartLaneClient(FZ.ChartLaneClient):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.http_statuses: Counter = Counter()
        self._status_lock = threading.Lock()
        self._http = _CountingTransport(self._http, self.http_statuses, self._status_lock)

    def stats(self) -> dict[str, Any]:
        with self._status_lock:
            statuses = dict(self.http_statuses)
        attempts = sum(self.request_counts.values())
        return {"http_attempts": attempts, "http_statuses": statuses,
                "status_429": statuses.get("429", 0),
                "status_5xx": sum(v for k, v in statuses.items() if k.isdigit() and int(k) >= 500),
                "transport_errors": sum(v for k, v in statuses.items() if not k.isdigit()),
                "order_request_count": self.order_request_count}


def build(*, base_url: str, auth: Any, clock: Callable[[], datetime], max_retries: int = 1,
          rate: float = LANE_RATE) -> tuple[FZ.Lane, FZ.Lane, list[CountingChartLaneClient], list[DeadlineRateLimiter]]:
    """Minute lane ``A`` (usa06011) and tick lane ``B`` (usa06010), the names production used."""
    from app.integrations.kiwoom.rate_limit import KiwoomRateLimits
    clients, limiters = [], []
    for _ in range(2):
        limits = KiwoomRateLimits()
        limiter = DeadlineRateLimiter(rate, clock)
        limits.chart = limiter                                # type: ignore[assignment]
        clients.append(CountingChartLaneClient(base_url=base_url, auth=auth, rate_limits=limits,
                                               max_retries=max_retries))
        limiters.append(limiter)
    return (FZ.Lane("A", FZ.MINUTE_API, clients[0]), FZ.Lane("B", FZ.TICK_API, clients[1]), clients, limiters)
