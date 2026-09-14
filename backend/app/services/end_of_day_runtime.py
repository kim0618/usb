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
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import logging

from app.core.exceptions import MarketDataError
from app.market.calendar import MarketCalendar, TradingSessionWindow
from app.market.domain import MarketSession, MinuteBar
from app.market.provider import MarketDataProvider
from app.services.end_of_day_lifecycle import (
    EndOfDayAction, EndOfDayLifecycleService, EndOfDayOutcome,
)
from app.services.exchange_authority import AUTHORITY_FAILURE_CODES, bind_open_position
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
                # Weekends, holidays, and pre-open time have no review; a position
                # already past its last holding moment is still signalled to leave.
                return self._overdue(now)
            if now > session.market_close:
                outcomes = self._overdue(now)
                await self._record_daily_performance(session.market_open, session.market_close,
                                                     session.session_date, now)
                return outcomes
            if not self.runtime.broker.get_positions():
                return ()
            outcomes = list(self._lifecycle.enforce_holding_limit(as_of=now, overdue_only=True))
            outcomes.extend(self._settle_overdue(session, now))
            outcomes.extend(self._lifecycle.activate_day2(as_of=now))
            review_at = self._lifecycle.review_at(session.session_date)
            if review_at is None or now < review_at:
                return tuple(outcomes)
            reviewed, symbols = self._review(session, now)
            outcomes.extend(reviewed)
            # A position the review could not reach this tick is still closed on its
            # last holding day: signalled without prices, sold by the minute driver.
            outcomes.extend(self._lifecycle.enforce_holding_limit(as_of=now, exclude=symbols))
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
                    # The closing mark is read on the trade's own exchange, exactly as
                    # the review and the minute driver read it.
                    bind_open_position(self._provider, self.runtime, position.symbol)
                    bars = self._provider.get_minute_bars(
                        [position.symbol], market_open, market_close, MarketSession.REGULAR,
                    )
                except MarketDataError as error:
                    if error.code in AUTHORITY_FAILURE_CODES:
                        logger.critical("DAILY PERFORMANCE MARK UNAVAILABLE: %s %s: %s",
                                        position.symbol, error.code, error)
                    else:
                        logger.exception("DAILY PERFORMANCE MARKET DATA FAILED: %s", position.symbol)
                    return
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

    def _overdue(self, now: datetime) -> tuple[EndOfDayOutcome, ...]:
        """Signal the mandatory Day 2 close for positions past their last holding moment."""
        if not self.runtime.broker.get_positions():
            return ()
        return self._lifecycle.enforce_holding_limit(as_of=now)

    def _acquire(self, symbols: Iterable[str], window: TradingSessionWindow, now: datetime,
                 ) -> tuple[dict[str, tuple[MinuteBar, ...]], list[EndOfDayOutcome]]:
        """Completed regular bars per symbol, read on its trade's exchange from its cursor."""
        # Construction stays lazy so an empty account performs no Kiwoom auth or
        # market-data work.  The configured factory remains the sole provider seam.
        if self._provider is None:
            self._provider = self._provider_factory()
        bars_by_symbol: dict[str, tuple[MinuteBar, ...]] = {}
        unavailable: list[EndOfDayOutcome] = []
        for symbol in symbols:
            try:
                # The venue is the trade's own scanner candidate, read from the
                # database, never this owner's provider memory or a default.
                bind_open_position(self._provider, self.runtime, symbol)
                # The read starts where the stop's durable cursor needs it to, so a
                # night reviewed after a restart still sees the bars it never tested.
                start = self._lifecycle.protection.fetch_start(symbol, window)
                # MarketDataProvider is intentionally synchronous across the
                # application; keep that established boundary here.
                bars = self._provider.get_minute_bars([symbol], start, now, MarketSession.REGULAR)
            except MarketDataError as error:
                if error.code not in AUTHORITY_FAILURE_CODES:
                    logger.exception("EOD MARKET DATA FAILED: %s", symbol)
                    continue
                # A night that cannot be reviewed on the right listing is reported,
                # never decided on a guessed venue's prices.
                logger.critical("EOD PROTECTION UNAVAILABLE: %s %s: %s", symbol, error.code, error)
                unavailable.append(EndOfDayOutcome(
                    symbol, EndOfDayAction.PROTECTION_UNAVAILABLE, f"{error.code}: {error}"))
                continue
            except Exception:
                # One symbol's data failure must not decide the whole book's night.
                logger.exception("EOD MARKET DATA FAILED: %s", symbol)
                continue
            bars_by_symbol[symbol] = tuple(bar for bar in bars if bar.available_at <= now)
        return bars_by_symbol, unavailable

    def _settle_overdue(self, session: TradingSessionWindow,
                        now: datetime) -> tuple[EndOfDayOutcome, ...]:
        """Settle reductions an earlier session decided but could not fill."""
        due = self._lifecycle.overdue_reductions(as_of=now)
        if not due:
            return ()
        bars_by_symbol, unavailable = self._acquire(sorted(due), session, now)
        if not bars_by_symbol:
            return tuple(unavailable)
        return tuple(unavailable) + self._lifecycle.settle_overdue_reductions(
            bars_by_symbol, as_of=now)

    def _review(self, session: TradingSessionWindow, now: datetime,
                ) -> tuple[tuple[EndOfDayOutcome, ...], frozenset[str]]:
        """Review every held symbol whose bars could be read; name the ones reviewed."""
        bars_by_symbol, unavailable = self._acquire(
            [position.symbol for position in self.runtime.broker.get_positions()], session, now)
        if not bars_by_symbol:
            return tuple(unavailable), frozenset()
        # The lifecycle service remains the only business and execution owner.
        reviewed = frozenset(bars_by_symbol)
        return tuple(unavailable) + self._lifecycle.review(
            bars_by_symbol, as_of=now, symbols=reviewed), reviewed

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
