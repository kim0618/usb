"""Persistence boundary for daily planned-risk and allocation reservations."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.risk import DailySymbolState
from app.risk.domain import DailyTradingState


class DailyRiskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def load(self, trading_date: date) -> DailyTradingState:
        rows = list(self.session.scalars(
            select(DailySymbolState)
            .where(DailySymbolState.trading_date == trading_date)
            .order_by(DailySymbolState.symbol)
        ))
        return DailyTradingState(
            trading_date=trading_date,
            attempted_symbols=frozenset(row.symbol for row in rows),
            planned_risk_reserved=sum((row.planned_risk_amount for row in rows), Decimal("0")),
            base_notional_reserved=sum((row.base_notional_reserved for row in rows), Decimal("0")),
            pyramid_notional_reserved=sum((row.pyramid_notional_reserved for row in rows), Decimal("0")),
            add_counts={row.symbol: row.add_count for row in rows},
        )

    def reserve_entry(
        self, *, trading_date: date, symbol: str, issued_at: datetime,
        planned_risk: Decimal, base_notional: Decimal,
        risk_version: str, strategy_version: str,
    ) -> DailySymbolState:
        row = DailySymbolState(
            trading_date=trading_date, symbol=symbol, entry_intent_issued_at=issued_at,
            planned_risk_amount=planned_risk, base_notional_reserved=base_notional,
            pyramid_notional_reserved=Decimal("0"), add_count=0,
            risk_version=risk_version, strategy_version=strategy_version,
            created_at=issued_at, updated_at=issued_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def reserve_add(self, trading_date: date, symbol: str, amount: Decimal, *, updated_at: datetime) -> None:
        row = self.session.scalar(select(DailySymbolState).where(
            DailySymbolState.trading_date == trading_date, DailySymbolState.symbol == symbol
        ))
        if row is None:
            raise LookupError("base entry reservation does not exist")
        row.pyramid_notional_reserved += amount
        row.add_count += 1
        row.updated_at = updated_at
        self.session.flush()
