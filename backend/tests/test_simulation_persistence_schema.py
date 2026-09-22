"""Migration head schema contract for durable simulation/accounting tables."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session
from pytest import MonkeyPatch

import app.models  # noqa: F401  # Register every application table.
from app.core.config import PROJECT_ROOT, get_settings
from app.core.database import Base
from app.dev.schema_fingerprint import schema_fingerprint
from app.models.simulation import SimulationAccountRecord, SimulationTradeRecord

REVISION = "20260921_0018"
SIMULATION_TABLES = ("simulation_accounts", "simulation_positions", "simulation_trades", "account_daily_performance")
OPEN_INDEX = "uq_simulation_trades_open_symbol"
# SimBroker fractional sizing produces repeating decimals that must survive exactly.
EXACT_QUANTITY = Decimal("166.6666666666666666666666667")


def _migrated_engine(path: Path, monkeypatch: MonkeyPatch, revision: str = "head") -> Engine:
    monkeypatch.setenv("RUNTIME_PROFILE", "default")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    get_settings.cache_clear()
    command.upgrade(Config(PROJECT_ROOT / "alembic.ini"), revision)
    return create_engine(f"sqlite:///{path}")


@pytest.fixture
def migrated(tmp_path: Path, monkeypatch: MonkeyPatch):
    engine = _migrated_engine(tmp_path / "migration.sqlite3", monkeypatch)
    try:
        yield engine
    finally:
        engine.dispose()
        get_settings.cache_clear()


@pytest.fixture
def created(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'create-all.sqlite3'}")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _open_index_sql(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='index' AND name=:name"), {"name": OPEN_INDEX}
        ).scalar()


def test_migration_adds_exactly_the_simulation_tables(migrated: Engine) -> None:
    tables = {name for name in inspect(migrated).get_table_names() if name != "alembic_version"}
    # Previous head plus the isolated entry-drift, premarket-volume, and paper entry
    # evaluation analytics tables, and the recorded paper scanner universe.
    assert len(tables) == 22
    assert set(SIMULATION_TABLES) <= tables


def test_alembic_current_is_head(migrated: Engine) -> None:
    with migrated.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [REVISION]
        assert connection.execute(text("PRAGMA quick_check")).scalars().all() == ["ok"]
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []


def test_create_all_matches_migration_head(created: Engine, migrated: Engine) -> None:
    assert schema_fingerprint(created) == schema_fingerprint(migrated)


def test_open_trade_partial_unique_index_predicate(created: Engine, migrated: Engine) -> None:
    """schema_fingerprint compares (name, columns, unique) only, so assert the predicate here."""
    for engine in (created, migrated):
        index = next(i for i in inspect(engine).get_indexes("simulation_trades") if i["name"] == OPEN_INDEX)
        assert index["unique"] and list(index["column_names"]) == ["account_id", "symbol"]
        assert str(index["dialect_options"]["sqlite_where"]) == "status = 'OPEN'"
    assert _open_index_sql(created) == _open_index_sql(migrated) is not None


def test_migration_creates_no_business_rows(migrated: Engine) -> None:
    with migrated.connect() as connection:
        for table in SIMULATION_TABLES:
            assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


def test_daily_performance_is_unique_per_account_and_trading_date(migrated: Engine) -> None:
    constraint = next(item for item in inspect(migrated).get_unique_constraints("account_daily_performance")
                      if item["name"] == "uq_account_daily_performance_account_date")
    assert constraint["column_names"] == ["account_id", "trading_date"]


def test_downgrade_and_reupgrade_restores_the_schema(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    path = tmp_path / "roundtrip.sqlite3"
    engine = _migrated_engine(path, monkeypatch)
    try:
        fingerprint = schema_fingerprint(engine)
        index_sql = _open_index_sql(engine)
        config = Config(PROJECT_ROOT / "alembic.ini")
        command.downgrade(config, "20260901_0008")
        assert not set(SIMULATION_TABLES) & set(inspect(engine).get_table_names())
        command.upgrade(config, "head")
        assert schema_fingerprint(engine) == fingerprint
        assert _open_index_sql(engine) == index_sql
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_active_analysis_backfill_preserves_previous_latest_authority(
        tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    path = tmp_path / "authority-backfill.sqlite3"
    engine = _migrated_engine(path, monkeypatch, "20260908_0011")
    run_sql = text("""INSERT INTO scanner_runs
        (id, trading_date, started_at, completed_at, status, provider, score_version,
         universe_count, excluded_count, candidate_count, top8_count, created_at)
        VALUES (:id, '2026-09-11', :at, :at, 'COMPLETED', 'TEST', 'quant_v0', 0, 0, 0, 0, :at)""")
    analysis_sql = text("""INSERT INTO gpt_analyses
        (id, scanner_run_id, trading_date, provider, model, prompt_version, schema_version,
         evidence_version, analysis_at, imported_at, status, raw_json, payload_hash)
        VALUES (:id, :run_id, '2026-09-11', 'GPT', 'm', 'p', 's', 'e', :analysis_at,
                :imported_at, :status, '{}', :payload_hash)""")
    with engine.begin() as connection:
        connection.execute(run_sql, [{"id": index, "at": "2026-09-11T20:00:00+00:00"}
                                     for index in range(1, 5)])
        connection.execute(analysis_sql, [
            {"id": 100, "run_id": 1, "analysis_at": "2026-09-13T02:00:00+00:00", "imported_at": "2026-09-13T03:00:00+00:00", "status": "IMPORTED", "payload_hash": "a" * 64},
            {"id": 200, "run_id": 1, "analysis_at": "2026-09-12T02:00:00+00:00", "imported_at": "2026-09-14T03:00:00+00:00", "status": "IMPORTED", "payload_hash": "b" * 64},
            {"id": 300, "run_id": 2, "analysis_at": "2026-09-13T02:00:00+00:00", "imported_at": "2026-09-13T03:00:00+00:00", "status": "IMPORTED", "payload_hash": "c" * 64},
            {"id": 301, "run_id": 2, "analysis_at": "2026-09-13T02:00:00+00:00", "imported_at": "2026-09-13T03:00:00+00:00", "status": "IMPORTED", "payload_hash": "d" * 64},
            {"id": 400, "run_id": 3, "analysis_at": "2026-09-14T02:00:00+00:00", "imported_at": "2026-09-14T03:00:00+00:00", "status": "IMPORTED", "payload_hash": "e" * 64},
            {"id": 401, "run_id": 3, "analysis_at": "2026-09-12T02:00:00+00:00", "imported_at": "2026-09-15T03:00:00+00:00", "status": "IMPORTED", "payload_hash": "f" * 64},
            {"id": 500, "run_id": 4, "analysis_at": "2026-09-13T02:00:00+00:00", "imported_at": "2026-09-13T03:00:00+00:00", "status": "IMPORTED", "payload_hash": "0" * 64},
            {"id": 501, "run_id": 4, "analysis_at": "2026-09-14T02:00:00+00:00", "imported_at": "2026-09-14T03:00:00+00:00", "status": "FAILED", "payload_hash": "1" * 64},
        ])
    engine.dispose()
    command.upgrade(Config(PROJECT_ROOT / "alembic.ini"), "head")
    upgraded = create_engine(f"sqlite:///{path}")
    try:
        with upgraded.connect() as connection:
            assert connection.execute(text(
                "SELECT id, active_gpt_analysis_id FROM scanner_runs ORDER BY id"
            )).fetchall() == [(1, 100), (2, 301), (3, 400), (4, 500)]
            assert connection.execute(text("PRAGMA quick_check")).scalars().all() == ["ok"]
            assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
    finally:
        upgraded.dispose()
        get_settings.cache_clear()


def test_server_defaults_apply_when_columns_are_omitted(created: Engine, migrated: Engine) -> None:
    account = ("INSERT INTO simulation_accounts "
               "(account_key, broker_type, base_currency, initial_cash, cash, created_at, updated_at) "
               "VALUES ('k', 'SIM', 'USD', '100000', '100000', :now, :now)")
    trade = ("INSERT INTO simulation_trades "
             "(account_id, trade_uid, symbol, entry_time, initial_quantity, total_quantity, "
             "average_entry_price, gross_pnl, net_pnl, planned_initial_risk, gross_r, net_r, "
             "total_cost, status, created_at, updated_at) "
             "VALUES (1, 'uid', 'TSLA', :now, '1', '1', '1', '0', '0', '1', '0', '0', '0', 'OPEN', :now, :now)")
    now = {"now": "2026-09-06T00:00:00+00:00"}
    for engine in (created, migrated):
        with engine.begin() as connection:
            connection.execute(text(account), now)
            assert connection.execute(text("SELECT state_version FROM simulation_accounts")).scalar() == 0
            connection.execute(text(trade), now)
            assert connection.execute(text(
                "SELECT entry_notional, exit_notional, sold_quantity, ambiguous_bar_count FROM simulation_trades"
            )).one() == ("0", "0", "0", 0)


def test_decimal_and_timestamp_round_trip_exactly(created: Engine) -> None:
    moment = datetime(2026, 9, 6, 13, 45, tzinfo=timezone.utc)
    with Session(created) as session:
        account = SimulationAccountRecord(
            account_key="k", broker_type="SIM", base_currency="USD",
            initial_cash=Decimal("100000"), cash=EXACT_QUANTITY, created_at=moment, updated_at=moment)
        session.add(account)
        session.flush()
        assert account.state_version == 0
        trade = SimulationTradeRecord(
            account_id=account.id, trade_uid="uid", symbol="TSLA", entry_time=moment,
            initial_quantity=EXACT_QUANTITY, total_quantity=EXACT_QUANTITY,
            average_entry_price=Decimal("102.153"), gross_pnl=Decimal("0"), net_pnl=Decimal("0"),
            planned_initial_risk=Decimal("1000"), gross_r=Decimal("0"), net_r=Decimal("0"),
            total_cost=Decimal("0"), status="OPEN", created_at=moment, updated_at=moment)
        session.add(trade)
        session.commit()
    with Session(created) as session:
        stored = session.get(SimulationAccountRecord, 1)
        assert stored is not None and stored.cash == EXACT_QUANTITY and str(stored.cash) == str(EXACT_QUANTITY)
        assert stored.created_at == moment and stored.created_at.tzinfo is not None
        assert session.get(SimulationTradeRecord, 1).entry_notional == Decimal("0")
