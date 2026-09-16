"""Stable error codes for the shared backtest workspace.

Every failure carries a machine-readable code so the CLI, the tests, and a future
collector all report the same string for the same condition.
"""


class WorkspaceError(Exception):
    code = "BACKTEST_WORKSPACE_ERROR"

    def __init__(self, reason: str) -> None:
        super().__init__(f"{self.code}: {reason}")
        self.reason = reason


class WorkspaceNotFound(WorkspaceError):
    code = "BACKTEST_WORKSPACE_NOT_FOUND"


class WorkspaceAmbiguous(WorkspaceError):
    code = "BACKTEST_WORKSPACE_AMBIGUOUS"


class WorkspaceInsideRepo(WorkspaceError):
    code = "BACKTEST_WORKSPACE_INSIDE_REPO"


class WorkspaceNameMismatch(WorkspaceError):
    code = "BACKTEST_WORKSPACE_NAME_MISMATCH"


class WorkspaceSchemaIncompatible(WorkspaceError):
    code = "BACKTEST_WORKSPACE_SCHEMA_INCOMPATIBLE"


class StateInvalid(WorkspaceError):
    code = "BACKTEST_STATE_INVALID"


class SecretLikeValue(WorkspaceError):
    code = "BACKTEST_STATE_SECRET_LIKE"


class ManifestError(WorkspaceError):
    code = "BACKTEST_MANIFEST_ERROR"


class ManifestIncomplete(ManifestError):
    code = "BACKTEST_MANIFEST_INCOMPLETE_FILE"


class WriterLockHeld(WorkspaceError):
    code = "BACKTEST_WRITER_LOCK_HELD"


class WriterLockStale(WorkspaceError):
    code = "BACKTEST_WRITER_LOCK_STALE"


class WriterLockNotOwned(WorkspaceError):
    code = "BACKTEST_WRITER_LOCK_NOT_OWNED"


class WriterLockUnreadable(WorkspaceError):
    code = "BACKTEST_WRITER_LOCK_UNREADABLE"


class SafeWriteError(WorkspaceError):
    code = "BACKTEST_SAFE_WRITE_ERROR"
