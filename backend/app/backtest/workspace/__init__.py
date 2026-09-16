"""Shared Google Drive workspace foundation for the historical backtest work.

Code, tests, and docs live in git. Market data, backtest runs, snapshots, and shared
state live in the Google Drive folder ``1_US-B``. Secrets live only in each PC's own
ignored ``.env`` and never enter either of the other two.
"""

from app.backtest.workspace.discovery import (
    WORKSPACE_DIR_NAME, discover_workspace_roots, resolve_workspace_root, validate_workspace_root,
)
from app.backtest.workspace.errors import (
    ManifestError, SafeWriteError, SecretLikeValue, StateInvalid, WorkspaceAmbiguous,
    WorkspaceError, WorkspaceInsideRepo, WorkspaceNotFound, WorkspaceSchemaIncompatible,
    WriterLockHeld, WriterLockStale,
)
from app.backtest.workspace.layout import Workspace, ensure_directories
from app.backtest.workspace.operations import Diagnosis, InitReport, diagnose, initialize
from app.backtest.workspace.safe_write import safe_write
from app.backtest.workspace.state import (
    UNCOMMITTED, CurrentState, read_current_state, update_current_state, write_current_state,
)


__all__ = [
    "UNCOMMITTED", "CurrentState", "Diagnosis", "InitReport", "ManifestError", "SafeWriteError",
    "SecretLikeValue", "StateInvalid", "WORKSPACE_DIR_NAME", "Workspace", "WorkspaceAmbiguous",
    "WorkspaceError", "WorkspaceInsideRepo", "WorkspaceNotFound", "WorkspaceSchemaIncompatible",
    "WriterLockHeld", "WriterLockStale", "diagnose", "discover_workspace_roots",
    "ensure_directories", "initialize", "read_current_state", "resolve_workspace_root",
    "safe_write", "update_current_state", "validate_workspace_root", "write_current_state",
]
