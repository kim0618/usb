"""Tests for BTC-P2.

The study exists because P1's gate measured volatility while its question asked about direction,
so these tests protect the things that would let that happen again: the label must not be
guessable from the decision bar, the gate must come from out-of-sample P1 output, the verdict
must rest on UP-versus-DOWN discrimination rather than each side's own AUC, and an unknowable
case must stay unknown rather than being filled in.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.crypto.research.btc_p2 import contract as C
from app.crypto.research.btc_p2 import external as X
from app.crypto.research.btc_p2 import features as F
from app.crypto.research.btc_p2 import folds as FD
from app.crypto.research.btc_p2 import gate as G
from app.crypto.research.btc_p2 import runner as R
from app.crypto.research.btc_p2 import targets as T

REPO_ROOT = Path(__file__).resolve().parents[3]
P1_PRESENT = (REPO_ROOT / G.P1_PREDICTIONS).exists()


def _rng() -> np.random.Generator:
    return np.random.default_rng(C.SEED)


# --- first touch --------------------------------------------------------------------------

def _spec(kind: str = T.FIRST_TOUCH, horizon: int = 4, bp: int = 100) -> T.DirectionSpec:
    return T.DirectionSpec("t", kind, horizon, bp)


def test_up_first_when_the_up_side_is_touched_earlier():
    close = np.full(8, 100.0)
    high = np.array([100.0, 101.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    low = np.array([100.0, 100.0, 100.0, 98.9, 100.0, 100.0, 100.0, 100.0])
    got = T.first_touch(_spec(horizon=4), high, low, close, np.array([0]))
    assert got[0] == T.UP_FIRST


def test_down_first_when_the_down_side_is_touched_earlier():
    close = np.full(8, 100.0)
    high = np.array([100.0, 100.0, 100.0, 101.0, 100.0, 100.0, 100.0, 100.0])
    low = np.array([100.0, 98.9, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    got = T.first_touch(_spec(horizon=4), high, low, close, np.array([0]))
    assert got[0] == T.DOWN_FIRST


def test_neither_when_no_side_is_touched():
    close = np.full(8, 100.0)
    got = T.first_touch(_spec(horizon=4), close * 1.001, close * 0.999, close, np.array([0]))
    assert got[0] == T.NEITHER


def test_a_single_bar_clearing_both_sides_is_ambiguous_not_guessed():
    # One minute's high clears +1% and its low clears -1%; the order inside that minute is not in
    # the data, and a rule for filling it would be the directional signal being measured.
    close = np.full(8, 100.0)
    high = np.array([100.0, 101.5, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    low = np.array([100.0, 98.5, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    got = T.first_touch(_spec(horizon=4), high, low, close, np.array([0]))
    assert got[0] == T.AMBIGUOUS
    assert not T.directional_mask(got)[0]


def test_the_decision_bar_itself_can_never_trigger_a_label():
    close = np.full(8, 100.0)
    high = close.copy()
    low = close.copy()
    high[0] = 200.0                     # a spike on the decision bar
    low[0] = 50.0
    got = T.first_touch(_spec(horizon=4), high, low, close, np.array([0]))
    assert got[0] == T.NEITHER


def test_a_touch_after_the_horizon_does_not_count():
    close = np.full(10, 100.0)
    high = close.copy()
    high[5] = 101.0                     # beyond a 4-bar horizon
    got = T.first_touch(_spec(horizon=4), high, close * 0.9999, close, np.array([0]))
    assert got[0] == T.NEITHER


def test_exactly_reaching_the_threshold_counts():
    close = np.full(6, 100.0)
    high = close.copy()
    high[1] = 101.0
    got = T.first_touch(_spec(horizon=4), high, close, close, np.array([0]))
    assert got[0] == T.UP_FIRST


def test_chunking_does_not_change_labels(monkeypatch):
    rng = _rng()
    n = 5000
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.0006, n)))
    high, low = close * 1.0008, close * 0.9992
    rows = np.arange(100, n - 400, 7)
    spec = _spec(horizon=240)
    whole = T.first_touch(spec, high, low, close, rows)
    monkeypatch.setattr(T, "CHUNK", 37)
    chunked = T.first_touch(spec, high, low, close, rows)
    assert np.array_equal(whole, chunked)


# --- endpoint and dominant excursion -------------------------------------------------------

def test_endpoint_reads_only_the_final_bar():
    close = np.full(10, 100.0)
    close[2] = 150.0                    # a spike inside the window
    close[4] = 100.0                    # but it ends flat
    got = T.endpoint_direction(_spec(T.ENDPOINT, horizon=4), close, np.array([0]))
    assert got[0] == T.NEITHER


def test_endpoint_direction_uses_the_threshold():
    close = np.full(10, 100.0)
    close[4] = 101.5
    assert T.endpoint_direction(_spec(T.ENDPOINT, horizon=4), close,
                                np.array([0]))[0] == T.UP_FIRST
    close[4] = 98.5
    assert T.endpoint_direction(_spec(T.ENDPOINT, horizon=4), close,
                                np.array([0]))[0] == T.DOWN_FIRST


def test_dominant_excursion_requires_the_threshold():
    # A window that wobbles by a few basis points has no direction a trade could have captured.
    close = np.full(10, 100.0)
    high = close * 1.0005
    low = close * 0.9999
    got = T.dominant_excursion(_spec(T.DOMINANT_EXCURSION, horizon=4), high, low, close,
                               np.array([0]))
    assert got[0] == T.NEITHER


def test_class_counts_partition_the_rows():
    labels = np.array([T.UP_FIRST, T.DOWN_FIRST, T.NEITHER, T.AMBIGUOUS, T.UP_FIRST])
    counts = T.class_counts(labels)
    assert counts["UP_FIRST"] + counts["DOWN_FIRST"] + counts["NEITHER"] + counts["AMBIGUOUS"] \
        == counts["total"]
    assert T.directional_mask(labels).sum() == 3


# --- gate ---------------------------------------------------------------------------------

def test_the_gate_reads_per_fold_predictions_not_the_frozen_artifact():
    # Applying the forward artifact, trained through 2025-12-31, to earlier rows would let it
    # choose its own evaluation set.
    source = Path(G.__file__).read_text(encoding="utf-8")
    assert "predictions_v1.parquet" in source
    assert "btc_p1_forward" not in source
    assert "artifact" not in source.split('"""')[2]


@pytest.mark.skipif(not P1_PRESENT, reason="P1 predictions not present")
def test_gate_frames_are_aligned_and_out_of_sample():
    frame = G.load(240, 100)
    assert len(frame.ts_ms) == len(frame.p_up) == len(frame.p_down)
    assert np.all(np.diff(frame.ts_ms) > 0)
    assert set(np.unique(frame.fold).tolist()) <= set(range(1, 10))


@pytest.mark.skipif(not P1_PRESENT, reason="P1 predictions not present")
def test_large_move_is_the_preregistered_maximum():
    frame = G.load(240, 100)
    assert np.array_equal(frame.large_move, np.maximum(frame.p_up, frame.p_down))
    assert np.array_equal(frame.separation, frame.p_down - frame.p_up)


@pytest.mark.skipif(not P1_PRESENT, reason="P1 predictions not present")
def test_an_unknown_combo_is_refused():
    with pytest.raises(G.GateError):
        G.load(240, 999)


def test_the_gate_cut_is_a_training_quantile():
    train = np.linspace(0.0, 1.0, 1001)
    assert FD.gate_threshold(train, 0.75) == pytest.approx(0.75, abs=1e-3)
    with pytest.raises(ValueError):
        FD.gate_threshold(np.empty(0), 0.75)


# --- folds --------------------------------------------------------------------------------

def test_there_are_eight_folds_and_the_first_p1_fold_is_training_only():
    built = FD.build()
    assert len(built) == C.D2_TOTAL_FOLDS == 8
    assert built[0].train_p1_folds == (1,)
    assert built[0].valid_p1_fold == 2
    assert built[-1].valid_p1_fold == 9
    assert 1 not in [f.valid_p1_fold for f in built]


def test_training_folds_always_precede_the_validation_fold():
    for fold in FD.build():
        assert all(p < fold.valid_p1_fold for p in fold.train_p1_folds)


def test_the_embargo_separates_training_from_validation():
    p1_fold = np.array([1] * 100 + [2] * 100)
    ts = np.arange(200, dtype=np.int64) * 60_000 * 60      # hourly
    fold = FD.build()[0]
    train, valid = fold.masks(p1_fold, ts)
    assert train.any() and valid.any()
    gap_minutes = (ts[valid].min() - ts[train].max()) / 60_000
    assert gap_minutes >= FD.EMBARGO_MINUTES


def test_training_and_validation_never_overlap():
    p1_fold = np.repeat(np.arange(1, 10), 50)
    ts = np.arange(450, dtype=np.int64) * 60_000 * 60
    for fold in FD.build():
        train, valid = fold.masks(p1_fold, ts)
        assert not (train & valid).any()
        if train.any() and valid.any():
            assert ts[train].max() < ts[valid].min()


# --- features -----------------------------------------------------------------------------

def _synthetic(n: int = 60_000) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    rng = _rng()
    close = 30_000 * np.exp(np.cumsum(rng.normal(0, 0.0004, n)))
    grid = {
        "ts": 1_612_137_600_000 + 60_000 * np.arange(n, dtype=np.int64),
        "open": close * (1 + rng.normal(0, 1e-5, n)), "close": close,
        "high": close * 1.0006, "low": close * 0.9994,
        "mark_close": close * (1 + rng.normal(0, 1e-5, n)), "index_close": close,
        "oi": 50_000 * np.exp(np.cumsum(rng.normal(0, 0.0002, n))),
        "funding_last": rng.normal(1e-4, 2e-5, n),
    }
    external = {"ts": grid["ts"], "um_close": close * (1 + rng.normal(0, 2e-5, n)),
                "spot_close": close * (1 - 4e-4)}
    return grid, external


def test_there_is_no_volatility_family():
    assert "F2" in F.FAMILIES
    assert all("rv_" not in name and "vol_ratio" not in name for name in F.ALL_NAMES)
    assert "volatility" not in " ".join(F.FAMILY_TITLE[f] for f in F.FAMILIES)


def test_the_feature_sets_are_the_frozen_sizes():
    assert len(F.D2_NAMES) == 34
    assert len(F.EXTERNAL_NAMES) == 6
    assert len(F.ALL_NAMES) == 40
    assert len(set(F.ALL_NAMES)) == 40
    assert set(F.FAMILY.values()) == set(F.FAMILIES)


def test_appending_future_rows_does_not_change_past_features():
    grid, external = _synthetic()
    base = F.build(grid, external)
    longer_grid = {k: np.concatenate([v, v[-5_000:]]) for k, v in grid.items()}
    longer_grid["ts"] = np.concatenate(
        [grid["ts"], grid["ts"][-1] + 60_000 * np.arange(1, 5_001)])
    longer_external = {k: np.concatenate([v, v[-5_000:]]) for k, v in external.items()}
    longer_external["ts"] = longer_grid["ts"]
    longer = F.build(longer_grid, longer_external)
    check = slice(50_000, 55_000)
    for name in F.ALL_NAMES:
        assert np.allclose(base[name][check], longer[name][check], equal_nan=True), name


def test_signed_features_are_divided_by_volatility():
    # Doubling every return must not double the normalised features, or the model inherits P1's
    # volatility signal under a different name.
    grid, external = _synthetic(50_000)
    calm = F.build(grid, external)
    loud = dict(grid)
    base = grid["close"][0]
    loud["close"] = base * (grid["close"] / base) ** 2
    loud["open"] = base * (grid["open"] / base) ** 2
    loud["high"] = base * (grid["high"] / base) ** 2
    loud["low"] = base * (grid["low"] / base) ** 2
    scaled = F.build(loud, external)
    check = slice(45_000, 50_000)
    ratio = np.std(scaled["ret_z_1h"][check]) / max(np.std(calm["ret_z_1h"][check]), 1e-12)
    assert 0.5 < ratio < 2.0, ratio


def test_the_external_family_is_absent_without_external_data():
    grid, _ = _synthetic(50_000)
    built = F.build(grid, None)
    assert set(built) == set(F.D2_NAMES)
    assert not any(name in built for name in F.EXTERNAL_NAMES)


# --- external archive ---------------------------------------------------------------------

def test_microsecond_timestamps_are_normalised():
    # Binance switched spot klines to microseconds in 2025 while futures stayed in milliseconds.
    assert X._to_milliseconds(1_735_689_600_000_000) == 1_735_689_600_000
    assert X._to_milliseconds(1_735_689_600_000) == 1_735_689_600_000


def test_a_long_forward_fill_is_refused(monkeypatch, tmp_path):
    # The unit bug produced a two-year fill that looked like a working series.
    monkeypatch.setattr(X, "CACHE", tmp_path / "cache.npz")
    stamps = np.array([dataset_start := 1_612_137_600_000], dtype=np.int64)
    monkeypatch.setattr(X, "_load_source",
                        lambda *_: (stamps, np.array([30_000.0])))
    assert dataset_start
    with pytest.raises(X.ExternalDataError, match="forward fill"):
        X.build(rebuild=True)


@pytest.mark.skipif(not X.available(), reason="D5.2 archive not present")
def test_the_external_grid_is_fresh_enough():
    grid = X.build()
    coverage = X.coverage(grid)
    for label, row in coverage.items():
        assert row["max_age_minutes"] <= X.MAX_STALENESS_MINUTES, label


# --- metrics and verdicts -------------------------------------------------------------------

def _result(auc_target: float, n: int = 5_000) -> dict[str, np.ndarray]:
    rng = _rng()
    y = (rng.uniform(size=n) < 0.5).astype(float)
    noise = rng.normal(size=n)
    strength = (auc_target - 0.5) * 6
    score = strength * y + noise
    p = 1 / (1 + np.exp(-score))
    return {"y": y, "M0": np.full(n, float(y.mean())), "M1": p, "M2": p,
            "fold": rng.integers(1, 9, n), "year": rng.choice(list(C.VALIDATION_YEARS), n),
            "vol": rng.choice(["LOW", "MID", "HIGH"], n)}


def test_evaluate_reports_direction_auc_not_per_side_auc():
    summary = R.evaluate(_result(0.70), "M2")
    assert "direction_auc" in summary
    assert summary["direction_auc"] > 0.60
    assert summary["up_first"] + summary["down_first"] == summary["n"]


def test_a_coin_flip_scores_about_one_half():
    summary = R.evaluate(_result(0.50), "M2")
    assert abs(summary["direction_auc"] - 0.5) < 0.05


def test_too_few_rows_is_inconclusive():
    summary = R.evaluate(_result(0.70, n=500), "M2")
    assert R.verdict(summary, None)["verdict"] == C.INCONCLUSIVE


def test_sample_check_precedes_the_metric_checks():
    summary = R.evaluate(_result(0.50, n=100), "M2")
    assert R.verdict(summary, None)["verdict"] == C.INCONCLUSIVE


def test_no_direction_when_auc_is_at_chance():
    summary = R.evaluate(_result(0.50), "M2")
    assert R.verdict(summary, None)["verdict"] == C.NO_DIRECTION


def test_strong_requires_the_permutation_control():
    summary = R.evaluate(_result(0.75), "M2")
    assert R.verdict(summary, None)["verdict"] == C.WEAK
    beaten = {"percentile_value": 0.55}
    assert R.verdict(summary, beaten)["verdict"] in (C.STRONG, C.WEAK)


def test_failing_the_permutation_control_blocks_strong():
    summary = R.evaluate(_result(0.75), "M2")
    out = R.verdict(summary, {"percentile_value": 0.99})
    assert out["verdict"] == C.WEAK
    assert not out["checks"]["D6_permutation"]["pass"]


def test_deciles_cover_every_row():
    result = _result(0.60)
    deciles = R._deciles(result["y"], result["M2"])
    assert sum(d["n"] for d in deciles) == len(result["y"])
    assert len(deciles) == C.DECILES


def test_the_decile_gap_points_the_right_way():
    result = _result(0.75)
    summary = R.evaluate(result, "M2")
    assert summary["decile_gap"] > 0


def test_best_verdict_wins():
    assert R._best({"M1": {"verdict": C.NO_DIRECTION},
                    "M2": {"verdict": C.WEAK}}) == C.WEAK
    assert R._best({"M1": {"verdict": C.STRONG},
                    "M2": {"verdict": C.NO_DIRECTION}}) == C.STRONG


# --- contract -----------------------------------------------------------------------------

def test_the_contract_states_every_frozen_value():
    C.verify_bindings()


def test_the_contract_hash_matches_the_freeze_record():
    assert C.require_frozen() == C.sha256()


def test_an_edited_contract_is_refused():
    body = C.CONTRACT.read_text(encoding="utf-8").replace("direction AUC > **0.55**",
                                                          "direction AUC > **0.40**")
    with pytest.raises(C.ContractMismatch):
        C.verify_bindings(body)


def test_the_freeze_record_forbids_metrics_before_it():
    payload = json.loads(Path(C.FREEZE).read_text())
    assert payload["frozen_before_any_direction_metric"] is True
    for banned in ("direction AUC", "Brier", "log loss"):
        assert banned in payload["forbidden_before_freeze"]
    assert payload["trading"]["orders"] == 0


def test_the_combos_are_the_frozen_four():
    assert C.COMBOS == ((240, 100), (720, 100), (720, 200), (1440, 200))
    assert len({h for h, _ in C.COMBOS}) == 3


# --- safety -------------------------------------------------------------------------------

P2_PACKAGE = Path(R.__file__).parent


def _code_only(path: Path) -> str:
    import io
    import tokenize

    kept = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(io.BytesIO(handle.read()).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    return " ".join(kept)


def test_no_module_touches_trading_or_the_parallel_studies():
    banned = ("crypto.paper", "crypto.terminal", "submit_order", "liquidation_forward",
              "manual_r1", "manual_r2", "btc_p1_forward")
    for path in sorted(P2_PACKAGE.glob("*.py")):
        code = _code_only(path)
        for needle in banned:
            assert needle not in code, f"{path.name} references {needle}"


def test_nothing_computes_pnl():
    for path in sorted(P2_PACKAGE.glob("*.py")):
        code = _code_only(path)
        for banned in ("pnl", "PnL", "leverage", "position_size"):
            assert banned not in code, f"{path.name} references {banned}"
