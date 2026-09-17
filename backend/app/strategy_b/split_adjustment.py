"""Point-in-time split adjustment.

Massive ``adjusted=true`` is never used. It rescales history by splits that had not happened
yet at the bar's date (observed: KIDZ 2026-03-09 0.068 became 510), so every adjusted value
leaks the future. Here the caller passes raw bars plus split records, and only splits with
``execution_date <= current_date`` exist for the computation.

Convention (Massive ``split_from`` / ``split_to``): a 1-for-10 reverse split is
``split_from=10, split_to=1``; a 2-for-1 forward split is ``split_from=1, split_to=2``.
A raw value observed on ``observed_date`` is made comparable with ``current_date`` by every
split with ``observed_date < execution_date <= current_date``:

* ``price_factor``: multiply a raw historical price by it (reverse 1:10 gives 10).
* ``share_factor``: multiply a raw historical share volume by it (reverse 1:10 gives 0.1).

On the execution date itself the tape already trades post-split, so a split applies to
the previous day's close but not to the same day's bars.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite, prod

from app.strategy_b.errors import PointInTimeViolation


@dataclass(frozen=True, slots=True)
class SplitRecord:
    execution_date: date
    split_from: float
    split_to: float

    def __post_init__(self) -> None:
        for name in ("split_from", "split_to"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.split_from == self.split_to:
            raise ValueError("a split with split_from == split_to changes nothing and is refused")

    @property
    def share_factor(self) -> float:
        return self.split_to / self.split_from

    @property
    def price_factor(self) -> float:
        return self.split_from / self.split_to

    @property
    def is_reverse(self) -> bool:
        return self.split_to < self.split_from


@dataclass(frozen=True, slots=True)
class SplitAdjustment:
    observed_date: date
    current_date: date
    price_factor: float
    share_factor: float
    applied: tuple[SplitRecord, ...]
    split_on_day: bool
    recent_split: bool

    @property
    def adjusted(self) -> bool:
        return bool(self.applied)


def known_splits(splits: Iterable[SplitRecord], current_date: date) -> tuple[SplitRecord, ...]:
    """The splits that exist as of ``current_date``, in execution order.

    Two records on the same execution date are refused: one symbol cannot split twice in a
    day, so a duplicate means the record source is wrong, and multiplying both would be too.
    """
    known = sorted((s for s in splits if s.execution_date <= current_date),
                   key=lambda s: s.execution_date)
    for earlier, later in zip(known, known[1:]):
        if earlier.execution_date == later.execution_date:
            raise ValueError(f"two split records share execution_date {later.execution_date}")
    return tuple(known)


def split_adjustment(splits: Iterable[SplitRecord], *, observed_date: date, current_date: date,
                     recent_split_calendar_days: int) -> SplitAdjustment:
    """Factors that bring a raw ``observed_date`` value to the ``current_date`` share basis."""
    if observed_date > current_date:
        raise PointInTimeViolation(
            f"observed_date {observed_date} is after current_date {current_date}; a future "
            "value cannot be adjusted into the present")
    known = known_splits(splits, current_date)
    applied = tuple(s for s in known if observed_date < s.execution_date)
    recent_start = current_date - timedelta(days=recent_split_calendar_days)
    return SplitAdjustment(
        observed_date=observed_date,
        current_date=current_date,
        price_factor=prod(s.price_factor for s in applied),
        share_factor=prod(s.share_factor for s in applied),
        applied=applied,
        split_on_day=any(s.execution_date == current_date for s in known),
        recent_split=any(recent_start <= s.execution_date < current_date for s in known),
    )


def adjusted_previous_close(raw_close: float, *, close_date: date, current_date: date,
                            splits: Iterable[SplitRecord], recent_split_calendar_days: int) -> float:
    """The previous close on today's price basis. Needed for any gap on a split day."""
    if close_date >= current_date:
        raise PointInTimeViolation("a previous close must come from before current_date")
    adjustment = split_adjustment(splits, observed_date=close_date, current_date=current_date,
                                  recent_split_calendar_days=recent_split_calendar_days)
    return raw_close * adjustment.price_factor
