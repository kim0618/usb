"""A_MOVER_PM_VOLUME_V1: A's own 20-session premarket share-volume baseline.

E's ``rvol_store`` values cannot be reused, and the reason is not preference. E's denominator
is premarket *dollar* volume over ``[04:00, 09:24]``, requires a staged 09:30 bar, and takes a
median once at least five sessions exist. A's relative volume is *share* volume over
``[04:00, 09:15)``, counts a covered session with no prints as a zero, needs the full twenty,
and divides by ``max(median, 1,000 shares)``. Those are different numbers from different
windows; dividing A's numerator by E's denominator would produce a ratio that is not either
contract's.

What *is* reused is the machinery, not a new framework:

* the durable table ``premarket_volume_sessions``, whose identity already includes
  ``source`` and ``collector_version`` - so A's rows live beside the V2 rows without either
  reinterpreting the other, and no migration is needed;
* ``PremarketVolumeHistoryService``'s planning, retry classification, idempotent upsert and
  "a COMPLETE session is never overwritten by a worse answer" rule, through a subclass that
  changes exactly two things: the window ends at A's cut, and the planning window is A's.

**A's membership rule, stated once.** The denominator is the median share volume of the last
twenty *covered* sessions strictly before the entry session. Covered means the source answered
for that session: a session with prints contributes its volume, and a session the source
covered with no premarket print contributes zero - a symbol that normally does not trade
premarket must be able to read as abnormal on the morning it does, which a dropped session
would prevent. A session the source could not answer for is not covered and is skipped, and
an older covered session takes its place; the walk is bounded by ``max_lookback_sessions`` and
refuses rather than reaching arbitrarily far back. The entry session itself is never in the
window, because its own premarket volume is the numerator.

The 1,000-share floor is deliberately *not* applied here. It belongs to the scanner's own
``premarket_rvol_baseline_floor_shares`` and is applied at the division inside ``scan``, so
this module returns the median the contract asks for and one config owns the floor.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.core.exceptions import MarketDataError
from app.market.calendar import MarketCalendar
from app.market.symbols import normalize_symbol
from app.models.analytics import PremarketVolumeSession
from app.services import premarket_volume_history as V
from app.services.premarket_volume_history import first_complete_session
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live.schedule import A_CUT, A_CUT_MINUTE

#: A's rows: the same minute source as E's, read to A's cut. Part of row identity.
SOURCE = "KIWOOM_USA06011_PM0915"
#: Part of row identity, so A's contract cannot reinterpret a V2 row or be reinterpreted by one.
COLLECTOR_VERSION = "a_mover_pm_volume_v1"
BASELINE_IDENTITY = LC.BASELINE_VERSION
BASELINE_METHOD = "MEDIAN"
BASELINE_SESSIONS = MoverScannerConfig().premarket_rvol_baseline_sessions
#: How far back the covered-session walk may reach for those twenty. Declared, not open-ended.
MAX_LOOKBACK_SESSIONS = 40

#: Stored qualities that mean "the source answered for this session".
COVERED_QUALITIES = frozenset({
    V.SessionQuality.COMPLETE.value,
    V.SessionQuality.NO_PREMARKET_BARS.value,
    V.SessionQuality.NO_REGULAR_BARS.value,
})


class BaselineStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    #: Fewer than twenty covered sessions inside the declared lookback.
    INSUFFICIENT_COVERED_SESSIONS = "INSUFFICIENT_COVERED_SESSIONS"
    UNSUPPORTED_EXCHANGE = "UNSUPPORTED_EXCHANGE"


@dataclass(frozen=True)
class BaselineRule:
    """The rule as plain data, so a run record says which denominator it used."""

    identity: str = BASELINE_IDENTITY
    source: str = SOURCE
    collector_version: str = COLLECTOR_VERSION
    window_minutes: tuple[int, int] = (240, A_CUT_MINUTE)
    sessions: int = BASELINE_SESSIONS
    method: str = BASELINE_METHOD
    quantity: str = "SHARE_VOLUME"
    silent_covered_session: str = "CONTRIBUTES_ZERO"
    current_session_in_denominator: bool = False
    max_lookback_sessions: int = MAX_LOOKBACK_SESSIONS
    floor_applied_here: bool = False

    def declaration(self) -> dict[str, Any]:
        return {
            "identity": self.identity, "source": self.source,
            "collector_version": self.collector_version,
            "window_minutes": list(self.window_minutes), "sessions": self.sessions,
            "method": self.method, "quantity": self.quantity,
            "silent_covered_session": self.silent_covered_session,
            "current_session_in_denominator": self.current_session_in_denominator,
            "max_lookback_sessions": self.max_lookback_sessions,
            "floor_applied_here": self.floor_applied_here,
            "floor_owner": "MoverScannerConfig.premarket_rvol_baseline_floor_shares",
        }


@dataclass(frozen=True)
class AMoverBaseline:
    """One symbol's denominator for one entry session, with the sessions it is a median over."""

    status: BaselineStatus
    symbol: str
    exchange: str
    entry_session_date: date
    #: The covered sessions used, oldest first. Exactly ``sessions`` long when AVAILABLE.
    used_sessions: tuple[date, ...]
    #: Sessions inside the lookback the source has no final covered answer for, newest first.
    missing_sessions: tuple[date, ...]
    #: Share volume per used session, same order; a silent covered session is 0.
    volumes: tuple[float, ...]
    median_volume: float | None
    rule: BaselineRule = BaselineRule()

    @property
    def available(self) -> bool:
        return self.status is BaselineStatus.AVAILABLE

    def declaration(self) -> dict[str, Any]:
        return {"status": str(self.status), "symbol": self.symbol, "exchange": self.exchange,
                "entry_session_date": self.entry_session_date.isoformat(),
                "used_sessions": [day.isoformat() for day in self.used_sessions],
                "covered_sessions": len(self.used_sessions),
                "missing_sessions": [day.isoformat() for day in self.missing_sessions],
                "median_volume": self.median_volume, "rule": self.rule.declaration()}


def usable_volume(row: PremarketVolumeSession) -> float | None:
    """A's reading of one stored row: a number for a covered session, None for an uncovered one."""
    if row.quality_status not in COVERED_QUALITIES:
        return None
    if row.quality_status == V.SessionQuality.NO_PREMARKET_BARS.value:
        return 0.0                 # covered, nothing printed: a real zero, not a gap
    return float(row.premarket_volume or 0)


def lookback_sessions(calendar: MarketCalendar, entry_session_date: date,
                      *, limit: int = MAX_LOOKBACK_SESSIONS) -> tuple[date, ...]:
    """The XNYS sessions the covered walk may consider, newest first, never the entry session."""
    if not calendar.is_trading_day(entry_session_date):
        raise ValueError(f"{entry_session_date} is not an XNYS session")
    out: list[date] = []
    day = entry_session_date
    for _ in range(limit):
        day = calendar.previous_trading_day(day)
        out.append(day)
    return tuple(out)


def _stored_rows(session: Session, symbol: str, exchange: str, days: Sequence[date], *,
                 source: str = SOURCE, collector_version: str = COLLECTOR_VERSION,
                 ) -> dict[date, PremarketVolumeSession]:
    rows = session.scalars(select(PremarketVolumeSession).where(
        PremarketVolumeSession.symbol == symbol,
        PremarketVolumeSession.exchange == exchange,
        PremarketVolumeSession.source == source,
        PremarketVolumeSession.collector_version == collector_version,
        PremarketVolumeSession.trading_date.in_(list(days))))
    return {row.trading_date: row for row in rows}


def load_baseline(session: Session, symbol: str, exchange: str, entry_session_date: date, *,
                  calendar: MarketCalendar | None = None, rule: BaselineRule | None = None,
                  ) -> AMoverBaseline:
    """A's denominator from durable rows only. Never a market-data request."""
    rule = rule or BaselineRule()
    calendar = calendar or MarketCalendar()
    symbol = normalize_symbol(symbol)
    days = lookback_sessions(calendar, entry_session_date, limit=rule.max_lookback_sessions)
    rows = _stored_rows(session, symbol, exchange, days, source=rule.source,
                        collector_version=rule.collector_version)
    used: list[date] = []
    volumes: list[float] = []
    missing: list[date] = []
    for day in days:                                   # newest first; the walk skips uncovered
        row = rows.get(day)
        value = usable_volume(row) if row is not None else None
        if value is None:
            missing.append(day)
            continue
        used.append(day)
        volumes.append(value)
        if len(used) == rule.sessions:
            break
    if len(used) < rule.sessions:
        return AMoverBaseline(BaselineStatus.INSUFFICIENT_COVERED_SESSIONS, symbol, exchange,
                              entry_session_date, tuple(reversed(used)), tuple(missing),
                              tuple(reversed(volumes)), None, rule)
    ordered = tuple(reversed(used))
    values = tuple(reversed(volumes))
    return AMoverBaseline(BaselineStatus.AVAILABLE, symbol, exchange, entry_session_date,
                          ordered, tuple(missing), values, float(median(values)), rule)


def covered_count(session: Session, symbol: str, exchange: str, entry_session_date: date, *,
                  calendar: MarketCalendar | None = None,
                  rule: BaselineRule | None = None) -> int:
    """How many sessions in the lookback already have a final covered answer stored."""
    rule = rule or BaselineRule()
    calendar = calendar or MarketCalendar()
    days = lookback_sessions(calendar, entry_session_date, limit=rule.max_lookback_sessions)
    rows = _stored_rows(session, normalize_symbol(symbol), exchange, days, source=rule.source,
                        collector_version=rule.collector_version)
    return sum(1 for day in days
               if day in rows and usable_volume(rows[day]) is not None)


def assess_session_at_cut(symbol: str, exchange: str, day: date, window, fetch, *,
                          collected_at: datetime, cut: time = A_CUT,
                          source: str = SOURCE,
                          collector_version: str = COLLECTOR_VERSION) -> V.SessionVolume:
    """``premarket_volume_history.assess_session`` with A's window end.

    The parent classifies minutes against the regular open; A's window ends at the scan cut, so
    a bar at or after the cut is a regular-session-side bar for A's purposes and is counted
    towards ``regular_bar_count`` instead of the premarket sum. Everything else - the symbol,
    date and session-label identity checks, the truncation rule and the quality vocabulary - is
    the parent's and is not restated.
    """
    zone = window.market_open.tzinfo
    start = datetime.combine(day, V.PREMARKET_START, zone)
    cut_at = datetime.combine(day, cut, zone)
    premarket: list[Any] = []
    after_cut = 0
    mismatch: str | None = None
    from app.market.domain import MarketSession
    for bar in sorted(fetch.bars, key=lambda item: item.timestamp):
        local = bar.timestamp.astimezone(zone)
        expected = (None if local < start
                    else MarketSession.PREMARKET if local < window.market_open
                    else MarketSession.REGULAR if local < window.market_close
                    else MarketSession.POSTMARKET)
        if bar.symbol != symbol or local.date() != day or bar.session is not expected:
            mismatch = mismatch or ("SYMBOL" if bar.symbol != symbol
                                    else "DATE" if local.date() != day else "SESSION_LABEL")
            continue
        if expected is MarketSession.PREMARKET and local < cut_at:
            premarket.append(bar)
        elif expected in (MarketSession.PREMARKET, MarketSession.REGULAR):
            after_cut += 1
    quality, reason = (
        (V.SessionQuality.SESSION_IDENTITY_MISMATCH, mismatch) if mismatch
        else (V.SessionQuality.TARGET_NOT_REACHED, None) if not fetch.target_reached
        else (V.SessionQuality.NO_PREMARKET_BARS, None) if not premarket
        else (V.SessionQuality.NO_REGULAR_BARS, None) if not after_cut
        else (V.SessionQuality.COMPLETE, None))
    return V.SessionVolume(
        symbol, exchange, day, source, collector_version, quality, reason,
        sum(bar.volume for bar in premarket) if premarket else None, len(premarket), after_cut,
        premarket[0].timestamp if premarket else None,
        premarket[-1].timestamp if premarket else None,
        fetch.pages_used, fetch.target_reached, collected_at)


class AMoverPremarketVolumeService(V.PremarketVolumeHistoryService):
    """The V2 collector with A's window and A's planning window. Two overrides, nothing else."""

    def __init__(self, session: Session, source=None, *, calendar: MarketCalendar | None = None,
                 clock=None, rule: BaselineRule | None = None) -> None:
        self.rule = rule or BaselineRule()
        super().__init__(session, source, calendar=calendar, clock=clock,
                         source_name=self.rule.source,
                         collector_version=self.rule.collector_version)

    def required_sessions(self, entry_session_date: date) -> tuple[date, ...]:
        """A's planning window, oldest first: every session the covered walk may reach."""
        return tuple(reversed(lookback_sessions(self.calendar, entry_session_date,
                                                limit=self.rule.max_lookback_sessions)))

    def missing_sessions(self, symbol: str, exchange: str,
                         entry_session_date: date) -> tuple[date, ...]:
        """Only what the denominator still needs, newest first - not the whole lookback.

        The parent returns every session in its window with no final answer, which for A's
        40-session lookback would be twice the work the twenty-session median actually needs.
        A asks for the newest unanswered sessions, as many as are still missing from twenty.
        If some of those come back uncovered the *next* run asks for the next older ones, so
        the worst case costs more runs rather than more requests per run; the durable rows stay
        the checkpoint either way.
        """
        symbol = normalize_symbol(symbol)
        days = lookback_sessions(self.calendar, entry_session_date,
                                 limit=self.rule.max_lookback_sessions)
        rows = _stored_rows(self.session, symbol, exchange, days, source=self.source_name,
                            collector_version=self.collector_version)
        covered, pending = 0, []
        for day in days:                                   # newest first
            row = rows.get(day)
            value = usable_volume(row) if row is not None else None
            if value is not None:
                covered += 1
            elif row is None or V.retryable(row, first_complete_session(
                    self.session, symbol, exchange, self.source_name, self.collector_version)):
                pending.append(day)
            if covered + len(pending) >= self.rule.sessions:
                break
        return tuple(pending)

    def collect_session(self, symbol: str, exchange: str, day: date) -> V.SessionVolume:
        window = self.calendar.session(day)
        if window is None:
            raise ValueError(f"{day} is not an XNYS session")
        now = self.clock()
        if now < window.market_close:
            raise ValueError(f"session {day} has not completed")
        if self.source is None:
            raise RuntimeError("collection needs a market-data source")
        start = datetime.combine(day, V.PREMARKET_START, window.market_open.tzinfo)
        try:
            fetch = self.source.fetch_session(symbol, exchange, start, window.market_close)
        except MarketDataError as exc:
            return V.SessionVolume(symbol, exchange, day, self.source_name,
                                   self.collector_version, V.SessionQuality.PROVIDER_FAILURE,
                                   exc.code, None, 0, 0, None, None, None, None, now)
        return assess_session_at_cut(symbol, exchange, day, window, fetch, collected_at=now,
                                     source=self.source_name,
                                     collector_version=self.collector_version)

    def baseline(self, symbol: str, exchange: str,
                 entry_session_date: date) -> AMoverBaseline:
        return load_baseline(self.session, symbol, exchange, entry_session_date,
                             calendar=self.calendar, rule=self.rule)


def rows_statistics(session: Session, *, rule: BaselineRule | None = None) -> dict[str, Any]:
    """How much of A's baseline exists today. Read-only; the backfill planner uses it."""
    rule = rule or BaselineRule()
    rows = list(session.scalars(select(PremarketVolumeSession).where(
        PremarketVolumeSession.source == rule.source,
        PremarketVolumeSession.collector_version == rule.collector_version)))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.quality_status] = counts.get(row.quality_status, 0) + 1
    sessions = sorted({row.trading_date for row in rows})
    return {"identity": rule.identity, "source": rule.source,
            "collector_version": rule.collector_version, "rows": len(rows),
            "symbols": len({row.symbol for row in rows}), "sessions": len(sessions),
            "oldest_session": sessions[0].isoformat() if sessions else None,
            "newest_session": sessions[-1].isoformat() if sessions else None,
            "quality_counts": counts,
            "covered_rows": sum(1 for row in rows
                                if row.quality_status in COVERED_QUALITIES)}
