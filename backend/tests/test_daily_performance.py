"""Actual-book post-close daily performance accounting contract."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core import database
from app.api.router import daily_performance
from app.broker.domain import SimPosition
from app.core.config import get_settings
from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession, MinuteBar
from app.models.execution import ShadowTradeRecord
from app.models.simulation import AccountDailyPerformanceRecord
from app.repositories.simulation import SimulationStateRepository
from app.services.daily_performance import DailyPerformanceService, DailyPerformanceUnavailable
from app.services.end_of_day_runtime import EndOfDayPositionRuntime
from app.services.simulation_runtime import activate_operator_simulation_runtime, clear_active_sim_broker
from backend.tests.test_simulation_persistence_schema import REVISION, _migrated_engine

ET = ZoneInfo("America/New_York")
DAY1 = date(2026, 9, 8)
DAY2 = date(2026, 9, 9)
INITIAL = Decimal("7428.92")


@pytest.fixture(autouse=True)
def ownership():
    clear_active_sim_broker()
    yield
    clear_active_sim_broker()


@pytest.fixture
def book(tmp_path, monkeypatch):
    engine = _migrated_engine(tmp_path / "daily.sqlite3", monkeypatch, REVISION)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setenv("RUNTIME_PROFILE", "real_market_operator")
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "kiwoom")
    get_settings.cache_clear()
    with factory() as session:
        SimulationStateRepository(session).create_account(
            broker_type="SIM", account_key="operator", base_currency="USD",
            initial_cash=INITIAL, cash=INITIAL,
            created_at=datetime(2026, 9, 8, 9, 0, tzinfo=ET))
        session.commit()
    runtime = activate_operator_simulation_runtime(get_settings(), session_factory=factory)
    assert runtime is not None
    try:
        yield runtime, factory
    finally:
        engine.dispose()
        get_settings.cache_clear()


def set_cash(runtime, factory, amount: Decimal) -> None:  # type: ignore[no-untyped-def]
    runtime.broker.cash = amount
    with factory() as session:
        row = SimulationStateRepository(session).get_account_by_id(runtime.account_id)
        assert row is not None
        row.cash = amount
        session.commit()


def add_position(runtime, factory, symbol: str, quantity: Decimal, average: Decimal) -> None:  # type: ignore[no-untyped-def]
    position = SimPosition(
        symbol=symbol, quantity=quantity, average_price=average,
        cost_basis=quantity * average, realized_pnl=Decimal("0"),
        opened_at=datetime(2026, 9, 8, 10, tzinfo=ET),
        updated_at=datetime(2026, 9, 8, 10, tzinfo=ET))
    runtime.broker._positions[symbol] = position
    with factory() as session:
        SimulationStateRepository(session).save_position(runtime.account_id, position)
        session.commit()


def reactivate(factory):  # type: ignore[no-untyped-def]
    runtime = activate_operator_simulation_runtime(get_settings(), session_factory=factory)
    assert runtime is not None
    return runtime


def after_close(day: date = DAY1) -> datetime:
    close = MarketCalendar().regular_market_close(day)
    assert close is not None
    return close + timedelta(minutes=1)


def snapshot_count(factory) -> int:  # type: ignore[no-untyped-def]
    with factory() as session:
        return int(session.scalar(select(func.count()).select_from(AccountDailyPerformanceRecord)) or 0)


def test_first_day_uses_initial_cash_and_persists_cash_only_zero_day(book) -> None:
    runtime, factory = book
    result = DailyPerformanceService(runtime).record(DAY1, {}, as_of=after_close())
    assert result.created
    assert result.snapshot.opening_equity == INITIAL
    assert result.snapshot.closing_equity == INITIAL
    assert result.snapshot.position_market_value == 0
    assert result.snapshot.daily_pnl == 0 and result.snapshot.daily_return == 0
    assert snapshot_count(factory) == 1


@pytest.mark.parametrize(("closing", "expected_sign"), [
    (Decimal("7500.00"), 1), (Decimal("7300.00"), -1),
])
def test_profitable_and_losing_days_use_equity_delta(book, closing, expected_sign) -> None:  # type: ignore[no-untyped-def]
    runtime, factory = book
    set_cash(runtime, factory, closing)
    row = DailyPerformanceService(runtime).record(DAY1, {}, as_of=after_close()).snapshot
    assert row.daily_pnl == closing - INITIAL
    assert (row.daily_return > 0) is (expected_sign > 0)


def test_second_day_uses_prior_closing_equity(book) -> None:
    runtime, factory = book
    set_cash(runtime, factory, Decimal("7500"))
    service = DailyPerformanceService(runtime)
    service.record(DAY1, {}, as_of=after_close(DAY1))
    set_cash(runtime, factory, Decimal("7475"))
    second = service.record(DAY2, {}, as_of=after_close(DAY2)).snapshot
    assert second.opening_equity == Decimal("7500")
    assert second.daily_pnl == Decimal("-25")


def test_one_and_multiple_open_positions_use_only_supplied_regular_marks(book) -> None:
    runtime, factory = book
    add_position(runtime, factory, "AAPL", Decimal("2"), Decimal("100"))
    add_position(runtime, factory, "NVDA", Decimal("3"), Decimal("50"))
    row = DailyPerformanceService(runtime).record(
        DAY1, {"AAPL": Decimal("110"), "NVDA": Decimal("55")}, as_of=after_close()).snapshot
    assert row.position_market_value == Decimal("385")
    assert row.closing_equity == INITIAL + Decimal("385")


def test_missing_position_mark_creates_no_fake_snapshot(book) -> None:
    runtime, factory = book
    add_position(runtime, factory, "AAPL", Decimal("2"), Decimal("100"))
    with pytest.raises(DailyPerformanceUnavailable, match="marks"):
        DailyPerformanceService(runtime).record(DAY1, {}, as_of=after_close())
    assert snapshot_count(factory) == 0


def test_duplicate_invocation_and_restart_return_the_one_durable_row(book) -> None:
    runtime, factory = book
    first = DailyPerformanceService(runtime).record(DAY1, {}, as_of=after_close())
    second = DailyPerformanceService(runtime).record(DAY1, {}, as_of=after_close())
    clear_active_sim_broker()
    restarted = reactivate(factory)
    third = DailyPerformanceService(restarted).record(DAY1, {}, as_of=after_close())
    assert first.created and not second.created and not third.created
    assert first.snapshot.id == second.snapshot.id == third.snapshot.id
    assert snapshot_count(factory) == 1


@pytest.mark.parametrize("day", [date(2026, 9, 5), date(2026, 9, 7)])
def test_weekend_and_holiday_never_create_snapshot(book, day) -> None:  # type: ignore[no-untyped-def]
    runtime, factory = book
    with pytest.raises(DailyPerformanceUnavailable, match="trading session"):
        DailyPerformanceService(runtime).record(day, {}, as_of=datetime(2026, 9, 8, tzinfo=ET))
    assert snapshot_count(factory) == 0


def test_missing_previous_trading_day_blocks_a_false_multiday_delta(book) -> None:
    runtime, factory = book
    DailyPerformanceService(runtime).record(DAY1, {}, as_of=after_close(DAY1))
    with pytest.raises(DailyPerformanceUnavailable, match="previous trading-day"):
        DailyPerformanceService(runtime).record(date(2026, 9, 10), {}, as_of=after_close(date(2026, 9, 10)))
    assert snapshot_count(factory) == 1


class Tape:
    def __init__(self, bars=(), *, fail=False):  # type: ignore[no-untyped-def]
        self.bars, self.fail, self.calls = list(bars), fail, 0

    def get_minute_bars(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider failure")
        return self.bars


def minute(symbol: str, at: datetime, price: float, session: MarketSession) -> MinuteBar:
    return MinuteBar(symbol=symbol, timestamp=at, open=price, high=price, low=price, close=price,
                     volume=1, session=session, observed_at=at + timedelta(minutes=1),
                     available_at=at + timedelta(minutes=1))


@pytest.mark.asyncio
async def test_runtime_uses_last_regular_bar_and_ignores_pre_and_postmarket(book) -> None:
    runtime, factory = book
    add_position(runtime, factory, "AAPL", Decimal("2"), Decimal("100"))
    tape = Tape([
        minute("AAPL", datetime(2026, 9, 8, 8, tzinfo=ET), 1, MarketSession.PREMARKET),
        minute("AAPL", datetime(2026, 9, 8, 15, 58, tzinfo=ET), 109, MarketSession.REGULAR),
        minute("AAPL", datetime(2026, 9, 8, 15, 59, tzinfo=ET), 110, MarketSession.REGULAR),
        minute("AAPL", datetime(2026, 9, 8, 16, 1, tzinfo=ET), 999, MarketSession.POSTMARKET),
    ])
    await EndOfDayPositionRuntime(runtime, lambda: tape).run_once(as_of=after_close())
    with factory() as session:
        row = session.scalar(select(AccountDailyPerformanceRecord))
        assert row is not None and row.position_market_value == Decimal("220")


@pytest.mark.asyncio
async def test_runtime_data_failure_is_retryable_and_never_falls_back_to_average(book) -> None:
    runtime, factory = book
    add_position(runtime, factory, "AAPL", Decimal("2"), Decimal("100"))
    failed = EndOfDayPositionRuntime(runtime, lambda: Tape(fail=True))
    await failed.run_once(as_of=after_close())
    assert snapshot_count(factory) == 0
    good_tape = Tape([minute("AAPL", datetime(2026, 9, 8, 15, 59, tzinfo=ET), 110, MarketSession.REGULAR)])
    await EndOfDayPositionRuntime(runtime, lambda: good_tape).run_once(as_of=after_close() + timedelta(minutes=1))
    assert snapshot_count(factory) == 1


@pytest.mark.asyncio
async def test_runtime_supports_early_close_and_skips_non_sessions(book) -> None:
    runtime, factory = book
    early_day = date(2026, 11, 27)
    await EndOfDayPositionRuntime(runtime, lambda: Tape()).run_once(as_of=after_close(early_day))
    assert snapshot_count(factory) == 1
    for value in (datetime(2026, 11, 28, 17, tzinfo=ET), datetime(2026, 11, 26, 17, tzinfo=ET)):
        await EndOfDayPositionRuntime(runtime, lambda: Tape()).run_once(as_of=value)
    assert snapshot_count(factory) == 1


def test_shadow_rows_are_never_read_or_required(book) -> None:
    runtime, factory = book
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ShadowTradeRecord)) == 0
    DailyPerformanceService(runtime).record(DAY1, {}, as_of=after_close())
    assert snapshot_count(factory) == 1


@pytest.mark.asyncio
async def test_api_projects_actual_rows_recent_first_with_cumulative_pnl(book) -> None:
    runtime, factory = book
    set_cash(runtime, factory, Decimal("7500"))
    service = DailyPerformanceService(runtime)
    service.record(DAY1, {}, as_of=after_close(DAY1))
    set_cash(runtime, factory, Decimal("7520"))
    service.record(DAY2, {}, as_of=after_close(DAY2))
    with factory() as session:
        payload = await daily_performance(session, limit=100)
    assert [row["trading_date"] for row in payload] == [DAY2, DAY1]
    assert payload[0]["daily_pnl"] == "20"
    assert payload[0]["cumulative_pnl"] == str(Decimal("7520") - INITIAL)
