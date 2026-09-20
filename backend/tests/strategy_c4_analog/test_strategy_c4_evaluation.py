"""The evaluation and decision path, exercised end to end on synthetic queries."""

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pytest

from app.backtest.strategy_c4_analog import features as c4_features
from app.backtest.strategy_c4_analog.run import Rows, SearchResult, decide, evaluate_families
from app.backtest.strategy_c4_analog.rules import load_rules

DATES = 120
PER_DATE = 30


@dataclass
class FakePanel:
    tickers: tuple


@dataclass
class FakeInputs:
    sessions: tuple
    panel: FakePanel


def _build(signal_strength: float, seed: int):
    rng = np.random.default_rng(seed)
    rules = load_rules()
    first = rules.first_query_idx
    sessions = tuple(date(2025, 1, 6) + timedelta(days=i) for i in range(first + DATES + 5))
    tickers = tuple(f"T{i:04d}" for i in range(PER_DATE * 4))
    query_i = np.repeat(np.arange(first, first + DATES), PER_DATE)
    query_j = np.tile(np.arange(PER_DATE), DATES)
    n = query_i.size
    forecast = rng.normal(0, 1, n)
    noise = rng.normal(0, 1, n)
    excess5 = signal_strength * forecast + noise
    outcomes = {"excess_5": excess5, "excess_10": signal_strength * forecast + rng.normal(0, 1, n),
                "mfe_5": np.abs(rng.normal(0.05, 0.02, n)), "mae_5": -np.abs(rng.normal(0.04, 0.02, n)),
                "mfe_10": np.abs(rng.normal(0.08, 0.03, n)), "mae_10": -np.abs(rng.normal(0.06, 0.03, n))}
    results = {}
    for family in ("F0", "F1", "F2", "F3"):
        strength = 1.0 if family == "F3" else 0.2
        values = strength * forecast + (1 - strength) * rng.normal(0, 1, n)
        results[family] = SearchResult({"median_excess_5": values}, np.ones(n, dtype=bool),
                                       np.full((n, 3), 5, dtype=np.int32),
                                       np.full((n, 3), 7, dtype=np.int32), np.full(n, 50))
    rows = Rows(np.array([]), np.array([]), query_i, query_j, np.zeros((1, 1), bool),
                np.zeros((1, 1), bool))
    shape = (len(sessions), len(tickers))
    raw = c4_features.RawFeatures(
        {"event_present": rng.integers(0, 2, shape).astype(float),
         "revenue_yoy": rng.normal(0.1, 0.3, shape),
         "return_5d": rng.normal(0.08, 0.05, shape)},
        np.zeros(shape, dtype=np.int8), np.zeros(shape, dtype=bool))
    inputs = FakeInputs(sessions, FakePanel(tickers))
    return rules, results, rows, inputs, outcomes, raw


def test_a_strong_planted_signal_is_recovered_and_passes_every_condition():
    rules, results, rows, inputs, outcomes, raw = _build(signal_strength=0.8, seed=11)
    stats = evaluate_families(results, rows, inputs, rules, outcomes, raw, log=lambda _m: None)
    assert stats["ic"]["F3"]["point"] > 0.3
    assert stats["ic"]["F3"]["ci_low"] > 0
    assert stats["q5_minus_q1_excess_5"]["point"] > 0
    assert stats["ic_difference_primary_minus_reference"]["point"] > 0
    verdict = decide(stats, {"pass": True}, {"pass": True}, rules)
    assert verdict["verdict"] == "PASS", verdict["failed"]


def test_a_null_signal_fails_the_gate():
    rules, results, rows, inputs, outcomes, raw = _build(signal_strength=0.0, seed=12)
    stats = evaluate_families(results, rows, inputs, rules, outcomes, raw, log=lambda _m: None)
    assert abs(stats["ic"]["F3"]["point"]) < 0.1
    verdict = decide(stats, {"pass": True}, {"pass": True}, rules)
    assert verdict["verdict"] == "FAIL"


def test_a_failed_engineering_gate_blocks_pass_even_with_a_signal():
    rules, results, rows, inputs, outcomes, raw = _build(signal_strength=0.8, seed=13)
    stats = evaluate_families(results, rows, inputs, rules, outcomes, raw, log=lambda _m: None)
    assert decide(stats, {"pass": False}, {"pass": True}, rules)["verdict"] == "FAIL"


def test_a_failed_coverage_gate_yields_inconclusive():
    rules, results, rows, inputs, outcomes, raw = _build(signal_strength=0.8, seed=14)
    stats = evaluate_families(results, rows, inputs, rules, outcomes, raw, log=lambda _m: None)
    assert decide(stats, {"pass": True}, {"pass": False}, rules)["verdict"] == "INCONCLUSIVE"


def test_time_blocks_and_leave_out_are_reported():
    rules, results, rows, inputs, outcomes, raw = _build(signal_strength=0.5, seed=15)
    stats = evaluate_families(results, rows, inputs, rules, outcomes, raw, log=lambda _m: None)
    assert len(stats["time_blocks"]) == rules.time_blocks
    assert set(stats["leave_out"]) == {"drop_top_5_tickers", "drop_top_10_dates"}
    assert stats["concentration"]["query_ticker"]["single_share"] == pytest.approx(1 / PER_DATE)
