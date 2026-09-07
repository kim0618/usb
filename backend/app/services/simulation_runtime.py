"""Single-process broker ownership; activation requires explicit composition.

Default backend startup never calls initialize_simulation_runtime: the operator
DB must be migrated and an account explicitly selected in a later stage.
"""

from threading import Lock

from sqlalchemy.orm import Session

from app.broker.sim import SimBroker
from app.execution.config import ExecutionConfig
from app.services.simulation import rehydrate_sim_broker

_active_sim_broker: SimBroker | None = None
_lock = Lock()


def get_active_sim_broker() -> SimBroker | None:
    with _lock:
        return _active_sim_broker


def set_active_sim_broker(broker: SimBroker) -> None:
    """Reject duplicate registration; callers must explicitly clear first."""
    global _active_sim_broker
    with _lock:
        if _active_sim_broker is not None:
            raise RuntimeError("active simulation broker already registered")
        _active_sim_broker = broker


def clear_active_sim_broker() -> None:
    """Release ownership without mutating the broker or persisting anything."""
    global _active_sim_broker
    with _lock:
        _active_sim_broker = None


def initialize_simulation_runtime(session: Session, account_id: int, *,
                                  config: ExecutionConfig | None = None) -> SimBroker:
    """Hydrate before registration; lookup/consistency errors preserve ownership.

    The caller supplies an existing account ID and current execution config.
    Missing accounts raise LookupError; no fallback account or broker is seeded.
    """
    with session.no_autoflush:
        broker = rehydrate_sim_broker(session, account_id, config=config)
    set_active_sim_broker(broker)
    return broker
