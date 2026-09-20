"""Where forward data lives, and the rules about what may be written where.

The frozen historical snapshots (``USB-HIST-V1``, ``USB-HIST-V2``) are never touched. Forward
market data goes to its own append-only tree beside them, so a reader can always tell which tape
an observation came from, and the research artifacts live under ``data/runtime`` like every other
study in this project.
"""

from datetime import date
from pathlib import Path

#: The first session that may enter a forward holdout. The last historical session is 2026-09-16.
FORWARD_HOLDOUT_START = date(2026, 9, 17)
#: The historical snapshot the development and confirmation blocks were bound to.
HISTORICAL_SNAPSHOT_ID = "USB-HIST-V1"
HISTORICAL_LAST_SESSION = date(2026, 9, 16)

#: Drive-side append-only market data, kept apart from the frozen snapshot tree.
FORWARD_MARKET_DATA = "market_data/forward/massive"
GROUPED_DAILY = f"{FORWARD_MARKET_DATA}/grouped_daily"
REFERENCE = f"{FORWARD_MARKET_DATA}/reference"
SPLITS = f"{FORWARD_MARKET_DATA}/splits"
MINUTE = f"{FORWARD_MARKET_DATA}/minute"

#: Repo-side research artifacts.
RUNTIME_ROOT = Path("data/runtime/strategy_e_candidate/forward")
REGISTRY_DIR = RUNTIME_ROOT / "registry"
SEALS_DIR = RUNTIME_ROOT / "decision_seals"
LABELS_DIR = RUNTIME_ROOT / "labels"
CHECKPOINTS_DIR = RUNTIME_ROOT / "checkpoints"


class ForwardViolation(RuntimeError):
    """An operation that would break the forward contract; it is refused, not repaired."""


def require_forward_session(session: date) -> date:
    """The single gate every forward path goes through."""
    if session < FORWARD_HOLDOUT_START:
        raise ForwardViolation(
            f"{session.isoformat()} is on or before the historical boundary "
            f"{HISTORICAL_LAST_SESSION.isoformat()}; a forward holdout observation must be dated "
            f"{FORWARD_HOLDOUT_START.isoformat()} or later. Old data is never promoted to a holdout.")
    return session


def grouped_daily_path(workspace_root: Path, session: date) -> Path:
    return workspace_root / GROUPED_DAILY / f"{session.year}" / f"{session.isoformat()}.json.gz"


def reference_path(workspace_root: Path, as_of: date) -> Path:
    return workspace_root / REFERENCE / f"CS_{as_of.isoformat()}.json.gz"


def splits_path(workspace_root: Path, session: date) -> Path:
    return workspace_root / SPLITS / f"splits_asof_{session.isoformat()}.json.gz"


def minute_path(workspace_root: Path, symbol: str, session: date) -> Path:
    return workspace_root / MINUTE / symbol / f"{symbol}_{session.isoformat()}.json.gz"


def seal_path(repo_root: Path, session: date) -> Path:
    return repo_root / SEALS_DIR / f"{session.isoformat()}.json"


def label_path(repo_root: Path, session: date) -> Path:
    return repo_root / LABELS_DIR / f"{session.isoformat()}.json"


def registry_path(repo_root: Path) -> Path:
    return repo_root / REGISTRY_DIR / "observations.jsonl"


def checkpoint_path(repo_root: Path, rows: int) -> Path:
    return repo_root / CHECKPOINTS_DIR / f"checkpoint_{rows}.json"
