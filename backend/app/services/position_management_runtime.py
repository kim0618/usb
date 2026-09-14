"""Single-process cadence owner for durable position management.

The backend has no general-purpose production scheduler: scanner runs are explicit,
bounded commands.  This owner therefore supplies only the missing minute cadence.
It is deliberately process-local and sequential; running multiple backend workers is
outside this contract and would require distributed ownership.

A session's final regular bar completes at the close itself, and a tick never lands
exactly there, so every in-session tick is too early to read it. Once the session
is over this owner therefore makes one final flush of it: the calendar names the
final bar, only regular bars are read, and the flush is repeated only for a symbol
whose final bar the provider has not published yet, a bounded number of times.

Every read starts where the symbol's durable protection cursor needs it to: the
session's open normally, or an earlier session's open when bars of that session
were never tested. The lifecycle then tests each bar after the cursor in order, so
the flush is the same catch-up an in-session tick makes, run after the close.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
import logging

from app.broker.domain import SimPosition
from app.core.exceptions import MarketDataError
from app.market.calendar import MarketCalendar, TradingSessionWindow
from app.market.domain import MarketSession, MinuteBar
from app.market.provider import MarketDataProvider
from app.services.exchange_authority import AUTHORITY_FAILURE_CODES, bind_open_position
from app.services.position_lifecycle import (
    PositionAction, PositionLifecycleService, PositionOutcome,
)
from app.services.simulation_runtime import SimulationRuntimeContext

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
ProviderFactory = Callable[[], MarketDataProvider]

# A final bar the provider has not published after this many flushes is reported
# rather than polled for the rest of the night.
FINAL_FLUSH_ATTEMPTS = 10


class PositionManagementRuntime:
    """Acquire completed regular bars and invoke the existing lifecycle service."""

    def __init__(
        self,
        runtime: SimulationRuntimeContext,
        provider_factory: ProviderFactory,
        *,
        clock: Clock = lambda: datetime.now(timezone.utc),
        calendar: MarketCalendar | None = None,
        lifecycle: PositionLifecycleService | None = None,
    ) -> None:
        self.runtime = runtime
        self._provider_factory = provider_factory
        self._provider: MarketDataProvider | None = None
        self._clock = clock
        self._calendar = calendar or MarketCalendar()
        self._lifecycle = lifecycle or PositionLifecycleService(runtime, calendar=self._calendar)
        self._run_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        # The session last flushed, the symbols whose final bar it has read, and how
        # many flushes it took. Process-local on purpose: a restart flushes again,
        # which the lifecycle makes harmless.
        self._flush_session: date | None = None
        self._flushed: set[str] = set()
        self._flush_attempts = 0

    async def run_once(self, *, as_of: datetime | None = None) -> tuple[PositionOutcome, ...]:
        """Run one non-overlapping tick; symbol acquisition failures are isolated."""
        if self._run_lock.locked():
            logger.warning("POSITION TICK SKIPPED: previous tick is still running")
            return ()
        async with self._run_lock:
            now = as_of or self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("position management clock must be timezone-aware")
            positions = self.runtime.broker.get_positions()
            if not positions:
                return ()
            local_now = now.astimezone(self._calendar.timezone)
            session = self._calendar.session(local_now.date())
            if session is None or not (session.market_open < now <= session.market_close):
                return self._final_flush(positions, now)

            bars_by_symbol, unprotected = self._acquire(positions, session, now, now)
            if not bars_by_symbol:
                return tuple(unprotected)
            # The lifecycle service remains the only business and execution owner.
            outcomes = self._lifecycle.evaluate(
                bars_by_symbol, as_of=now, symbols=frozenset(bars_by_symbol)
            )
            for outcome in outcomes:
                logger.info("POSITION OUTCOME: %s %s (%s)", outcome.symbol,
                            outcome.action.value, outcome.reason)
            return tuple(unprotected) + tuple(outcomes)

    def _acquire(self, positions: Sequence[SimPosition], window: TradingSessionWindow,
                 end: datetime, now: datetime,
                 ) -> tuple[dict[str, tuple[MinuteBar, ...]], list[PositionOutcome]]:
        """Completed regular bars per held symbol, each read on its trade's exchange.

        Each read starts where that symbol's protection cursor needs it to, which is
        ``window``'s open unless bars of an earlier session were never tested.
        """
        # Construction stays lazy so an empty account performs no Kiwoom auth or
        # market-data work.  The configured factory remains the sole provider seam.
        if self._provider is None:
            self._provider = self._provider_factory()
        bars_by_symbol: dict[str, tuple[MinuteBar, ...]] = {}
        unprotected: list[PositionOutcome] = []
        for position in positions:
            try:
                # The venue is the trade's own scanner candidate, read from the
                # database every tick, so a restart or a separate provider
                # instance cannot route a held symbol to the wrong listing.
                bind_open_position(self._provider, self.runtime, position.symbol)
                start = self._lifecycle.fetch_start(position.symbol, window)
                # MarketDataProvider is intentionally synchronous across the
                # application; keep that established boundary here.
                bars = self._provider.get_minute_bars(
                    [position.symbol], start, end, MarketSession.REGULAR,
                )
            except MarketDataError as error:
                if error.code not in AUTHORITY_FAILURE_CODES:
                    logger.exception("POSITION MARKET DATA FAILED: %s", position.symbol)
                    continue
                # Guessing a venue would read another listing's prices into this
                # stop. A position whose stop cannot be read is reported as such,
                # never skipped in silence.
                logger.critical("POSITION PROTECTION UNAVAILABLE: %s %s: %s",
                                position.symbol, error.code, error)
                unprotected.append(PositionOutcome(
                    position.symbol, PositionAction.PROTECTION_UNAVAILABLE,
                    f"{error.code}: {error}"))
                continue
            except Exception:
                logger.exception("POSITION MARKET DATA FAILED: %s", position.symbol)
                continue
            bars_by_symbol[position.symbol] = tuple(
                bar for bar in bars if bar.available_at <= now
            )
        return bars_by_symbol, unprotected

    def _completed_session(self, now: datetime) -> TradingSessionWindow | None:
        """The last XNYS session that has closed, while no session is in progress."""
        day = now.astimezone(self._calendar.timezone).date()
        today = self._calendar.session(day)
        if today is not None and now > today.market_close:
            return today
        if today is not None and now > today.market_open:
            return None
        return self._calendar.session(self._calendar.previous_trading_day(day))

    def _final_flush(self, positions: Sequence[SimPosition],
                     now: datetime) -> tuple[PositionOutcome, ...]:
        """Test every held stop through the closed session's final regular bar, once.

        This is the in-session catch-up run after the close: every bar the cursor has
        not reached is tested in order, the final bar included. A symbol counts as
        flushed once its final bar has been read.
        """
        window = self._completed_session(now)
        if window is None:
            return ()
        if window.session_date != self._flush_session:
            self._flush_session, self._flushed, self._flush_attempts = (
                window.session_date, set(), 0)
        pending = [position for position in positions if position.symbol not in self._flushed]
        if not pending or self._flush_attempts >= FINAL_FLUSH_ATTEMPTS:
            return ()
        self._flush_attempts += 1
        final_at = window.market_close - timedelta(minutes=1)
        acquired, unprotected = self._acquire(pending, window, window.market_close, now)
        read = {symbol: bars for symbol, bars in acquired.items() if bars}
        outcomes = (self._lifecycle.evaluate(read, as_of=now, symbols=frozenset(read))
                    if read else ())
        self._flushed |= {
            symbol for symbol, bars in acquired.items()
            if any(bar.timestamp == final_at and bar.session is MarketSession.REGULAR
                   for bar in bars)
        }
        missing = sorted(position.symbol for position in pending
                         if position.symbol not in self._flushed)
        if missing and self._flush_attempts >= FINAL_FLUSH_ATTEMPTS:
            logger.critical("FINAL BAR PROTECTION UNAVAILABLE: %s %s", window.session_date,
                            ",".join(missing))
        for outcome in outcomes:
            logger.info("POSITION FINAL-BAR OUTCOME: %s %s (%s)", outcome.symbol,
                        outcome.action.value, outcome.reason)
        return tuple(unprotected) + tuple(outcomes)

    async def run(self) -> None:
        """Evaluate immediately, then at each wall-clock completed-minute boundary."""
        while not self._stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("POSITION TICK FAILED; retrying at next minute boundary")
            now = self._clock()
            boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            delay = max(0.0, (boundary - now).total_seconds())
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()


_owner: PositionManagementRuntime | None = None
_task: asyncio.Task[None] | None = None


def get_position_management_runtime() -> PositionManagementRuntime | None:
    return _owner


def start_position_management_runtime(
    runtime: SimulationRuntimeContext, provider_factory: ProviderFactory,
) -> asyncio.Task[None]:
    """Start exactly one task in this backend process; duplicate starts are idempotent."""
    global _owner, _task
    if _task is not None and not _task.done():
        return _task
    _owner = PositionManagementRuntime(runtime, provider_factory)
    _task = asyncio.create_task(_owner.run(), name="position-management")
    return _task


async def stop_position_management_runtime() -> None:
    """Cooperatively stop and await the owner before broker ownership is cleared.

    The holder is released before the task is awaited, not after: a tick that died
    is still worth raising, but it must not leave this process pointing at an owner
    bound to a broker the shutdown is about to release.
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
