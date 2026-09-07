"""Ordinary operator startup owns the broker, and the runner executes durably.

Two contracts meet here. Startup must hand the process a broker rebuilt from the
durable SIM/operator account without writing anything, and the lifecycle runner
must reach that same broker through the durable execution transaction rather
than the in-memory one. Everything runs against an isolated migrated 0009
database; the real operator file is never opened.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import Engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.broker.domain import OrderStatus, RejectionReason, TradeStatus
from app.core import database
from app.core.config import get_settings
from app.core.database import get_db
from app.execution.config import ExecutionConfig
from app.main import create_app
from app.market.domain import MarketSession, MinuteBar
from app.models.execution import ExecutionFillRecord, ExecutionOrderRecord
from app.models.simulation import (
    SimulationAccountRecord, SimulationPositionRecord, SimulationTradeRecord,
)
from app.repositories.simulation import SimulationStateRepository
from app.risk.domain import AccountSnapshot, Currency, DailyTradingState, PortfolioSnapshot
from app.services.execution import ExecutionService
from app.services.simulation_runtime import (
    activate_operator_simulation_runtime, clear_active_sim_broker, get_active_runtime,
    get_active_sim_broker, simulation_activation_enabled,
)
from app.strategy.domain import DecisionType, StrategyDecision
from app.strategy.lifecycle import StrategyBook, StrategyPhase, StrategyState
from app.strategy.runner import StrategyLifecycleRunner
from backend.tests.test_simulation_persistence_schema import REVISION, _migrated_engine

ET = ZoneInfo("America/New_York")
DAY = date(2026, 7, 2)
# 09:45 ET signal, 09:46 ET next bar: the Stage 10B deterministic entry fixture.
AS_OF = datetime(2026, 7, 2, 9, 45, tzinfo=ET)
NEXT_BAR = datetime(2026, 7, 2, 9, 46, tzinfo=ET)
CASH = Decimal("100000")
FILL_PRICE = Decimal("102.153")
QUANTITY = Decimal("166.6666666666666666666666667")
FILLED_CASH = Decimal("82957.50")


# Fixtures ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def ownership():
    assert get_active_sim_broker() is None
    yield
    clear_active_sim_broker()


def _operator_profile(monkeypatch) -> None:
    monkeypatch.setenv("RUNTIME_PROFILE", "real_market_operator")
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "kiwoom")
    get_settings.cache_clear()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A migrated 0009 database bound in place of the process session factory."""
    engine = _migrated_engine(tmp_path / "operator.sqlite3", monkeypatch, REVISION)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    # Activation resolves SessionLocal at call time, so the real operator file
    # stays closed even though the operator profile names it.
    monkeypatch.setattr(database, "SessionLocal", factory)
    try:
        yield engine, factory
    finally:
        engine.dispose()
        get_settings.cache_clear()


@pytest.fixture
def operator(isolated, monkeypatch):
    _operator_profile(monkeypatch)
    return isolated


def account(session: Session, cash: Decimal = CASH) -> int:
    return SimulationStateRepository(session).create_account(
        broker_type="SIM", account_key="operator", base_currency="USD",
        initial_cash=CASH, cash=cash, created_at=AS_OF).id


def seed_operator_account(factory, cash: Decimal = CASH) -> int:
    with factory() as session:
        account_id = account(session, cash)
        session.commit()
        return account_id


def counts(session: Session) -> dict[str, int]:
    return {name: session.scalar(select(func.count()).select_from(model)) for name, model in (
        ("accounts", SimulationAccountRecord), ("orders", ExecutionOrderRecord),
        ("fills", ExecutionFillRecord), ("positions", SimulationPositionRecord),
        ("trades", SimulationTradeRecord))}


def account_state(session: Session) -> list[tuple]:
    return [(row.id, row.broker_type, row.account_key, row.cash, row.state_version)
            for row in session.scalars(select(SimulationAccountRecord).order_by(SimulationAccountRecord.id))]


def forbid_writes(engine: Engine) -> None:
    def check(_connection, _cursor, statement, _parameters, _context, _many):
        assert statement.lstrip().split()[0].upper() in {"SELECT", "PRAGMA"}, statement
    event.listen(engine, "before_cursor_execute", check)


def bar(at: datetime = NEXT_BAR, price: float = 102.0) -> MinuteBar:
    return MinuteBar(symbol="TSLA", timestamp=at, open=price, high=price + .1, low=price - .1,
                     close=price, volume=1000, session=MarketSession.REGULAR, observed_at=at,
                     available_at=at + timedelta(minutes=1))


def entry_signal() -> tuple[StrategyState, StrategyDecision]:
    """The frozen Strategy/Risk inputs; nothing in this module changes either."""
    state = StrategyState("TSLA", DAY, phase=StrategyPhase.ENTRY_SIGNALLED, book=StrategyBook.ACTUAL,
                          entry_price=Decimal("102"), initial_stop=Decimal("99"),
                          active_stop=Decimal("99"), holding_day_number=1)
    return state, StrategyDecision("TSLA", DecisionType.ENTER, "ENTRY", AS_OF,
                                   strategy_version="strategy_v0")


def run_entry(runner: StrategyLifecycleRunner, *, bars: tuple[MinuteBar, ...] = (bar(),)):
    state, decision = entry_signal()
    return runner.execute_entry(
        state=state, decision=decision,
        account=AccountSnapshot(CASH, CASH, Currency.USD, AS_OF),
        portfolio=PortfolioSnapshot((), Decimal("0"), Decimal("0"), AS_OF),
        market_bars=bars, instrument_currency=Currency.USD, created_at=AS_OF,
        actual_risk_state=DailyTradingState(DAY))


def count_submissions(broker) -> list:
    """Shadow the bound method so every broker submission is observed."""
    seen: list = []
    original = broker.submit_order

    def counted(intent, market_bars):
        seen.append(intent)
        return original(intent, market_bars)

    broker.submit_order = counted
    return seen


# Startup activation --------------------------------------------------------

@pytest.mark.asyncio
async def test_normal_operator_startup_rehydrates_without_touching_the_database(operator):
    """No smoke script calls initialize: the app's own lifespan is the owner."""
    engine, factory = operator
    account_id = seed_operator_account(factory)
    with factory() as session:
        before, state_before = counts(session), account_state(session)
    app = create_app()

    async def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    forbid_writes(engine)
    async with app.router.lifespan_context(app):
        runtime = get_active_runtime()
        assert runtime is not None and runtime.durable
        assert get_active_sim_broker() is runtime.broker
        assert runtime.account_id == account_id
        assert runtime.session_factory is factory
        assert runtime.config == ExecutionConfig()
        assert runtime.broker.cash == CASH
        assert runtime.broker.get_positions() == () and runtime.broker._trades == {}
        assert runtime.broker._sequence == 0 and runtime.broker.execution_scope
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            body = (await client.get("/api/v1/trading")).json()
            assert body["availability"] == "AVAILABLE"
            assert Decimal(body["account"]["cash"]) == CASH
    # Shutdown releases ownership and saves nothing.
    assert get_active_sim_broker() is None
    with factory() as session:
        assert counts(session) == before and account_state(session) == state_before


@pytest.mark.asyncio
async def test_startup_without_an_account_never_seeds_one(operator):
    engine, factory = operator
    app = create_app()

    async def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    forbid_writes(engine)
    async with app.router.lifespan_context(app):
        assert get_active_runtime() is None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200
            body = (await client.get("/api/v1/trading")).json()
            assert body["availability"] == "NO_ACTIVE_SIM_BROKER"
            assert body["account"] is None
    with factory() as session:
        assert counts(session)["accounts"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["position_only", "trade_only"])
async def test_inconsistent_state_fails_closed_with_the_backend_still_up(operator, invalid):
    """Neither an empty broker nor a repair: unusable state means no trading."""
    from backend.tests.test_sim_broker_rehydration import position, trade

    engine, factory = operator
    with factory() as session:
        account_id = account(session)
        repository = SimulationStateRepository(session)
        if invalid == "position_only":
            repository.save_position(account_id, position())
        else:
            repository.save_trade(account_id, trade(), updated_at=AS_OF)
        session.commit()
        before = counts(session)
    app = create_app()

    async def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    forbid_writes(engine)
    async with app.router.lifespan_context(app):
        assert get_active_runtime() is None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/api/v1/trading")).json()["availability"] == "NO_ACTIVE_SIM_BROKER"
    with factory() as session:
        assert counts(session) == before


@pytest.mark.asyncio
async def test_default_profile_never_activates_even_with_an_operator_account(isolated):
    """Gating is on the profile, not on whether an account happens to exist."""
    engine, factory = isolated
    seed_operator_account(factory)
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute",
                 lambda c, u, sql, p, x, m: statements.append(sql))
    app = create_app()

    async def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with app.router.lifespan_context(app):
        assert get_settings().runtime_profile == "default"
        assert not simulation_activation_enabled(get_settings())
        assert get_active_runtime() is None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/trading")).json()["availability"] == "NO_ACTIVE_SIM_BROKER"
    assert all("simulation_" not in sql.lower() for sql in statements)


def test_activation_gate_reads_profile_and_broker_provider(monkeypatch):
    from app.core.config import Settings

    default = Settings(_env_file=None)
    operator_settings = Settings(_env_file=None, runtime_profile="real_market_operator",
                                 market_data_provider="kiwoom")
    assert not simulation_activation_enabled(default)
    assert simulation_activation_enabled(operator_settings)
    # The gate refuses on the broker provider too; the config validator currently
    # admits only "simulation", so the branch is asserted directly.
    assert not simulation_activation_enabled(
        operator_settings.model_copy(update={"broker_provider": "live"}))


def test_account_is_found_by_key_not_by_a_hardcoded_id(operator):
    """Row ids are database facts; the lookup must not assume the first one."""
    engine, factory = operator
    with factory() as session:
        SimulationStateRepository(session).create_account(
            broker_type="SIM", account_key="other", base_currency="USD",
            initial_cash=CASH, cash=CASH, created_at=AS_OF)
        session.commit()
        account_id = account(session)
        session.commit()
    assert account_id != 1
    forbid_writes(engine)
    runtime = activate_operator_simulation_runtime(get_settings(), config=ExecutionConfig())
    assert runtime is not None and runtime.account_id == account_id
    with factory() as session:
        stored = SimulationStateRepository(session).get_account("SIM", "operator")
        assert stored.id == runtime.account_id


# Runner durable execution --------------------------------------------------

def activated(factory, cash: Decimal = CASH):
    runtime = activate_operator_simulation_runtime(get_settings(), config=ExecutionConfig())
    assert runtime is not None
    return runtime


def test_runner_binds_to_the_activated_broker_and_account(operator):
    _, factory = operator
    account_id = seed_operator_account(factory)
    runtime = activated(factory)
    runner = StrategyLifecycleRunner.for_active_runtime()
    assert runner.broker is get_active_sim_broker()
    assert runner.execution.broker is get_active_sim_broker()
    assert runner.runtime is runtime
    assert runner.runtime.account_id == account_id
    assert runner.runtime.session_factory is factory


def test_runner_refuses_a_broker_the_runtime_does_not_own(operator):
    from app.broker.sim import SimBroker

    _, factory = operator
    seed_operator_account(factory)
    runtime = activated(factory)
    with pytest.raises(ValueError, match="different broker"):
        StrategyLifecycleRunner(SimBroker(CASH), runtime=runtime)


def test_filled_entry_submits_once_through_the_durable_path(operator, monkeypatch):
    _, factory = operator
    account_id = seed_operator_account(factory)
    activated(factory)
    runner = StrategyLifecycleRunner.for_active_runtime()
    submissions = count_submissions(runner.broker)
    calls = {"execute": 0, "persist": 0}
    original = ExecutionService.execute_and_persist
    monkeypatch.setattr(ExecutionService, "execute",
                        lambda *a, **k: calls.__setitem__("execute", calls["execute"] + 1))

    def counted(self, *args, **kwargs):
        calls["persist"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ExecutionService, "execute_and_persist", counted)
    result = run_entry(runner)
    assert result.order is not None and result.order.status is OrderStatus.FILLED
    assert calls == {"execute": 0, "persist": 1}
    assert len(submissions) == 1
    with factory() as session:
        assert counts(session) == {"accounts": 1, "orders": 1, "fills": 1,
                                   "positions": 1, "trades": 1}
        assert SimulationStateRepository(session).get_account_by_id(account_id).state_version == 1


def test_filled_entry_persists_exact_broker_figures(operator):
    _, factory = operator
    account_id = seed_operator_account(factory)
    activated(factory)
    runner = StrategyLifecycleRunner.for_active_runtime()
    result = run_entry(runner)
    broker = runner.broker
    assert result.state.phase is StrategyPhase.POSITION_OPEN
    assert result.risk is not None and result.risk.approved
    fills = broker.get_fills(result.order.id)
    assert len(fills) == 1 and fills[0].fill_price == FILL_PRICE
    assert result.order.filled_quantity == QUANTITY
    assert broker.cash == FILLED_CASH
    with factory() as session:
        repository = SimulationStateRepository(session)
        stored = repository.get_account_by_id(account_id)
        assert stored.cash == FILLED_CASH == broker.cash
        assert stored.state_version == 1
        position = repository.get_position(account_id, "TSLA")
        assert position == broker.get_position("TSLA")
        assert position.quantity == QUANTITY and position.average_price == FILL_PRICE
        trade = repository.get_open_trade(account_id, "TSLA")
        assert trade == broker.get_trade("TSLA") and trade.status is TradeStatus.OPEN
        order = session.scalars(select(ExecutionOrderRecord)).one()
        assert order.symbol == "TSLA" and order.status == "FILLED"
        assert order.filled_quantity == QUANTITY
        assert session.scalars(select(ExecutionFillRecord)).one().fill_price == FILL_PRICE


def test_rejected_entry_persists_the_order_and_moves_nothing_else(operator):
    """NO_NEXT_BAR still owes a durable order; simulation state stays untouched."""
    _, factory = operator
    account_id = seed_operator_account(factory)
    activated(factory)
    runner = StrategyLifecycleRunner.for_active_runtime()
    submissions = count_submissions(runner.broker)
    with factory() as session:
        before = account_state(session)
    result = run_entry(runner, bars=())
    assert result.order is not None and result.order.status is OrderStatus.REJECTED
    assert result.order.rejection_reason is RejectionReason.NO_NEXT_BAR
    assert len(submissions) == 1
    assert runner.broker.cash == CASH and runner.broker.get_positions() == ()
    with factory() as session:
        assert counts(session) == {"accounts": 1, "orders": 1, "fills": 0,
                                   "positions": 0, "trades": 0}
        assert account_state(session) == before
        assert SimulationStateRepository(session).get_account_by_id(account_id).state_version == 0


def test_restart_after_a_runner_fill_rehydrates_what_the_runner_wrote(operator):
    _, factory = operator
    seed_operator_account(factory)
    activated(factory)
    first = StrategyLifecycleRunner.for_active_runtime()
    run_entry(first)
    position, trade = first.broker.get_position("TSLA"), first.broker.get_trade("TSLA")
    first.broker._sequence = 9
    clear_active_sim_broker()

    runtime = activated(factory)
    second = runtime.broker
    assert second is not first.broker
    assert second.execution_scope != first.broker.execution_scope
    assert second._sequence == 0
    assert second.cash == FILLED_CASH
    assert second.get_position("TSLA") == position
    assert second.get_trade("TSLA") == trade
    assert second.get_open_orders() == () and second.get_fills() == ()
    assert StrategyLifecycleRunner.for_active_runtime().broker is second


@pytest.mark.asyncio
async def test_api_projects_the_position_the_runner_created(operator):
    engine, factory = operator
    seed_operator_account(factory)
    app = create_app()

    async def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with app.router.lifespan_context(app):
        runner = StrategyLifecycleRunner.for_active_runtime()
        result = run_entry(runner)
        assert result.order is not None
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            body = (await client.get("/api/v1/trading")).json()
            assert body["availability"] == "AVAILABLE"
            assert Decimal(body["account"]["cash"]) == FILLED_CASH
            projected = body["open_positions"][0]
            assert projected["symbol"] == "TSLA"
            assert Decimal(projected["quantity"]) == QUANTITY
            assert Decimal(projected["average_price"]) == FILL_PRICE
            assert body["open_orders"] == []
            detail = await client.get("/api/v1/trading/positions/tsla")
            assert detail.status_code == 200 and detail.json() == projected
            orders = (await client.get("/api/v1/trading/orders")).json()
            assert len(orders) == 1 and orders[0]["symbol"] == "TSLA"
            assert orders[0]["status"] == "FILLED"
            assert Decimal(orders[0]["filled_quantity"]) == QUANTITY
            fills = (await client.get("/api/v1/trading/fills")).json()
            assert len(fills) == 1 and fills[0]["symbol"] == "TSLA"
            assert Decimal(fills[0]["fill_price"]) == FILL_PRICE
            assert fills[0]["order_id"] == orders[0]["order_id"]


@pytest.mark.asyncio
async def test_operator_startup_and_durable_entry_never_reach_kiwoom(operator, monkeypatch):
    from app.integrations.kiwoom.client import KiwoomMarketDataClient
    import app.market.factory as factory_module

    engine, factory = operator
    seed_operator_account(factory)
    assert get_settings().kiwoom_mode == "market_data_only"
    assert get_settings().broker_provider == "simulation"
    assert not hasattr(KiwoomMarketDataClient, "submit_order")
    monkeypatch.setattr(KiwoomMarketDataClient, "request",
                        lambda *a, **k: pytest.fail("Kiwoom request during simulation execution"))
    monkeypatch.setattr(factory_module, "build_kiwoom_provider",
                        lambda *a, **k: pytest.fail("Kiwoom provider built at startup"))
    app = create_app()

    async def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with app.router.lifespan_context(app):
        runner = StrategyLifecycleRunner.for_active_runtime()
        result = run_entry(runner)
        assert result.order is not None and result.order.id.startswith("SIM-")
    with factory() as session:
        assert session.scalars(select(ExecutionOrderRecord)).one().broker_type == "SIM"


def test_shadow_runner_without_a_runtime_still_executes_without_persistence(operator):
    """The replay/shadow path keeps its in-memory execution, writing nothing."""
    from app.broker.sim import SimBroker

    _, factory = operator
    seed_operator_account(factory)
    broker = SimBroker(CASH)
    runner = StrategyLifecycleRunner(broker)
    assert runner.runtime is None
    result = run_entry(runner)
    assert result.order is not None and result.order.status is OrderStatus.FILLED
    assert broker.cash == FILLED_CASH
    with factory() as session:
        assert counts(session) == {"accounts": 1, "orders": 0, "fills": 0,
                                   "positions": 0, "trades": 0}
