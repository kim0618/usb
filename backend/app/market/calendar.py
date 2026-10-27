"""NYSE regular-session calendar utilities.

Sessions are read from ``data/xnys_sessions.csv``, which ``app.dev.generate_xnys_sessions`` copies
offline from ``exchange_calendars``. The backend therefore answers calendar questions without
importing that library or pandas. A date outside the table raises ``CalendarRangeError`` rather
than being guessed, the same way the library refuses a date outside its bounds. A table that
disagrees with its meta.json (sha256, row count, first/last session, range, order, row fields)
raises ``CalendarTableError`` on load, which happens while the backend imports.
"""

from array import array
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
import hashlib
import json
import logging
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

from app.core.config import get_settings

DATA_DIR = Path(__file__).with_name("data")
TABLE_PATH = DATA_DIR / "xnys_sessions.csv"
META_PATH = DATA_DIR / "xnys_sessions.meta.json"
TABLE_HEADER = "date,open_utc,close_utc,early_close"
GENERATOR_VERSION = "xnys-sessions-v1"  # app.dev.generate_xnys_sessions.GENERATOR_VERSION
META_KEYS = frozenset({"generator_version", "calendar", "range_start", "range_end", "first_session",
                       "last_session", "row_count", "sha256"})
#: Startup logs a warning once the table ends within this many days; regenerate with a later range.
EXPIRY_WARNING_DAYS = 365

logger = logging.getLogger(__name__)


class CalendarRangeError(ValueError):
    """The requested date or session lies outside the generated XNYS table."""


class CalendarTableError(RuntimeError):
    """The XNYS table or its meta.json is corrupt or inconsistent; the backend refuses to start."""


@dataclass(frozen=True)
class TradingSessionWindow:
    session_date: date
    market_open: datetime
    market_close: datetime
    is_early_close: bool


@dataclass(frozen=True)
class _SessionTable:
    range_start: int  # ordinals of the generated range, inclusive
    range_end: int
    days: array  # session ordinals, ascending
    opens: array  # epoch seconds, UTC
    closes: array
    early: bytes


_table: _SessionTable | None = None
_table_lock = Lock()


def _weekdays_between(first: int, last: int) -> int:
    """Weekdays strictly after ordinal ``first`` and up to and including ordinal ``last``."""
    return sum(1 for o in range(first + 1, last + 1) if date.fromordinal(o).weekday() < 5)


def _load_table() -> _SessionTable:
    # The sha256 covers the CSV only; the meta fields are checked against the rows below, so a
    # hand-edited meta.json (a stretched range_end above all) fails here instead of answering "closed".
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    missing = META_KEYS - meta.keys()
    if missing or meta["calendar"] != "XNYS" or meta["generator_version"] != GENERATOR_VERSION:
        raise CalendarTableError("XNYS session meta is not a %s XNYS table: missing %s, calendar %r, generator %r"
                                 % (GENERATOR_VERSION, sorted(missing), meta.get("calendar"),
                                    meta.get("generator_version")))
    body = TABLE_PATH.read_bytes()
    if hashlib.sha256(body).hexdigest() != meta["sha256"]:
        raise CalendarTableError("XNYS session table does not match its recorded sha256")
    lines = body.decode("ascii").splitlines()
    if not lines or lines[0] != TABLE_HEADER:
        raise CalendarTableError("XNYS session table header changed: %r" % (lines[:1],))
    days, opens, closes, early = array("l"), array("q"), array("q"), bytearray()
    for number, line in enumerate(lines[1:], start=2):
        try:
            day, opened, closed, flag = line.split(",")
            row = date.fromisoformat(day).toordinal(), int(opened), int(closed), int(flag)
        except ValueError as exc:
            raise CalendarTableError("XNYS session table line %d is malformed: %r" % (number, line)) from exc
        if row[1] >= row[2] or row[3] not in (0, 1):
            raise CalendarTableError("XNYS session table line %d breaks the row contract: %r" % (number, line))
        if days and row[0] <= days[-1]:
            raise CalendarTableError("XNYS session table line %d %s session %s"
                                     % (number, "repeats" if row[0] == days[-1] else "goes back to", day))
        days.append(row[0])
        opens.append(row[1])
        closes.append(row[2])
        early.append(row[3])
    if not days or len(days) != meta["row_count"]:
        raise CalendarTableError("XNYS session table has %d rows, meta records %s"
                                 % (len(days), meta["row_count"]))
    start, end = (date.fromisoformat(meta[key]).toordinal() for key in ("range_start", "range_end"))
    edges = tuple(date.fromisoformat(meta[key]).toordinal() for key in ("first_session", "last_session"))
    if edges != (days[0], days[-1]):
        raise CalendarTableError("XNYS session meta first/last session do not match the table rows")
    # range_start/range_end are the dates handed to the generator, and it copies every session in
    # between, so the weekdays between a range edge and the nearest session are closures. A gap
    # longer than any closure inside the table means the meta range claims days the table never saw.
    longest_closure = max((_weekdays_between(a, b) - 1 for a, b in zip(days, days[1:])), default=0)
    if not start <= days[0] <= days[-1] <= end or max(
            _weekdays_between(start - 1, days[0] - 1), _weekdays_between(days[-1], end)) > longest_closure:
        raise CalendarTableError("XNYS session meta range %s..%s is not covered by the table rows"
                                 % (meta["range_start"], meta["range_end"]))
    return _SessionTable(start, end, days, opens, closes, bytes(early))


def session_table() -> _SessionTable:
    global _table
    if _table is None:
        with _table_lock:
            if _table is None:
                _table = _load_table()
                warn_if_expiring(_table, date.today())
    return _table


def warn_if_expiring(table: _SessionTable, today: date) -> bool:
    days_left = table.range_end - today.toordinal()
    if days_left > EXPIRY_WARNING_DAYS:
        return False
    logger.warning("XNYS session table ends %s (%d days left); dates after it raise CalendarRangeError. "
                   "Regenerate with app.dev.generate_xnys_sessions.", date.fromordinal(table.range_end), days_left)
    return True


def _ordinal(day: date) -> int:
    # The library accepted a date or a naive midnight datetime and refused anything else.
    if isinstance(day, datetime) and (day.tzinfo is not None or day.time() != time(0)):
        raise ValueError("calendar dates must be dates or naive midnight datetimes, got %r" % (day,))
    return day.toordinal()


class MarketCalendar:
    """Timezone-aware NYSE calendar backed by official exchange rules."""

    def __init__(self, timezone_name: str | None = None) -> None:
        self._sessions = session_table()
        self.timezone = ZoneInfo(timezone_name or get_settings().market_timezone)

    def _index(self, day: date) -> int | None:
        ordinal = _ordinal(day)
        table = self._sessions
        if not table.range_start <= ordinal <= table.range_end:
            raise CalendarRangeError("%s is outside the XNYS session table" % (day,))
        index = bisect_left(table.days, ordinal)
        return index if index < len(table.days) and table.days[index] == ordinal else None

    def _session_date(self, index: int) -> date:
        if not 0 <= index < len(self._sessions.days):
            raise CalendarRangeError("requested session is outside the XNYS session table")
        return date.fromordinal(self._sessions.days[index])

    def is_trading_day(self, day: date) -> bool:
        return self._index(day) is not None

    def is_holiday(self, day: date) -> bool:
        return day.weekday() < 5 and not self.is_trading_day(day)

    def session(self, day: date) -> TradingSessionWindow | None:
        index = self._index(day)
        if index is None:
            return None
        table = self._sessions
        return TradingSessionWindow(
            session_date=day,
            market_open=datetime.fromtimestamp(table.opens[index], timezone.utc).astimezone(self.timezone),
            market_close=datetime.fromtimestamp(table.closes[index], timezone.utc).astimezone(self.timezone),
            is_early_close=bool(table.early[index]),
        )

    def regular_market_open(self, day: date) -> datetime | None:
        session = self.session(day)
        return session.market_open if session else None

    def regular_market_close(self, day: date) -> datetime | None:
        session = self.session(day)
        return session.market_close if session else None

    def is_early_close(self, day: date) -> bool:
        session = self.session(day)
        return bool(session and session.is_early_close)

    def next_trading_day(self, day: date) -> date:
        """Return the next XNYS session, skipping weekends and holidays."""
        self._index(day)
        index = bisect_left(self._sessions.days, _ordinal(day))
        # Compared as objects, as before: a midnight datetime never equals its date, so it is kept.
        if index < len(self._sessions.days) and self._session_date(index) == day:
            index += 1
        return self._session_date(index)

    def previous_trading_day(self, day: date) -> date:
        """Return the XNYS session immediately before ``day``."""
        self._index(day)
        index = bisect_right(self._sessions.days, _ordinal(day)) - 1
        if index >= 0 and self._session_date(index) == day:
            index -= 1
        return self._session_date(index)

    def holding_day_number(self, entry_day: date, current_day: date) -> int:
        if current_day < entry_day or not self.is_trading_day(entry_day) or not self.is_trading_day(current_day):
            raise ValueError("holding dates must be ordered XNYS sessions")
        return bisect_right(self._sessions.days, _ordinal(current_day)) - bisect_left(
            self._sessions.days, _ordinal(entry_day))
