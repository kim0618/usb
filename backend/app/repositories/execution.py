"""Atomic persistence of immutable simulation result copies."""

from decimal import Decimal

from sqlalchemy.orm import Session

from app.broker.domain import SimFill, SimOrder, TradeResult
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord, ShadowTradeRecord
from app.shadow.domain import ShadowResult


class ExecutionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def persist_execution(self, order: SimOrder, fills: tuple[SimFill, ...], trade: TradeResult | None = None,
                          shadow: ShadowResult | None = None, *, scanner_run_id: int | None = None,
                          scanner_candidate_id: int | None = None, gpt_analysis_id: int | None = None) -> None:
        try:
            self.session.add(ExecutionOrderRecord(
                id=order.id, broker_type="SIM", symbol=order.symbol, side=order.side.value,
                requested_quantity=order.requested_quantity, filled_quantity=order.filled_quantity,
                status=order.status.value, rejection_reason=order.rejection_reason.value if order.rejection_reason else None,
                reference_price=order.reference_price, submitted_at=order.submitted_at,
                completed_at=order.filled_at, execution_version=order.execution_version,
            ))
            self.session.flush()
            for fill in fills:
                self.session.add(ExecutionFillRecord(
                    id=fill.fill_id, order_id=fill.order_id, quantity=fill.quantity,
                    raw_market_price=fill.raw_market_price, fill_price=fill.fill_price,
                    spread_cost=fill.spread_cost, slippage_cost=fill.slippage_cost,
                    commission=fill.commission, fx_cost=fill.fx_cost, total_cost=fill.total_cost,
                    filled_at=fill.filled_at,
                ))
            if shadow is not None:
                self._add_shadow(shadow, trade, scanner_run_id, scanner_candidate_id, gpt_analysis_id)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def persist_no_trade(self, result: ShadowResult, *, record_id: str,
                         scanner_run_id: int | None = None, scanner_candidate_id: int | None = None) -> None:
        try:
            self._add_shadow(result, None, scanner_run_id, scanner_candidate_id, None, record_id=record_id)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def _add_shadow(self, shadow: ShadowResult, trade: TradeResult | None, scanner_run_id: int | None,
                    scanner_candidate_id: int | None, gpt_analysis_id: int | None,
                    record_id: str | None = None) -> None:
        zero = Decimal("0")
        self.session.add(ShadowTradeRecord(
            id=record_id or (trade.trade_id + f"-{shadow.variant.value}" if trade else f"NO-TRADE-{shadow.symbol}-{shadow.variant.value}"),
            scanner_run_id=scanner_run_id, scanner_candidate_id=scanner_candidate_id,
            gpt_analysis_id=gpt_analysis_id, symbol=shadow.symbol, variant=shadow.variant.value,
            variant_version=shadow.variant_version, is_control=shadow.variant.is_control,
            initial_planned_risk=trade.planned_initial_risk if trade else zero,
            entry_at=trade.entry_time if trade else None, exit_at=trade.exit_time if trade else None,
            average_entry_price=trade.average_entry_price if trade else None,
            average_exit_price=trade.average_exit_price if trade else None,
            gross_pnl=trade.gross_pnl if trade else zero, net_pnl=trade.net_pnl if trade else zero,
            gross_r=trade.gross_r if trade else zero, net_r=trade.net_r if trade else zero,
            total_cost=trade.total_cost if trade else zero,
            ambiguous_bar_count=trade.ambiguous_bar_count if trade else 0, status=shadow.status.value,
            premarket_passed=shadow.premarket_passed, opening_passed=shadow.opening_passed,
            entry_signalled=shadow.entry_signalled, entry_filled=shadow.entry_filled,
            no_trade_reason=shadow.no_trade_reason, exit_reason=shadow.exit_reason,
            gate_reached=shadow.gate_reached, holding_days=shadow.holding_days,
        ))
