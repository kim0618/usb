"""Isolated P0-B1 maintenance/rehearsal safety contracts (no operator writes)."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import create_engine

from app.dev import reconcile_schema as tool
from app.dev import rehearse_schema as rehearsal
from app.dev.schema_fingerprint import _normalize_default, schema_fingerprint
from tests.test_schema_contract import _runtime_failure_values, _shadow_trade_values


@pytest.fixture(scope='module')
def reference():
    with tool.reference_0008() as path:
        yield path


@pytest.fixture
def legacy(tmp_path, reference, monkeypatch):
    path = tmp_path / 'legacy.sqlite3'
    with tool.connect(reference) as ref, sqlite3.connect(path) as connection:
        for name, ddl in ref.execute("SELECT name,sql FROM sqlite_schema WHERE type='table' "
                                     "AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"):
            if name == 'runtime_failures':
                ddl = ddl.replace("DEFAULT '{}'", '').replace('DEFAULT 0', '')
            if name == 'shadow_trades':
                ddl = ddl.replace("DEFAULT '0'", '')
            connection.execute(ddl)
        for (ddl,) in ref.execute("SELECT sql FROM sqlite_schema WHERE type='index' AND sql IS NOT NULL"):
            connection.execute(ddl)
        connection.execute("INSERT INTO execution_orders "
                           "(id,broker_type,symbol,side,requested_quantity,filled_quantity,status,"
                           "reference_price,submitted_at,execution_version) "
                           "VALUES ('historical-order','SIM','TEST','BUY','1','1','FILLED','10','2026-09-06','v1')")
        connection.execute("INSERT INTO execution_fills "
                           "(id,order_id,quantity,raw_market_price,fill_price,spread_cost,slippage_cost,"
                           "commission,fx_cost,total_cost,filled_at) "
                           "VALUES ('historical-fill','historical-order','1','10','10','0','0','0','0','0','2026-09-06')")

    @contextmanager
    def fixed_reference():
        yield reference

    monkeypatch.setattr(tool, 'reference_0008', fixed_reference)
    monkeypatch.setattr(rehearsal, 'reference_0008', fixed_reference)
    return path


def state(path):
    with tool.connect(path) as connection:
        return (rehearsal.snapshot(connection), connection.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name').fetchall())


def insert(connection, table, values):
    names = ','.join(map(tool.quote, values))
    placeholders = ','.join('?' for _ in values)
    connection.execute(f'INSERT INTO {table} ({names}) VALUES ({placeholders})', tuple(values.values()))


def test_dry_run_is_default_and_read_only(legacy, monkeypatch, capsys):
    before = state(legacy)
    raw = legacy.read_bytes()
    monkeypatch.setattr('sys.argv', ['reconcile_schema', str(legacy)])
    assert tool.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report['mode'] == 'dry-run' and report['status'] == 'DRY_RUN_PASS'
    assert len(report['known_missing_defaults']) == 3
    assert state(legacy) == before and legacy.read_bytes() == raw
    with tool.connect(legacy) as connection:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            connection.execute('CREATE TABLE forbidden (id INTEGER)')


def test_apply_exact_schema_data_and_defaults(legacy, reference):
    before = state(legacy)[0]
    report = tool.reconcile(legacy, apply=True)
    assert report['status'] == 'APPLIED'
    assert report['fingerprint_after'] == report['fingerprint_reference']
    with tool.connect(legacy) as connection, tool.connect(reference) as ref:
        assert tool.fingerprint(connection) == tool.fingerprint(ref)
        assert tool.row_state(connection) == before['business']
        assert not tool.versioned(connection)
        assert connection.execute('PRAGMA foreign_keys').fetchone() == (1,)
        assert len(connection.execute('PRAGMA foreign_key_list(shadow_trades)').fetchall()) == 3
        assert all(row[6] == 'SET NULL' for row in connection.execute('PRAGMA foreign_key_list(shadow_trades)'))
    assert rehearsal.omitted_defaults(legacy) == {
        'metadata_json': '{}', 'resolved': 0, 'holding_days': 0, 'test_rows_retained': 0}
    assert state(legacy)[0]['business'] == before['business']
    with pytest.raises(tool.GuardError, match='PRECONDITION_SCHEMA_MISMATCH'):
        tool.reconcile(legacy, apply=True)


@pytest.mark.parametrize('table', tool.AFFECTED)
def test_nonempty_is_rejected(legacy, table):
    with sqlite3.connect(legacy) as connection:
        values = (_runtime_failure_values() | {'metadata_json': '{}', 'resolved': 0}
                  if table == 'runtime_failures' else _shadow_trade_values() | {'holding_days': 0})
        insert(connection, table, values)
    before = state(legacy)
    with pytest.raises(tool.GuardError, match='AFFECTED_TABLE_NOT_EMPTY'):
        tool.reconcile(legacy, apply=True)
    assert state(legacy) == before


@pytest.mark.parametrize('ddl,code', [
    ('CREATE TABLE alembic_version (version_num VARCHAR(32))', 'ALREADY_VERSIONED_OR_UNEXPECTED_DB'),
    ('ALTER TABLE scanner_runs ADD COLUMN unexpected TEXT', 'PRECONDITION_SCHEMA_MISMATCH'),
    ('CREATE TABLE shadow_trades__reconcile_new (id INTEGER)', 'REPLACEMENT_TABLE_EXISTS'),
    ('DROP INDEX ix_shadow_trades_symbol', 'PRECONDITION_SCHEMA_MISMATCH'),
    ('CREATE INDEX extra ON shadow_trades (symbol COLLATE NOCASE DESC) WHERE holding_days > 0', 'PRECONDITION_SCHEMA_MISMATCH'),
    ('CREATE TRIGGER extra AFTER DELETE ON shadow_trades BEGIN DELETE FROM execution_orders; END', 'PRECONDITION_SCHEMA_MISMATCH'),
])
def test_unexpected_schema_fails_closed(legacy, ddl, code):
    with sqlite3.connect(legacy) as connection:
        connection.execute(ddl)
    before = state(legacy)
    with pytest.raises(tool.GuardError, match=code):
        tool.reconcile(legacy, apply=True)
    assert state(legacy) == before


@pytest.mark.parametrize('fail_after', [1, 2])
def test_transaction_rolls_back_both_tables(legacy, monkeypatch, fail_after):
    original = tool.rebuild
    calls = []
    before = state(legacy)
    raw = legacy.read_bytes()

    def broken(connection, reference, table):
        assert connection.in_transaction
        assert connection.execute('PRAGMA foreign_keys').fetchone() == (1,)
        original(connection, reference, table)
        calls.append(table)
        if len(calls) == fail_after:
            raise RuntimeError('deliberate failure after rebuild')

    monkeypatch.setattr(tool, 'rebuild', broken)
    with pytest.raises(RuntimeError, match='deliberate failure'):
        tool.reconcile(legacy, apply=True)
    assert len(calls) == fail_after
    assert state(legacy) == before and legacy.read_bytes() == raw
    assert not any('__reconcile_new' in row[1] for row in state(legacy)[1])


def test_postcondition_failure_rolls_back(legacy, monkeypatch):
    original = tool.rebuild
    before = state(legacy)

    def broken(connection, reference, table):
        original(connection, reference, table)
        if table == 'shadow_trades':
            connection.execute('DROP INDEX ix_shadow_trades_symbol')

    monkeypatch.setattr(tool, 'rebuild', broken)
    with pytest.raises(tool.GuardError, match='RECONCILIATION_FINGERPRINT_MISMATCH'):
        tool.reconcile(legacy, apply=True)
    assert state(legacy) == before


@pytest.mark.parametrize('alias', ['direct', 'symlink', 'hardlink'])
def test_wrong_target_before_any_connection(legacy, tmp_path, monkeypatch, alias):
    monkeypatch.setattr(tool, 'OPERATOR', legacy)
    target = legacy
    if alias != 'direct':
        target = tmp_path / alias
        if alias == 'symlink':
            target.symlink_to(legacy)
        else:
            target.hardlink_to(legacy)
    raw = legacy.read_bytes()
    monkeypatch.setattr(tool, 'connect', lambda *a, **k: pytest.fail('must reject before opening'))
    with pytest.raises(tool.GuardError, match='PRODUCTION_TARGET_FORBIDDEN'):
        tool.reconcile(target, apply=True)
    assert legacy.read_bytes() == raw


def test_actual_operator_guard_without_opening(monkeypatch):
    monkeypatch.setattr(tool, 'connect', lambda *a, **k: pytest.fail('operator must not open'))
    with pytest.raises(tool.GuardError, match='PRODUCTION_TARGET_FORBIDDEN'):
        tool.reconcile(tool.OPERATOR, apply=True)


def test_backup_committed_wal_and_source_unchanged(legacy, tmp_path):
    destination = tmp_path / 'wal-clone.sqlite3'
    with sqlite3.connect(legacy) as writer:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
        writer.execute("UPDATE execution_orders SET symbol='COMMITTED_WAL'")
        writer.commit()
        before = state(legacy)
        writer.execute("UPDATE execution_orders SET symbol='UNCOMMITTED'")
        copied = rehearsal.backup_clone(legacy, destination)
        assert copied == before[0] and state(destination) == before
        writer.rollback()
        assert state(legacy) == before
    with pytest.raises(tool.GuardError, match='CLONE_ALREADY_EXISTS'):
        rehearsal.backup_clone(legacy, destination)


def test_stamp_requires_reconciliation(legacy):
    before = state(legacy)
    with pytest.raises(tool.GuardError, match='STAMP_SCHEMA_MISMATCH'):
        rehearsal.stamp_clone(legacy)
    assert state(legacy) == before


def test_complete_rehearsal_and_stamp(legacy):
    before = state(legacy)
    report = rehearsal.rehearse(legacy)
    assert report['status'] == 'TEMP RECONCILIATION REHEARSAL PASS'
    assert report['source_unchanged']
    assert report['stamp']['revision'] == tool.REVISION
    assert report['stamp']['current_verified']
    assert report['clone_after']['business'] == before[0]['business']
    assert state(legacy) == before
    with pytest.raises(tool.GuardError, match='ALREADY_VERSIONED_OR_UNEXPECTED_DB'):
        tool.reconcile(Path(report['clone']), apply=True)


@pytest.mark.parametrize('table,old,new', [
    ('runtime_failures', 'failure_code VARCHAR(64)', 'failure_code VARCHAR(64) COLLATE NOCASE'),
    ('runtime_failures', 'PRIMARY KEY (id)', 'PRIMARY KEY (id) ON CONFLICT REPLACE'),
    ('shadow_trades', 'ON DELETE SET NULL', 'ON DELETE CASCADE'),
    ('shadow_trades', 'UNIQUE (scanner_candidate_id, variant, variant_version)',
     'UNIQUE (scanner_candidate_id, variant_version, variant)'),
    ('shadow_trades', 'ON DELETE SET NULL', 'ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED'),
])
def test_unexpected_ddl_details_fail_closed(legacy, table, old, new):
    with sqlite3.connect(legacy) as connection:
        ddl = connection.execute('SELECT sql FROM sqlite_schema WHERE name=?', (table,)).fetchone()[0]
        assert old in ddl
        indexes = connection.execute("SELECT sql FROM sqlite_schema WHERE type='index' "
                                     "AND tbl_name=? AND sql IS NOT NULL", (table,)).fetchall()
        connection.execute(f'DROP TABLE {table}')
        connection.execute(ddl.replace(old, new))
        for (sql,) in indexes:
            connection.execute(sql)
    before = state(legacy)
    with pytest.raises(tool.GuardError, match='PRECONDITION_SCHEMA_MISMATCH'):
        tool.reconcile(legacy, apply=True)
    assert state(legacy) == before


def test_row_hash_is_independent_of_column_and_row_order(tmp_path):
    first, second = tmp_path / 'one.sqlite3', tmp_path / 'two.sqlite3'
    with sqlite3.connect(first) as c:
        c.execute('CREATE TABLE sample (id INTEGER, value BLOB)')
        c.executemany('INSERT INTO sample VALUES (?,?)', [(2, b'blob'), (1, 'text'), (1, 'text')])
    with sqlite3.connect(second) as c:
        c.execute('CREATE TABLE sample (value BLOB, id INTEGER)')
        c.executemany('INSERT INTO sample (id,value) VALUES (?,?)', [(1, 'text'), (1, 'text'), (2, b'blob')])
    with tool.connect(first) as a, tool.connect(second) as b:
        assert tool.row_state(a) == tool.row_state(b)
    with sqlite3.connect(second) as c:
        c.execute('DELETE FROM sample WHERE rowid=1')
    with tool.connect(first) as a, tool.connect(second) as b:
        assert tool.row_state(a)['row_hash'] != tool.row_state(b)['row_hash']


@pytest.mark.parametrize('alias', ['file_symlink', 'directory_symlink', 'hardlink'])
def test_clone_aliases_are_rejected_before_opening(legacy, tmp_path, monkeypatch, alias):
    if alias == 'directory_symlink':
        directory = tmp_path / 'linked-directory'
        directory.symlink_to(legacy.parent, target_is_directory=True)
        target = directory / legacy.name
    else:
        target = tmp_path / alias
        if alias == 'file_symlink':
            target.symlink_to(legacy)
        else:
            target.hardlink_to(legacy)
    raw = legacy.read_bytes()
    monkeypatch.setattr(tool, 'connect', lambda *a, **k: pytest.fail('must reject before opening'))
    code = 'TEMP_CLONE_REQUIRED' if alias == 'hardlink' else 'SYMLINK_TARGET_FORBIDDEN'
    with pytest.raises(tool.GuardError, match=code):
        tool.reconcile(target, apply=True)
    assert legacy.read_bytes() == raw


def test_upgrade_cannot_adopt_an_existing_clone(legacy, monkeypatch):
    before = state(legacy)
    monkeypatch.setattr(tool.subprocess, 'run', lambda *a, **k: pytest.fail('must not run Alembic'))
    with pytest.raises(tool.GuardError, match='UPGRADE_REQUIRES_EMPTY_REFERENCE'):
        tool.alembic(legacy, 'upgrade')
    assert state(legacy) == before


@pytest.mark.parametrize('left,right', [
    ("CHECK (a != 'OPEN')", "CHECK (a != 'open')"),
    ("CHECK (a != 'two  spaces')", "CHECK (a != 'two spaces')"),
    ('PRIMARY KEY (a,b)', 'PRIMARY KEY (b,a)'),
    ('UNIQUE (a,b)', 'UNIQUE (b,a)'),
])
def test_shared_fingerprint_preserves_semantic_differences(tmp_path, left, right):
    engines = [create_engine(f'sqlite:///{tmp_path / name}') for name in ['left.sqlite3', 'right.sqlite3']]
    try:
        for engine, constraint in zip(engines, [left, right]):
            with engine.begin() as connection:
                connection.exec_driver_sql(f'CREATE TABLE example (a TEXT, b TEXT, {constraint})')
        assert schema_fingerprint(engines[0]) != schema_fingerprint(engines[1])
    finally:
        for engine in engines:
            engine.dispose()


def test_shared_fingerprint_ignores_physical_column_order(tmp_path):
    engines = [create_engine(f'sqlite:///{tmp_path / name}') for name in ['left.sqlite3', 'right.sqlite3']]
    try:
        for engine, columns in zip(engines, ['a TEXT, b INTEGER', 'b INTEGER, a TEXT']):
            with engine.begin() as connection:
                connection.exec_driver_sql(f'CREATE TABLE example ({columns}, PRIMARY KEY (a,b))')
        assert schema_fingerprint(engines[0]) == schema_fingerprint(engines[1])
    finally:
        for engine in engines:
            engine.dispose()


def test_default_parentheses_preserve_expression_and_literals():
    expression = "(1) + (2)"
    assert _normalize_default(expression) == expression
    assert _normalize_default(f'({expression})') == expression
    assert _normalize_default("(')(')") == "')('"
    assert _normalize_default('((0))') == '0'


def test_row_hash_includes_application_names_resembling_sqlite_prefix(tmp_path):
    path = tmp_path / 'names.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE sqliteX_application (id INTEGER)')
        connection.execute('INSERT INTO sqliteX_application VALUES (1)')
    with tool.connect(path) as connection:
        assert tool.row_state(connection)['counts'] == {'sqliteX_application': 1}
