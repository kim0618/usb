"""Emergency halt plus best-effort broker-position liquidation foundation."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.broker.contract import Broker
from app.broker.domain import OrderStatus, SimOrder
from app.market.domain import MinuteBar
from app.monitoring.domain import FailureCode, FailureSeverity, RuntimeMode
from app.monitoring.service import RuntimeHealthService
from app.risk.domain import Currency, PositionSnapshot
from app.risk.engine import RiskEngine
from app.strategy.domain import DecisionType, StrategyDecision


@dataclass(frozen=True)
class KillSwitchResult:
    runtime_mode: RuntimeMode
    liquidation_orders: tuple[SimOrder, ...]
    failed_symbols: tuple[str, ...]


def activate_kill_switch(*, runtime: RuntimeHealthService, broker: Broker,
                         market_bars: Mapping[str, Sequence[MinuteBar]],
                         activated_at: datetime, market_as_of: datetime,
                         confirmed: bool = True, risk_engine: RiskEngine | None = None) -> KillSwitchResult:
    if runtime.config.kill_switch_requires_confirmation and not confirmed:
        raise ValueError("kill switch confirmation is required")
    runtime.halt(activated_at, code=FailureCode.KILL_SWITCH_ACTIVATED,
                 message="kill switch activated")
    engine = risk_engine or RiskEngine()
    orders: list[SimOrder] = []
    failed: list[str] = []
    for position in broker.get_positions():
        bars = tuple(market_bars.get(position.symbol, ()))
        reference = Decimal(str(bars[-1].close)) if bars else position.average_price
        decision = StrategyDecision(position.symbol, DecisionType.EXIT,
            "KILL_SWITCH", market_as_of, "strategy_v0")
        snapshot = PositionSnapshot(position.symbol, position.quantity, position.average_price,
            reference, Currency.USD, initial_stop=position.average_price)
        intent = engine.build_exit_intent(decision=decision, position=snapshot,
            created_at=activated_at)
        try:
            order = broker.submit_order(intent, bars)
            orders.append(order)
            if order.status is not OrderStatus.FILLED:
                failed.append(position.symbol)
        except Exception as exc:
            failed.append(position.symbol)
            runtime.report_failure(code=FailureCode.EXECUTION_UNAVAILABLE,
                severity=FailureSeverity.CRITICAL, component="kill_switch",
                message=f"liquidation failed for {position.symbol}: {type(exc).__name__}",
                occurred_at=activated_at, market_as_of=market_as_of,
                target_mode=RuntimeMode.HALTED)
    if failed:
        runtime.report_failure(code=FailureCode.EXECUTION_REJECTED,
            severity=FailureSeverity.CRITICAL, component="kill_switch",
            message=f"partial liquidation failure: {','.join(sorted(failed))}",
            occurred_at=activated_at, market_as_of=market_as_of,
            target_mode=RuntimeMode.HALTED)
    return KillSwitchResult(RuntimeMode.HALTED, tuple(orders), tuple(sorted(failed)))
