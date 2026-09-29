"""I1: the decision and the execution must not leak into each other.

The interpretation's whole claim is that a bar at t+1 can change the quantity and nothing else.
These tests hold it to that: the signal is invariant to the t+1 price, the quantity is not, and a
rejected execution leaves the signal standing rather than rewriting it as a HOLD.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from app.crypto.research.d6 import decision as DEC
from app.crypto.research.d6 import sizing as SIZING
from app.crypto.research.d6.model import HISTORICAL, LONG, MarketContext, StrategyState
from app.crypto.research.d6c import execution as EX
from app.crypto.research.d6c import signals as S
from app.crypto.research.d6c.contract import I1_JSON, load as load_contracts

from tests.crypto.d6_fixtures import clean_state, force_last_bar, synthetic_grid, window


@pytest.fixture(scope="module")
def backtest():
    return load_contracts()


@pytest.fixture(scope="module")
def strategy(backtest):
    return backtest.strategy


@pytest.fixture(scope="module")
def i1() -> dict:
    return json.loads(I1_JSON.read_text())


# --- the record itself -------------------------------------------------------------------

def test_i1_does_not_modify_the_strategy_contract(i1, strategy):
    """I1 names the markdown contract, which is the authority, not its machine-readable copy."""
    from app.crypto.research.d6.contract import CONTRACT_MD, sha256_file
    assert i1["contract_modified"] is False
    assert i1["amends"] == "docs/crypto/CRYPTO_D6_AUTO_STRATEGY_DESIGN_CONTRACT_V1.md"
    assert i1["amends_sha256"] == sha256_file(CONTRACT_MD)


def test_i1_declares_the_decision_needs_no_price(i1):
    assert i1["decision_time"]["price_required"] is False
    assert i1["decision_time"]["final_qty_required"] is False
    assert i1["decision_time"]["uses_open_t_plus_1"] is False


def test_i1_forbids_execution_from_touching_the_signal(i1):
    execution = i1["execution_time"]
    assert execution["may_change_score"] is False
    assert execution["may_change_direction"] is False
    assert execution["may_change_past_filter_verdicts"] is False


def test_i1_records_the_rejected_readings(i1):
    ids = {row["id"] for row in i1["rejected_interpretations"]}
    assert ids == {"R1", "R2", "R3"}
    for row in i1["rejected_interpretations"]:
        assert row["reason"].strip()


def test_i1_states_the_one_clause_whose_wording_changed(i1):
    status = i1["contract_clause_status"]
    assert status["section_3_pit"] == "HONOURED"
    assert status["section_6_1_entry_open_t_plus_1"] == "HONOURED"
    assert "EXECUTION_REJECT" in status["section_8_hold_on_unsizable"]


# --- the signal is price free --------------------------------------------------------------

def scene(**kwargs):
    grid = synthetic_grid(days=32, oi_slope=-1e-7, vol=9.0e-4, **kwargs)
    force_last_bar(grid, basis=-0.004, drop_1h=-0.02, oi_change_1h=-0.03)
    return grid


def decide(strategy, grid, *, entry_price, equity=7440.48):
    win = window(grid)
    market = MarketContext(now_ms=win.bar_close_ms,
                           oi_record_ts_ms=win.bar_close_ms - 6 * 60_000,
                           next_funding_ts_ms=win.bar_close_ms + 60 * 60_000,
                           entry_reference_price=entry_price)
    return DEC.decide(strategy, win, market, StrategyState(equity=equity), mode=HISTORICAL)


def test_the_signal_is_the_same_at_every_entry_price(strategy):
    grid = scene()
    base = S.signal_is_long(decide(strategy, grid, entry_price=None))
    for price in (1.0, 50_000.0, 100_000.0, 1_000_000.0):
        assert S.signal_is_long(decide(strategy, grid, entry_price=price)) is base


def test_score_and_filters_are_the_same_at_every_entry_price(strategy):
    grid = scene()
    reference = decide(strategy, grid, entry_price=None)
    for price in (50_000.0, 250_000.0):
        other = decide(strategy, grid, entry_price=price)
        assert other.long_score == reference.long_score
        assert [c.points for c in other.categories] == [c.points for c in reference.categories]
        assert [(f.filter_id, f.status) for f in other.filters] == \
               [(f.filter_id, f.status) for f in reference.filters]


def test_signal_is_long_agrees_with_the_engine_when_a_price_is_supplied(strategy):
    """The adapter is only allowed to differ from `decision == LONG` by the sizing deferral."""
    grid = scene()
    priced = decide(strategy, grid, entry_price=100_000.0)
    unpriced = decide(strategy, grid, entry_price=None)
    assert S.signal_is_long(unpriced) == (priced.decision == LONG)


def test_the_deferral_is_the_only_extra_reason_tolerated(strategy):
    grid = scene()
    unpriced = decide(strategy, grid, entry_price=None)
    extra = set(unpriced.reason_codes) - S.NON_BLOCKING
    assert extra <= S.DEFERRED_TO_EXECUTION


def test_a_real_blocking_reason_still_kills_the_signal(strategy):
    grid = scene()
    win = window(grid)
    market = MarketContext(now_ms=win.bar_close_ms, oi_record_ts_ms=None,
                           next_funding_ts_ms=win.bar_close_ms + 3_600_000,
                           entry_reference_price=None)
    blocked = DEC.decide(strategy, win, market, StrategyState(equity=7440.48), mode=HISTORICAL)
    assert S.signal_is_long(blocked) is False


# --- the quantity does depend on t+1 -------------------------------------------------------

def test_target_notional_is_price_free(strategy):
    a = EX.target_notional(strategy, 10_000.0, 0.03)
    b = EX.target_notional(strategy, 10_000.0, 0.03)
    assert a == b == pytest.approx(10_000.0 * 0.005 / 0.03)


def test_target_notional_honours_the_risk_identity(strategy):
    equity, distance = 10_000.0, 0.04
    notional = EX.target_notional(strategy, equity, distance)
    assert notional * distance == pytest.approx(equity * strategy.sizing["risk_budget"])


def test_target_notional_respects_the_cap(strategy):
    equity = 10_000.0
    notional = EX.target_notional(strategy, equity, 0.001)   # below the contract's stop floor
    assert notional <= equity * strategy.sizing["notional_cap_over_equity"]


def test_quantity_changes_with_the_execution_price(strategy):
    notional = EX.target_notional(strategy, 10_000.0, 0.03)
    from decimal import Decimal
    step = Decimal(str(strategy.qty_step))
    cheap = SIZING.floor_to_step(Decimal(str(notional)) / Decimal("50000"), step)
    dear = SIZING.floor_to_step(Decimal(str(notional)) / Decimal("100000"), step)
    assert cheap > dear


def test_target_notional_matches_the_d6b_sizing_module_at_the_contract_multiplier(strategy):
    """The harness formula and the engine's sizing must be the same thing at k = 2.5."""
    from decimal import Decimal
    equity, price, rv = 10_000.0, 100_000.0, 6.0e-4
    distance = SIZING.stop_distance(strategy, rv)
    notional = EX.target_notional(strategy, equity, distance)
    harness_qty = float(SIZING.floor_to_step(Decimal(str(notional)) / Decimal(str(price)),
                                             Decimal(str(strategy.qty_step))))
    engine_qty = SIZING.size(strategy, equity=equity, entry_price=price, rv24h=rv).final_qty
    assert harness_qty == engine_qty


# --- execution rejection does not rewrite the signal ------------------------------------------

def test_reject_codes_are_the_contracted_ones(i1):
    assert set(i1["execution_reject"]["codes"]) >= {
        "EXECUTION_REJECT_QTY_BELOW_MIN", "EXECUTION_REJECT_NOTIONAL_BELOW_MIN"}
    assert i1["execution_reject"]["counts_as_trade"] is False
    assert i1["execution_reject"]["starts_cooldown"] is False


def test_a_rejected_execution_is_not_a_trade(strategy, backtest):
    """A tiny account produces signals it cannot size; those are rejects, not HOLDs."""
    grid = _tiny_run_grid()
    signals = S.build(grid, strategy)
    if not signals.bar_eligible.any():
        pytest.skip("synthetic grid produced no signal")
    tiny = dict(backtest.account)
    tiny["starting_capital_krw"] = "1000"
    patched = type(backtest)(doc={**backtest.doc, "account": tiny}, sha256=backtest.sha256,
                             i1=backtest.i1, i1_sha256=backtest.i1_sha256,
                             strategy=backtest.strategy)
    result = EX.run_arm(grid, patched, signals, "A-MAIN")
    assert result.signals > 0
    assert result.trades == []
    assert result.rejects
    assert {r["code"] for r in result.rejects} <= {EX.REJECT_QTY, EX.REJECT_NOTIONAL,
                                                   EX.REJECT_ENGINE}


def test_rejects_do_not_start_a_cooldown(strategy, backtest):
    """Two consecutive eligible bars must both be able to reject; a cooldown would hide one."""
    grid = _tiny_run_grid()
    signals = S.build(grid, strategy)
    if signals.bar_eligible.sum() < 2:
        pytest.skip("need at least two eligible bars")
    tiny = dict(backtest.account)
    tiny["starting_capital_krw"] = "1000"
    patched = type(backtest)(doc={**backtest.doc, "account": tiny}, sha256=backtest.sha256,
                             i1=backtest.i1, i1_sha256=backtest.i1_sha256,
                             strategy=backtest.strategy)
    result = EX.run_arm(grid, patched, signals, "A-MAIN")
    # Every signal is accounted for: each one either rejected at execution or ran out of window
    # before its 4 hour exit. None was swallowed by a cooldown, which a reject must not start.
    assert len(result.rejects) + result.excluded_unfinished == result.signals
    assert len(result.rejects) > 1


def _tiny_run_grid() -> dict:
    """A synthetic grid shaped to produce signals, with the extra columns the harness needs.

    The basis has to vary. With `index == close` the perp-index gap is a flat zero, every bar
    lands in the top bucket, and M1 blocks the lot, so the fixture would silently test nothing.
    """
    grid = synthetic_grid(days=66, oi_slope=-2e-7, vol=1.4e-3)
    n = len(grid["ts"])
    # Shift the grid so trading lands inside the contracted OOS window: 32 days of bucket and
    # volatility warm-up before 2022-01-01, then a month the harness will actually trade.
    oos_start = 1_640_995_200_000          # 2022-01-01T00:00:00Z
    grid["ts"] = np.arange(n, dtype=np.int64) * 60_000 + (oos_start - 32 * 1440 * 60_000)
    rng = np.random.default_rng(20260928)
    basis = rng.normal(0.0, 3e-4, n)
    grid["index_close"] = grid["close"] * np.exp(-basis)
    grid["mark_low"] = grid["close"] * 0.999
    grid["mark_high"] = grid["close"] * 1.001
    grid["oi_record_ts"] = grid["ts"] - 6 * 60_000
    grid["next_funding_ts"] = ((grid["ts"] // (8 * 3_600_000)) + 1) * 8 * 3_600_000
    grid["funding_ts"] = np.arange(grid["ts"][0], grid["ts"][-1], 8 * 3_600_000, dtype=np.int64)
    grid["funding_rate"] = np.zeros(len(grid["funding_ts"]))
    assert len(grid["open"]) == n
    return grid
