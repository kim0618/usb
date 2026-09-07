from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest

from app.services.position_management_runtime import (
    PositionManagementRuntime, get_position_management_runtime,
    start_position_management_runtime, stop_position_management_runtime,
)
from app.services.simulation_runtime import SimulationRuntimeContext
from tests.market_fixtures import minute_bar


ET = ZoneInfo("America/New_York")
AS_OF = datetime(2024, 6, 18, 10, 0, tzinfo=ET)


class Broker:
    def __init__(self, *symbols: str) -> None:
        self.positions = tuple(SimpleNamespace(symbol=symbol) for symbol in symbols)

    def get_positions(self):
        return self.positions


class Lifecycle:
    def __init__(self) -> None:
        self.calls = []

    def evaluate(self, bars, *, as_of, symbols=None):
        self.calls.append((bars, as_of, symbols))
        return ()


class Provider:
    def __init__(self, *, failing=()) -> None:
        self.calls = []
        self.failing = set(failing)

    def get_minute_bars(self, symbols, start=None, end=None, session=None):
        symbol = symbols[0]
        self.calls.append((symbol, start, end, session))
        if symbol in self.failing:
            raise RuntimeError("market data unavailable")
        future = minute_bar(symbol, 59).model_copy(
            update={"available_at": AS_OF + timedelta(minutes=1)}
        )
        return [minute_bar(symbol, 58), future]


def owner(broker, provider_factory, lifecycle):
    return PositionManagementRuntime(
        SimpleNamespace(broker=broker), provider_factory,
        clock=lambda: AS_OF, lifecycle=lifecycle,
    )


@pytest.mark.asyncio
async def test_no_position_fast_path_builds_no_provider_and_evaluates_nothing():
    built = []
    lifecycle = Lifecycle()
    runtime = owner(Broker(), lambda: built.append(True), lifecycle)

    assert await runtime.run_once(as_of=AS_OF) == ()
    assert built == [] and lifecycle.calls == []


@pytest.mark.asyncio
async def test_completed_bars_are_acquired_per_symbol_and_future_bars_are_excluded():
    provider = Provider()
    lifecycle = Lifecycle()
    runtime = owner(Broker("AAA", "BBB"), lambda: provider, lifecycle)

    await runtime.run_once(as_of=AS_OF)

    assert [call[0] for call in provider.calls] == ["AAA", "BBB"]
    bars, as_of, symbols = lifecycle.calls[0]
    assert as_of == AS_OF and symbols == frozenset({"AAA", "BBB"})
    assert {symbol: [bar.timestamp.minute for bar in values]
            for symbol, values in bars.items()} == {"AAA": [58], "BBB": [58]}


@pytest.mark.asyncio
async def test_one_symbol_market_failure_does_not_evaluate_that_symbol():
    provider = Provider(failing={"AAA"})
    lifecycle = Lifecycle()
    runtime = owner(Broker("AAA", "BBB"), lambda: provider, lifecycle)

    await runtime.run_once(as_of=AS_OF)

    bars, _, symbols = lifecycle.calls[0]
    assert set(bars) == {"BBB"} and symbols == frozenset({"BBB"})


@pytest.mark.asyncio
async def test_overlapping_run_once_is_skipped():
    lifecycle = Lifecycle()
    provider = Provider()
    runtime = owner(Broker("AAA"), lambda: provider, lifecycle)
    await runtime._run_lock.acquire()
    try:
        assert await runtime.run_once(as_of=AS_OF) == ()
    finally:
        runtime._run_lock.release()
    assert lifecycle.calls == [] and provider.calls == []


@pytest.mark.asyncio
async def test_process_holder_starts_once_and_stops_cleanly():
    runtime = SimulationRuntimeContext(Broker(), account_id=1, session_factory=lambda: None)
    first = start_position_management_runtime(runtime, lambda: Provider())
    second = start_position_management_runtime(runtime, lambda: Provider())
    assert first is second and get_position_management_runtime() is not None
    await stop_position_management_runtime()
    assert first.done() and get_position_management_runtime() is None


# 2024-06-18 is a regular XNYS session; 06-19 is the Juneteenth holiday.
PREMARKET = datetime(2024, 6, 18, 9, 0, tzinfo=ET)
POSTMARKET = datetime(2024, 6, 18, 16, 30, tzinfo=ET)
HOLIDAY = datetime(2024, 6, 19, 11, 0, tzinfo=ET)
WEEKEND = datetime(2024, 6, 22, 11, 0, tzinfo=ET)


@pytest.mark.parametrize("as_of", [PREMARKET, POSTMARKET, HOLIDAY, WEEKEND],
                         ids=["premarket", "postmarket", "holiday", "weekend"])
@pytest.mark.asyncio
async def test_outside_the_regular_session_nothing_is_acquired_or_evaluated(as_of):
    """The cadence manages the regular session only; other hours are not this loop's.

    The gate is what keeps an unattended process from authenticating and
    requesting market data around the clock, so it is asserted before the
    provider exists rather than after the request comes back empty.
    """
    built = []
    lifecycle = Lifecycle()
    runtime = owner(Broker("AAA"), lambda: built.append(True), lifecycle)

    assert await runtime.run_once(as_of=as_of) == ()
    assert built == [] and lifecycle.calls == []
