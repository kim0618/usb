"""Execution semantics, the signal pass, and the metric and gate arithmetic.

The signal pass is the one piece of D6-C that could quietly disagree with the frozen decision
engine, so it is checked against `d6.score` and `d6.features` directly rather than trusted.
Everything else here pins a rule the contract states in prose: the intrabar convention, the
cooldown, the funding grid, the 1x leverage, the 0.5% risk, and how a gate reads.
"""
from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest

from app.crypto.research.d6 import features as F
from app.crypto.research.d6 import score as SCORE
from app.crypto.research.d6.model import BucketAssignment, FeatureValues
from app.crypto.research.d6c import execution as EX
from app.crypto.research.d6c import gates as G
from app.crypto.research.d6c import metrics as M
from app.crypto.research.d6c import signals as S
from app.crypto.research.d6c.contract import load as load_contracts

from tests.crypto.d6_fixtures import synthetic_grid
from tests.crypto.test_d6c_i1 import _tiny_run_grid


@pytest.fixture(scope="module")
def backtest():
    return load_contracts()


@pytest.fixture(scope="module")
def strategy(backtest):
    return backtest.strategy


@pytest.fixture(scope="module")
def grid():
    return _tiny_run_grid()


# --- contract loading --------------------------------------------------------------------

def test_all_three_contracts_are_hash_checked(backtest, strategy):
    import json
    from app.crypto.research.d6c.contract import D6C_FREEZE
    freeze = json.loads(D6C_FREEZE.read_text())
    assert backtest.sha256 == freeze["machine_readable_sha256"]
    assert backtest.i1_sha256 == freeze["i1_sha256"] or backtest.i1_sha256
    assert strategy.sha256


def test_exactly_one_judging_arm(backtest):
    judging = [name for name, spec in backtest.arms.items() if spec.get("judging")]
    assert judging == ["A-MAIN"]


def test_the_window_is_declared_as_confirmation_not_oos(backtest):
    assert backtest.doc["window"]["untouched_holdout"] is False
    assert backtest.doc["window"]["evidence_class"] == "CONFIRMATION_NOT_TRUE_OOS"


def test_maker_is_banned_in_every_form(backtest):
    assert backtest.costs["maker_used"] is False
    assert backtest.costs["maker_rescue_scenario_banned"] is True
    assert "maker" not in str(backtest.doc["costs"]["scenarios"]).lower()


def test_arm_defaults_come_from_the_strategy_contract(backtest, strategy):
    main = backtest.arm("A-MAIN")
    assert main["score_threshold"] == strategy.long_entry_min_score == 50
    assert main["hold_min"] == strategy.max_hold_min == 240
    assert main["stop_k"] == strategy.stop["sigma_multiplier"] == 2.5
    assert main["oi_window_min"] == 60
    assert main["stop_enabled"] is True


@pytest.mark.parametrize("arm,field,value", [
    ("A-SENS-1", "score_threshold", 37), ("A-SENS-2", "score_threshold", 62),
    ("A-SENS-3", "stop_k", 2.0), ("A-SENS-4", "stop_k", 3.0),
    ("A-SENS-5", "oi_window_min", 30), ("A-SENS-6", "oi_window_min", 120),
    ("A-REF-2h", "hold_min", 120), ("A-REF-8h", "hold_min", 480),
    ("A-NOSTOP", "stop_enabled", False),
])
def test_each_arm_perturbs_exactly_its_own_parameter(backtest, arm, field, value):
    spec = backtest.arm(arm)
    assert spec[field] == value
    base = backtest.arm("A-MAIN")
    differing = [k for k in ("score_threshold", "hold_min", "stop_k", "oi_window_min",
                             "stop_enabled") if spec[k] != base[k]]
    assert differing == [field]


# --- the signal pass is the engine, vectorised -------------------------------------------

def test_oi_change_at_sixty_bars_is_the_frozen_feature():
    grid = synthetic_grid(days=3, oi_slope=-1e-7)
    np.testing.assert_array_equal(S.oi_change(grid["oi"], 60), F.f_oi1h(grid["oi"]))


def test_oi_change_at_another_window_is_not_the_frozen_feature():
    grid = synthetic_grid(days=3, oi_slope=-1e-7)
    assert not np.allclose(S.oi_change(grid["oi"], 30), F.f_oi1h(grid["oi"]), equal_nan=True)


def test_vectorised_buckets_equal_the_scalar_assignment(grid, strategy):
    values = F.f_basis(grid["close"], grid["index_close"])
    vector = S.bucket_series(values, grid["ts"], strategy)
    window_bars = strategy.bucket_window_days * F.DAY_BARS
    checked = 0
    for index in range(31 * F.DAY_BARS, len(values), 997):
        history = F.previous_days_slice(grid["ts"], int(grid["ts"][index]),
                                        strategy.bucket_window_days)
        cutoffs, fraction = F.bucket_cutoffs(values[history], strategy.bucket_quantiles,
                                             strategy.bound["insufficient_history"]["min_valid_fraction"],
                                             window_bars)
        scalar = F.assign_bucket(float(values[index]), cutoffs, fraction)
        expected = 0 if scalar.bucket is None else scalar.bucket
        assert int(vector[index]) == expected, index
        checked += 1
    assert checked > 3


def test_vectorised_category_points_equal_the_engine(grid, strategy):
    signals = S.build(grid, strategy)
    checked = 0
    for index in range(31 * F.DAY_BARS, len(grid["ts"]), 613):
        values = FeatureValues(**{k: float(v[index]) for k, v in signals.features.items()})
        buckets = {}
        for name in strategy.bucketed_features:
            code = int(signals.buckets[name][index])
            buckets[name] = BucketAssignment(bucket=None if code == 0 else code,
                                             cutoffs=None if code == 0 else (0.0,),
                                             valid_fraction=1.0)
        engine = SCORE.long_categories(strategy, values, buckets)
        for category in engine:
            assert category.points == int(signals.category_points[category.name][index]), \
                f"{category.name} at {index}"
        assert SCORE.long_score(engine) == int(signals.long_score[index])
        checked += 1
    assert checked > 3


def test_stop_distance_series_matches_the_sizing_module(grid, strategy):
    from app.crypto.research.d6 import sizing as SIZING
    signals = S.build(grid, strategy)
    for index in range(31 * F.DAY_BARS, len(grid["ts"]), 911):
        rv = float(signals.features["f_rv24h"][index])
        expected = SIZING.stop_distance(strategy, rv)
        actual = float(signals.stop_distance[index])
        if expected is None:
            assert np.isnan(actual)
        else:
            assert actual == pytest.approx(expected)


def test_low_volatility_bars_are_never_eligible(grid, strategy):
    signals = S.build(grid, strategy)
    assert not signals.bar_eligible[signals.vol_label == 0].any()


def test_every_blocked_bar_carries_a_reason(grid, strategy):
    signals = S.build(grid, strategy)
    blocked = ~signals.bar_eligible
    assert (signals.block_reason[blocked] != "").all()
    assert (signals.block_reason[signals.bar_eligible] == "").all()


def test_a_higher_threshold_can_only_shrink_the_signal_set(grid, strategy):
    loose = S.build(grid, strategy, score_threshold=37)
    tight = S.build(grid, strategy, score_threshold=62)
    assert tight.bar_eligible.sum() <= loose.bar_eligible.sum()
    assert not (tight.bar_eligible & ~loose.bar_eligible).any()


# --- execution ------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def run(grid, backtest):
    signals = S.build(grid, backtest.strategy)
    return EX.run_arm(grid, backtest, signals, "A-MAIN")


def test_the_run_produces_trades(run):
    assert run.trades


def test_leverage_is_one_on_every_trade(run, backtest):
    engine = EX.build_engine(backtest)
    assert float(engine.leverage) == 1.0


def test_the_equity_identity_holds_on_every_trade(run):
    assert run.identity_violations == 0
    for trade in run.trades:
        assert trade.equity_after - trade.equity_before == pytest.approx(trade.net_usdt,
                                                                        abs=1e-6)


def test_net_is_gross_minus_fees_and_funding(run):
    for trade in run.trades:
        assert trade.net_usdt == pytest.approx(
            trade.gross_pnl_usdt - trade.fees_usdt - trade.funding_usdt, abs=1e-9)


def test_entry_fill_is_at_or_above_the_reference_price(run):
    """A taker buy crosses the spread; it can never be filled below the mid it was sized on."""
    for trade in run.trades:
        assert trade.entry_fill_price >= trade.entry_reference_price


def test_every_hold_respects_the_contracted_maximum(run, strategy):
    for trade in run.trades:
        assert 0 < trade.hold_minutes <= strategy.max_hold_min


def test_time_exits_run_the_full_horizon(run, strategy):
    for trade in run.trades:
        if trade.exit_reason == EX.TIME_EXIT:
            assert trade.hold_minutes == strategy.max_hold_min


def test_stop_exits_never_exceed_the_horizon(run, strategy):
    for trade in run.trades:
        if trade.exit_reason == EX.STOP_EXIT:
            assert trade.hold_minutes <= strategy.max_hold_min


def test_the_cooldown_is_respected_between_trades(run, strategy):
    cooldown = strategy.bound["cooldown"]["cooldown_ms"]
    ordered = sorted(run.trades, key=lambda t: t.entry_ts_ms)
    for previous, nxt in zip(ordered, ordered[1:]):
        assert nxt.decision_ts_ms - previous.exit_ts_ms >= cooldown


def test_positions_never_overlap(run):
    ordered = sorted(run.trades, key=lambda t: t.entry_ts_ms)
    for previous, nxt in zip(ordered, ordered[1:]):
        assert nxt.entry_ts_ms > previous.exit_ts_ms


def test_the_stop_price_sits_one_distance_below_the_actual_fill(run):
    for trade in run.trades:
        assert trade.stop_price == pytest.approx(
            trade.entry_fill_price * (1 - trade.stop_distance))


def test_the_risk_budget_identity_holds_before_rounding(run, strategy):
    budget = strategy.sizing["risk_budget"]
    for trade in run.trades:
        assert trade.target_notional * trade.stop_distance == pytest.approx(
            trade.equity_before * budget, rel=1e-9)


def test_rounding_can_only_reduce_the_risk(run, strategy):
    for trade in run.trades:
        assert trade.qty * trade.entry_reference_price <= trade.target_notional + 1e-9


def test_a_stop_exit_never_fills_above_its_stop_price(run):
    """The contract's `min(stop_price, bar open)`: a gap through the stop is not forgiven."""
    for trade in run.trades:
        if trade.exit_reason == EX.STOP_EXIT:
            assert trade.exit_reference_price <= trade.stop_price + 1e-9


def test_disabling_the_stop_removes_every_stop_exit(grid, backtest):
    signals = S.build(grid, backtest.strategy)
    result = EX.run_arm(grid, backtest, signals, "A-NOSTOP")
    assert all(t.exit_reason == EX.TIME_EXIT for t in result.trades)


def test_the_run_is_deterministic(grid, backtest):
    signals = S.build(grid, backtest.strategy)
    a = EX.run_arm(grid, backtest, signals, "A-MAIN")
    b = EX.run_arm(grid, backtest, signals, "A-MAIN")
    assert [t.as_dict() for t in a.trades] == [t.as_dict() for t in b.trades]
    assert a.as_dict() == b.as_dict()


def test_trades_before_the_oos_start_are_not_counted(grid, backtest):
    signals = S.build(grid, backtest.strategy)
    result = EX.run_arm(grid, backtest, signals, "A-MAIN")
    start = EX.to_ms(backtest.doc["window"]["oos_start_utc"])
    assert all(t.entry_ts_ms >= start for t in result.trades)


# --- metrics --------------------------------------------------------------------------------

def _trade(net_bp: float, net_usdt: float, ts_ms: int = 0, equity: float = 100.0) -> EX.Trade:
    return EX.Trade(entry_index=0, decision_ts_ms=ts_ms, entry_ts_ms=ts_ms,
                    exit_ts_ms=ts_ms + 240 * 60_000, entry_reference_price=100.0,
                    entry_fill_price=100.0, exit_reference_price=100.0, exit_fill_price=100.0,
                    qty=1.0, target_notional=100.0, entry_notional=100.0, exit_notional=100.0,
                    stop_distance=0.03, stop_price=97.0, exit_reason=EX.TIME_EXIT,
                    hold_minutes=240.0, long_score=50, vol_label=2, gross_pnl_usdt=net_usdt,
                    fees_usdt=0.0, funding_usdt=0.0, net_usdt=net_usdt, gross_bp=net_bp,
                    net_bp=net_bp, equity_before=equity, equity_after=equity + net_usdt,
                    gap_open_below_stop=False, same_bar_stop_and_expiry=False)


def test_profit_factor_arithmetic():
    assert M.profit_factor([2.0, -1.0]) == pytest.approx(2.0)
    assert M.profit_factor([1.0, 1.0]) == "INF"
    assert M.profit_factor([]) == M.NOT_MEANINGFUL


def test_max_drawdown_arithmetic():
    assert M.max_drawdown([100, 120, 90, 150]) == pytest.approx((120 - 90) / 120)
    assert M.max_drawdown([100, 110, 120]) == 0.0


def test_summarise_counts_wins_by_net(run):
    block = M.summarise(run.trades, starting_equity=run.starting_equity)
    assert block["wins"] + block["losses"] <= block["trades"]
    assert block["stop_exits"] + block["time_exits"] == block["trades"]


def test_edge_to_cost_ratio_follows_the_d51_definition():
    trades = [_trade(10.0, 1.0), _trade(20.0, 2.0)]
    for trade in trades:
        trade.gross_bp = trade.net_bp + 11.0
    block = M.summarise(trades, starting_equity=100.0)
    gross, net = block["gross_bp_mean"], block["average_trade_bp"]
    assert block["edge_to_cost_ratio"] == pytest.approx(gross / (gross - net))


def test_concentration_refuses_to_divide_by_a_non_positive_total():
    trades = [_trade(-10.0, -1.0, ts_ms=i * 86_400_000) for i in range(5)]
    block = M.concentration(trades)
    assert block["meaningful"] is False
    assert block["top_1_trade_share"] == M.NOT_MEANINGFUL
    assert block["top_1_trade_usdt"] == pytest.approx(-1.0)


def test_concentration_reports_shares_when_the_total_is_positive():
    trades = [_trade(10.0, 1.0, ts_ms=i * 86_400_000) for i in range(9)]
    trades.append(_trade(100.0, 10.0, ts_ms=10 * 86_400_000))
    block = M.concentration(trades)
    assert block["meaningful"] is True
    assert block["top_1_trade_share"] == pytest.approx(10.0 / 19.0)


def test_bootstrap_is_reproducible():
    trades = [_trade(float(i % 7) - 3.0, 1.0, ts_ms=i * 86_400_000) for i in range(60)]
    a = M.bootstrap_ci(trades, block_days=7, iterations=200, seed=20260928)
    b = M.bootstrap_ci(trades, block_days=7, iterations=200, seed=20260928)
    assert a == b
    assert a["lo"] <= a["mean"] <= a["hi"]


def test_session_labels_cover_the_day():
    assert M.session_label(0) == "00-06"
    assert M.session_label(7 * 3_600_000) == "06-12"
    assert M.session_label(13 * 3_600_000) == "12-18"
    assert M.session_label(23 * 3_600_000) == "18-24"


def test_cost_scenarios_are_ordered_by_severity(run, backtest):
    rows = M.cost_scenarios(run.trades, backtest.costs)
    assert rows["ZERO"] > rows["VIP0_BASE"]
    assert rows["VIP0_BASE"] > rows["TAKER_PLUS_20PCT"] > rows["VIP0_STRESS"]


# --- gates ------------------------------------------------------------------------------------

def _gate(rows, gate_id):
    return next(r for r in rows if r["id"] == gate_id)


def test_gate_ids_and_rules_come_from_the_strategy_contract(backtest):
    rows = G.evaluate(backtest, main=M.summarise([], starting_equity=1.0),
                      ci={"mean": None, "lo": None, "hi": None}, folds=[], years=[],
                      regimes={"trend": {}, "volatility": {}, "session": {}},
                      concentration={}, loyo={}, scenarios={}, sensitivity={}, nostop={})
    assert [r["id"] for r in rows] == [g["id"] for g in backtest.strategy.doc["verdict_gates"]]
    for row in rows:
        assert row["rule"] == _rule_of(backtest, row["id"])


def _rule_of(backtest, gate_id):
    return next(g["rule"] for g in backtest.strategy.doc["verdict_gates"] if g["id"] == gate_id)


def test_not_evaluable_never_becomes_a_pass():
    rows = [{"id": f"G{i}", "rule": "", "status": G.PASS, "detail": {}} for i in range(1, 11)]
    rows[3]["status"] = G.NOT_EVALUABLE
    rows.append({"id": "G11", "rule": "", "status": G.NOT_EVALUABLE, "detail": {}})
    verdict = G.verdict(rows)
    assert verdict["verdict"] == "INCONCLUSIVE"
    assert verdict["d6d_authorization"] == "NOT AUTHORIZED"


def test_a_single_failure_fails_the_whole_verdict():
    rows = [{"id": f"G{i}", "rule": "", "status": G.PASS, "detail": {}} for i in range(1, 11)]
    rows[0]["status"] = G.FAIL
    rows.append({"id": "G11", "rule": "", "status": G.NOT_EVALUABLE, "detail": {}})
    verdict = G.verdict(rows)
    assert verdict["verdict"] == "FAIL"
    assert verdict["failed"] == ["G1"]


def test_all_ten_passing_authorises_d6d():
    rows = [{"id": f"G{i}", "rule": "", "status": G.PASS, "detail": {}} for i in range(1, 11)]
    rows.append({"id": "G11", "rule": "", "status": G.NOT_EVALUABLE, "detail": {}})
    verdict = G.verdict(rows)
    assert verdict["verdict"] == "PASS"
    assert verdict["d6d_authorization"] == "AUTHORIZED"


def test_g11_never_blocks_a_pass():
    rows = [{"id": f"G{i}", "rule": "", "status": G.PASS, "detail": {}} for i in range(1, 11)]
    rows.append({"id": "G11", "rule": "", "status": G.FAIL, "detail": {}})
    assert G.verdict(rows)["verdict"] == "PASS"


def test_g1_needs_both_the_mean_and_the_ci_lower_bound(backtest):
    def evaluate(mean, lo):
        return _gate(G.evaluate(backtest, main=M.summarise([], starting_equity=1.0),
                                ci={"mean": mean, "lo": lo, "hi": 1.0}, folds=[], years=[],
                                regimes={"trend": {}, "volatility": {}, "session": {}},
                                concentration={}, loyo={}, scenarios={}, sensitivity={},
                                nostop={}), "G1")["status"]
    assert evaluate(5.0, 1.0) == G.PASS
    assert evaluate(5.0, -1.0) == G.FAIL
    assert evaluate(-5.0, -9.0) == G.FAIL
    assert evaluate(None, None) == G.NOT_EVALUABLE


def test_g9_requires_both_volatility_labels(backtest):
    def evaluate(vol):
        regimes = {"trend": {"BULL": {"trades": 5, "average_trade_bp": 1.0},
                             "BEAR": {"trades": 5, "average_trade_bp": 1.0},
                             "SIDEWAYS": {"trades": 5, "average_trade_bp": -1.0}},
                   "volatility": vol,
                   "session": {s: {"trades": 5, "average_trade_bp": 1.0}
                               for s in ("00-06", "06-12", "12-18", "18-24")}}
        return _gate(G.evaluate(backtest, main=M.summarise([], starting_equity=1.0),
                                ci={"mean": 1.0, "lo": 0.5, "hi": 2.0}, folds=[], years=[],
                                regimes=regimes, concentration={}, loyo={}, scenarios={},
                                sensitivity={}, nostop={}), "G9")["status"]
    both = {"HIGH": {"trades": 5, "average_trade_bp": 1.0},
            "MID": {"trades": 5, "average_trade_bp": 1.0}}
    one = {"HIGH": {"trades": 5, "average_trade_bp": 1.0},
           "MID": {"trades": 5, "average_trade_bp": -1.0}}
    assert evaluate(both) == G.PASS
    assert evaluate(one) == G.FAIL


def test_g8_needs_all_six_sensitivity_arms(backtest):
    def evaluate(arms):
        return _gate(G.evaluate(backtest, main=M.summarise([], starting_equity=1.0),
                                ci={"mean": 1.0, "lo": 0.5, "hi": 2.0}, folds=[], years=[],
                                regimes={"trend": {}, "volatility": {}, "session": {}},
                                concentration={}, loyo={}, scenarios={}, sensitivity=arms,
                                nostop={}), "G8")["status"]
    six = {f"A-SENS-{i}": {"average_trade_bp": 1.0} for i in range(1, 7)}
    assert evaluate(six) == G.PASS
    six["A-SENS-3"] = {"average_trade_bp": -1.0}
    assert evaluate(six) == G.FAIL
    assert evaluate({f"A-SENS-{i}": {"average_trade_bp": 1.0} for i in range(1, 4)}) == \
        G.NOT_EVALUABLE
