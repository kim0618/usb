"""Execution, sizing, the bracket, the verdict rules, and isolation.

The bracket is the new machinery here, so the intrabar rule, the gap handling and the one
position at a time constraint are pinned directly. The verdict rules are checked against
hand-built inputs where the right answer is arithmetic, not a backtest.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
import pytest

from app.crypto.research.d5_4 import events as EV
from app.crypto.research.d5_4 import execution as EX
from app.crypto.research.d5_4 import runner as R
from app.crypto.research.d5_4 import verdict as V

from tests.crypto.test_d5_4_events import _grid_with_columns

D5_4_DIR = Path(__file__).resolve().parents[2] / "app" / "crypto" / "research" / "d5_4"
CRYPTO_DIR = Path(__file__).resolve().parents[2] / "app" / "crypto"


@pytest.fixture(scope="module")
def contract():
    return R.load_contract()


def _shock_grid() -> dict[str, np.ndarray]:
    """A synthetic grid with deliberate ten sigma shocks, so C1 actually fires.

    Gaussian noise never produces a ten sigma move, so a plain random walk would leave the event
    set empty and every execution test would pass vacuously. Shocks are injected sparsely (every
    three days) so they stay a negligible share of the 30 day MAD window and remain tails rather
    than becoming the new normal.
    """
    grid = _grid_with_columns()
    n = len(grid["ts"])
    shock_rng = np.random.default_rng(4242)
    close = grid["close"].copy()
    # Open interest needs its own noise: a perfectly smooth series has a MAD of zero, the robust
    # z is undefined, and every bar would be filtered out before any event could form.
    oi = grid["oi"] * np.exp(np.cumsum(shock_rng.normal(0.0, 2e-4, n)))
    for start in range(32 * EV.DAY_BARS, n - 600, 3 * EV.DAY_BARS):
        close[start:] *= 0.95          # a 5 percent drop, far beyond ten sigma of disp15
        oi[start:] *= 0.93             # open interest contracting with it
    grid["close"] = close
    grid["oi"] = oi
    grid["mark_close"] = close
    grid["mark_low"] = close * 0.995
    grid["mark_high"] = close * 1.005
    grid["open"] = close
    rng = np.random.default_rng(20260928)
    grid["index_close"] = close * np.exp(-rng.normal(0.0, 3e-4, n))
    return grid


@pytest.fixture(scope="module")
def grid():
    return _shock_grid()


@pytest.fixture(scope="module")
def run_c1(contract, grid):
    candidate = contract["candidates"][0]
    signals = EV.build(grid, candidate, cooldown_bars=240)
    return EX.run(grid, contract, signals, arm="BASE", hold_bars=240, bracket_multiple=0.5,
                  fee_path=R.FEE_PATH, risk_limit_path=R.RISK_LIMIT,
                  oos_start_ms=int(grid["ts"][0]), oos_end_ms=int(grid["ts"][-1]) + 1)


# --- bracket arithmetic --------------------------------------------------------------------

def test_bracket_scales_with_the_event_and_clips():
    assert EX.bracket_distance(0.0140, 0.5, 0.003, 0.05) == pytest.approx(0.0070)
    assert EX.bracket_distance(0.0001, 0.5, 0.003, 0.05) == 0.003      # floor
    assert EX.bracket_distance(0.5000, 0.5, 0.003, 0.05) == 0.05       # cap
    assert EX.bracket_distance(-0.0140, 0.5, 0.003, 0.05) == pytest.approx(0.0070)


def test_target_and_stop_are_the_same_distance(run_c1):
    """A 1:1 bracket is the whole design; an asymmetry here would be untested tuning."""
    for trade in run_c1.trades:
        up = trade.target_price / trade.entry_fill_price - 1
        down = 1 - trade.stop_price / trade.entry_fill_price
        if trade.side == EX.LONG:
            assert up == pytest.approx(trade.bracket_distance, rel=1e-9)
            assert down == pytest.approx(trade.bracket_distance, rel=1e-9)


def test_target_notional_honours_the_risk_identity():
    notional = EX.target_notional(0.005, 1.0, 10_000.0, 0.04)
    assert notional * 0.04 == pytest.approx(10_000.0 * 0.005)


def test_target_notional_respects_the_cap():
    assert EX.target_notional(0.005, 1.0, 10_000.0, 0.003) == pytest.approx(10_000.0)


def test_the_cap_binds_at_the_bracket_floor():
    """0.005 / 0.003 = 1.67x equity, so unlike D6-C the cap is reachable here."""
    uncapped = 10_000.0 * 0.005 / 0.003
    assert uncapped > 10_000.0
    assert EX.target_notional(0.005, 1.0, 10_000.0, 0.003) == 10_000.0


# --- intrabar rule --------------------------------------------------------------------------

def bar_grid(lows, highs, opens) -> dict[str, np.ndarray]:
    n = len(lows)
    return {"ts": np.arange(n, dtype=np.int64) * 60_000,
            "open": np.array(opens, dtype=float),
            "mark_low": np.array(lows, dtype=float),
            "mark_high": np.array(highs, dtype=float)}


def test_the_stop_wins_when_one_bar_touches_both():
    grid = bar_grid(lows=[95.0], highs=[105.0], opens=[100.0])
    index, reason, price, both = EX._find_exit(EX.LONG, grid, 0, 0, 104.0, 96.0)
    assert reason == EX.STOP_EXIT
    assert both is True
    assert price == 96.0


def test_a_gap_through_the_stop_fills_at_the_open_not_the_stop():
    grid = bar_grid(lows=[90.0], highs=[92.0], opens=[91.0])
    _, reason, price, _ = EX._find_exit(EX.LONG, grid, 0, 0, 104.0, 96.0)
    assert reason == EX.STOP_EXIT
    assert price == 91.0                      # min(96, 91)


def test_a_gap_through_the_target_fills_at_the_open():
    grid = bar_grid(lows=[108.0], highs=[112.0], opens=[110.0])
    _, reason, price, _ = EX._find_exit(EX.LONG, grid, 0, 0, 104.0, 96.0)
    assert reason == EX.TARGET_EXIT
    assert price == 110.0                     # max(104, 110)


def test_an_untouched_bracket_runs_to_the_time_exit():
    grid = bar_grid(lows=[99.0, 99.5], highs=[101.0, 100.5], opens=[100.0, 100.2])
    index, reason, price, _ = EX._find_exit(EX.LONG, grid, 0, 1, 104.0, 96.0)
    assert reason == EX.TIME_EXIT
    assert index == 1 and price == 100.2


def test_the_first_touched_bar_ends_the_trade():
    grid = bar_grid(lows=[99.0, 95.0, 90.0], highs=[101.0, 101.0, 101.0],
                    opens=[100.0, 100.0, 100.0])
    index, reason, _, _ = EX._find_exit(EX.LONG, grid, 0, 2, 104.0, 96.0)
    assert index == 1 and reason == EX.STOP_EXIT


def test_the_short_bracket_is_mirrored():
    grid = bar_grid(lows=[96.0], highs=[99.0], opens=[98.0])
    _, reason, price, _ = EX._find_exit(EX.SHORT, grid, 0, 0, 96.0, 104.0)
    assert reason == EX.TARGET_EXIT
    assert price == 96.0
    grid = bar_grid(lows=[99.0], highs=[106.0], opens=[100.0])
    _, reason, price, _ = EX._find_exit(EX.SHORT, grid, 0, 0, 96.0, 104.0)
    assert reason == EX.STOP_EXIT
    assert price == 104.0


# --- the executed run -----------------------------------------------------------------------

def test_the_run_produces_trades(run_c1):
    assert run_c1.trades


def test_equity_identity_holds_on_every_trade(run_c1):
    assert run_c1.identity_violations == 0
    for trade in run_c1.trades:
        assert trade.equity_after - trade.equity_before == pytest.approx(trade.net_usdt, abs=1e-6)
        assert trade.net_usdt == pytest.approx(
            trade.gross_pnl_usdt - trade.fees_usdt - trade.funding_usdt, abs=1e-9)


def test_only_one_position_at_a_time(run_c1):
    ordered = sorted(run_c1.trades, key=lambda t: t.entry_ts_ms)
    for previous, nxt in zip(ordered, ordered[1:]):
        assert nxt.entry_ts_ms > previous.exit_ts_ms


def test_the_reentry_cooldown_is_respected(run_c1, contract):
    cooldown = contract["position"]["reentry_cooldown_bars"] * 60_000
    ordered = sorted(run_c1.trades, key=lambda t: t.entry_ts_ms)
    for previous, nxt in zip(ordered, ordered[1:]):
        assert nxt.decision_ts_ms - previous.exit_ts_ms >= cooldown


def test_no_trade_outlives_the_max_hold(run_c1):
    for trade in run_c1.trades:
        assert 0 < trade.hold_minutes <= 240


def test_a_long_entry_fills_at_or_above_the_reference(run_c1):
    for trade in run_c1.trades:
        if trade.side == EX.LONG:
            assert trade.entry_fill_price >= trade.entry_reference_price


def test_rejects_are_recorded_and_are_not_trades(run_c1):
    for row in run_c1.rejects:
        assert row["code"] in {EX.REJECT_QTY, EX.REJECT_NOTIONAL, EX.REJECT_ENGINE,
                               EX.REJECT_NO_EXIT_WINDOW}
    entries = {t.entry_ts_ms for t in run_c1.trades}
    assert not any(row["ts_ms"] in entries for row in run_c1.rejects)


def test_overlapping_events_are_counted_not_traded(run_c1):
    assert run_c1.events_ignored_overlap + run_c1.events_in_cooldown + len(run_c1.trades) \
        + len(run_c1.rejects) == run_c1.events_in_oos


def test_the_run_is_deterministic(contract, grid):
    candidate = contract["candidates"][0]
    signals = EV.build(grid, candidate, cooldown_bars=240)
    kwargs = dict(arm="BASE", hold_bars=240, bracket_multiple=0.5, fee_path=R.FEE_PATH,
                  risk_limit_path=R.RISK_LIMIT, oos_start_ms=int(grid["ts"][0]),
                  oos_end_ms=int(grid["ts"][-1]) + 1)
    a = EX.run(grid, contract, signals, **kwargs)
    b = EX.run(grid, contract, signals, **kwargs)
    assert [t.as_dict() for t in a.trades] == [t.as_dict() for t in b.trades]
    assert a.as_dict() == b.as_dict()


def test_trades_outside_the_window_are_not_taken(contract, grid):
    candidate = contract["candidates"][0]
    signals = EV.build(grid, candidate, cooldown_bars=240)
    empty = EX.run(grid, contract, signals, arm="BASE", hold_bars=240, bracket_multiple=0.5,
                   fee_path=R.FEE_PATH, risk_limit_path=R.RISK_LIMIT,
                   oos_start_ms=int(grid["ts"][-1]), oos_end_ms=int(grid["ts"][-1]) + 1)
    assert empty.trades == []


def test_leverage_is_one(contract):
    engine = EX.build_engine(contract, R.FEE_PATH, R.RISK_LIMIT)
    assert float(engine.leverage) == 1.0
    assert contract["risk"]["leverage"] == 1.0
    assert contract["risk"]["leverage_optimisation"] is False


def test_risk_budget_is_the_contracted_half_percent(contract):
    assert contract["risk"]["risk_budget_pct_of_equity"] == 0.5
    assert contract["risk"]["risk_budget_shared_across_candidates"] is True


# --- arms ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("arm,field,value", [
    ("SENS-TARGET", "bracket_multiple", 0.75),
    ("SENS-HOLD", "hold_bars", 120),
    ("REF-8H", "hold_bars", 480),
])
def test_each_arm_changes_exactly_one_thing(contract, arm, field, value):
    base = R.arm_settings(contract, contract["candidates"][0], "BASE")
    spec = R.arm_settings(contract, contract["candidates"][0], arm)
    assert spec[field] == value
    differing = [k for k in ("hold_bars", "bracket_multiple", "threshold_override")
                 if spec[k] != base[k]]
    assert differing == [field]


def test_the_threshold_arm_moves_exactly_one_ladder_step(contract):
    spec = R.arm_settings(contract, contract["candidates"][0], "SENS-THRESH")
    assert spec["threshold_override"] == {"z_disp15_max": -8.0}


def test_the_short_candidate_relaxes_in_its_own_direction(contract):
    short = next(c for c in contract["candidates"] if c["id"] == "C2")
    assert R.relax_threshold(short) == {"z_disp15_min": 8.0}


def test_only_base_judges(contract):
    judging = [name for name, spec in contract["arms"].items() if spec.get("judging")]
    assert judging == ["BASE"]
    assert contract["arms"]["REF-8H"]["role"] == "REPORT_ONLY"


# --- verdict rules ---------------------------------------------------------------------------------

def block(**over):
    base = {"trades": 200, "gross_bp_mean": 30.0, "average_trade_bp": 20.0,
            "edge_to_cost_ratio": 3.0, "profit_factor": 1.6, "mdd": 0.10}
    base.update(over)
    return base


def folds(n=9, trades=20, gross=5.0, net=5.0):
    return [{"fold": f"F{i+1}", "trades": trades, "gross_bp_mean": gross,
             "average_trade_bp": net} for i in range(n)]


def evaluate(contract, **over):
    kwargs = {"main": block(), "ci": {"mean": 20.0, "lo": 5.0, "hi": 35.0},
              "folds": folds(), "years": [{"year": y} for y in range(2022, 2027)],
              "concentration": {"top_1_trade_share": 0.05, "total_net_usdt": 100.0},
              "loyo": {str(y): 5.0 for y in range(2022, 2027)},
              "scenarios": {"VIP0_STRESS": 5.0},
              "sensitivity": {f"SENS-{i}": {"average_trade_bp": 3.0} for i in range(3)}}
    kwargs.update(over)
    return V.evaluate(contract, **kwargs)


def status_of(rows, rule_id):
    return next(r for r in rows if r["id"] == rule_id)["status"]


def test_a_fully_passing_input_survives(contract):
    rows = evaluate(contract)
    assert all(r["status"] == V.PASS for r in rows)
    assert V.classify(rows, 200)["verdict"] == V.SURVIVE


def test_s1_needs_the_ci_lower_bound(contract):
    assert status_of(evaluate(contract, ci={"mean": 20.0, "lo": -1.0, "hi": 40.0}), "S1") == V.FAIL
    assert status_of(evaluate(contract, ci={"mean": None, "lo": None, "hi": None}), "S1") == \
        V.NOT_EVALUABLE


def test_s2_requires_twice_the_cost(contract):
    assert status_of(evaluate(contract, main=block(edge_to_cost_ratio=1.9)), "S2") == V.FAIL
    assert status_of(evaluate(contract, main=block(edge_to_cost_ratio=2.0)), "S2") == V.PASS


def test_s5_fails_on_one_negative_year(contract):
    loyo = {str(y): 5.0 for y in range(2022, 2027)}
    loyo["2024"] = -1.0
    assert status_of(evaluate(contract, loyo=loyo), "S5") == V.FAIL


def test_s6_is_not_evaluable_on_a_negative_total(contract):
    concentration = {"top_1_trade_share": V.NOT_MEANINGFUL, "total_net_usdt": -100.0}
    rows = evaluate(contract, concentration=concentration)
    assert status_of(rows, "S6") == V.NOT_EVALUABLE
    assert V.classify(rows, 200)["verdict"] != V.SURVIVE


def test_s7_needs_both_pf_and_mdd(contract):
    assert status_of(evaluate(contract, main=block(profit_factor=1.2)), "S7") == V.FAIL
    assert status_of(evaluate(contract, main=block(mdd=0.25)), "S7") == V.FAIL


def test_s9_fails_when_any_sensitivity_arm_is_negative(contract):
    arms = {f"SENS-{i}": {"average_trade_bp": 3.0} for i in range(3)}
    arms["SENS-1"] = {"average_trade_bp": -0.1}
    assert status_of(evaluate(contract, sensitivity=arms), "S9") == V.FAIL


def test_too_few_trades_is_inconclusive_not_reject(contract):
    rows = evaluate(contract, main=block(trades=50))
    assert V.classify(rows, 50)["verdict"] == V.INCONCLUSIVE


def test_weak_needs_s1_to_pass(contract):
    rows = evaluate(contract, main=block(edge_to_cost_ratio=1.0))
    call = V.classify(rows, 200)
    assert call["verdict"] == V.WEAK and call["s1"] == V.PASS


def test_reject_when_s1_fails(contract):
    rows = evaluate(contract, ci={"mean": -5.0, "lo": -15.0, "hi": 4.0})
    assert V.classify(rows, 200)["verdict"] == V.REJECT


def test_not_evaluable_never_makes_a_survive(contract):
    rows = evaluate(contract, concentration={"top_1_trade_share": V.NOT_MEANINGFUL,
                                             "total_net_usdt": -1.0})
    assert V.classify(rows, 200)["verdict"] != V.SURVIVE


# --- isolation ---------------------------------------------------------------------------------

def imported(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def sources() -> list[Path]:
    return sorted(p for p in D5_4_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_banned_data_line_is_reachable():
    banned = ("liquidation_forward", "derivatives_flow", "expert_execution", "registry_d5_2")
    for path in sources():
        for module in imported(path):
            for name in banned:
                assert name not in module, f"{path.name} imports {module}"


def test_no_production_path_is_named():
    banned = ("data/runtime/crypto/paper", "usb_runtime", "/root/", "traderj", "crypto-api")
    for path in sources():
        text = path.read_text(encoding="utf-8")
        for name in banned:
            assert name not in text, f"{path.name} names {name}"


def test_no_ledger_file_is_opened():
    for path in sources():
        text = path.read_text(encoding="utf-8")
        for token in ("input.jsonl", "ledger.jsonl", "InputTape", "recover("):
            assert token not in text, f"{path.name} touches {token}"


def test_no_clock_or_network_in_the_decision_path():
    """`runner.py` times its own run for the report; nothing below it may read a clock at all."""
    for path in sources():
        text = path.read_text(encoding="utf-8")
        tokens = ["datetime.now(", "utcnow", "requests.", "websocket"]
        if path.name != "runner.py":
            tokens.append("time.time(")
        for token in tokens:
            assert token not in text, f"{path.name} uses {token}"


def test_the_runner_only_uses_the_clock_to_measure_elapsed_time():
    text = (D5_4_DIR / "runner.py").read_text(encoding="utf-8")
    uses = [line.strip() for line in text.splitlines() if "time.time(" in line]
    assert uses == ["started = time.time()",
                    '"runtime_sec": round(time.time() - started, 1),']


def test_the_paper_engine_does_not_import_this_package():
    for path in list((CRYPTO_DIR / "paper").rglob("*.py")) + \
            list((CRYPTO_DIR / "terminal").rglob("*.py")):
        assert "d5_4" not in path.read_text(encoding="utf-8"), path


def test_the_contract_bans_what_the_prompt_bans(contract):
    banned = set(contract["prohibitions"])
    for item in ("threshold brute force", "best horizon selection", "best year selection",
                 "FDN-V1 parameter reuse tuning", "maker", "ADD", "ML",
                 "liquidation forward data", "AUTO implementation", "commit", "push"):
        assert item in banned, item


def test_results_declare_the_evidence_class():
    results = json.loads((Path(__file__).resolve().parents[3]
                          / "data/research/crypto/d5_4/results_v1.json").read_text())
    assert results["evidence_class"] == "HISTORICAL_CONFIRMATION_NOT_TRUE_OOS"
    assert results["d6_reentry"] == ("AUTHORIZED" if results["survive_count"]
                                     else "NOT AUTHORIZED")
