"""Atomic orchestration around the pure RiskEngine."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app.risk.domain import (
    AccountSnapshot, Currency, FxRate, PortfolioSnapshot, RiskEvaluation,
    RiskRejectionReason,
)
from app.risk.engine import RiskEngine
from app.repositories.risk import DailyRiskRepository
from app.strategy.domain import StrategyDecision, TradingEligibility


class RiskService:
    def __init__(self, engine: RiskEngine, repository: DailyRiskRepository) -> None:
        self.engine = engine
        self.repository = repository

    def prepare_base_entry(
        self, *, trading_date: date, decision: StrategyDecision,
        eligibility: TradingEligibility, account: AccountSnapshot,
        portfolio: PortfolioSnapshot, entry_price: Decimal | float | str,
        stop_price: Decimal | float | str, instrument_currency: Currency,
        created_at: datetime, fx_rate: FxRate | None = None,
    ) -> RiskEvaluation:
        session = self.repository.session
        if session.in_transaction():
            if session.new or session.dirty or session.deleted:
                raise RuntimeError("RiskService requires a clean Session")
            session.commit()
        try:
            state = self.repository.load(trading_date)
            evaluation = self.engine.evaluate_base_entry(
                decision=decision, eligibility=eligibility, account=account,
                portfolio=portfolio, daily_state=state, entry_price=entry_price,
                stop_price=stop_price, instrument_currency=instrument_currency,
                created_at=created_at, fx_rate=fx_rate,
            )
            if not evaluation.approved:
                session.rollback()
                return evaluation
            assert evaluation.order_intent is not None and evaluation.metrics is not None
            self.repository.reserve_entry(
                trading_date=trading_date, symbol=decision.symbol, issued_at=created_at,
                planned_risk=evaluation.metrics.planned_risk,
                base_notional=evaluation.metrics.final_notional_account_ccy,
                risk_version=self.engine.config.version,
                strategy_version=decision.strategy_version,
            )
            session.commit()
            return evaluation
        except IntegrityError:
            session.rollback()
            return RiskEvaluation(False, rejection_reason=RiskRejectionReason.SYMBOL_ALREADY_ATTEMPTED)
        except Exception:
            session.rollback()
            raise

    def reserve_pyramid_add(
        self, *, trading_date: date, decision: StrategyDecision,
        eligibility: TradingEligibility, account: AccountSnapshot,
        portfolio: PortfolioSnapshot, requested_notional_account_ccy: Decimal,
        created_at: datetime, fx_rate: FxRate | None = None,
    ) -> RiskEvaluation:
        session = self.repository.session
        if session.in_transaction():
            if session.new or session.dirty or session.deleted:
                raise RuntimeError("RiskService requires a clean Session")
            session.commit()
        try:
            state = self.repository.load(trading_date)
            evaluation = self.engine.evaluate_pyramid_add(
                decision=decision, eligibility=eligibility, account=account,
                portfolio=portfolio, daily_state=state,
                requested_notional_account_ccy=requested_notional_account_ccy,
                created_at=created_at, fx_rate=fx_rate,
            )
            if not evaluation.approved:
                session.rollback()
                return evaluation
            assert evaluation.metrics is not None
            self.repository.reserve_add(
                trading_date, decision.symbol,
                evaluation.metrics.final_notional_account_ccy, updated_at=created_at,
            )
            session.commit()
            return evaluation
        except Exception:
            session.rollback()
            raise
