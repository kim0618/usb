"""The four features, the bucket rule, and point-in-time safety.

The contract claims all four formulas are inherited verbatim from D5. That claim is tested here
against the D5 implementation that actually produced the research results, not against a restated
formula, because a restatement can drift while the words stay the same.

The PIT tests are the other half. Each one appends bars after the decision bar and asserts the
decision bar's value does not move. That is a structural property of trailing windows, and it is
worth asserting because a single stray centred window would silently invalidate D6-C.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.crypto.research import features as D5F
from app.crypto.research.d6 import features as F
from app.crypto.research.d6.model import BarWindow

from tests.crypto.d6_fixtures import DAY_BARS, synthetic_grid, window


@pytest.fixture(scope="module")
def grid() -> dict[str, np.ndarray]:
    return synthetic_grid(days=32, oi_slope=-1e-7)


# --- verbatim against D5 -------------------------------------------------------------------

def test_f_basis_matches_d5_fund_premium(grid):
    d5 = D5F.compute_features({
        "close": grid["close"], "open": grid["close"], "high": grid["close"],
        "low": grid["close"], "turnover": np.ones(len(grid["close"])),
        "oi": grid["oi"], "index_close": grid["index_close"], "ts": grid["ts"],
        "funding_ts": np.array([grid["ts"][0]], dtype=np.int64),
        "funding_rate": np.array([0.0]), "funding_last": np.zeros(len(grid["close"])),
    })
    np.testing.assert_allclose(F.f_basis(grid["close"], grid["index_close"]),
                               d5["fund_premium"], rtol=0, atol=0)


def test_f_oi1h_matches_d5_oi_chg_60(grid):
    d5 = D5F.compute_features({
        "close": grid["close"], "open": grid["close"], "high": grid["close"],
        "low": grid["close"], "turnover": np.ones(len(grid["close"])),
        "oi": grid["oi"], "index_close": grid["index_close"], "ts": grid["ts"],
        "funding_ts": np.array([grid["ts"][0]], dtype=np.int64),
        "funding_rate": np.array([0.0]), "funding_last": np.zeros(len(grid["close"])),
    })
    np.testing.assert_allclose(F.f_oi1h(grid["oi"]), d5["oi_chg_60"], rtol=0, atol=0)


def test_f_drop1h_matches_d5_trend_ret_60(grid):
    d5 = D5F.compute_features({
        "close": grid["close"], "open": grid["close"], "high": grid["close"],
        "low": grid["close"], "turnover": np.ones(len(grid["close"])),
        "oi": grid["oi"], "index_close": grid["index_close"], "ts": grid["ts"],
        "funding_ts": np.array([grid["ts"][0]], dtype=np.int64),
        "funding_rate": np.array([0.0]), "funding_last": np.zeros(len(grid["close"])),
    })
    np.testing.assert_allclose(F.f_drop1h(grid["close"]), d5["trend_ret_60"], rtol=0, atol=0)


def test_f_rv24h_matches_the_d5_regime_volatility(grid):
    """D5 builds its volatility label from `_rstd(r1, 1440)`; f_rv24h must be that same series."""
    ln_c = np.log(grid["close"])
    expected = D5F._rstd(ln_c - D5F._lag(ln_c, 1), D5F.DAY_BARS)
    np.testing.assert_allclose(F.f_rv24h(grid["close"]), expected, rtol=0, atol=0)


def test_bucket_assignment_matches_d5_bucketize(grid):
    """Same cutoffs and the same boundary convention, day by day."""
    values = F.f_basis(grid["close"], grid["index_close"])
    n_days = len(values) // DAY_BARS
    d5_buckets = D5F.bucketize(values, n_days)
    checked = 0
    for day in range(30, n_days):
        index = day * DAY_BARS + 17  # an arbitrary but fixed minute inside the day
        history = F.previous_days_slice(grid["ts"], int(grid["ts"][index]), 30)
        cutoffs, fraction = F.bucket_cutoffs(values[history], (0.10, 0.30, 0.70, 0.90),
                                             0.5, 30 * DAY_BARS)
        assignment = F.assign_bucket(float(values[index]), cutoffs, fraction)
        expected = int(d5_buckets[index])
        assert assignment.bucket == expected + 1, f"day {day}"
        checked += 1
    assert checked >= 2


# --- boundary behaviour --------------------------------------------------------------------

def test_bucket_boundary_follows_searchsorted_right():
    """A value exactly on a cutoff lands in the bucket above it, as D5's convention dictates."""
    cutoffs = np.array([1.0, 2.0, 3.0, 4.0])
    assert F.assign_bucket(0.999, cutoffs).bucket == 1
    assert F.assign_bucket(1.0, cutoffs).bucket == 2      # on the 10th percentile, not B1
    assert F.assign_bucket(1.001, cutoffs).bucket == 2
    assert F.assign_bucket(4.0, cutoffs).bucket == 5
    assert F.assign_bucket(9.0, cutoffs).bucket == 5


def test_bucket_is_none_when_the_window_is_too_sparse():
    history = np.full(30 * DAY_BARS, np.nan)
    history[: 30 * DAY_BARS // 4] = np.arange(30 * DAY_BARS // 4)
    cutoffs, fraction = F.bucket_cutoffs(history, (0.10, 0.30, 0.70, 0.90), 0.5, 30 * DAY_BARS)
    assert cutoffs is None
    assert fraction == pytest.approx(0.25)
    assert F.assign_bucket(1.0, cutoffs, fraction).bucket is None


def test_sparse_denominator_is_the_nominal_window_not_the_slice():
    """D5 divides by the fixed 30 day width, so a short slice must be refused, not rescaled."""
    history = np.arange(1000, dtype=float)  # every value finite, but far short of 30 days
    cutoffs, fraction = F.bucket_cutoffs(history, (0.10, 0.30, 0.70, 0.90), 0.5, 30 * DAY_BARS)
    assert cutoffs is None
    assert fraction < 0.5


def test_bucket_value_that_is_nan_has_no_bucket():
    cutoffs = np.array([1.0, 2.0, 3.0, 4.0])
    assert F.assign_bucket(float("nan"), cutoffs).bucket is None


@pytest.mark.parametrize("rv,expected", [
    (0.0007, "LOW"),
    (0.0007592730942805516, "MID"),   # exactly the cutoff is not LOW: D5 uses rv < lo
    (0.0009, "MID"),
    (0.0010924950359531157, "MID"),   # exactly the cutoff is not HIGH: D5 uses rv > hi
    (0.0012, "HIGH"),
])
def test_volatility_label_boundaries(rv, expected):
    assert F.volatility_label(rv, 0.0007592730942805516, 0.0010924950359531157) == expected


def test_volatility_label_is_none_for_nan():
    assert F.volatility_label(float("nan"), 1e-4, 1e-3) is None


# --- missing data and short lookbacks ------------------------------------------------------

def test_insufficient_lookback_gives_nan_not_a_guess():
    grid = synthetic_grid(days=1)
    win = window(grid, end_index=30)   # only 31 bars, far short of 60 and 1440
    values = F.compute_at_t(win)
    assert np.isnan(values.f_oi1h)
    assert np.isnan(values.f_drop1h)
    assert np.isnan(values.f_rv24h)
    assert np.isfinite(values.f_basis)   # needs no lookback


def test_a_nan_at_the_lagged_point_breaks_the_two_point_features():
    grid = synthetic_grid(days=2)
    grid["close"] = grid["close"].copy()
    grid["close"][-61] = np.nan          # exactly C[t-60]
    values = F.compute_at_t(window(grid))
    assert np.isnan(values.f_drop1h)
    assert np.isnan(values.f_rv24h)


def test_a_nan_elsewhere_in_the_hour_leaves_the_two_point_features_alone():
    """f_drop1h is C[t] against C[t-60]; D5 does not average over the hour, so a hole between
    the two endpoints is not supposed to matter. The rolling volatility does go NaN."""
    grid = synthetic_grid(days=2)
    grid["close"] = grid["close"].copy()
    grid["close"][-30] = np.nan
    values = F.compute_at_t(window(grid))
    assert np.isfinite(values.f_drop1h)
    assert np.isnan(values.f_rv24h)


def test_zero_and_negative_prices_become_nan_not_inf():
    grid = synthetic_grid(days=2)
    grid["index_close"] = grid["index_close"].copy()
    grid["index_close"][-1] = 0.0
    values = F.compute_at_t(window(grid))
    assert np.isnan(values.f_basis)


def test_window_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        BarWindow(ts_ms=np.arange(3), close=np.ones(3), index_close=np.ones(2), oi=np.ones(3))


def test_window_rejects_empty_input():
    with pytest.raises(ValueError):
        BarWindow(ts_ms=np.array([]), close=np.array([]), index_close=np.array([]),
                  oi=np.array([]))


# --- PIT: future rows must not move a past value -------------------------------------------

@pytest.fixture(scope="module")
def pit_grid() -> dict[str, np.ndarray]:
    return synthetic_grid(days=3, oi_slope=-1e-7)


def _at(grid, index, fn, *keys):
    return fn(*[grid[k][: index + 1] for k in keys])[-1]


@pytest.mark.parametrize("name,fn,keys", [
    ("f_basis", F.f_basis, ("close", "index_close")),
    ("f_oi1h", F.f_oi1h, ("oi",)),
    ("f_drop1h", F.f_drop1h, ("close",)),
    ("f_rv24h", F.f_rv24h, ("close",)),
])
def test_pit_future_rows_do_not_change_a_feature(pit_grid, name, fn, keys):
    index = 2 * DAY_BARS
    truncated = _at(pit_grid, index, fn, *keys)
    full = fn(*[pit_grid[k] for k in keys])[index]
    assert truncated == full or (np.isnan(truncated) and np.isnan(full)), name


@pytest.mark.parametrize("name,fn,keys", [
    ("f_basis", F.f_basis, ("close", "index_close")),
    ("f_oi1h", F.f_oi1h, ("oi",)),
    ("f_drop1h", F.f_drop1h, ("close",)),
    ("f_rv24h", F.f_rv24h, ("close",)),
])
def test_pit_tampering_with_future_rows_does_not_change_a_feature(pit_grid, name, fn, keys):
    """The strong form: corrupt everything after t and the value at t must be identical."""
    index = 2 * DAY_BARS
    before = fn(*[pit_grid[k] for k in keys])[index]
    tampered = {k: pit_grid[k].copy() for k in keys}
    for k in keys:
        tampered[k][index + 1:] *= 5.0
    after = fn(*[tampered[k] for k in keys])[index]
    assert before == after or (np.isnan(before) and np.isnan(after)), name


def test_pit_bucket_cutoffs_exclude_the_decision_day_and_everything_after(pit_grid):
    """Cutoffs come from [D-30d, D); the decision bar's own day must not contribute."""
    values = F.f_basis(pit_grid["close"], pit_grid["index_close"])
    decision_ts = int(pit_grid["ts"][2 * DAY_BARS + 500])
    history = F.previous_days_slice(pit_grid["ts"], decision_ts, 2)
    assert history.stop <= 2 * DAY_BARS
    assert int(pit_grid["ts"][history.stop - 1]) < decision_ts - 500 * 60_000
    tampered = values.copy()
    tampered[history.stop:] = 999.0
    a, _ = F.bucket_cutoffs(values[history], (0.10, 0.30, 0.70, 0.90), 0.5, 2 * DAY_BARS)
    b, _ = F.bucket_cutoffs(tampered[history], (0.10, 0.30, 0.70, 0.90), 0.5, 2 * DAY_BARS)
    np.testing.assert_array_equal(a, b)


def test_pit_previous_days_slice_never_reaches_the_current_day(pit_grid):
    for offset in (0, 1, 500, DAY_BARS - 1):
        index = 2 * DAY_BARS + offset
        decision_ts = int(pit_grid["ts"][index])
        history = F.previous_days_slice(pit_grid["ts"], decision_ts, 2)
        assert history.stop == 2 * DAY_BARS, offset
        assert history.start == 0, offset
