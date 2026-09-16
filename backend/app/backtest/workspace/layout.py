"""Directory layout of the shared workspace and the handle every other module takes."""

from dataclasses import dataclass
from pathlib import Path


WORKSPACE_DIRECTORIES = (
    "market_data/raw/massive",
    "market_data/normalized/minute",
    "market_data/normalized/daily",
    "market_data/metadata",
    "backtest/runs",
    "backtest/results",
    "backtest/reports",
    "snapshots/paper_trading",
    "state",
    "logs",
)
IDENTITY_FILENAME = "state/workspace.json"
CURRENT_STATE_FILENAME = "state/CURRENT_STATE.json"
MANIFEST_FILENAME = "state/collector_manifest.sqlite3"
WRITER_LOCK_FILENAME = "state/writer.lock.json"


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def identity_path(self) -> Path:
        return self.root / IDENTITY_FILENAME

    @property
    def current_state_path(self) -> Path:
        return self.root / CURRENT_STATE_FILENAME

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_FILENAME

    @property
    def writer_lock_path(self) -> Path:
        return self.root / WRITER_LOCK_FILENAME

    def required_directories(self) -> tuple[Path, ...]:
        return tuple(self.root / relative for relative in WORKSPACE_DIRECTORIES)

    def missing_directories(self) -> tuple[Path, ...]:
        return tuple(path for path in self.required_directories() if not path.is_dir())

    def relative(self, path: Path) -> str:
        return str(Path(path).resolve().relative_to(self.root))


def ensure_directories(workspace: Workspace) -> tuple[Path, ...]:
    """Create any missing directory. Existing directories and their contents are untouched."""
    created = []
    for path in workspace.required_directories():
        if not path.is_dir():
            created.append(path)
        path.mkdir(parents=True, exist_ok=True)
    return tuple(created)
