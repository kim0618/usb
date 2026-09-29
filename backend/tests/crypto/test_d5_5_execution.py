"""Execution, the one-trade-per-event limit, the verdict rules, and isolation.

The rules that make D5.5 different from D5.4 are the ones pinned hardest: an event yields at most
one trade, NO_TRADE is a real outcome, and the exit is a structural level rather than a symmetric
bracket. S10 gets its own tests because it carries the study's question.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
import pytest

from app.crypto.research.d5_4.events import Event
from app.crypto.research.d5_5 import confirmation as CF
from app.crypto.research.d5_5 import execution as EX
from app.crypto.research.d5_5 import runner as R
from app.crypto.research.d5_5 import verdict as V

D5_5_DIR = Path(__file__).resolve().parents[2] / "app" / "crypto" / "research" / "d5_5"
CRYPTO_DIR = Path(__file__).resolve().parents[2] / "app" / "crypto"
OOS_START_MS = 1_640_995_200_000          # 2022-01-01T00:00:00Z


@pytest.fixture(scope="module")
def contract():
    return R.load_contract()


def synthetic_grid(n: int = 4_000, *, seed: int = 20260928) -> dict[str, np.ndarray]:
    """A grid inside the OOS window with the columns the harness reads."""
    rng = np.random.default_rng(seed)
    close = 100_000.0 * np.exp(np.cumsum(rng.normal(0.0, 8e-4, n)))
    ts = OOS_START_MS + np.arange(n, dtype=np.int64) * 60_000
    return {"ts": ts, "close": close, "open": close,
            "mark_close": close, "mark_low": close * 0.997, "mark_high": close * 1.003,
            "oi": np.full(n, 1e9), "index_close": close,
            "funding_ts": np.arange(ts[0], ts[-1], 8 * 3_600_000, dtype=np.int64),
            "funding_rate": np.zeros(len(np.arange(ts[0], ts[-1], 8 * 3_600_000)))}


def always_confirm(side: str, *, bar_offset: int = 1, stop_gap: float = 0.01,
                   target_gap: float | None = None):
    """A detector that confirms every event, so execution can be tested in isolation."""
    def detector(event: Event) -> CF.Confirmation:
        bar = event.end + bar_offset
        return CF.Confirmation(
            event.start, event.end,
            CF.CONFIRMED_LONG if side == CF.LONG else CF.CONFIRMED_SHORT,
            bar=bar, side=side, level=0.0, stop_price=0.0, target_price=None,
            distance=0.001,
            shock=CF.ShockWindow(0, 0, 1.0, 1.0, 1.0, 0.02),
            detail={"stop_gap": stop_gap, "target_gap": target_gap})
    return detector


def priced_detector(grid, side: str, *, stop_gap: float = 0.01,
                    target_gap: float | None = None):
    def detector(event: Event) -> CF.Confirmation:
        bar = event.end + 1
        reference = float(grid["close"][bar])
        sign = 1.0 if side == CF.LONG else -1.0
        stop = reference * (1 - sign * stop_gap)
        target = None if target_gap is None else reference * (1 + sign * target_gap)
        return CF.Confirmation(
            event.start, event.end,
            CF.CONFIRMED_LONG if side == CF.LONG else CF.CONFIRMED_SHORT,
            bar=bar, side=side, level=reference, stop_price=stop, target_price=target,
            distance=0.001,
            shock=CF.ShockWindow(0, 0, reference, reference, reference, 0.02))
    return detector


def run(contract, grid, detector, events, *, arm: str = "BASE", hold_bars: int = 240):
    candidate = contract["candidates"][0]
    return EX.run(grid, contract, candidate, events, detector, arm=arm, hold_bars=hold_bars,
                  fee_path=R.FEE_PATH, risk_limit_path=R.RISK_LIMIT,
                  oos_start_ms=int(grid["ts"][0]), oos_end_ms=int(grid["ts"][-1]) + 1)


# --- exit resolution ------------------------------------------------------------------------

def bar_grid(lows, highs, opens):
    n = len(lows)
    return {"ts": np.arange(n, dtype=np.int64) * 60_000,
            "open": np.array(opens, dtype=float),
            "mark_low": np.array(lows, dtype=float),
            "mark_high": np.array(highs, dtype=float)}


def test_the_stop_wins_when_one_bar_touches_both():
    grid = bar_grid([95.0], [105.0], [100.0])
    index, reason, price, both = EX.find_exit(CF.LONG, grid, 0, 0, 96.0, 104.0)
    assert reason == EX.STOP_EXIT and both is True and price == 96.0


def test_a_candidate_without_a_target_can_only_stop_or_time_out():
    grid = bar_grid([99.0, 95.0], [140.0, 140.0], [100.0, 100.0])
    index, reason, _, _ = EX.find_exit(CF.LONG, grid, 0, 1, 96.0, None)
    assert reason == EX.STOP_EXIT and index == 1


def test_no_target_means_a_big_favourable_move_runs_to_the_horizon():
    grid = bar_grid([99.0, 99.0], [200.0, 300.0], [100.0, 100.0])
    index, reason, price, _ = EX.find_exit(CF.LONG, grid, 0, 1, 96.0, None)
    assert reason == EX.TIME_EXIT and index == 1 and price == 100.0


def test_a_gap_through_the_stop_fills_at_the_open():
    grid = bar_grid([90.0], [92.0], [91.0])
    _, reason, price, _ = EX.find_exit(CF.LONG, grid, 0, 0, 96.0, None)
    assert reason == EX.STOP_EXIT and price == 91.0


def test_a_gap_through_the_target_fills_at_the_open():
    grid = bar_grid([108.0], [112.0], [110.0])
    _, reason, price, _ = EX.find_exit(CF.LONG, grid, 0, 0, 96.0, 104.0)
    assert reason == EX.TARGET_EXIT and price == 110.0


def test_the_short_side_is_mirrored():
    grid = bar_grid([96.0], [99.0], [98.0])
    _, reason, price, _ = EX.find_exit(CF.SHORT, grid, 0, 0, 104.0, 96.0)
    assert reason == EX.TARGET_EXIT and price == 96.0
    grid = bar_grid([99.0], [106.0], [100.0])
    _, reason, price, _ = EX.find_exit(CF.SHORT, grid, 0, 0, 104.0, 96.0)
    assert reason == EX.STOP_EXIT and price == 104.0


# --- one trade per event, NO_TRADE, overlap -------------------------------------------------

def test_no_trade_is_counted_not_forced(contract):
    grid = synthetic_grid()
    events = [Event(start=100, end=101, displacement=0.05),
              Event(start=1000, end=1001, displacement=0.05)]
    expired = lambda event: CF.Confirmation(event.start, event.end, CF.EXPIRED)  # noqa: E731
    result = run(contract, grid, expired, events)
    assert result.events_in_oos == 2
    assert result.no_trade == 2
    assert result.confirmed == 0
    assert result.trades == []
    assert result.confirmation_rate == 0.0


def test_one_event_yields_at_most_one_trade(contract):
    grid = synthetic_grid()
    events = [Event(start=100, end=101, displacement=0.05)]
    result = run(contract, grid, priced_detector(grid, CF.LONG), events)
    assert len(result.trades) <= 1
    assert result.confirmed == 1


def test_a_confirmation_while_a_position_is_open_is_ignored(contract):
    grid = synthetic_grid()
    events = [Event(start=100, end=101, displacement=0.05),
              Event(start=150, end=151, displacement=0.05)]
    result = run(contract, grid, priced_detector(grid, CF.LONG), events, hold_bars=240)
    assert result.confirmed == 2
    assert result.ignored_position_open + result.ignored_cooldown >= 1
    assert len(result.trades) == 1


def test_positions_never_overlap(contract):
    grid = synthetic_grid()
    events = [Event(start=s, end=s + 1, displacement=0.05) for s in range(100, 3000, 90)]
    result = run(contract, grid, priced_detector(grid, CF.LONG), events)
    ordered = sorted(result.trades, key=lambda t: t.entry_ts_ms)
    for previous, nxt in zip(ordered, ordered[1:]):
        assert nxt.entry_ts_ms > previous.exit_ts_ms


def test_the_reentry_cooldown_is_respected(contract):
    grid = synthetic_grid()
    events = [Event(start=s, end=s + 1, displacement=0.05) for s in range(100, 3000, 90)]
    result = run(contract, grid, priced_detector(grid, CF.LONG), events)
    cooldown = contract["position"]["reentry_cooldown_bars"] * 60_000
    ordered = sorted(result.trades, key=lambda t: t.entry_ts_ms)
    for previous, nxt in zip(ordered, ordered[1:]):
        assert nxt.decision_ts_ms - previous.exit_ts_ms >= cooldown


def test_every_event_is_accounted_for(contract):
    grid = synthetic_grid()
    events = [Event(start=s, end=s + 1, displacement=0.05) for s in range(100, 3000, 90)]
    result = run(contract, grid, priced_detector(grid, CF.LONG), events)
    assert (len(result.trades) + len(result.rejects) + result.no_trade
            + result.ignored_position_open + result.ignored_cooldown) == result.events_in_oos


# --- execution mechanics ---------------------------------------------------------------------

def test_a_stop_on_the_wrong_side_is_rejected_not_traded(contract):
    grid = synthetic_grid()
    events = [Event(start=100, end=101, displacement=0.05)]
    detector = priced_detector(grid, CF.LONG, stop_gap=-0.01)   # stop above entry for a LONG
    result = run(contract, grid, detector, events)
    assert result.trades == []
    assert [r["code"] for r in result.rejects] == [EX.REJECT_STOP_SHAPE]


def test_a_target_closer_than_cost_is_rejected(contract):
    grid = synthetic_grid()
    events = [Event(start=100, end=101, displacement=0.05)]
    detector = priced_detector(grid, CF.LONG, target_gap=0.0001)   # 1bp of room
    result = run(contract, grid, detector, events)
    assert result.trades == []
    assert [r["code"] for r in result.rejects] == [EX.REJECT_ROOM]


def test_a_confirmation_too_late_for_an_exit_window_is_rejected(contract):
    grid = synthetic_grid(n=300)
    events = [Event(start=200, end=201, displacement=0.05)]
    result = run(contract, grid, priced_detector(grid, CF.LONG), events, hold_bars=240)
    assert result.trades == []
    assert [r["code"] for r in result.rejects] == [EX.REJECT_NO_EXIT_WINDOW]


def test_a_reject_does_not_erase_the_confirmation(contract):
    grid = synthetic_grid(n=300)
    events = [Event(start=200, end=201, displacement=0.05)]
    result = run(contract, grid, priced_detector(grid, CF.LONG), events, hold_bars=240)
    assert result.confirmed == 1
    assert result.no_trade == 0            # it confirmed; it just could not be executed


def test_equity_identity_holds_on_every_trade(contract):
    grid = synthetic_grid()
    events = [Event(start=s, end=s + 1, displacement=0.05) for s in range(100, 3000, 90)]
    result = run(contract, grid, priced_detector(grid, CF.LONG), events)
    assert result.trades
    assert result.identity_violations == 0
    for trade in result.trades:
        assert trade.equity_after - trade.equity_before == pytest.approx(trade.net_usdt, abs=1e-6)
        assert trade.net_usdt == pytest.approx(
            trade.gross_pnl_usdt - trade.fees_usdt - trade.funding_usdt, abs=1e-9)


def test_a_long_entry_fills_at_or_above_the_reference(contract):
    grid = synthetic_grid()
    events = [Event(start=s, end=s + 1, displacement=0.05) for s in range(100, 3000, 300)]
    result = run(contract, grid, priced_detector(grid, CF.LONG), events)
    for trade in result.trades:
        assert trade.entry_fill_price >= trade.entry_reference_price


def test_the_risk_budget_identity_holds_before_rounding(contract):
    grid = synthetic_grid()
    events = [Event(start=s, end=s + 1, displacement=0.05) for s in range(100, 3000, 300)]
    result = run(contract, grid, priced_detector(grid, CF.LONG), events)
    budget = contract["risk"]["risk_budget_pct_of_equity"] / 100.0
    for trade in result.trades:
        if trade.notional_capped:
            continue
        assert trade.target_notional * trade.stop_distance == pytest.approx(
            trade.equity_before * budget, rel=1e-9)


def test_no_trade_outlives_the_max_hold(contract):
    grid = synthetic_grid()
    events = [Event(start=s, end=s + 1, displacement=0.05) for s in range(100, 3000, 300)]
    result = run(contract, grid, priced_detector(grid, CF.LONG), events, hold_bars=240)
    for trade in result.trades:
        assert 0 < trade.hold_minutes <= 240


def test_the_run_is_deterministic(contract):
    grid = synthetic_grid()
    events = [Event(start=s, end=s + 1, displacement=0.05) for s in range(100, 3000, 90)]
    a = run(contract, grid, priced_detector(grid, CF.LONG), events)
    b = run(contract, grid, priced_detector(grid, CF.LONG), events)
    assert [t.as_dict() for t in a.trades] == [t.as_dict() for t in b.trades]
    assert a.as_dict() == b.as_dict()


def test_events_outside_the_window_are_skipped(contract):
    grid = synthetic_grid()
    candidate = contract["candidates"][0]
    events = [Event(start=100, end=101, displacement=0.05)]
    result = EX.run(grid, contract, candidate, events, priced_detector(grid, CF.LONG),
                    arm="BASE", hold_bars=240, fee_path=R.FEE_PATH,
                    risk_limit_path=R.RISK_LIMIT,
                    oos_start_ms=int(grid["ts"][-1]), oos_end_ms=int(grid["ts"][-1]) + 1)
    assert result.events_in_oos == 0 and result.trades == []


def test_leverage_is_one(contract):
    engine = EX.build_engine(contract, R.FEE_PATH, R.RISK_LIMIT)
    assert float(engine.leverage) == 1.0
    assert contract["risk"]["leverage"] == 1.0
    assert contract["risk"]["leverage_optimisation"] is False


# --- arms -----------------------------------------------------------------------------------

@pytest.mark.parametrize("arm,window", [("BASE", 60), ("SENS-MARGIN", 60),
                                        ("SENS-WINDOW", 30), ("REF-120M", 120)])
def test_each_arm_changes_exactly_what_it_says(contract, arm, window):
    settings = R.arm_settings(contract, arm)
    assert settings["window_bars"] == window
    assert bool(settings.get("use_margin_overrides")) == (arm == "SENS-MARGIN")


def test_the_margin_arm_moves_every_candidate_one_step(contract):
    assert R.SENS_MARGIN["A"] == {"margin_fraction": 0.40}
    assert R.SENS_MARGIN["B"] == {"retrace_fraction": 0.618}
    assert R.SENS_MARGIN["C"] == {"compression_fraction": 0.35}


def test_only_base_judges(contract):
    judging = [name for name, spec in contract["arms"].items() if spec.get("judging")]
    assert judging == ["BASE"]
    assert contract["arms"]["REF-120M"]["role"] == "REPORT_ONLY"


# --- verdict rules ----------------------------------------------------------------------------

class FakeRun:
    events_in_oos = 415


def block(**over):
    base = {"trades": 200, "gross_bp_mean": 40.0, "average_trade_bp": 25.0,
            "edge_to_cost_ratio": 3.0, "profit_factor": 1.6, "mdd": 0.10}
    base.update(over)
    return base


def folds(n=9, trades=20, gross=5.0, net=5.0):
    return [{"fold": f"F{i+1}", "trades": trades, "gross_bp_mean": gross,
             "average_trade_bp": net} for i in range(n)]


def evaluate(contract, **over):
    kwargs = {"main": block(), "ci": {"mean": 25.0, "lo": 5.0, "hi": 45.0},
              "folds": folds(), "years": [{"year": y} for y in range(2022, 2027)],
              "concentration": {"top_1_trade_share": 0.05, "total_net_usdt": 100.0},
              "loyo": {str(y): 5.0 for y in range(2022, 2027)},
              "scenarios": {"VIP0_STRESS": 5.0},
              "sensitivity": {f"SENS-{i}": {"average_trade_bp": 3.0} for i in range(2)},
              "run": FakeRun(), "stop_share": 0.30}
    kwargs.update(over)
    return V.evaluate(contract, **kwargs)


def status_of(rows, rule_id):
    return next(r for r in rows if r["id"] == rule_id)["status"]


def test_a_fully_passing_input_survives(contract):
    rows = evaluate(contract)
    assert all(r["status"] == V.PASS for r in rows)
    assert V.classify(rows, 200, contract)["verdict"] == V.SURVIVE


def test_s10_is_the_coin_flip_test(contract):
    assert status_of(evaluate(contract, stop_share=0.49), "S10") == V.PASS
    assert status_of(evaluate(contract, stop_share=0.50), "S10") == V.FAIL
    assert status_of(evaluate(contract, stop_share=0.71), "S10") == V.FAIL
    assert status_of(evaluate(contract, stop_share=None), "S10") == V.NOT_EVALUABLE


def test_s10_alone_can_block_a_survive(contract):
    rows = evaluate(contract, stop_share=0.71)
    call = V.classify(rows, 200, contract)
    assert call["verdict"] == V.WEAK          # S1 still passes
    assert call["failed"] == ["S10"]


def test_s1_needs_the_ci_lower_bound(contract):
    assert status_of(evaluate(contract, ci={"mean": 25.0, "lo": -1.0, "hi": 50.0}), "S1") == V.FAIL
    assert status_of(evaluate(contract, ci={"mean": None, "lo": None, "hi": None}), "S1") == \
        V.NOT_EVALUABLE


def test_s2_requires_twice_the_cost(contract):
    assert status_of(evaluate(contract, main=block(edge_to_cost_ratio=1.9)), "S2") == V.FAIL
    assert status_of(evaluate(contract, main=block(edge_to_cost_ratio=2.0)), "S2") == V.PASS


def test_s3_reads_the_sample_gate_from_the_contract(contract):
    gate = contract["sample_gate"]
    assert gate["min_confirmed_trades"] == 60
    assert status_of(evaluate(contract, main=block(trades=59)), "S3") == V.FAIL

    class Thin:
        events_in_oos = 10
    assert status_of(evaluate(contract, run=Thin()), "S3") == V.FAIL


def test_s5_fails_on_one_negative_year(contract):
    loyo = {str(y): 5.0 for y in range(2022, 2027)}
    loyo["2024"] = -1.0
    assert status_of(evaluate(contract, loyo=loyo), "S5") == V.FAIL


def test_s6_is_not_evaluable_on_a_negative_total(contract):
    rows = evaluate(contract, concentration={"top_1_trade_share": V.NOT_MEANINGFUL,
                                             "total_net_usdt": -100.0})
    assert status_of(rows, "S6") == V.NOT_EVALUABLE
    assert V.classify(rows, 200, contract)["verdict"] != V.SURVIVE


def test_too_few_trades_is_inconclusive(contract):
    rows = evaluate(contract, main=block(trades=40))
    assert V.classify(rows, 40, contract)["verdict"] == V.INCONCLUSIVE


def test_reject_when_s1_fails(contract):
    rows = evaluate(contract, ci={"mean": -5.0, "lo": -15.0, "hi": 4.0})
    assert V.classify(rows, 200, contract)["verdict"] == V.REJECT


# --- isolation ----------------------------------------------------------------------------------

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
    return sorted(p for p in D5_5_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def test_the_event_detector_is_imported_from_d5_4_not_rewritten():
    modules = set()
    for path in sources():
        modules |= imported(path)
    assert any("d5_4" in name for name in modules)
    for path in sources():
        text = path.read_text(encoding="utf-8")
        assert "def robust_z" not in text, f"{path.name} reimplements robust_z"
        assert "def merge(" not in text, f"{path.name} reimplements the event merge"


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
    for path in sources():
        text = path.read_text(encoding="utf-8")
        tokens = ["datetime.now(", "utcnow", "requests.", "websocket"]
        if path.name != "runner.py":
            tokens.append("time.time(")
        for token in tokens:
            assert token not in text, f"{path.name} uses {token}"


def test_the_paper_engine_does_not_import_this_package():
    for path in list((CRYPTO_DIR / "paper").rglob("*.py")) + \
            list((CRYPTO_DIR / "terminal").rglob("*.py")):
        assert "d5_5" not in path.read_text(encoding="utf-8"), path


def test_the_contract_bans_what_the_prompt_bans(contract):
    banned = set(contract["prohibitions"])
    for item in ("D5.4 threshold tuning", "D5.4 C1-SENS-TARGET reuse", "FDN-V1 tuning",
                 "confirmation threshold brute force", "best observation window selection",
                 "post-hoc single-side selection", "maker", "LIMIT", "ADD", "ML",
                 "liquidation forward data", "AUTO implementation", "commit", "push"):
        assert item in banned, item


def test_results_declare_the_evidence_class_and_gate():
    results = json.loads((Path(__file__).resolve().parents[3]
                          / "data/research/crypto/d5_5/results_v1.json").read_text())
    assert results["evidence_class"] == "HISTORICAL_CONFIRMATION_NOT_TRUE_OOS"
    assert results["d6_v2"] == ("AUTHORIZED" if results["survive_count"] else "NOT AUTHORIZED")
    assert results["events_detected"] == 458
