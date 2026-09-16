"""Initialize and diagnose a shared workspace.

Git is only ever read here. Nothing in this module runs add, commit, push, pull, reset,
stash, or rebase: the commit itself stays a deliberate human step.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import sqlite3
import subprocess

from app.backtest.workspace.discovery import validate_workspace_root
from app.backtest.workspace.errors import WorkspaceError, WorkspaceNotFound
from app.backtest.workspace.identity import WorkspaceIdentity, ensure_identity, read_identity
from app.backtest.workspace.layout import Workspace, ensure_directories
from app.backtest.workspace.lock import read_lock
from app.backtest.workspace.manifest import (
    MANIFEST_SCHEMA_VERSION, counts_by_status, manifest_connection, quick_check,
)
from app.backtest.workspace.safe_write import safe_write
from app.backtest.workspace.state import CurrentState, read_current_state, write_current_state
from app.core.config import PROJECT_ROOT


GIT_TIMEOUT_SECONDS = 15
PROBE_NAME = ".doctor_probe"


@dataclass(frozen=True)
class InitReport:
    workspace: Workspace
    root_created: bool
    created_directories: tuple[Path, ...]
    identity: WorkspaceIdentity
    identity_created: bool
    manifest_created: bool
    state: CurrentState | None
    state_created: bool


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class GitSnapshot:
    available: bool
    commit: str | None = None
    branch: str | None = None
    dirty_files: int = 0
    detail: str = ""


@dataclass
class Diagnosis:
    workspace: Workspace
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True,
                          timeout=GIT_TIMEOUT_SECONDS, check=False)


def git_snapshot(repo_root: Path = PROJECT_ROOT) -> GitSnapshot:
    """Read-only view of the repository the workspace is paired with."""
    try:
        head = _git(repo_root, "rev-parse", "HEAD")
        if head.returncode != 0:
            return GitSnapshot(available=False, detail=head.stderr.strip() or "git rev-parse failed")
        branch = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
        status = _git(repo_root, "status", "--porcelain")
    except (OSError, subprocess.SubprocessError) as error:
        return GitSnapshot(available=False, detail=str(error))
    dirty = len([line for line in status.stdout.splitlines() if line.strip()])
    return GitSnapshot(available=True, commit=head.stdout.strip(),
                       branch=branch.stdout.strip() or None, dirty_files=dirty)


def initialize(root: Path, *, create_root: bool = False, seed_state: CurrentState | None = None,
               now: datetime | None = None) -> InitReport:
    resolved = validate_workspace_root(root, must_exist=not create_root)
    root_created = False
    if not resolved.is_dir():
        if not resolved.parent.is_dir():
            raise WorkspaceNotFound(
                f"{resolved.parent} does not exist; mount Google Drive before creating the root")
        resolved.mkdir()
        root_created = True
    workspace = Workspace(resolved)
    created_directories = ensure_directories(workspace)
    identity, identity_created = ensure_identity(workspace, now=now)
    manifest_created = not workspace.manifest_path.is_file()
    with manifest_connection(workspace) as connection:
        quick_check(connection)
    state = read_current_state(workspace)
    state_created = False
    if state is None and seed_state is not None:
        state = write_current_state(workspace, seed_state)
        state_created = True
    return InitReport(workspace=workspace, root_created=root_created,
                      created_directories=created_directories, identity=identity,
                      identity_created=identity_created, manifest_created=manifest_created,
                      state=state, state_created=state_created)


def _check(name: str, ok: bool, detail: str) -> Check:
    return Check(name=name, ok=ok, detail=detail)


def _probe_write(workspace: Workspace) -> Check:
    probe = workspace.state_dir / PROBE_NAME
    try:
        with safe_write(probe) as handle:
            handle.partial_path.write_text("probe\n", encoding="utf-8")
    except (OSError, WorkspaceError) as error:
        return _check("write_permission", False, f"cannot write into state/: {error}")
    finally:
        probe.unlink(missing_ok=True)
        probe.with_name(f"{probe.name}.partial").unlink(missing_ok=True)
    return _check("write_permission", True, "partial write and atomic replace succeeded")


def diagnose(workspace: Workspace, *, repo_root: Path = PROJECT_ROOT,
             now: datetime | None = None) -> Diagnosis:
    diagnosis = Diagnosis(workspace=workspace)
    add = diagnosis.checks.append

    root_ok = workspace.root.is_dir()
    add(_check("workspace_root", root_ok,
               str(workspace.root) if root_ok else f"{workspace.root} is not a directory"))
    if not root_ok:
        return diagnosis

    missing = workspace.missing_directories()
    add(_check("required_directories", not missing,
               "all present" if not missing
               else "missing: " + ", ".join(workspace.relative(path) for path in missing)))
    add(_probe_write(workspace))

    try:
        identity = read_identity(workspace)
        add(_check("workspace_identity", identity is not None,
                   f"schema {identity.workspace_schema_version} created {identity.created_at}"
                   if identity else "state/workspace.json is absent; run init"))
    except WorkspaceError as error:
        add(_check("workspace_identity", False, str(error)))

    state: CurrentState | None = None
    try:
        state = read_current_state(workspace)
        add(_check("current_state", state is not None,
                   f"{state.stage} {state.status}" if state
                   else "state/CURRENT_STATE.json is absent; run init"))
    except WorkspaceError as error:
        add(_check("current_state", False, str(error)))

    if workspace.manifest_path.is_file():
        try:
            with manifest_connection(workspace, create=False) as connection:
                integrity = quick_check(connection)
                counts = counts_by_status(connection)
            add(_check("manifest", integrity == "ok",
                       f"quick_check={integrity} schema={MANIFEST_SCHEMA_VERSION} "
                       + " ".join(f"{key}={value}" for key, value in counts.items())))
        except (WorkspaceError, sqlite3.Error) as error:
            add(_check("manifest", False, str(error)))
    else:
        add(_check("manifest", False, "state/collector_manifest.sqlite3 is absent; run init"))

    try:
        lock = read_lock(workspace)
        if lock is None:
            add(_check("writer_lock", True, "free"))
        elif lock.is_stale(now):
            add(_check("writer_lock", False,
                       f"STALE owner={lock.owner_id} purpose={lock.purpose} "
                       f"expired_at={lock.expires_at().isoformat()}"))
        else:
            add(_check("writer_lock", True,
                       f"HELD owner={lock.owner_id} purpose={lock.purpose} "
                       f"acquired_at={lock.acquired_at}"))
    except WorkspaceError as error:
        add(_check("writer_lock", False, str(error)))

    add(_git_check(state, repo_root))
    return diagnosis


def _git_check(state: CurrentState | None, repo_root: Path) -> Check:
    snapshot = git_snapshot(repo_root)
    if not snapshot.available:
        return _check("git", False, f"git unavailable: {snapshot.detail}")
    parts = [f"branch={snapshot.branch}", f"head={snapshot.commit}",
             f"dirty_files={snapshot.dirty_files}"]
    if state is None:
        parts.append("state_source_commit=none")
    elif not state.committed:
        parts.append("state_source_commit=UNCOMMITTED")
    elif state.source_commit == snapshot.commit:
        parts.append("state_source_commit=matches_head")
    else:
        parts.append(f"state_source_commit={state.source_commit} differs_from_head")
    # Informational: a dirty tree or a differing commit is normal mid-stage.
    return _check("git", True, " ".join(parts))


def describe(checks: Sequence[Check]) -> str:
    return "\n".join(f"{check.name}={'OK' if check.ok else 'FAIL'} {check.detail}"
                     for check in checks)
