"""Single-process broker ownership and the operator's startup activation.

Activation is profile-gated: only the real_market_operator profile running the
simulation broker rehydrates an account at startup. Every other profile keeps the
previous behaviour of owning no broker, so a development or test database is
never queried for simulation tables it may not have.

The registered value is a context rather than a bare broker, because durable
execution needs three things that must stay consistent with one another: the
broker the API projects, the durable account that broker was rebuilt from, and a
session factory pointing at the same database. Handing those out separately is
what would let a runner execute against one account while the API shows another.
"""

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.broker.sim import SimBroker
from app.core.config import Settings
from app.execution.config import ExecutionConfig
from app.repositories.simulation import SimulationStateRepository
from app.services.simulation import SimulationStateInconsistent, rehydrate_sim_broker

logger = logging.getLogger(__name__)

OPERATOR_BROKER_TYPE = "SIM"
OPERATOR_ACCOUNT_KEY = "operator"

SessionFactory = Callable[[], Session]


@dataclass(frozen=True)
class SimulationRuntimeContext:
    """What one activated simulation runtime owns for the life of the process.

    ``account_id`` and ``session_factory`` are optional so a broker registered
    without a database (shadow paths, focused tests) still has a context; durable
    execution simply refuses to run without both.
    """

    broker: SimBroker
    account_id: int | None = None
    session_factory: SessionFactory | None = None
    config: ExecutionConfig | None = None

    @property
    def durable(self) -> bool:
        return self.account_id is not None and self.session_factory is not None


_active_runtime: SimulationRuntimeContext | None = None
_lock = Lock()


def get_active_runtime() -> SimulationRuntimeContext | None:
    with _lock:
        return _active_runtime


def get_active_sim_broker() -> SimBroker | None:
    runtime = get_active_runtime()
    return None if runtime is None else runtime.broker


def set_active_runtime(context: SimulationRuntimeContext) -> None:
    """Reject duplicate registration; callers must explicitly clear first."""
    global _active_runtime
    with _lock:
        if _active_runtime is not None:
            raise RuntimeError("active simulation broker already registered")
        _active_runtime = context


def set_active_sim_broker(broker: SimBroker) -> None:
    """Register a broker with no durable account behind it."""
    set_active_runtime(SimulationRuntimeContext(broker))


def clear_active_sim_broker() -> None:
    """Release ownership without mutating the broker or persisting anything."""
    global _active_runtime
    with _lock:
        _active_runtime = None


def initialize_simulation_runtime(session: Session, account_id: int, *,
                                  config: ExecutionConfig | None = None,
                                  session_factory: SessionFactory | None = None) -> SimBroker:
    """Hydrate before registration; lookup/consistency errors preserve ownership.

    The caller supplies an existing account ID and current execution config.
    Missing accounts raise LookupError; no fallback account or broker is seeded.
    """
    with session.no_autoflush:
        broker = rehydrate_sim_broker(session, account_id, config=config)
    set_active_runtime(SimulationRuntimeContext(broker, account_id, session_factory, config))
    return broker


def simulation_activation_enabled(settings: Settings) -> bool:
    """Only the operator profile on the simulation broker activates at startup."""
    return (settings.runtime_profile == "real_market_operator"
            and settings.broker_provider == "simulation")


def activate_operator_simulation_runtime(
    settings: Settings, session_factory: SessionFactory | None = None, *,
    config: ExecutionConfig | None = None,
) -> SimulationRuntimeContext | None:
    """Rehydrate the durable SIM/operator account into an active runtime.

    Read-only by construction: the account is looked up, never created, and
    rehydration copies persisted figures without writing any of them back. A
    missing account, unusable state, or unreachable simulation schema leaves the
    process with no broker - the backend keeps serving and /trading reports
    itself unavailable - because seeding cash or silently starting empty would
    both invent an account state nobody chose.
    """
    if not simulation_activation_enabled(settings):
        return None
    if session_factory is None:
        # Resolved here rather than at import so the engine bound at startup is
        # the one this process actually serves from.
        from app.core import database

        session_factory = database.SessionLocal
    try:
        with session_factory() as session:
            account = SimulationStateRepository(session).get_account(
                OPERATOR_BROKER_TYPE, OPERATOR_ACCOUNT_KEY)
            if account is None:
                logger.error("SIMULATION RUNTIME INACTIVE: no %s/%s account; trading unavailable",
                             OPERATOR_BROKER_TYPE, OPERATOR_ACCOUNT_KEY)
                return None
            account_id = account.id
            with session.no_autoflush:
                broker = rehydrate_sim_broker(session, account_id, config=config)
    except (LookupError, SimulationStateInconsistent, SQLAlchemyError) as error:
        logger.error("SIMULATION RUNTIME INACTIVE: %s; trading unavailable", error)
        return None
    context = SimulationRuntimeContext(broker, account_id, session_factory, config)
    set_active_runtime(context)
    logger.info("SIMULATION RUNTIME ACTIVE: account=%s cash=%s positions=%d",
                account_id, broker.cash, len(broker.get_positions()))
    return context
