"""Clock-based features on dense, sparse and premarket tapes, with hand-computed values."""

from datetime import time, timedelta

import pytest

from app.strategy_b.config import DollarVolumePriceBasis, FeatureConfig, StrategyBConfig
from app.strategy_b.errors import InvalidTape, PointInTimeViolation
from app.strategy_b.features import (
    clock_return, cumulative_dollar_volume, high_low_of_day, last_price, missing_minute_ratio,
    rolling_dollar_volume, session_vwap, tape_density, volume_acceleration,
)
from app.strategy_b.models import (
    Availability, HaltStatus, Measured, MomentumBar, RvolStatus, Session, TapeDensity,
)
from app.strategy_b.session import AggregationScope, SessionBoundaries
from app.strategy_b.snapshot import compute_feature_snapshot
from app.strategy_b.sparse_session import minute_clock_view
from tests.strategy_b.fixtures import (
    D, dense_regular_session, et, flat_volume_profile, make_bar, make_tape, past_session_dates,
    premarket_sparse_session, sparse_low_liquidity_session,
)


LOCAL = AggregationScope.SESSION_LOCAL
EXTENDED = AggregationScope.EXTENDED_DAY
PRE_REG = AggregationScope.PREMARKET_AND_REGULAR


def value(measured: Measured) -> float:
    assert measured.status is Availability.AVAILABLE, measured
    assert measured.value is not None
    return measured.value


# ---- session boundaries ----------------------------------------------------------------

def test_session_boundaries_classify_half_open_and_early_close() -> None:
    b = SessionBoundaries.standard(D)
    assert b.classify(et(3, 59)) is Session.OUTSIDE
    assert b.classify(et(4, 0)) is Session.PREMARKET
    assert b.classify(et(9, 30)) is Session.REGULAR
    assert b.classify(et(16, 0)) is Session.AFTER
    assert b.classify(et(20, 0)) is Session.OUTSIDE
    early = SessionBoundaries.standard(D, regular_close=time(13, 0))
    assert early.classify(et(13, 30)) is Session.AFTER
    assert b.scope_start(et(10, 0), PRE_REG) == et(4, 0)
    assert b.scope_start(et(17, 0), PRE_REG) == et(16, 0)
    assert b.scope_start(et(10, 0), LOCAL) == et(9, 30)
    assert b.scope_start(et(21, 0), LOCAL) is None
    assert b.elapsed_minute_slots(et(9, 30), et(9, 44)) == 14
    assert b.elapsed_minute_slots(et(9, 30), et(9, 44, 59)) == 14
    assert b.elapsed_minute_slots(et(19, 50), et(21, 0)) == 10


# ---- clock returns ---------------------------------------------------------------------

def test_sparse_returns_are_wall_clock_not_bar_count() -> None:
    tape = make_tape(sparse_low_liquidity_session())  # 09:31 10.00, 09:34 10.50, 09:39 11.00

    # 09:36: now 10.50; 1m ago 10.50; 3m ago (09:33) 10.00; 5m ago (09:31) nothing traded yet.
    assert value(clock_return(tape, et(9, 36), 1, EXTENDED)) == 0.0
    assert value(clock_return(tape, et(9, 36), 3, EXTENDED)) == pytest.approx(5.0)
    assert clock_return(tape, et(9, 36), 5, EXTENDED).status is Availability.INSUFFICIENT_HISTORY

    # 09:44: now 11.00; 1m and 3m ago still 11.00; 5m ago (09:39) the 09:39 bar is not yet
    # available (available 09:40), so the reference is 10.50. "One bar back" would give 4.76%
    # for return_1m, which is exactly the error this test exists to catch.
    assert value(clock_return(tape, et(9, 44), 1, EXTENDED)) == 0.0
    assert value(clock_return(tape, et(9, 44), 3, EXTENDED)) == 0.0
    assert value(clock_return(tape, et(9, 44), 5, EXTENDED)) == pytest.approx((11.0 / 10.5 - 1) * 100)


def test_bar_becomes_usable_exactly_one_minute_after_it_opens() -> None:
    tape = make_tape(sparse_low_liquidity_session())
    assert last_price(tape, et(9, 39, 59, 999999), LOCAL)[0].value == 10.50
    assert last_price(tape, et(9, 40), LOCAL)[0].value == 11.00


def test_returns_before_any_trade_and_session_local_reference() -> None:
    tape = make_tape(sparse_low_liquidity_session())
    assert clock_return(tape, et(9, 31, 30), 1, EXTENDED).status is Availability.NO_DATA
    assert clock_return(tape, et(9, 33), 5, LOCAL).status is Availability.INSUFFICIENT_HISTORY


def test_dense_tape_clock_return_equals_bar_count_return() -> None:
    bars = dense_regular_session()
    tape = make_tape(bars)
    as_of = et(10, 0)  # last available bar 09:59 (index 29)
    for minutes in (1, 3, 5):
        expected = (bars[29].close / bars[29 - minutes].close - 1) * 100
        assert value(clock_return(tape, as_of, minutes, LOCAL)) == pytest.approx(expected)


def test_premarket_price_seeds_early_regular_returns_only_in_extended_scope() -> None:
    tape = make_tape(premarket_sparse_session())
    # 09:31: now 09:30 bar 9.80; 5m ago (09:26) premarket 09:25 bar 9.50.
    assert value(clock_return(tape, et(9, 31), 5, EXTENDED)) == pytest.approx((9.8 / 9.5 - 1) * 100)
    assert clock_return(tape, et(9, 31), 5, LOCAL).status is Availability.INSUFFICIENT_HISTORY


# ---- VWAP, HOD/LOD ---------------------------------------------------------------------

def test_session_vwap_uses_source_vwap_weighted_by_volume() -> None:
    tape = make_tape(sparse_low_liquidity_session())
    expected = (9.9 * 100 + 10.4 * 200 + 10.9 * 300) / 600
    assert value(session_vwap(tape, et(9, 44), LOCAL)) == pytest.approx(expected)
    assert value(session_vwap(tape, et(9, 36), LOCAL)) == pytest.approx((9.9 * 100 + 10.4 * 200) / 300)
    assert session_vwap(tape, et(9, 31), LOCAL).status is Availability.NO_DATA


def test_missing_source_vwap_is_unknown_not_typical_price() -> None:
    bars = sparse_low_liquidity_session()
    bars[1] = MomentumBar(bars[1].timestamp, bars[1].open, bars[1].high, bars[1].low, bars[1].close,
                          bars[1].volume, bars[1].session, vwap=None)
    tape = make_tape(bars)
    assert session_vwap(tape, et(9, 33), LOCAL).status is Availability.AVAILABLE
    assert session_vwap(tape, et(9, 36), LOCAL).status is Availability.NO_SOURCE_VWAP
    assert cumulative_dollar_volume(tape, et(9, 36), LOCAL, DollarVolumePriceBasis.SOURCE_VWAP).status \
        is Availability.NO_SOURCE_VWAP
    assert value(cumulative_dollar_volume(tape, et(9, 36), LOCAL, DollarVolumePriceBasis.CLOSE)) == 3100.0


def test_zero_volume_bar_without_vwap_does_not_block_vwap() -> None:
    bars = [make_bar(9, 30, 10.0, 100.0, vwap=10.0),
            MomentumBar(et(9, 31), 10.0, 10.0, 10.0, 10.0, 0.0, Session.REGULAR, vwap=None)]
    assert value(session_vwap(make_tape(bars), et(9, 32), LOCAL)) == 10.0


def test_vwap_scope_premarket_and_regular_is_explicit() -> None:
    tape = make_tape(premarket_sparse_session())
    local = value(session_vwap(tape, et(9, 31), LOCAL))
    combined = value(session_vwap(tape, et(9, 31), PRE_REG))
    assert local == pytest.approx(9.70)
    pre_notional = 8.0 * 50 + 8.4 * 80 + 9.0 * 400 + 9.2 * 150 + 9.5 * 500
    assert combined == pytest.approx((pre_notional + 9.7 * 2000) / (1180 + 2000))


def test_hod_lod_are_running_extremes() -> None:
    tape = make_tape(sparse_low_liquidity_session())
    hod, lod = high_low_of_day(tape, et(9, 36), LOCAL)
    assert (value(hod), value(lod)) == (10.60, 9.80)
    hod, lod = high_low_of_day(tape, et(9, 40), LOCAL)
    assert (value(hod), value(lod)) == (11.20, 9.80)


def test_session_local_hod_ignores_premarket_high() -> None:
    tape = make_tape(premarket_sparse_session())
    assert value(high_low_of_day(tape, et(9, 34), LOCAL)[0]) == pytest.approx(10.15)
    assert value(high_low_of_day(tape, et(9, 34), EXTENDED)[0]) == 12.00


# ---- dollar volume, acceleration, density ----------------------------------------------

def test_dollar_volume_cumulative_and_rolling_clock_windows() -> None:
    tape = make_tape(sparse_low_liquidity_session())
    close = DollarVolumePriceBasis.CLOSE
    assert value(cumulative_dollar_volume(tape, et(9, 44), LOCAL, close)) == 6400.0
    # (09:39, 09:44]: only the 09:39 bar (available 09:40).
    assert value(rolling_dollar_volume(tape, et(9, 44), 5, LOCAL, close)) == 3300.0
    # (09:40, 09:45]: nothing traded; zero, not unknown.
    assert value(rolling_dollar_volume(tape, et(9, 45), 5, LOCAL, close)) == 0.0
    assert rolling_dollar_volume(tape, et(9, 34), 5, LOCAL, close).status is Availability.INSUFFICIENT_HISTORY
    vwap_basis = DollarVolumePriceBasis.SOURCE_VWAP
    assert value(cumulative_dollar_volume(tape, et(9, 44), LOCAL, vwap_basis)) == pytest.approx(6340.0)


def test_volume_acceleration_compares_clock_windows() -> None:
    tape = make_tape(sparse_low_liquidity_session())
    # recent (09:39, 09:44] = 300; previous (09:34, 09:39] = 200 (09:34 bar available 09:35).
    assert value(volume_acceleration(tape, et(9, 44), 5, LOCAL)) == pytest.approx(1.5)
    assert volume_acceleration(tape, et(9, 39), 5, LOCAL).status is Availability.INSUFFICIENT_HISTORY
    # recent (09:50, 09:55] = 0; previous (09:45, 09:50] = 0.
    assert volume_acceleration(tape, et(9, 55), 5, LOCAL).status is Availability.ZERO_BASELINE


def test_missing_minute_ratio_and_density() -> None:
    config = FeatureConfig()
    sparse = make_tape(sparse_low_liquidity_session())
    ratio = missing_minute_ratio(sparse, et(9, 44), LOCAL)
    assert value(ratio) == pytest.approx(1 - 3 / 14)
    assert tape_density(ratio, config) is TapeDensity.VERY_SPARSE
    dense = make_tape(dense_regular_session())
    ratio = missing_minute_ratio(dense, et(10, 0), LOCAL)
    assert value(ratio) == 0.0
    assert tape_density(ratio, config) is TapeDensity.DENSE
    assert missing_minute_ratio(dense, et(9, 30, 30), LOCAL).status is Availability.NO_DATA
    assert tape_density(Measured.missing(Availability.NO_DATA), config) is TapeDensity.UNKNOWN


# ---- synthetic clock view --------------------------------------------------------------

def test_clock_view_fills_silence_without_touching_the_tape() -> None:
    bars = sparse_low_liquidity_session()
    tape = make_tape(bars)
    view = minute_clock_view(tape, start=et(9, 30), as_of=et(9, 44))
    # 09:30 has no prior price and is omitted; 09:31..09:43 are 13 slots.
    assert [b.timestamp for b in view] == [et(9, m) for m in range(31, 44)]
    assert [b for b in view if not b.synthetic] == bars
    filler = view[1]
    assert (filler.synthetic, filler.close, filler.volume, filler.vwap) == (True, 10.0, 0.0, None)
    assert tape.bars == tuple(bars)


def test_clock_view_agrees_with_tape_features_at_every_as_of() -> None:
    tape = make_tape(sparse_low_liquidity_session())
    start = et(9, 30)
    for seconds in range(0, 16 * 60, 15):
        as_of = et(9, 42) + timedelta(seconds=seconds)
        view = minute_clock_view(tape, start=start, as_of=as_of)
        for minutes in (1, 3, 5):
            expected = (view[-1].close / view[-1 - minutes].close - 1) * 100
            assert value(clock_return(tape, as_of, minutes, LOCAL)) == pytest.approx(expected)
        recent = sum(b.volume for b in view[-5:])
        previous = sum(b.volume for b in view[-10:-5])
        measured = volume_acceleration(tape, as_of, 5, LOCAL)
        if previous:
            assert value(measured) == pytest.approx(recent / previous)
        else:
            assert measured.status is Availability.ZERO_BASELINE


def test_tape_refuses_synthetic_bars() -> None:
    tape = make_tape(sparse_low_liquidity_session())
    view = minute_clock_view(tape, start=et(9, 30), as_of=et(9, 44))
    with pytest.raises(InvalidTape, match="synthetic"):
        make_tape(list(view))


# ---- tape validation -------------------------------------------------------------------

def test_tape_refuses_unordered_duplicate_mislabelled_or_out_of_day_bars() -> None:
    bars = sparse_low_liquidity_session()
    with pytest.raises(InvalidTape):
        make_tape([bars[1], bars[0]])
    with pytest.raises(InvalidTape):
        make_tape([bars[0], bars[0]])
    mislabelled = MomentumBar(et(9, 45), 10, 10.1, 9.9, 10, 1.0, Session.PREMARKET)
    with pytest.raises(InvalidTape, match="labelled"):
        make_tape([mislabelled])
    with pytest.raises(InvalidTape, match="outside"):
        make_tape([MomentumBar(et(3, 0), 10, 10.1, 9.9, 10, 1.0, Session.OUTSIDE)])
    with pytest.raises(InvalidTape):
        make_tape(bars, symbol="btest")


# ---- snapshot --------------------------------------------------------------------------

def test_snapshot_on_dense_tape_with_full_rvol_history() -> None:
    config = StrategyBConfig()
    tape = make_tape(dense_regular_session())
    history = [flat_volume_profile(d, 500.0) for d in past_session_dates(20)]
    snap = compute_feature_snapshot(tape, et(9, 40), config, rvol_history=history)
    assert snap.session is Session.REGULAR
    assert snap.price.value == 10.09
    assert snap.price_age_seconds == 0.0
    assert snap.rvol_status is RvolStatus.FULL
    # today 09:30-09:39: Σ(1000 + 10k) = 10450; baseline 10 × 500 = 5000.
    assert value(snap.rvol) == pytest.approx(10450 / 5000)
    assert snap.hod_distance_pct.value == pytest.approx((10.09 / 10.14 - 1) * 100)
    assert snap.sparse_status is TapeDensity.DENSE
    assert snap.halt_inferred is HaltStatus.NO_HALT_SIGNAL
    assert snap.split_adjusted is False
    assert snap.corporate_action_flags == frozenset()


def test_snapshot_rejects_as_of_on_another_date() -> None:
    tape = make_tape(dense_regular_session())
    with pytest.raises(PointInTimeViolation):
        compute_feature_snapshot(tape, et(9, 40) + timedelta(days=1), StrategyBConfig())


def test_snapshot_is_deterministic() -> None:
    config = StrategyBConfig()
    tape = make_tape(premarket_sparse_session())
    first = compute_feature_snapshot(tape, et(9, 34), config)
    second = compute_feature_snapshot(make_tape(premarket_sparse_session()), et(9, 34), config)
    assert first == second
    assert first.rvol_status is RvolStatus.UNKNOWN
    assert first.rvol.status is Availability.INSUFFICIENT_HISTORY
