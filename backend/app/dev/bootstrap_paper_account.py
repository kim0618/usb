"""Explicit, fail-closed bootstrap for the Cloud paper simulation account."""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.core import database
from app.core.config import PROJECT_ROOT, REAL_MARKET_DATABASE_URL, get_settings
from app.models.simulation import SimulationAccountRecord
from app.repositories.simulation import SimulationStateRepository

PAPER_INITIAL_CASH = Decimal("7428.92")
PAPER_BASE_CURRENCY = "USD"
PAPER_BROKER_TYPE = "SIM"
PAPER_ACCOUNT_KEY = "operator"


def bootstrap_paper_account(session_factory=database.SessionLocal) -> str:  # type: ignore[no-untyped-def]
    """Create exactly one pristine operator account or verify an exact prior bootstrap."""
    with session_factory() as session:
        try:
            count = int(session.scalar(select(func.count()).select_from(SimulationAccountRecord)) or 0)
            repository = SimulationStateRepository(session)
            existing = repository.get_account(PAPER_BROKER_TYPE, PAPER_ACCOUNT_KEY)
            if existing is not None:
                exact = (
                    count == 1
                    and existing.base_currency == PAPER_BASE_CURRENCY
                    and existing.initial_cash == PAPER_INITIAL_CASH
                    and existing.cash == PAPER_INITIAL_CASH
                    and existing.state_version == 0
                )
                if not exact:
                    raise RuntimeError("paper account exists with different state; refusing overwrite")
                session.rollback()
                return "already-initialized"
            if count:
                raise RuntimeError("another simulation account exists; refusing bootstrap")
            now = datetime.now(timezone.utc)
            repository.create_account(
                broker_type=PAPER_BROKER_TYPE, account_key=PAPER_ACCOUNT_KEY,
                base_currency=PAPER_BASE_CURRENCY, initial_cash=PAPER_INITIAL_CASH,
                cash=PAPER_INITIAL_CASH, created_at=now,
            )
            session.commit()
            return "initialized"
        except Exception:
            session.rollback()
            raise


def main() -> None:
    settings = get_settings()
    if settings.runtime_profile != "real_market_operator":
        raise SystemExit("RUNTIME_PROFILE=real_market_operator is required")
    if not settings.paper_database_url:
        raise SystemExit("PAPER_DATABASE_URL must explicitly select the fresh Paper DB")
    default_operator = f"sqlite:///{(PROJECT_ROOT / 'data/runtime/usb_real_market_review.sqlite3').resolve()}"
    if settings.resolved_database_url == default_operator:
        raise SystemExit(f"refusing protected local operator DB ({REAL_MARKET_DATABASE_URL})")
    print(bootstrap_paper_account())


if __name__ == "__main__":
    main()
