"""PIT-safe intraday features over one symbol's one-day tape of actual minute bars.

A ``SessionTape`` may hold the whole stored day, including bars after any as-of time.
Every query takes ``as_of`` and cuts the tape with ``bar.available_at <= as_of``
(``models.AVAILABILITY_DELAY``) before reading anything, so adding later bars cannot change
an earlier answer. The PIT mutation tests pin this.

Sparse-bar policy (HYBRID-S):

* Windows are wall-clock, never bar counts. ``return_5m`` compares the last actual trade
  available at ``as_of`` with the last actual trade available at ``as_of - 5 minutes``;
  on a tape with bars at 09:31, 09:34 and 09:39 that is not "two bars ago".
* A minute without a bar is a minute with zero volume and an unchanged last price.
  That carry-forward is implicit here (a bisect on availability) and explicit in
  ``sparse_session.minute_clock_view``; both give the same numbers.
* HOD, LOD and VWAP use actual bars only.

Cost: building a tape is O(n log n); each feature query is O(log n) through prefix sums,
a sparse table for range max/min, and bisect. Nothing rescans the history per as-of.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta
from itertools import accumulate
from typing import Any

from app.strategy_b.config import DollarVolumePriceBasis, FeatureConfig
from app.strategy_b.errors import InvalidTape
from app.strategy_b.models import Availability, Measured, MomentumBar, TapeDensity
from app.strategy_b.session import AggregationScope, SessionBoundaries


class _RangeExtreme:
    """Sparse table: O(n log n) build, O(1) max or min over any index range."""

    def __init__(self, values: Sequence[float], pick: Callable[[float, float], float]) -> None:
        self._pick = pick
        self._levels: list[list[float]] = [list(values)]
        width = 1
        while 2 * width <= len(values):
            previous = self._levels[-1]
            self._levels.append([pick(previous[i], previous[i + width])
                                 for i in range(len(values) - 2 * width + 1)])
            width *= 2

    def query(self, start: int, stop: int) -> float:
        level = (stop - start).bit_length() - 1
        row = self._levels[level]
        return self._pick(row[start], row[stop - (1 << level)])


class SessionTape:
    """One symbol's actual minute bars for one trading date, immutable after construction."""

    def __init__(self, symbol: str, boundaries: SessionBoundaries,
                 bars: Iterable[MomentumBar]) -> None:
        if not symbol or symbol != symbol.strip().upper():
            raise InvalidTape("symbol must be a non-blank upper-case ticker")
        self.symbol = symbol
        self.boundaries = boundaries
        self.bars: tuple[MomentumBar, ...] = tuple(bars)
        self._validate()
        self.timestamps = [bar.timestamp for bar in self.bars]
        self.available = [bar.available_at for bar in self.bars]
        volumes = [bar.volume for bar in self.bars]
        self._cum_volume = [0.0, *accumulate(volumes)]
        self._cum_close_notional = [0.0, *accumulate(b.close * b.volume for b in self.bars)]
        self._cum_vwap_notional = [0.0, *accumulate((b.vwap or 0.0) * b.volume for b in self.bars)]
        self._cum_missing_vwap = [0, *accumulate(
            int(b.vwap is None and b.volume > 0) for b in self.bars)]
        self._high = _RangeExtreme([b.high for b in self.bars], max)
        self._low = _RangeExtreme([b.low for b in self.bars], min)
        self._memo: dict[Any, Any] = {}

    def derived(self, key: Any, build: Callable[["SessionTape"], Any]) -> Any:
        """Memoize an index derived from the whole tape (for example halt gap events).

        ``build`` must be a pure function of the tape; its result is itself queried with an
        as-of cut, never read whole.
        """
        if key not in self._memo:
            self._memo[key] = build(self)
        return self._memo[key]

    def _validate(self) -> None:
        start, end = self.boundaries.premarket_start, self.boundaries.after_close
        previous: datetime | None = None
        for bar in self.bars:
            if bar.synthetic:
                raise InvalidTape("a tape holds actual trades only; synthetic bars are a view")
            if not start <= bar.timestamp < end:
                raise InvalidTape(f"bar {bar.timestamp.isoformat()} is outside {self.boundaries.session_date}")
            if bar.session is not self.boundaries.classify(bar.timestamp):
                raise InvalidTape(
                    f"bar {bar.timestamp.isoformat()} is labelled {bar.session} but the session "
                    f"boundaries say {self.boundaries.classify(bar.timestamp)}")
            if previous is not None and bar.timestamp <= previous:
                raise InvalidTape("bars must be strictly increasing by timestamp, without duplicates")
            previous = bar.timestamp

    # ---- cuts ---------------------------------------------------------------------------

    def cut(self, as_of: datetime) -> int:
        """Number of bars available at ``as_of``. Every read goes through this."""
        return bisect_right(self.available, as_of)

    def first_index_at_or_after(self, moment: datetime) -> int:
        return bisect_left(self.timestamps, moment)

    def available_bars(self, as_of: datetime) -> tuple[MomentumBar, ...]:
        return self.bars[:self.cut(as_of)]

    def scope_range(self, as_of: datetime, scope: AggregationScope) -> tuple[datetime, int, int] | None:
        start = self.boundaries.scope_start(as_of, scope)
        if start is None:
            return None
        return start, self.first_index_at_or_after(start), self.cut(as_of)

    def window_range(self, start_idx: int, window_open: datetime, as_of: datetime) -> tuple[int, int]:
        """Bars available in ``(window_open, as_of]``, not before ``start_idx``."""
        return max(start_idx, bisect_right(self.available, window_open)), self.cut(as_of)

    # ---- range aggregates ---------------------------------------------------------------

    def volume(self, lo: int, hi: int) -> float:
        return self._cum_volume[hi] - self._cum_volume[lo] if hi > lo else 0.0

    def dollar_volume(self, lo: int, hi: int, basis: DollarVolumePriceBasis) -> Measured:
        """Intraday dollar volume in USD: Σ(basis price × volume) over bars ``[lo, hi)``.

        Every scanner dollar-volume feature goes through here. ``CLOSE`` never depends on
        source VWAP availability.
        """
        if hi <= lo:
            return Measured.of(0.0)
        if basis is DollarVolumePriceBasis.CLOSE:
            return Measured.of(self._cum_close_notional[hi] - self._cum_close_notional[lo])
        if self._cum_missing_vwap[hi] - self._cum_missing_vwap[lo]:
            return Measured.missing(Availability.NO_SOURCE_VWAP)
        return Measured.of(self._cum_vwap_notional[hi] - self._cum_vwap_notional[lo])

    def vwap(self, lo: int, hi: int) -> Measured:
        if hi <= lo:
            return Measured.missing(Availability.NO_DATA)
        if self._cum_missing_vwap[hi] - self._cum_missing_vwap[lo]:
            return Measured.missing(Availability.NO_SOURCE_VWAP)
        volume = self.volume(lo, hi)
        if volume <= 0:
            return Measured.missing(Availability.ZERO_BASELINE)
        return Measured.of((self._cum_vwap_notional[hi] - self._cum_vwap_notional[lo]) / volume)

    def high(self, lo: int, hi: int) -> float:
        return self._high.query(lo, hi)

    def low(self, lo: int, hi: int) -> float:
        return self._low.query(lo, hi)


# ---- individual features ---------------------------------------------------------------

def last_price(tape: SessionTape, as_of: datetime, scope: AggregationScope) -> tuple[Measured, MomentumBar | None]:
    """Close of the last actual bar available at ``as_of`` inside ``scope``."""
    scoped = tape.scope_range(as_of, scope)
    if scoped is None or scoped[2] <= scoped[1]:
        return Measured.missing(Availability.NO_DATA), None
    bar = tape.bars[scoped[2] - 1]
    return Measured.of(bar.close), bar


def clock_return(tape: SessionTape, as_of: datetime, minutes: int, scope: AggregationScope) -> Measured:
    """Percent change from the price at ``as_of - minutes`` to the price at ``as_of``."""
    scoped = tape.scope_range(as_of, scope)
    if scoped is None or scoped[2] <= scoped[1]:
        return Measured.missing(Availability.NO_DATA)
    start, first, now = scoped
    reference_time = as_of - timedelta(minutes=minutes)
    if reference_time < start:
        return Measured.missing(Availability.INSUFFICIENT_HISTORY)
    reference = tape.cut(reference_time)
    if reference <= first:
        return Measured.missing(Availability.INSUFFICIENT_HISTORY)
    return Measured.of((tape.bars[now - 1].close / tape.bars[reference - 1].close - 1) * 100)


def session_vwap(tape: SessionTape, as_of: datetime, scope: AggregationScope) -> Measured:
    """Σ(source vwap × volume) / Σ volume over available bars.

    No synthetic VWAP fallback: a bar with volume and no source VWAP makes the result
    ``NO_SOURCE_VWAP``, never (H+L+C)/3 or the close. The tape does not know where ``vwap`` came
    from; a realtime runtime may fill it from trades (see README, Realtime VWAP contract).
    """
    scoped = tape.scope_range(as_of, scope)
    if scoped is None:
        return Measured.missing(Availability.NO_DATA)
    return tape.vwap(scoped[1], scoped[2])


def high_low_of_day(tape: SessionTape, as_of: datetime, scope: AggregationScope) -> tuple[Measured, Measured]:
    scoped = tape.scope_range(as_of, scope)
    if scoped is None or scoped[2] <= scoped[1]:
        missing = Measured.missing(Availability.NO_DATA)
        return missing, missing
    return Measured.of(tape.high(scoped[1], scoped[2])), Measured.of(tape.low(scoped[1], scoped[2]))


def cumulative_dollar_volume(tape: SessionTape, as_of: datetime, scope: AggregationScope,
                             basis: DollarVolumePriceBasis) -> Measured:
    scoped = tape.scope_range(as_of, scope)
    if scoped is None:
        return Measured.missing(Availability.NO_DATA)
    return tape.dollar_volume(scoped[1], scoped[2], basis)


def rolling_dollar_volume(tape: SessionTape, as_of: datetime, window_minutes: int,
                          scope: AggregationScope, basis: DollarVolumePriceBasis) -> Measured:
    """Dollar volume of bars that became available in ``(as_of - window, as_of]``."""
    scoped = tape.scope_range(as_of, scope)
    if scoped is None:
        return Measured.missing(Availability.NO_DATA)
    start, first, _ = scoped
    window_open = as_of - timedelta(minutes=window_minutes)
    if window_open < start:
        return Measured.missing(Availability.INSUFFICIENT_HISTORY)
    return tape.dollar_volume(*tape.window_range(first, window_open, as_of), basis)


def volume_acceleration(tape: SessionTape, as_of: datetime, window_minutes: int,
                        scope: AggregationScope) -> Measured:
    """Volume in the last clock window over volume in the clock window before it."""
    scoped = tape.scope_range(as_of, scope)
    if scoped is None:
        return Measured.missing(Availability.NO_DATA)
    start, first, _ = scoped
    window = timedelta(minutes=window_minutes)
    if as_of - 2 * window < start:
        return Measured.missing(Availability.INSUFFICIENT_HISTORY)
    recent = tape.volume(*tape.window_range(first, as_of - window, as_of))
    previous = tape.volume(*tape.window_range(first, as_of - 2 * window, as_of - window))
    if previous <= 0:
        return Measured.missing(Availability.ZERO_BASELINE)
    return Measured.of(recent / previous)


def missing_minute_ratio(tape: SessionTape, as_of: datetime, scope: AggregationScope) -> Measured:
    """Share of observable minute slots in ``scope`` that have no actual bar."""
    scoped = tape.scope_range(as_of, scope)
    if scoped is None:
        return Measured.missing(Availability.NO_DATA)
    start, first, now = scoped
    slots = tape.boundaries.elapsed_minute_slots(start, as_of)
    if slots == 0:
        return Measured.missing(Availability.NO_DATA)
    return Measured.of(1 - (now - first) / slots)


def tape_density(ratio: Measured, config: FeatureConfig) -> TapeDensity:
    if ratio.value is None:
        return TapeDensity.UNKNOWN
    if ratio.value <= config.dense_max_missing_ratio:
        return TapeDensity.DENSE
    if ratio.value <= config.sparse_max_missing_ratio:
        return TapeDensity.SPARSE
    return TapeDensity.VERY_SPARSE


def percent_distance(value: Measured, reference: Measured) -> Measured:
    if value.value is None:
        return value
    if reference.value is None:
        return reference
    return Measured.of((value.value / reference.value - 1) * 100)
