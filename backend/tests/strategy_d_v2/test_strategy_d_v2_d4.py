"""The screening gate: the statistics it reads, and the wall between reading and deciding.

The tests that matter most here are not the ones that check a correlation is computed correctly.
They are the ones that check the verdict is a function of the declaration and nothing else: that
every threshold comes out of the frozen JSON, that a statistic cannot see a threshold, and that
the three-way decision follows the words in ``pass_fail_policy.decision`` even in the cases a
result-shopping author would be tempted to round.
"""

import inspect
import json

import numpy as np
import pyarrow as pa
import pytest

from app.backtest.strategy_d_analog import metrics, resample
from app.backtest.strategy_d_v2 import d4
from app.backtest.strategy_d_v2.config import FEATURE_NAMES, load_rules
from app.backtest.strategy_d_v2.d3 import SIGNAL_STATUS_ORDER
from app.backtest.strategy_d_v2.models import HardFail

RULES = load_rules()
HORIZON = RULES.primary_horizon
OK = SIGNAL_STATUS_ORDER.index("OK")


# -- fixtures ------------------------------------------------------------------------------------

def make_sample(dates=40, per_date=150, *, seed=7, signal_strength=0.0, status=None,
                label_valid=None):
    """A joined sample with a tunable amount of real signal in A(q).

    ``signal_strength`` mixes the realized outcome into A(q), so a test can ask for a sample
    whose IC is genuinely positive without hand-writing correlations.
    """
    rng = np.random.default_rng(seed)
    rows = dates * per_date
    date_idx = np.repeat(np.arange(260, 260 + dates), per_date).astype(np.int64)
    sample_rank = np.tile(np.arange(per_date), dates).astype(np.int64)
    ticker_col = np.tile(np.arange(per_date), dates).astype(np.int64)
    realized = rng.normal(0.0, 0.03, rows)
    analog = signal_strength * realized + (1 - abs(signal_strength)) * rng.normal(0.0, 0.01, rows)
    b0 = rng.normal(0.0, 0.1, rows)
    return d4.JoinedSample(
        date_idx=date_idx, sample_rank=sample_rank, ticker_col=ticker_col,
        analog=analog, b0=b0, b0_strong=rng.normal(0.0, 0.1, rows), realized=realized,
        label_valid=np.ones(rows, dtype=bool) if label_valid is None else label_valid,
        signal_status=np.full(rows, OK, dtype=np.int64) if status is None else status,
        mean_distance=rng.random(rows), horizon=HORIZON)


def stats_for(sample, series, boot, *, pit_violations=0, quintile_spread=0.001):
    scored = np.concatenate(series.rows)
    return {
        "sample": {"evaluable_dates": len(series), "valid_queries": int(scored.size),
                   "unique_tickers": int(np.unique(sample.ticker_col[scored]).size),
                   "insufficient_neighbors_share": 0.0, "vector_undefined_share": 0.0},
        "mean_ic_a": float(np.mean(series.ic_a)), "mean_ic_b0": float(np.mean(series.ic_b0)),
        "mean_delta": float(np.mean(series.delta)),
        "delta_ci_low": boot.delta.low, "delta_ci_high": boot.delta.high,
        "ic_a_ci_high": boot.ic_a.high,
        "blocks_delta_positive": 4, "blocks_ic_a_positive": 4,
        "quintile_spread": quintile_spread, "pit_violations": pit_violations,
    }


# -- the join -------------------------------------------------------------------------------------

def _tables(rows=6, *, shuffle_evaluation=False, drop_one=False):
    keys = {"query_date_idx": np.arange(rows, dtype=np.int32) // 3,
            "sample_rank": (np.arange(rows, dtype=np.int16) % 3),
            "query_ticker_col": np.arange(rows, dtype=np.int32)}
    signal = pa.table({**{k: pa.array(v) for k, v in keys.items()},
                       "analog_signal_A": pa.array(np.linspace(-0.01, 0.01, rows)),
                       "b0": pa.array(np.linspace(-0.2, 0.2, rows)),
                       "b0_strong": pa.array(np.zeros(rows)),
                       "mean_distance": pa.array(np.ones(rows)),
                       "signal_status": pa.DictionaryArray.from_arrays(
                           pa.array(np.zeros(rows, dtype=np.int32)),
                           pa.array(list(SIGNAL_STATUS_ORDER)))})
    order = np.arange(rows)[::-1] if shuffle_evaluation else np.arange(rows)
    keep = slice(0, rows - 1) if drop_one else slice(None)
    evaluation = pa.table({
        **{k: pa.array(v[order][keep]) for k, v in keys.items()},
        "query_label_valid": pa.array(np.ones(rows, dtype=bool)[order][keep]),
        f"query_excess_return_{HORIZON}": pa.array(np.linspace(-0.05, 0.05, rows)[order][keep])})
    return signal, evaluation


def test_the_join_is_an_identity_that_must_hold_exactly():
    signal, evaluation = _tables()
    sample = d4.join_artifacts(signal, evaluation, HORIZON)
    assert len(sample) == 6
    assert sample.realized[0] == pytest.approx(-0.05)


def test_a_reordered_evaluation_artifact_is_refused_instead_of_merged():
    """A merge would quietly repair this. The two files came from one array in one order, so a
    reordering means something regenerated one of them, and the pair must not be scored."""
    signal, evaluation = _tables(shuffle_evaluation=True)
    with pytest.raises(HardFail):
        d4.join_artifacts(signal, evaluation, HORIZON)


def test_a_missing_evaluation_row_is_refused():
    signal, evaluation = _tables(drop_one=True)
    with pytest.raises(HardFail):
        d4.join_artifacts(signal, evaluation, HORIZON)


def test_a_wrong_horizon_column_is_refused():
    signal, evaluation = _tables()
    with pytest.raises(HardFail):
        d4.join_artifacts(signal, evaluation, HORIZON + 1)


# -- what counts as a scored query ------------------------------------------------------------------

def test_only_ok_status_with_a_decidable_label_is_scored():
    sample = make_sample(dates=3, per_date=10)
    status = sample.signal_status.copy()
    status[0] = SIGNAL_STATUS_ORDER.index("VECTOR_UNDEFINED")
    status[1] = SIGNAL_STATUS_ORDER.index("INSUFFICIENT_NEIGHBORS")
    valid = sample.label_valid.copy()
    valid[2] = False
    realized = sample.realized.copy()
    realized[2] = np.nan
    excluded = d4.JoinedSample(**{**sample.__dict__, "signal_status": status,
                                  "label_valid": valid, "realized": realized})
    assert excluded.usable.sum() == len(sample) - 3


def test_a_thin_date_is_dropped_whole_and_counted():
    """Dropping a date for A but keeping it for B0 would put delta on two different date sets."""
    sample = make_sample(dates=4, per_date=120)
    valid = sample.label_valid.copy()
    valid[:110] = False                       # date 0 keeps only 10 usable queries
    thinned = d4.JoinedSample(**{**sample.__dict__, "label_valid": valid})
    groups = d4.group_by_date(thinned)
    series = d4.per_date_ic(thinned, groups, min_valid_queries=100)
    assert len(series) == 3
    assert series.dropped_thin == (260,)
    assert len(series.ic_a) == len(series.ic_b0) == 3


def test_ic_a_and_ic_b0_are_measured_on_one_query_set_per_date():
    sample = make_sample(dates=5, per_date=120)
    groups = d4.group_by_date(sample)
    series = d4.per_date_ic(sample, groups, min_valid_queries=100)
    for position, (rows, count) in enumerate(zip(series.rows, series.valid_queries)):
        assert rows.size == count
        assert series.ic_a[position] == pytest.approx(
            metrics.spearman(sample.analog[rows], sample.realized[rows]))
        assert series.ic_b0[position] == pytest.approx(
            metrics.spearman(sample.b0[rows], sample.realized[rows]))
        # the two correlations saw the same rows, which is what makes delta a paired difference
        assert np.array_equal(rows, series.rows[position])


def test_a_real_signal_shows_up_as_a_positive_mean_ic():
    """A positive control: if the pipeline stopped correlating, every other test here would still
    pass on noise."""
    sample = make_sample(dates=30, per_date=150, signal_strength=0.6)
    series = d4.per_date_ic(sample, d4.group_by_date(sample), min_valid_queries=100)
    assert float(np.mean(series.ic_a)) > 0.3
    noise = make_sample(dates=30, per_date=150, signal_strength=0.0)
    noise_series = d4.per_date_ic(noise, d4.group_by_date(noise), min_valid_queries=100)
    assert abs(float(np.mean(noise_series.ic_a))) < 0.05


# -- bootstrap -------------------------------------------------------------------------------------

def test_the_bootstrap_is_paired_and_reproducible():
    sample = make_sample(dates=60, per_date=120)
    series = d4.per_date_ic(sample, d4.group_by_date(sample), min_valid_queries=100)
    boot = d4.bootstrap(series, block_length=20, replicates=500, seed=20260920, horizon=HORIZON)
    again = d4.bootstrap(series, block_length=20, replicates=500, seed=20260920, horizon=HORIZON)
    assert boot.draw_digest == again.draw_digest
    assert (boot.delta.low, boot.delta.high) == (again.delta.low, again.delta.high)
    # paired: the interval is built from per-date differences, not from two independent draws
    manual = resample.interval(series.delta, boot.indices, alpha=0.05, level="nominal_95")
    assert (manual.low, manual.high) == (boot.delta.low, boot.delta.high)


def test_the_gate_reads_the_literal_seed_and_reports_the_spawned_variant():
    sample = make_sample(dates=60, per_date=120)
    series = d4.per_date_ic(sample, d4.group_by_date(sample), min_valid_queries=100)
    boot = d4.bootstrap(series, block_length=20, replicates=400, seed=20260920, horizon=HORIZON)
    literal = resample.block_indices(len(series), horizon=d4.LITERAL_SPAWN, block_length=20,
                                     replicates=400, seed=20260920)
    assert boot.draw_digest == resample.draw_digest(literal)
    assert boot.alternate_digest != boot.draw_digest
    assert boot.as_dict()["alternate_seed_spawn"]["role"].startswith("disclosure only")


# -- the gate --------------------------------------------------------------------------------------

def test_every_threshold_comes_from_the_declaration():
    """No gate number is a literal in the module. If one were, editing the JSON would leave the
    code deciding by the old value while the report quoted the new one."""
    source = inspect.getsource(d4.screening_gate)
    for literal in ("0.0030", "0.003", "-0.0025", "150", "20000", "1000"):
        assert literal not in source
    sample = make_sample(dates=40, per_date=150)
    series = d4.per_date_ic(sample, d4.group_by_date(sample), min_valid_queries=100)
    boot = d4.bootstrap(series, block_length=20, replicates=200, seed=20260920, horizon=HORIZON)
    conditions = d4.screening_gate(stats_for(sample, series, boot), RULES)
    assert conditions["S5"].threshold == RULES.delta_threshold
    assert conditions["S6"].threshold == RULES.delta_ci_low_threshold
    assert conditions["S7"].threshold == RULES.block_stability_minimum
    assert conditions["S1"].threshold == RULES.sample_gate.as_dict()
    for name, condition in conditions.items():
        assert condition.declared == str(
            RULES.raw["pass_fail_policy"]["conditions"][condition.name])


def test_no_statistic_function_can_see_a_threshold():
    for function in (d4.per_date_ic, d4.bootstrap, d4.quintile_profile, d4.mean_ic_of,
                     d4.variance_disclosure):
        parameters = set(inspect.signature(function).parameters)
        assert not parameters & {"threshold", "gate", "conditions", "verdict"}


def _conditions(**overrides):
    base = {"S1": True, "S2": True, "S3": True, "S4": True, "S5": True, "S6": True, "S7": True,
            "S8": True}
    base.update(overrides)
    return {name: d4.Condition(f"{name}_x", holds, None, None, "") for name, holds in base.items()}


def _stats(mean_delta=0.005, ic_a_ci_high=0.02, delta_ci_high=0.02):
    return {"mean_delta": mean_delta, "ic_a_ci_high": ic_a_ci_high,
            "delta_ci_high": delta_ci_high}


def test_pass_needs_every_condition():
    assert d4.decide(_conditions(), _stats())["screening_verdict"] == "SCREENING_PASS"


@pytest.mark.parametrize("failed", ["S5", "S6", "S7"])
def test_borderline_is_exactly_one_optional_condition(failed):
    decision = d4.decide(_conditions(**{failed: False}), _stats())
    assert decision["screening_verdict"] == "SCREENING_BORDERLINE"
    assert decision["long_data_purchase_candidate"] == "YES_CONDITIONAL"


def test_two_optional_failures_are_a_fail():
    decision = d4.decide(_conditions(S5=False, S6=False), _stats())
    assert decision["screening_verdict"] == "SCREENING_FAIL"


@pytest.mark.parametrize("failed", ["S1", "S2", "S3", "S4", "S8"])
def test_a_core_failure_is_never_borderline(failed):
    assert d4.decide(_conditions(**{failed: False}),
                     _stats())["screening_verdict"] == "SCREENING_FAIL"


def test_borderline_needs_a_positive_point_estimate():
    """S5 can fail with mean delta either side of zero. Only the positive side is borderline."""
    assert d4.decide(_conditions(S5=False),
                     _stats(mean_delta=-0.001))["screening_verdict"] == "SCREENING_FAIL"


def test_a_significantly_negative_mean_is_reported_and_is_never_a_pass():
    decision = d4.decide(_conditions(), _stats(delta_ci_high=-0.004))
    assert decision["screening_verdict"] == "SCREENING_FAIL"
    assert decision["inverse_effect"] == ["mean_delta"]
    assert decision["long_data_purchase_candidate"] == "NO"


def test_the_sign_guard_catches_a_baseline_that_works_in_reverse():
    """S4 is the hole the four WEAK priors could open: a B0 with a large negative IC would make
    delta look large while the analog was the weaker of the two."""
    sample = make_sample(dates=40, per_date=150)
    series = d4.per_date_ic(sample, d4.group_by_date(sample), min_valid_queries=100)
    boot = d4.bootstrap(series, block_length=20, replicates=200, seed=20260920, horizon=HORIZON)
    stats = stats_for(sample, series, boot)
    stats.update(mean_ic_a=0.004, mean_ic_b0=-0.030, mean_delta=0.034)
    conditions = d4.screening_gate(stats, RULES)
    assert conditions["S5"].holds and not conditions["S4"].holds
    assert d4.decide(conditions, {**_stats(mean_delta=0.034)})["screening_verdict"] \
        == "SCREENING_FAIL"


# -- disclosure ---------------------------------------------------------------------------------------

def test_the_variance_disclosure_says_when_pairing_failed():
    sample = make_sample(dates=50, per_date=120)
    series = d4.per_date_ic(sample, d4.group_by_date(sample), min_valid_queries=100)
    boot = d4.bootstrap(series, block_length=20, replicates=300, seed=20260920, horizon=HORIZON)
    block = d4.variance_disclosure(series, boot, RULES)
    for key in ("realized_rho_ic_a_ic_b0", "sd_ic_a", "sd_ic_b0", "sd_delta",
                "se_realized_bootstrap", "se_predicted_at_realized_rho"):
        assert key in block
    sd_delta, sd_a = block["sd_delta"], block["sd_ic_a"]
    assert block["pairing_reduced_variance"] is (sd_delta <= sd_a)
    assert ("did not reduce" in block["pairing_statement"]) is (sd_delta > sd_a)


def test_quintile_profile_reports_both_readings_of_s8():
    sample = make_sample(dates=20, per_date=150, signal_strength=0.5)
    series = d4.per_date_ic(sample, d4.group_by_date(sample), min_valid_queries=100)
    profile = d4.quintile_profile(sample, series, sample.analog)
    assert len(profile["within_date_baskets"]) == 5
    assert len(profile["pooled_baskets"]) == 5
    assert profile["within_date_spread"] > 0          # the planted signal is monotone
    assert profile["pooled_spread"] is not None


def test_secondary_diagnostics_never_return_a_verdict():
    sample = make_sample(dates=20, per_date=150)
    series = d4.per_date_ic(sample, d4.group_by_date(sample), min_valid_queries=100)
    out = d4.secondary_diagnostics(sample, series, RULES, rank_matrix=None, rv_20=None,
                                   mfe=None, mae=None, market_vol=None)
    body = json.dumps(out)
    for word in ("SCREENING_PASS", "SCREENING_FAIL", "SCREENING_BORDERLINE",
                 "LONG_DATA_PURCHASE"):
        assert word not in body
    assert "S-7" in out and set(d4.DEFERRED_SECONDARY) <= set(out["deferred"])


def test_the_orthogonalized_residual_collapses_when_a_is_its_own_coordinates():
    """B3's purpose: if A(q) is a re-expression of the coordinates, the residual carries nothing.

    The regression target is ``rank(A)``, as declared, so even an exact linear combination does
    not reach R^2 = 1: ranking is monotone but not linear. What must hold is that almost all of
    the variation is explained and the leftover correlates with nothing, and that an A built from
    something else entirely is explained far less well.
    """
    sample = make_sample(dates=12, per_date=150, signal_strength=0.7)
    series = d4.per_date_ic(sample, d4.group_by_date(sample), min_valid_queries=100)
    rng = np.random.default_rng(3)
    coordinates = rng.random((len(sample), len(FEATURE_NAMES)))
    linear = coordinates @ np.arange(1.0, len(FEATURE_NAMES) + 1)

    built_from_them = d4.orthogonalized_residual_ic(
        d4.JoinedSample(**{**sample.__dict__, "analog": linear}), series, coordinates)
    assert built_from_them["mean_r_squared_of_A_on_coordinates"] > 0.95
    assert abs(built_from_them["mean_residual_ic"]) < 0.05

    independent = d4.orthogonalized_residual_ic(sample, series, coordinates)
    assert independent["mean_r_squared_of_A_on_coordinates"] < 0.2
    assert (built_from_them["mean_r_squared_of_A_on_coordinates"]
            > independent["mean_r_squared_of_A_on_coordinates"])


def test_the_residual_is_unchanged_by_standardizing_the_regressors():
    """The declaration says "standardized coordinates". With an intercept in the design, OLS
    residuals are invariant to any invertible linear rescaling, so ranks and z-scores give the
    same answer - which is why the code regresses on the ranks it already has."""
    sample = make_sample(dates=10, per_date=150, signal_strength=0.4)
    series = d4.per_date_ic(sample, d4.group_by_date(sample), min_valid_queries=100)
    rng = np.random.default_rng(11)
    coordinates = rng.random((len(sample), len(FEATURE_NAMES)))
    standardized = (coordinates - coordinates.mean(axis=0)) / coordinates.std(axis=0)
    plain = d4.orthogonalized_residual_ic(sample, series, coordinates)
    scaled = d4.orthogonalized_residual_ic(sample, series, standardized)
    assert plain["mean_residual_ic"] == pytest.approx(scaled["mean_residual_ic"], abs=1e-9)
