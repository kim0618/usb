"""Timezone conversion helpers using aware datetimes only."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.exceptions import DataError


UTC = ZoneInfo("UTC")
KST = ZoneInfo("Asia/Seoul")


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DataError("A timezone-aware datetime is required")
    return value


def convert_timezone(value: datetime, timezone_name: str) -> datetime:
    """Convert an aware datetime to an IANA timezone."""

    return require_aware(value).astimezone(ZoneInfo(timezone_name))

