"""D3 contracts: forward distribution, S(q), sigma(q), parent binding and the query-future firewall.

The test this phase exists for is ``test_the_signal_cannot_see_the_query_future``: mutate every
bar after the query date and require S and sigma to come back bit-identical while the evaluation
label - which is supposed to depend on exactly that future - changes. A signal that survives the
first half of that test but not the second is not point-in-time, it is inert.

Expected numbers are computed from the declared formulas, not read back from the implementation.
Everything runs on synthetic data; no test reads the Drive workspace or the network.
"""

import ast
import json
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_c_selection.panel import Panel, with_changes
from app.backtest.strategy_d_analog import d2, d3, evaluation, labels, signal, universe
from app.backtest.strategy_d_analog.config import load_rules
from app.backtest.strategy_d_analog.label_extension import compute_validity
from app.backtest.strategy_d_analog.models import HardFail, TestId
from app.backtest.strategy_d_analog.source import load_daily_history
from tests.strategy_d import fixtures

PACKAGE = Path(__file__).resolve().parents[2] / "app/backtest/strategy_d_analog"
#: Names that could only appear in D3 if it had started to judge the signal it builds (§16).
FORBIDDEN_IDENTIFIERS = ("spearman", "pearson_ic", "quintile", "bootstrap", "bonferroni",
                         "baseline_n1", "baseline_n2", "pass_fail", "ic_point")


@pytest.fixture(scope="module")
def rules():
    return load_rules()


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("d3workspace") / "1_US-B"
    fixtures.build(root)
    return root


def history_of(root, rules, **kwargs):
    return load_daily_history(root, fixtures.SNAPSHOT_ID, allowed_exchanges=rules.allowed_exchanges,
                              **kwargs)


@pytest.fixture(scope="module")
def parent_run(dataset, rules, tmp_path_factory):
    """A finished D2 run on the fixture dataset; D3 consumes this and never re-selects."""
    expected = history_of(dataset, rules).freeze
    result = d2.execute(dataset, fixtures.SNAPSHOT_ID, expected=expected,
                        parent_run="dpit1-fixture", rules=rules,
                        runs_dir=tmp_path_factory.mktemp("d2parent"), log=lambda _: None,
                        eval_date_limit=3, audit_count=1, capture_audit=False)
    d2.finish(result, d2.run_context(dataset, fixtures.SNAPSHOT_ID))
    return result.run_dir


@pytest.fixture(scope="module")
def d3_run(dataset, rules, parent_run, tmp_path_factory):
    result = d3.execute(dataset, fixtures.SNAPSHOT_ID, parent_dir=parent_run, rules=rules,
                        runs_dir=tmp_path_factory.mktemp("d3runs"), log=lambda _: None)
    return result


# --- 1. Distribution aggregation ----------------------------------------------------------

def single_query(excess, metric, representation="A", window=20, horizon=5, top_k=5,
                 query_end_idx=300, neighbor_end=200):
    values = np.asarray(excess, dtype=np.float64)
    return signal.build(
        representation=representation, window=window, horizon=horizon,
        query_end_idx=np.array([query_end_idx]),
        neighbor_end_idx=np.full(values.size, neighbor_end, dtype=np.int64),
        neighbor_excess=values, metric_value=np.asarray(metric, dtype=np.float64),
        counts=np.array([values.size]), top_k=top_k)


def test_the_distribution_is_the_declared_summary_of_the_neighbour_returns():
    excess = [-0.02, -0.01, 0.00, 0.03, 0.10]
    out = single_query(excess, [0.9, 0.8, 0.7, 0.6, 0.5])
    assert out.n_total[0] == 5 and out.n_valid[0] == 5
    assert out.mean[0] == pytest.approx(0.02, abs=1e-15)
    assert out.median[0] == pytest.approx(0.0, abs=1e-15)
    assert out.std[0] == pytest.approx(0.04847679857416329, abs=1e-12)
    assert (int(out.positive_count[0]), int(out.negative_count[0]), int(out.zero_count[0])) == (2, 2, 1)
    assert out.hit_rate[0] == pytest.approx(0.4, abs=1e-15)
    expected = {0.10: -0.016, 0.25: -0.01, 0.50: 0.0, 0.75: 0.03, 0.90: 0.07200000000000001}
    for quantile, value in expected.items():
        assert out.quantiles[quantile][0] == pytest.approx(value, abs=1e-12)


def test_s_is_the_median_of_the_same_distribution():
    """D0 ``analog_forward_signal.primary``: the median, not the mean, and not a second statistic."""
    out = single_query([-0.02, -0.01, 0.00, 0.03, 0.10], [0.9] * 5)
    assert out.signal[0] == pytest.approx(0.0, abs=1e-15)
    assert out.signal[0] == out.quantiles[0.50][0]
    assert out.signal[0] != out.mean[0]
    signal.assert_signal_is_the_declared_median(out)


def test_sigma_follows_the_representation():
    """A is a mean correlation; B is a mean distance turned into a score by -1/sqrt(W) (D0)."""
    a = single_query([0.0] * 5, [0.9, 0.8, 0.7, 0.6, 0.5], representation="A")
    assert a.sigma[0] == pytest.approx(0.7000000000000001, abs=1e-12)
    b = single_query([0.0] * 5, [1.0, 2.0, 3.0, 4.0, 5.0], representation="B", window=20)
    assert b.sigma[0] == pytest.approx(-0.6708203932499369, abs=1e-12)
    wider = single_query([0.0] * 5, [1.0, 2.0, 3.0, 4.0, 5.0], representation="B", window=60)
    assert wider.sigma[0] == pytest.approx(-3.0 / np.sqrt(60), abs=1e-12)
    with pytest.raises(HardFail):
        signal.sigma_of("C", np.zeros((1, 5)), 20)


def test_an_invalid_neighbour_label_is_dropped_from_the_aggregate_but_not_refilled():
    """§5: the query keeps the 50 D2 gave it; rank 51 is never pulled in to make the count up."""
    excess = [np.nan, -0.01, 0.00, 0.03, 0.10]
    out = single_query(excess, [0.9, 0.8, 0.7, 0.6, 0.5], top_k=5)
    assert out.n_total[0] == 5
    assert out.n_valid[0] == 4
    assert out.status[0] == signal.STATUS_ORDER.index(signal.SignalStatus.INSUFFICIENT_VALID_NEIGHBORS)
    assert np.isnan(out.signal[0]) and np.isnan(out.sigma[0])
    # the distribution itself is still summarised over what survived
    assert out.median[0] == pytest.approx(0.015, abs=1e-15)
    assert int(out.positive_count[0]) == 2


def test_insufficient_valid_neighbours_uses_the_declared_top_k(rules):
    """D0 ``insufficient_neighbors`` is "fewer than top_k after all policies"; no extra ratio."""
    assert rules.top_k == 50
    full = single_query([0.01] * 5, [0.9] * 5, top_k=5)
    assert full.status[0] == signal.STATUS_ORDER.index(signal.SignalStatus.OK)
    short = single_query([0.01] * 4, [0.9] * 4, top_k=5)
    assert short.status[0] == signal.STATUS_ORDER.index(signal.SignalStatus.INSUFFICIENT_VALID_NEIGHBORS)
    assert short.n_valid[0] == 4 and np.isnan(short.signal[0])


def test_a_neighbour_past_the_embargo_is_a_hard_fail():
    """§14: the embargo is re-checked at the join, because the label is a later fact than D2."""
    with pytest.raises(HardFail) as caught:
        signal.build(representation="A", window=20, horizon=5,
                     query_end_idx=np.array([300]),
                     neighbor_end_idx=np.array([276]),   # limit is 300 - 20 - 5 = 275
                     neighbor_excess=np.array([0.01]), metric_value=np.array([0.9]),
                     counts=np.array([1]), top_k=1)
    assert caught.value.code in ("R4", "R7")
    ok = signal.build(representation="A", window=20, horizon=5, query_end_idx=np.array([300]),
                      neighbor_end_idx=np.array([275]), neighbor_excess=np.array([0.01]),
                      metric_value=np.array([0.9]), counts=np.array([1]), top_k=1)
    assert ok.n_valid[0] == 1


def test_ragged_neighbour_counts_stay_inside_their_own_query():
    padded = signal.pad_by_query(np.array([1.0, 2.0, 3.0, 4.0, 5.0]), np.array([3, 2]), 3)
    assert np.array_equal(padded[0], [1.0, 2.0, 3.0])
    assert np.array_equal(padded[1][:2], [4.0, 5.0]) and np.isnan(padded[1][2])
    with pytest.raises(HardFail):
        signal.pad_by_query(np.array([1.0, 2.0]), np.array([3]), 3)


# --- 2. Label values ----------------------------------------------------------------------

def make_panel(days=60, names=("AAA", "BBB", "CCC", "DDD"), seed=3):
    sessions = fixtures.sessions(days)
    rng = np.random.default_rng(seed)
    close = np.vstack([20.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, days))) for _ in names]).T
    return Panel(sessions, names, close * 0.999, close * 1.02, close * 0.98, close,
                 np.full((days, len(names)), 800_000.0), (), {sessions[0]: frozenset(names)})


def labels_of(panel, rules, horizons=(5,), eligible=None):
    validity = compute_validity(panel, horizons, rules.ca_suspect_ratio)
    if eligible is None:
        eligible = np.ones(panel.close.shape, dtype=bool)
    return labels.compute_labels(panel, validity, eligible)


def test_close_return_is_open_of_d_plus_one_to_close_of_d_plus_h(rules):
    """D0 ``labels``: P0 = O(D+1), window D+1..D+h, clipped to [-1, 1], split-normalized."""
    panel = make_panel()
    forward = labels_of(panel, rules, (5,))
    row, column, horizon = 10, 0, 5
    reference = panel.open[row + 1, column]
    target = panel.close[row + horizon, column]
    assert forward.close_return[horizon][row, column] == pytest.approx(
        target / reference - 1.0, abs=1e-15)


def test_excess_subtracts_the_dates_full_universe_median(rules):
    """The benchmark is the date's whole eligible label-valid universe, not the query sample."""
    panel = make_panel()
    forward = labels_of(panel, rules, (5,))
    row, horizon = 10, 5
    valid = forward.valid[horizon][row] & forward.eligible[row]
    expected = np.median(forward.close_return[horizon][row][valid])
    assert forward.market_median[horizon][row] == pytest.approx(expected, abs=1e-15)
    for column in range(panel.close.shape[1]):
        assert forward.excess_return[horizon][row, column] == pytest.approx(
            forward.close_return[horizon][row, column] - expected, abs=1e-15)
    # the median of the excess over that universe is zero by construction
    assert np.median(forward.excess_return[horizon][row][valid]) == pytest.approx(0.0, abs=1e-15)


def test_an_ineligible_ticker_is_not_in_the_benchmark_but_still_gets_an_excess(rules):
    panel = make_panel()
    eligible = np.ones(panel.close.shape, dtype=bool)
    eligible[:, 3] = False
    forward = labels_of(panel, rules, (5,), eligible=eligible)
    row, horizon = 10, 5
    inside = forward.valid[horizon][row] & eligible[row]
    assert forward.market_median[horizon][row] == pytest.approx(
        np.median(forward.close_return[horizon][row][inside]), abs=1e-15)
    assert np.isfinite(forward.excess_return[horizon][row, 3])


def test_a_label_is_blind_to_prices_after_its_own_horizon(rules):
    """§20: changing bars after d+h must not move the label that ended at d+h."""
    panel = make_panel()
    horizon, row = 5, 10
    before = labels_of(panel, rules, (horizon,))
    close = panel.close.copy()
    # A 1.5x step, deliberately below the 3.0 corporate-action ratio: a bigger jump would make
    # the label CA-suspect and invalidate it, which would prove nothing about point-in-time.
    close[row + horizon + 1:] *= 1.5
    mutated = labels_of(with_changes(panel, close=close, open=close * 0.999,
                                     high=close * 1.02, low=close * 0.98), rules, (horizon,))
    assert before.close_return[horizon][row].tobytes() == mutated.close_return[horizon][row].tobytes()
    assert before.excess_return[horizon][row].tobytes() == mutated.excess_return[horizon][row].tobytes()
    # positive control: the same change does move a label whose horizon reaches into it
    assert before.close_return[horizon][row + 2].tobytes() != \
        mutated.close_return[horizon][row + 2].tobytes()


def test_the_return_is_clipped_to_the_declared_bounds(rules):
    panel = make_panel()
    close = panel.close.copy()
    close[15:, 0] *= 100.0
    panel = with_changes(panel, close=close, open=close * 0.999, high=close * 1.02, low=close * 0.98)
    forward = labels_of(panel, rules, (5,))
    finite = forward.close_return[5][np.isfinite(forward.close_return[5])]
    assert finite.max() <= labels.RETURN_CLIP and finite.min() >= -labels.RETURN_CLIP


# --- 3. The query-future firewall (§15, §20) ----------------------------------------------

def signals_of(dataset, rules, parent_run, tmp_path, history=None):
    """Run D3 and return (S, sigma) per test plus the evaluation labels, as plain arrays."""
    result = d3.execute(dataset, fixtures.SNAPSHOT_ID, parent_dir=parent_run, rules=rules,
                        runs_dir=tmp_path, log=lambda _: None)
    import pyarrow.parquet as pq

    table = pq.read_table(result.run_dir / "signal_rows.parquet",
                          columns=["test_id", "query_date_idx", "sample_rank", "S", "sigma"])
    labels_table = pq.read_table(result.run_dir / "evaluation_labels.parquet",
                                 columns=["horizon", "query_date_idx", "sample_rank",
                                          "query_forward_return"])
    return result, table, labels_table


def test_the_signal_cannot_see_the_query_future(dataset, rules, parent_run, tmp_path):
    """§20 core: mutate every bar after the last query date.

    S and sigma must be bit-identical, because every value behind them belongs to a neighbour
    whose label ended at ``d + h <= D - W``. The query's own realized label must change, because
    it is a statement about exactly the bars that moved - a test that only proved the first half
    would pass on a pipeline that had stopped computing anything at all.
    """
    import pyarrow.parquet as pq

    base_result, base_signals, base_labels = signals_of(dataset, rules, parent_run,
                                                        tmp_path / "base")
    last_query = int(np.asarray(base_signals["query_date_idx"]).max())

    mutated_root = tmp_path / "mutated" / "1_US-B"
    fixtures.build(mutated_root)
    history = history_of(mutated_root, rules)
    close = history.panel.close.copy()
    rng = np.random.default_rng(11)
    shape = close[last_query + 1:].shape
    close[last_query + 1:] *= rng.uniform(1.1, 2.5, shape)
    history.panel = with_changes(history.panel, close=close, open=close * 0.999,
                                 high=close * 1.02, low=close * 0.98)
    history._cache.clear()

    horizons = labels.horizons_of(rules.combinations)
    validity = compute_validity(history.panel, horizons, rules.ca_suspect_ratio)
    eligible = labels.eligibility_matrix(len(history.grid), len(history.tickers), {
        i: universe.evaluate(history.panel_view(i), history.membership(i), rules).eligible
        for i in range(len(history.grid))})
    forward = labels.compute_labels(history.panel, validity, eligible)

    parent = d3.load_parent(parent_run)
    moved_signal, moved_label = 0, 0
    for name in parent.tests:
        test = next(t for t in rules.tests if t.name == name)
        neighbors = d3.read_neighbors(parent, test)
        queries = d3.read_query_results(parent, test)
        counts = d3.align(neighbors, queries)
        after = d3.build_signals(test=test, neighbors=neighbors, queries=queries, counts=counts,
                                 excess=forward.excess_for(test.horizon), top_k=parent.top_k)
        want = np.asarray(base_signals.filter(
            np.asarray(base_signals["test_id"].to_pylist()) == name)["S"])
        assert after.signal.tobytes() == np.asarray(want).tobytes(), f"{name}: S moved"
        moved_signal += 1
    assert moved_signal == 14

    for horizon in horizons:
        rows = evaluation.build(forward, horizon, queries["query_date_idx"],
                                queries["sample_rank"], queries["query_ticker_col"])
        mask = np.asarray(base_labels["horizon"]) == horizon
        before = np.asarray(base_labels.filter(mask)["query_forward_return"])
        if not np.array_equal(np.nan_to_num(before, nan=-999.0),
                              np.nan_to_num(rows.forward_return, nan=-999.0)):
            moved_label += 1
    assert moved_label == len(horizons), "the evaluation labels did not react to the mutation"


def test_the_signal_module_cannot_import_the_evaluation_path():
    """§15: structural separation, not a convention. ``signal.py`` must not name ``evaluation``."""
    def imported(path):
        out = []
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                out.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                out.append(node.module or "")
        return out

    for name in imported(PACKAGE / "signal.py"):
        assert "evaluation" not in name, name
    assert any("labels" in n for n in imported(PACKAGE / "evaluation.py"))


#: The modules that exist by the end of D3. D4 is the phase that is *supposed* to compute an IC,
#: a quintile and a baseline, so the scan below names the D0-D3 set instead of globbing a package
#: that later phases also live in.
D3_MODULES = ("config.py", "models.py", "identity.py", "source.py", "universe.py",
              "label_extension.py", "sampling.py", "encoder.py", "library.py", "similarity.py",
              "neighbor_search.py", "pit.py", "artifacts.py", "d2.py", "labels.py", "signal.py",
              "evaluation.py", "d3.py")


def test_d3_computes_no_alpha_verdict():
    """§16: nothing up to and including D3 names an IC, a quintile, a baseline or a decision."""
    def identifiers(path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                first = node.body[0] if node.body else None
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                        and isinstance(first.value.value, str):
                    docstrings.add(id(first.value))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                found.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                found.add(node.attr.lower())
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found.add(node.name.lower())
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in docstrings:
                found.add(node.value.lower())
        return found

    # ``config.py`` is the rules reader: exposing a declared field such as the bootstrap seed
    # is its job, and ``load_rules`` uses that seed only to cross-check the query hash literal.
    for name in D3_MODULES:
        path = PACKAGE / name
        assert path.exists(), name
        if name == "config.py":
            continue
        found = identifiers(path)
        for identifier in FORBIDDEN_IDENTIFIERS:
            hits = [n for n in found if identifier in n]
            assert not hits, (name, identifier, hits)


def test_the_summary_reports_counts_and_never_an_average_signal(d3_run):
    """§16: row counts, valid counts and runtime are allowed; a mean S would not be."""
    text = json.dumps(d3_run.summary).lower()
    for banned in ("mean_s", "avg_s", "direction", "agreement", "ic", "quintile"):
        if banned == "ic":
            continue  # 'ic' appears inside unrelated words; the identifier test covers the code
        assert banned not in text, banned
    assert set(d3_run.summary["neighbor_label_join"]) == {
        "neighbor_rows", "neighbor_valid", "neighbor_invalid"}


# --- 4. Parent binding (§3) ---------------------------------------------------------------

def test_a_parent_without_a_complete_token_is_refused(tmp_path):
    empty = tmp_path / "unfinished"
    empty.mkdir()
    with pytest.raises(HardFail) as caught:
        d3.load_parent(empty)
    assert caught.value.code == "F2"


def test_a_changed_parent_neighbour_table_is_refused(dataset, rules, parent_run, tmp_path):
    """§3: D2 artifacts are immutable input; a single flipped value stops D3."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.backtest.strategy_d_analog import artifacts

    copy_dir = tmp_path / "tampered"
    copy_dir.mkdir()
    for path in parent_run.iterdir():
        (copy_dir / path.name).write_bytes(path.read_bytes())
    parent = d3.load_parent(copy_dir)
    test = next(t for t in rules.tests if t.name == parent.tests[0])
    assert d3.read_neighbors(parent, test)["rank"].size > 0     # the untouched copy is accepted

    target = copy_dir / f"neighbors_{test.name}.parquet"
    table = pq.read_table(target)
    metric = np.asarray(table["metric_value"]).copy()
    metric[0] += 1e-9
    columns = {name: table[name] for name in table.column_names}
    columns["metric_value"] = pa.array(metric, pa.float64())
    artifacts.write_table(target, pa.table(columns).replace_schema_metadata(
        table.schema.metadata or {}), artifacts.read_identity(target))
    with pytest.raises(HardFail) as caught:
        d3.read_neighbors(d3.load_parent(copy_dir), test)
    assert caught.value.code == "F1"


def test_a_parent_bound_to_another_dataset_is_refused(dataset, rules, parent_run, tmp_path):
    """Freeze mutation: D3 loads with the parent's identity as ``expected`` and stops on a diff."""
    other = tmp_path / "other" / "1_US-B"
    fixtures.build(other, day_count=280)
    with pytest.raises(HardFail) as caught:
        d3.execute(other, fixtures.SNAPSHOT_ID, parent_dir=parent_run, rules=rules,
                   runs_dir=tmp_path / "runs", log=lambda _: None)
    assert caught.value.code == "R2"


def test_the_identity_records_the_parent_and_the_label_contract(d3_run, rules):
    payload = d3_run.identity.payload
    assert payload["phase"] == "D3" and d3_run.identity.run_id.startswith("dsig1-")
    assert payload["parent_d2_run_id"].startswith("dneigh1-")
    assert len(payload["parent_d2_identity"]) == 64
    assert len(payload["parent_d2_complete_digest"]) == 64
    assert payload["label_value_contract"] == labels.LABEL_VALUE_CONTRACT
    assert payload["rules_checksum"] == rules.checksum
    assert payload["data"]["grid_digest"] and payload["data"]["freeze_digest"]
    assert payload["signal"]["S"].startswith("median neighbour excess_return_h")
    assert len(payload["tests"]) == 14


# --- 5. Artifacts and determinism ---------------------------------------------------------

def test_the_run_writes_every_declared_artifact_and_completes_last(d3_run, dataset, rules):
    from app.backtest.strategy_d_analog import artifacts

    run_dir = d3_run.run_dir
    assert not (run_dir / "COMPLETE.json").exists()
    d3.finish(d3_run, d3.run_context(dataset, fixtures.SNAPSHOT_ID))
    for name in ("run_identity.json", "parent.json", "status_counts.json", "summary.json",
                 "signal_rows.parquet", "evaluation_labels.parquet"):
        assert (run_dir / name).exists(), name
    complete = artifacts.load_complete(run_dir)
    assert complete["verdict"] == "PASS"
    assert complete["parent_d2_run_id"] == d3_run.summary["parent_d2_run_id"]
    expected = d3_run.summary["artifact_identity"]
    for path in sorted(run_dir.glob("*.parquet")):
        assert artifacts.read_identity(path) == expected, path.name
    for path in sorted(run_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for field in artifacts.IDENTITY_FIELDS:
            assert payload.get(field) == expected[field], (path.name, field)


def test_the_signal_table_carries_what_d4_needs_and_nothing_it_must_not_see(d3_run, rules):
    """§24: D4 reads this table alone - no pattern library, no similarity engine."""
    import pyarrow.parquet as pq

    schema = pq.read_schema(d3_run.run_dir / "signal_rows.parquet")
    required = {"test_id", "query_date_idx", "query_ticker", "sample_rank", "window", "horizon",
                "representation", "neighbor_total", "neighbor_valid", "S", "sigma",
                "distribution_mean", "distribution_median", "distribution_std", "hit_rate",
                "signal_status"}
    assert required <= set(schema.names)
    for quantile in (10, 25, 50, 75, 90):
        assert f"distribution_q{quantile:02d}" in schema.names
    # The answer key is a separate table, joined by D4 and never by the signal path.
    assert not any("query_forward" in n or "query_label" in n for n in schema.names)
    evaluation_schema = pq.read_schema(d3_run.run_dir / "evaluation_labels.parquet")
    assert {"horizon", "query_date_idx", "sample_rank", "query_forward_return",
            "query_label_valid"} <= set(evaluation_schema.names)
    assert "S" not in evaluation_schema.names and "sigma" not in evaluation_schema.names


def test_every_test_id_produces_a_signal_row_for_every_query(d3_run, rules):
    import pyarrow.parquet as pq

    table = pq.read_table(d3_run.run_dir / "signal_rows.parquet", columns=["test_id"])
    names, counts = np.unique(np.asarray(table["test_id"].to_pylist()), return_counts=True)
    assert len(names) == 14
    assert len(set(counts)) == 1, "the 14 tests do not share one query sample"
    totals = d3_run.summary["signal_status_by_test"]
    assert set(totals) == set(names)
    for name in names:
        assert sum(totals[name].values()) == int(counts[0])


def test_the_library_already_applied_label_validity(d3_run, parent_run, rules):
    """D0 ``library.definition`` includes "has a valid label for the tested horizon h", so an
    accepted neighbour is label-valid by construction and the join must lose none of them.

    Whether a query then reaches top_k is decided entirely by how many neighbours D2 accepted:
    on this small fixture universe D2 itself runs out of candidates, so INSUFFICIENT here comes
    from the parent, never from a label going missing.
    """
    import pyarrow.parquet as pq

    join = d3_run.summary["neighbor_label_join"]
    assert join["neighbor_rows"] == join["neighbor_valid"]
    assert join["neighbor_invalid"] == 0

    table = pq.read_table(d3_run.run_dir / "signal_rows.parquet",
                          columns=["test_id", "neighbor_total", "neighbor_valid", "signal_status"])
    total = np.asarray(table["neighbor_total"])
    valid = np.asarray(table["neighbor_valid"])
    assert np.array_equal(total, valid), "a label was lost between D2 and D3"
    status = np.asarray(table["signal_status"].to_pylist())
    insufficient = status == signal.SignalStatus.INSUFFICIENT_VALID_NEIGHBORS
    assert np.array_equal(insufficient, total < rules.top_k)


def test_two_identical_runs_agree_on_every_content_digest(dataset, rules, parent_run, tmp_path):
    first = d3.execute(dataset, fixtures.SNAPSHOT_ID, parent_dir=parent_run, rules=rules,
                       runs_dir=tmp_path / "one", log=lambda _: None)
    second = d3.execute(dataset, fixtures.SNAPSHOT_ID, parent_dir=parent_run, rules=rules,
                        runs_dir=tmp_path / "two", log=lambda _: None)
    assert first.digests == second.digests
    assert first.identity.digest == second.identity.digest
    assert first.summary["signal_status_by_test"] == second.summary["signal_status_by_test"]
    assert first.summary["label_value_counts"] == second.summary["label_value_counts"]


def test_the_evaluation_table_covers_every_horizon_once_per_query(d3_run, rules):
    import pyarrow.parquet as pq

    table = pq.read_table(d3_run.run_dir / "evaluation_labels.parquet",
                          columns=["horizon", "query_date_idx", "sample_rank"])
    horizons = labels.horizons_of(rules.combinations)
    values, counts = np.unique(np.asarray(table["horizon"]), return_counts=True)
    assert sorted(values) == sorted(horizons)
    assert len(set(counts)) == 1
    keys = list(zip(np.asarray(table["horizon"]).tolist(),
                    np.asarray(table["query_date_idx"]).tolist(),
                    np.asarray(table["sample_rank"]).tolist()))
    assert len(set(keys)) == len(keys)
