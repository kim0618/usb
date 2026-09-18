"""Stable error codes for the Massive historical collector.

Every refusal carries a machine-readable code, so the CLI, the tests, and the manifest
all name the same condition the same way. A collector failure never leaves a COMPLETE
manifest row behind and never replaces a file that was already COMPLETE.
"""


class CollectorError(Exception):
    code = "COLLECTOR_ERROR"

    def __init__(self, reason: str) -> None:
        super().__init__(f"{self.code}: {reason}")
        self.reason = reason


class ProductionEnvironment(CollectorError):
    """The process is not a local development checkout; collection is refused."""

    code = "COLLECTOR_PRODUCTION_ENVIRONMENT"


class SameDayRequest(CollectorError):
    """The effective end reaches a session that has not been published yet (T or later)."""

    code = "COLLECTOR_SAME_DAY_REQUEST"


class EmptyRange(CollectorError):
    code = "COLLECTOR_EMPTY_RANGE"


class RangeTooLarge(CollectorError):
    """The range would need more pages than the client's hard ceiling allows."""

    code = "COLLECTOR_RANGE_TOO_LARGE"


class IncompleteRange(CollectorError):
    """HTTP 200 on every page, but the last expected XNYS session is not in the data."""

    code = "COLLECTOR_INCOMPLETE_RANGE"


class SessionsMissing(CollectorError):
    code = "COLLECTOR_SESSIONS_MISSING"


class RegularMinutesMissing(CollectorError):
    code = "COLLECTOR_REGULAR_MINUTES_MISSING"


class DataQualityFailed(CollectorError):
    code = "COLLECTOR_DATA_QUALITY_FAILED"


class PrecisionLoss(CollectorError):
    """A provider number does not survive the storage type; nothing is rounded silently."""

    code = "COLLECTOR_PRECISION_LOSS"


class PartitionConflict(CollectorError):
    """A finished file of another manifest entry sits where this collection would write."""

    code = "COLLECTOR_PARTITION_CONFLICT"


class StorageError(CollectorError):
    code = "COLLECTOR_STORAGE_ERROR"


class WarmupTooShort(CollectorError):
    """Fewer prior sessions were asked for than the scanner's D-20..D window needs."""

    code = "COLLECTOR_WARMUP_TOO_SHORT"


class DailyMissingData(CollectorError):
    """Expected daily sessions are absent and no listing declaration explains them.

    Deliberately not PRE_LISTING. The provider's answer looks identical either way, so a
    short history is only ever a listing fact when the universe file says so.
    """

    code = "COLLECTOR_DAILY_MISSING_DATA"


class DailyDataQualityFailed(CollectorError):
    """A daily row is corrupt: duplicate, out of order, null, OHLC-invalid, or off-date."""

    code = "COLLECTOR_DAILY_DATA_QUALITY_FAILED"


class DailyFileConflict(CollectorError):
    """A finished daily file of another manifest entry sits where this run would write."""

    code = "COLLECTOR_DAILY_FILE_CONFLICT"
