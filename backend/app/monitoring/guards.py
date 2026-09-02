"""Deterministic freshness, execution-failure, and invariant guards."""

from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal

from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession
from app.monitoring.config import OperationsConfig
from app.monitoring.domain import FailureCode, FreshnessResult
from app.strategy.lifecycle import StrategyPhase, StrategyState


class MarketDataStalenessGuard:
    def __init__(self, config: OperationsConfig | None = None,
                 calendar: MarketCalendar | None = None) -> None:
        self.config = config or OperationsConfig()
        self.calendar = calendar or MarketCalendar()

    def check(self, *, market_as_of: datetime, latest_data_available_at: datetime | None,
              session: MarketSession, expected_market_activity: bool | None = None) -> FreshnessResult:
        if market_as_of.tzinfo is None or market_as_of.utcoffset() is None:
            raise ValueError("market_as_of must be timezone-aware")
        window = self.calendar.session(market_as_of.astimezone(self.calendar.timezone).date())
        regular_active = bool(window and window.market_open <= market_as_of.astimezone(self.calendar.timezone)
                              <= window.market_close and session is MarketSession.REGULAR)
        expected = regular_active if expected_market_activity is None else expected_market_activity
        if session is MarketSession.PREMARKET and not self.config.monitor_premarket:
            expected = False
        if not expected:
            return FreshnessResult(True, 0.0, False)
        if latest_data_available_at is None:
            return FreshnessResult(False, float("inf"), True, FailureCode.MARKET_DATA_UNAVAILABLE)
        if latest_data_available_at.tzinfo is None or latest_data_available_at.utcoffset() is None:
            return FreshnessResult(False, float("inf"), True, FailureCode.MARKET_DATA_INVALID)
        stale = max(0.0, (market_as_of - latest_data_available_at).total_seconds())
        healthy = stale <= self.config.market_data_stale_seconds
        return FreshnessResult(healthy, stale, True,
                               None if healthy else FailureCode.MARKET_DATA_STALE)


INFRASTRUCTURE_FAILURES = {FailureCode.EXECUTION_UNAVAILABLE, FailureCode.EXECUTION_TIMEOUT,
                           FailureCode.EXECUTION_REJECTED}


class ExecutionFailureTracker:
    """Sliding-window tracker. Success does not erase still-recent failures."""

    def __init__(self, config: OperationsConfig | None = None) -> None:
        self.config = config or OperationsConfig()
        self._failures: deque[tuple[datetime, FailureCode]] = deque()

    def record(self, code: FailureCode, occurred_at: datetime, *, infrastructure: bool = True) -> int:
        if infrastructure and code in INFRASTRUCTURE_FAILURES:
            self._failures.append((occurred_at, code))
        self._prune(occurred_at)
        return len(self._failures)

    def threshold_reached(self, now: datetime) -> bool:
        self._prune(now)
        return len(self._failures) >= self.config.execution_failure_threshold

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.config.execution_failure_window_seconds)
        while self._failures and self._failures[0][0] < cutoff:
            self._failures.popleft()


def runtime_invariant_errors(*, cash: Decimal, positions: tuple[object, ...],
                             strategy_states: tuple[StrategyState, ...]) -> tuple[str, ...]:
    errors: list[str] = []
    if cash < 0:
        errors.append("negative cash")
    for position in positions:
        if getattr(position, "quantity", Decimal("0")) < 0:
            errors.append(f"short position: {getattr(position, 'symbol', 'UNKNOWN')}")
    for state in strategy_states:
        if state.add_count > 1:
            errors.append(f"add_count > 1: {state.symbol}")
        if state.holding_day_number > 2:
            errors.append(f"invalid Day3: {state.symbol}")
        if state.phase is StrategyPhase.EXITED and any(
            getattr(p, "symbol", None) == state.symbol and getattr(p, "quantity", 0) > 0 for p in positions
        ):
            errors.append(f"terminal strategy with broker position: {state.symbol}")
    return tuple(errors)
