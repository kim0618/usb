"""Pure, deterministic risk calculations for Stage 5."""

from datetime import datetime
from decimal import Decimal

from app.execution.domain import IntentType, OrderIntent, OrderSide
from app.risk.config import RiskConfig
from app.risk.domain import (
    AccountSnapshot, Currency, DailyTradingState, FxRate, OvernightStress, PositionSnapshot,
    OvernightAction, OvernightRiskEvaluation, PortfolioSnapshot, RiskEvaluation, RiskMetrics, RiskRejectionReason,
    decimal_from,
)
from app.strategy.domain import DecisionType, StrategyDecision, TradingEligibility


class RiskEngine:
    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()

    def one_r(self, account: AccountSnapshot) -> Decimal:
        return account.equity * self.config.risk_per_trade_pct

    def daily_risk_limit(self, account: AccountSnapshot) -> Decimal:
        return self.one_r(account) * self.config.max_daily_risk_units

    def evaluate_base_entry(
        self,
        *,
        decision: StrategyDecision,
        eligibility: TradingEligibility,
        account: AccountSnapshot,
        portfolio: PortfolioSnapshot,
        daily_state: DailyTradingState,
        entry_price: Decimal | float | str,
        stop_price: Decimal | float | str,
        instrument_currency: Currency,
        created_at: datetime,
        fx_rate: FxRate | None = None,
    ) -> RiskEvaluation:
        reject = self._entry_gate(decision, eligibility, account, portfolio, daily_state)
        if reject is not None:
            return reject
        entry, stop = decimal_from(entry_price), decimal_from(stop_price)
        if entry <= 0:
            return _reject(RiskRejectionReason.INVALID_ENTRY_PRICE)
        if stop <= 0 or stop >= entry:
            return _reject(RiskRejectionReason.INVALID_STOP_PRICE)
        per_share_instrument = entry - stop
        if per_share_instrument == 0:
            return _reject(RiskRejectionReason.ZERO_RISK_DISTANCE)
        multiplier = self._account_currency_multiplier(
            Currency(instrument_currency), account.currency, fx_rate
        )
        if multiplier is None:
            return _reject(RiskRejectionReason.CURRENCY_MISMATCH)
        if multiplier <= 0:
            return _reject(RiskRejectionReason.INVALID_FX_RATE)

        one_r = self.one_r(account)
        daily_remaining = self.daily_risk_limit(account) - daily_state.planned_risk_reserved
        if one_r <= 0:
            return _reject(RiskRejectionReason.INVALID_ACCOUNT_STATE)
        if daily_remaining < one_r:
            return _reject(RiskRejectionReason.DAILY_RISK_LIMIT)

        per_share_account = per_share_instrument * multiplier
        requested_quantity = one_r / per_share_account
        requested_account_notional = requested_quantity * entry * multiplier
        base_remaining = (
            account.equity * self.config.base_capacity_pct
            - portfolio.base_exposure_used - daily_state.base_notional_reserved
        )
        if base_remaining <= 0:
            return _reject(RiskRejectionReason.BASE_CAPACITY_EXHAUSTED)
        current_symbol_exposure = Decimal("0")
        existing = portfolio.position(decision.symbol)
        if existing is not None:
            current_symbol_exposure = (
                existing.base_notional_account_ccy + existing.pyramid_notional_account_ccy
            )
        symbol_remaining = account.equity * self.config.max_symbol_exposure_pct - current_symbol_exposure
        if symbol_remaining <= 0:
            return _reject(RiskRejectionReason.SYMBOL_EXPOSURE_LIMIT)
        if account.cash <= 0:
            return _reject(RiskRejectionReason.INSUFFICIENT_CASH)

        final_account_notional = min(
            requested_account_notional, base_remaining, symbol_remaining, account.cash
        )
        # A positive cap always yields a positive Decimal quantity; no broker lot rounding occurs here.
        final_quantity = final_account_notional / (entry * multiplier)
        planned_risk = final_quantity * per_share_account
        metrics = RiskMetrics(
            one_r=one_r, planned_risk=planned_risk,
            per_share_risk=per_share_account,
            requested_quantity=requested_quantity, final_quantity=final_quantity,
            requested_notional_account_ccy=requested_account_notional,
            final_notional_account_ccy=final_account_notional,
            base_capacity_remaining=base_remaining,
            pyramid_capacity_remaining=(
                account.equity * self.config.pyramid_reserve_pct
                - portfolio.pyramid_exposure_used - daily_state.pyramid_notional_reserved
            ),
            symbol_capacity_remaining=symbol_remaining,
        )
        intent = OrderIntent(
            symbol=decision.symbol, side=OrderSide.BUY, intent_type=IntentType.BASE_ENTRY,
            quantity=final_quantity, reference_price=entry,
            notional=final_quantity * entry, account_notional=final_account_notional,
            account_currency=account.currency.value,
            instrument_currency=Currency(instrument_currency).value,
            strategy_version=decision.strategy_version, risk_amount=planned_risk,
            initial_stop=stop, market_as_of=decision.market_as_of,
            created_at=created_at, reason=decision.reason_code,
        )
        return RiskEvaluation(True, intent, metrics)

    def evaluate_pyramid_add(
        self,
        *,
        decision: StrategyDecision,
        eligibility: TradingEligibility,
        account: AccountSnapshot,
        portfolio: PortfolioSnapshot,
        daily_state: DailyTradingState,
        requested_notional_account_ccy: Decimal | str,
        created_at: datetime,
        fx_rate: FxRate | None = None,
    ) -> RiskEvaluation:
        if decision.decision is not DecisionType.ADD:
            return _reject(RiskRejectionReason.NOT_ENTER_DECISION)
        if eligibility.safe_mode:
            return _reject(RiskRejectionReason.SAFE_MODE)
        if eligibility.book == "ACTUAL" and not eligibility.human_approved:
            return _reject(RiskRejectionReason.HUMAN_NOT_APPROVED)
        position = portfolio.position(decision.symbol)
        if position is None:
            return _reject(RiskRejectionReason.POSITION_NOT_FOUND)
        add_count = max(position.add_count, daily_state.add_counts.get(decision.symbol, 0))
        if add_count >= self.config.max_pyramid_adds:
            return _reject(RiskRejectionReason.PYRAMID_LIMIT)
        if position.current_price <= position.average_price:
            return _reject(RiskRejectionReason.POSITION_NOT_PROFITABLE)
        requested = decimal_from(requested_notional_account_ccy)
        reserve_remaining = (
            account.equity * self.config.pyramid_reserve_pct
            - portfolio.pyramid_exposure_used - daily_state.pyramid_notional_reserved
        )
        if requested <= 0 or reserve_remaining <= 0:
            return _reject(RiskRejectionReason.PYRAMID_RESERVE_EXHAUSTED)
        multiplier = self._account_currency_multiplier(position.instrument_currency, account.currency, fx_rate)
        if multiplier is None:
            return _reject(RiskRejectionReason.CURRENCY_MISMATCH)
        symbol_used = position.base_notional_account_ccy + position.pyramid_notional_account_ccy
        symbol_remaining = account.equity * self.config.max_symbol_exposure_pct - symbol_used
        final_account_notional = min(requested, reserve_remaining, symbol_remaining, account.cash)
        if final_account_notional <= 0:
            return _reject(RiskRejectionReason.PYRAMID_RESERVE_EXHAUSTED)
        quantity = final_account_notional / (position.current_price * multiplier)
        stop = position.initial_stop or position.average_price
        intent = OrderIntent(
            symbol=decision.symbol, side=OrderSide.BUY, intent_type=IntentType.PYRAMID_ADD,
            quantity=quantity, reference_price=position.current_price,
            notional=quantity * position.current_price, account_notional=final_account_notional,
            account_currency=account.currency.value, instrument_currency=position.instrument_currency.value,
            strategy_version=decision.strategy_version, risk_amount=Decimal("0"), initial_stop=stop,
            market_as_of=decision.market_as_of, created_at=created_at, reason=decision.reason_code,
        )
        metrics = RiskMetrics(
            one_r=self.one_r(account), planned_risk=Decimal("0"), per_share_risk=Decimal("0"),
            requested_quantity=requested / (position.current_price * multiplier), final_quantity=quantity,
            requested_notional_account_ccy=requested, final_notional_account_ccy=final_account_notional,
            base_capacity_remaining=account.equity * self.config.base_capacity_pct - portfolio.base_exposure_used - daily_state.base_notional_reserved,
            pyramid_capacity_remaining=reserve_remaining, symbol_capacity_remaining=symbol_remaining,
        )
        return RiskEvaluation(True, intent, metrics)

    def overnight_stress(
        self, account_equity: Decimal | str, proposed_notional: Decimal | str,
        gap_scenario_pct: Decimal | str,
    ) -> OvernightStress:
        equity, notional, gap = map(decimal_from, (account_equity, proposed_notional, gap_scenario_pct))
        gap = abs(gap)
        if equity <= 0 or notional < 0 or gap <= 0:
            raise ValueError("equity and gap must be positive; notional cannot be negative")
        loss = notional * gap
        max_loss = equity * self.config.max_overnight_stress_loss_pct
        return OvernightStress(
            estimated_loss=loss, account_loss_pct=loss / equity,
            max_notional_by_stress=max_loss / gap, within_limit=loss <= max_loss,
        )

    def can_add_overnight_position(self, portfolio: PortfolioSnapshot) -> bool:
        return portfolio.overnight_position_count < self.config.max_overnight_positions

    def evaluate_overnight_notional(self, *, account: AccountSnapshot,
                                    portfolio: PortfolioSnapshot,
                                    proposed_notional: Decimal | str) -> OvernightRiskEvaluation:
        proposed = decimal_from(proposed_notional)
        if proposed <= 0 or not self.can_add_overnight_position(portfolio):
            return OvernightRiskEvaluation(OvernightAction.EXIT_ALL, Decimal("0"), max(proposed, Decimal("0")))
        stress = self.overnight_stress(account.equity, proposed, self.config.overnight_stress_gap_pct)
        safe = min(proposed, stress.max_notional_by_stress)
        if safe <= 0:
            return OvernightRiskEvaluation(OvernightAction.EXIT_ALL, Decimal("0"), proposed)
        if safe < proposed:
            return OvernightRiskEvaluation(OvernightAction.REDUCE_AND_HOLD, safe, proposed - safe)
        return OvernightRiskEvaluation(OvernightAction.HOLD_FULL, proposed, Decimal("0"))

    def build_exit_intent(self, *, decision: StrategyDecision, position: PositionSnapshot,
                          created_at: datetime, quantity: Decimal | str | None = None,
                          account_currency: Currency | None = None,
                          fx_rate: FxRate | None = None) -> OrderIntent:
        """Translate an EXIT quality decision to a full/partial SELL; no exit quality is judged here."""
        if decision.decision is not DecisionType.EXIT:
            raise ValueError("an EXIT strategy decision is required")
        sell_quantity = position.quantity if quantity is None else decimal_from(quantity)
        if sell_quantity <= 0 or sell_quantity > position.quantity:
            raise ValueError("exit quantity must be within the broker position")
        account_ccy = account_currency or position.instrument_currency
        multiplier = self._account_currency_multiplier(position.instrument_currency, account_ccy, fx_rate)
        if multiplier is None or multiplier <= 0:
            raise ValueError("a directed FX rate is required for cross-currency exit")
        notional = sell_quantity * position.current_price
        return OrderIntent(
            symbol=position.symbol, side=OrderSide.SELL, intent_type=IntentType.EXIT,
            quantity=sell_quantity, reference_price=position.current_price,
            notional=notional, account_notional=notional * multiplier,
            account_currency=account_ccy.value,
            instrument_currency=position.instrument_currency.value,
            strategy_version=decision.strategy_version, risk_amount=Decimal("0"),
            initial_stop=position.initial_stop or position.current_price,
            market_as_of=decision.market_as_of, created_at=created_at,
            reason=decision.reason_code,
        )

    def _entry_gate(self, decision, eligibility, account, portfolio, daily_state):  # type: ignore[no-untyped-def]
        if decision.decision is not DecisionType.ENTER:
            return _reject(RiskRejectionReason.NOT_ENTER_DECISION)
        if eligibility.safe_mode:
            return _reject(RiskRejectionReason.SAFE_MODE)
        if eligibility.book == "ACTUAL" and not eligibility.human_approved:
            return _reject(RiskRejectionReason.HUMAN_NOT_APPROVED)
        if account.equity <= 0 or account.cash > account.equity:
            return _reject(RiskRejectionReason.INVALID_ACCOUNT_STATE)
        if portfolio.base_exposure_used + portfolio.pyramid_exposure_used > account.equity:
            return _reject(RiskRejectionReason.INVALID_PORTFOLIO_STATE)
        if decision.symbol in daily_state.attempted_symbols:
            return _reject(RiskRejectionReason.SYMBOL_ALREADY_ATTEMPTED)
        if daily_state.planned_risk_reserved >= self.daily_risk_limit(account):
            return _reject(RiskRejectionReason.DAILY_RISK_LIMIT)
        if len(daily_state.attempted_symbols) >= self.config.max_new_symbols_per_day:
            return _reject(RiskRejectionReason.DAILY_SYMBOL_LIMIT)
        return None

    @staticmethod
    def _account_currency_multiplier(
        instrument_currency: Currency, account_currency: Currency, fx_rate: FxRate | None
    ) -> Decimal | None:
        if instrument_currency == account_currency:
            return Decimal("1")
        if fx_rate is None:
            return None
        if fx_rate.base_currency != instrument_currency or fx_rate.quote_currency != account_currency:
            return None
        return fx_rate.quote_per_base


def _reject(reason: RiskRejectionReason, details: str | None = None) -> RiskEvaluation:
    return RiskEvaluation(False, rejection_reason=reason, details=details)
