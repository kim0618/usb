"""Explicit P0-B2 production authorization; no backup creation or automatic stamp.

The reviewed manifest pins an approved commit (never inferred from current HEAD),
source business state, historical IDs and a separately created standalone backup.
It is an operator instruction, not a credential/signature. Keep it and the backup
outside the repository in a private directory. Stop all writers before backup,
preflight and immediate apply; keep them stopped through post-apply inspection.
Runtime visibility must cover the host. Containers without host PID visibility
fail closed. Process checks cannot stop a writer from starting later; SQLite's
BEGIN IMMEDIATE protects DB revalidation/rebuild, not filesystem path replacement.
Exclusive directory ownership and no other writer are operational requirements.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
from typing import Iterator

from sqlalchemy.engine import make_url

from app.core.config import PROJECT_ROOT, Settings
from app.dev import reconcile_schema as core

CONFIRMATION = 'OPERATOR_DB_SCHEMA_RECONCILIATION'
PRE_STATE = 'UNVERSIONED_0008_PRE_RECONCILIATION'
CRITICAL = {'human_decisions', 'execution_orders', 'execution_fills'}
FIELDS = {'database_path', 'expected_git_revision', 'expected_row_hash', 'backup_path',
          'expected_backup_sha256', 'expected_counts', 'expected_revision_state',
          'historical_orders', 'historical_fills'}
PROC_ROOT = Path('/proc')
# Linux initial namespace inode IDs (include/uapi/linux/nsfs.h).
# https://github.com/torvalds/linux/blob/master/include/uapi/linux/nsfs.h
INITIAL_PID_NAMESPACE = 0xEFFFFFFC
INITIAL_NET_NAMESPACE = 0xEFFFFFF9


def regular_path(path: Path, code: str) -> Path:
    core.require(path.is_absolute(), code)
    core.require(not any(p.is_symlink() for p in (path, *path.parents)), code)
    try:
        info = path.stat()
        core.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, code)
        resolved = path.resolve(strict=True)
        core.require(path == resolved, code)
        return resolved
    except OSError as error:
        raise core.GuardError(code) from error


def file_hash(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_manifest(path: Path | None) -> dict:
    core.require(path is not None, 'AUTHORIZATION_MANIFEST_REQUIRED')
    path = regular_path(path, 'INVALID_MANIFEST_PATH')
    core.require(not path.is_relative_to(PROJECT_ROOT.resolve()), 'MANIFEST_OUTSIDE_REPOSITORY_REQUIRED')

    def unique_keys(pairs: list) -> dict:
        result = {}
        for key, value in pairs:
            core.require(key not in result, 'INVALID_AUTHORIZATION_MANIFEST')
            result[key] = value
        return result

    try:
        manifest = json.loads(path.read_text(), object_pairs_hook=unique_keys)
        core.require(isinstance(manifest, dict) and set(manifest) == FIELDS,
                     'INVALID_AUTHORIZATION_MANIFEST')
        for key, length in [('expected_git_revision', 40), ('expected_row_hash', 64),
                            ('expected_backup_sha256', 64)]:
            core.require(isinstance(manifest[key], str)
                         and re.fullmatch('[0-9a-f]{' + str(length) + '}', manifest[key]) is not None,
                         'INVALID_AUTHORIZATION_MANIFEST')
        core.require(all(isinstance(manifest[k], str) for k in ['database_path', 'backup_path']),
                     'INVALID_AUTHORIZATION_MANIFEST')
        counts = manifest['expected_counts']
        core.require(isinstance(counts, dict) and CRITICAL <= counts.keys()
                     and all(type(v) is int and v >= 0 for v in counts.values()),
                     'INVALID_AUTHORIZATION_MANIFEST')
        core.require(manifest['expected_revision_state'] == PRE_STATE, 'INVALID_REVISION_STATE')
        orders, fills = manifest['historical_orders'], manifest['historical_fills']
        core.require(isinstance(orders, list) and len(orders) == counts['execution_orders']
                     and len(orders) >= 2 and all(isinstance(r, list) and len(r) == 3
                     and all(isinstance(v, str) for v in r[:2]) and (r[2] is None or isinstance(r[2], str))
                     for r in orders), 'INVALID_HISTORICAL_EVIDENCE')
        core.require({'REJECTED', 'FILLED'} <= {r[1] for r in orders}
                     and len({r[0] for r in orders}) == len(orders), 'INVALID_HISTORICAL_EVIDENCE')
        core.require(isinstance(fills, list) and len(fills) == counts['execution_fills']
                     and len(fills) >= 1 and all(isinstance(r, list) and len(r) == 2
                     and all(isinstance(v, str) for v in r) for r in fills), 'INVALID_HISTORICAL_EVIDENCE')
        core.require(len({r[0] for r in fills}) == len(fills)
                     and all(r[1] in {o[0] for o in orders} for r in fills), 'INVALID_HISTORICAL_EVIDENCE')
        return manifest
    except (OSError, ValueError, TypeError) as error:
        raise core.GuardError('INVALID_AUTHORIZATION_MANIFEST') from error


def procfs_lists_every_process() -> bool:
    """Reject a procfs that can hide PIDs, so an empty scan cannot mean "hidden".

    hidepid=2 removes other users' /proc/<pid> directories from the listing, which
    would silently shrink the scan instead of raising. https://docs.kernel.org/filesystems/proc.html
    """
    for line in (PROC_ROOT / 'mounts').read_text().splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1] == '/proc' and fields[2] == 'proc':
            return not any(option.startswith('hidepid=') and option[len('hidepid='):] not in ('0', 'off')
                           for option in fields[3].split(','))
    return False  # No procfs mount entry: enumeration cannot be trusted.


def runtime_status() -> dict:
    """Read Linux listener/process truth; never output process command lines.

    Only this process's own namespace links are read. PID 1's link adds nothing -
    being in the initial PID namespace already makes /proc enumeration host-wide -
    and it is root-owned, so requiring it would fail closed for every non-root
    operator on an ordinary host.
    """
    try:
        visible = all(str((PROC_ROOT / relative).readlink()) == expected for relative, expected in [
            ('self/ns/pid', f'pid:[{INITIAL_PID_NAMESPACE}]'),
            ('self/ns/net', f'net:[{INITIAL_NET_NAMESPACE}]'),
        ]) and procfs_lists_every_process()
        listening = False
        for name in ['tcp', 'tcp6']:
            for line in (PROC_ROOT / 'net' / name).read_text().splitlines()[1:]:
                fields = line.split()
                if fields[3] == '0A' and int(fields[1].split(':')[1], 16) == 8000:
                    listening = True
        pids = []
        for directory in PROC_ROOT.iterdir():
            if not directory.name.isdigit():
                continue
            try:
                args = (directory / 'cmdline').read_bytes().split(b'\0')
            except FileNotFoundError:
                continue  # Process exited during observation.
            # Conservative: any uvicorn process, or gunicorn serving USB's app.
            words = [a.decode(errors='replace') for a in args]
            if any(Path(w).name in {'uvicorn', 'uvicorn.exe'} for w in words) or (
                    any('gunicorn' in w for w in words) and any('app.main' in w for w in words)):
                pids.append(int(directory.name))
        return {'listener_8000': listening, 'backend_pids': sorted(pids), 'host_visibility': visible}
    except (OSError, ValueError, IndexError) as error:
        raise core.GuardError('RUNTIME_VISIBILITY_UNVERIFIED') from error


def require_backend_stopped(report: dict) -> dict:
    status = runtime_status()
    report['runtime'] = status
    core.require(not status['listener_8000'] and not status['backend_pids'], 'BACKEND_ACTIVE')
    core.require(status['host_visibility'], 'RUNTIME_VISIBILITY_UNVERIFIED')
    return status


def settings_truth() -> tuple[str, Path]:
    try:
        settings = Settings()
        url = make_url(settings.resolved_database_url)
        core.require(url.drivername == 'sqlite' and url.database is not None, 'RUNTIME_PROFILE_MISMATCH')
        return settings.runtime_profile, Path(url.database).resolve()
    except ValueError as error:
        raise core.GuardError('RUNTIME_PROFILE_MISMATCH') from error


def git_truth() -> tuple[str, bool]:
    try:
        def git(*args: str) -> str:
            return subprocess.check_output(['git', *args], cwd=PROJECT_ROOT, text=True,
                                           stderr=subprocess.DEVNULL).strip()
        return git('rev-parse', 'HEAD'), not git('status', '--porcelain', '--untracked-files=all')
    except (OSError, subprocess.CalledProcessError) as error:
        raise core.GuardError('GIT_STATE_UNAVAILABLE') from error


def validate_environment(target: Path, manifest: dict, report: dict) -> None:
    target = regular_path(target, 'WRONG_OPERATOR_PATH')
    core.require(target == core.OPERATOR.resolve()
                 and manifest['database_path'] == str(target), 'WRONG_OPERATOR_PATH')
    profile, configured = settings_truth()
    core.require(profile == 'real_market_operator' and configured == target, 'RUNTIME_PROFILE_MISMATCH')
    # Check runtime before Git so a live backend is an explicit preflight blocker.
    require_backend_stopped(report)
    head, clean = git_truth()
    core.require(head == manifest['expected_git_revision'], 'GIT_REVISION_MISMATCH')
    core.require(clean, 'DIRTY_WORKING_TREE')
    report['git_revision'] = head


def validate_business(connection: sqlite3.Connection, manifest: dict, before: dict) -> None:
    core.require(before['row_hash'] == manifest['expected_row_hash'], 'BUSINESS_HASH_MISMATCH')
    core.require(all(before['counts'].get(t) == n for t, n in manifest['expected_counts'].items()),
                 'CRITICAL_COUNT_MISMATCH')
    orders = [list(r) for r in connection.execute(
        'SELECT id,status,rejection_reason FROM execution_orders ORDER BY id')]
    fills = [list(r) for r in connection.execute('SELECT id,order_id FROM execution_fills ORDER BY id')]
    core.require(orders == sorted(manifest['historical_orders'], key=lambda r: r[0])
                 and fills == sorted(manifest['historical_fills'], key=lambda r: r[0]),
                 'HISTORICAL_EVIDENCE_MISMATCH')


def validate_backup(target: Path, manifest: dict, before: dict, current: dict) -> str:
    backup = regular_path(Path(manifest['backup_path']), 'INVALID_BACKUP_PATH')
    core.require(not backup.samefile(target) and not backup.is_relative_to(PROJECT_ROOT.resolve()),
                 'INVALID_BACKUP_PATH')
    # Bind the checksum to a standalone snapshot, not a main file plus mutable WAL.
    core.require(not any(Path(str(backup) + suffix).exists() for suffix in ['-wal', '-shm', '-journal']),
                 'BACKUP_NOT_STANDALONE')
    checksum = file_hash(backup)
    core.require(checksum == manifest['expected_backup_sha256'], 'BACKUP_CHECKSUM_MISMATCH')
    # immutable is safe only for this separately verified, private, standalone backup.
    with closing(sqlite3.connect(backup.as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
        core.integrity(connection)
        core.require(not core.versioned(connection), 'BACKUP_STATE_MISMATCH')
        core.require(core.fingerprint(connection) == current, 'BACKUP_SCHEMA_MISMATCH')
        core.require(core.row_state(connection) == before, 'BACKUP_BUSINESS_MISMATCH')
    core.require(file_hash(backup) == checksum, 'BACKUP_CHANGED_DURING_VALIDATION')
    return checksum


@contextmanager
def authorized_connection(target: Path, *, write: bool) -> Iterator[sqlite3.Connection]:
    """Private production boundary; caller completes read-only checks first.

    mode=ro/query_only prevent SQL writes. SQLite may still update SHM read marks
    while reading WAL: https://www.sqlite.org/walformat.html#wal_locks
    Thus preflight guarantees no DB/WAL writes, not universal SHM byte equality.
    Do not use immutable=1 here: it would ignore live committed WAL content.
    """
    target = regular_path(target, 'WRONG_OPERATOR_PATH')
    core.require(target == core.OPERATOR.resolve(), 'WRONG_OPERATOR_PATH')
    connection = sqlite3.connect(target.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                                 uri=True, isolation_level=None, timeout=5)
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        if not write:
            connection.execute('PRAGMA query_only=ON')
        yield connection
    finally:
        connection.close()


def production_reconcile(target: Path, *, apply: bool = False, preflight: bool = False,
                         allow_production: bool = False, manifest_path: Path | None = None,
                         confirmation: str | None = None) -> dict:
    report = {'target': str(target), 'mode': 'production-apply' if apply else 'production-preflight'}
    try:
        core.require(allow_production, 'PRODUCTION_AUTHORIZATION_REQUIRED')
        core.require(apply != preflight, 'EXPLICIT_PRODUCTION_MODE_REQUIRED')
        if apply:
            core.require(confirmation == CONFIRMATION, 'CONFIRMATION_REQUIRED')
        manifest = load_manifest(manifest_path)
        validate_environment(target, manifest, report)
        with core.reference_0008() as path, core.connect(path) as reference:
            desired = core.fingerprint(reference)

            def check(connection: sqlite3.Connection) -> dict:
                validate_environment(target, manifest, report)
                before = core.check_preconditions(connection, desired, report)
                validate_business(connection, manifest, before)
                report['backup_sha256'] = validate_backup(target, manifest, before, core.fingerprint(connection))
                return before

            with authorized_connection(target, write=False) as connection:
                connection.execute('BEGIN')
                try:
                    check(connection)
                finally:
                    connection.rollback()
            if apply:
                with authorized_connection(target, write=True) as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    try:
                        # Re-read environment, schema, business and backup after acquiring the lock.
                        before = check(connection)
                        core.apply_reconciliation(connection, reference, before, desired, report)
                        connection.commit()
                    except BaseException:
                        connection.rollback()
                        raise
            report['status'] = ('PRODUCTION_RECONCILIATION_APPLIED' if apply
                                else 'PRODUCTION_RECONCILIATION_PREFLIGHT_PASS')
        return report
    except core.GuardError as error:
        raise core.GuardError(str(error), report) from error
    except (OSError, sqlite3.Error) as error:
        raise core.GuardError('PRODUCTION_PREFLIGHT_IO_FAILED', report) from error
