"""The C1 engine against the contract: the three conditions, the lifecycle, and the dedupe.

Every case drives the real inputs over a real-sized window (30 whole days plus the volatility
window) rather than stubbing a condition, so a test that passes says the engine read the contract
and not that a mock returned True.
"""
from __future__ import annotations

from app.crypto.c1 import contract as K
from app.crypto.c1.engine import C1Engine, evaluate, signal_id_for, warmup_bars
from app.crypto.c1.features import RollingMoments, rolling_std
from tests.crypto.c1_fixtures import (
    HIGH_STEP, LOW_STEP, basis_dipping, build, falling_oi, window_bars,
)

LAST = window_bars() - 1
# S1 only moves when a 5m bucket has *ended*, so a basis dip has to land on a bucket's final
# minute to be visible at all - and it is then visible for the five bars that read that bucket.
# These indices are those final minutes, which is why the lifecycle cases below count in fives.
assert LAST % 5 == 4, "the fixture window is sized so the last bar closes a 5m bucket"
BLOCK_A = LAST - 5           # ON for bars BLOCK_A .. BLOCK_A + 4
BLOCK_B = LAST - 15          # ON for bars BLOCK_B .. BLOCK_B + 4
assert BLOCK_A % 5 == 4 and BLOCK_B % 5 == 4


def test_direction_contract_is_long_only() -> None:
    """D5.2 scored both sides of C1 and only LONG was ever positive; results section 18. The
    engine must not offer a SHORT the contract does not define."""
    assert K.DIRECTION == "LONG"
    assert K.DIRECTION_CONTRACT == "LONG_ONLY"
    assert signal_id_for(1).startswith("C1-LONG-")


def test_all_three_conditions_true_is_on() -> None:
    grid, _ = build(basis=basis_dipping([LAST]), oi=falling_oi(LAST))
    evaluation = C1Engine().evaluate_at(grid, LAST)
    assert evaluation.state == "ON"
    assert evaluation.on is True
    features = evaluation.features
    assert (features.condition_basis_b1, features.condition_oi_falling,
            features.condition_vol_high) == (True, True, True)


def test_basis_outside_b1_is_off() -> None:
    """The bucket is the only condition changed; the other two still hold."""
    grid, _ = build(oi=falling_oi(LAST))
    evaluation = C1Engine().evaluate_at(grid, LAST)
    assert evaluation.state == "OFF"
    assert evaluation.features.condition_basis_b1 is False
    assert evaluation.features.condition_oi_falling is True
    assert evaluation.features.condition_vol_high is True


def test_rising_open_interest_is_off() -> None:
    grid, _ = build(basis=basis_dipping([LAST]))        # default OI rises inside each window
    evaluation = C1Engine().evaluate_at(grid, LAST)
    assert evaluation.state == "OFF"
    assert evaluation.features.condition_oi_falling is False


def test_unchanged_open_interest_is_not_falling() -> None:
    """Contract section 4 says `< 0`. Five bars of the research window sit at exactly zero and the
    study excludes them, so flat open interest must not count as a decrease."""
    grid, _ = build(basis=basis_dipping([LAST]), oi=lambda _i: 1_000_000.0)
    evaluation = C1Engine().evaluate_at(grid, LAST)
    assert evaluation.features.oi_change_1h == 0.0
    assert evaluation.features.condition_oi_falling is False
    assert evaluation.state == "OFF"


def test_low_volatility_is_off_and_labelled() -> None:
    grid, _ = build(step=LOW_STEP, basis=basis_dipping([LAST]), oi=falling_oi(LAST))
    evaluation = C1Engine().evaluate_at(grid, LAST)
    assert evaluation.features.vol_regime == "LOW"
    assert evaluation.features.condition_vol_high is False
    assert evaluation.state == "OFF"


def test_volatility_cutoff_is_the_frozen_f1_train_figure() -> None:
    """Not re-estimated from recent data: a live HIGH has to mean the HIGH the study measured."""
    grid, _ = build(basis=basis_dipping([LAST]), oi=falling_oi(LAST))
    evaluation = C1Engine().evaluate_at(grid, LAST)
    assert evaluation.features.vol_high_cutoff == K.VOL_CUTOFF_HI_F1_TRAIN
    assert evaluation.features.rv_24h > K.VOL_REGIME_HIGH_CUTOFF


def test_missing_perpetual_bar_is_not_eligible_rather_than_false() -> None:
    """A hole inside the volatility window suppresses the regime, and the bar reports why."""
    grid, _ = build(basis=basis_dipping([LAST]), oi=falling_oi(LAST),
                    drop_minutes=[LAST - 10])
    evaluation = C1Engine().evaluate_at(grid, LAST)
    assert evaluation.state == "NOT_ELIGIBLE"
    assert evaluation.on is False
    assert "VOLATILITY_24H" in evaluation.completeness.missing
    assert evaluation.eligible is False


def test_open_interest_that_stopped_arriving_is_stale_not_flat() -> None:
    """The study's as-of join carries the last value forever, which over a live feed would read a
    dead venue as an unchanged open interest. The guard says STALE instead."""
    grid, _ = build(basis=basis_dipping([LAST]), oi=falling_oi(LAST), oi_until=LAST - 200)
    evaluation = C1Engine().evaluate_at(grid, LAST)
    assert evaluation.state == "NOT_ELIGIBLE"
    assert "OPEN_INTEREST_STALE" in evaluation.completeness.missing
    assert evaluation.on is False


def test_no_open_interest_at_all_is_not_eligible() -> None:
    grid, _ = build(basis=basis_dipping([LAST]), oi=falling_oi(LAST), oi_until=-10_000)
    evaluation = C1Engine().evaluate_at(grid, LAST)
    assert evaluation.state == "NOT_ELIGIBLE"
    assert "OPEN_INTEREST_1H_CHANGE" in evaluation.completeness.missing


def test_fresh_open_interest_passes_the_staleness_guard() -> None:
    """The bound has to be loose enough for the venue's normal cadence: one 5m interval plus the
    contract's five-minute knowledge delay."""
    grid, _ = build(basis=basis_dipping([LAST]), oi=falling_oi(LAST))
    evaluation = C1Engine().evaluate_at(grid, LAST)
    assert "OPEN_INTEREST_STALE" not in evaluation.completeness.missing
    staleness = evaluation.decided_at_ms - grid.oi_stamp[LAST]
    assert staleness <= K.OI_OBSERVED_MAX_STALENESS_MS


def test_missing_spot_beyond_one_bar_leaves_the_basis_absent() -> None:
    """Contract section 2 allows one 5m carry and no more, so a whole 5m bucket of missing spot
    makes S1 unavailable instead of stale."""
    gap = list(range(LAST - 9, LAST + 1))
    grid, _ = build(basis=basis_dipping([LAST]), oi=falling_oi(LAST), drop_spot_minutes=gap)
    evaluation = C1Engine().evaluate_at(grid, LAST)
    assert evaluation.state == "NOT_ELIGIBLE"
    assert "SPOT_BASIS" in evaluation.completeness.missing


def test_one_missing_spot_bucket_is_carried_once() -> None:
    """The other half of the same rule: a single missing 5m bucket is allowed to carry."""
    grid, _ = build(basis=basis_dipping([LAST]), oi=falling_oi(LAST),
                    drop_spot_minutes=range(LAST - 4, LAST + 1))
    evaluation = C1Engine().evaluate_at(grid, LAST)
    assert "SPOT_BASIS" not in evaluation.completeness.missing


def test_no_bucket_before_thirty_days_of_history() -> None:
    grid, _ = build(bars=20 * 1440)
    evaluation = C1Engine().evaluate_at(grid, 20 * 1440 - 1)
    assert evaluation.state == "NOT_ELIGIBLE"
    assert "BASIS_BUCKET_CUTOFFS" in evaluation.completeness.missing


def test_repeated_true_bars_produce_one_signal() -> None:
    """The study counted every bar inside an event; an operator gets one event. Five consecutive
    true bars are one signal, not five."""
    grid, _ = build(basis=basis_dipping([BLOCK_A]), oi=falling_oi(LAST))
    engine = C1Engine()
    states = [engine.evaluate_at(grid, index).state for index in range(BLOCK_A, BLOCK_A + 5)]
    assert states == ["ON"] * 5
    signals = list(engine.advance(grid, start_index=BLOCK_A - 3, stop_index=LAST + 1))
    assert len(signals) == 1
    assert signals[0].signal_bar_ms == grid.ts(BLOCK_A)


def test_the_condition_must_lapse_before_another_signal() -> None:
    """Two events separated by false bars are two signals; the gap is what re-arms the engine."""
    grid, _ = build(basis=basis_dipping([BLOCK_B, BLOCK_A]), oi=falling_oi(LAST))
    engine = C1Engine()
    between = engine.evaluate_at(grid, BLOCK_B + 5).state
    signals = list(engine.advance(grid, start_index=BLOCK_B - 3, stop_index=LAST + 1))
    assert between == "OFF"
    assert [signal.signal_bar_ms for signal in signals] == [grid.ts(BLOCK_B), grid.ts(BLOCK_A)]
    assert len({signal.signal_id for signal in signals}) == 2


def test_a_not_eligible_bar_ends_the_event() -> None:
    """An unknown bar is not a true bar, so it closes the event rather than extending it.

    Open interest going stale part-way through is the case that can be staged and recovered from:
    a dropped candle would poison the volatility window for the next 24 h, which is a different
    (and also correct) behaviour tested above.
    """
    stop, resume = BLOCK_A + 1, BLOCK_A + 3
    def oi(index: int) -> float:
        return falling_oi(LAST)(min(index, 10 ** 9))
    grid, _ = build(basis=basis_dipping([BLOCK_A]), oi=oi)
    # Blank the stamps for two bars in the middle of the event, as a feed outage would.
    for index in range(stop, resume):
        grid.oi_stamp[index] = grid.ts(index) - 10 * K.MINUTE_MS - K.OI_MAX_STALENESS_MS
    engine = C1Engine()
    states = [engine.evaluate_at(grid, index).state for index in range(BLOCK_A, BLOCK_A + 5)]
    assert states == ["ON", "NOT_ELIGIBLE", "NOT_ELIGIBLE", "ON", "ON"]
    signals = list(engine.advance(grid, start_index=BLOCK_A - 3, stop_index=LAST + 1))
    assert len(signals) == 2
    assert [s.signal_bar_ms for s in signals] == [grid.ts(BLOCK_A), grid.ts(resume)]


def test_signal_id_is_derived_from_the_trigger_instant() -> None:
    """A replay of the same bars has to recognise its own signals instead of duplicating them."""
    grid, _ = build(basis=basis_dipping([LAST]), oi=falling_oi(LAST))
    first = list(C1Engine().advance(grid, start_index=LAST - 2, stop_index=LAST + 1))
    again = list(C1Engine().advance(grid, start_index=LAST - 2, stop_index=LAST + 1))
    assert [s.signal_id for s in first] == [s.signal_id for s in again]
    assert first[0].signal_id == signal_id_for(grid.ts(LAST) + K.MINUTE_MS)


def test_signal_records_the_contract_execution_rule() -> None:
    """Decide at the close of t, enter at the open of t+1, leave 240 minutes later."""
    grid, _ = build(basis=basis_dipping([BLOCK_A]), oi=falling_oi(LAST))
    signal = next(iter(C1Engine().advance(grid, start_index=BLOCK_A - 2, stop_index=LAST + 1)))
    assert signal.triggered_at_ms == signal.signal_bar_ms + K.MINUTE_MS
    assert signal.official_entry_at_ms == signal.signal_bar_ms + K.MINUTE_MS
    assert signal.planned_exit_at_ms == (signal.official_entry_at_ms
                                         + K.OFFICIAL_HORIZON_MIN * K.MINUTE_MS)
    assert signal.official_entry_price == grid.opens[grid.index_of(signal.official_entry_at_ms)]
    assert signal.signal_price == grid.closes[BLOCK_A]
    assert signal.horizon_min == 240


def test_feature_snapshot_keeps_every_input_and_marks_liquidation_as_not_an_input() -> None:
    """Section 0 rule Z3 keeps liquidations out of D5.2 because their history is forward-only."""
    grid, _ = build(basis=basis_dipping([LAST]), oi=falling_oi(LAST))
    features = C1Engine().evaluate_at(grid, LAST).features
    assert features.s1 < features.s1_b1_cutoff
    assert features.s1_bucket == 0
    assert features.oi_now > 0 and features.oi_lagged > 0
    assert features.perp_5m_close > 0 and features.spot_5m_close > 0
    assert features.liquidation_available is False
    assert "Z3" in features.liquidation_context


def test_snapshot_is_kept_for_bars_that_did_not_fire() -> None:
    """A later threshold study needs the distribution, not only the crossings."""
    grid, _ = build(oi=falling_oi(LAST))
    features = C1Engine().evaluate_at(grid, LAST).features
    assert features.condition_basis_b1 is False
    assert features.s1 == features.s1 and features.s1_bucket >= 0
    assert features.rv_24h > 0


def test_the_sliding_volatility_matches_summing_the_window() -> None:
    """The fast path exists only for the replay; it has to agree with the definition."""
    grid, _ = build()
    returns = grid.returns()
    moments = RollingMoments(returns)
    for index in range(len(grid) - 300, len(grid)):
        fast = moments.at(index)
        slow = rolling_std(returns[index + 1 - K.VOL_WINDOW_BARS: index + 1], K.VOL_WINDOW_BARS)
        assert abs(fast - slow) < 1e-18


def test_warmup_covers_both_windows() -> None:
    assert warmup_bars() >= K.NORM_DAYS * K.DAY_BARS + K.VOL_WINDOW_BARS


def test_evaluate_is_pure() -> None:
    """Same grid, same bar, same answer - the verdict carries no memory."""
    grid, _ = build(basis=basis_dipping([LAST]), oi=falling_oi(LAST))
    cutoffs = C1Engine().cutoffs.get(grid, LAST)
    first = evaluate(grid, LAST, cutoffs)
    second = evaluate(grid, LAST, cutoffs)
    assert first == second


def test_a_replaced_grid_gets_a_fresh_volatility_accumulator() -> None:
    """The runtime rebuilds the grid on every tick. The cached accumulator must follow the grid
    object, not its address: a freed grid's address can be reused, and a stale accumulator over
    the previous returns would then be accepted as current."""
    engine = C1Engine()
    first, _ = build(basis=basis_dipping([LAST]), oi=falling_oi(LAST))
    list(engine.advance(first, start_index=LAST - 2, stop_index=LAST + 1))
    held = engine._moments_for(first)
    assert engine._moments_for(first) is held
    second, _ = build(step=LOW_STEP, basis=basis_dipping([LAST]), oi=falling_oi(LAST))
    assert engine._moments_for(second) is not held
    assert engine._moments_for(second).returns is second.returns()
