"""OrderIntent-to-Broker orchestration and the durable execution transaction.

Owns the transaction so one fill lands as a single commit: execution history plus
the simulation broker's cash, position, and trade. SimBroker mutates process-local
state before persistence runs, so a durable failure rolls both layers back.
"""

from collections.abc import Callable, Sequence
from datetime import datetime

from sqlalchemy.orm import Session

from app.broker.contract import Broker
from app.broker.domain import OrderStatus, SimOrder
from app.execution.domain import OrderIntent
from app.market.domain import MinuteBar
from app.repositories.execution import ExecutionRepository
from app.repositories.simulation import SimulationStateRepository
from app.shadow.domain import ShadowResult

SETTLED = frozenset({OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED})


class ExecutionService:
    def __init__(self, broker: Broker, session: Session | None = None, *,
                 account_id: int | None = None) -> None:
        self.broker = broker
        self.session = session
        self.account_id = account_id
        self.executions = None if session is None else ExecutionRepository(session)
        self.state = None if session is None else SimulationStateRepository(session)

    def execute(self, intent: OrderIntent, market_bars: Sequence[MinuteBar]):  # type: ignore[no-untyped-def]
        """Execute without persistence; the caller owns any durable record."""
        return self.broker.submit_order(intent, market_bars)

    def execute_and_persist(self, intent: OrderIntent, market_bars: Sequence[MinuteBar], *,
                            updated_at: datetime, shadow: ShadowResult | None = None,
                            scanner_run_id: int | None = None, scanner_candidate_id: int | None = None,
                            gpt_analysis_id: int | None = None,
                            on_persist: Callable[[Session, SimOrder], None] | None = None) -> SimOrder:
        """Execute and durably record in one transaction, or leave nothing moved.

        A rejected order still commits its execution history; only a settled order
        touches simulation state. The account row must already exist - this never
        creates one, because opening an account is an explicit operator action.

        ``on_persist`` runs on this session just before the commit, so a caller's
        own row - the strategy phase a fill produces, say - lands in the same
        transaction as the fill. It is called for rejections too, since a rejected
        order is durable history the caller may need to reflect. Raising from it
        rolls the whole execution back, broker memory included.
        """
        if self.session is None or self.account_id is None:
            raise ValueError("durable execution requires a session and an account")
        account = self.state.get_account_by_id(self.account_id)
        if account is None:
            raise LookupError("simulation account does not exist")
        expected_version = account.state_version
        snapshot = self.broker.state_snapshot()
        try:
            order = self.broker.submit_order(intent, market_bars)
            self.executions.persist_execution(
                order, self.broker.get_fills(order.id), self.broker.get_trade(intent.symbol), shadow,
                scanner_run_id=scanner_run_id, scanner_candidate_id=scanner_candidate_id,
                gpt_analysis_id=gpt_analysis_id)
            if order.status in SETTLED:
                self._persist_broker_state(intent.symbol, expected_version, updated_at)
            if on_persist is not None:
                on_persist(self.session, order)
            self.session.commit()
            return order
        except BaseException:
            # Memory first: restore_state only reassigns copies, so broker consistency
            # cannot depend on rollback succeeding on a connection that just died.
            self.broker.restore_state(snapshot)
            self.session.rollback()
            raise

    def _persist_broker_state(self, symbol: str, expected_version: int, updated_at: datetime) -> None:
        """Mirror the broker's own figures; nothing here recomputes prices or PnL."""
        self.state.update_account_cash(self.account_id, expected_version, self.broker.cash, updated_at)
        position = self.broker.get_position(symbol)
        if position is None:
            # A fully closed position is dropped by the broker, so drop the row too.
            self.state.delete_position(self.account_id, symbol)
        else:
            self.state.save_position(self.account_id, position)
        trade = self.broker.get_trade(symbol)
        if trade is not None:
            self.state.save_trade(self.account_id, trade, updated_at=updated_at)
