"""Production authorization tests use only an injected /tmp official target."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import os
import subprocess
import sys

import pytest

from app.dev import production_reconciliation as prod
from app.dev import reconcile_schema as core
from tests.test_schema_reconciliation import legacy, reference, state  # noqa: F401


@pytest.fixture
def authorized(legacy, tmp_path, monkeypatch):
    with sqlite3.connect(legacy) as connection:
        connection.execute("INSERT INTO execution_orders "
                           "(id,broker_type,symbol,side,requested_quantity,filled_quantity,status,"
                           "rejection_reason,reference_price,submitted_at,execution_version) "
                           "VALUES ('rejected-order','SIM','TEST','BUY','1','0','REJECTED',"
                           "'NO_NEXT_BAR','10','2026-09-06','v1')")
    backup = tmp_path / 'backup.sqlite3'
    with core.connect(legacy) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    with core.connect(legacy) as source:
        before = core.row_state(source)
        orders = [list(r) for r in source.execute('SELECT id,status,rejection_reason FROM execution_orders ORDER BY id')]
        fills = [list(r) for r in source.execute('SELECT id,order_id FROM execution_fills ORDER BY id')]
    manifest = {
        'database_path': str(legacy), 'expected_git_revision': 'a' * 40,
        'expected_row_hash': before['row_hash'], 'backup_path': str(backup),
        'expected_backup_sha256': prod.file_hash(backup), 'expected_counts': before['counts'],
        'expected_revision_state': prod.PRE_STATE, 'historical_orders': orders, 'historical_fills': fills,
    }
    path = tmp_path / 'authorization.json'
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(core, 'OPERATOR', legacy)
    monkeypatch.setattr(prod, 'settings_truth', lambda: ('real_market_operator', legacy))
    monkeypatch.setattr(prod, 'git_truth', lambda: ('a' * 40, True))
    monkeypatch.setattr(prod, 'runtime_status', lambda: {
        'listener_8000': False, 'backend_pids': [], 'host_visibility': True})
    return legacy, path, manifest


def run(authorized, *, apply=False, **overrides):
    target, path, _ = authorized
    kwargs = dict(apply=apply, preflight=not apply, allow_production=True,
                  manifest_path=path, confirmation=prod.CONFIRMATION if apply else None)
    kwargs.update(overrides)
    return prod.production_reconcile(target, **kwargs)


def persist(authorized):
    authorized[1].write_text(json.dumps(authorized[2]))


def test_preflight_read_only_without_confirmation(authorized, monkeypatch):
    target, _, manifest = authorized
    before = state(target)
    raw = target.read_bytes()
    statements = []
    original = prod.authorized_connection

    @contextmanager
    def traced(path, *, write):
        assert write is False
        with original(path, write=write) as connection:
            assert connection.execute('PRAGMA query_only').fetchone() == (1,)
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(prod, 'authorized_connection', traced)
    report = run(authorized)
    assert report['status'] == 'PRODUCTION_RECONCILIATION_PREFLIGHT_PASS'
    assert report['backup_sha256'] == manifest['expected_backup_sha256']
    assert state(target) == before and target.read_bytes() == raw
    assert not any(s.startswith(('BEGIN IMMEDIATE', 'CREATE', 'ALTER', 'DROP', 'INSERT', 'UPDATE', 'DELETE'))
                   for s in statements)


def test_authorized_apply_exact_match_without_stamp(authorized, reference):
    target = authorized[0]
    before = state(target)[0]['business']
    report = run(authorized, apply=True)
    assert report['status'] == 'PRODUCTION_RECONCILIATION_APPLIED'
    with core.connect(target) as connection, core.connect(reference) as ref:
        assert core.fingerprint(connection) == core.fingerprint(ref)
        assert core.row_state(connection) == before
        assert not core.versioned(connection)
        assert core.integrity(connection)['fk_violations'] == 0


@pytest.mark.parametrize('apply', [False, True])
def test_missing_production_flag_rejected(authorized, apply):
    before = state(authorized[0])
    with pytest.raises(core.GuardError, match='PRODUCTION_AUTHORIZATION_REQUIRED'):
        run(authorized, apply=apply, allow_production=False)
    assert state(authorized[0]) == before


@pytest.mark.parametrize('token', [None, '', 'OPERATOR_DB_SCHEMA_RECONCILIATIO'])
def test_confirmation_required(authorized, token):
    with pytest.raises(core.GuardError, match='CONFIRMATION_REQUIRED'):
        run(authorized, apply=True, confirmation=token)


@pytest.mark.parametrize('mutation,code', [
    ('git', 'GIT_REVISION_MISMATCH'), ('row', 'BUSINESS_HASH_MISMATCH'),
    ('backup_hash', 'BACKUP_CHECKSUM_MISMATCH'), ('path', 'WRONG_OPERATOR_PATH'),
    ('count', 'CRITICAL_COUNT_MISMATCH'), ('historical', 'HISTORICAL_EVIDENCE_MISMATCH'),
    ('missing_backup', 'INVALID_BACKUP_PATH'), ('version_state', 'INVALID_REVISION_STATE'),
])
@pytest.mark.parametrize('apply', [False, True])
def test_manifest_tampering_rejected(authorized, mutation, code, apply):
    target, _, m = authorized
    if mutation == 'git': m['expected_git_revision'] = 'b' * 40
    if mutation == 'row': m['expected_row_hash'] = 'b' * 64
    if mutation == 'backup_hash': m['expected_backup_sha256'] = 'b' * 64
    if mutation == 'path': m['database_path'] = str(target.parent / 'wrong.sqlite3')
    if mutation == 'count': m['expected_counts']['human_decisions'] += 1
    if mutation == 'historical': m['historical_orders'][0][0] = 'unknown-order'
    if mutation == 'missing_backup': m['backup_path'] = str(target.parent / 'missing.sqlite3')
    if mutation == 'version_state': m['expected_revision_state'] = 'VERSIONED'
    # Keep historical FK manifest internally valid so DB evidence is what fails.
    if mutation == 'historical': m['historical_fills'][0][1] = 'unknown-order'
    persist(authorized)
    before = state(target)
    with pytest.raises(core.GuardError, match=code):
        run(authorized, apply=apply)
    assert state(target) == before


@pytest.mark.parametrize('kind,code', [
    ('dirty', 'DIRTY_WORKING_TREE'), ('backend_port', 'BACKEND_ACTIVE'),
    ('backend_process', 'BACKEND_ACTIVE'), ('visibility', 'RUNTIME_VISIBILITY_UNVERIFIED'),
    ('profile', 'RUNTIME_PROFILE_MISMATCH'), ('profile_path', 'RUNTIME_PROFILE_MISMATCH'),
])
def test_environment_guards(authorized, monkeypatch, kind, code):
    if kind == 'dirty': monkeypatch.setattr(prod, 'git_truth', lambda: ('a' * 40, False))
    if kind.startswith('backend') or kind == 'visibility':
        monkeypatch.setattr(prod, 'runtime_status', lambda: {
            'listener_8000': kind == 'backend_port',
            'backend_pids': [123] if kind == 'backend_process' else [],
            'host_visibility': kind != 'visibility'})
    if kind.startswith('profile'):
        monkeypatch.setattr(prod, 'settings_truth', lambda: (
            'default' if kind == 'profile' else 'real_market_operator',
            authorized[0] if kind == 'profile' else authorized[0].parent / 'other.sqlite3'))
    before = state(authorized[0])
    with pytest.raises(core.GuardError, match=code): run(authorized, apply=True)
    assert state(authorized[0]) == before


@pytest.mark.parametrize('kind,code', [
    ('data', 'BACKUP_BUSINESS_MISMATCH'), ('schema', 'BACKUP_SCHEMA_MISMATCH'),
    ('fk', 'INTEGRITY_FAILED'), ('version', 'BACKUP_STATE_MISMATCH'),
    ('corrupt', 'PRODUCTION_PREFLIGHT_IO_FAILED'), ('wal', 'BACKUP_NOT_STANDALONE'),
])
def test_backup_independent_validation(authorized, kind, code):
    backup = Path(authorized[2]['backup_path'])
    if kind in {'data', 'schema', 'fk', 'version'}:
        with sqlite3.connect(backup) as connection:
            if kind == 'data': connection.execute("UPDATE execution_orders SET symbol='OTHER'")
            if kind == 'schema': connection.execute('CREATE TABLE unexpected (id INTEGER)')
            if kind == 'fk': connection.execute("UPDATE execution_fills SET order_id='missing'")
            if kind == 'version': connection.execute('CREATE TABLE alembic_version (version_num TEXT)')
    if kind == 'corrupt': backup.write_bytes(b'not a database')
    if kind == 'wal': Path(str(backup) + '-wal').touch()
    authorized[2]['expected_backup_sha256'] = prod.file_hash(backup)
    persist(authorized)
    before = state(authorized[0])
    with pytest.raises(core.GuardError, match=code): run(authorized)
    assert state(authorized[0]) == before


@pytest.mark.parametrize('kind', ['same', 'symlink', 'hardlink', 'directory'])
def test_backup_path_guard(authorized, kind):
    target, _, manifest = authorized
    backup = Path(manifest['backup_path'])
    alias = target.parent / 'invalid-backup'
    if kind == 'same': alias = target
    if kind == 'symlink': alias.symlink_to(backup)
    if kind == 'hardlink': alias.hardlink_to(backup)
    if kind == 'directory': alias.mkdir()
    manifest['backup_path'] = str(alias)
    persist(authorized)
    with pytest.raises(core.GuardError, match='INVALID_BACKUP_PATH'): run(authorized)


@pytest.mark.parametrize('kind', ['wrong', 'relative', 'symlink', 'hardlink'])
def test_exact_official_path(authorized, kind):
    target, manifest_path, _ = authorized
    requested = target.parent / ('wrong-' + kind)
    if kind == 'wrong': requested.touch()
    if kind == 'relative': requested = Path('relative.sqlite3')
    if kind == 'symlink': requested.symlink_to(target)
    if kind == 'hardlink': requested.hardlink_to(target)
    with pytest.raises(core.GuardError, match='WRONG_OPERATOR_PATH'):
        prod.production_reconcile(requested, preflight=True, allow_production=True, manifest_path=manifest_path)


def test_preflight_pass_is_not_reused_by_apply(authorized):
    assert run(authorized)['status'].endswith('PREFLIGHT_PASS')
    with sqlite3.connect(authorized[0]) as connection:
        connection.execute("UPDATE execution_orders SET symbol='CHANGED'")
    before = state(authorized[0])
    with pytest.raises(core.GuardError, match='BUSINESS_HASH_MISMATCH'): run(authorized, apply=True)
    assert state(authorized[0]) == before


def test_recheck_after_lock_and_rollback(authorized, monkeypatch):
    original = prod.authorized_connection
    locked = []

    @contextmanager
    def changed(path, *, write):
        if write:
            with sqlite3.connect(path) as writer:
                writer.execute("UPDATE execution_orders SET symbol='BETWEEN_CHECK_AND_LOCK'")
        with original(path, write=write) as connection:
            if write: connection.set_trace_callback(locked.append)
            yield connection

    monkeypatch.setattr(prod, 'authorized_connection', changed)
    with pytest.raises(core.GuardError, match='BUSINESS_HASH_MISMATCH'): run(authorized, apply=True)
    assert 'BEGIN IMMEDIATE' in locked and 'ROLLBACK' in locked
    assert not any(s.startswith('CREATE') for s in locked)


def test_production_rebuild_failure_atomic(authorized, monkeypatch):
    before = state(authorized[0])
    original = core.rebuild

    def fail(connection, reference, table):
        original(connection, reference, table)
        raise RuntimeError('injected rebuild failure')

    monkeypatch.setattr(core, 'rebuild', fail)
    with pytest.raises(RuntimeError, match='injected rebuild failure'): run(authorized, apply=True)
    assert state(authorized[0]) == before


def test_preflight_wal_preserves_data_with_only_shm_reader_marks(authorized):
    target = authorized[0]
    with sqlite3.connect(target) as keeper:
        keeper.execute('PRAGMA journal_mode=WAL')
        keeper.execute("UPDATE execution_orders SET symbol=symbol")
        keeper.commit()
        before = {s: Path(str(target)+s).read_bytes() for s in ['', '-wal', '-shm']}
        assert run(authorized)['status'].endswith('PREFLIGHT_PASS')
        after = {s: Path(str(target)+s).read_bytes() for s in before}
        assert before[''] == after[''] and before['-wal'] == after['-wal']
        # SQLite read-only WAL readers can update documented SHM read marks.
        # Keep this edge case visible instead of falsely claiming SHM immutability.
        changed = {i for i, (a, b) in enumerate(zip(before['-shm'], after['-shm'])) if a != b}
        assert len(before['-shm']) == len(after['-shm'])
        assert changed <= set(range(100, 120))


def test_parser_preflight_and_apply_exclusion(authorized, monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['reconcile_schema', str(authorized[0]), '--preflight-production',
                                  '--allow-production-target', '--authorization-manifest', str(authorized[1])])
    assert core.main() == 0
    assert json.loads(capsys.readouterr().out)['status'].endswith('PREFLIGHT_PASS')
    monkeypatch.setattr('sys.argv', ['reconcile_schema', str(authorized[0]), '--preflight-production', '--apply'])
    with pytest.raises(SystemExit) as error: core.main()
    assert error.value.code == 2


@pytest.mark.parametrize('kind,active,visible', [
    ('stopped', False, True), ('port', True, True), ('uvicorn', True, True),
    ('gunicorn', True, True), ('frontend', False, True), ('container', False, False),
    ('systemd_container', False, False), ('network_namespace', False, False),
])
def test_linux_runtime_checker(tmp_path, monkeypatch, kind, active, visible):
    (tmp_path / '1').mkdir()
    (tmp_path / '1/comm').write_text('codex' if kind == 'container' else 'systemd')
    (tmp_path / '1/cmdline').write_bytes(b'/sbin/init\0')
    (tmp_path / '1/ns').mkdir()
    (tmp_path / 'self/ns').mkdir(parents=True)
    pid_namespace = 999 if kind in {'container', 'systemd_container'} else prod.INITIAL_PID_NAMESPACE
    net_namespace = 999 if kind == 'network_namespace' else prod.INITIAL_NET_NAMESPACE
    (tmp_path / '1/ns/pid').symlink_to(f'pid:[{pid_namespace}]')
    (tmp_path / 'self/ns/pid').symlink_to(f'pid:[{pid_namespace}]')
    (tmp_path / 'self/ns/net').symlink_to(f'net:[{net_namespace}]')
    (tmp_path / 'net').mkdir()
    line = '0: 0100007F:1F40 00000000:0000 0A 0:0 0:0 0 1000 0 1\n' if kind == 'port' else ''
    (tmp_path / 'net/tcp').write_text('header\n' + line)
    (tmp_path / 'net/tcp6').write_text('header\n')
    if kind in {'uvicorn', 'gunicorn', 'frontend'}:
        (tmp_path / '123').mkdir()
        cmd = {'uvicorn': b'python\0-m\0uvicorn\0app.main:app\0',
               'gunicorn': b'gunicorn\0app.main:app\0', 'frontend': b'node\0vite\0'}[kind]
        (tmp_path / '123/cmdline').write_bytes(cmd)
    monkeypatch.setattr(prod, 'PROC_ROOT', tmp_path)
    result = prod.runtime_status()
    assert bool(result['listener_8000'] or result['backend_pids']) == active
    assert result['host_visibility'] == visible


def test_missing_proc_is_not_assumed_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(prod, 'PROC_ROOT', tmp_path)
    with pytest.raises(core.GuardError, match='RUNTIME_VISIBILITY_UNVERIFIED'):
        prod.runtime_status()


@pytest.mark.parametrize('kind', ['duplicate', 'unknown', 'missing', 'malformed', 'short_revision'])
def test_manifest_structure_rejected(authorized, kind):
    _, path, manifest = authorized
    if kind == 'duplicate': path.write_text('{"database_path":"one","database_path":"two"}')
    elif kind == 'malformed': path.write_text('{')
    else:
        if kind == 'unknown': manifest['unexpected'] = True
        if kind == 'missing': manifest.pop('expected_backup_sha256')
        if kind == 'short_revision': manifest['expected_git_revision'] = 'a9a85d4'
        persist(authorized)
    with pytest.raises(core.GuardError, match='INVALID_AUTHORIZATION_MANIFEST'):
        run(authorized)


def test_missing_manifest_rejected(authorized):
    with pytest.raises(core.GuardError, match='AUTHORIZATION_MANIFEST_REQUIRED'):
        run(authorized, manifest_path=None)


@pytest.mark.parametrize('ddl,code', [
    ('CREATE TABLE alembic_version(version_num TEXT)', 'ALREADY_VERSIONED_OR_UNEXPECTED_DB'),
    ('CREATE TABLE unexpected(id INTEGER)', 'PRECONDITION_SCHEMA_MISMATCH'),
    ('CREATE TABLE runtime_failures__reconcile_new(id INTEGER)', 'REPLACEMENT_TABLE_EXISTS'),
    ("INSERT INTO runtime_failures(failure_code,severity,component,message,occurred_at,created_at,metadata_json,resolved) "
     "VALUES('TEST','WARNING','TEST','test','2026-09-06','2026-09-06','{}',0)", 'AFFECTED_TABLE_NOT_EMPTY'),
])
def test_production_keeps_legacy_schema_guards(authorized, ddl, code):
    with sqlite3.connect(authorized[0]) as connection: connection.execute(ddl)
    before = state(authorized[0])
    with pytest.raises(core.GuardError, match=code): run(authorized, apply=True)
    assert state(authorized[0]) == before


def test_git_checker_includes_untracked(monkeypatch):
    calls = []
    def fake(argv, **kwargs):
        calls.append(argv)
        return 'a' * 40 + '\n' if argv[1] == 'rev-parse' else '?? unexpected.py\n'
    monkeypatch.setattr(prod.subprocess, 'check_output', fake)
    assert prod.git_truth() == ('a' * 40, False)
    assert '--untracked-files=all' in calls[1]


def test_git_checker_errors_fail_closed(monkeypatch):
    def fail(*args, **kwargs): raise OSError('cannot run git')
    monkeypatch.setattr(prod.subprocess, 'check_output', fail)
    with pytest.raises(core.GuardError, match='GIT_STATE_UNAVAILABLE'): prod.git_truth()


@pytest.mark.parametrize('kind,code', [
    ('backend', 'BACKEND_ACTIVE'), ('git', 'DIRTY_WORKING_TREE'),
    ('backup', 'BACKUP_CHECKSUM_MISMATCH'),
])
def test_environment_and_backup_are_rechecked_under_lock(authorized, monkeypatch, kind, code):
    original = prod.authorized_connection
    before = state(authorized[0])
    statements = []

    @contextmanager
    def changed(path, *, write):
        if write:
            if kind == 'backend':
                monkeypatch.setattr(prod, 'runtime_status', lambda: {
                    'listener_8000': True, 'backend_pids': [], 'host_visibility': True})
            if kind == 'git': monkeypatch.setattr(prod, 'git_truth', lambda: ('a' * 40, False))
            if kind == 'backup':
                with sqlite3.connect(authorized[2]['backup_path']) as backup:
                    backup.execute("UPDATE execution_orders SET symbol='CHANGED_BACKUP'")
        with original(path, write=write) as connection:
            if write: connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(prod, 'authorized_connection', changed)
    with pytest.raises(core.GuardError, match=code): run(authorized, apply=True)
    assert 'BEGIN IMMEDIATE' in statements and 'ROLLBACK' in statements
    assert state(authorized[0]) == before


def test_module_cli_guard_failure_is_structured_json(tmp_path):
    target = tmp_path / 'target.sqlite3'
    target.touch()
    result = subprocess.run(
        [sys.executable, '-m', 'app.dev.reconcile_schema', str(target), '--preflight-production'],
        cwd=core.PROJECT_ROOT,
        env={**os.environ, 'PYTHONPATH': str(core.PROJECT_ROOT / 'backend')},
        capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)['status'] == 'PRODUCTION_AUTHORIZATION_REQUIRED'
    assert result.stderr == ''
