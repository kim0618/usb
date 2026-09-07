"""Explicit runtime composition on isolated migrated databases only."""

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.execution.config import ExecutionConfig
from app.main import create_app
from app.services.execution import ExecutionService
from app.services.simulation import SimulationStateInconsistent
from app.services.simulation_runtime import (
    clear_active_sim_broker, get_active_sim_broker, initialize_simulation_runtime,
    set_active_sim_broker,
)
from backend.tests.test_sim_broker_rehydration import (
    CASH, START, bar, intent, opened, position, seeded, snapshot, trade,
)
from backend.tests.test_simulation_persistence_schema import _migrated_engine


@pytest.fixture(autouse=True)
def ownership():
    assert get_active_sim_broker() is None
    yield
    clear_active_sim_broker()


@pytest.fixture
def engine(tmp_path, monkeypatch):
    db = _migrated_engine(tmp_path / "runtime.sqlite3", monkeypatch, "20260906_0009")
    try:
        yield db
    finally:
        db.dispose()
        get_settings.cache_clear()


def forbid_writes(engine):
    def check(_connection, _cursor, statement, _parameters, _context, _many):
        assert statement.lstrip().split()[0].upper() in {"SELECT", "PRAGMA"}, statement
    event.listen(engine, "before_cursor_execute", check)


def test_missing_account_no_seed(engine):
    with Session(engine) as session:
        before = snapshot(session)
        forbid_writes(engine)
        with pytest.raises(LookupError, match="simulation account does not exist"):
            initialize_simulation_runtime(session, 999)
        assert get_active_sim_broker() is None
        assert snapshot(session) == before


def test_empty_account_identity_duplicate_and_clear(engine):
    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        before = snapshot(session)
        forbid_writes(engine)
        config = ExecutionConfig(partial_fill_enabled=True)
        broker = initialize_simulation_runtime(session, account_id, config=config)
        assert get_active_sim_broker() is broker
        assert broker.config is config
        assert broker.cash == broker.starting_cash == CASH
        assert broker.get_positions() == broker.get_open_orders() == broker.get_fills() == ()
        assert broker._trades == {} and broker._sequence == 0 and broker.execution_scope
        for register in (lambda: set_active_sim_broker(broker),
                         lambda: initialize_simulation_runtime(session, account_id)):
            with pytest.raises(RuntimeError, match="active simulation broker already registered"):
                register()
            assert get_active_sim_broker() is broker
        state = broker.state_snapshot()
        clear_active_sim_broker()
        clear_active_sim_broker()
        assert get_active_sim_broker() is None
        assert broker.state_snapshot() == state
        assert snapshot(session) == before


def test_persisted_restart_exact_read_only(engine):
    with Session(engine) as session:
        account_id = opened(session, cash=Decimal("82957.50"))
        held, live_trade = position(realized="-42.50"), trade()
        seeded(session, account_id, positions=[held], trades=[live_trade])
        before = snapshot(session)
        forbid_writes(engine)
        first = initialize_simulation_runtime(session, account_id)
        assert first.cash == Decimal("82957.50")
        assert first.get_position("TSLA") == held
        assert first.get_trade("TSLA") == live_trade
        first._sequence = 12
        clear_active_sim_broker()
    with Session(engine) as session:
        second = initialize_simulation_runtime(session, account_id)
        assert second is get_active_sim_broker() and second is not first
        assert second.state_snapshot() == first.state_snapshot()
        assert second.execution_scope != first.execution_scope
        assert second._sequence == 0
        assert snapshot(session) == before


@pytest.mark.parametrize("previous", [False, True])
@pytest.mark.parametrize("invalid", ["position_only", "trade_only", "quantity", "risk", "missing"])
def test_failures_preserve_ownership_and_rows(engine, previous, invalid):
    with Session(engine) as session:
        good_id = opened(session, "good")
        bad_id = opened(session, "bad")
        positions = [] if invalid in {"trade_only", "missing"} else [
            position(quantity=Decimal("0")) if invalid == "quantity" else position()]
        trades = [] if invalid in {"position_only", "missing"} else [
            trade(risk="0") if invalid == "risk" else trade()]
        seeded(session, bad_id, positions=positions, trades=trades)
        old = initialize_simulation_runtime(session, good_id) if previous else None
        before = snapshot(session)
        forbid_writes(engine)
        with pytest.raises(LookupError if invalid == "missing" else SimulationStateInconsistent):
            initialize_simulation_runtime(session, 999 if invalid == "missing" else bad_id)
        assert get_active_sim_broker() is old
        assert snapshot(session) == before


@pytest.mark.asyncio
async def test_api_projection_history_and_shutdown(engine):
    with Session(engine) as session:
        account_id = opened(session, cash=Decimal("82957.50"))
        seeded(session, account_id, positions=[position()], trades=[trade()])
        # A separate account's execution history must remain visible as before.
        history_id = opened(session, "history")
        session.commit()
        from app.broker.sim import SimBroker
        ExecutionService(SimBroker(CASH), session, account_id=history_id).execute_and_persist(
            intent(), [bar(1)], updated_at=START)
        before = snapshot(session)
    app = create_app()
    async def override():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_db] = override
    forbid_writes(engine)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            empty = (await client.get("/api/v1/trading")).json()
            assert empty == {"broker_mode": "SIMULATION", "availability": "NO_ACTIVE_SIM_BROKER",
                             "account": None, "open_positions": [], "open_orders": [], "strategy_states": []}
            assert (await client.get("/api/v1/trading/positions/TSLA")).status_code == 404
            history = {name: (await client.get(f"/api/v1/trading/{name}")).json() for name in ("orders", "fills")}
            assert len(history["orders"]) == len(history["fills"]) == 1
            with Session(engine) as session:
                broker = initialize_simulation_runtime(session, account_id)
            result = (await client.get("/api/v1/trading")).json()
            assert result.keys() == empty.keys()
            assert result["availability"] == "AVAILABLE"
            assert result["account"]["cash"] == "82957.50"
            assert result["account"]["equity"] is None
            assert result["open_orders"] == [] and broker.get_fills() == ()
            projected = result["open_positions"][0]
            assert projected["symbol"] == "TSLA"
            assert projected["quantity"] == str(position().quantity)
            assert projected["average_price"] == str(position().average_price)
            detail = await client.get("/api/v1/trading/positions/tsla")
            assert detail.status_code == 200 and detail.json() == projected
            assert (await client.get("/api/v1/trading/positions/UNKNOWN")).status_code == 404
            for name, rows in history.items():
                assert (await client.get(f"/api/v1/trading/{name}")).json() == rows
            assert get_active_sim_broker() is broker
    assert get_active_sim_broker() is None
    assert broker.get_position("TSLA") == position()
    with Session(engine) as session:
        assert snapshot(session) == before


@pytest.mark.asyncio
async def test_default_startup_on_isolated_0008_never_queries_simulation(tmp_path, monkeypatch):
    db = _migrated_engine(tmp_path / "operator-boundary.sqlite3", monkeypatch, "20260901_0008")
    app = create_app()
    statements = []
    event.listen(db, "before_cursor_execute", lambda c, u, sql, p, x, m: statements.append(sql))
    async def override():
        with Session(db) as session:
            yield session
    app.dependency_overrides[get_db] = override
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.get("/health")).status_code == 200
                assert (await client.get("/api/v1/trading")).json()["availability"] == "NO_ACTIVE_SIM_BROKER"
        assert get_active_sim_broker() is None
        assert all("simulation_" not in sql.lower() for sql in statements)
        with db.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "20260901_0008"
    finally:
        db.dispose()
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_empty_account_api_and_active_open_orders(engine):
    from app.execution.config import ExecutionConfig

    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        broker = initialize_simulation_runtime(
            session, account_id, config=ExecutionConfig(partial_fill_enabled=True))
        before = snapshot(session)
    app = create_app()
    async def override():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_db] = override
    forbid_writes(engine)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = (await client.get("/api/v1/trading")).json()
        assert result["account"]["cash"] == result["account"]["equity"] == str(CASH)
        assert result["open_positions"] == result["open_orders"] == []
        order = broker.submit_order(intent(), [bar(1)])
        assert broker.get_open_orders() == (order,)
        result = (await client.get("/api/v1/trading")).json()
        assert result["open_orders"][0]["order_id"] == order.id
        assert result["open_orders"][0]["filled_quantity"] == str(order.filled_quantity)
        assert (await client.get("/api/v1/trading/orders")).json() == []
        assert (await client.get("/api/v1/trading/fills")).json() == []
    with Session(engine) as session:
        assert snapshot(session) == before


def test_composition_does_not_autoflush_caller_pending_rows(engine):
    from app.models.simulation import SimulationAccountRecord

    with Session(engine) as session:
        account_id = opened(session)
        session.commit()
        before = snapshot(session)
        pending = SimulationAccountRecord(account_key="pending", broker_type="SIM",
                                          base_currency="USD", initial_cash=CASH, cash=CASH,
                                          created_at=START, updated_at=START)
        session.add(pending)
        forbid_writes(engine)
        initialize_simulation_runtime(session, account_id)
        assert pending in session.new and pending.id is None
        session.rollback()
        assert snapshot(session) == before
