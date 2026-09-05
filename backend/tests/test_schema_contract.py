"""Keep declarative metadata aligned with the immutable Alembic 0008 schema."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import re

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, inspect, text
from sqlalchemy.orm import Session
from pytest import MonkeyPatch

import app.models  # noqa: F401  # Register every application table.
from app.core.config import PROJECT_ROOT, get_settings
from app.core.database import Base
from app.models.execution import ShadowTradeRecord
from app.models.runtime import RuntimeFailureRecord


def _normalize_default(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    if normalized.casefold() == "false":
        return "0"
    if normalized in {"'0'", '"0"'}:
        return "0"
    if normalized in {"'{}'", '"{}"'}:
        return "{}"
    return normalized


def _normalize_sql(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _schema_fingerprint(engine: Engine) -> dict[str, object]:
    inspector = inspect(engine)
    tables = sorted(name for name in inspector.get_table_names() if name != "alembic_version")
    return {
        "tables": tables,
        "columns": {
            table: {
                column["name"]: (
                    str(column["type"]).casefold(),
                    column["nullable"],
                    _normalize_default(column["default"]),
                    bool(column.get("primary_key")),
                )
                for column in inspector.get_columns(table)
            }
            for table in tables
        },
        "primary_keys": {
            table: tuple(sorted(inspector.get_pk_constraint(table).get("constrained_columns") or ()))
            for table in tables
        },
        "foreign_keys": {
            table: sorted(
                (
                    tuple(foreign_key["constrained_columns"]),
                    foreign_key["referred_table"],
                    tuple(foreign_key["referred_columns"]),
                    tuple(sorted((foreign_key.get("options") or {}).items())),
                )
                for foreign_key in inspector.get_foreign_keys(table)
            )
            for table in tables
        },
        "unique_constraints": {
            table: sorted(
                tuple(sorted(constraint["column_names"]))
                for constraint in inspector.get_unique_constraints(table)
            )
            for table in tables
        },
        "indexes": {
            table: sorted(
                (index["name"], tuple(index["column_names"]), index["unique"])
                for index in inspector.get_indexes(table)
            )
            for table in tables
        },
        "check_constraints": {
            table: sorted(
                (constraint["name"], _normalize_sql(constraint["sqltext"]))
                for constraint in inspector.get_check_constraints(table)
            )
            for table in tables
        },
    }


def _runtime_failure_values() -> dict[str, object]:
    return {
        "failure_code": "TEST_FAILURE",
        "severity": "WARNING",
        "component": "schema_contract",
        "message": "isolated contract test",
        "occurred_at": "2026-09-06T00:00:00+00:00",
        "created_at": "2026-09-06T00:00:00+00:00",
    }


def _shadow_trade_values() -> dict[str, object]:
    return {
        "id": "schema-contract-shadow",
        "symbol": "TEST",
        "variant": "A",
        "variant_version": "test-v1",
        "is_control": 1,
        "initial_planned_risk": "100",
        "gross_pnl": "0",
        "net_pnl": "0",
        "gross_r": "0",
        "net_r": "0",
        "total_cost": "0",
        "ambiguous_bar_count": 0,
        "status": "OPEN",
    }


def _assert_omitted_server_defaults(connection: Connection) -> None:
    connection.execute(
        text(
            "INSERT INTO runtime_failures "
            "(failure_code, severity, component, message, occurred_at, created_at) "
            "VALUES (:failure_code, :severity, :component, :message, :occurred_at, :created_at)"
        ),
        _runtime_failure_values(),
    )
    failure = connection.execute(
        text("SELECT metadata_json, resolved FROM runtime_failures")
    ).one()
    assert failure == ("{}", 0)

    connection.execute(
        text(
            "INSERT INTO shadow_trades "
            "(id, symbol, variant, variant_version, is_control, initial_planned_risk, "
            "gross_pnl, net_pnl, gross_r, net_r, total_cost, ambiguous_bar_count, status) "
            "VALUES (:id, :symbol, :variant, :variant_version, :is_control, :initial_planned_risk, "
            ":gross_pnl, :net_pnl, :gross_r, :net_r, :total_cost, :ambiguous_bar_count, :status)"
        ),
        _shadow_trade_values(),
    )
    assert connection.scalar(text("SELECT holding_days FROM shadow_trades")) == 0


@contextmanager
def _isolated_schema_engines(tmp_path: Path, monkeypatch: MonkeyPatch) -> Iterator[tuple[Engine, Engine]]:
    create_all_path = tmp_path / "create-all.sqlite3"
    migration_path = tmp_path / "migration-0008.sqlite3"
    assert create_all_path.parent == tmp_path and migration_path.parent == tmp_path

    create_all_engine = create_engine(f"sqlite:///{create_all_path}")
    Base.metadata.create_all(create_all_engine)

    monkeypatch.setenv("RUNTIME_PROFILE", "default")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{migration_path}")
    get_settings.cache_clear()
    config = Config(PROJECT_ROOT / "alembic.ini")
    command.upgrade(config, "20260901_0008")
    migration_engine = create_engine(f"sqlite:///{migration_path}")
    try:
        yield create_all_engine, migration_engine
    finally:
        create_all_engine.dispose()
        migration_engine.dispose()
        get_settings.cache_clear()


def test_create_all_schema_matches_alembic_0008(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    with _isolated_schema_engines(tmp_path, monkeypatch) as (create_all_engine, migration_engine):
        assert _schema_fingerprint(create_all_engine) == _schema_fingerprint(migration_engine)
        with create_all_engine.begin() as connection:
            _assert_omitted_server_defaults(connection)
        with migration_engine.begin() as connection:
            _assert_omitted_server_defaults(connection)


def test_orm_python_defaults_are_preserved(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'orm-defaults.sqlite3'}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            failure_values = _runtime_failure_values()
            failure_values["occurred_at"] = datetime(2026, 9, 6, tzinfo=timezone.utc)
            failure_values["created_at"] = datetime(2026, 9, 6, tzinfo=timezone.utc)
            failure = RuntimeFailureRecord(**failure_values)
            shadow = ShadowTradeRecord(**_shadow_trade_values())
            session.add_all((failure, shadow))
            session.flush()
            assert failure.metadata_json == "{}"
            assert failure.resolved is False
            assert shadow.holding_days == 0
    finally:
        engine.dispose()
