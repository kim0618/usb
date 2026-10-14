"""The America/New_York calendar day H's daily jobs are keyed on (queue retries, SEC cache roots)."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def et_day(moment: datetime | None = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(ET).date().isoformat()
