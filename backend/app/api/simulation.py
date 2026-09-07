"""Read-only projections of the active broker, without fabricated market marks."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.broker.domain import SimPosition
from app.broker.sim import SimBroker


def position_projection(broker: SimBroker, position: SimPosition) -> dict[str, Any]:
    return {"symbol": position.symbol, "currency": broker.currency.value,
            "quantity": str(position.quantity), "average_price": str(position.average_price),
            "invested_notional": str(position.cost_basis),
            "realized_pnl": str(position.realized_pnl), "mark_price": None,
            "market_value": None, "unrealized_pnl": None, "return_pct": None,
            "active_stop": None, "phase": None}


def broker_projection(broker: SimBroker) -> dict[str, Any]:
    positions = broker.get_positions()
    # With no positions equity needs no marks. Account-wide realized/today PnL
    # cannot be recovered from the broker's open-trade slots.
    equity = None if positions else str(broker.account_snapshot({}, datetime.now(timezone.utc)).equity)
    return {"account": {"currency": broker.currency.value, "cash": str(broker.cash),
                        "equity": equity,
                        "invested_notional": str(sum((p.cost_basis for p in positions), Decimal("0"))),
                        "unrealized_pnl": None if positions else "0",
                        "realized_pnl": None, "today_pnl": None},
            "open_positions": [position_projection(broker, p) for p in positions],
            "open_orders": [{"order_id": o.id, "broker_type": "SIM", "symbol": o.symbol,
                             "side": o.side.value, "requested_quantity": str(o.requested_quantity),
                             "filled_quantity": str(o.filled_quantity), "status": o.status.value,
                             "rejection_reason": o.rejection_reason,
                             "reference_price": str(o.reference_price), "submitted_at": o.submitted_at,
                             "completed_at": o.filled_at, "execution_version": o.execution_version}
                            for o in broker.get_open_orders()]}
