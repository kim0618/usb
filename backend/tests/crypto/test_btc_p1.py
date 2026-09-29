"""Tests for BTC-P1.

Three things are being protected. The label must not be knowable from the features, or the whole
study reports a leak as a discovery. The hand-written models must actually learn, or the study
reports NO_SIGNAL for something that was there. And the verdict must come from the frozen
thresholds rather than from whatever the numbers turned out to be.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.crypto.research.btc_p1 import contract as C
from app.crypto.research.btc_p1 import features as F
from app.crypto.research.btc_p1 import folds as FD
from app.crypto.research.btc_p1 import gates as G
from app.crypto.research.btc_p1 import metrics as MT
from app.crypto.research.btc_p1 import models as M
from app.crypto.research.btc_p1 import targets as T
from app.crypto.research.btc_p1 import windows as W

SEED = C.SEED


def _rng() -> np.random.Generator:
    return np.random.default_rng(SEED)


# --- windows ----------------------------------------------------------------------------

def test_window_max_matches_brute_force():
    x = _rng().normal(size=400)
    for w in (1, 2, 5, 17, 60):
        got = W.window_max(x, w)
        want = np.array([x[i:i + w].max() for i in range(len(x) - w + 1)])
        assert np.allclose(got, want), w


def test_window_min_matches_brute_force():
    x = _rng().normal(size=400)
    for w in (1, 3, 17, 60):
        got = W.window_min(x, w)
        want = np.array([x[i:i + w].min() for i in range(len(x) - w + 1)])
        assert np.allclose(got, want), w


def test_forward_max_starts_at_the_next_bar():
    # The decision is taken at the close of bar i, so bar i itself can never satisfy the label.
    x = np.array([9.0, 1.0, 2.0, 3.0, 1.0])
    got = W.forward_max(x, 2)
    assert got[0] == 2.0            # max(x[1], x[2]), not 9.0
    assert got[1] == 3.0
    assert np.isnan(got[-2:]).all()


def test_forward_windows_are_nan_where_incomplete():
    x = _rng().normal(size=100)
    for h in (1, 10, 99):
        got = W.forward_max(x, h)
        assert np.isnan(got[len(x) - h:]).all(), h
        assert np.isfinite(got[:len(x) - h]).all(), h


def test_shift_is_backward_only():
    with pytest.raises(ValueError):
        W.shift(np.arange(5.0), 0)
    with pytest.raises(ValueError):
        W.shift(np.arange(5.0), -1)
    got = W.shift(np.arange(5.0), 2)
    assert np.isnan(got[:2]).all() and list(got[2:]) == [0.0, 1.0, 2.0]


def test_rolling_statistics_use_only_the_past():
    x = _rng().normal(size=200)
    mean, std = W.rolling_mean(x, 10), W.rolling_std(x, 10)
    assert np.isnan(mean[:9]).all() and np.isnan(std[:9]).all()
    for i in (9, 50, 199):
        assert mean[i] == pytest.approx(x[i - 9:i + 1].mean())
        assert std[i] == pytest.approx(x[i - 9:i + 1].std())


# --- targets ----------------------------------------------------------------------------

def _specs() -> tuple[T.TargetSpec, ...]:
    return T.build_specs(C.TARGET_GRID)


def test_the_target_grid_is_the_frozen_one():
    specs = _specs()
    assert len(specs) == 28
    assert sum(1 for s in specs if s.kind == T.PATH) == 14
    assert sum(1 for s in specs if s.kind == T.ENDPOINT) == 14
    assert {s.horizon_minutes for s in specs} == {240, 720, 1440}


def test_path_touch_needs_only_a_touch():
    # Price spikes to +1% mid-window and comes back; a path target counts it, an endpoint does not.
    close = np.array([100.0, 100.0, 100.0, 100.0])
    high = np.array([100.0, 101.0, 100.0, 100.0])
    low = np.full(4, 100.0)
    path = T.TargetSpec("t", T.UP, 100, 2, T.PATH)
    endpoint = T.TargetSpec("t", T.UP, 100, 2, T.ENDPOINT)
    assert T.labels(path, high, low, close)[0] == 1.0
    assert T.labels(endpoint, high, low, close)[0] == 0.0


def test_down_targets_read_the_low():
    close = np.full(4, 100.0)
    high = np.full(4, 100.0)
    low = np.array([100.0, 98.9, 100.0, 100.0])
    spec = T.TargetSpec("t", T.DOWN, 100, 2, T.PATH)
    assert T.labels(spec, high, low, close)[0] == 1.0


def test_a_threshold_exactly_touched_counts():
    close = np.full(3, 100.0)
    high = np.array([100.0, 100.5, 100.0])
    low = np.full(3, 100.0)
    spec = T.TargetSpec("t", T.UP, 50, 1, T.PATH)
    assert T.labels(spec, high, low, close)[0] == 1.0


def test_an_incomplete_horizon_is_nan_not_zero():
    # Filling the tail with zero would teach the model that the sample's last day is always calm.
    close = np.full(10, 100.0)
    spec = T.TargetSpec("t", T.UP, 100, 4, T.PATH)
    got = T.labels(spec, close, close, close)
    assert np.isnan(got[-4:]).all()
    assert not np.isnan(got[:-4]).any()


def test_up_and_down_can_both_happen():
    # The contract models them as independent labels for exactly this reason.
    close = np.full(5, 100.0)
    high = np.array([100.0, 103.0, 100.0, 100.0, 100.0])
    low = np.array([100.0, 100.0, 97.0, 100.0, 100.0])
    up = T.TargetSpec("u", T.UP, 200, 3, T.PATH)
    down = T.TargetSpec("d", T.DOWN, 200, 3, T.PATH)
    assert T.labels(up, high, low, close)[0] == 1.0
    assert T.labels(down, high, low, close)[0] == 1.0


def test_labels_never_read_the_decision_bar():
    rng = _rng()
    n = 500
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    high, low = close * 1.001, close * 0.999
    spec = T.TargetSpec("t", T.UP, 50, 30, T.PATH)
    before = T.labels(spec, high, low, close)
    # Blow up the decision bar's own high. Nothing about the label may move.
    high2 = high.copy()
    high2[100] *= 1.5
    after = T.labels(spec, high2, low, close)
    assert after[100] == before[100]


# --- PIT / leakage ------------------------------------------------------------------------

def test_appending_future_rows_does_not_change_past_features():
    rng = _rng()
    n = 60_000
    grid = _synthetic_grid(rng, n)
    base = F.build(grid)
    longer = F.build(_synthetic_grid(rng, n, extend=grid))
    check = slice(50_000, 55_000)
    for name in F.NAMES:
        a, b = base[name][check], longer[name][check]
        assert np.allclose(a, b, equal_nan=True), name


def _synthetic_grid(rng, n: int, extend: dict | None = None) -> dict[str, np.ndarray]:
    if extend is not None:
        extra = 5_000
        more = _synthetic_grid(rng, extra)
        return {k: np.concatenate([extend[k], more[k]]) if k != "ts"
                else np.concatenate([extend["ts"],
                                     extend["ts"][-1] + 60_000 * np.arange(1, extra + 1)])
                for k in extend}
    close = 30_000 * np.exp(np.cumsum(rng.normal(0, 0.0004, n)))
    return {
        "ts": 1_612_137_600_000 + 60_000 * np.arange(n, dtype=np.int64),
        "open": close, "close": close, "high": close * 1.0006, "low": close * 0.9994,
        "volume": np.abs(rng.normal(10, 2, n)), "turnover": np.abs(rng.normal(1e5, 1e4, n)),
        "mark_close": close * (1 + rng.normal(0, 1e-5, n)),
        "index_close": close,
        "oi": 50_000 * np.exp(np.cumsum(rng.normal(0, 0.0002, n))),
        "funding_last": rng.normal(1e-4, 2e-5, n),
    }


def test_scaler_is_fit_on_training_rows_only():
    rng = _rng()
    x_train = rng.normal(size=(2000, 5))
    x_valid = rng.normal(size=(500, 5)) * 50 + 100
    scaler = M.RobustScaler().fit(x_train)
    center, scale = scaler.center.copy(), scaler.scale.copy()
    scaler.transform(x_valid)
    assert np.array_equal(scaler.center, center)
    assert np.array_equal(scaler.scale, scale)


def test_a_constant_training_column_does_not_explode():
    x = np.ones((100, 3))
    x[:, 1] = np.arange(100)
    scaler = M.RobustScaler().fit(x)
    out = scaler.transform(x)
    assert np.isfinite(out).all()


# --- folds ------------------------------------------------------------------------------

def test_folds_are_chronological_and_expanding():
    folds = FD.build(C.SAMPLE_START, C.FOLD_STARTS, C.END, C.EMBARGO_MINUTES)
    assert len(folds) == C.S6_TOTAL_FOLDS
    for f in folds:
        assert f.train_start_ms < f.train_end_ms < f.valid_start_ms < f.valid_end_ms
    for a, b in zip(folds, folds[1:]):
        assert a.train_start_ms == b.train_start_ms      # expanding, not rolling
        assert a.train_end_ms < b.train_end_ms
        assert a.valid_end_ms == b.valid_start_ms        # no gap, no overlap


def test_the_embargo_is_one_full_maximum_horizon():
    # Without it, a training row's 24h label describes the start of the validation window.
    folds = FD.build(C.SAMPLE_START, C.FOLD_STARTS, C.END, C.EMBARGO_MINUTES)
    for f in folds:
        gap_minutes = (f.valid_start_ms - f.train_end_ms) / 60_000
        assert gap_minutes == C.MAX_HORIZON_MINUTES


def test_the_calibration_split_is_also_embargoed():
    ts = np.arange(10_000, dtype=np.int64) * 60_000
    model, calib = FD.split_train(ts, C.EMBARGO_MINUTES)
    assert model.any() and calib.any()
    assert not (model & calib).any()
    gap = (ts[calib].min() - ts[model].max()) / 60_000
    assert gap >= C.EMBARGO_MINUTES


def test_the_model_slice_comes_before_the_calibration_slice():
    ts = np.arange(10_000, dtype=np.int64) * 60_000
    model, calib = FD.split_train(ts, C.EMBARGO_MINUTES)
    assert ts[model].max() < ts[calib].min()


def test_decision_rows_respect_warmup_and_horizon():
    ts = np.arange(100_000, dtype=np.int64) * 60_000
    rows = FD.decision_rows(ts, step_minutes=60, warmup_minutes=1000, max_horizon_minutes=500)
    assert rows.min() >= 1000
    assert rows.max() <= len(ts) - 500 - 1
    assert np.all(np.diff(rows) == 60)


# --- models -----------------------------------------------------------------------------

def _planted(rng, n: int, d: int = 34):
    x = rng.normal(size=(n, d))
    z = 1.4 * (x[:, 0] * x[:, 1]) + 1.1 * np.sign(x[:, 2]) * np.sqrt(np.abs(x[:, 3])) \
        - 0.8 * x[:, 4] ** 2
    z -= z.mean()
    y = (rng.uniform(size=n) < 1 / (1 + np.exp(-z))).astype(float)
    return x, y


def test_baseline_predicts_the_training_frequency():
    y = np.array([1.0, 1.0, 0.0, 0.0, 0.0])
    p = M.Baseline().fit(y).predict(np.zeros((3, 2)))
    assert np.allclose(p, 0.4)


def test_probabilities_stay_inside_the_unit_interval():
    rng = _rng()
    x, y = _planted(rng, 4000)
    for model in (M.RidgeLogistic(), M.GradientBoosting(n_trees=20)):
        p = model.fit(x, y).predict(x)
        assert p.min() >= 0.0 and p.max() <= 1.0
        assert np.isfinite(p).all()


def test_models_are_deterministic():
    rng = _rng()
    x, y = _planted(rng, 3000)
    a = M.GradientBoosting(n_trees=25).fit(x, y).predict(x)
    b = M.GradientBoosting(n_trees=25).fit(x, y).predict(x)
    assert np.array_equal(a, b)
    c = M.RidgeLogistic().fit(x, y).predict(x)
    d = M.RidgeLogistic().fit(x, y).predict(x)
    assert np.array_equal(c, d)


def test_the_booster_recovers_a_planted_structure():
    """The contract's precondition for reporting M2 at all.

    Without it a bug in the hand-written booster would be indistinguishable from an absence of
    signal, and this study exists to tell those two apart.
    """
    rng = _rng()
    x, y = _planted(rng, 20_000)
    tr, va = slice(0, 14_000), slice(14_000, 20_000)
    ref = M.Baseline().fit(y[tr]).predict(x[va])
    p1 = M.RidgeLogistic().fit(x[tr], y[tr]).predict(x[va])
    p2 = M.GradientBoosting().fit(x[tr], y[tr]).predict(x[va])
    s1 = MT.brier_skill(y[va], p1, ref)
    s2 = MT.brier_skill(y[va], p2, ref)
    assert s1 > 0.01, s1
    assert s2 > s1, (s1, s2)
    assert MT.roc_auc(y[va], p2) > 0.65


def test_the_booster_finds_nothing_in_noise():
    rng = _rng()
    x = rng.normal(size=(20_000, 34))
    y = (rng.uniform(size=20_000) < 0.3).astype(float)
    tr, va = slice(0, 14_000), slice(14_000, 20_000)
    ref = M.Baseline().fit(y[tr]).predict(x[va])
    p2 = M.GradientBoosting().fit(x[tr], y[tr]).predict(x[va])
    assert MT.brier_skill(y[va], p2, ref) < 0.01
    assert abs(MT.roc_auc(y[va], p2) - 0.5) < 0.05


def test_trees_respect_the_leaf_minimum():
    rng = _rng()
    x, y = _planted(rng, 5000)
    model = M.GradientBoosting(n_trees=5, min_samples_leaf=500).fit(x, y)
    binned = model._bin(x)
    for tree in model.trees:
        leaves = tree.predict(binned)
        for value in np.unique(leaves):
            assert int((leaves == value).sum()) >= 500 or len(np.unique(leaves)) == 1


# --- calibration --------------------------------------------------------------------------

def test_isotonic_is_monotone():
    rng = _rng()
    s = rng.uniform(size=2000)
    y = (rng.uniform(size=2000) < s).astype(float)
    out = M.Isotonic().fit(s, y).transform(np.sort(s))
    assert np.all(np.diff(out) >= -1e-12)


def test_isotonic_repairs_a_known_miscalibration():
    rng = _rng()
    n = 20_000
    s_cal = rng.uniform(0.02, 0.98, n)
    true = 1 / (1 + np.exp(-0.5 * np.log(s_cal / (1 - s_cal))))
    y_cal = (rng.uniform(size=n) < true).astype(float)
    iso = M.Isotonic().fit(s_cal, y_cal)

    s = rng.uniform(0.02, 0.98, 50_000)
    y = (rng.uniform(size=50_000) < 1 / (1 + np.exp(-0.5 * np.log(s / (1 - s))))).astype(float)
    before = MT.expected_calibration_error(y, s, C.RELIABILITY_BUCKETS)
    after = MT.expected_calibration_error(y, iso.transform(s), C.RELIABILITY_BUCKETS)
    assert before > 0.05
    assert after < before / 3


def test_isotonic_on_an_empty_slice_is_a_no_op():
    iso = M.Isotonic().fit(np.empty(0), np.empty(0))
    s = np.array([0.1, 0.9])
    assert np.array_equal(iso.transform(s), s)


# --- metrics ----------------------------------------------------------------------------

def test_roc_auc_matches_a_hand_computed_case():
    y = np.array([0.0, 0.0, 1.0, 1.0])
    p = np.array([0.1, 0.4, 0.35, 0.8])
    assert MT.roc_auc(y, p) == pytest.approx(0.75)


def test_roc_auc_of_constant_predictions_is_one_half():
    y = np.array([1.0, 1.0, 0.0, 0.0])
    assert MT.roc_auc(y, np.full(4, 0.7)) == pytest.approx(0.5)


def test_average_precision_of_a_perfect_ranking_is_one():
    y = np.array([1.0, 1.0, 0.0, 0.0])
    assert MT.average_precision(y, np.array([0.9, 0.8, 0.2, 0.1])) == pytest.approx(1.0)


def test_brier_skill_is_zero_against_itself():
    y = np.array([1.0, 0.0, 1.0, 0.0])
    ref = np.full(4, 0.5)
    assert MT.brier_skill(y, ref, ref) == pytest.approx(0.0)


def test_brier_skill_is_negative_for_a_worse_model():
    y = np.array([1.0, 1.0, 0.0, 0.0])
    ref = np.full(4, 0.5)
    worse = np.array([0.1, 0.1, 0.9, 0.9])
    assert MT.brier_skill(y, worse, ref) < 0


def test_log_loss_is_finite_at_certainty():
    y = np.array([1.0, 0.0])
    assert np.isfinite(MT.log_loss(y, np.array([1.0, 0.0])))


def test_calibration_line_recovers_slope_one_on_honest_probabilities():
    rng = _rng()
    p = rng.uniform(0.05, 0.95, 60_000)
    y = (rng.uniform(size=60_000) < p).astype(float)
    line = MT.calibration_line(y, p)
    assert line["slope"] == pytest.approx(1.0, abs=0.1)
    assert line["intercept"] == pytest.approx(0.0, abs=0.1)


def test_calibration_line_detects_overconfidence():
    rng = _rng()
    p = rng.uniform(0.02, 0.98, 60_000)
    true = 1 / (1 + np.exp(-0.5 * np.log(p / (1 - p))))
    y = (rng.uniform(size=60_000) < true).astype(float)
    assert MT.calibration_line(y, p)["slope"] < 0.7


def test_expected_calibration_error_is_zero_when_perfect():
    p = np.repeat([0.05, 0.95], 10_000)
    y = np.concatenate([np.zeros(9500), np.ones(500), np.zeros(500), np.ones(9500)])
    assert MT.expected_calibration_error(y, p, 10) < 0.01


def test_reliability_buckets_cover_every_row():
    rng = _rng()
    p = rng.uniform(size=5000)
    y = (rng.uniform(size=5000) < p).astype(float)
    rows = MT.reliability(y, p, C.RELIABILITY_BUCKETS)
    assert len(rows) == C.RELIABILITY_BUCKETS
    assert sum(r["n"] for r in rows) == 5000


def test_lift_is_relative_to_the_base_rate():
    y = np.concatenate([np.ones(30), np.zeros(70)])
    p = np.concatenate([np.full(30, 0.9), np.full(70, 0.1)])
    rows = MT.confidence_buckets(y, p, (0.65,), base_rate=0.3)
    assert rows[0]["n"] == 30
    assert rows[0]["actual_rate"] == pytest.approx(1.0)
    assert rows[0]["lift"] == pytest.approx(1 / 0.3)


# --- gates ------------------------------------------------------------------------------

def _pooled(**over) -> dict:
    base = {"n": 40_000, "positives": 10_000, "brier_skill": 0.05, "roc_auc": 0.60,
            "ece": 0.02, "calibration_slope": 1.0,
            "confidence": [{"threshold": t, "n": 1000, "lift": 1.5, "actual_rate": 0.4,
                            "mean_predicted": 0.7} for t in C.CONFIDENCE_THRESHOLDS]}
    base.update(over)
    return base


def test_a_clean_result_is_strong():
    out = G.evaluate(_pooled(), [0.05] * 9, {y: 0.05 for y in C.VALIDATION_YEARS})
    assert out["verdict"] == C.STRONG


def test_too_few_positives_is_inconclusive_not_no_signal():
    out = G.evaluate(_pooled(positives=100), [0.05] * 9, {y: 0.05 for y in C.VALIDATION_YEARS})
    assert out["verdict"] == C.INCONCLUSIVE


def test_sample_check_runs_before_the_signal_checks():
    # A tiny sample with terrible metrics still reports INCONCLUSIVE: it was never measurable.
    out = G.evaluate(_pooled(n=100, positives=10, brier_skill=-9.0, roc_auc=0.1),
                     [-1.0] * 9, {y: -1.0 for y in C.VALIDATION_YEARS})
    assert out["verdict"] == C.INCONCLUSIVE


def test_good_ranking_with_bad_calibration_is_only_weak():
    out = G.evaluate(_pooled(ece=0.30, calibration_slope=0.2), [0.05] * 9,
                     {y: 0.05 for y in C.VALIDATION_YEARS})
    assert out["verdict"] == C.WEAK


def test_failing_fold_consistency_blocks_strong():
    skills = [0.05] * 4 + [-0.01] * 5
    out = G.evaluate(_pooled(), skills, {y: 0.05 for y in C.VALIDATION_YEARS})
    assert out["verdict"] == C.WEAK
    assert not out["checks"]["S6_fold_consistency"]["pass"]


def test_failing_year_consistency_blocks_strong():
    years = {"2022": 0.05, "2023": -0.01, "2024": -0.02, "2025": 0.01, "2026": -0.01}
    out = G.evaluate(_pooled(), [0.05] * 9, years)
    assert out["verdict"] == C.WEAK
    assert not out["checks"]["S7_year_consistency"]["pass"]


def test_no_skill_is_no_signal():
    out = G.evaluate(_pooled(brier_skill=-0.01, roc_auc=0.50), [-0.01] * 9,
                     {y: -0.01 for y in C.VALIDATION_YEARS})
    assert out["verdict"] == C.NO_SIGNAL


def test_a_thin_confidence_bucket_blocks_strong():
    pooled = _pooled(confidence=[{"threshold": t, "n": 10, "lift": 5.0, "actual_rate": 0.9,
                                  "mean_predicted": 0.8} for t in C.CONFIDENCE_THRESHOLDS])
    out = G.evaluate(pooled, [0.05] * 9, {y: 0.05 for y in C.VALIDATION_YEARS})
    assert out["verdict"] == C.WEAK
    assert not out["checks"]["S5_confidence_bucket"]["pass"]


def test_best_of_prefers_the_better_verdict():
    assert G.best_of({"M1": {"verdict": C.NO_SIGNAL}, "M2": {"verdict": C.WEAK}}) == C.WEAK
    assert G.best_of({"M1": {"verdict": C.STRONG}, "M2": {"verdict": C.NO_SIGNAL}}) == C.STRONG


# --- contract ---------------------------------------------------------------------------

def test_the_contract_document_still_states_every_frozen_value():
    C.verify_bindings()


def test_the_contract_hash_matches_the_freeze_record():
    assert C.require_frozen() == C.sha256()


def test_an_edited_contract_is_refused():
    body = C.CONTRACT.read_text(encoding="utf-8").replace("ROC AUC > **0.550**",
                                                          "ROC AUC > **0.400**")
    with pytest.raises(C.ContractMismatch):
        C.verify_bindings(body)


def test_the_freeze_record_declares_what_was_allowed_before_it():
    payload = json.loads(Path(C.FREEZE).read_text())
    assert payload["frozen_before_any_performance_metric"] is True
    for banned in ("ROC AUC", "Brier", "log loss"):
        assert banned in payload["forbidden_before_freeze"]


# --- direction vs volatility diagnostic ----------------------------------------------------

def _fake_predictions(n: int, *, directional: bool) -> dict:
    """Two aligned target series where volatility is predictable and direction may not be."""
    rng = _rng()
    vol = rng.uniform(0.1, 0.9, n)                  # the model knows how violent the tape is
    coin = rng.uniform(size=n) < 0.5                # but not which way, unless `directional`
    touch_up = (rng.uniform(size=n) < vol) & (coin | directional)
    touch_down = (rng.uniform(size=n) < vol) & (~coin | directional)
    if directional:
        # Make DOWN happen exactly where the model says DOWN is likelier.
        touch_down = (rng.uniform(size=n) < vol) & ~coin
        touch_up = (rng.uniform(size=n) < vol) & coin
    p_up = vol / 2 + (0.2 * coin if directional else 0.0)
    p_down = vol / 2 + (0.2 * ~coin if directional else 0.0)
    ts = np.arange(n, dtype=np.int64) * 60_000
    return {
        "ts_ms": np.concatenate([ts, ts]),
        "target": np.array(["4H_UP_050"] * n + ["4H_DOWN_050"] * n),
        "y": np.concatenate([touch_up.astype(float), touch_down.astype(float)]),
        "p_m2_cal": np.concatenate([p_up, p_down]),
        "p_m1_cal": np.concatenate([p_up, p_down]),
    }


def test_direction_diagnostic_separates_volatility_from_direction(monkeypatch):
    from app.crypto.research.btc_p1 import direction as D

    monkeypatch.setattr(D, "_load", lambda: _fake_predictions(40_000, directional=False))
    row = D.analyse("M2")["4H_050"]
    # Knowing how violent the tape is ranks "either side touched" well ...
    assert row["volatility"]["roc_auc"] > 0.60
    # ... while carrying no information about which side it was.
    assert abs(row["direction"]["roc_auc"] - 0.5) < 0.05


def test_direction_diagnostic_detects_real_direction(monkeypatch):
    from app.crypto.research.btc_p1 import direction as D

    monkeypatch.setattr(D, "_load", lambda: _fake_predictions(40_000, directional=True))
    row = D.analyse("M2")["4H_050"]
    assert row["direction"]["roc_auc"] > 0.70


def test_direction_diagnostic_refuses_misaligned_rows(monkeypatch):
    from app.crypto.research.btc_p1 import direction as D

    data = _fake_predictions(1_000, directional=False)
    data["ts_ms"][1_000:] += 60_000          # shift the DOWN series by one bar
    monkeypatch.setattr(D, "_load", lambda: data)
    with pytest.raises(RuntimeError, match="not aligned"):
        D.analyse("M2")


def test_direction_diagnostic_feeds_no_gate(monkeypatch):
    from app.crypto.research.btc_p1 import direction as D

    monkeypatch.setattr(D, "_load", lambda: _fake_predictions(2_000, directional=False))
    payload = D.run(write=False)
    assert payload["feeds_any_gate"] is False
    assert payload["status"] == "POST_HOC_DIAGNOSTIC"


def test_feature_families_are_capped_at_six():
    assert len(F.FAMILIES) == 6
    assert len(F.NAMES) == 34
    assert set(F.FAMILY.values()) == set(F.FAMILIES)
    assert len(set(F.NAMES)) == len(F.NAMES)
