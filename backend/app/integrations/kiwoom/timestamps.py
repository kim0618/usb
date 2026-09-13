"""Canonical Kiwoom US-chart timestamp parsing.

Kiwoom documents ``cntr_tm`` as ``YYYYMMDDHHmmss`` but its US chart feed can
use hours greater than 23.  Live rows show that this is an ET business-day
timeline: 24:xx is the following ET calendar day while retaining the business
date prefix.  Normalize the wall-clock value before attaching the ET zone so
DST is resolved for the resulting calendar instant.  Overnight trading ends at
04:00 ET, so hours above 27 would overlap the next business date and are
rejected rather than wrapped.
"""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
MAX_BUSINESS_HOUR = 27  # 27:59 business-relative = 03:59 ET next calendar day


def minute_timestamp(row: dict[str, Any]) -> datetime:
    business_date = str(row["bus_dt"]).strip()
    raw = str(row["cntr_tm"]).strip().replace(":", "")
    if len(raw) == 14:
        if raw[:8] != business_date:
            raise ValueError("Kiwoom cntr_tm date must match bus_dt")
        clock = raw[8:]
    elif len(raw) == 6:  # retained for older captured/test payloads
        clock = raw
    else:
        raise ValueError("Kiwoom cntr_tm must be YYYYMMDDHHmmss")
    if not business_date.isdigit() or not clock.isdigit():
        raise ValueError("Kiwoom minute timestamp must be numeric")
    hour, minute, second = int(clock[:2]), int(clock[2:4]), int(clock[4:6])
    if hour > MAX_BUSINESS_HOUR or minute > 59 or second > 59:
        raise ValueError("Kiwoom minute timestamp is out of range")
    base = datetime.strptime(business_date, "%Y%m%d")
    local = base + timedelta(hours=hour, minutes=minute, seconds=second)
    return local.replace(tzinfo=ET)
