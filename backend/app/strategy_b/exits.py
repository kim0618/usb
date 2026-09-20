"""Strategy B exit management: B-F0 section 9, applied bar by bar.

One open position, one tick at a time, the same shape as the candidate FSM: values in,
values out, no portfolio and no broker. The engine owns equity, the daily loss limit and the
run store; this module answers "what happened to this position between the last tick and
now, in the order it happened".

Order inside one bar is the declared priority, worst case first:

``HARD_STOP`` / ``TRAILING_STOP`` > ``EOD_EXIT`` > ``TIME_STOP`` > ``PARTIAL_TRAIL``

A bar that reaches the 2R target and also trades through the stop is booked as a stop, not
as a partial profit. Minute bars do not say in which order the two prices happened, and
assuming the good one first would quietly flatter every result.

Deadlines (the time stop and the end of day) are wall-clock, not bar-indexed. When one
passes, the fill is the close of the last actual bar **before** the deadline, exactly as
declared: a silent tape does not postpone a time stop, and a bar printed after the deadline
does not become the exit price.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from math import floor

from app.strategy_b.config import ExitConfig, parse_et_clock
from app.strategy_b.errors import InvalidTransition, PointInTimeViolation
from app.strategy_b.features import SessionTape
from app.strategy_b.models import SetupType
from app.strategy_b.session import ET, AggregationScope


class ExitReason(StrEnum):
    HARD_STOP = "HARD_STOP"
    """The stop was still where the setup put it."""
    TRAILING_STOP = "TRAILING_STOP"
    """The stop had already moved up (breakeven after the partial, then the trail)."""
    PARTIAL_TRAIL = "PARTIAL_TRAIL"
    """Not an exit of the position: the 2R partial, after which the rest runs on the trail."""
    TIME_STOP = "TIME_STOP"
    EOD_EXIT = "EOD_EXIT"


@dataclass(frozen=True, slots=True)
class ExitEvent:
    reason: ExitReason
    at: datetime
    price: float
    shares: int
    closes_position: bool


@dataclass(frozen=True, slots=True)
class Position:
    """An open B position. ``shares == 0`` means it is closed and cannot be advanced again."""

    symbol: str
    setup: SetupType
    entry_price: float
    entered_at: datetime
    shares: int
    initial_stop: float
    stop: float
    partial_taken: bool
    updated_at: datetime
    """Availability cursor, not a wall clock: bars available after it have not been read yet.

    It starts at the **entry bar's timestamp**, so the very bar the fill came from is still
    scanned for a stop. A breakout bar that runs through the trigger and then through the
    stop inside the same minute is a taken loss (B-F0 10.4); starting the scan after the fill
    would delete exactly those trades from the study.
    """

    def __post_init__(self) -> None:
        if self.shares < 0:
            raise ValueError("shares cannot be negative")
        if not self.initial_stop < self.entry_price:
            raise ValueError("the initial stop must sit below the entry price")
        if self.stop < self.initial_stop:
            raise ValueError("a stop never moves down")

    @property
    def is_open(self) -> bool:
        return self.shares > 0

    @property
    def risk_per_share(self) -> float:
        """1R, fixed at entry. The trail moves the stop; it does not redefine R."""
        return self.entry_price - self.initial_stop

    def target_price(self, config: ExitConfig) -> float:
        return self.entry_price + config.partial_take_profit_r * self.risk_per_share


@dataclass(frozen=True, slots=True)
class PositionUpdate:
    position: Position
    events: tuple[ExitEvent, ...]


def open_position(symbol: str, setup: SetupType, *, entry_price: float, entered_at: datetime,
                  shares: int, initial_stop: float, entry_bar_timestamp: datetime) -> Position:
    """Open a position filled from the bar opening at ``entry_bar_timestamp``.

    ``entered_at`` is the tick the engine recorded the fill on (one tick after the signal,
    B-F0 8.3); ``entry_bar_timestamp`` is the bar the price came from, and the exit scan
    starts there so the entry bar's own low is checked against the stop.
    """
    if shares <= 0:
        raise ValueError("a position opens with at least one share")
    if entry_bar_timestamp > entered_at:
        raise PointInTimeViolation("the entry bar cannot open after the fill was recorded")
    return Position(symbol=symbol, setup=setup, entry_price=entry_price, entered_at=entered_at,
                    shares=shares, initial_stop=initial_stop, stop=initial_stop,
                    partial_taken=False, updated_at=entry_bar_timestamp)


def advance_position(position: Position, tape: SessionTape, as_of: datetime, *,
                     config: ExitConfig, scope: AggregationScope) -> PositionUpdate:
    """Apply every bar that became available since the last tick, in order."""
    if not position.is_open:
        raise InvalidTransition(f"{position.symbol} is already closed")
    if position.symbol != tape.symbol:
        raise ValueError("position and tape describe different symbols")
    if as_of < position.updated_at:
        raise PointInTimeViolation(
            f"tick {as_of.isoformat()} is before the position's last update "
            f"{position.updated_at.isoformat()}")

    eod_at = _et_moment(tape, config.eod_exit_et)
    current, events = position, []
    window = tape.scope_range(as_of, scope)
    if window is not None:
        _, lo, hi = window
        start, stop_index = tape.window_range(lo, position.updated_at, as_of)
        for index in range(start, stop_index):
            bar = tape.bars[index]
            deadline = _due_deadline(current, config, eod_at, bar.timestamp)
            if deadline is not None:
                current, events = _close_on_deadline(current, tape, deadline, events, lo, hi)
                break
            if current.partial_taken and index > lo:
                current = replace(current, stop=max(current.stop, tape.bars[index - 1].low))
            if bar.low <= current.stop:
                price = bar.open if bar.open < current.stop else current.stop
                reason = (ExitReason.HARD_STOP if current.stop == current.initial_stop
                          else ExitReason.TRAILING_STOP)
                current, events = _close(current, reason, bar.timestamp, price, events)
                break
            if not current.partial_taken and bar.high >= current.target_price(config):
                current, events = _take_partial(current, config, bar.timestamp, events)
            current = replace(current, updated_at=max(current.updated_at, bar.available_at))

    if current.is_open:
        deadline = _due_deadline(current, config, eod_at, as_of)
        if deadline is not None and window is not None:
            _, lo, hi = window
            current, events = _close_on_deadline(current, tape, deadline, events, lo, hi)
    return PositionUpdate(replace(current, updated_at=max(current.updated_at, as_of)),
                          tuple(events))


# ---- helpers ----------------------------------------------------------------------------

def _due_deadline(position: Position, config: ExitConfig, eod_at: datetime,
                  moment: datetime) -> tuple[ExitReason, datetime] | None:
    """The deadline that has passed by ``moment``. EOD outranks the time stop (B-F0 9)."""
    if moment >= eod_at:
        return ExitReason.EOD_EXIT, eod_at
    time_stop_at = position.entered_at + timedelta(minutes=config.time_stop_minutes)
    if not position.partial_taken and moment >= time_stop_at:
        return ExitReason.TIME_STOP, time_stop_at
    return None


def _close_on_deadline(position: Position, tape: SessionTape,
                       deadline: tuple[ExitReason, datetime], events: list[ExitEvent],
                       lo: int, hi: int) -> tuple[Position, list[ExitEvent]]:
    """Close at the last actual close before the deadline, never at a bar printed after it."""
    reason, at = deadline
    index = min(tape.first_index_at_or_after(at), hi) - 1
    price = tape.bars[index].close if index >= lo else position.entry_price
    return _close(position, reason, at, price, events)


def _close(position: Position, reason: ExitReason, at: datetime, price: float,
           events: list[ExitEvent]) -> tuple[Position, list[ExitEvent]]:
    events.append(ExitEvent(reason, at, price, position.shares, closes_position=True))
    return replace(position, shares=0, updated_at=max(position.updated_at, at)), events


def _take_partial(position: Position, config: ExitConfig, at: datetime,
                  events: list[ExitEvent]) -> tuple[Position, list[ExitEvent]]:
    """Sell the declared fraction at the target and lift the rest's stop to breakeven.

    A position too small to split (one share, or a fraction that rounds to zero) takes the
    whole profit at the target instead of holding on: a 2R target that cannot be halved is
    still a 2R target, and pretending to scale out of one share would invent a trade.
    """
    price = position.target_price(config)
    partial = floor(position.shares * config.partial_exit_fraction)
    if partial <= 0:
        return _close(position, ExitReason.PARTIAL_TRAIL, at, price, events)
    events.append(ExitEvent(ExitReason.PARTIAL_TRAIL, at, price, partial, closes_position=False))
    return replace(position, shares=position.shares - partial, stop=position.entry_price,
                   partial_taken=True, updated_at=max(position.updated_at, at)), events


def _et_moment(tape: SessionTape, clock: str) -> datetime:
    """``HH:MM`` ET on the tape's own session date."""
    return datetime.combine(tape.boundaries.session_date, parse_et_clock(clock), tzinfo=ET)
