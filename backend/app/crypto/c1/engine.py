"""Evaluating C1 on the grid, and turning a run of true bars into one signal.

Two separate jobs, deliberately not mixed:

`evaluate` answers "did the contract hold at the close of this bar", and nothing else. It is a
pure function of the grid and has no memory.

`C1Engine.advance` answers "is this a new event". The study had no need for that question - it
counted every one of the 18,498 bars inside its events as a sample - but an operator cannot act on
a bar, and a marker per bar would put 40 arrows on one candle. So one signal covers one
contiguous run of true bars (contract.OVERLAP_POLICY), the condition has to lapse before another
can start, and the signal id is derived from the trigger instant so replaying the same bars
produces the same ids instead of duplicates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence

from .contract import (
    DAY_BARS, DIRECTION, ENTRY_OFFSET_BARS, MINUTE_MS, OFFICIAL_HORIZON_MIN, OI_CHANGE_LAG_MIN,
    OI_MAX_STALENESS_MS, STRATEGY, VOL_REGIME_HIGH_CUTOFF, VOL_WINDOW_BARS,
)
from .features import (
    NAN, RollingMoments, bucket_cutoffs, bucket_index, in_b1, is_nan, oi_falling, rolling_std,
    vol_regime,
)
from .grid import Grid, bucket_window
from .models import Completeness, FeatureSnapshot, Signal


@dataclass(frozen=True)
class Evaluation:
    """One bar's verdict. `on` is only ever true when `eligible` is true."""
    bar_ms: int
    decided_at_ms: int
    on: bool
    eligible: bool
    state: str
    features: FeatureSnapshot
    completeness: Completeness

    @property
    def signal_id(self) -> str:
        return signal_id_for(self.decided_at_ms)


def signal_id_for(decided_at_ms: int) -> str:
    """Deterministic, so a restart that re-reads the same bars recognises its own signals."""
    return f"{STRATEGY}-{DIRECTION}-{decided_at_ms}"


class CutoffCache:
    """Bucket edges per UTC day. The window is 43,200 rows and the edges change once a day, so
    they are computed once a day and not once a bar."""

    def __init__(self) -> None:
        self._by_day: dict[int, tuple[float, ...] | None] = {}

    def get(self, grid: Grid, index: int) -> tuple[float, ...] | None:
        day = grid.utc_day(index)
        if day not in self._by_day:
            window = bucket_window(grid, index)
            self._by_day[day] = None if window is None else bucket_cutoffs(window)
        return self._by_day[day]

    def clear(self) -> None:
        self._by_day.clear()


def oi_change_at(grid: Grid, index: int, lag: int = OI_CHANGE_LAG_MIN) -> float:
    """ln(OI[index] / OI[index - lag]) read by position, without copying the prefix.

    `features.oi_log_change` states the same rule over a sequence and the tests hold the two
    against each other; this is the one the replay calls, because slicing the prefix on every bar
    turns a walk of the grid into quadratic work.
    """
    if index < lag:
        return NAN
    now, before = grid.oi[index], grid.oi[index - lag]
    if is_nan(now) or is_nan(before) or now <= 0 or before <= 0:
        return NAN
    import math

    return math.log(now / before)


def evaluate(grid: Grid, index: int, cutoffs: tuple[float, ...] | None,
             moments: RollingMoments | None = None) -> Evaluation:
    """Read the three conditions at the close of bar `index`.

    An input that is absent lands in `missing` and makes the bar NOT_ELIGIBLE. It never becomes a
    false condition, and no value is carried forward to stand in for it beyond the single 5m carry
    the contract itself allows.

    `moments` is an optional sliding accumulator for the 24 h volatility. Given one, the bar costs
    constant time; without one the window is summed from scratch, which is the definition the
    accumulator resyncs against.
    """
    bar_ms = grid.ts(index)
    decided_at_ms = bar_ms + MINUTE_MS
    missing: list[str] = []

    s1 = grid.s1[index]
    if is_nan(s1):
        missing.append("SPOT_BASIS")
    if cutoffs is None:
        missing.append("BASIS_BUCKET_CUTOFFS")

    change = oi_change_at(grid, index, OI_CHANGE_LAG_MIN)
    oi_now = grid.oi[index] if index < len(grid.oi) else NAN
    oi_lagged = grid.oi[index - OI_CHANGE_LAG_MIN] if index >= OI_CHANGE_LAG_MIN else NAN
    if is_nan(change):
        missing.append("OPEN_INTEREST_1H_CHANGE")
    else:
        # The as-of join would carry a value indefinitely; a live feed that stopped answering must
        # not be read as a flat open interest. See contract.OI_MAX_STALENESS_MS.
        stamp = grid.oi_stamp[index] if index < len(grid.oi_stamp) else -1
        if stamp < 0 or decided_at_ms - stamp > OI_MAX_STALENESS_MS:
            missing.append("OPEN_INTEREST_STALE")

    if moments is not None:
        rv = moments.at(index)
    elif index + 1 >= VOL_WINDOW_BARS:
        rv = rolling_std(grid.returns()[index + 1 - VOL_WINDOW_BARS: index + 1], VOL_WINDOW_BARS)
    else:
        rv = NAN
    if is_nan(rv):
        missing.append("VOLATILITY_24H")

    regime = vol_regime(rv)
    basis_ok = in_b1(s1, cutoffs)
    oi_ok = oi_falling(change)
    vol_ok = regime == "HIGH"
    features = FeatureSnapshot(
        s1=s1, s1_bucket=bucket_index(s1, cutoffs),
        s1_b1_cutoff=cutoffs[0] if cutoffs else NAN,
        s1_boundary_ms=((bar_ms + MINUTE_MS) // (5 * MINUTE_MS)) * (5 * MINUTE_MS),
        perp_5m_close=grid.perp_5m[index], spot_5m_close=grid.spot_5m[index],
        oi_change_1h=change, oi_now=oi_now, oi_lagged=oi_lagged,
        rv_24h=rv, vol_regime=regime, vol_high_cutoff=VOL_REGIME_HIGH_CUTOFF,
        condition_basis_b1=basis_ok, condition_oi_falling=oi_ok, condition_vol_high=vol_ok,
    )
    eligible = not missing
    completeness = Completeness(eligible=eligible, missing=tuple(missing),
                                source_timestamps={"bar_ms": bar_ms,
                                                   "basis_boundary_ms": features.s1_boundary_ms})
    on = eligible and basis_ok and oi_ok and vol_ok
    state = "ON" if on else ("OFF" if eligible else "NOT_ELIGIBLE")
    return Evaluation(bar_ms=bar_ms, decided_at_ms=decided_at_ms, on=on, eligible=eligible,
                      state=state, features=features, completeness=completeness)


def warmup_bars() -> int:
    """How much history the first decidable bar needs: 30 whole days for the bucket window, plus
    the 24 h volatility window and the hour the open-interest change looks back over."""
    from .contract import NORM_DAYS

    return NORM_DAYS * DAY_BARS + max(VOL_WINDOW_BARS, OI_CHANGE_LAG_MIN) + 1


def new_signal(evaluation: Evaluation, grid: Grid, index: int) -> Signal:
    """A signal from the bar that opened the event.

    Entry and exit instants come from the contract's execution rule - enter at the open of t+1,
    leave at the open of t+1+240 - and the entry price is filled in when that bar exists, never
    guessed from the signal bar's close.
    """
    entry_index = index + ENTRY_OFFSET_BARS
    entry_at = grid.ts(entry_index)
    return Signal(
        signal_id=evaluation.signal_id,
        triggered_at_ms=evaluation.decided_at_ms,
        signal_bar_ms=evaluation.bar_ms,
        signal_price=grid.closes[index],
        official_entry_at_ms=entry_at,
        planned_exit_at_ms=entry_at + OFFICIAL_HORIZON_MIN * MINUTE_MS,
        official_entry_price=grid.opens[entry_index] if grid.has_bar(entry_index) else None,
        features=evaluation.features,
        completeness=evaluation.completeness,
    )


class C1Engine:
    """Walks the grid once, in order, emitting one signal per contiguous event.

    `armed` is the whole dedupe mechanism: it goes false while an event is running and only comes
    back when a bar is evaluated and is not ON. A bar that is NOT_ELIGIBLE ends the event like any
    other non-true bar - it is not a true bar, and pretending the event continued through unknown
    inputs would be inventing one.
    """

    def __init__(self) -> None:
        self.cutoffs = CutoffCache()
        self.armed = True
        self.last_decided_at_ms: int | None = None
        self.last_evaluation: Evaluation | None = None
        self._moments: tuple[Grid, RollingMoments] | None = None

    def _moments_for(self, grid: Grid) -> RollingMoments:
        """The sliding volatility for this grid, rebuilt when the grid is replaced.

        The cached pair holds the grid itself and the identity is compared with `is`. Keying on
        `id(grid)` instead would be wrong in exactly the case the runtime creates every tick: the
        previous grid is dropped, a new one is allocated, and CPython may hand back the same
        address - at which point a stale accumulator over the *previous* returns would be
        accepted as current. Holding the reference makes that impossible.
        """
        if self._moments is None or self._moments[0] is not grid:
            self._moments = (grid, RollingMoments(grid.returns()))
        return self._moments[1]

    def evaluate_at(self, grid: Grid, index: int) -> Evaluation:
        """A single bar, summed from scratch. The accumulator is only for walking a range."""
        return evaluate(grid, index, self.cutoffs.get(grid, index))

    def walk(self, grid: Grid, *, start_index: int,
             stop_index: int) -> Iterator[tuple[int, Evaluation, Signal | None]]:
        """Every bar in [start_index, stop_index), in order, with the signal it opened or None.

        The lifecycle lives here rather than in `advance` so that a replay can see the verdict of
        each bar - which is what the parity gate compares - and not only the events.
        """
        moments = self._moments_for(grid)
        for index in range(max(start_index, 0), min(stop_index, len(grid))):
            evaluation = evaluate(grid, index, self.cutoffs.get(grid, index), moments)
            self.last_evaluation = evaluation
            self.last_decided_at_ms = evaluation.decided_at_ms
            signal = None
            if evaluation.on:
                if self.armed:
                    self.armed = False
                    signal = new_signal(evaluation, grid, index)
            else:
                self.armed = True
            yield index, evaluation, signal

    def advance(self, grid: Grid, *, start_index: int, stop_index: int) -> Iterator[Signal]:
        """Evaluate bars [start_index, stop_index) in order and yield the signals they open."""
        for _, _, signal in self.walk(grid, start_index=start_index, stop_index=stop_index):
            if signal is not None:
                yield signal

    def resume_from(self, decided_at_ms: int | None, armed: bool) -> None:
        """Restore the dedupe state after a restart so an event that was already running does not
        trigger a second signal on the next bar."""
        self.last_decided_at_ms = decided_at_ms
        self.armed = armed
