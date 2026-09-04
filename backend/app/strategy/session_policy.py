"""XNYS-aware execution-session policy for the V1 strategy lifecycle."""

from dataclasses import dataclass
from datetime import datetime

from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession
from app.market.domain import MinuteBar


@dataclass(frozen=True)
class SessionPermissions:
    market_data: bool
    new_entry: bool
    pyramid: bool
    position_monitoring: bool
    overnight_review: bool


OBSERVE_ONLY = SessionPermissions(True, False, False, True, False)
REGULAR_EXECUTION = SessionPermissions(True, True, True, True, True)
POSTMARKET_MANAGEMENT = SessionPermissions(True, False, False, True, True)
CLOSED = SessionPermissions(False, False, False, False, False)


class SessionPolicy:
    """Resolve permissions from the exchange calendar, including early closes."""

    def __init__(self, calendar: MarketCalendar | None = None) -> None:
        self.calendar = calendar or MarketCalendar()

    def session_at(self, as_of: datetime) -> MarketSession | None:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        local = as_of.astimezone(self.calendar.timezone)
        window = self.calendar.session(local.date())
        if window is None:
            return None
        if local < window.market_open:
            return MarketSession.PREMARKET
        if local <= window.market_close:
            return MarketSession.REGULAR
        return MarketSession.POSTMARKET

    def permissions_at(self, as_of: datetime) -> SessionPermissions:
        session = self.session_at(as_of)
        return {
            MarketSession.PREMARKET: OBSERVE_ONLY,
            MarketSession.REGULAR: REGULAR_EXECUTION,
            MarketSession.POSTMARKET: POSTMARKET_MANAGEMENT,
            None: CLOSED,
        }[session]

    def regular_fill_bars(self, bars: tuple[MinuteBar, ...], *, as_of: datetime) -> tuple[MinuteBar, ...]:
        """Keep fills inside the signal's regular XNYS session."""
        local = as_of.astimezone(self.calendar.timezone)
        window = self.calendar.session(local.date())
        if window is None:
            return ()
        return tuple(
            bar for bar in bars
            if bar.session is MarketSession.REGULAR
            and window.market_open <= bar.timestamp.astimezone(self.calendar.timezone) <= window.market_close
        )
