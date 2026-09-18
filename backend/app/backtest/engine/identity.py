"""How a research run names itself, and what the name is a function of.

A run identity is a digest over plain ``key=value`` lines. Two rules decide what goes in:

* **Everything that can change the artifact bytes is in the digest**: strategy id, config
  fingerprint, execution mode, adapter/scanner/engine versions, dataset identities, range,
  and the content digest of the code that computes the result (``code_digest``). Hashing
  the code directly means an edit without a version bump is still a different run, which
  the baseline's version-bump rule cannot guarantee.
* **Nothing that cannot change the bytes is in the digest.** Wall clock, the machine, and
  git commit status stay out: committing the identical tree does not change a result, so it
  must not change the run id. The git state is recorded next to the identity as provenance
  (``source_provenance``), following the workspace rule that ``source_commit`` is a real hash
  only for a clean tree whose HEAD a remote already has, and ``UNCOMMITTED`` otherwise.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess

from app.backtest.research.contract import checksum

UNCOMMITTED = "UNCOMMITTED"


@dataclass(frozen=True)
class RunIdentity:
    run_id: str
    digest: str
    lines: tuple[str, ...]


def run_identity(namespace: str, prefix: str, lines: Sequence[str]) -> RunIdentity:
    for line in lines:
        if "\n" in line or "=" not in line:
            raise ValueError(f"identity line {line!r} must be one key=value line")
    digest = checksum(namespace, "\n".join(lines))
    return RunIdentity(f"{prefix}-{digest[:20]}", digest, tuple(lines))


def code_digest(files: Iterable[Path], *, root: Path) -> str:
    """sha256 over (path relative to ``root``, file sha256), sorted by path."""
    digest = hashlib.sha256()
    for path in sorted(Path(item) for item in files):
        body = path.read_bytes()
        digest.update(f"{path.resolve().relative_to(root.resolve())}\t"
                      f"{hashlib.sha256(body).hexdigest()}\n".encode())
    return digest.hexdigest()


def package_files(*packages: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for package in packages for path in Path(package).glob("*.py")))


def _git(repo: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                   text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def source_provenance(repo: Path, paths: Sequence[Path]) -> dict[str, object]:
    """Read-only git facts about the code that produced a run. Never part of the digest."""
    head = _git(repo, "rev-parse", "HEAD")
    relative = [str(Path(path).resolve().relative_to(repo.resolve())) for path in paths]
    status = _git(repo, "status", "--porcelain", "--", *relative)
    dirty = status is None or bool(status)
    pushed = bool(head and _git(repo, "branch", "-r", "--contains", head))
    return {
        "source_commit": head if head and not dirty and pushed else UNCOMMITTED,
        "git_head": head,
        "code_paths_dirty": dirty,
        "head_on_remote": pushed,
    }
