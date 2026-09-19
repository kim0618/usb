"""When a filing becomes usable, on the XNYS session grid, in America/New_York.

`effective_time` follows `c_e0_rules_v1.json > pit.effective_time`: inside a session it is the
acceptance time, before the open it is that session's open, at/after the close or on a non-session
day it is the next session's open. So an effective_time always lies in [open(S), close(S)) of its
own `event_session`, which is what lets the candidate join compare session indices instead of
timestamps - the audit still asserts the timestamp form.

The zone of the submissions-JSON `acceptanceDateTime` is **not** assumed: it is passed in after the
timezone audit has decided it (`ET` or `UTC`), and the choice is recorded in the run manifest.
"""

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.market.calendar import MarketCalendar

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
ACCEPTANCE_ZONES = {"ET": ET, "UTC": UTC}
DURING_SESSION = "DURING_SESSION"
BEFORE_OPEN = "BEFORE_OPEN"
AFTER_CLOSE_OR_NON_SESSION = "AFTER_CLOSE_OR_NON_SESSION"


class AcceptanceZoneUndecided(RuntimeError):
    """The timezone audit has not decided how to read `acceptanceDateTime`."""


@dataclass(frozen=True)
class SessionGrid:
    sessions: tuple[date, ...]
    opens: tuple[datetime, ...]
    closes: tuple[datetime, ...]

    @property
    def index(self) -> dict[date, int]:
        return {day: i for i, day in enumerate(self.sessions)}

    def open_of(self, i: int) -> datetime:
        return self.opens[i]

    def close_of(self, i: int) -> datetime:
        return self.closes[i]


@dataclass(frozen=True)
class Placement:
    """Where one acceptance time lands on the grid."""

    effective_time: datetime
    session_index: int
    rule: str

    @property
    def session_date(self) -> date:
        return self.effective_time.date()


def build_grid(sessions: Sequence[date], calendar: MarketCalendar | None = None) -> SessionGrid:
    calendar = calendar or MarketCalendar("America/New_York")
    opens, closes = [], []
    for day in sessions:
        window = calendar.session(day)
        if window is None:
            raise ValueError(f"{day} is not an XNYS session")
        opens.append(window.market_open.astimezone(ET))
        closes.append(window.market_close.astimezone(ET))
    return SessionGrid(tuple(sessions), tuple(opens), tuple(closes))


def parse_acceptance(value: str, zone: str) -> datetime:
    """Read one `acceptanceDateTime` string under the audited zone interpretation."""
    if zone not in ACCEPTANCE_ZONES:
        raise AcceptanceZoneUndecided(f"acceptance zone {zone!r} is not one of {sorted(ACCEPTANCE_ZONES)}")
    text = value.strip().replace("Z", "").replace("z", "")
    if text.endswith(("-04:00", "-05:00", "+00:00")):  # an explicit offset is taken as filed
        return datetime.fromisoformat(text).astimezone(ET)
    naive = datetime.fromisoformat(text)
    if naive.tzinfo is not None:
        return naive.astimezone(ET)
    return naive.replace(tzinfo=ACCEPTANCE_ZONES[zone]).astimezone(ET)


def place(grid: SessionGrid, acceptance: datetime) -> Placement | None:
    """The session that makes `acceptance` usable, or None when it falls outside the grid.

    Outside means either after the last session's close or before the first session's open: a
    filing older than the grid must not be folded onto session 0, where it would inflate that
    session's counts. No window the study uses reaches session 0, so nothing is lost.
    """
    moment = acceptance.astimezone(ET)
    if moment < grid.opens[0]:
        return None
    i = bisect_right(grid.closes, moment)
    if i >= len(grid.sessions):
        return None
    if moment >= grid.opens[i]:
        return Placement(moment, i, DURING_SESSION)
    rule = BEFORE_OPEN if moment.date() == grid.sessions[i] else AFTER_CLOSE_OR_NON_SESSION
    return Placement(grid.opens[i], i, rule)


def window_sessions(grid: SessionGrid, d_index: int, back: int) -> range:
    """Session indices of [open(D-back), close(D)) - the window form every C-E0 window takes."""
    return range(max(0, d_index - back), d_index + 1)


def calendar_days_before(day: date, days: int) -> date:
    return day - timedelta(days=days)
