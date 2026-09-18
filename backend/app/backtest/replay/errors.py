"""Stable error codes for the Historical Replay Core.

Every refusal carries a machine-readable code, so the CLI, the tests, and any future
backtester name the same condition the same way. Replay fails closed: a dataset it
cannot prove, a bar it cannot represent, and a point-in-time violation all stop the
run rather than producing a result nobody can trust.
"""


class ReplayError(Exception):
    code = "REPLAY_ERROR"

    def __init__(self, reason: str) -> None:
        super().__init__(f"{self.code}: {reason}")
        self.reason = reason


class DatasetNotFound(ReplayError):
    """No COMPLETE manifest entry for this provider/symbol/timeframe."""

    code = "REPLAY_DATASET_NOT_FOUND"


class DatasetAmbiguous(ReplayError):
    """More than one COMPLETE entry claims this symbol; replay never picks one."""

    code = "REPLAY_DATASET_AMBIGUOUS"


class DatasetIncomplete(ReplayError):
    """The manifest entry exists but is not COMPLETE, or was written by another version."""

    code = "REPLAY_DATASET_INCOMPLETE"


class DatasetChecksumMismatch(ReplayError):
    """A file the manifest names no longer hashes to the checksum it recorded."""

    code = "REPLAY_DATASET_CHECKSUM_MISMATCH"


class DatasetSchemaMismatch(ReplayError):
    """A Parquet file does not carry the dataset schema the collector recorded."""

    code = "REPLAY_DATASET_SCHEMA_MISMATCH"


class DateOutsideDataset(ReplayError):
    code = "REPLAY_DATE_OUTSIDE_DATASET"


class SymbolMismatch(ReplayError):
    code = "REPLAY_SYMBOL_MISMATCH"


class NotATradingSession(ReplayError):
    code = "REPLAY_NOT_A_TRADING_SESSION"


class SessionDataMissing(ReplayError):
    """The dataset range covers the date but holds no row for it."""

    code = "REPLAY_SESSION_DATA_MISSING"


class BarRowInvalid(ReplayError):
    """A stored row cannot become a market-domain bar (null price, OHLC violation)."""

    code = "REPLAY_BAR_ROW_INVALID"


class SessionLabelMismatch(ReplayError):
    """The stored session part disagrees with the XNYS calendar, which is authority."""

    code = "REPLAY_SESSION_LABEL_MISMATCH"


class PointInTimeViolation(ReplayError):
    """A bar reached the strategy that was not complete at the replay clock's time."""

    code = "REPLAY_POINT_IN_TIME_VIOLATION"


class ReplayClockError(ReplayError):
    code = "REPLAY_CLOCK_ERROR"


class ProductionDatabaseRefused(ReplayError):
    """Replay was pointed at a database a live or paper runtime owns."""

    code = "REPLAY_PRODUCTION_DATABASE_REFUSED"
