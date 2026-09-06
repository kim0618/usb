"""P0-B1: reconcile only the known, empty-table, unversioned 0008 predecessor.

Usage: python -m app.dev.reconcile_schema /tmp/.../operator-clone.sqlite3 [--apply]
The reference is always built from immutable migrations, never supplied by callers.
Production apply requires the separate authorization/preflight contract. Run with exclusive
ownership of the clone directory; concurrent path replacement is not supported.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.core.config import PROJECT_ROOT, REAL_MARKET_DATABASE_URL
from app.dev.schema_fingerprint import _normalize_default, schema_fingerprint

REVISION = "20260901_0008"
OPERATOR = PROJECT_ROOT / REAL_MARKET_DATABASE_URL.removeprefix("sqlite:///")
AFFECTED = ("runtime_failures", "shadow_trades")
MISSING_DEFAULTS = (("runtime_failures", "metadata_json"),
                    ("runtime_failures", "resolved"), ("shadow_trades", "holding_days"))


class GuardError(RuntimeError):
    """Stable error code plus a payload-free diagnostic report."""

    def __init__(self, code: str, report: dict | None = None):
        super().__init__(code)
        self.report = {**(report or {}), "status": code}


def require(condition: bool, code: str) -> None:
    if not condition:
        raise GuardError(code)


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def writable_target(path: Path) -> Path:
    target = path.resolve(strict=True)
    require(target != OPERATOR.resolve() and not (OPERATOR.exists() and target.samefile(OPERATOR)),
            "PRODUCTION_TARGET_FORBIDDEN")
    absolute = path.absolute()
    require(not any(part.is_symlink() for part in (absolute, *absolute.parents)),
            "SYMLINK_TARGET_FORBIDDEN")
    require(target.is_relative_to(Path('/tmp').resolve())
            and not target.is_relative_to(PROJECT_ROOT.resolve())
            and target.stat().st_nlink == 1, "TEMP_CLONE_REQUIRED")
    return target


@contextmanager
def connect(path: Path, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    path = writable_target(path) if write else path.resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                                 uri=True, isolation_level=None)
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        if not write:
            connection.execute('PRAGMA query_only=ON')
        yield connection
    finally:
        connection.close()


def tables(connection: sqlite3.Connection) -> list[str]:
    return [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' "
        "AND name NOT GLOB 'sqlite_*' AND name != 'alembic_version' ORDER BY name")]


def row_state(connection: sqlite3.Connection) -> dict:
    """Hash all columns/rows, preserving SQLite types and duplicate multiplicity.

    Rows are sorted by their canonical JSON encoding (not physical/rowid order).
    BLOBs are encoded as tagged hex. No row payload is returned or logged.
    """
    counts, hashes = {}, {}
    for table in tables(connection):
        columns = sorted(r[1] for r in connection.execute(f'PRAGMA table_info({quote(table)})'))
        cursor = connection.execute(f'SELECT {",".join(map(quote, columns))} FROM {quote(table)}')
        rows = sorted(json.dumps(
            [({'blob_hex': value.hex()} if isinstance(value, bytes) else value) for value in row],
            ensure_ascii=False, separators=(',', ':')) for row in cursor)
        counts[table] = len(rows)
        hashes[table] = digest([columns, rows])
    return {"counts": counts, "table_hashes": hashes, "row_hash": digest(hashes)}


def table_clauses(sql: str) -> list[tuple[str, ...]]:
    """Compare full DDL clauses without column/constraint ordering or ID quoting.

    String literals remain case-sensitive. This supplements reflection so changes
    to collations, conflict policies, FK deferral and generated expressions cannot
    silently pass. Only reference-generated, conventional SQLite DDL is accepted.
    """
    tokens = re.findall(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`[^`]*`|\[[^\]]*\]|[a-zA-Z_][a-zA-Z_0-9]*|[^\s]", sql)
    tokens = [token if token.startswith("'") else
              token[1:-1].replace('""', '"').casefold() if token.startswith(('"', '`', '['))
              else token.casefold() for token in tokens]
    body = tokens[tokens.index('(') + 1:]
    clauses, clause, depth = [], [], 0
    for token in body:
        if token == ')' and depth == 0:
            clauses.append(tuple(clause))
            break
        if token == ',' and depth == 0:
            clauses.append(tuple(clause))
            clause = []
            continue
        depth += (token == '(') - (token == ')')
        clause.append(token)
    return sorted(clauses)


def fingerprint(connection: sqlite3.Connection) -> dict:
    # SQLAlchemy must neither close nor rollback the caller's SQLite transaction.
    class Borrowed:
        def __getattr__(self, name: str):
            return getattr(connection, name)

        def close(self) -> None:
            pass

        def rollback(self) -> None:
            pass

    engine = create_engine('sqlite://', creator=Borrowed, poolclass=StaticPool,
                           pool_reset_on_return=None)
    try:
        with engine.connect() as wrapper:
            result = schema_fingerprint(wrapper)
        # Preserve details the general ORM contract does not reflect: PK
        # order, hidden columns, index collation/direction, partial/expression SQL.
        result['xinfo'] = {t: sorted((r[1], r[2].casefold(), r[3], _normalize_default(r[4]), r[5], r[6])
                                    for r in connection.execute(f'PRAGMA table_xinfo({quote(t)})'))
                           for t in tables(connection)}
        result['table_flags'] = sorted((r[1], r[4], r[5]) for r in connection.execute('PRAGMA table_list')
                                       if r[1] in tables(connection))
        result['index_details'] = {
            t: sorted((i[1], i[2], i[3], i[4],
                       [(r[0], r[2], r[3], r[4], r[5]) for r in
                        connection.execute(f'PRAGMA index_xinfo({quote(i[1])})')],
                       connection.execute('SELECT sql FROM sqlite_schema WHERE name=?',
                                          (i[1],)).fetchone()[0])
                      for i in connection.execute(f'PRAGMA index_list({quote(t)})'))
            for t in tables(connection)}
        result['table_clauses'] = {t: table_clauses(connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (t,)).fetchone()[0])
            for t in tables(connection)}
        result['extra_objects'] = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE type IN ('trigger','view') ORDER BY type,name").fetchall()
        return result
    finally:
        engine.dispose()


def integrity(connection: sqlite3.Connection) -> dict:
    result = {'quick_check': [r[0] for r in connection.execute('PRAGMA quick_check')],
              'fk_violations': len(connection.execute('PRAGMA foreign_key_check').fetchall())}
    require(result == {'quick_check': ['ok'], 'fk_violations': 0}, 'INTEGRITY_FAILED')
    return result


def versioned(connection: sqlite3.Connection) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_schema WHERE name='alembic_version'").fetchone() is not None


def alembic(path: Path, action: str) -> None:
    """Isolate settings/cache from the caller and verify the effective DB URL."""
    path = writable_target(path)
    require(action in ('upgrade', 'stamp', 'current'), 'INVALID_ALEMBIC_ACTION')
    if action == 'upgrade':
        # Upgrade only constructs an empty reference, never adopts a target DB.
        with connect(path) as connection:
            require(connection.execute('SELECT count(*) FROM sqlite_schema').fetchone()[0] == 0,
                    'UPGRADE_REQUIRES_EMPTY_REFERENCE')
    code = '''
import sys
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from app.core.config import PROJECT_ROOT, get_settings
path, action, revision = sys.argv[1:]
if Path(make_url(get_settings().resolved_database_url).database).resolve() != Path(path):
    raise SystemExit('ALEMBIC_TARGET_MISMATCH')
config = Config(PROJECT_ROOT / 'alembic.ini')
if action == 'current':
    command.current(config, verbose=True)
else:
    getattr(command, action)(config, revision)
'''
    env = {**os.environ, 'RUNTIME_PROFILE': 'default', 'DATABASE_URL': f'sqlite:///{path}',
           'PYTHONPATH': str(PROJECT_ROOT / 'backend')}
    completed = subprocess.run([sys.executable, '-c', code, str(path), action, REVISION],
                               cwd=PROJECT_ROOT, env=env, capture_output=True, text=True)
    require(completed.returncode == 0, 'ALEMBIC_FAILED')
    if action == 'current':
        require(REVISION in completed.stdout, 'ALEMBIC_CURRENT_MISMATCH')


@contextmanager
def reference_0008() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix='usb-p0b1-reference-', dir='/tmp') as directory:
        path = Path(directory) / 'reference-0008.sqlite3'
        path.touch(exist_ok=False)
        alembic(path, 'upgrade')
        with connect(path) as connection:
            require(connection.execute('SELECT version_num FROM alembic_version').fetchall()
                    == [(REVISION,)], 'INVALID_REFERENCE')
            integrity(connection)
        yield path


def expected_pre(reference: dict) -> dict:
    expected = deepcopy(reference)
    for table, column in MISSING_DEFAULTS:
        clauses = expected['table_clauses'][table]
        for index, clause in enumerate(clauses):
            if clause[0] == column:
                offset = clause.index('default')
                clauses[index] = clause[:offset] + clause[offset + 2:]
        expected['table_clauses'][table] = sorted(clauses)
        values = list(expected['columns'][table][column])
        values[2] = None
        expected['columns'][table][column] = tuple(values)
        expected['xinfo'][table] = [tuple(None if n == 3 and row[0] == column else value
                                                for n, value in enumerate(row))
                                     for row in expected['xinfo'][table]]
    return expected


def rebuild(connection: sqlite3.Connection, reference: sqlite3.Connection, table: str) -> None:
    replacement = table + '__reconcile_new'
    ddl = reference.execute('SELECT sql FROM sqlite_schema WHERE type="table" AND name=?',
                            (table,)).fetchone()[0]
    # Use the reference body verbatim; only the CREATE TABLE name is replaced.
    body = ddl[ddl.index('('):]
    connection.execute(f'CREATE TABLE {quote(replacement)} {body}')
    columns = ','.join(quote(r[1]) for r in reference.execute(f'PRAGMA table_info({quote(table)})'))
    connection.execute(f'INSERT INTO {quote(replacement)} ({columns}) SELECT {columns} FROM {quote(table)}')
    connection.execute(f'DROP TABLE {quote(table)}')
    connection.execute(f'ALTER TABLE {quote(replacement)} RENAME TO {quote(table)}')
    for (sql,) in reference.execute(
            "SELECT sql FROM sqlite_schema WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name",
            (table,)):
        connection.execute(sql)


def check_preconditions(connection: sqlite3.Connection, desired: dict, report: dict) -> dict:
    report['integrity_before'] = integrity(connection)
    report['alembic_version_exists'] = versioned(connection)
    require(not report['alembic_version_exists'], 'ALREADY_VERSIONED_OR_UNEXPECTED_DB')
    current = fingerprint(connection)
    report['fingerprint_before'] = digest(current)
    report['fingerprint_reference'] = digest(desired)
    require(not any(t + '__reconcile_new' in current['tables'] for t in AFFECTED),
            'REPLACEMENT_TABLE_EXISTS')
    require(current == expected_pre(desired), 'PRECONDITION_SCHEMA_MISMATCH')
    report['known_missing_defaults'] = MISSING_DEFAULTS
    before = row_state(connection)
    report['business_before'] = before
    require(all(before['counts'][t] == 0 for t in AFFECTED), 'AFFECTED_TABLE_NOT_EMPTY')
    # DROP with FK ON cannot cause incoming CASCADE/SET NULL writes.
    require(not any(fk[1] in AFFECTED for fks in current['foreign_keys'].values()
                    for fk in fks), 'INCOMING_FK_UNSUPPORTED')
    return before


def apply_reconciliation(connection: sqlite3.Connection, reference: sqlite3.Connection,
                         before: dict, desired: dict, report: dict) -> None:
    """Caller owns BEGIN IMMEDIATE and commits only after every check succeeds."""
    for table in AFFECTED:
        rebuild(connection, reference, table)
    after = fingerprint(connection)
    require(after == desired, 'RECONCILIATION_FINGERPRINT_MISMATCH')
    require(not versioned(connection), 'UNEXPECTED_VERSION_CREATED')
    require(row_state(connection) == before, 'BUSINESS_STATE_CHANGED')
    report['fingerprint_after'] = digest(after)
    report['application_schema_differences_after'] = 0
    report['integrity_after'] = integrity(connection)


def reconcile(target: Path, *, apply: bool = False) -> dict:
    report = {'target': str(target.resolve()), 'mode': 'apply' if apply else 'dry-run',
              'file_exists': target.is_file(), 'affected_tables': AFFECTED}
    try:
        if apply:
            writable_target(target)  # Reject production before even building a reference.
        require(target.is_file(), 'TARGET_NOT_FOUND')
        report['file_size'] = target.stat().st_size
        with reference_0008() as reference_path, connect(reference_path) as reference:
            desired = fingerprint(reference)
            with connect(target, write=apply) as connection:
                connection.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
                try:
                    before = check_preconditions(connection, desired, report)
                    if apply:
                        apply_reconciliation(connection, reference, before, desired, report)
                        connection.commit()
                    else:
                        connection.rollback()
                    report['status'] = 'APPLIED' if apply else 'DRY_RUN_PASS'
                except BaseException:
                    connection.rollback()
                    raise
        return report
    except GuardError as error:
        raise GuardError(str(error), report) from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--preflight-production', action='store_true')
    parser.add_argument('--allow-production-target', action='store_true')
    parser.add_argument('--maintenance-window-confirmed', action='store_true',
                        help='Confirm backend and all other operator DB writers are stopped '
                             'for the controlled maintenance window')
    parser.add_argument('--authorization-manifest', type=Path)
    parser.add_argument('--confirm')
    args = parser.parse_args()
    try:
        production_requested = (args.preflight_production or args.allow_production_target
                                or args.maintenance_window_confirmed
                                or args.authorization_manifest is not None or args.confirm is not None)
        if production_requested:
            from app.dev.production_reconciliation import production_reconcile
            report = production_reconcile(
                args.target, apply=args.apply, preflight=args.preflight_production,
                allow_production=args.allow_production_target,
                maintenance_window_confirmed=args.maintenance_window_confirmed,
                manifest_path=args.authorization_manifest, confirmation=args.confirm)
        else:
            report = reconcile(args.target, apply=args.apply)
    except GuardError as error:
        print(json.dumps(error.report, sort_keys=True))
        return 2
    except (OSError, sqlite3.Error):
        print(json.dumps({'target': str(args.target.resolve()), 'status': 'TARGET_IO_FAILED'}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    # Use the canonical module so the production module shares this GuardError
    # class even when Python starts us as __main__ via -m.
    from app.dev.reconcile_schema import main as entrypoint
    raise SystemExit(entrypoint())
