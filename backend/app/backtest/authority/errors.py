"""Stable error codes for the Historical Authority Layer.

Authority fails closed, exactly as the Replay Core does. A source it cannot prove, a
mode combination it cannot honour, and a store whose bytes moved under it all stop the
run rather than producing a context nobody can trace back to a record.
"""


class AuthorityError(Exception):
    code = "AUTHORITY_ERROR"

    def __init__(self, reason: str) -> None:
        super().__init__(f"{self.code}: {reason}")
        self.reason = reason


class AuthorityModeInvalid(AuthorityError):
    """A mode combination that would make the resolved context unreadable."""

    code = "AUTHORITY_MODE_INVALID"


class AuthoritySourceRefused(AuthorityError):
    """The import was pointed at a database a live, paper, or review process owns."""

    code = "AUTHORITY_SOURCE_REFUSED"


class AuthoritySourceUnreadable(AuthorityError):
    """The source is not a SQLite file, or lacks the tables an authority chain needs."""

    code = "AUTHORITY_SOURCE_UNREADABLE"


class AuthoritySourceMutated(AuthorityError):
    """The source file's bytes changed while it was being read."""

    code = "AUTHORITY_SOURCE_MUTATED"


class AuthorityRecordConflict(AuthorityError):
    """Two sources claim the same symbol and entry session; import never picks one."""

    code = "AUTHORITY_RECORD_CONFLICT"


class AuthorityStoreCorrupt(AuthorityError):
    """The stored records do not hash to what the index recorded for them."""

    code = "AUTHORITY_STORE_CORRUPT"


class AuthorityVersionMismatch(AuthorityError):
    """The store was written by a different authority version than this code speaks."""

    code = "AUTHORITY_VERSION_MISMATCH"


class AuthorityRefused(AuthorityError):
    """A replay was asked to evaluate a symbol/date no authority admits."""

    code = "AUTHORITY_REFUSED"
