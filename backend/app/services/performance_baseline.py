"""Which paper-trading history counts as strategy performance.

2026-09-08..2026-09-11 ran with confirmed system and data-pipeline defects (entry
session mapping, morning-scanner env, GPT authority, candidate run scope, Kiwoom
timestamps, minute availability, daily base date), so no approved candidate could be
evaluated normally. Those rows remain as immutable history but are excluded from
strategy performance. POST-FIX PAPER TRADING DAY 1 is the first XNYS session after the
fixes reached production, 2026-09-14. This is a reviewed, versioned code contract;
nothing in the database is rewritten to express it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.orm import Session

from app.market.calendar import MarketCalendar
from app.services.daily_performance import session_opening_equity


class PerformanceScope(StrEnum):
    STRATEGY = "STRATEGY"
    SYSTEM_VALIDATION_PRE_FIX = "SYSTEM_VALIDATION_PRE_FIX"
    BEFORE_STRATEGY_START = "BEFORE_STRATEGY_START"


STRATEGY_PERFORMANCE_VALID_FROM = date(2026, 9, 14)
EXCLUDED_PERFORMANCE_PERIODS: tuple[tuple[date, date, PerformanceScope], ...] = (
    (date(2026, 9, 8), date(2026, 9, 11), PerformanceScope.SYSTEM_VALIDATION_PRE_FIX),
)


def excluded_period(day: date) -> PerformanceScope | None:
    return next((scope for start, end, scope in EXCLUDED_PERFORMANCE_PERIODS if start <= day <= end), None)


def performance_scope(day: date) -> PerformanceScope:
    excluded = excluded_period(day)
    if excluded is not None:
        return excluded
    return PerformanceScope.STRATEGY if day >= STRATEGY_PERFORMANCE_VALID_FROM else PerformanceScope.BEFORE_STRATEGY_START


def strategy_baseline_equity(session: Session, account_id: int,
                             calendar: MarketCalendar | None = None) -> Decimal:
    """Equity at the start of POST-FIX DAY 1, read from persisted history only."""
    return session_opening_equity(session, account_id, STRATEGY_PERFORMANCE_VALID_FROM,
                                  calendar or MarketCalendar()).equity


def strategy_performance_fields(day: date, closing_equity: Decimal, baseline: Decimal) -> dict[str, object]:
    scope = performance_scope(day)
    counted = scope is PerformanceScope.STRATEGY
    return {"performance_scope": scope.value,
            "strategy_cumulative_pnl": str(closing_equity - baseline) if counted else None}
