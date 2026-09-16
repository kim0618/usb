"""Shared backtest workspace foundation: no Massive call, no Kiwoom call, no real Drive path.

Every test builds a fake mount tree under ``tmp_path`` so the same assertions hold on a
PC where Google Drive is ``G:`` and on one where it is ``H:``.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from app.backtest.workspace import discovery, lock as lock_module, manifest as manifest_module
from app.backtest.workspace.discovery import (
    WORKSPACE_DIR_NAME, discover_workspace_roots, resolve_workspace_root, validate_workspace_root,
)
from app.backtest.workspace.errors import (
    ManifestError, ManifestIncomplete, SafeWriteError, SecretLikeValue, StateInvalid,
    WorkspaceAmbiguous, WorkspaceInsideRepo, WorkspaceNameMismatch, WorkspaceNotFound,
    WorkspaceSchemaIncompatible, WriterLockHeld, WriterLockNotOwned, WriterLockStale,
)
from app.backtest.workspace.identity import IDENTITY_SCHEMA_VERSION, read_identity
from app.backtest.workspace.layout import WORKSPACE_DIRECTORIES, Workspace
from app.backtest.workspace.manifest import (
    ManifestKey, counts_by_status, mark_collecting, mark_complete, open_manifest, plan_entry,
    quick_check,
)
from app.backtest.workspace.operations import diagnose, initialize
from app.backtest.workspace.safe_write import safe_write, sha256_file
from app.backtest.workspace.state import (
    UNCOMMITTED, CurrentState, read_current_state, update_current_state, write_current_state,
)
from app.core.config import PROJECT_ROOT
from app.dev import backtest_workspace as cli


NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def mount_workspace(base: Path, letter: str, *, drive_root: str = "내 드라이브") -> Path:
    root = base / letter / drive_root / WORKSPACE_DIR_NAME
    root.mkdir(parents=True)
    return root.resolve()


def state(**overrides: object) -> CurrentState:
    values: dict[str, object] = {
        "stage": "MASSIVE_MULTI_SYMBOL_5_SESSION_VALIDATION",
        "status": "PASS_WITH_LIMITATIONS",
        "provider": "massive",
        "source_commit": UNCOMMITTED,
        "last_completed_at": NOW.isoformat(timespec="seconds"),
        "next_stage": "MASSIVE_AAPL_1_YEAR_FEASIBILITY",
        "notes": "AAPL/AMD/ORCL x 5 completed XNYS sessions.",
    }
    values.update(overrides)
    return CurrentState(**values)  # type: ignore[arg-type]


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    root = mount_workspace(tmp_path / "mnt", "g")
    initialize(root, seed_state=state(), now=NOW)
    return Workspace(root)  # mount_workspace already returns a resolved path


# --- C. discovery -----------------------------------------------------------------


def test_discovers_the_google_drive_root(tmp_path: Path) -> None:
    root = mount_workspace(tmp_path / "mnt", "g")
    assert discover_workspace_roots([tmp_path / "mnt"]) == (root,)
    assert resolve_workspace_root(bases=[tmp_path / "mnt"]) == root


def test_discovery_follows_a_different_drive_letter(tmp_path: Path) -> None:
    """The office PC mounts the same Drive as H: and nothing in the code says g."""
    root = mount_workspace(tmp_path / "mnt", "h")
    assert discover_workspace_roots([tmp_path / "mnt"]) == (root,)


def test_discovery_accepts_the_english_drive_root(tmp_path: Path) -> None:
    root = mount_workspace(tmp_path / "mnt", "h", drive_root="My Drive")
    assert discover_workspace_roots([tmp_path / "mnt"]) == (root,)


def test_no_workspace_is_not_found(tmp_path: Path) -> None:
    (tmp_path / "mnt" / "c").mkdir(parents=True)
    with pytest.raises(WorkspaceNotFound) as error:
        resolve_workspace_root(bases=[tmp_path / "mnt"])
    assert "BACKTEST_WORKSPACE_NOT_FOUND" in str(error.value)


def test_two_workspaces_are_ambiguous(tmp_path: Path) -> None:
    mount_workspace(tmp_path / "mnt", "g")
    mount_workspace(tmp_path / "mnt", "h")
    assert len(discover_workspace_roots([tmp_path / "mnt"])) == 2
    with pytest.raises(WorkspaceAmbiguous) as error:
        resolve_workspace_root(bases=[tmp_path / "mnt"])
    assert "BACKTEST_WORKSPACE_AMBIGUOUS" in str(error.value)


def test_a_candidate_inside_the_repo_is_never_used(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """No fallback root inside the git repository, discovered or explicit."""
    fake_repo = tmp_path / "repo"
    monkeypatch.setattr(discovery, "PROJECT_ROOT", fake_repo)
    mount_workspace(fake_repo / "mnt", "g")
    assert discover_workspace_roots([fake_repo / "mnt"]) == ()
    with pytest.raises(WorkspaceNotFound):
        resolve_workspace_root(bases=[fake_repo / "mnt"])
    with pytest.raises(WorkspaceInsideRepo):
        validate_workspace_root(fake_repo / "mnt" / "g" / "내 드라이브" / WORKSPACE_DIR_NAME)


def test_repo_root_itself_is_rejected() -> None:
    with pytest.raises(WorkspaceInsideRepo):
        validate_workspace_root(PROJECT_ROOT / WORKSPACE_DIR_NAME, must_exist=False)


def test_explicit_root_must_carry_the_shared_name(tmp_path: Path) -> None:
    other = tmp_path / "US-B"
    other.mkdir()
    with pytest.raises(WorkspaceNameMismatch):
        validate_workspace_root(other)


def test_unreadable_mount_base_does_not_break_discovery(tmp_path: Path) -> None:
    assert discover_workspace_roots([tmp_path / "missing"]) == ()


# --- D/E. init and identity -------------------------------------------------------


def test_init_creates_the_layout_and_is_idempotent(tmp_path: Path) -> None:
    root = mount_workspace(tmp_path / "mnt", "g")
    first = initialize(root, seed_state=state(), now=NOW)
    assert {Path(path).relative_to(root).as_posix() for path in first.created_directories} == set(
        WORKSPACE_DIRECTORIES)
    assert first.identity_created and first.manifest_created and first.state_created

    second = initialize(root, seed_state=state(status="FAIL"), now=NOW)
    assert second.created_directories == ()
    assert not second.identity_created and not second.state_created
    assert second.state is not None and second.state.status == "PASS_WITH_LIMITATIONS"


def test_init_preserves_existing_data(tmp_path: Path) -> None:
    root = mount_workspace(tmp_path / "mnt", "g")
    initialize(root, seed_state=state(), now=NOW)
    payload = root / "market_data" / "raw" / "massive" / "aapl.json"
    payload.write_text('{"kept": true}', encoding="utf-8")
    identity_before = (root / "state" / "workspace.json").read_text(encoding="utf-8")

    initialize(root, seed_state=state(), now=NOW + timedelta(days=1))
    assert payload.read_text(encoding="utf-8") == '{"kept": true}'
    assert (root / "state" / "workspace.json").read_text(encoding="utf-8") == identity_before


def test_init_can_create_the_root_next_to_a_mounted_drive(tmp_path: Path) -> None:
    drive = tmp_path / "mnt" / "g" / "내 드라이브"
    drive.mkdir(parents=True)
    report = initialize(drive / WORKSPACE_DIR_NAME, create_root=True, seed_state=state(), now=NOW)
    assert report.root_created and report.workspace.root.is_dir()


def test_identity_schema_is_validated(workspace: Workspace) -> None:
    identity = read_identity(workspace)
    assert identity is not None and identity.workspace_schema_version == IDENTITY_SCHEMA_VERSION
    assert identity.workspace_name == WORKSPACE_DIR_NAME

    payload = json.loads(workspace.identity_path.read_text(encoding="utf-8"))
    payload["workspace_schema_version"] = 99
    workspace.identity_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WorkspaceSchemaIncompatible):
        read_identity(workspace)


# --- F. shared current state ------------------------------------------------------


def test_current_state_round_trips(workspace: Workspace) -> None:
    loaded = read_current_state(workspace)
    assert loaded is not None
    assert loaded.stage == "MASSIVE_MULTI_SYMBOL_5_SESSION_VALIDATION"
    assert loaded.source_commit == UNCOMMITTED and not loaded.committed

    updated = update_current_state(workspace, source_commit="a" * 40, now=NOW)
    assert updated.committed
    assert read_current_state(workspace).source_commit == "a" * 40


def test_current_state_write_leaves_no_partial_file(workspace: Workspace) -> None:
    write_current_state(workspace, state(status="PASS"))
    leftovers = [path.name for path in workspace.state_dir.iterdir()
                 if ".tmp-" in path.name or path.name.endswith(".partial")]
    assert leftovers == []


def test_invalid_current_state_is_rejected(workspace: Workspace) -> None:
    payload = json.loads(workspace.current_state_path.read_text(encoding="utf-8"))
    payload["status"] = "pass with limitations"
    workspace.current_state_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(StateInvalid):
        read_current_state(workspace)

    workspace.current_state_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateInvalid):
        read_current_state(workspace)


def test_state_rejects_a_fabricated_commit(workspace: Workspace) -> None:
    with pytest.raises(StateInvalid):
        write_current_state(workspace, state(source_commit="not-a-commit"))


def test_state_refuses_secret_like_content(workspace: Workspace) -> None:
    with pytest.raises(SecretLikeValue):
        write_current_state(workspace, state(notes="massive api_key=abcd1234 for the collector"))

    payload = json.loads(workspace.current_state_path.read_text(encoding="utf-8"))
    payload["massive_api_key"] = "whatever"
    workspace.current_state_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises((SecretLikeValue, StateInvalid)):
        read_current_state(workspace)


# --- G. manifest ------------------------------------------------------------------


KEY = ManifestKey(provider="massive", symbol="AAPL", data_kind="raw_minute", timeframe="1min",
                  start_date="2026-09-08", end_date="2026-09-12")


def test_manifest_creates_and_reopens(workspace: Workspace) -> None:
    connection = open_manifest(workspace.manifest_path)
    entry_id = plan_entry(connection, KEY, collector_version="v0", now=NOW)
    assert plan_entry(connection, KEY, collector_version="v0", now=NOW) == entry_id
    connection.close()

    reopened = open_manifest(workspace.manifest_path, create=False)
    assert quick_check(reopened) == "ok"
    assert counts_by_status(reopened) == {"PLANNED": 1, "COLLECTING": 0, "COMPLETE": 0, "FAILED": 0}
    reopened.close()


def test_manifest_rejects_complete_without_a_final_file(workspace: Workspace) -> None:
    connection = open_manifest(workspace.manifest_path)
    entry_id = plan_entry(connection, KEY, collector_version="v0", now=NOW)
    mark_collecting(connection, entry_id, now=NOW)

    relative = "market_data/raw/massive/aapl_2026-09-08_2026-09-12.json"
    with pytest.raises(ManifestIncomplete):
        mark_complete(connection, entry_id, workspace=workspace, relative_path=relative,
                      checksum="0" * 64, row_count=10, now=NOW)
    with pytest.raises(ManifestIncomplete):
        mark_complete(connection, entry_id, workspace=workspace,
                      relative_path=f"{relative}.partial", checksum="0" * 64, row_count=10,
                      now=NOW)

    target = workspace.root / relative
    with safe_write(target) as handle:
        handle.partial_path.write_text('{"rows": 10}', encoding="utf-8")
    mark_complete(connection, entry_id, workspace=workspace, relative_path=relative,
                  checksum=handle.checksum or "", row_count=10, now=NOW)
    assert counts_by_status(connection)["COMPLETE"] == 1

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "UPDATE collector_entries SET checksum = NULL WHERE id = ?", (entry_id,))
    connection.close()


def test_manifest_schema_version_mismatch_is_refused(workspace: Workspace,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    open_manifest(workspace.manifest_path).close()
    monkeypatch.setattr(manifest_module, "MANIFEST_SCHEMA_VERSION", 99)
    with pytest.raises(ManifestError):
        open_manifest(workspace.manifest_path, create=False)


# --- H. writer lock ---------------------------------------------------------------


def test_writer_lock_acquire_and_release(workspace: Workspace) -> None:
    held = lock_module.acquire(workspace, purpose="collect_massive", now=NOW)
    assert workspace.writer_lock_path.is_file()
    assert lock_module.read_lock(workspace).owner_id == held.owner_id
    lock_module.release(workspace, held)
    assert not workspace.writer_lock_path.exists()


def test_competing_writer_is_rejected(workspace: Workspace) -> None:
    first = lock_module.acquire(workspace, purpose="collect_massive", now=NOW)
    with pytest.raises(WriterLockHeld):
        lock_module.acquire(workspace, purpose="backtest_run", now=NOW + timedelta(minutes=1))
    with pytest.raises(WriterLockNotOwned):
        lock_module.release(workspace, replace(first, owner_id="someone-else"))
    assert workspace.writer_lock_path.is_file()


def test_stale_lock_is_reported_not_deleted(workspace: Workspace) -> None:
    lock_module.acquire(workspace, purpose="collect_massive", lease_seconds=60, now=NOW)
    later = NOW + timedelta(minutes=30)
    with pytest.raises(WriterLockStale):
        lock_module.acquire(workspace, purpose="backtest_run", now=later)
    assert workspace.writer_lock_path.is_file(), "a stale lock is never removed automatically"

    with pytest.raises(WriterLockHeld):
        lock_module.force_release(workspace, now=NOW)
    released = lock_module.force_release(workspace, now=later)
    assert released is not None and not workspace.writer_lock_path.exists()


def test_heartbeat_extends_the_lease(workspace: Workspace) -> None:
    held = lock_module.acquire(workspace, purpose="collect_massive", lease_seconds=60, now=NOW)
    renewed = lock_module.heartbeat(workspace, held, now=NOW + timedelta(seconds=30))
    assert not renewed.is_stale(NOW + timedelta(seconds=60))
    assert renewed.is_stale(NOW + timedelta(seconds=120))


# --- I. safe file write -----------------------------------------------------------


def test_safe_write_publishes_only_on_success(workspace: Workspace) -> None:
    target = workspace.root / "market_data" / "normalized" / "minute" / "aapl.csv"
    with safe_write(target) as handle:
        handle.partial_path.write_text("t,o,h,l,c\n", encoding="utf-8")
        assert not target.exists(), "the final path appears only after the rename"
    assert target.is_file() and handle.checksum == sha256_file(target)
    assert not handle.partial_path.exists()


def test_safe_write_removes_the_partial_on_failure(workspace: Workspace) -> None:
    target = workspace.root / "market_data" / "normalized" / "minute" / "amd.csv"
    with pytest.raises(RuntimeError):
        with safe_write(target) as handle:
            handle.partial_path.write_text("half a file", encoding="utf-8")
            raise RuntimeError("collector died mid-write")
    assert not target.exists()
    assert not handle.partial_path.exists()

    with pytest.raises(SafeWriteError):
        with safe_write(target):
            pass


# --- K. CLI -----------------------------------------------------------------------


def test_cli_status_and_doctor_report_without_secrets(workspace: Workspace,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--workspace-root", str(workspace.root), "status"]) == 0
    status_output = capsys.readouterr().out
    assert f"workspace_root={workspace.root}" in status_output
    assert "current_stage=MASSIVE_MULTI_SYMBOL_5_SESSION_VALIDATION" in status_output
    assert "writer_lock=FREE" in status_output
    assert "manifest=planned:0" in status_output

    assert cli.main(["--workspace-root", str(workspace.root), "doctor"]) == 0
    doctor_output = capsys.readouterr().out
    assert "doctor=OK" in doctor_output
    for line in status_output.splitlines() + doctor_output.splitlines():
        assert "api_key" not in line.lower() and "secret" not in line.lower()


def test_cli_init_seeds_the_verified_stage(tmp_path: Path,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    root = mount_workspace(tmp_path / "mnt", "g")
    assert cli.main(["--workspace-root", str(root), "init"]) == 0
    output = capsys.readouterr().out
    assert "state_created=True" in output
    assert f"source_commit={UNCOMMITTED}" in output
    seeded = read_current_state(Workspace(root))
    assert seeded is not None and seeded.stage == cli.INITIAL_STAGE
    assert seeded.next_stage == cli.INITIAL_NEXT_STAGE


def test_cli_reports_a_missing_workspace_without_creating_one(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The default discovery path, driven by a mount tree that holds no workspace.

    The bases are read through ``discovery.mount_bases`` at call time, so this patch
    actually reaches the CLI and the result no longer depends on whether the machine
    running the tests happens to have Google Drive mounted.
    """
    monkeypatch.setattr(discovery, "DEFAULT_MOUNT_BASES", (tmp_path / "mnt",))
    (tmp_path / "mnt" / "c").mkdir(parents=True)
    assert discovery.mount_bases() == (tmp_path / "mnt",)
    with pytest.raises(SystemExit) as error:
        cli.main(["status"])
    assert "BACKTEST_WORKSPACE_NOT_FOUND" in str(error.value)
    assert not (PROJECT_ROOT / WORKSPACE_DIR_NAME).exists()


def test_default_mount_bases_are_read_at_call_time_not_at_import(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A default argument would freeze the real /mnt into the signature at import.

    That is exactly how this suite came to depend on the machine it ran on: the
    override looked effective and the CLI kept searching the real Drive mount.
    """
    monkeypatch.setattr(discovery, "DEFAULT_MOUNT_BASES", (tmp_path / "mnt",))
    root = mount_workspace(tmp_path / "mnt", "g")
    assert discovery.discover_workspace_roots() == (root,)
    assert discovery.resolve_workspace_root() == root


def test_doctor_fails_on_a_missing_directory(workspace: Workspace) -> None:
    (workspace.root / "backtest" / "reports").rmdir()
    diagnosis = diagnose(workspace)
    assert not diagnosis.ok
    failed = [check.name for check in diagnosis.checks if not check.ok]
    assert failed == ["required_directories"]
