"""D4 contracts: the declared statistic, the declared baselines, and a gate that cannot be moved.

The risk this phase carries is not a wrong number, it is a threshold that drifts once the numbers
are visible. So the tests here pin the gate arithmetic against the ten conditions as D0 wrote
them, pin the statistic against hand-computed Spearman values, and assert that a result which
misses a condition stays failed however close it came.

Everything runs on synthetic data. Expected values come from the declared formulas, computed
independently of the implementation.
"""

import ast
import json
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_c_selection.panel import Panel
from app.backtest.strategy_d_analog import baselines, features, gate, metrics, resample
from app.backtest.strategy_d_analog.config import load_rules
from app.backtest.strategy_d_analog.models import HardFail
from tests.strategy_d import fixtures

PACKAGE = Path(__file__).resolve().parents[2] / "app/backtest/strategy_d_analog"


@pytest.fixture(scope="module")
def rules():
    return load_rules()


# --- 1. The declared statistic ------------------------------------------------------------

def test_spearman_matches_the_rank_difference_formula():
    """Cross-checked against 1 - 6*sum(d^2)/(n(n^2-1)), the closed form for untied ranks."""
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    b = np.array([2.0, 1.0, 4.0, 3.0, 5.0])
    d_squared = np.sum((metrics.average_rank(a) - metrics.average_rank(b)) ** 2)
    closed_form = 1.0 - 6.0 * d_squared / (5 * (25 - 1))
    assert metrics.spearman(a, b) == pytest.approx(closed_form, abs=1e-12)
    assert metrics.spearman(a, b) == pytest.approx(0.8, abs=1e-12)
    assert metrics.spearman(a, a) == pytest.approx(1.0, abs=1e-12)
    assert metrics.spearman(a, -a) == pytest.approx(-1.0, abs=1e-12)


def test_spearman_averages_ties_and_refuses_a_flat_side():
    tied = np.array([1.0, 1.0, 2.0, 3.0])
    assert np.array_equal(metrics.average_rank(tied), [1.5, 1.5, 3.0, 4.0])
    assert np.isnan(metrics.spearman(np.ones(4), tied))
    assert np.isnan(metrics.spearman(np.array([1.0]), np.array([2.0])))


def test_quintiles_are_built_inside_the_date_and_are_equal_count():
    signal = np.arange(10.0)
    realized = np.arange(10.0)
    baskets = metrics.quintile_baskets(signal, realized, np.arange(10))
    assert np.allclose(baskets, [0.5, 2.5, 4.5, 6.5, 8.5])
    assert metrics.monotonic_violations(baskets) == 0
    reversed_baskets = metrics.quintile_baskets(signal, -realized, np.arange(10))
    assert metrics.monotonic_violations(reversed_baskets) == 4
    assert np.isnan(metrics.quintile_baskets(np.arange(3.0), np.arange(3.0), np.arange(3))).all()


def test_time_blocks_are_four_consecutive_near_equal_groups():
    parts = metrics.time_blocks(np.arange(221), 4)
    assert [len(p) for p in parts] == [56, 55, 55, 55]
    assert sum(len(p) for p in parts) == 221
    flat = np.concatenate(parts)
    assert np.array_equal(flat, np.sort(flat))     # chronological, never shuffled


# --- 2. Bootstrap and multiplicity --------------------------------------------------------

def test_the_bootstrap_is_the_declared_recipe(rules):
    assert resample.BLOCK_LENGTH == 20
    assert resample.REPLICATES == 10_000
    assert resample.SEED == 20260917
    assert resample.FAMILY_SIZE == 14
    assert resample.BONFERRONI_ALPHA == pytest.approx(0.05 / 14, abs=1e-15)
    assert 1 - resample.BONFERRONI_ALPHA == pytest.approx(0.99643, abs=1e-5)
    declared = rules.raw["statistics"]
    assert "block length 20" in declared["bootstrap"] and "10000" in declared["bootstrap"]
    assert str(resample.SEED) in declared["bootstrap"]
    assert declared["ci"] == "percentile"


def test_bootstrap_draws_are_blocks_and_are_reproducible():
    first = resample.block_indices(221, horizon=5, replicates=500)
    assert first.shape == (500, 221)
    assert np.array_equal(first, resample.block_indices(221, horizon=5, replicates=500))
    assert not np.array_equal(first, resample.block_indices(221, horizon=10, replicates=500))
    # within a replicate the draws come in runs of consecutive dates
    row = first[0]
    steps = np.diff(row[:resample.BLOCK_LENGTH])
    assert np.all(steps == 1), "a block is not contiguous"


def test_the_bonferroni_interval_is_wider_than_the_nominal_one():
    values = np.random.default_rng(3).normal(0.01, 0.05, 221)
    indices = resample.block_indices(221, horizon=1, replicates=2000)
    both = resample.both_levels(values, indices)
    assert both["bonferroni"].low < both["nominal"].low
    assert both["bonferroni"].high > both["nominal"].high
    assert both["bonferroni"].point == both["nominal"].point == pytest.approx(values.mean())


def test_paired_differences_use_the_same_draws():
    left = np.random.default_rng(1).normal(0.02, 0.05, 100)
    right = np.random.default_rng(2).normal(0.01, 0.05, 100)
    indices = resample.block_indices(100, horizon=1, replicates=1000)
    paired = resample.paired_difference(left, right, indices)
    assert paired["bonferroni"].point == pytest.approx(np.mean(left - right))
    # the same draws applied to the difference, not two independent resamples
    assert resample.paired_difference(left, right, indices)["bonferroni"].low == \
        paired["bonferroni"].low


# --- 3. Features and baselines ------------------------------------------------------------

def make_panel(days=120, names=("AAA", "BBB", "CCC", "DDD", "EEE"), seed=5):
    sessions = fixtures.sessions(days)
    rng = np.random.default_rng(seed)
    close = np.vstack([30.0 * np.exp(np.cumsum(rng.normal(0.0, 0.015, days))) for _ in names]).T
    return Panel(sessions, names, close * 0.999, close * 1.03, close * 0.97, close,
                 np.full((days, len(names)), 900_000.0), (), {sessions[0]: frozenset(names)})


def test_the_five_features_are_the_declared_formulas(rules):
    panel = make_panel()
    eligible = np.ones(panel.close.shape, dtype=bool)
    window, row, column = 20, 60, 0
    built = features.build(panel, window, eligible)
    price = panel.close[:, column]
    assert set(features.FEATURE_NAMES) == set(rules.raw["baseline_N2"]["features"])
    assert built.raw["return_W"][row, column] == pytest.approx(
        price[row] / price[row - window] - 1.0, abs=1e-12)
    assert built.raw["return_1d"][row, column] == pytest.approx(price[row] / price[row - 1] - 1.0)
    assert built.raw["return_5d"][row, column] == pytest.approx(price[row] / price[row - 5] - 1.0)
    steps = np.log(price[row - window + 1:row + 1] / price[row - window:row])
    assert built.raw["realized_vol_W"][row, column] == pytest.approx(steps.std(ddof=1), abs=1e-12)
    window_high = panel.high[row - window + 1:row + 1, column].max()
    assert built.raw["distance_to_W_high"][row, column] == pytest.approx(
        price[row] / window_high - 1.0, abs=1e-12)


def test_standardization_is_the_declared_percentile_rank():
    values = np.array([[10.0, 20.0, 30.0, 40.0]])
    mask = np.ones((1, 4), dtype=bool)
    assert np.allclose(features.percentile_rank(values, mask), [[0.0, 1 / 3, 2 / 3, 1.0]])
    tied = np.array([[10.0, 20.0, 20.0, 40.0]])
    assert np.allclose(features.percentile_rank(tied, mask), [[0.0, 0.5, 0.5, 1.0]])
    partial = np.array([[10.0, 20.0, 30.0, 40.0]])
    only_two = np.array([[True, False, True, False]])
    assert np.allclose(features.percentile_rank(partial, only_two)[0, [0, 2]], [0.0, 1.0])


def test_the_volatility_quintile_uses_the_declared_edges():
    panel = make_panel(names=tuple(f"T{i:02d}" for i in range(20)))
    built = features.build(panel, 20, np.ones(panel.close.shape, dtype=bool))
    row = 60
    assigned = built.volatility_quintile[row]
    rank = built.standardized["realized_vol_W"][row]
    assert features.VOLATILITY_EDGES == (0.2, 0.4, 0.6, 0.8)
    for column in np.nonzero(np.isfinite(rank))[0]:
        expected = int(sum(rank[column] > edge for edge in features.VOLATILITY_EDGES))
        assert assigned[column] == expected
    assert set(np.unique(assigned[assigned >= 0]).tolist()) <= {0, 1, 2, 3, 4}


def test_the_n1_draw_is_deterministic_and_respects_the_caps():
    candidates = np.arange(3000)
    ticker_col = np.arange(3000) % 900
    end_idx = (np.arange(3000) // 30) * 5
    figi = np.full(3000, -1)
    seed = baselines.n1_seed(0, "2025-10-01", "AAPL")
    first = baselines.draw_random_analogs(
        candidates=candidates, ticker_col=ticker_col, end_idx=end_idx, query_ticker_col=-1,
        query_figi_code=-1, figi_code=figi, seed=seed, top_k=50, ticker_cap=1, date_cap=5)
    again = baselines.draw_random_analogs(
        candidates=candidates, ticker_col=ticker_col, end_idx=end_idx, query_ticker_col=-1,
        query_figi_code=-1, figi_code=figi, seed=seed, top_k=50, ticker_cap=1, date_cap=5)
    assert first == again
    assert len(first) == 50
    assert len(set(ticker_col[first].tolist())) == 50              # ticker cap 1
    assert max(np.bincount(end_idx[first] // 5)) <= 5              # date cap 5
    other = baselines.draw_random_analogs(
        candidates=candidates, ticker_col=ticker_col, end_idx=end_idx, query_ticker_col=-1,
        query_figi_code=-1, figi_code=figi,
        seed=baselines.n1_seed(1, "2025-10-01", "AAPL"), top_k=50, ticker_cap=1, date_cap=5)
    assert other != first, "two replicates drew the same neighbours"


def test_the_n1_draw_never_returns_the_query_symbol():
    candidates = np.arange(2000)
    ticker_col = np.arange(2000) % 500
    end_idx = (np.arange(2000) // 25) * 5
    figi = np.where(np.arange(2000) % 500 == 7, 42, -1)
    chosen = baselines.draw_random_analogs(
        candidates=candidates, ticker_col=ticker_col, end_idx=end_idx, query_ticker_col=7,
        query_figi_code=42, figi_code=figi, seed=baselines.n1_seed(0, "2025-10-01", "Q"),
        top_k=50, ticker_cap=1, date_cap=5)
    assert 7 not in ticker_col[chosen].tolist()
    assert 42 not in figi[chosen].tolist()


def test_the_n1_seed_uses_the_declared_literal_and_replicate_base(rules):
    import hashlib

    assert baselines.N1_SEED == "20260917"
    assert baselines.N1_REPLICATES == int(rules.raw["baseline_N1"]["replicates"]) == 20
    assert str(rules.raw["baseline_N1"]["seed"]) == baselines.N1_SEED
    expected = int.from_bytes(hashlib.sha256(b"N1|20260917|0|2025-10-01|AAPL").digest(), "big")
    assert baselines.n1_seed(0, "2025-10-01", "AAPL") == expected


def test_orthogonalization_removes_exactly_the_feature_span():
    generator = np.random.default_rng(7)
    feature_matrix = generator.normal(size=(200, 5))
    signal = 3.0 + feature_matrix @ np.array([1.0, -2.0, 0.5, 0.0, 4.0])
    residual = baselines.orthogonalize(signal, feature_matrix)
    assert np.abs(residual).max() < 1e-9, "a pure feature combination should leave nothing"
    extra = generator.normal(size=200)
    mixed = baselines.orthogonalize(signal + extra, feature_matrix)
    assert np.abs(mixed).max() > 1e-6
    for column in range(5):
        assert abs(float(np.dot(mixed, feature_matrix[:, column]))) < 1e-8


# --- 4. The gate, exactly as D0 wrote it --------------------------------------------------

def passing_kwargs(**overrides):
    """A test that meets all ten conditions; each case below breaks exactly one of them."""
    base = dict(test_id="W20_H1_A", ic_point=0.01, ic_ci_low=0.002, ic_ci_high=0.02,
                delta_n1_ci_low=0.001, n2b_ci_low=0.001, delta_n2a_point=0.001,
                quintile_spread_point=0.0005,
                block_ic_points=[0.01, 0.01, 0.01, 0.01],
                other_horizon_ic_points={"W20_H3_A": 0.008, "W20_H5_A": 0.005},
                evaluable_dates=221, valid_queries=66255, unique_tickers=3327,
                insufficient_share=0.0, pit_violations=0)
    base.update(overrides)
    return base


def test_the_ten_conditions_are_the_declared_ten(rules):
    declared = rules.raw["pass_fail_policy"]["per_test_conditions"]
    assert [name for name, _ in gate.CONDITIONS] == list(declared)
    assert len(gate.CONDITIONS) == 10
    assert gate.MIN_EVALUABLE_DATES == 150
    assert gate.MIN_VALID_QUERIES == 20_000
    assert gate.MIN_UNIQUE_TICKERS == 1_000
    assert gate.MAX_INSUFFICIENT_SHARE == 0.05
    assert "150" in declared["9_sample"] and "20000" in declared["9_sample"]
    assert "1000" in declared["9_sample"] and "0.05" in declared["9_sample"]


def test_a_fully_passing_test_passes():
    verdict = gate.evaluate_test(**passing_kwargs())
    assert verdict.status == gate.PASS and verdict.failed == []


@pytest.mark.parametrize("override, broken", [
    ({"ic_point": 0.0}, "1_ic_point"),
    ({"ic_ci_low": 0.0}, "2_ic_ci_low"),
    ({"delta_n1_ci_low": -1e-9}, "3_delta_ic_vs_N1_ci_low"),
    ({"n2b_ci_low": 0.0}, "4_orthogonalized_ic_vs_N2b_ci_low"),
    ({"delta_n2a_point": 0.0}, "5_delta_ic_vs_N2a_point"),
    ({"quintile_spread_point": -1e-9}, "6_quintile_spread_point"),
    ({"block_ic_points": [0.01, 0.01, -0.01, -0.01]}, "7_time_blocks"),
    ({"other_horizon_ic_points": {"W20_H3_A": -0.001, "W20_H5_A": 0.005}}, "8_horizon_consistency"),
    ({"evaluable_dates": 149}, "9_sample"),
    ({"valid_queries": 19_999}, "9_sample"),
    ({"unique_tickers": 999}, "9_sample"),
    ({"insufficient_share": 0.0501}, "9_sample"),
    ({"pit_violations": 1}, "10_pit_violations"),
])
def test_each_condition_can_fail_on_its_own(override, broken):
    """A boundary miss is a miss: 0.0 is not "> 0" and 149 dates is not 150."""
    verdict = gate.evaluate_test(**passing_kwargs(**override))
    assert verdict.failed == [broken], verdict.failed
    assert verdict.status != gate.PASS


def test_a_boundary_value_is_not_rounded_into_a_pass():
    assert gate.evaluate_test(**passing_kwargs(ic_ci_low=1e-12)).status == gate.PASS
    assert gate.evaluate_test(**passing_kwargs(ic_ci_low=0.0)).status != gate.PASS
    assert gate.evaluate_test(**passing_kwargs(evaluable_dates=150)).status == gate.PASS
    assert gate.evaluate_test(**passing_kwargs(insufficient_share=0.05)).status == gate.PASS


def test_a_nan_never_satisfies_a_condition():
    nan = float("nan")
    verdict = gate.evaluate_test(**passing_kwargs(ic_point=nan, ic_ci_low=nan, n2b_ci_low=nan))
    assert not verdict.conditions["1_ic_point"]
    assert not verdict.conditions["2_ic_ci_low"]
    assert not verdict.conditions["4_orthogonalized_ic_vs_N2b_ci_low"]


def test_a_test_failing_only_a_soft_condition_is_inconclusive():
    """D0 ``decision.INCONCLUSIVE``: conditions 1-4 met and only 7, 8 or 9 missed."""
    for soft in ({"block_ic_points": [0.01, 0.01, -0.01, -0.01]},
                 {"other_horizon_ic_points": {"W20_H3_A": -0.001}},
                 {"evaluable_dates": 100}):
        verdict = gate.evaluate_test(**passing_kwargs(**soft))
        assert verdict.status == gate.INCONCLUSIVE, soft
    # missing a hard condition as well makes it a plain FAIL
    verdict = gate.evaluate_test(**passing_kwargs(evaluable_dates=100, delta_n2a_point=-0.001))
    assert verdict.status == gate.FAIL


def test_a_significant_negative_ic_is_an_inverse_effect_and_never_a_pass():
    verdict = gate.evaluate_test(**passing_kwargs(ic_point=-0.01, ic_ci_low=-0.02,
                                                  ic_ci_high=-0.002))
    assert verdict.inverse_effect
    assert verdict.status == gate.INVERSE_EFFECT != gate.PASS


def test_the_strategy_decision_follows_the_declared_rule():
    winner = gate.evaluate_test(**passing_kwargs(test_id="WIN"))
    loser = gate.evaluate_test(**passing_kwargs(test_id="LOSE", ic_point=-0.01, ic_ci_low=-0.02,
                                                delta_n2a_point=-0.01))
    soft = gate.evaluate_test(**passing_kwargs(test_id="SOFT", evaluable_dates=200,
                                               block_ic_points=[0.01, -0.01, -0.01, 0.01]))
    assert gate.strategy_decision([winner, loser], 221)["decision"] == gate.PASS
    assert gate.strategy_decision([loser, soft], 221)["decision"] == gate.INCONCLUSIVE
    assert gate.strategy_decision([loser], 221)["decision"] == gate.FAIL
    # too little data is inconclusive whatever the tests said
    assert gate.strategy_decision([loser], 100)["decision"] == gate.INCONCLUSIVE


def test_the_gate_has_no_condition_d0_did_not_declare(rules):
    """Monotonicity and concentration are reported; neither may move a verdict."""
    declared = set(rules.raw["pass_fail_policy"]["per_test_conditions"])
    verdict = gate.evaluate_test(**passing_kwargs())
    assert set(verdict.conditions) == declared
    for word in ("monotonic", "concentration", "sharpe", "drawdown"):
        assert not any(word in name for name in verdict.conditions), word


def test_concentration_is_a_diagnostic_with_no_verdict_attached():
    values = np.array([1.0, 0.1, 0.1])
    report = metrics.concentration(values, ["AAA", "BBB", "CCC"], top=2)
    assert report["top"][0]["label"] == "AAA"
    assert report["top"][0]["abs_share"] == pytest.approx(1.0 / 1.2, abs=1e-12)
    assert "decision" not in report and "status" not in report


# --- 5. The D4 firewall -------------------------------------------------------------------

def imported_names(path: Path) -> list[str]:
    out: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.append(node.module or "")
    return out


def test_d4_never_reruns_the_analog_search():
    """§2: D's own signal is read from D3. The encoder is not reachable from the D4 modules."""
    for name in ("d4.py", "baselines.py", "metrics.py", "gate.py", "resample.py", "features.py"):
        for imported in imported_names(PACKAGE / name):
            tail = imported.rsplit(".", 1)[-1]
            assert tail not in ("encoder", "library", "d2", "d3", "signal"), (name, imported)


def test_the_gate_module_reads_no_data_at_all():
    """A gate that cannot open a file cannot be tuned to one."""
    for imported in imported_names(PACKAGE / "gate.py"):
        assert not imported.startswith("app."), imported
        assert imported not in ("json", "pathlib", "pyarrow"), imported


def test_the_declared_n1_deviation_is_recorded_not_hidden():
    """The draw order departs from D0's literal recipe; the run identity has to say so."""
    contracts = baselines.contracts()
    assert contracts["n1_draw_contract"] == "d-n1-draw-v1-prefix-seeded"
    body = (PACKAGE / "baselines.py").read_text(encoding="utf-8")
    assert "DECLARED DEVIATION" in body
    assert "sha256" in body and "86 single-core hours" in body


# --- 6. The whole chain on synthetic data -------------------------------------------------

@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("d4workspace") / "1_US-B"
    fixtures.build(root)
    return root


@pytest.fixture(scope="module")
def chain(dataset, rules, tmp_path_factory):
    """D2 -> D3 -> D4 on the fixture dataset, so D4 is exercised through real parent artifacts."""
    from app.backtest.strategy_d_analog import d2, d3
    from app.backtest.strategy_d_analog.source import load_daily_history

    history = load_daily_history(dataset, fixtures.SNAPSHOT_ID,
                                 allowed_exchanges=rules.allowed_exchanges)
    root = tmp_path_factory.mktemp("d4chain")
    second = d2.execute(dataset, fixtures.SNAPSHOT_ID, expected=history.freeze,
                        parent_run="dpit1-fixture", rules=rules, runs_dir=root,
                        log=lambda _: None, eval_date_limit=3, audit_count=1, capture_audit=False)
    d2.finish(second, d2.run_context(dataset, fixtures.SNAPSHOT_ID))
    third = d3.execute(dataset, fixtures.SNAPSHOT_ID, parent_dir=second.run_dir, rules=rules,
                       runs_dir=root, log=lambda _: None)
    d3.finish(third, d3.run_context(dataset, fixtures.SNAPSHOT_ID))
    return {"root": root, "d2": second.run_dir, "d3": third.run_dir, "dataset": dataset}


def test_d4_refuses_parents_that_are_not_bound_to_each_other(chain, rules, tmp_path):
    from app.backtest.strategy_d_analog import d4

    with pytest.raises(HardFail) as caught:
        d4.load_parents(chain["d3"], chain["d3"])     # a D3 run where a D2 run is required
    assert caught.value.code == "F2"
    empty = tmp_path / "nothing"
    empty.mkdir()
    with pytest.raises(HardFail):
        d4.load_parents(empty, chain["d2"])


def test_d4_refuses_a_parent_table_that_moved(chain, rules, tmp_path):
    """§1 gate 5/6: a single altered value in D3's signal table stops D4 before it evaluates."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.backtest.strategy_d_analog import artifacts, d4

    copy_dir = tmp_path / "tampered_d3"
    copy_dir.mkdir()
    for path in chain["d3"].iterdir():
        (copy_dir / path.name).write_bytes(path.read_bytes())
    parents = d4.load_parents(copy_dir, chain["d2"])
    assert d4.read_parent_tables(parents) is not None      # the untouched copy is accepted

    target = copy_dir / "signal_rows.parquet"
    table = pq.read_table(target)
    values = np.asarray(table["S"]).copy()
    values[0] = values[0] + 1e-9 if np.isfinite(values[0]) else 0.5
    columns = {name: table[name] for name in table.column_names}
    columns["S"] = pa.array(values, pa.float64())
    artifacts.write_table(target, pa.table(columns), artifacts.read_identity(target))
    with pytest.raises(HardFail) as caught:
        d4.read_parent_tables(d4.load_parents(copy_dir, chain["d2"]))
    assert caught.value.code == "F1"


def test_d4_runs_the_chain_and_writes_every_artifact(chain, rules):
    from app.backtest.strategy_d_analog import artifacts, d4

    result = d4.execute(chain["dataset"], fixtures.SNAPSHOT_ID, d3_dir=chain["d3"],
                        d2_dir=chain["d2"], rules=rules, runs_dir=chain["root"] / "d4",
                        log=lambda _: None)
    assert not (result.run_dir / "COMPLETE.json").exists()
    d4.finish(result, d4.run_context(chain["dataset"], fixtures.SNAPSHOT_ID))
    for name in ("run_identity.json", "parent.json", "summary.json", "gate_results.json",
                 "bootstrap.json", "testid_results.parquet", "ic_daily.parquet",
                 "quintile_daily.parquet", "sigma_daily.parquet"):
        assert (result.run_dir / name).exists(), name
    complete = artifacts.load_complete(result.run_dir)
    assert complete["phase"] == "D4"
    assert complete["parent_d3_run_id"].startswith("dsig1-")
    assert complete["parent_d2_run_id"].startswith("dneigh1-")
    assert result.identity.run_id.startswith("deval1-")
    expected = result.summary["artifact_identity"]
    for path in sorted(result.run_dir.glob("*.parquet")):
        assert artifacts.read_identity(path) == expected, path.name
    for path in sorted(result.run_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for field in artifacts.IDENTITY_FIELDS:
            assert payload.get(field) == expected[field], (path.name, field)
    assert result.decision["decision"] in (gate.PASS, gate.FAIL, gate.INCONCLUSIVE)
    assert len(result.verdicts) == 14


def test_two_identical_d4_runs_agree_on_every_content_digest(chain, rules):
    """§27: same parents, same code, same rules, same freeze - same numbers."""
    from app.backtest.strategy_d_analog import d4

    first = d4.execute(chain["dataset"], fixtures.SNAPSHOT_ID, d3_dir=chain["d3"],
                       d2_dir=chain["d2"], rules=rules, runs_dir=chain["root"] / "det1",
                       log=lambda _: None)
    second = d4.execute(chain["dataset"], fixtures.SNAPSHOT_ID, d3_dir=chain["d3"],
                        d2_dir=chain["d2"], rules=rules, runs_dir=chain["root"] / "det2",
                        log=lambda _: None)
    assert first.digests == second.digests
    assert first.identity.digest == second.identity.digest
    assert first.decision == second.decision
    assert first.summary["bootstrap_draw_digests"] == second.summary["bootstrap_draw_digests"]
    assert first.summary["sample"] == second.summary["sample"]


def test_the_random_baseline_is_reproducible_across_runs(chain, rules):
    """The N1 draws are keyed by (replicate, query), so they cannot drift between runs."""
    from app.backtest.strategy_d_analog import d4

    result = d4.execute(chain["dataset"], fixtures.SNAPSHOT_ID, d3_dir=chain["d3"],
                        d2_dir=chain["d2"], rules=rules, runs_dir=chain["root"] / "n1det",
                        log=lambda _: None)
    import pyarrow.parquet as pq

    table = pq.read_table(result.run_dir / "ic_daily.parquet", columns=["test_id", "ic_n1"])
    again = d4.execute(chain["dataset"], fixtures.SNAPSHOT_ID, d3_dir=chain["d3"],
                       d2_dir=chain["d2"], rules=rules, runs_dir=chain["root"] / "n1det2",
                       log=lambda _: None)
    other = pq.read_table(again.run_dir / "ic_daily.parquet", columns=["test_id", "ic_n1"])
    assert np.asarray(table["ic_n1"]).tobytes() == np.asarray(other["ic_n1"]).tobytes()
