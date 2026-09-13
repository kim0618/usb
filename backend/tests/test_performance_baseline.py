"""POST-FIX strategy performance baseline: pre-fix history kept, excluded from strategy aggregates."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.router import daily_performance
from app.market.calendar import MarketCalendar
from app.models.scanner import ScannerRun
from app.models.simulation import AccountDailyPerformanceRecord
from app.repositories.simulation import SimulationStateRepository
from app.services.simulation_runtime import clear_active_sim_broker
from app.services.performance_baseline import (
    EXCLUDED_PERFORMANCE_PERIODS, STRATEGY_PERFORMANCE_VALID_FROM, PerformanceScope, performance_scope,
)
from backend.tests.test_daily_performance import book  # noqa: F401
from backend.tests.test_shadow_summary_period import api, get, shadow  # noqa: F401

@pytest.fixture(autouse=True)
def _release_active_runtime():  # type: ignore[no-untyped-def]
    """`book` activates the process-wide operator runtime; never leak it to later modules."""
    yield
    clear_active_sim_broker()


CLOSES = {date(2026, 9, 8): "7428.92", date(2026, 9, 9): "7428.92", date(2026, 9, 10): "7428.92",
          date(2026, 9, 11): "7428.92", date(2026, 9, 14): "7441.42", date(2026, 9, 15): "7468.92"}


def test_valid_from_is_the_first_xnys_session_after_the_pre_fix_period() -> None:
    calendar = MarketCalendar()
    (start, end, scope), = EXCLUDED_PERFORMANCE_PERIODS
    assert scope is PerformanceScope.SYSTEM_VALIDATION_PRE_FIX
    assert (start, end) == (date(2026, 9, 8), date(2026, 9, 11))
    assert calendar.session(STRATEGY_PERFORMANCE_VALID_FROM) is not None
    assert calendar.next_trading_day(end) == STRATEGY_PERFORMANCE_VALID_FROM == date(2026, 9, 14)


@pytest.mark.parametrize(("day", "scope"), [
    (date(2026, 9, 7), PerformanceScope.BEFORE_STRATEGY_START),
    (date(2026, 9, 8), PerformanceScope.SYSTEM_VALIDATION_PRE_FIX),
    (date(2026, 9, 11), PerformanceScope.SYSTEM_VALIDATION_PRE_FIX),
    (date(2026, 9, 14), PerformanceScope.STRATEGY),
])
def test_performance_scope(day: date, scope: PerformanceScope) -> None:
    assert performance_scope(day) is scope


def rows(session):  # type: ignore[no-untyped-def]
    return [(r.trading_date, r.opening_equity, r.closing_equity, r.daily_pnl, r.cash)
            for r in session.scalars(select(AccountDailyPerformanceRecord).order_by(AccountDailyPerformanceRecord.trading_date))]


@pytest.mark.asyncio
async def test_pre_fix_rows_are_kept_and_strategy_cumulative_starts_on_post_fix_day_one(book) -> None:
    runtime, factory = book
    with factory() as session:
        repository = SimulationStateRepository(session)
        previous = None
        for day, close in CLOSES.items():
            opening = Decimal(previous or close)
            repository.add_daily_performance(
                account_id=runtime.account_id, trading_date=day, opening_equity=opening,
                closing_equity=Decimal(close), cash=Decimal(close), position_market_value=Decimal("0"),
                daily_pnl=Decimal(close) - opening, daily_return=Decimal("0"),
                recorded_at=datetime.combine(day, datetime.min.time(), timezone.utc))
            previous = close
        session.commit()
        before = rows(session)
    with factory() as session:
        payload = await daily_performance(session, limit=100)
        assert rows(session) == before  # history is never rewritten or re-dated
    by_day = {row["trading_date"]: row for row in payload}
    assert sorted(by_day) == sorted(CLOSES)
    for day in (date(2026, 9, 8), date(2026, 9, 11)):
        assert by_day[day]["performance_scope"] == "SYSTEM_VALIDATION_PRE_FIX"
        assert by_day[day]["strategy_cumulative_pnl"] is None
        assert by_day[day]["closing_equity"] == "7428.92"
    assert by_day[date(2026, 9, 14)]["strategy_cumulative_pnl"] == "12.50"   # vs 09/11 close
    assert by_day[date(2026, 9, 15)]["strategy_cumulative_pnl"] == "40.00"
    assert by_day[date(2026, 9, 15)]["performance_scope"] == "STRATEGY"


@pytest.mark.asyncio
async def test_shadow_summary_never_counts_pre_fix_paths_and_dashboard_names_day_one(api) -> None:
    app, sessions = api
    with sessions() as session:
        session.add_all([
            shadow("PRE", "C", "CLOSED", exit_at=datetime(2026, 9, 9, 18, tzinfo=timezone.utc), net_r="5"),
            shadow("POST", "C", "CLOSED", exit_at=datetime(2026, 9, 15, 18, tzinfo=timezone.utc), net_r="1"),
            shadow("OLD", "C", "CLOSED", exit_at=datetime(2025, 10, 31, 18, tzinfo=timezone.utc), net_r="2"),
        ])
        session.commit()
        runs_before = [(r.id, r.trading_date) for r in session.scalars(select(ScannerRun))]
    summary = (await get(app, "/api/v1/shadow/summary")).json()
    control = next(v for v in summary["variants"] if v["variant"] == "C")
    assert (control["trades"], control["net_r"]) == (2, "3")
    windowed = (await get(app, "/api/v1/shadow/summary?start_date=2026-09-08&end_date=2026-09-11")).json()
    assert next(v for v in windowed["variants"] if v["variant"] == "C")["trades"] == 0
    assert summary["excluded_periods"] == [{"start": "2026-09-08", "end": "2026-09-11",
                                            "reason": "SYSTEM_VALIDATION_PRE_FIX"}]
    dashboard = (await get(app, "/api/v1/dashboard")).json()
    assert dashboard["trading"]["strategy_performance_valid_from"] == "2026-09-14"
    with sessions() as session:
        assert [(r.id, r.trading_date) for r in session.scalars(select(ScannerRun))] == runs_before
