"""Unit, edge-case and determinism tests for the E1-H5 confirmation modules.

All synthetic. Nothing here touches the workspace, the frozen snapshot, the live minute tape or
the network.
"""

from datetime import date

import numpy as np
import pytest

from app.backtest.strategy_e1_h5_confirm import gate, matching
from app.backtest.strategy_e1_h5_confirm.config import (
    DECLARED_RULES_CHECKSUM, RulesChanged, canonical_checksum, load_rules,
)
from app.backtest.strategy_e1_h5_confirm.run import cost_stress, h5_mask
from app.backtest.strategy_e1_premarket import evaluate as e1_evaluate
from app.backtest.strategy_e1_premarket.config import load_rules as load_e1_rules


# -- declaration -----------------------------------------------------------------------------

def test_declared_rules_still_hash_to_the_frozen_checksum():
    rules = load_rules()
    assert rules.checksum == DECLARED_RULES_CHECKSUM
    assert canonical_checksum(rules.raw) == DECLARED_RULES_CHECKSUM


def test_load_rules_refuses_an_edited_declaration(tmp_path):
    import json
    edited = json.loads(json.dumps(load_rules().raw))
    edited["gate"]["pass_requires_all"]["confirmation_rows"] = 10
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(RulesChanged):
        load_rules(path)


def test_h5_statement_matches_the_e1_declaration_verbatim():
    """The whole point of a confirmation: the hypothesis must not have drifted."""
    confirm = load_rules()
    e1 = load_e1_rules()
    assert confirm.raw["hypothesis"]["statement"] == e1.hypotheses["H5"]["rule"]
    assert confirm.e1_rules_checksum == e1.checksum


def test_h5_mask_is_the_e1_mask_not_a_reimplementation():
    features = {
        "premarket_gap": np.array([0.02, -0.01, 0.03]),
        "premarket_rvol": np.array([5.0, 5.0, 1.0]),
        "position_in_premarket_range": np.array([0.9, 0.9, 0.9]),
        "return_0900_0925": np.array([0.01, 0.01, 0.01]),
        "return_last30m": np.array([0.01, 0.01, 0.01]),
        "relative_strength_vs_spy": np.array([0.01, 0.01, 0.01]),
        "premarket_dollar_volume": np.array([2e6, 2e6, 2e6]),
    }

    class _Rows:
        pass

    rows = _Rows()
    rows.features = features
    assert h5_mask(rows).tolist() == e1_evaluate.mask("H5", features).tolist()


# -- cell construction -------------------------------------------------------------------------

def _features(n=6, **overrides):
    base = {
        "close_price": np.array([4.0, 10.0, 10.0, 50.0, 150.0, 1.0])[:n],
        "previous_day_dollar_volume": np.full(n, 1e7),
        "premarket_dollar_volume": np.full(n, 5e5),
        "premarket_gap": np.full(n, 0.02),
    }
    base.update({k: v[:n] for k, v in overrides.items()})
    return base


def test_cells_group_equal_rows_and_separate_unequal_ones():
    """Matching edges are deliberately coarse (3 / 20 / 100), not the reporting edges."""
    edges = load_rules().match_edges
    cells, valid = matching.cell_ids(_features(), edges)
    # $4 and $10 are both inside [3, 20), so they must share a cell
    assert cells[0] == cells[1] == cells[2]
    # $50 and $150 fall in the next two price buckets and must not
    assert len({cells[0], cells[3], cells[4]}) == 3
    # a $1 stock is below the lowest declared price edge and has no cell
    assert cells[5] == -1 and not valid[5]


def test_a_row_outside_any_declared_bucket_is_excluded_not_clamped():
    edges = load_rules().match_edges
    features = _features(premarket_dollar_volume=np.array([1.0] * 6))
    cells, valid = matching.cell_ids(features, edges)
    assert (cells == -1).all() and not valid.any()


# -- the matching estimators --------------------------------------------------------------------

def _grid(n_sessions=6, per_session=8, effect=0.0, seed=0):
    """A synthetic block: half the rows are H5, with a planted additive effect."""
    rng = np.random.default_rng(seed)
    sessions, symbols, values, is_h5 = [], [], [], []
    for s in range(n_sessions):
        session_shock = rng.normal(0, 0.01)
        for i in range(per_session):
            sessions.append(date(2026, 5, 1 + s))
            symbols.append(f"S{i}")
            h5 = i % 2 == 0
            is_h5.append(h5)
            values.append(session_shock + rng.normal(0, 0.002) + (effect if h5 else 0.0))
    return (np.array(values), np.array(sessions, dtype=object),
            np.array(symbols, dtype=object), np.array(is_h5))


def test_session_demeaning_removes_a_common_market_shock():
    """Every row of a session shifted by the same amount must not create a lift."""
    values, sessions, symbols, is_h5 = _grid(effect=0.0, seed=1)
    cells = np.zeros(values.size, dtype=np.int64)
    result = matching.session_demeaned(values, sessions, symbols, cells, is_h5)
    assert abs(result.mean_lift) < 5e-4
    shifted = values + np.array([0.05 if s.day % 2 else -0.05 for s in sessions])
    shocked = matching.session_demeaned(shifted, sessions, symbols, cells, is_h5)
    assert result.mean_lift == pytest.approx(shocked.mean_lift, abs=1e-12)


def test_matching_recovers_a_planted_effect():
    values, sessions, symbols, is_h5 = _grid(effect=0.004, seed=2)
    cells = np.zeros(values.size, dtype=np.int64)
    result = matching.session_demeaned(values, sessions, symbols, cells, is_h5)
    assert result.mean_lift == pytest.approx(0.004, abs=0.001)
    assert result.matched_rows == int(is_h5.sum())


def test_matching_removes_an_effect_that_is_really_a_cell_difference():
    """If H5 rows only look good because they sit in a richer cell, matching must erase it."""
    values = np.array([0.02, 0.02, 0.0, 0.0])
    sessions = np.array([date(2026, 5, 1)] * 4, dtype=object)
    symbols = np.array(["A", "B", "C", "D"], dtype=object)
    cells = np.array([1, 1, 0, 0])
    is_h5 = np.array([True, False, True, False])
    result = matching.session_demeaned(values, sessions, symbols, cells, is_h5)
    assert result.mean_lift == pytest.approx(0.0, abs=1e-12)


def test_unmatched_rows_are_dropped_and_counted():
    values = np.array([0.01, 0.0, 0.02])
    sessions = np.array([date(2026, 5, 1)] * 3, dtype=object)
    symbols = np.array(["A", "B", "C"], dtype=object)
    cells = np.array([0, 0, 7])           # cell 7 holds no control
    is_h5 = np.array([True, False, True])
    result = matching.session_demeaned(values, sessions, symbols, cells, is_h5)
    assert result.h5_rows == 2
    assert result.matched_rows == 1
    assert result.unmatched_rows == 1
    assert result.match_rate == pytest.approx(0.5)


def test_same_session_estimator_refuses_a_cross_day_control():
    values = np.array([0.01, 0.0])
    sessions = np.array([date(2026, 5, 1), date(2026, 5, 2)], dtype=object)
    symbols = np.array(["A", "B"], dtype=object)
    cells = np.array([0, 0])
    is_h5 = np.array([True, False])
    assert matching.same_session(values, sessions, symbols, cells, is_h5).matched_rows == 0
    assert matching.session_demeaned(values, sessions, symbols, cells, is_h5).matched_rows == 1


def test_thin_price_buckets_are_merged_by_the_declared_rule():
    values, sessions, symbols, is_h5 = _grid(n_sessions=10, per_session=20, seed=3)
    cells = np.zeros(values.size, dtype=np.int64)
    price = np.where(np.arange(values.size) % 20 < 2, 4.0, 150.0)
    table = matching.by_bucket(values, sessions, symbols, cells, is_h5, price,
                               (3.0, 5.0, 10.0, None), minimum_rows=30)
    assert all(row.get("h5_rows", 0) == 0 or row["h5_rows"] >= 30 or ".." in row["bucket"]
               for row in table)
    assert any(".." in row["bucket"] for row in table)


# -- cost stress -------------------------------------------------------------------------------

def test_break_even_cost_is_the_gross_lift_in_basis_points():
    rules = load_rules()
    out = cost_stress(0.00165, rules)
    assert out["break_even_cost_bp"] == pytest.approx(16.5)
    assert out["net_by_assumed_cost"]["0bp"] == pytest.approx(0.00165)
    assert out["net_by_assumed_cost"]["20bp"] == pytest.approx(0.00165 - 0.0020)
    assert out["net_positive_at_declared_cost"] is True


def test_cost_stress_turns_a_thin_edge_negative():
    out = cost_stress(0.0005, load_rules())
    assert out["break_even_cost_bp"] == pytest.approx(5.0)
    assert out["net_positive_at_declared_cost"] is False


# -- gate ---------------------------------------------------------------------------------------

def _block(**overrides):
    block = {
        "matched_primary": {"matched_rows": 3000, "mean_lift": 0.002, "median_lift": 0.001,
                            "win_rate_lift": 0.03, "downside_ratio": 1.1},
        "extreme_removal": [{"removal": "drop_top_1pct", "mean_lift": 0.001}],
        "concentration": {"top_5_share": 0.2},
        "bootstrap": {"session_cluster": {"ci": [0.0005, 0.004]}},
        "cost_stress": {"net_at_declared_cost": 0.001, "net_positive_at_declared_cost": True},
        "monthly": [{"month": f"2026-0{i}", "h5_rows": 100, "mean_lift": 0.002} for i in (4, 5, 6)],
        "by_price": [{"bucket": "a", "h5_rows": 100, "mean_lift": 0.002},
                     {"bucket": "b", "h5_rows": 100, "mean_lift": 0.001}],
        "by_liquidity": [{"bucket": "a", "h5_rows": 100, "mean_lift": 0.002},
                         {"bucket": "b", "h5_rows": 100, "mean_lift": 0.001}],
    }
    block.update(overrides)
    return block


def test_gate_without_a_holdout_cannot_promote_even_when_everything_passes():
    result = gate.evaluate(_block(), load_rules(), holdout_available=False)
    assert result["failed"] == []
    assert result["verdict"] == gate.HOLDOUT_SHORT
    assert result["promotes_to_strategy_e"] is False


def test_gate_passes_only_with_a_holdout():
    result = gate.evaluate(_block(), load_rules(), holdout_available=True)
    assert result["verdict"] == gate.PASS and result["promotes_to_strategy_e"] is True


def test_short_sample_is_inconclusive_not_fail():
    block = _block(matched_primary={"matched_rows": 80, "mean_lift": 0.002, "median_lift": 0.001,
                                    "win_rate_lift": 0.03, "downside_ratio": 1.1})
    result = gate.evaluate(block, load_rules(), holdout_available=False)
    assert result["verdict"] == gate.INCONCLUSIVE
    assert "confirmation_rows" in result["failed"]


def test_a_dead_edge_is_fail_even_with_a_short_sample():
    block = _block(matched_primary={"matched_rows": 80, "mean_lift": -0.001, "median_lift": -0.001,
                                    "win_rate_lift": -0.01, "downside_ratio": 1.1})
    assert gate.evaluate(block, load_rules(),
                         holdout_available=False)["verdict"] == gate.FAIL


def test_neutralization_failure_is_fail_not_inconclusive():
    block = _block(by_price=[{"bucket": "a", "h5_rows": 100, "mean_lift": -0.002},
                             {"bucket": "b", "h5_rows": 100, "mean_lift": -0.001}])
    result = gate.evaluate(block, load_rules(), holdout_available=True)
    assert "price_neutralized_positive" in result["failed"]
    assert result["verdict"] == gate.FAIL


def test_cost_failure_is_caught():
    block = _block(cost_stress={"net_at_declared_cost": -0.0005,
                                "net_positive_at_declared_cost": False})
    result = gate.evaluate(block, load_rules(), holdout_available=True)
    assert "net_positive_at_declared_cost" in result["failed"]


def test_thin_buckets_do_not_vote_on_neutralization():
    """A bucket below the 30-row minimum must not decide the majority."""
    block = _block(by_price=[{"bucket": "a", "h5_rows": 100, "mean_lift": 0.002},
                             {"bucket": "b", "h5_rows": 5, "mean_lift": -0.10},
                             {"bucket": "c", "h5_rows": 5, "mean_lift": -0.10}])
    result = gate.evaluate(block, load_rules(), holdout_available=True)
    assert "price_neutralized_positive" not in result["failed"]


def test_session_mean_ignores_missing_values():
    """A NaN in one row must not wipe out its whole session through the demeaning."""
    values = np.array([0.01, 0.02, np.nan, 0.04])
    sessions = np.array([date(2026, 5, 1)] * 2 + [date(2026, 5, 2)] * 2, dtype=object)
    means = matching._session_means(values, sessions)
    assert means[0] == pytest.approx(0.015)
    assert means[2] == pytest.approx(0.04)
    assert np.isfinite(means).all()


def test_subset_analysis_demeans_against_the_full_universe():
    """A bucket analysis must not re-baseline itself against the bucket."""
    values, sessions, symbols, is_h5 = _grid(n_sessions=4, per_session=10, effect=0.003, seed=9)
    cells = np.zeros(values.size, dtype=np.int64)
    subset = np.arange(values.size) % 10 < 6
    with_subset = matching.session_demeaned(values, sessions, symbols, cells, is_h5,
                                            subset=subset)
    # every matched row lies inside the subset, and the estimate is still finite
    assert with_subset.matched_rows == int((is_h5 & subset).sum())
    assert np.isfinite(with_subset.mean_lift)
    # masking the values instead (the old, broken way) would have produced no matches at all
    assert with_subset.matched_rows > 0


def test_bucket_table_reports_finite_lifts_for_populated_buckets():
    """Price must vary independently of H5, or a bucket legitimately has no control."""
    values, sessions, symbols, is_h5 = _grid(n_sessions=12, per_session=20, effect=0.002, seed=11)
    cells = np.zeros(values.size, dtype=np.int64)
    within = np.arange(values.size) % 20
    price = np.where(within < 10, 10.0, 150.0)     # H5 is every other row, so both halves mix
    table = matching.by_bucket(values, sessions, symbols, cells, is_h5, price,
                               (3.0, 100.0, None), minimum_rows=30)
    populated = [r for r in table if r.get("h5_rows", 0) >= 30]
    assert len(populated) == 2, "both price buckets should be populated and hold controls"
    for row in populated:
        assert row["matched_rows"] > 0
        assert np.isfinite(row["mean_lift"])
        assert row["mean_lift"] == pytest.approx(0.002, abs=0.0015)


def test_bucket_with_no_control_reports_no_match_rather_than_a_number():
    """If every row of a bucket is H5 there is nothing to compare it with, and that must show."""
    values, sessions, symbols, is_h5 = _grid(n_sessions=6, per_session=10, effect=0.002, seed=13)
    cells = np.zeros(values.size, dtype=np.int64)
    price = np.where(is_h5, 10.0, 150.0)           # perfectly aligned on purpose
    table = matching.by_bucket(values, sessions, symbols, cells, is_h5, price,
                               (3.0, 100.0, None), minimum_rows=30)
    low = [r for r in table if r["bucket"].startswith("[3,")][0]
    assert low["h5_rows"] > 0
    assert low["matched_rows"] == 0
    assert low["match_rate"] == 0.0
