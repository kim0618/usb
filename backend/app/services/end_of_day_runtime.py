"""Single-process cadence owner for the closing review and Day 2 activation.

The minute driver beside this one owns regular-session position management, and
mixing the two would make one loop answer to two different clocks: the stop runs on
every completed bar, while the closing review runs once, at a moment the exchange
calendar decides. This owner therefore supplies only that second cadence. It is
deliberately process-local and sequential, exactly like the minute driver; running
multiple backend workers is outside this contract and would require distributed
ownership.

Nothing here is a general scheduler. It resolves one moment - the exchange's own
close, less the configured review window - and it does not know what a closing
review decides.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import logging

from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession, MinuteBar
from app.market.provider import MarketDataProvider
from app.services.end_of_day_lifecycle import EndOfDayLifecycleService, EndOfDayOutcome
from app.services.daily_performance import DailyPerformanceService, DailyPerformanceUnavailable
from app.services.simulation_runtime import SimulationRuntimeContext

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
ProviderFactory = Callable[[], MarketDataProvider]


class EndOfDayPositionRuntime:
    """Activate Day 2 at the open and run the closing review before the close."""

    def __init__(
        self,
        runtime: SimulationRuntimeContext,
        provider_factory: ProviderFactory,
        *,
        clock: Clock = lambda: datetime.now(timezone.utc),
        calendar: MarketCalendar | None = None,
        lifecycle: EndOfDayLifecycleService | None = None,
        daily_performance: DailyPerformanceService | None = None,
    ) -> None:
        self.runtime = runtime
        self._provider_factory = provider_factory
        self._provider: MarketDataProvider | None = None
        self._clock = clock
        self._calendar = calendar or MarketCalendar()
        self._lifecycle = lifecycle or EndOfDayLifecycleService(runtime, calendar=self._calendar)
        self._daily_performance = daily_performance or DailyPerformanceService(runtime, calendar=self._calendar)
        self._run_lock = asyncio.Lock()
        self._stop = asyncio.Event()

    async def run_once(self, *, as_of: datetime | None = None) -> tuple[EndOfDayOutcome, ...]:
        """Run one non-overlapping tick; symbol acquisition failures are isolated."""
        if self._stop.is_set():
            # A shutdown that has been asked for must not start selling anything new.
            return ()
        if self._run_lock.locked():
            logger.warning("EOD TICK SKIPPED: previous tick is still running")
            return ()
        async with self._run_lock:
            now = as_of or self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("end-of-day clock must be timezone-aware")
            local_now = now.astimezone(self._calendar.timezone)
            session = self._calendar.session(local_now.date())
            if session is None or now < session.market_open:
                # Weekends, holidays, and pre-open time have no EOD work.
                return ()
            if now > session.market_close:
                await self._record_daily_performance(session.market_open, session.market_close,
                                                     session.session_date, now)
                return ()
            if not self.runtime.broker.get_positions():
                return ()
            outcomes = list(self._lifecycle.activate_day2(as_of=now))
            review_at = self._lifecycle.review_at(session.session_date)
            if review_at is None or now < review_at:
                return tuple(outcomes)
            outcomes.extend(await self._review(session.market_open, now))
            for outcome in outcomes:
                logger.info("EOD OUTCOME: %s %s (%s)", outcome.symbol,
                            outcome.action.value, outcome.reason)
            return tuple(outcomes)

    async def _record_daily_performance(self, market_open: datetime, market_close: datetime,
                                        trading_date: date, now: datetime) -> None:
        """Record post-close equity only when every actual position has a valid mark."""
        if self._daily_performance.has_snapshot(trading_date):
            return
        positions = self.runtime.broker.get_positions()
        marks = {}
        if positions:
            if self._provider is None:
                self._provider = self._provider_factory()
            for position in positions:
                try:
                    bars = self._provider.get_minute_bars(
                        [position.symbol], market_open, market_close, MarketSession.REGULAR,
                    )
                except Exception:
                    logger.exception("DAILY PERFORMANCE MARKET DATA FAILED: %s", position.symbol)
                    return
                valid = [
                    bar for bar in bars
                    if bar.symbol == position.symbol and bar.session is MarketSession.REGULAR
                    and market_open <= bar.timestamp < market_close and bar.available_at <= now
                ]
                if not valid:
                    logger.warning("DAILY PERFORMANCE MARK UNAVAILABLE: %s", position.symbol)
                    return
                marks[position.symbol] = Decimal(str(max(valid, key=lambda bar: bar.timestamp).close))
        try:
            result = self._daily_performance.record(trading_date, marks, as_of=now)
        except DailyPerformanceUnavailable as error:
            logger.warning("DAILY PERFORMANCE DEFERRED: %s", error)
            return
        if result.created:
            logger.info("DAILY PERFORMANCE RECORDED: %s equity=%s",
                        trading_date, result.snapshot.closing_equity)

    async def _review(self, market_open: datetime, now: datetime) -> tuple[EndOfDayOutcome, ...]:
        # Construction stays lazy so an empty account performs no Kiwoom auth or
        # market-data work.  The configured factory remains the sole provider seam.
        if self._provider is None:
            self._provider = self._provider_factory()
        bars_by_symbol: dict[str, tuple[MinuteBar, ...]] = {}
        for position in self.runtime.broker.get_positions():
            try:
                # MarketDataProvider is intentionally synchronous across the
                # application; keep that established boundary here.
                bars = self._provider.get_minute_bars(
                    [position.symbol], market_open, now, MarketSession.REGULAR,
                )
            except Exception:
                # One symbol's data failure must not decide the whole book's night.
                logger.exception("EOD MARKET DATA FAILED: %s", position.symbol)
                continue
            bars_by_symbol[position.symbol] = tuple(bar for bar in bars if bar.available_at <= now)
        if not bars_by_symbol:
            return ()
        # The lifecycle service remains the only business and execution owner.
        return self._lifecycle.review(bars_by_symbol, as_of=now,
                                      symbols=frozenset(bars_by_symbol))

    async def run(self) -> None:
        """Evaluate immediately, then at each wall-clock completed-minute boundary."""
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("EOD TICK FAILED; retrying at next minute boundary")
            now = self._clock()
            boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            delay = max(0.0, (boundary - now).total_seconds())
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()


_owner: EndOfDayPositionRuntime | None = None
_task: asyncio.Task[None] | None = None


def get_end_of_day_runtime() -> EndOfDayPositionRuntime | None:
    return _owner


def start_end_of_day_runtime(
    runtime: SimulationRuntimeContext, provider_factory: ProviderFactory,
) -> asyncio.Task[None]:
    """Start exactly one task in this backend process; duplicate starts are idempotent."""
    global _owner, _task
    if _task is not None and not _task.done():
        return _task
    _owner = EndOfDayPositionRuntime(runtime, provider_factory)
    _task = asyncio.create_task(_owner.run(), name="end-of-day-management")
    return _task


async def stop_end_of_day_runtime() -> None:
    """Cooperatively stop and await the owner before broker ownership is cleared.

    The holder is released before the task is awaited, for the same reason the
    minute driver releases its own: a tick that died is still worth raising, but it
    must not leave this process pointing at an owner bound to a broker the shutdown
    is about to release.
    """
    global _owner, _task
    owner, task = _owner, _task
    _owner = None
    _task = None
    if owner is not None:
        owner.stop()
    if task is not None:
        try:
            await task
        except asyncio.CancelledError:
            pass
