"""D4 code-owned price arithmetic: event alignment, event-window returns, pre-event context.

Every number D4 reports about price comes from here. The AI never computes one (D4 brief §25), and
this module never interprets one - a return of +18% is a fact this module produces; what it means
about expectations is `analysis_schema`'s question and carries its own confidence.

Two PIT rules are enforced mechanically rather than left to caller discipline:

1. No bar published after `decision_time` is readable at all (`pit_eligible_series`). A forward D4
   run that could see a post-decision close would be measuring the future, and D4 brief §9 forbids
   it explicitly. The check is on the *series*, before any arithmetic, so no individual function
   has to remember it.
2. An event window that would need a session this series does not have is INCOMPLETE and returns
   `None` - never silently shortened to the sessions that do exist, which is how a "3-day event
   return" quietly becomes a 1-day one.

Session close times. US equity regular-session close is 16:00 ET, which is 20:00Z under EDT and
21:00Z under EST. This module does not resolve which applies on a given date (the repository has no
exchange-calendar/DST source, and inventing one would be exactly the kind of fabricated precision
D4 exists to avoid). It instead uses whichever bound is conservative for the question being asked:

- PIT eligibility uses the LATER bound (21:00Z): a bar is treated as knowable only after the latest
  time its session could have closed, so a bar is never assumed known earlier than it could be.
- Event alignment uses the EARLIER bound (20:00Z): the reacting session is the first whose earliest
  possible close is strictly after the news, so a session is never assumed to have reacted to news
  it might not yet have seen.

When those two bounds disagree about which session an event belongs to, the event is flagged
`alignment_ambiguous` rather than silently assigned to one of them. The same flag covers a source
whose own timestamp is `DATE_ONLY`/`UNKNOWN` precision, where no time-of-day comparison is possible
at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from enum import StrEnum
import math
from typing import Mapping, Sequence

#: Earliest possible UTC time of a US regular-session close (16:00 ET under EDT).
SESSION_CLOSE_EARLIEST_UTC = time(20, 0, tzinfo=timezone.utc)
#: Latest possible UTC time of a US regular-session close (16:00 ET under EST).
SESSION_CLOSE_LATEST_UTC = time(21, 0, tzinfo=timezone.utc)

#: Session lookbacks, reusing D1's own conventions (`run_strategy_h_v2_d1.price_context_of`) so a
#: D4 1-month return means the same thing as the D1 one already in every evidence bundle.
SESSIONS_1M = 21
SESSIONS_3M = 63
SESSIONS_6M = 126
SESSIONS_52W = 252
#: Realized-volatility window. 60 sessions ~ one quarter: long enough that a single gap day does not
#: dominate, short enough to still describe the current regime rather than last year's.
VOLATILITY_SESSIONS = 60
TRADING_DAYS_PER_YEAR = 252


class FuturePriceLeakError(RuntimeError):
    """A price series contained a session whose close could not have been known at `decision_time`.
    Raised, never filtered silently: a caller that handed D4 future prices has a bug in its own
    data assembly, and quietly trimming the series would hide it."""


class WindowStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE_FUTURE = "INCOMPLETE_FUTURE"
    """The window extends past the last session available at `decision_time` - the honest state for
    a forward/live run, where the post-event sessions simply have not happened yet."""
    INCOMPLETE_HISTORY = "INCOMPLETE_HISTORY"
    """The window extends before the first session this series has."""
    NO_EVENT_SESSION = "NO_EVENT_SESSION"
    """No session in the series can be attributed to the event at all."""


def _session_close(session: date, at: time) -> datetime:
    return datetime.combine(session, at.replace(tzinfo=None), tzinfo=timezone.utc)


def pit_eligible_series(
    series: Mapping[date, float], decision_time: datetime,
) -> dict[date, float]:
    """Every bar knowable at `decision_time`, and a hard error if the input contained one that was
    not. `decision_time` must be timezone-aware."""
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise ValueError("decision_time must be timezone-aware")
    cutoff = decision_time.astimezone(timezone.utc)
    leaked = sorted(
        session for session in series
        if _session_close(session, SESSION_CLOSE_LATEST_UTC) > cutoff
    )
    if leaked:
        raise FuturePriceLeakError(
            f"{len(leaked)} session(s) close after decision_time {cutoff.isoformat()} "
            f"(first {leaked[0].isoformat()}, last {leaked[-1].isoformat()}) - D4 may not read a "
            "price bar that did not exist at the decision point"
        )
    return dict(series)


@dataclass(frozen=True)
class EventAlignment:
    """Which session, if any, is the first that could have reacted to an event."""

    event_session: date | None
    prior_session: date | None
    """The last session whose close preceded the event - the base for an event return."""
    alignment_ambiguous: bool
    ambiguity_reason: str | None
    status: WindowStatus


def align_event(
    sessions: Sequence[date], available_at: datetime | None, *, timestamp_is_exact: bool = True,
) -> EventAlignment:
    """The first session whose close is strictly after `available_at`, plus the session before it.

    `timestamp_is_exact=False` (a `DATE_ONLY`/`UNKNOWN` `date_precision` source) still aligns - to
    the first session strictly AFTER the stated calendar date, the only choice that cannot assume a
    reaction that had not yet had the chance to happen - but is always flagged ambiguous.
    """
    ordered = sorted(sessions)
    if not ordered or available_at is None:
        return EventAlignment(None, None, True, "no event timestamp or no sessions",
                              WindowStatus.NO_EVENT_SESSION)
    stamp = available_at.astimezone(timezone.utc)

    def _first_after(close_at: time) -> date | None:
        for session in ordered:
            if _session_close(session, close_at) > stamp:
                return session
        return None

    if timestamp_is_exact:
        early = _first_after(SESSION_CLOSE_EARLIEST_UTC)
        late = _first_after(SESSION_CLOSE_LATEST_UTC)
        chosen, ambiguous = early, early != late
        reason = (
            None if not ambiguous else
            f"event session is {early} under a 20:00Z close and {late} under a 21:00Z close; the "
            "repository has no exchange calendar to resolve which applied on this date"
        )
    else:
        chosen = next((s for s in ordered if s > stamp.date()), None)
        ambiguous = True
        reason = "source timestamp is not DATETIME-precise; aligned to the first session after its stated date"

    if chosen is None:
        return EventAlignment(None, None, ambiguous,
                              reason or "event is after every available session",
                              WindowStatus.INCOMPLETE_FUTURE)
    index = ordered.index(chosen)
    if index == 0:
        return EventAlignment(chosen, None, ambiguous,
                              reason or "no session precedes the event in this series",
                              WindowStatus.INCOMPLETE_HISTORY)
    return EventAlignment(chosen, ordered[index - 1], ambiguous, reason, WindowStatus.COMPLETE)


@dataclass(frozen=True)
class WindowReturn:
    """One window's return, or an explicit reason there is none. `value` is `None` whenever `status`
    is not COMPLETE - there is no zero-filled fallback anywhere in this module."""

    value: float | None
    status: WindowStatus
    from_session: date | None = None
    to_session: date | None = None

    def to_dict(self) -> dict:
        return {
            "value": self.value, "status": self.status.value,
            "from_session": self.from_session.isoformat() if self.from_session else None,
            "to_session": self.to_session.isoformat() if self.to_session else None,
        }


def _span_return(
    series: Mapping[date, float], sessions: Sequence[date], start_index: int, end_index: int,
) -> WindowReturn:
    if start_index < 0:
        return WindowReturn(None, WindowStatus.INCOMPLETE_HISTORY)
    if end_index > len(sessions) - 1:
        return WindowReturn(None, WindowStatus.INCOMPLETE_FUTURE)
    start, end = sessions[start_index], sessions[end_index]
    base = series[start]
    if base <= 0:
        return WindowReturn(None, WindowStatus.INCOMPLETE_HISTORY, start, end)
    return WindowReturn(series[end] / base - 1, WindowStatus.COMPLETE, start, end)


def event_window_return(
    series: Mapping[date, float], alignment: EventAlignment, sessions_after: int,
) -> WindowReturn:
    """Return from the close before the event to `sessions_after` sessions after the event session.

    `sessions_after=0` is the 1-day event return (prior close -> event-session close);
    `sessions_after=2` is the conventional 3-day event return.
    """
    if alignment.prior_session is None or alignment.event_session is None:
        return WindowReturn(None, alignment.status or WindowStatus.NO_EVENT_SESSION)
    ordered = sorted(series)
    try:
        base_index = ordered.index(alignment.prior_session)
        event_index = ordered.index(alignment.event_session)
    except ValueError:
        return WindowReturn(None, WindowStatus.NO_EVENT_SESSION)
    return _span_return(series, ordered, base_index, event_index + sessions_after)


def benchmark_adjusted(
    candidate: WindowReturn, series: Mapping[date, float], benchmark: Mapping[date, float],
) -> WindowReturn:
    """`candidate` minus the benchmark's return over the SAME two calendar sessions.

    Not "over the same number of sessions" - the same sessions, which is why this takes the already
    computed `WindowReturn` rather than recomputing from an offset. A benchmark that did not trade
    on exactly those two days yields `None`, because a benchmark return over a different span is
    not an adjustment, it is a different number wearing the same name.
    """
    del series
    if candidate.value is None or candidate.from_session is None or candidate.to_session is None:
        return WindowReturn(None, candidate.status, candidate.from_session, candidate.to_session)
    start, end = candidate.from_session, candidate.to_session
    if start not in benchmark or end not in benchmark or benchmark[start] <= 0:
        return WindowReturn(None, WindowStatus.INCOMPLETE_HISTORY, start, end)
    bench = benchmark[end] / benchmark[start] - 1
    return WindowReturn(candidate.value - bench, WindowStatus.COMPLETE, start, end)


def pre_event_return(
    series: Mapping[date, float], alignment: EventAlignment, lookback_sessions: int,
) -> WindowReturn:
    """Return over the `lookback_sessions` ending at the last close BEFORE the event.

    Ending before the event, never through it: the question this answers is what the price had
    already done by the time the news arrived (D4 brief §11), and a window that includes the event
    day has folded the answer into the question.
    """
    if alignment.prior_session is None:
        return WindowReturn(None, alignment.status or WindowStatus.NO_EVENT_SESSION)
    ordered = sorted(series)
    try:
        end_index = ordered.index(alignment.prior_session)
    except ValueError:
        return WindowReturn(None, WindowStatus.NO_EVENT_SESSION)
    return _span_return(series, ordered, end_index - lookback_sessions, end_index)


def trailing_return(series: Mapping[date, float], lookback_sessions: int) -> WindowReturn:
    """Return over the `lookback_sessions` ending at the last session in the series."""
    ordered = sorted(series)
    if not ordered:
        return WindowReturn(None, WindowStatus.INCOMPLETE_HISTORY)
    return _span_return(series, ordered, len(ordered) - 1 - lookback_sessions, len(ordered) - 1)


def realized_volatility(
    series: Mapping[date, float], sessions: int = VOLATILITY_SESSIONS,
) -> float | None:
    """Annualized sample standard deviation of daily log returns over the last `sessions` bars.
    `None` - never 0.0 - when there are too few bars or any non-positive close."""
    ordered = sorted(series)
    window = ordered[-(sessions + 1):]
    if len(window) < 3:
        return None
    logs: list[float] = []
    for previous, current in zip(window, window[1:], strict=False):
        before, after = series[previous], series[current]
        if before <= 0 or after <= 0:
            return None
        logs.append(math.log(after / before))
    mean = sum(logs) / len(logs)
    variance = sum((x - mean) ** 2 for x in logs) / (len(logs) - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS_PER_YEAR)


@dataclass(frozen=True)
class PriceLevelContext:
    """Code-owned pre-event price context (D4 brief §11).

    The high/low fields are named after what they actually measure - the extremes of the CLOSE
    series over the last 252 sessions - rather than "52-week high/low", which in market usage means
    the extremes of intraday highs and lows. The daily store D1/D2 read carries a close per session,
    so a close-based extreme is what is computable here; naming it after the intraday statistic
    would misdescribe it by exactly the intraday range. (D1's own `price_context.trailing_52w_low`
    is likewise computed from closes; this module does not modify D1 or its bundles, it declines to
    repeat the label.)
    """

    last_session: date | None
    last_close: float | None
    return_1m: WindowReturn
    return_3m: WindowReturn
    return_6m: WindowReturn
    relative_strength_1m: WindowReturn
    relative_strength_3m: WindowReturn
    relative_strength_6m: WindowReturn
    close_252s_high: float | None
    close_252s_low: float | None
    drawdown_from_252s_close_high: float | None
    realized_volatility_60s: float | None
    sessions_available: int

    def to_dict(self) -> dict:
        return {
            "last_session": self.last_session.isoformat() if self.last_session else None,
            "last_close": self.last_close,
            "return_1m": self.return_1m.to_dict(), "return_3m": self.return_3m.to_dict(),
            "return_6m": self.return_6m.to_dict(),
            "relative_strength_1m": self.relative_strength_1m.to_dict(),
            "relative_strength_3m": self.relative_strength_3m.to_dict(),
            "relative_strength_6m": self.relative_strength_6m.to_dict(),
            "close_252s_high": self.close_252s_high, "close_252s_low": self.close_252s_low,
            "drawdown_from_252s_close_high": self.drawdown_from_252s_close_high,
            "realized_volatility_60s": self.realized_volatility_60s,
            "sessions_available": self.sessions_available,
        }


def price_level_context(
    series: Mapping[date, float], benchmark: Mapping[date, float],
) -> PriceLevelContext:
    ordered = sorted(series)
    if not ordered:
        empty = WindowReturn(None, WindowStatus.INCOMPLETE_HISTORY)
        return PriceLevelContext(None, None, empty, empty, empty, empty, empty, empty,
                                 None, None, None, None, 0)
    last = ordered[-1]
    r1m, r3m, r6m = (trailing_return(series, n) for n in (SESSIONS_1M, SESSIONS_3M, SESSIONS_6M))
    closes = [series[s] for s in ordered[-SESSIONS_52W:]]
    high = max(closes) if closes else None
    drawdown = None if not high or high <= 0 else series[last] / high - 1
    return PriceLevelContext(
        last_session=last, last_close=series[last],
        return_1m=r1m, return_3m=r3m, return_6m=r6m,
        relative_strength_1m=benchmark_adjusted(r1m, series, benchmark),
        relative_strength_3m=benchmark_adjusted(r3m, series, benchmark),
        relative_strength_6m=benchmark_adjusted(r6m, series, benchmark),
        close_252s_high=high, close_252s_low=min(closes) if closes else None,
        drawdown_from_252s_close_high=drawdown,
        realized_volatility_60s=realized_volatility(series),
        sessions_available=len(ordered),
    )
