from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base, create_db_engine
from app.dev.bootstrap_paper_account import bootstrap_paper_account
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord
from app.models.simulation import (
    SimulationAccountRecord, SimulationPositionRecord, SimulationTradeRecord,
)


@pytest.fixture
def sessions(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'paper.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def test_bootstrap_creates_exact_capital_once_and_is_idempotent(sessions) -> None:
    assert bootstrap_paper_account(sessions) == "initialized"
    assert bootstrap_paper_account(sessions) == "already-initialized"
    with sessions() as session:
        rows = list(session.scalars(select(SimulationAccountRecord)))
        assert len(rows) == 1
        account = rows[0]
        assert (account.broker_type, account.account_key, account.base_currency) == (
            "SIM", "operator", "USD")
        assert account.initial_cash == Decimal("7428.92")
        assert account.cash == Decimal("7428.92") and account.state_version == 0
        for model in (SimulationPositionRecord, SimulationTradeRecord,
                      ExecutionOrderRecord, ExecutionFillRecord):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_bootstrap_refuses_to_overwrite_different_existing_account(sessions) -> None:
    bootstrap_paper_account(sessions)
    with sessions() as session:
        account = session.scalar(select(SimulationAccountRecord))
        account.cash = Decimal("7000")
        session.commit()
    with pytest.raises(RuntimeError, match="refusing overwrite"):
        bootstrap_paper_account(sessions)
    with sessions() as session:
        assert session.scalar(select(SimulationAccountRecord)).cash == Decimal("7000")
