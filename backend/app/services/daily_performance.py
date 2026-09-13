"""Durable post-close actual-book account performance snapshots."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Mapping

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.market.calendar import MarketCalendar
from app.models.simulation import AccountDailyPerformanceRecord
from app.repositories.simulation import SimulationStateRepository
from app.services.simulation_runtime import SimulationRuntimeContext


class SessionEquitySource(StrEnum):
    PREVIOUS_SESSION_CLOSE = "PREVIOUS_SESSION_CLOSE"
    LATEST_EARLIER_CLOSE = "LATEST_EARLIER_CLOSE"
    INITIAL_CASH = "INITIAL_CASH"


@dataclass(frozen=True)
class SessionEquity:
    equity: Decimal
    source: SessionEquitySource


def session_opening_equity(session: Session, account_id: int, trading_date: date,
                           calendar: MarketCalendar) -> SessionEquity:
    """Durable start-of-session equity that fixes the daily risk-accounting 1R.

    It is the opening equity the daily performance snapshot already uses: the exact
    previous XNYS session's recorded closing equity, or the account's initial cash
    when nothing was ever recorded. A missed snapshot falls back to the latest earlier
    close and says so through ``source`` rather than blocking every entry. Only
    persisted rows are read, so a restart reproduces the same value.
    """
    repository = SimulationStateRepository(session)
    previous = repository.latest_daily_performance_before(account_id, trading_date)
    if previous is not None:
        exact = previous.trading_date == calendar.previous_trading_day(trading_date)
        return SessionEquity(previous.closing_equity, SessionEquitySource.PREVIOUS_SESSION_CLOSE
                             if exact else SessionEquitySource.LATEST_EARLIER_CLOSE)
    account = repository.get_account_by_id(account_id)
    if account is None:
        raise LookupError("simulation account does not exist")
    return SessionEquity(account.initial_cash, SessionEquitySource.INITIAL_CASH)


class DailyPerformanceUnavailable(RuntimeError):
    """A trustworthy complete EOD account valuation cannot be produced yet."""


@dataclass(frozen=True)
class DailyPerformanceResult:
    snapshot: AccountDailyPerformanceRecord
    created: bool


class DailyPerformanceService:
    """Persist one closing-equity delta per actual simulation account/session."""

    def __init__(self, runtime: SimulationRuntimeContext, *, calendar: MarketCalendar | None = None) -> None:
        if not runtime.durable:
            raise ValueError("daily performance requires a durable runtime")
        self.runtime = runtime
        self.calendar = calendar or MarketCalendar()

    def has_snapshot(self, trading_date: date) -> bool:
        account_id = self.runtime.account_id
        session_factory = self.runtime.session_factory
        assert account_id is not None and session_factory is not None
        with session_factory() as session:
            return SimulationStateRepository(session).get_daily_performance(account_id, trading_date) is not None

    def record(self, trading_date: date, marks: Mapping[str, Decimal], *, as_of: datetime) -> DailyPerformanceResult:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("daily performance timestamp must be timezone-aware")
        session_window = self.calendar.session(trading_date)
        if session_window is None:
            raise DailyPerformanceUnavailable("daily performance requires an XNYS trading session")
        if as_of < session_window.market_close:
            raise DailyPerformanceUnavailable("daily performance cannot be recorded before market close")

        account_id = self.runtime.account_id
        session_factory = self.runtime.session_factory
        assert account_id is not None and session_factory is not None
        positions = self.runtime.broker.get_positions()
        required = {position.symbol for position in positions}
        if set(marks) != required:
            missing = ",".join(sorted(required - set(marks)))
            raise DailyPerformanceUnavailable(f"complete regular-session marks are required: {missing}")
        position_value = sum(
            (position.quantity * Decimal(marks[position.symbol]) for position in positions), Decimal("0")
        )
        cash = self.runtime.broker.cash
        closing_equity = cash + position_value

        with session_factory() as session:
            repository = SimulationStateRepository(session)
            existing = repository.get_daily_performance(account_id, trading_date)
            if existing is not None:
                return DailyPerformanceResult(existing, False)
            account = repository.get_account_by_id(account_id)
            if account is None:
                raise DailyPerformanceUnavailable("durable simulation account is unavailable")
            if account.cash != cash:
                raise DailyPerformanceUnavailable("active and persisted account cash disagree")
            previous = repository.latest_daily_performance_before(account_id, trading_date)
            if previous is None:
                opening_equity = account.initial_cash
            else:
                expected = self.calendar.previous_trading_day(trading_date)
                if previous.trading_date != expected:
                    raise DailyPerformanceUnavailable("previous trading-day snapshot is missing")
                opening_equity = previous.closing_equity
            daily_pnl = closing_equity - opening_equity
            daily_return = Decimal("0") if opening_equity == 0 else daily_pnl / opening_equity
            try:
                snapshot = repository.add_daily_performance(
                    account_id=account_id, trading_date=trading_date,
                    opening_equity=opening_equity, closing_equity=closing_equity,
                    cash=cash, position_market_value=position_value,
                    daily_pnl=daily_pnl, daily_return=daily_return, recorded_at=as_of)
                session.commit()
                return DailyPerformanceResult(snapshot, True)
            except IntegrityError:
                session.rollback()
                existing = repository.get_daily_performance(account_id, trading_date)
                if existing is None:
                    raise
                return DailyPerformanceResult(existing, False)
