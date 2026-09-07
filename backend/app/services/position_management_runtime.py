"""Single-process cadence owner for durable position management.

The backend has no general-purpose production scheduler: scanner runs are explicit,
bounded commands.  This owner therefore supplies only the missing minute cadence.
It is deliberately process-local and sequential; running multiple backend workers is
outside this contract and would require distributed ownership.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import logging

from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession, MinuteBar
from app.market.provider import MarketDataProvider
from app.services.position_lifecycle import PositionLifecycleService, PositionOutcome
from app.services.simulation_runtime import SimulationRuntimeContext

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
ProviderFactory = Callable[[], MarketDataProvider]


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
                return ()

            # Construction stays lazy so an empty account performs no Kiwoom auth or
            # market-data work.  The configured factory remains the sole provider seam.
            if self._provider is None:
                self._provider = self._provider_factory()
            bars_by_symbol: dict[str, tuple[MinuteBar, ...]] = {}
            for position in positions:
                try:
                    # MarketDataProvider is intentionally synchronous across the
                    # application; keep that established boundary here.
                    bars = self._provider.get_minute_bars(
                        [position.symbol], session.market_open, now, MarketSession.REGULAR,
                    )
                except Exception:
                    logger.exception("POSITION MARKET DATA FAILED: %s", position.symbol)
                    continue
                bars_by_symbol[position.symbol] = tuple(
                    bar for bar in bars if bar.available_at <= now
                )
            if not bars_by_symbol:
                return ()
            # The lifecycle service remains the only business and execution owner.
            outcomes = self._lifecycle.evaluate(
                bars_by_symbol, as_of=now, symbols=frozenset(bars_by_symbol)
            )
            for outcome in outcomes:
                logger.info("POSITION OUTCOME: %s %s (%s)", outcome.symbol,
                            outcome.action.value, outcome.reason)
            return outcomes

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
