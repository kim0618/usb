"""Time-of-day RVOL status and PIT split adjustment."""

from datetime import date, timedelta

import pytest

from app.strategy_b.config import RvolConfig
from app.strategy_b.errors import PointInTimeViolation
from app.strategy_b.models import Availability, RvolStatus
from app.strategy_b.rvol import build_volume_profile, time_of_day_rvol
from app.strategy_b.session import AggregationScope
from app.strategy_b.split_adjustment import (
    SplitRecord, adjusted_previous_close, known_splits, split_adjustment,
)
from tests.strategy_b.fixtures import (
    D, et, flat_volume_profile, future_split_leak_case, ipo_short_history, make_bar, make_tape,
    past_session_dates, reverse_split_day,
)


def today_tape(per_minute: float = 300.0, count: int = 10):
    return make_tape([make_bar(9, 30 + k, 10.0, per_minute) for k in range(count)])


# ---- RVOL ------------------------------------------------------------------------------

def test_rvol_full_with_twenty_sessions() -> None:
    history = [flat_volume_profile(d, 100.0) for d in past_session_dates(20)]
    result = time_of_day_rvol(today_tape(), et(9, 35), history, splits=(), config=RvolConfig())
    # today 5 × 300 = 1500 by 09:35; each past session 5 × 100 = 500 by the same elapsed time.
    assert (result.value, result.status, result.sessions_used, result.sessions_required) == (3.0, RvolStatus.FULL, 20, 20)


def test_rvol_uses_only_the_most_recent_lookback_sessions() -> None:
    old = [flat_volume_profile(d, 10_000.0) for d in past_session_dates(25)[:5]]
    recent = [flat_volume_profile(d, 100.0) for d in past_session_dates(20)]
    result = time_of_day_rvol(today_tape(), et(9, 35), old + recent, splits=(), config=RvolConfig())
    assert result.value == 3.0 and result.sessions_used == 20


def test_rvol_partial_and_unknown_thresholds() -> None:
    config = RvolConfig()
    seven = [flat_volume_profile(d, 100.0) for d in past_session_dates(7)]
    partial = time_of_day_rvol(today_tape(), et(9, 35), seven, splits=(), config=config)
    assert (partial.value, partial.status, partial.sessions_used) == (3.0, RvolStatus.PARTIAL, 7)
    five = time_of_day_rvol(today_tape(), et(9, 35), seven[-5:], splits=(), config=config)
    assert five.status is RvolStatus.PARTIAL
    four = time_of_day_rvol(today_tape(), et(9, 35), seven[-4:], splits=(), config=config)
    assert (four.value, four.status, four.availability) == (None, RvolStatus.UNKNOWN, Availability.INSUFFICIENT_HISTORY)


def test_ipo_short_history_rvol_is_unknown() -> None:
    _, profiles = ipo_short_history()
    result = time_of_day_rvol(today_tape(), et(9, 35), profiles, splits=(), config=RvolConfig())
    assert result.status is RvolStatus.UNKNOWN and result.sessions_used == 3


def test_rvol_zero_baseline_is_unknown_not_infinite() -> None:
    history = [flat_volume_profile(d, 100.0) for d in past_session_dates(20)]
    # 09:30:30: nothing is available in today's tape or in any past curve.
    result = time_of_day_rvol(today_tape(), et(9, 30, 30), history, splits=(), config=RvolConfig())
    assert (result.status, result.availability) == (RvolStatus.UNKNOWN, Availability.ZERO_BASELINE)


def test_rvol_refuses_same_day_or_future_history_and_mismatched_scope() -> None:
    history = [flat_volume_profile(d, 100.0) for d in past_session_dates(19)]
    with pytest.raises(PointInTimeViolation):
        time_of_day_rvol(today_tape(), et(9, 35), history + [flat_volume_profile(D, 100.0)],
                         splits=(), config=RvolConfig())
    with pytest.raises(ValueError, match="repeats"):
        time_of_day_rvol(today_tape(), et(9, 35), history + history[-1:], splits=(), config=RvolConfig())
    with pytest.raises(ValueError, match="scope"):
        time_of_day_rvol(today_tape(), et(9, 35), history, splits=(),
                         config=RvolConfig(scope=AggregationScope.PREMARKET_AND_REGULAR))


def test_rvol_rescales_history_across_a_reverse_split() -> None:
    dates = past_session_dates(20)
    split_day = dates[10]
    # Before the 1-for-10 reverse split the stock traded 10x the shares for the same interest.
    history = [flat_volume_profile(d, 1000.0 if d < split_day else 100.0) for d in dates]
    split = SplitRecord(execution_date=split_day, split_from=10, split_to=1)
    unadjusted = time_of_day_rvol(today_tape(), et(9, 35), history, splits=(), config=RvolConfig())
    adjusted = time_of_day_rvol(today_tape(), et(9, 35), history, splits=(split,), config=RvolConfig())
    assert adjusted.value == pytest.approx(3.0) and adjusted.split_adjusted
    assert unadjusted.value == pytest.approx(1500 / ((10 * 5000 + 10 * 500) / 20))
    assert not unadjusted.split_adjusted


def test_rvol_ignores_a_split_after_today() -> None:
    split, history = future_split_leak_case()
    with_future = time_of_day_rvol(today_tape(), et(9, 35), history, splits=(split,), config=RvolConfig())
    without = time_of_day_rvol(today_tape(), et(9, 35), history, splits=(), config=RvolConfig())
    assert with_future == without
    assert with_future.split_adjusted is False


def test_premarket_and_regular_rvol_scope_counts_premarket_volume() -> None:
    scope = AggregationScope.PREMARKET_AND_REGULAR
    config = RvolConfig(scope=scope)
    past = []
    for d in past_session_dates(20):
        bars = [make_bar(9, 0, 10.0, 400.0, day=d)] + [make_bar(9, 30 + k, 10.0, 100.0, day=d) for k in range(10)]
        past.append(build_volume_profile(make_tape(bars, d), scope))
    today = make_tape([make_bar(9, 0, 10.0, 800.0)] + [make_bar(9, 30 + k, 10.0, 100.0) for k in range(10)])
    result = time_of_day_rvol(today, et(9, 35), past, splits=(), config=config)
    assert result.value == pytest.approx((800 + 500) / (400 + 500))


# ---- split adjustment ------------------------------------------------------------------

def test_no_split_is_identity() -> None:
    adjustment = split_adjustment((), observed_date=D - timedelta(days=30), current_date=D,
                                  recent_split_calendar_days=5)
    assert (adjustment.price_factor, adjustment.share_factor, adjustment.adjusted,
            adjustment.split_on_day, adjustment.recent_split) == (1, 1, False, False, False)


def test_forward_split_factors() -> None:
    split = SplitRecord(date(2026, 3, 5), split_from=1, split_to=2)
    adjustment = split_adjustment([split], observed_date=date(2026, 3, 2), current_date=D,
                                  recent_split_calendar_days=5)
    assert (adjustment.price_factor, adjustment.share_factor) == (0.5, 2.0)
    assert adjustment.recent_split and not adjustment.split_on_day


def test_same_day_reverse_split_applies_to_previous_close_not_to_todays_bars() -> None:
    case = reverse_split_day()
    previous = split_adjustment([case.split], observed_date=case.previous_close_date, current_date=D,
                                recent_split_calendar_days=5)
    assert (previous.price_factor, previous.share_factor, previous.split_on_day) == (10.0, 0.1, True)
    same_day = split_adjustment([case.split], observed_date=D, current_date=D, recent_split_calendar_days=5)
    assert (same_day.price_factor, same_day.adjusted, same_day.split_on_day) == (1.0, False, True)
    prior_close = adjusted_previous_close(case.previous_raw_close, close_date=case.previous_close_date,
                                          current_date=D, splits=[case.split], recent_split_calendar_days=5)
    raw_gap = (case.bars[0].open / case.previous_raw_close - 1) * 100
    adjusted_gap = (case.bars[0].open / prior_close - 1) * 100
    assert prior_close == pytest.approx(5.0)
    assert raw_gap == pytest.approx(920.0)  # the fake "surge" a raw scan would see
    assert adjusted_gap == pytest.approx(2.0)


def test_multiple_splits_compose_only_after_the_observed_date() -> None:
    forward = SplitRecord(date(2026, 1, 10), split_from=1, split_to=2)
    reverse = SplitRecord(date(2026, 3, 1), split_from=10, split_to=1)
    both = split_adjustment([reverse, forward], observed_date=date(2026, 1, 5), current_date=D,
                            recent_split_calendar_days=5)
    assert both.price_factor == pytest.approx(5.0) and both.share_factor == pytest.approx(0.2)
    assert both.applied == (forward, reverse)
    only_reverse = split_adjustment([forward, reverse], observed_date=date(2026, 2, 1), current_date=D,
                                    recent_split_calendar_days=5)
    assert only_reverse.applied == (reverse,)


def test_future_split_is_never_applied_to_the_past() -> None:
    split, _ = future_split_leak_case()
    adjustment = split_adjustment([split], observed_date=D - timedelta(days=10), current_date=D,
                                  recent_split_calendar_days=5)
    assert (adjustment.price_factor, adjustment.share_factor, adjustment.applied) == (1.0, 1.0, ())
    assert not adjustment.split_on_day and not adjustment.recent_split
    assert known_splits([split], D) == ()


def test_split_records_are_validated() -> None:
    with pytest.raises(ValueError):
        SplitRecord(D, split_from=2, split_to=2)
    with pytest.raises(ValueError):
        SplitRecord(D, split_from=0, split_to=1)
    with pytest.raises(ValueError, match="share execution_date"):
        known_splits([SplitRecord(D, 1, 2), SplitRecord(D, 10, 1)], D)
    with pytest.raises(PointInTimeViolation):
        split_adjustment((), observed_date=D + timedelta(days=1), current_date=D, recent_split_calendar_days=5)
    with pytest.raises(PointInTimeViolation):
        adjusted_previous_close(1.0, close_date=D, current_date=D, splits=(), recent_split_calendar_days=5)
