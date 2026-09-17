"""PIT mutation tests.

Two halves, both required:

1. Invariance: for every as-of time on a grid, a snapshot computed on the full stored tape
   (with future bars, a final-HOD print and a future split injected) equals the snapshot
   computed on a tape that physically holds only bars available at that time.
2. Sensitivity: the same harness, pointed at deliberately leaky implementations of the
   mistakes this layer exists to prevent, does report violations. A harness that cannot fail
   proves nothing.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from math import prod
from typing import Any

import pytest

from app.strategy_b.config import RvolConfig, ScopeConfig, StrategyBConfig
from app.strategy_b.errors import PointInTimeViolation
from app.strategy_b.features import SessionTape, clock_return, high_low_of_day, last_price
from app.strategy_b.models import MomentumBar
from app.strategy_b.rvol import time_of_day_rvol
from app.strategy_b.scope import PriorDailyBar, ScopeInputs, TickerMetadataAsOf, evaluate_research_scope
from app.strategy_b.session import AggregationScope
from app.strategy_b.snapshot import compute_feature_snapshot
from app.strategy_b.split_adjustment import SplitRecord, split_adjustment
from tests.strategy_b.fixtures import (
    D, dense_regular_session, et, flat_volume_profile, future_split_leak_case, halt_like_gap_session,
    make_bar, make_tape, past_session_dates, premarket_sparse_session, reverse_split_day,
    scope_inputs, sparse_low_liquidity_session,
)


LOCAL = AggregationScope.SESSION_LOCAL
EXTENDED = AggregationScope.EXTENDED_DAY
CONFIG = StrategyBConfig()
HISTORY = [flat_volume_profile(d, 500.0) for d in past_session_dates(20)]


def grid(start: datetime, end: datetime, step_seconds: int = 23) -> list[datetime]:
    """As-of times that land both on and between minute boundaries."""
    count = int((end - start).total_seconds() // step_seconds) + 1
    moments = [start + timedelta(seconds=k * step_seconds) for k in range(count)]
    return sorted(set(moments) | {start.replace(second=0) + timedelta(minutes=m)
                                  for m in range(int((end - start).total_seconds() // 60) + 1)})


def truncated(tape: SessionTape, as_of: datetime) -> SessionTape:
    return SessionTape(tape.symbol, tape.boundaries, [b for b in tape.bars if b.available_at <= as_of])


def pit_violations(feature: Callable[[SessionTape, datetime], Any], tape: SessionTape,
                   moments: list[datetime]) -> list[datetime]:
    return [m for m in moments if feature(tape, m) != feature(truncated(tape, m), m)]


def with_future_noise(bars: list[MomentumBar]) -> list[MomentumBar]:
    """Append the day's final high right after the fixture, a late blow-off and an after-hours print.

    Every fixture ends by 10:29, so all three are strictly later than its last bar.
    """
    after = bars[-1].timestamp + timedelta(minutes=1)
    return bars + [
        make_bar(after.hour, after.minute, 99.0, 9e8, high=999.0),
        make_bar(15, 59, 150.0, 5e8, high=160.0, low=1.0),
        make_bar(17, 45, 150.0, 1e6),
    ]


FIXTURES = {
    "dense_regular_session": (dense_regular_session, et(9, 25), et(10, 40)),
    "sparse_low_liquidity_session": (sparse_low_liquidity_session, et(9, 28), et(10, 0)),
    "premarket_sparse_session": (premarket_sparse_session, et(4, 0), et(9, 40)),
    "halt_like_gap_session": (halt_like_gap_session, et(9, 28), et(10, 0)),
    "reverse_split_day": (lambda: reverse_split_day().bars, et(9, 28), et(9, 45)),
}


# ---- invariance ------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_snapshot_never_changes_when_future_bars_exist(name: str) -> None:
    build, start, end = FIXTURES[name]
    full = make_tape(with_future_noise(build()))
    future_split, _ = future_split_leak_case()

    def snapshot(tape: SessionTape, as_of: datetime):
        return compute_feature_snapshot(tape, as_of, CONFIG, rvol_history=HISTORY, splits=[future_split])

    moments = grid(start, end) + [et(16, 0), et(16, 0, 30), et(19, 59, 30), et(20, 0, 30)]
    assert pit_violations(snapshot, full, moments) == []


def test_future_final_hod_does_not_move_the_as_of_hod() -> None:
    bars = sparse_low_liquidity_session()
    plain = make_tape(bars)
    spiked = make_tape(bars + [make_bar(9, 50, 30.0, 1e6, high=50.0)])
    for as_of in grid(et(9, 30), et(9, 50, 59)):
        assert high_low_of_day(plain, as_of, LOCAL) == high_low_of_day(spiked, as_of, LOCAL)
    assert high_low_of_day(spiked, et(9, 51), LOCAL)[0].value == 50.0


def test_future_split_does_not_change_past_features_or_factors() -> None:
    split, history = future_split_leak_case()
    tape = make_tape(dense_regular_session())
    for as_of in grid(et(9, 30), et(10, 30), 97):
        assert (compute_feature_snapshot(tape, as_of, CONFIG, rvol_history=history, splits=[split])
                == compute_feature_snapshot(tape, as_of, CONFIG, rvol_history=history))
    for days_back in range(1, 30):
        observed = D - timedelta(days=days_back)
        assert split_adjustment([split], observed_date=observed, current_date=D, recent_split_calendar_days=5) \
            == split_adjustment([], observed_date=observed, current_date=D, recent_split_calendar_days=5)


def test_scope_cannot_receive_decision_day_data() -> None:
    base = scope_inputs()
    day_bar = PriorDailyBar(D, close=500.0, volume=1e9)
    with pytest.raises(PointInTimeViolation):
        ScopeInputs(D, base.metadata, base.daily_history + (day_bar,))
    with pytest.raises(PointInTimeViolation):
        ScopeInputs(D, base.metadata, base.daily_history + (PriorDailyBar(D + timedelta(days=1), 1.0, 1.0),))
    same_day_meta = TickerMetadataAsOf(base.metadata.symbol, D, "CS", "XNAS", "stocks", base.metadata.listing_status)
    with pytest.raises(PointInTimeViolation):
        ScopeInputs(D, same_day_meta, base.daily_history)
    assert evaluate_research_scope(base, ScopeConfig()).included


def test_rvol_refuses_history_that_includes_today() -> None:
    tape = make_tape(dense_regular_session())
    with pytest.raises(PointInTimeViolation):
        time_of_day_rvol(tape, et(9, 40), HISTORY + [flat_volume_profile(D, 500.0)], splits=(),
                         config=RvolConfig())


# ---- sensitivity: injected leaks must be caught ----------------------------------------

def leaky_final_hod(tape: SessionTape, as_of: datetime) -> float | None:
    """Mistake: the session high over every stored bar, i.e. the day's final HOD."""
    scoped = tape.scope_range(as_of, LOCAL)
    if scoped is None:
        return None
    session = tape.boundaries.classify(as_of)
    highs = [b.high for b in tape.bars if b.session is session]
    return max(highs) if highs and scoped[2] > scoped[1] else None


def leaky_bar_open_cut(tape: SessionTape, as_of: datetime) -> float | None:
    """Mistake: treating a bar as known at its open time instead of open + 1 minute."""
    bars = [b for b in tape.bars if b.timestamp <= as_of]
    return bars[-1].close if bars else None


def leaky_split_factor(splits: list[SplitRecord], observed, current) -> float:
    """Mistake: ``adjusted=true`` semantics, applying every split after the observed date."""
    return prod(s.price_factor for s in splits if s.execution_date > observed)


def bar_count_return(tape: SessionTape, as_of: datetime, minutes: int) -> float | None:
    """Mistake: "N bars ago" as "N minutes ago"."""
    bars = tape.available_bars(as_of)
    if len(bars) <= minutes:
        return None
    return (bars[-1].close / bars[-1 - minutes].close - 1) * 100


def test_harness_catches_injected_final_hod_leak() -> None:
    full = make_tape(with_future_noise(sparse_low_liquidity_session()))
    moments = grid(et(9, 32), et(9, 58))
    assert pit_violations(lambda t, m: high_low_of_day(t, m, LOCAL), full, moments) == []
    assert pit_violations(leaky_final_hod, full, moments)


def test_harness_catches_injected_bar_open_availability_leak() -> None:
    full = make_tape(dense_regular_session())
    moments = grid(et(9, 30), et(9, 45))
    assert pit_violations(lambda t, m: last_price(t, m, EXTENDED), full, moments) == []
    assert pit_violations(leaky_bar_open_cut, full, moments)
    # At 09:35:30 the 09:35 bar is still forming; the leak already reads its close.
    assert leaky_bar_open_cut(full, et(9, 35, 30)) == full.bars[5].close
    assert last_price(full, et(9, 35, 30), EXTENDED)[0].value == full.bars[4].close


def test_snapshot_harness_fails_when_a_leak_is_injected_into_the_real_code_path(monkeypatch) -> None:
    from bisect import bisect_right

    import app.strategy_b.snapshot as snapshot_module

    full = make_tape(with_future_noise(sparse_low_liquidity_session()))
    moments = grid(et(9, 30), et(9, 45))

    def snapshot(tape: SessionTape, as_of: datetime):
        return compute_feature_snapshot(tape, as_of, CONFIG, rvol_history=HISTORY)

    assert pit_violations(snapshot, full, moments) == []
    with monkeypatch.context() as patch:
        patch.setattr(SessionTape, "cut", lambda self, as_of: bisect_right(self.timestamps, as_of))
        assert pit_violations(snapshot, full, moments)
    with monkeypatch.context() as patch:
        def final_hod(tape, as_of, scope):
            hod, lod = high_low_of_day(tape, as_of, scope)
            if hod.value is None:
                return hod, lod
            session = tape.boundaries.classify(as_of)
            return type(hod).of(max(b.high for b in tape.bars if b.session is session)), lod
        patch.setattr(snapshot_module, "high_low_of_day", final_hod)
        assert pit_violations(snapshot, full, moments)


def test_future_split_leak_is_caught_when_injected() -> None:
    split, _ = future_split_leak_case()
    observed = D - timedelta(days=5)
    assert leaky_split_factor([split], observed, D) != leaky_split_factor([], observed, D)
    assert split_adjustment([split], observed_date=observed, current_date=D,
                            recent_split_calendar_days=5).price_factor == 1.0


def test_sparse_tape_bar_count_distortion_is_caught() -> None:
    tape = make_tape(sparse_low_liquidity_session())
    distorted = [m for m in grid(et(9, 36), et(9, 58))
                 if clock_return(tape, m, 1, EXTENDED).value != bar_count_return(tape, m, 1)]
    assert distorted, "on a sparse tape, one bar back is not one minute back"
    dense = make_tape(dense_regular_session())
    for as_of in (et(9, 45), et(10, 0), et(10, 20)):
        assert clock_return(dense, as_of, 1, LOCAL).value == pytest.approx(bar_count_return(dense, as_of, 1))
