"""Signal construction: the median, the label join, and the wall between them and the future.

The mutation tests come in pairs. One half plants a change the signal must not see, the other
half plants a change it must see - a pipeline that had stopped computing anything would pass the
first half of every pair and fail the second.
"""

import ast
import inspect
import json
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_c_selection.panel import with_changes
from app.backtest.strategy_d_analog.label_extension import compute_validity
from app.backtest.strategy_d_analog.labels import compute_labels
from app.backtest.strategy_d_v2 import analog_signal, b0_composite, d2, d3, evaluation_labels
from app.backtest.strategy_d_v2 import structure_features
from app.backtest.strategy_d_v2.config import load_rules
from app.backtest.strategy_d_v2.models import HardFail, PointInTimeViolation
from tests.strategy_d_v2 import fixtures

RULES = load_rules()
HORIZON = RULES.primary_horizon
TOP_K = RULES.top_k
PACKAGE_DIR = Path(d3.__file__).resolve().parent


# -- the median ---------------------------------------------------------------------------------

def test_the_signal_is_the_plain_median_of_its_group():
    rng = np.random.default_rng(4)
    values = rng.normal(0.0, 0.02, 7 * TOP_K)
    signal = analog_signal.median_by_group(values, TOP_K)
    assert len(signal) == 7
    for group in range(7):
        block = values[group * TOP_K:(group + 1) * TOP_K]
        assert signal.values[group] == pytest.approx(float(np.median(block)), rel=0, abs=0)
        # an even group averages the two middle values: the ordinary median, not a mid-rank pick
        middle = np.sort(block)[TOP_K // 2 - 1:TOP_K // 2 + 1]
        assert signal.values[group] == pytest.approx(float(middle.mean()), abs=1e-18)


def test_the_signal_is_not_a_mean_or_a_weighted_statistic():
    values = np.concatenate([np.zeros(TOP_K - 1), np.array([10.0])])
    signal = analog_signal.median_by_group(values, TOP_K)
    assert signal.values[0] == 0.0                      # the outlier moves a mean, not a median
    assert signal.values[0] != pytest.approx(values.mean())


def test_the_signal_function_cannot_be_given_distances():
    parameters = list(inspect.signature(analog_signal.median_by_group).parameters)
    assert parameters == ["values", "group_size"]


def test_a_ragged_or_non_finite_group_is_a_hard_fail():
    with pytest.raises(HardFail):
        analog_signal.median_by_group(np.zeros(TOP_K + 1), TOP_K)
    broken = np.zeros(TOP_K)
    broken[3] = np.nan
    with pytest.raises(HardFail):
        analog_signal.median_by_group(broken, TOP_K)


def test_the_stored_signal_must_match_its_own_inputs():
    values = np.arange(2 * TOP_K, dtype=np.float64)
    signal = analog_signal.median_by_group(values, TOP_K)
    analog_signal.assert_is_the_declared_median(signal, values)
    tampered = analog_signal.AnalogSignal(signal.values + 1.0, TOP_K, 2)
    with pytest.raises(HardFail):
        analog_signal.assert_is_the_declared_median(tampered, values)


# -- neighbour layout, validity and embargo ---------------------------------------------------------

def _layout(queries=3, top_k=TOP_K):
    return {"query_date_idx": np.repeat(np.array([300, 305, 310]), top_k)[: queries * top_k],
            "sample_rank": np.repeat(np.arange(queries, dtype=np.int16), top_k),
            "rank": np.tile(np.arange(1, top_k + 1, dtype=np.int16), queries)}


def test_neighbor_layout_must_be_contiguous_blocks_in_rank_order():
    good = _layout()
    d3.assert_neighbor_layout(top_k=TOP_K, queries=3, **good)
    shuffled = dict(good, rank=good["rank"].copy())
    shuffled["rank"][0], shuffled["rank"][1] = shuffled["rank"][1], shuffled["rank"][0]
    with pytest.raises(HardFail):
        d3.assert_neighbor_layout(top_k=TOP_K, queries=3, **shuffled)
    out_of_order = dict(good, query_date_idx=good["query_date_idx"][::-1].copy())
    with pytest.raises(HardFail):
        d3.assert_neighbor_layout(top_k=TOP_K, queries=3, **out_of_order)
    with pytest.raises(HardFail):
        d3.assert_neighbor_layout(top_k=TOP_K, queries=4, **good)


def test_label_embargo_is_rechecked_on_the_rows_that_become_numbers():
    query = np.full(TOP_K, 300, dtype=np.int64)
    limit = 300 - RULES.max_lookback - HORIZON
    ok = np.full(TOP_K, limit, dtype=np.int64)
    assert d3.assert_label_embargo(query, ok, lookback=RULES.max_lookback, horizon=HORIZON) == 0
    late = ok.copy()
    late[7] = limit + 1
    with pytest.raises(PointInTimeViolation):
        d3.assert_label_embargo(query, late, lookback=RULES.max_lookback, horizon=HORIZON)


def test_distance_summary_is_metadata_only():
    distance = np.tile(np.arange(TOP_K, dtype=np.float64), 2)
    summary = d3.distance_summary(distance, 2, TOP_K)
    assert set(summary) == {"mean_distance", "median_distance", "min_distance", "max_distance"}
    assert summary["min_distance"].tolist() == [0.0, 0.0]
    assert summary["max_distance"].tolist() == [float(TOP_K - 1)] * 2


# -- one label definition, two index sets -------------------------------------------------------------

@pytest.fixture(scope="module")
def market():
    panel = fixtures.make_panel(days=200, tickers=fixtures.WIDE_TICKERS, seed=9)
    eligible = fixtures.eligible_all(panel)
    validity = compute_validity(panel, (HORIZON,), RULES.ca_suspect_ratio)
    excess = evaluation_labels.forward_excess(panel, validity, eligible, HORIZON)
    return panel, eligible, validity, excess


def test_historical_and_query_labels_come_from_one_matrix(market):
    """The two paths cannot drift because there is only one formula and one array."""
    panel, eligible, validity, excess = market
    reference = compute_labels(panel, validity, eligible).excess_for(HORIZON)
    np.testing.assert_array_equal(np.nan_to_num(excess.excess, nan=-999.0),
                                  np.nan_to_num(reference, nan=-999.0))
    historical = excess.gather(np.array([80, 90]), np.array([3, 4]))
    query = evaluation_labels.query_labels(excess, np.array([80, 90]), np.array([3, 4]))
    np.testing.assert_array_equal(historical, np.where(query.valid, query.values, historical))


def test_query_labels_keep_invalid_rows_as_flagged_nan(market):
    _, _, _, excess = market
    session = np.array([195, 196, 197], dtype=np.int64)   # too close to the end for a 5D label
    ticker = np.array([0, 1, 2], dtype=np.int64)
    query = evaluation_labels.query_labels(excess, session, ticker)
    assert not query.valid.any()
    assert np.isnan(query.values).all()
    assert query.counts() == {"rows": 3, "valid": 0, "invalid": 3}


# -- mutation pairs -------------------------------------------------------------------------------------

def _excess_row(panel, eligible, row):
    validity = compute_validity(panel, (HORIZON,), RULES.ca_suspect_ratio)
    excess = evaluation_labels.forward_excess(panel, validity, eligible, HORIZON)
    return excess.excess[row].copy(), validity.valid[HORIZON][row].copy()


def test_a_historical_label_is_blind_to_bars_after_its_ca_window(market):
    """Beyond ``d + 10`` - the declared CA window for h <= 10 - nothing can reach the label."""
    panel, eligible, _, _ = market
    row = 100
    before, before_valid = _excess_row(panel, eligible, row)
    close = panel.close.copy()
    high, low = panel.high.copy(), panel.low.copy()
    close[row + 11:] *= 40.0
    high[row + 11:] *= 40.0
    low[row + 11:] *= 0.03
    after, after_valid = _excess_row(with_changes(panel, close=close, high=high, low=low),
                                     eligible, row)
    np.testing.assert_array_equal(np.nan_to_num(before, nan=-999.0),
                                  np.nan_to_num(after, nan=-999.0))
    np.testing.assert_array_equal(before_valid, after_valid)


def test_a_change_inside_the_label_window_does_move_it(market):
    """Positive control: ``D+1..D+5`` is exactly what the label reads."""
    panel, eligible, _, _ = market
    row = 100
    before, _ = _excess_row(panel, eligible, row)
    close = panel.close.copy()
    close[row + 1:row + 6] *= 1.25
    after, _ = _excess_row(with_changes(panel, close=close), eligible, row)
    assert not np.array_equal(np.nan_to_num(before, nan=-999.0),
                              np.nan_to_num(after, nan=-999.0))


def test_the_benchmark_moves_when_the_same_date_universe_moves(market):
    """The excess leg is a cross-sectional median, so a peer's label window is part of it."""
    panel, eligible, _, _ = market
    row = 100
    before, _ = _excess_row(panel, eligible, row)
    close = panel.close.copy()
    close[row + 1:row + 6, 10:] *= 1.4        # every ticker but the first ten
    after, _ = _excess_row(with_changes(panel, close=close), eligible, row)
    assert before[0] != after[0]


def test_future_query_mutation_leaves_the_signal_alone_and_moves_the_label(market):
    """The pair the contract calls most important, on a synthetic market.

    Neighbour labels end at ``d + 5 <= D - 60`` and B0 reads ``D-60..D``, so replacing everything
    after D must leave A(q) and B0 exactly where they were - while the query's own label, which
    is computed from ``D+1..D+5``, must move.
    """
    panel, eligible, _, excess = market
    date_idx = 150
    neighbour_rows = np.arange(60, 60 + TOP_K, dtype=np.int64)          # ends far before D - 65
    neighbour_cols = np.arange(TOP_K, dtype=np.int64)
    query_rows = np.full(4, date_idx, dtype=np.int64)
    query_cols = np.arange(4, dtype=np.int64)

    features = structure_features.build(panel, eligible, RULES, diagnostic_frame=False)
    before_signal = analog_signal.median_by_group(
        excess.gather(np.repeat(neighbour_rows[:TOP_K], 1), neighbour_cols), TOP_K)
    before_b0 = b0_composite.build(features.matrix(query_rows, query_cols), RULES)
    before_label = evaluation_labels.query_labels(excess, query_rows, query_cols)

    mutated = d2.mutate_future(panel, date_idx)
    validity = compute_validity(mutated, (HORIZON,), RULES.ca_suspect_ratio)
    after_excess = evaluation_labels.forward_excess(mutated, validity, eligible, HORIZON)
    after_features = structure_features.build(mutated, eligible, RULES, diagnostic_frame=False)
    after_signal = analog_signal.median_by_group(
        after_excess.gather(neighbour_rows, neighbour_cols), TOP_K)
    after_b0 = b0_composite.build(after_features.matrix(query_rows, query_cols), RULES)
    after_label = evaluation_labels.query_labels(after_excess, query_rows, query_cols)

    np.testing.assert_array_equal(before_signal.values, after_signal.values)
    np.testing.assert_array_equal(before_b0.values, after_b0.values)
    moved = ~((np.isnan(before_label.values) & np.isnan(after_label.values))
              | (before_label.values == after_label.values))
    assert moved.any(), "the query's own label must move when its own future is replaced"


def test_signal_construction_is_deterministic(market):
    _, _, _, excess = market
    rows = np.arange(60, 60 + 2 * TOP_K, dtype=np.int64) % 120 + 60
    cols = np.arange(2 * TOP_K, dtype=np.int64) % len(fixtures.WIDE_TICKERS)
    first = analog_signal.median_by_group(excess.gather(rows, cols), TOP_K)
    second = analog_signal.median_by_group(excess.gather(rows, cols), TOP_K)
    np.testing.assert_array_equal(first.values, second.values)


# -- firewall and parent binding ---------------------------------------------------------------------------

def test_the_signal_module_cannot_reach_the_evaluation_path():
    tree = ast.parse(Path(analog_signal.__file__).read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    for banned in ("app.backtest.strategy_d_v2.evaluation_labels",
                   "app.backtest.strategy_d_analog.labels",
                   "app.backtest.strategy_d_v2.d3"):
        assert banned not in imported
    assert not any("label" in (name or "") for name in imported)


def test_only_the_evaluation_module_turns_bars_into_numbers():
    """``labels`` is the one V1 module that computes a return, and only one V2-A file loads it."""
    loaders = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.backtest.strategy_d_analog.labels":
                loaders.append(path.name)
    assert loaders == ["evaluation_labels.py"]


def test_parent_d2_binding_rejects_a_wrong_or_unfinished_parent(tmp_path):
    from app.backtest.strategy_d_v2 import schema as schema_module

    feature_schema = schema_module.build(RULES)
    d1 = tmp_path / "dv2a1-x"
    d1.mkdir()
    d1_identity = {"identity_digest": "a" * 64, "phase": "D1", "rules_checksum": RULES.checksum,
                   "strategy_id": RULES.raw["strategy_id"],
                   "data": {"freeze_digest": "f", "grid_digest": "g", "d_read_digest": "r"}}
    (d1 / "run_identity.json").write_text(json.dumps(d1_identity))
    (d1 / "d1_report.json").write_text(json.dumps(
        {"verdict": "PASS", "determinism": {"digests": {"query_sample": "qs"}}}))
    (d1 / "COMPLETE.json").write_text(json.dumps({"verdict": "PASS", "identity_digest": "a" * 64}))
    parent_d1 = d2.load_parent(tmp_path, "dv2a1-x", RULES)

    d2_dir = tmp_path / "dv2a2-x"
    d2_dir.mkdir()
    identity = {"identity_digest": "b" * 64, "phase": "D2", "rules_checksum": RULES.checksum,
                "parent": "a" * 64, "feature_schema_digest": feature_schema.digest,
                "data": {"freeze_digest": "f", "grid_digest": "g", "d_read_digest": "r"}}
    summary = {"digests": {"neighbors": "n" * 64}, "query_sample": {"digest": "qs"}}
    (d2_dir / "identity.json").write_text(json.dumps(identity))
    (d2_dir / "summary.json").write_text(json.dumps(summary))
    for name in ("parent_d1.json", "library_manifest.json", "query_manifest.json",
                 "status_counts.json", "feature_schema.json", "neighbors.parquet"):
        (d2_dir / name).write_bytes(b"{}")

    with pytest.raises(HardFail):                       # no COMPLETE token
        d3.load_parent_d2(tmp_path, "dv2a2-x", RULES, feature_schema, parent_d1)
    (d2_dir / "COMPLETE.json").write_text(json.dumps(
        {"verdict": "PASS", "identity_digest": "b" * 64, "neighbors_digest": "n" * 64}))
    bound = d3.load_parent_d2(tmp_path, "dv2a2-x", RULES, feature_schema, parent_d1)
    assert bound.neighbors_digest == "n" * 64

    identity["parent"] = "z" * 64                       # built on a different D1
    (d2_dir / "identity.json").write_text(json.dumps(identity))
    with pytest.raises(HardFail):
        d3.load_parent_d2(tmp_path, "dv2a2-x", RULES, feature_schema, parent_d1)

    identity["parent"] = "a" * 64
    identity["feature_schema_digest"] = "0" * 64        # a different vector meaning
    (d2_dir / "identity.json").write_text(json.dumps(identity))
    with pytest.raises(HardFail):
        d3.load_parent_d2(tmp_path, "dv2a2-x", RULES, feature_schema, parent_d1)


def test_required_parent_digests_are_phase_scoped():
    class Parent:
        digests = {name: "x" for name in d3.REQUIRED_D1_DIGESTS}
    good = {name: "x" for name in d3.REQUIRED_D1_DIGESTS}
    assert all(d2.verify_parent_matrices(Parent, good, required=d3.REQUIRED_D1_DIGESTS).values())
    with pytest.raises(HardFail):                       # D2 also demands the library digest
        d2.verify_parent_matrices(Parent, good)


def test_the_signal_artifact_carries_no_realized_outcome():
    columns = {name: np.zeros(2, dtype=np.int32) for name in
               ("query_date_idx", "sample_rank", "query_ticker_col", "neighbor_total",
                "neighbor_valid", "signal_status")}
    for name in ("analog_signal_A", "b0", "b0_strong", "mean_distance", "median_distance",
                 "min_distance", "max_distance"):
        columns[name] = np.zeros(2)
    table = d3.signal_table(columns, ("AAA", "BBB"), fixtures.sessions(2))
    for banned in ("excess_return", "realized", "label_valid", "forward"):
        assert not any(banned in name for name in table.column_names)
    assert "analog_signal_A" in table.column_names and "b0" in table.column_names
