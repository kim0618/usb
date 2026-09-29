"""Price-engine tests. The PIT rule and the window-completeness rule are the two that matter most:
a leak makes every downstream number a measurement of the future, and a silently shortened window
makes a 3-day reaction a 1-day one without saying so."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from d4_helpers import sessions, utc

from app.backtest.strategy_h_v2.expectation import price_engine as pe


def test_pit_filter_rejects_a_bar_that_closes_after_the_decision_time():
    series = sessions(5, start=date(2026, 9, 14))
    with pytest.raises(pe.FuturePriceLeakError) as error:
        pe.pit_eligible_series(series, utc(2026, 9, 15, 21, 0))
    assert "2026-09-16" in str(error.value)


def test_pit_filter_accepts_bars_whose_close_precedes_the_decision_time():
    series = sessions(3, start=date(2026, 9, 14))
    kept = pe.pit_eligible_series(series, utc(2026, 9, 17))
    assert set(kept) == set(series)


def test_pit_filter_requires_an_aware_decision_time():
    from datetime import datetime
    with pytest.raises(ValueError):
        pe.pit_eligible_series(sessions(2), datetime(2026, 9, 17))


def test_event_after_the_close_aligns_to_the_next_session():
    series = sessions(5, start=date(2026, 3, 2))
    alignment = pe.align_event(list(series), utc(2026, 3, 3, 22, 0))
    assert alignment.event_session == date(2026, 3, 4)
    assert alignment.prior_session == date(2026, 3, 3)
    assert alignment.alignment_ambiguous is False


def test_event_before_the_close_aligns_to_the_same_session():
    series = sessions(5, start=date(2026, 3, 2))
    alignment = pe.align_event(list(series), utc(2026, 3, 3, 12, 0))
    assert alignment.event_session == date(2026, 3, 3)
    assert alignment.prior_session == date(2026, 3, 2)
    assert alignment.alignment_ambiguous is False


def test_event_between_the_two_close_bounds_is_flagged_ambiguous_not_guessed():
    """20:30Z is after a 20:00Z close and before a 21:00Z close, so which session reacted depends
    on a DST answer this repository does not have. The flag is the honest output."""
    series = sessions(5, start=date(2026, 3, 2))
    alignment = pe.align_event(list(series), utc(2026, 3, 3, 20, 30))
    assert alignment.alignment_ambiguous is True
    assert "20:00Z" in alignment.ambiguity_reason and "21:00Z" in alignment.ambiguity_reason


def test_date_only_precision_is_always_ambiguous():
    series = sessions(5, start=date(2026, 3, 2))
    alignment = pe.align_event(list(series), utc(2026, 3, 3, 12, 0), timestamp_is_exact=False)
    assert alignment.alignment_ambiguous is True
    assert alignment.event_session == date(2026, 3, 4)


def test_event_return_is_measured_from_the_close_before_the_event():
    series = {date(2026, 3, 2): 100.0, date(2026, 3, 3): 110.0, date(2026, 3, 4): 121.0}
    alignment = pe.align_event(list(series), utc(2026, 3, 2, 22, 0))
    assert alignment.prior_session == date(2026, 3, 2)
    assert pe.event_window_return(series, alignment, 0).value == pytest.approx(0.10)


def test_three_session_window_is_incomplete_rather_than_shortened():
    """The whole point: a window that needs a session the series does not have reports
    INCOMPLETE_FUTURE with no value, never the 1-day return wearing a 3-day label."""
    series = {date(2026, 3, 2): 100.0, date(2026, 3, 3): 110.0}
    alignment = pe.align_event(list(series), utc(2026, 3, 2, 22, 0))
    assert alignment.event_session == date(2026, 3, 3)
    window = pe.event_window_return(series, alignment, 2)
    assert window.value is None
    assert window.status == pe.WindowStatus.INCOMPLETE_FUTURE


def test_window_with_no_prior_session_is_incomplete_history():
    series = {date(2026, 3, 2): 100.0, date(2026, 3, 3): 110.0}
    alignment = pe.align_event(list(series), utc(2026, 3, 1, 12, 0))
    assert alignment.status == pe.WindowStatus.INCOMPLETE_HISTORY
    assert pe.event_window_return(series, alignment, 0).value is None


def test_benchmark_adjustment_uses_the_same_two_sessions():
    series = {date(2026, 3, 1): 90.0, date(2026, 3, 2): 100.0, date(2026, 3, 3): 110.0}
    benchmark = {date(2026, 3, 1): 49.0, date(2026, 3, 2): 50.0, date(2026, 3, 3): 52.0}
    alignment = pe.align_event(list(series), utc(2026, 3, 2, 22, 0))
    raw = pe.event_window_return(series, alignment, 0)
    adjusted = pe.benchmark_adjusted(raw, series, benchmark)
    assert adjusted.value == pytest.approx(0.10 - 0.04)


def test_benchmark_missing_one_of_those_sessions_yields_none_not_the_raw_return():
    series = {date(2026, 3, 1): 90.0, date(2026, 3, 2): 100.0, date(2026, 3, 3): 110.0}
    benchmark = {date(2026, 3, 2): 50.0}
    alignment = pe.align_event(list(series), utc(2026, 3, 2, 22, 0))
    raw = pe.event_window_return(series, alignment, 0)
    assert pe.benchmark_adjusted(raw, series, benchmark).value is None


def test_pre_event_return_ends_before_the_event_session():
    series = {date(2026, 3, 1) + timedelta(days=i): 100.0 + i for i in range(10)}
    alignment = pe.align_event(list(series), utc(2026, 3, 5, 22, 0))
    window = pe.pre_event_return(series, alignment, 3)
    assert window.to_session == date(2026, 3, 5)
    assert window.from_session == date(2026, 3, 2)


def test_realized_volatility_is_none_not_zero_when_history_is_too_short():
    assert pe.realized_volatility({date(2026, 3, 2): 100.0}) is None


def test_realized_volatility_of_a_constant_series_is_zero():
    series = {date(2026, 3, 1) + timedelta(days=i): 100.0 for i in range(10)}
    assert pe.realized_volatility(series) == pytest.approx(0.0)


def test_price_level_context_on_an_empty_series_reports_no_values():
    context = pe.price_level_context({}, {})
    assert context.last_close is None
    assert context.sessions_available == 0
    assert context.return_1m.value is None
