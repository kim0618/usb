"""Portable SQLAlchemy value types."""

from datetime import datetime, timezone

from decimal import Decimal

from sqlalchemy.types import String, TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Persist aware datetimes as UTC ISO-8601 text, including on SQLite."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, _dialect: object) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        return value.astimezone(timezone.utc).isoformat()

    def process_result_value(self, value: str | None, _dialect: object) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("Stored UTCDateTime value is naive")
        return parsed.astimezone(timezone.utc)


class DecimalString(TypeDecorator[Decimal]):
    """Persist exact arbitrary-precision Decimal text on SQLite."""

    impl = String(100)
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, _dialect: object) -> str | None:
        return None if value is None else str(value)

    def process_result_value(self, value: str | None, _dialect: object) -> Decimal | None:
        return None if value is None else Decimal(value)
