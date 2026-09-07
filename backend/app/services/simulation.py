"""Rebuild a SimBroker's process-local state from durable simulation state.

Restart safety only. Persisted cash, positions, and open trades are the broker's
own figures, written verbatim by the execution transaction, so they are copied
back verbatim: nothing here recomputes an average, a cost basis, or a PnL, and no
order or fill is replayed. Replaying would be a second, independently rounded
calculation, and the durable row would stop being the authority.

Execution history stays where it belongs. The account's orders and fills live in
execution_orders/execution_fills and are not loaded into broker memory, because
no broker calculation reads them - see rehydrate_sim_broker for the full contract.
"""

from sqlalchemy.orm import Session

from app.broker.domain import SimPosition, TradeResult
from app.broker.sim import SimBroker, generate_execution_scope
from app.execution.config import ExecutionConfig
from app.market.symbols import normalize_symbol
from app.repositories.simulation import SimulationStateRepository
from app.risk.domain import Currency


class SimulationStateInconsistent(RuntimeError):
    """Durable state cannot produce a broker that would execute correctly."""


def rehydrate_sim_broker(session: Session, account_id: int, *,
                         config: ExecutionConfig | None = None) -> SimBroker:
    """Return a fresh SimBroker carrying the account's persisted state.

    The account must already exist: opening one is an explicit operator action,
    never a side effect of a restart, so a missing row fails closed.

    Deliberately not restored:

    * The execution scope. A new one is minted here on every call rather than
      persisted and reused, so order and fill IDs from this process can never
      collide with the IDs a previous process already wrote.
    * The ID sequence. It stays at zero, which is safe precisely because the
      scope above is new.
    * Orders and fills. `_orders` and `_fills` feed only `get_order`,
      `get_open_orders`, and `get_fills`; no cash, position, or trade figure is
      derived from them. Loading them would also republish pre-restart
      PARTIALLY_FILLED orders as open while nothing exists to settle the
      remainder.
    * Closed trades. `_trades` is keyed by symbol and holds one trade per symbol,
      so it is a live-trade slot rather than a history store, and a stale closed
      trade left in that slot would be handed to the next execution's shadow
      record.

    Config is injected by the caller rather than read back from the database;
    persisted state carries figures, not the assumptions that produced them.
    """
    repository = SimulationStateRepository(session)
    account = repository.get_account_by_id(account_id)
    if account is None:
        raise LookupError("simulation account does not exist")
    positions = {normalize_symbol(row.symbol): row for row in repository.list_positions(account_id)}
    trades = {normalize_symbol(row.symbol): row for row in repository.list_open_trades(account_id)}
    _reject_unworkable_state(positions, trades)
    broker = SimBroker(account.initial_cash, currency=Currency(account.base_currency),
                       config=config, execution_scope=generate_execution_scope())
    # Snapshot injection through the broker's own primitive: current cash replaces
    # the starting cash the constructor seeded, and no order path runs.
    broker.restore_state({"cash": account.cash, "orders": {}, "fills": [],
                          "positions": positions, "trades": trades})
    return broker


def _reject_unworkable_state(positions: dict[str, SimPosition],
                             trades: dict[str, TradeResult]) -> None:
    """Refuse state the broker's own buy and sell paths could not settle.

    Symbol uniqueness and one-open-trade-per-symbol are database constraints, so
    only what the schema cannot express is re-checked here.
    """
    for symbol, position in positions.items():
        if position.quantity <= 0:
            # The broker deletes a position at zero, so a row at or below it is malformed.
            raise SimulationStateInconsistent(f"{symbol} position quantity is not positive")
    for symbol, trade in trades.items():
        if trade.planned_initial_risk <= 0:
            # Every R figure a sell writes is divided by this.
            raise SimulationStateInconsistent(f"{symbol} open trade has no planned initial risk")
    if positions.keys() != trades.keys():
        # A second buy and every sell index _trades[symbol] once a position exists,
        # and a first buy overwrites the slot, discarding an orphaned open trade.
        stranded = ", ".join(sorted(positions.keys() ^ trades.keys()))
        raise SimulationStateInconsistent(f"position and open trade disagree on {stranded}")
