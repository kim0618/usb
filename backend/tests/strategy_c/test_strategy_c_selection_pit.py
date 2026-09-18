"""C-P2 mutation tests and C-P3/C-P4 contracts on synthetic panels. No network, no workspace."""

import ast
from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.backtest.strategy_c_selection import evaluate
from app.backtest.strategy_c_selection.features import FEATURE_COLUMNS, compute
from app.backtest.strategy_c_selection.labels import compute_labels
from app.backtest.strategy_c_selection.panel import Panel, SplitEvent, truncate, with_changes
from app.backtest.strategy_c_selection.pit_audit import audit
from app.backtest.strategy_c_selection.rules import RULES_PATH, RulesChanged, load_rules
from app.backtest.strategy_c_selection.run import table_digest

T = 300
D = 270
PACKAGE = Path(__file__).resolve().parents[2] / "app/backtest/strategy_c_selection"


@pytest.fixture(scope="module")
def rules():
    return load_rules()


def _sessions(n: int) -> tuple[date, ...]:
    out, cursor = [], date(2024, 9, 16)
    while len(out) < n:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return tuple(out)


def make_panel(extra_tickers: int = 30, seed: int = 7) -> Panel:
    """SPY, MOM (momentum at D), DEL (momentum at D, then disappears after D+2), and quiet names."""
    rng = np.random.default_rng(seed)
    tickers = ("DEL", "MOM", "SPY") + tuple(f"Q{i:02d}" for i in range(extra_tickers))
    n = len(tickers)
    returns = rng.normal(0.0, 0.01, size=(T, n))
    close = 20.0 * np.exp(np.cumsum(returns, axis=0))
    volume = rng.uniform(0.9e6, 1.1e6, size=(T, n))
    for name in ("MOM", "DEL"):
        j = tickers.index(name)
        close[D - 4:, j] = close[D - 5, j] * np.cumprod(np.r_[np.full(4, 1.02), 1.05,
                                                          np.full(T - D - 1, 1.01)])
        volume[D, j] = 4e6
    spy = tickers.index("SPY")
    close[:, spy] = 500.0
    open_ = close * (1 + rng.normal(0, 0.002, size=(T, n)))
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    dele = tickers.index("DEL")
    for arr in (open_, high, low, close, volume):
        arr[D + 3:, dele] = np.nan
    sessions = _sessions(T)
    members = frozenset(t for t in tickers if t != "SPY")
    return Panel(sessions, tickers, open_, high, low, close, volume, (), {sessions[0]: members})


def row(features, i: int, j: int) -> dict:
    return {name: features.values[name][i, j] for name in FEATURE_COLUMNS}


def assert_rows_equal(a, b, i: int, rules) -> None:
    for name in FEATURE_COLUMNS:
        np.testing.assert_array_equal(a.values[name][i], b.values[name][i], err_msg=name)
    for variant in rules.variants:
        np.testing.assert_array_equal(a.eligible(variant)[i], b.eligible(variant)[i])
        np.testing.assert_array_equal(a.candidates(variant)[i], b.candidates(variant)[i])


def test_rules_checksum_is_the_declared_one(tmp_path, rules):
    assert rules.checksum == load_rules().checksum
    changed = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    changed["variants"]["C-M0"]["all_of"][0][2] = 0.02
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(RulesChanged):
        load_rules(path)


def test_synthetic_momentum_is_a_candidate_and_quiet_names_are_not(rules):
    panel = make_panel()
    features = compute(panel, rules)
    m0 = next(v for v in rules.variants if v.name == "C-M0")
    cand = features.candidates(m0)[D]
    assert cand[panel.column("MOM")] and cand[panel.column("DEL")]
    assert features.values["rvol_20"][D, panel.column("MOM")] > 3.5


def test_1_future_bar_insertion_does_not_change_signal(rules):
    panel = make_panel()
    full = compute(panel, rules)
    mutated = {name: getattr(panel, name).copy() for name in ("open", "high", "low", "close", "volume")}
    for name in mutated:
        mutated[name][D + 1:] *= 7.0
    mutated["high"][D + 1:] *= 50.0
    other = compute(with_changes(panel, **mutated), rules)
    assert_rows_equal(full, other, D, rules)


def test_2_future_split_does_not_change_past_features_but_adjusts_labels(rules):
    panel = make_panel()
    j = panel.column("MOM")
    base_features = compute(panel, rules)
    base_labels = compute_labels(panel, rules.horizons, rules.ca_ratio)
    split_session = panel.sessions[D + 4]
    raw = {name: getattr(panel, name).copy() for name in ("open", "high", "low", "close", "volume")}
    for name in ("open", "high", "low", "close"):
        raw[name][D + 4:, j] /= 2.0  # 2-for-1 forward split: raw prices halve from the execution date
    raw["volume"][D + 4:, j] *= 2.0
    split = with_changes(panel, splits=(SplitEvent("MOM", split_session, 1.0, 2.0),), **raw)
    split_features = compute(split, rules)
    assert_rows_equal(base_features, split_features, D, rules)
    split_labels = compute_labels(split, rules.horizons, rules.ca_ratio)
    for k in rules.horizons:
        np.testing.assert_allclose(split_labels.mfe[k][D, j], base_labels.mfe[k][D, j], rtol=1e-12)
        np.testing.assert_allclose(split_labels.close_return[k][D, j], base_labels.close_return[k][D, j],
                                   rtol=1e-12)
    # Without the split record the raw halving would be a -50% label, and still not touch features.
    unrecorded_labels = compute_labels(with_changes(panel, **raw), rules.horizons, rules.ca_ratio)
    assert unrecorded_labels.close_return[10][D, j] < base_labels.close_return[10][D, j] - 0.3


def test_3_ticker_that_later_disappears_is_kept_and_removing_it_changes_nothing_else(rules):
    panel = make_panel()
    full = compute(panel, rules)
    labels = compute_labels(panel, rules.horizons, rules.ca_ratio)
    dele = panel.column("DEL")
    m0 = next(v for v in rules.variants if v.name == "C-M0")
    assert full.candidates(m0)[D, dele]
    assert labels.disappeared[10][D, dele] and not labels.disappeared[1][D, dele]
    removed = {name: getattr(panel, name).copy() for name in ("open", "high", "low", "close", "volume")}
    for name in removed:
        removed[name][:, dele] = np.nan
    other = compute(with_changes(panel, **removed), rules)
    keep = np.ones(len(panel.tickers), dtype=bool)
    keep[dele] = False
    for name in FEATURE_COLUMNS:
        np.testing.assert_array_equal(full.values[name][D][keep], other.values[name][D][keep])
    np.testing.assert_array_equal(full.candidates(m0)[D][keep], other.candidates(m0)[D][keep])


def test_4_52w_high_ignores_the_future_and_sees_the_past(rules):
    panel = make_panel()
    j = panel.column("Q00")
    full = compute(panel, rules)
    future = panel.high.copy()
    future[D + 1:, j] *= 100.0
    assert full.values["distance_52w_high"][D, j] == compute(with_changes(panel, high=future), rules) \
        .values["distance_52w_high"][D, j]
    past = panel.high.copy()
    past[D - 200, j] *= 100.0
    assert compute(with_changes(panel, high=past), rules).values["distance_52w_high"][D, j] \
        < full.values["distance_52w_high"][D, j]
    outside = panel.high.copy()
    outside[D - 252, j] *= 100.0
    assert compute(with_changes(panel, high=outside), rules).values["distance_52w_high"][D, j] \
        == full.values["distance_52w_high"][D, j]


@pytest.mark.parametrize("i", [D - 30, D, D + 10, T - 1])
def test_5_truncated_rerun_equals_full_run(rules, i):
    panel = make_panel()
    panel = with_changes(panel, splits=(SplitEvent("Q01", panel.sessions[D - 3], 10.0, 1.0),
                                        SplitEvent("Q02", panel.sessions[D + 2], 1.0, 3.0)))
    full = compute(panel, rules)
    assert_rows_equal(full, compute(truncate(panel, panel.sessions[i]), rules), i, rules)


def test_6_same_input_twice_gives_identical_digest(rules):
    panel = make_panel()
    digests = []
    for _ in range(2):
        features = compute(with_changes(panel), rules)
        frame = pd.DataFrame({name: features.values[name][D] for name in FEATURE_COLUMNS})
        digests.append(table_digest(frame))
    assert digests[0] == digests[1]


def test_real_data_audit_function_reports_zero_on_clean_panel(rules):
    panel = make_panel()
    full = compute(panel, rules)
    report = audit(panel, full, rules, [D - 5, D])
    assert report["violations"] == 0
    assert report["positive_control_detected"]


def test_reverse_split_is_not_momentum(rules):
    panel = make_panel()
    j = panel.column("Q03")
    raw = {name: getattr(panel, name).copy() for name in ("open", "high", "low", "close", "volume")}
    for name in ("open", "high", "low", "close"):
        raw[name][D:, j] *= 10.0  # 1-for-10 reverse split executed on D
    raw["volume"][D:, j] /= 10.0
    m0 = next(v for v in rules.variants if v.name == "C-M0")
    recorded = compute(with_changes(panel, splits=(SplitEvent("Q03", panel.sessions[D], 10.0, 1.0),), **raw),
                       rules)
    assert abs(recorded.values["return_1d"][D, j] - compute(panel, rules).values["return_1d"][D, j]) < 1e-12
    assert recorded.values["split_flag"][D, j] == 1.0 and recorded.values["reverse_split_flag"][D, j] == 1.0
    assert not recorded.eligible(m0)[D, j] and not recorded.candidates(m0)[D, j]
    unrecorded = compute(with_changes(panel, **raw), rules)
    assert unrecorded.values["return_1d"][D, j] > 8.0
    assert unrecorded.values["ca_suspect"][D, j] == 1.0 and not unrecorded.candidates(m0)[D, j]


def test_labels_start_after_d_and_use_d_plus_1_open(rules):
    panel = make_panel()
    j = panel.column("MOM")
    base = compute_labels(panel, rules.horizons, rules.ca_ratio)
    high = panel.high.copy()
    high[D, j] *= 50.0
    moved = compute_labels(with_changes(panel, high=high), rules.horizons, rules.ca_ratio)
    for k in rules.horizons:
        assert moved.mfe[k][D, j] == base.mfe[k][D, j]
    expected = panel.high[D + 1: D + 4, j].max() / panel.open[D + 1, j] - 1
    assert base.mfe[3][D, j] == pytest.approx(expected)
    assert base.reference[D, j] == panel.open[D + 1, j]


def test_matched_base_and_lift_on_a_hand_built_frame(rules):
    rows = []
    for cell, (cand_hits, ctrl_hits) in {1: ([1, 1], [1, 0, 0, 0, 0]), 2: ([0], [0, 0, 0, 0, 1, 1])}.items():
        for flag, hits in ((True, cand_hits), (False, ctrl_hits)):
            for hit in hits:
                rows.append({"date_idx": 0, "ticker": f"T{len(rows)}", "candidate": flag, "cell": cell,
                             "valid_10": True, "mfe_10": 0.2 if hit else 0.0, "mae_10": -0.05,
                             "close_10": 0.01, "mfe10_ge_10": float(hit), "mfe10_ge_15": float(hit),
                             "mfe10_ge_20": float(hit)})
    frame = pd.DataFrame(rows)
    matched = evaluate.attach_matched_base(frame, rules, 10)
    assert matched["matched"].all()
    lift = matched["mfe10_ge_15"].sum() / matched["base_mfe10_ge_15"].sum()
    assert lift == pytest.approx(2 / (0.2 * 2 + 2 / 6))


def test_block_bootstrap_is_seeded_and_contiguous():
    a, b = evaluate.block_indices(35, replicates=5), evaluate.block_indices(35, replicates=5)
    np.testing.assert_array_equal(a, b)
    assert a.shape == (5, 35) and a.max() <= 34
    assert np.all(np.diff(a[:, :10], axis=1) == 1)


def test_selection_package_has_no_trading_imports():
    forbidden = ("app.broker", "app.risk", "app.strategy", "app.services", "app.execution",
                 "app.backtest.portfolio", "app.backtest.engine.adapter", "app.integrations.kiwoom")
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                assert not name.startswith(forbidden) or name.startswith("app.strategy_b"), (path.name, name)
    features_src = (PACKAGE / "features.py").read_text(encoding="utf-8")
    assert "labels" not in {n.module.rsplit(".", 1)[-1] for n in ast.walk(ast.parse(features_src))
                            if isinstance(n, ast.ImportFrom) and n.module}


def test_audit_refuses_a_negative_date_index(rules):
    panel = make_panel()
    with pytest.raises(ValueError):
        audit(panel, compute(panel, rules), rules, [-3])
