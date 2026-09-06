"""Clone-only P0-B1 rehearsal; source connections are always mode=ro/query_only.

Run: python -m app.dev.rehearse_schema
Retains the clone and a payload-free JSON report in a fresh private /tmp directory.
No production authorization switch is provided.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile

from app.dev.reconcile_schema import (
    OPERATOR, REVISION, GuardError, alembic, connect, digest,
    fingerprint, integrity, reconcile, reference_0008, require, row_state,
    versioned, writable_target,
)


def snapshot(connection: sqlite3.Connection) -> dict:
    return {'business': row_state(connection), 'fingerprint': digest(fingerprint(connection)),
            'versioned': versioned(connection), 'integrity': integrity(connection),
            'orders': connection.execute(
                'SELECT id, status, rejection_reason FROM execution_orders ORDER BY id').fetchall(),
            'fills': connection.execute('SELECT id, order_id FROM execution_fills ORDER BY id').fetchall()}


def backup_clone(source: Path, destination: Path) -> dict:
    """Pin one source read snapshot for both Backup API and source/clone checks."""
    require(not destination.exists(), 'CLONE_ALREADY_EXISTS')
    require(destination.parent.resolve().is_relative_to(Path('/tmp').resolve()), 'TEMP_CLONE_REQUIRED')
    # Exclusive creation, never truncate an existing target or follow its symlink.
    with destination.open('xb'):
        pass
    writable_target(destination)
    with connect(source) as source_connection, connect(destination, write=True) as clone:
        source_connection.execute('BEGIN')
        try:
            before = snapshot(source_connection)
            source_connection.backup(clone)
            require(snapshot(clone) == before, 'SOURCE_CLONE_MISMATCH')
        finally:
            source_connection.rollback()
    return before


def omitted_defaults(path: Path) -> dict:
    """Exercise raw SQL defaults in a transaction that always rolls back."""
    with connect(path, write=True) as connection:
        before = row_state(connection)
        connection.execute('BEGIN IMMEDIATE')
        try:
            connection.execute(
                "INSERT INTO runtime_failures "
                "(failure_code,severity,component,message,occurred_at,created_at) "
                "VALUES ('TEST','WARNING','rehearsal','test','2026-09-06','2026-09-06')")
            failure = connection.execute('SELECT metadata_json,resolved FROM runtime_failures').fetchone()
            connection.execute(
                "INSERT INTO shadow_trades "
                "(id,symbol,variant,variant_version,is_control,initial_planned_risk,"
                "gross_pnl,net_pnl,gross_r,net_r,total_cost,ambiguous_bar_count,status) "
                "VALUES ('p0b1-default-test','TEST','A','test',1,'0','0','0','0','0','0',0,'OPEN')")
            holding = connection.execute('SELECT holding_days FROM shadow_trades').fetchone()[0]
            require(failure == ('{}', 0) and holding == 0, 'DEFAULT_SEMANTICS_FAILED')
            integrity(connection)
        finally:
            connection.rollback()
        require(row_state(connection) == before, 'DEFAULT_TEST_RESIDUE')
    return {'metadata_json': '{}', 'resolved': 0, 'holding_days': 0, 'test_rows_retained': 0}


def stamp_clone(path: Path) -> dict:
    writable_target(path)
    with reference_0008() as reference, connect(reference) as ref, connect(path) as connection:
        require(not versioned(connection), 'ALREADY_VERSIONED_OR_UNEXPECTED_DB')
        require(fingerprint(connection) == fingerprint(ref), 'STAMP_SCHEMA_MISMATCH')
        before = snapshot(connection)
    alembic(path, 'stamp')
    alembic(path, 'current')
    with connect(path) as connection:
        require(connection.execute('SELECT version_num FROM alembic_version').fetchall()
                == [(REVISION,)], 'STAMP_REVISION_MISMATCH')
        after = snapshot(connection)
        require({**after, 'versioned': False} == before, 'STAMP_CHANGED_APPLICATION')
    return {'revision': REVISION, 'current_verified': True,
            'application_schema_changed': False, 'business_hash_changed': False}


def rehearse(source: Path = OPERATOR) -> dict:
    directory = Path(tempfile.mkdtemp(prefix='usb-p0b1-', dir='/tmp'))
    clone = directory / 'operator-clone.sqlite3'
    report = {'source': str(source.resolve()), 'read_mode': 'mode=ro; query_only=ON',
              'clone': str(clone), 'backup_api': 'sqlite3.Connection.backup'}
    with connect(source) as connection:
        connection.execute('BEGIN')
        initial = snapshot(connection)
        connection.rollback()
    try:
        before = backup_clone(source, clone)
        require(before == initial, 'SOURCE_CHANGED_BEFORE_BACKUP')
        report['source_before'] = before
        report['source_clone_counts_match'] = report['source_clone_hash_match'] = True
        report['dry_run'] = reconcile(clone)
        report['reconciliation'] = reconcile(clone, apply=True)
        report['defaults'] = omitted_defaults(clone)
        report['stamp'] = stamp_clone(clone)
        with connect(clone) as connection:
            report['clone_after'] = snapshot(connection)
        require(report['clone_after']['business'] == before['business'], 'BUSINESS_STATE_CHANGED')
        report['status'] = 'TEMP RECONCILIATION REHEARSAL PASS'
    except GuardError as error:
        report['status'] = str(error)
        raise
    finally:
        with connect(source) as connection:
            connection.execute('BEGIN')
            final = snapshot(connection)
            connection.rollback()
        report['source_after'] = final
        report['source_unchanged'] = final == initial
        if final != initial:
            report['status'] = 'OPERATOR DB SAFETY VIOLATION'
        (directory / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        require(final == initial, 'OPERATOR DB SAFETY VIOLATION')
    return report


if __name__ == '__main__':
    try:
        print(json.dumps(rehearse(), sort_keys=True))
    except (GuardError, OSError, sqlite3.Error) as error:
        print(json.dumps({'status': str(error) if isinstance(error, GuardError) else 'REHEARSAL_IO_FAILED'}))
        raise SystemExit(2)
