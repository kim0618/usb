"""The 13 hard filters, one pass and one fail each, plus the statuses in between.

Two of them (H6 spread, H7 depth) are `realtime_only`. Over history they must report
NOT_EVALUATED and must not block, because there is no historical order book to evaluate them
with; treating the absence as a pass would be a silent claim, and treating it as a fail would
stop D6-C dead. The distinction is tested in both directions.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.crypto.research.d6 import features as F
from app.crypto.research.d6 import filters as FILTERS
from app.crypto.research.d6.contract import load as load_contract
from app.crypto.research.d6.decision import build_buckets
from app.crypto.research.d6.model import (FAIL, FeatureValues, HISTORICAL, NOT_EVALUATED, PASS,
                                          REALTIME)

from tests.crypto.d6_fixtures import MINUTE_MS, clean_market, clean_state, synthetic_grid, window


@pytest.fixture(scope="module")
def contract():
    return load_contract()


@pytest.fixture(scope="module")
def scene(contract):
    """One clean decision bar, reused. Each test perturbs a single input."""
    grid = synthetic_grid(days=32, oi_slope=-1e-7, vol=9.0e-4)
    win = window(grid)
    series = F.compute_series(win)
    values = FeatureValues(f_basis=float(series["f_basis"][-1]),
                           f_oi1h=float(series["f_oi1h"][-1]),
                           f_drop1h=float(series["f_drop1h"][-1]),
                           f_rv24h=float(series["f_rv24h"][-1]))
    buckets = build_buckets(contract, win, series)
    label = F.volatility_label(values.f_rv24h, contract.vol_low_below, contract.vol_high_above)
    return {"window": win, "features": values, "buckets": buckets, "vol_label": label}


def run(contract, scene, *, mode=HISTORICAL, market=None, state=None,
        features=None, buckets=None, vol_label="keep", intended_qty=None):
    return FILTERS.evaluate(
        contract, mode=mode,
        features=features or scene["features"],
        buckets=buckets or scene["buckets"],
        vol_label=scene["vol_label"] if vol_label == "keep" else vol_label,
        market=market or clean_market(scene["window"]),
        state=state or clean_state(),
        bar_close_ms=scene["window"].bar_close_ms,
        intended_qty=intended_qty)


def status_of(results, filter_id):
    return next(r for r in results if r.filter_id == filter_id)


# --- coverage --------------------------------------------------------------------------------

def test_all_thirteen_filters_are_evaluated_in_contract_order(contract, scene):
    results = run(contract, scene)
    assert [r.filter_id for r in results] == list(contract.hard_filter_ids)
    assert len(results) == 13


def test_names_come_from_the_contract(contract, scene):
    for result in run(contract, scene):
        assert result.name == contract.hard_filter(result.filter_id)["name"]


def test_a_clean_scene_blocks_on_nothing(contract, scene):
    results = run(contract, scene)
    assert FILTERS.blocking(results) == []
    skipped = [r.filter_id for r in results if r.status == NOT_EVALUATED]
    assert skipped == ["H6", "H7"]


# --- H1 DATA_STALE ---------------------------------------------------------------------------

def test_h1_passes_at_the_bar_close(contract, scene):
    assert status_of(run(contract, scene), "H1").status == PASS


def test_h1_fails_when_the_bar_is_too_old(contract, scene):
    market = clean_market(scene["window"], now_ms=scene["window"].bar_close_ms + 91_000)
    result = status_of(run(contract, scene, market=market), "H1")
    assert result.status == FAIL and result.reason == "H1_DATA_STALE"


def test_h1_boundary_is_inclusive_at_the_limit(contract, scene):
    exactly = clean_market(scene["window"], now_ms=scene["window"].bar_close_ms + 90_000)
    over = clean_market(scene["window"], now_ms=scene["window"].bar_close_ms + 90_001)
    assert status_of(run(contract, scene, market=exactly), "H1").status == PASS
    assert status_of(run(contract, scene, market=over), "H1").status == FAIL


# --- H2 OI_STALE -----------------------------------------------------------------------------

def test_h2_fails_when_the_oi_record_is_old(contract, scene):
    market = clean_market(scene["window"],
                          oi_record_ts_ms=scene["window"].bar_close_ms - 16 * MINUTE_MS)
    result = status_of(run(contract, scene, market=market), "H2")
    assert result.status == FAIL and result.reason == "H2_OI_STALE"


def test_h2_fails_when_the_record_timestamp_is_missing(contract, scene):
    market = clean_market(scene["window"], oi_record_ts_ms=None)
    result = status_of(run(contract, scene, market=market), "H2")
    assert result.status == FAIL and result.reason == "H2_OI_RECORD_TS_UNKNOWN"


def test_h2_passes_at_a_normal_lag(contract, scene):
    market = clean_market(scene["window"],
                          oi_record_ts_ms=scene["window"].bar_close_ms - 9 * MINUTE_MS)
    assert status_of(run(contract, scene, market=market), "H2").status == PASS


# --- H3 INSUFFICIENT_HISTORY -----------------------------------------------------------------

def test_h3_fails_when_a_feature_has_no_cutoffs(contract, scene):
    thin = dict(scene["buckets"])
    name = "f_basis"
    thin[name] = F.assign_bucket(0.0, None, 0.1)
    result = status_of(run(contract, scene, buckets=thin), "H3")
    assert result.status == FAIL and result.reason == "H3_INSUFFICIENT_HISTORY"
    assert name in result.observed["features_without_cutoffs"]


def test_h3_passes_with_a_full_window(contract, scene):
    assert status_of(run(contract, scene), "H3").status == PASS


def test_h3_fires_on_a_short_grid(contract):
    grid = synthetic_grid(days=3)
    win = window(grid)
    series = F.compute_series(win)
    values = FeatureValues(*[float(series[k][-1]) for k in
                             ("f_basis", "f_oi1h", "f_drop1h", "f_rv24h")])
    buckets = build_buckets(contract, win, series)
    results = FILTERS.evaluate(contract, mode=HISTORICAL, features=values, buckets=buckets,
                               vol_label="MID", market=clean_market(win), state=clean_state(),
                               bar_close_ms=win.bar_close_ms)
    assert status_of(results, "H3").status == FAIL


# --- H4 FEATURE_NAN --------------------------------------------------------------------------

def test_h4_fails_on_a_nan_feature(contract, scene):
    broken = FeatureValues(f_basis=float("nan"), f_oi1h=-0.01, f_drop1h=-0.02, f_rv24h=9e-4)
    result = status_of(run(contract, scene, features=broken), "H4")
    assert result.status == FAIL and result.observed["nan_features"] == ["f_basis"]


def test_h4_lists_every_nan_feature(contract, scene):
    nan = float("nan")
    broken = FeatureValues(f_basis=nan, f_oi1h=nan, f_drop1h=nan, f_rv24h=nan)
    result = status_of(run(contract, scene, features=broken), "H4")
    assert result.observed["nan_features"] == ["f_basis", "f_oi1h", "f_drop1h", "f_rv24h"]


def test_h4_passes_when_all_four_are_finite(contract, scene):
    assert status_of(run(contract, scene), "H4").status == PASS


# --- H5 VOL_LOW ------------------------------------------------------------------------------

@pytest.mark.parametrize("label,expected", [("LOW", FAIL), ("MID", PASS), ("HIGH", PASS)])
def test_h5_blocks_only_low_volatility(contract, scene, label, expected):
    assert status_of(run(contract, scene, vol_label=label), "H5").status == expected


def test_h5_fails_when_the_label_is_unknown(contract, scene):
    result = status_of(run(contract, scene, vol_label=None), "H5")
    assert result.status == FAIL and result.reason == "H5_VOL_LABEL_UNKNOWN"


# --- H6 SPREAD_WIDE --------------------------------------------------------------------------

def test_h6_is_not_evaluated_over_history(contract, scene):
    result = status_of(run(contract, scene, mode=HISTORICAL), "H6")
    assert result.status == NOT_EVALUATED and result.reason == "H6_NO_HISTORICAL_ORDERBOOK"
    assert result.blocking is False


def test_h6_passes_on_a_tight_realtime_quote(contract, scene):
    market = clean_market(scene["window"], bid=100_000.0, ask=100_000.1)
    result = status_of(run(contract, scene, mode=REALTIME, market=market), "H6")
    assert result.status == PASS
    assert result.observed["spread_bp"] < 5.0


def test_h6_fails_on_a_wide_realtime_quote(contract, scene):
    market = clean_market(scene["window"], bid=100_000.0, ask=100_100.0)
    result = status_of(run(contract, scene, mode=REALTIME, market=market), "H6")
    assert result.status == FAIL and result.reason == "H6_SPREAD_WIDE"


def test_h6_fails_in_realtime_without_a_quote(contract, scene):
    market = clean_market(scene["window"], bid=None, ask=None)
    result = status_of(run(contract, scene, mode=REALTIME, market=market), "H6")
    assert result.status == FAIL and result.reason == "H6_QUOTE_UNKNOWN"


# --- H7 DEPTH_SHORT --------------------------------------------------------------------------

def test_h7_is_not_evaluated_over_history(contract, scene):
    result = status_of(run(contract, scene, mode=HISTORICAL, intended_qty=999.0), "H7")
    assert result.status == NOT_EVALUATED and result.blocking is False


def test_h7_fails_when_the_intended_qty_exceeds_safe_max(contract, scene):
    market = clean_market(scene["window"], bid=100_000.0, ask=100_000.1, safe_max_qty=0.05)
    result = status_of(run(contract, scene, mode=REALTIME, market=market, intended_qty=0.06), "H7")
    assert result.status == FAIL and result.reason == "H7_DEPTH_SHORT"


def test_h7_passes_within_safe_max(contract, scene):
    market = clean_market(scene["window"], bid=100_000.0, ask=100_000.1, safe_max_qty=0.05)
    result = status_of(run(contract, scene, mode=REALTIME, market=market, intended_qty=0.04), "H7")
    assert result.status == PASS


def test_h7_fails_in_realtime_when_safe_max_is_unavailable(contract, scene):
    market = clean_market(scene["window"], bid=100_000.0, ask=100_000.1, safe_max_qty=None)
    result = status_of(run(contract, scene, mode=REALTIME, market=market, intended_qty=0.04), "H7")
    assert result.status == FAIL and result.reason == "H7_SAFE_MAX_UNAVAILABLE"


# --- H8 COOLDOWN -----------------------------------------------------------------------------

def test_h8_passes_when_there_was_no_prior_exit(contract, scene):
    result = status_of(run(contract, scene), "H8")
    assert result.status == PASS and result.reason == "H8_NO_PRIOR_EXIT"


def test_h8_fails_inside_the_cooldown(contract, scene):
    state = clean_state(last_exit_ts_ms=scene["window"].bar_close_ms - 59 * MINUTE_MS)
    result = status_of(run(contract, scene, state=state), "H8")
    assert result.status == FAIL and result.reason == "H8_COOLDOWN"


def test_h8_passes_exactly_at_sixty_minutes(contract, scene):
    state = clean_state(last_exit_ts_ms=scene["window"].bar_close_ms - 60 * MINUTE_MS)
    assert status_of(run(contract, scene, state=state), "H8").status == PASS


# --- H9 POSITION_OPEN ------------------------------------------------------------------------

def test_h9_fails_while_a_position_is_open(contract, scene):
    result = status_of(run(contract, scene, state=clean_state(position_open=True)), "H9")
    assert result.status == FAIL and result.reason == "H9_POSITION_OPEN"


def test_h9_passes_when_flat(contract, scene):
    assert status_of(run(contract, scene), "H9").status == PASS


# --- H10 DAILY_LOSS_GUARD --------------------------------------------------------------------

@pytest.mark.parametrize("pnl_pct,expected", [(0.0, PASS), (-1.9, PASS), (-2.0, FAIL),
                                              (-2.1, FAIL), (3.0, PASS)])
def test_h10_daily_loss_guard(contract, scene, pnl_pct, expected):
    state = clean_state(day_realized_pnl_pct=pnl_pct, day_utc="2021-03-05")
    assert status_of(run(contract, scene, state=state), "H10").status == expected


# --- H11 CONSECUTIVE_LOSS --------------------------------------------------------------------

def test_h11_fails_on_four_losses_inside_the_block(contract, scene):
    state = clean_state(consecutive_losses=4,
                        last_loss_ts_ms=scene["window"].bar_close_ms - 3600_000)
    result = status_of(run(contract, scene, state=state), "H11")
    assert result.status == FAIL and result.reason == "H11_CONSECUTIVE_LOSS"


def test_h11_passes_on_three_losses(contract, scene):
    state = clean_state(consecutive_losses=3,
                        last_loss_ts_ms=scene["window"].bar_close_ms - 3600_000)
    assert status_of(run(contract, scene, state=state), "H11").status == PASS


def test_h11_expires_after_twenty_four_hours(contract, scene):
    state = clean_state(consecutive_losses=5,
                        last_loss_ts_ms=scene["window"].bar_close_ms - 86_400_000)
    assert status_of(run(contract, scene, state=state), "H11").status == PASS


# --- H12 FUNDING_WINDOW ----------------------------------------------------------------------

def test_h12_fails_just_before_a_settlement(contract, scene):
    market = clean_market(scene["window"],
                          next_funding_ts_ms=scene["window"].bar_close_ms + 4 * MINUTE_MS)
    result = status_of(run(contract, scene, market=market), "H12")
    assert result.status == FAIL and result.reason == "H12_FUNDING_WINDOW"


def test_h12_passes_exactly_five_minutes_out(contract, scene):
    market = clean_market(scene["window"],
                          next_funding_ts_ms=scene["window"].bar_close_ms + 5 * MINUTE_MS)
    assert status_of(run(contract, scene, market=market), "H12").status == PASS


def test_h12_fails_when_the_next_settlement_is_unknown(contract, scene):
    market = clean_market(scene["window"], next_funding_ts_ms=None)
    result = status_of(run(contract, scene, market=market), "H12")
    assert result.status == FAIL and result.reason == "H12_NEXT_FUNDING_UNKNOWN"


# --- H13 LEDGER_DIVERGENCE -------------------------------------------------------------------

def test_h13_fails_on_an_emergency_stop(contract, scene):
    state = clean_state(emergency_stop="LEDGER_DIVERGENCE")
    result = status_of(run(contract, scene, state=state), "H13")
    assert result.status == FAIL and result.observed["trigger"] == "LEDGER_DIVERGENCE"


@pytest.mark.parametrize("trigger", ["LEDGER_DIVERGENCE", "FEED_DOWN_5MIN",
                                     "SAFE_MAX_UNAVAILABLE"])
def test_h13_covers_every_contracted_emergency_trigger(contract, scene, trigger):
    state = clean_state(emergency_stop=trigger)
    assert status_of(run(contract, scene, state=state), "H13").status == FAIL


def test_h13_passes_when_nothing_is_wrong(contract, scene):
    assert status_of(run(contract, scene), "H13").status == PASS


# --- several at once -------------------------------------------------------------------------

def test_multiple_failures_are_all_reported(contract, scene):
    market = clean_market(scene["window"],
                          now_ms=scene["window"].bar_close_ms + 120_000,
                          oi_record_ts_ms=None, next_funding_ts_ms=None)
    state = clean_state(position_open=True, day_realized_pnl_pct=-5.0,
                        emergency_stop="FEED_DOWN_5MIN")
    results = run(contract, scene, market=market, state=state)
    failed = {r.filter_id for r in FILTERS.blocking(results)}
    assert {"H1", "H2", "H9", "H10", "H12", "H13"} <= failed


def test_reason_codes_are_unique_per_filter(contract, scene):
    reasons = [r.reason for r in run(contract, scene)]
    assert len(set(reasons)) == len(reasons)


def test_every_reason_code_names_its_filter(contract, scene):
    for result in run(contract, scene):
        assert result.reason.startswith(result.filter_id + "_")


def test_filter_coverage_mismatch_raises(contract, scene, monkeypatch):
    """A filter dropped by a future edit must break loudly, not silently stop being checked."""
    monkeypatch.setattr(contract.__class__, "hard_filter_ids",
                        property(lambda self: tuple(f"H{i}" for i in range(1, 15))))
    with pytest.raises(RuntimeError, match="coverage mismatch"):
        run(contract, scene)
