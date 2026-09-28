from __future__ import annotations

from datetime import datetime, timezone


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)
