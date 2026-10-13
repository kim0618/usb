import json

import numpy as np

from app.crypto.research.c1_strength import (
    CONTRACT,
    horizon_perf,
    order_stats,
    quantiles,
    run,
    spearman,
)


def test_spearman_handles_monotone_and_reverse_relationships():
    x = np.arange(10, dtype=float)
    assert np.isclose(spearman(x, x ** 2), 1)
    assert np.isclose(spearman(x, -x), -1)


def test_quantiles_are_deterministic_and_near_equal_count():
    q = quantiles(np.ones(11))
    assert q.tolist() == [1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4]
    assert max(np.bincount(q)[1:]) - min(np.bincount(q)[1:]) <= 1


def test_order_stats_counts_only_strict_favorable_steps():
    out = order_stats([1.0, 3.0, 2.0, 4.0])
    assert out["quantile_mean_rho"] > 0
    assert out["favorable_adjacent_steps"] == 2


def test_horizon_perf_reports_win_rate_and_profit_factor():
    out = horizon_perf(np.array([-2.0, 1.0, 5.0]), np.array([-1.0, 2.0, 6.0]))
    assert np.isclose(out["win_rate"], 2 / 3)
    assert out["pf"] == 3.0


def test_frozen_research_population_and_verdict_are_preserved():
    out = run(write=False)
    assert out["trades"] == 1603
    assert out["regime_counts"] == {"R1": 163, "R2": 383, "R3": 182, "R4": 562, "R5": 313}
    assert out["top5_removed"] == 81
    assert out["repeatable_base_features"] == []
    assert out["direction_core_base_features"] == ["premium_extreme_bp"]
    assert out["verdict"] == "NO_STRENGTH_SIGNAL"
    assert out["three_level_score_proposed"] is False
    assert out["operational_application"] == "NOT_AUTHORIZED"


def test_research_is_deterministic_and_bound_to_frozen_contract():
    first = run(write=False)
    second = run(write=False)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["contract_sha256"]
    assert CONTRACT.is_file()
