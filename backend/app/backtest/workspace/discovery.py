"""Locate the shared Google Drive workspace root without hardcoding a drive letter.

The same folder is mounted at a different path on every PC (``/mnt/g`` at home,
``/mnt/h`` at the office), so the root is found by name under the WSL mount bases and
never guessed. Exactly one hit is required: zero and two are both errors, because
silently picking the first would make two machines write to different roots. Nothing
here creates a directory, and a candidate inside the git repository is never accepted.
"""

from collections.abc import Iterable, Iterator
import os
from pathlib import Path

from app.backtest.workspace.errors import (
    WorkspaceAmbiguous, WorkspaceInsideRepo, WorkspaceNameMismatch, WorkspaceNotFound,
)
from app.core.config import PROJECT_ROOT


WORKSPACE_DIR_NAME = "1_US-B"
# Google Drive for desktop names the personal root per UI language.
DRIVE_ROOT_NAMES = ("내 드라이브", "My Drive")
DEFAULT_MOUNT_BASES = (Path("/mnt"),)
# WSL internal mounts: never a Windows drive, and iterating them is pointless.
SKIPPED_MOUNTS = frozenset({"wsl", "wslg"})


def _candidate_paths(mount: Path) -> Iterator[Path]:
    """Fixed-depth candidates only: a recursive scan of a synced drive is far too slow."""
    yield mount / WORKSPACE_DIR_NAME
    for drive_root in DRIVE_ROOT_NAMES:
        yield mount / drive_root / WORKSPACE_DIR_NAME


def _iter_mounts(base: Path) -> Iterator[Path]:
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name.startswith(".") or entry.name in SKIPPED_MOUNTS:
            continue
        try:
            if entry.is_dir():
                yield entry
        except OSError:
            continue


def _is_directory(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def inside_repo(path: Path) -> bool:
    return path == PROJECT_ROOT or path.is_relative_to(PROJECT_ROOT)


def discover_workspace_roots(mount_bases: Iterable[Path] = DEFAULT_MOUNT_BASES) -> tuple[Path, ...]:
    """Every distinct ``1_US-B`` directory reachable under the given mount bases."""
    found: dict[str, Path] = {}
    for base in mount_bases:
        for mount in _iter_mounts(Path(base)):
            for candidate in _candidate_paths(mount):
                if not _is_directory(candidate):
                    continue
                resolved = Path(os.path.realpath(candidate))
                if inside_repo(resolved):
                    continue
                found.setdefault(str(resolved), resolved)
    return tuple(found[key] for key in sorted(found))


def validate_workspace_root(path: Path, *, must_exist: bool = True) -> Path:
    """Check an explicitly supplied root before anything is written into it."""
    resolved = Path(path).expanduser().resolve()
    if resolved.name != WORKSPACE_DIR_NAME:
        raise WorkspaceNameMismatch(f"{resolved} is not named {WORKSPACE_DIR_NAME}")
    if inside_repo(resolved):
        raise WorkspaceInsideRepo(f"{resolved} is inside the git repository {PROJECT_ROOT}")
    if must_exist and not _is_directory(resolved):
        raise WorkspaceNotFound(f"{resolved} does not exist")
    return resolved


def resolve_workspace_root(explicit: Path | None = None, *,
                           mount_bases: Iterable[Path] = DEFAULT_MOUNT_BASES,
                           must_exist: bool = True) -> Path:
    if explicit is not None:
        return validate_workspace_root(explicit, must_exist=must_exist)
    roots = discover_workspace_roots(mount_bases)
    if not roots:
        searched = ", ".join(str(Path(base)) for base in mount_bases)
        raise WorkspaceNotFound(
            f"no {WORKSPACE_DIR_NAME} directory under {searched}; mount Google Drive or pass "
            "--workspace-root")
    if len(roots) > 1:
        listed = ", ".join(str(root) for root in roots)
        raise WorkspaceAmbiguous(f"{len(roots)} candidates found ({listed}); pass --workspace-root")
    return roots[0]
