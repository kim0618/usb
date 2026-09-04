"""Canonical Kiwoom timestamp parsing shared by collection and mapping."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")


def minute_timestamp(row: dict[str, Any]) -> datetime:
    raw_time = str(row["cntr_tm"]).strip().replace(":", "")[:6].zfill(6)
    return datetime.strptime(f"{row['bus_dt']}{raw_time}", "%Y%m%d%H%M%S").replace(tzinfo=ET)
