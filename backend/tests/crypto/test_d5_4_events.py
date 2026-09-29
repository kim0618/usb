"""Event detection, merging, cooldown and PIT safety.

The merge rule is the idea that separates D5.4 from D5: a condition holding for thirty minutes is
one event, not thirty signals. Everything downstream counts episodes, so the rule is tested here
on hand-built masks where the right answer is obvious, rather than inferred from a backtest.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from app.crypto.research.d5_4 import events as EV
from app.crypto.research.d5_4 import runner as R

from tests.crypto.d6_fixtures import synthetic_grid


@pytest.fixture(scope="module")
def contract():
    return R.load_contract()


# --- contract -------------------------------------------------------------------------------

def test_contract_hash_is_checked(contract):
    assert contract["record"] == "CRYPTO_D5_4_COST_FIRST_EVENT_CONTRACT_V1"
    freeze = json.loads(R.FREEZE.read_text())
    assert R.sha256_file(R.CONTRACT_JSON) == freeze["machine_readable_sha256"]
    assert R.sha256_file(R.CONTRACT_MD) == freeze["sha256"]


def test_a_tampered_contract_is_refused(tmp_path, contract, monkeypatch):
    edited = tmp_path / "contract.json"
    doc = json.loads(json.dumps(contract))
    doc["candidates"][0]["condition"]["z_disp15_max"] = -3.0
    edited.write_text(json.dumps(doc))
    monkeypatch.setattr(R, "CONTRACT_JSON", edited)
    with pytest.raises(R.ContractHashMismatch):
        R.load_contract()


def test_e2_cannot_be_revived_by_editing_the_json(tmp_path, contract, monkeypatch):
    """The cost-first screen is a rule, not advice: the runner refuses a contract that unrejects E2."""
    edited = tmp_path / "contract.json"
    doc = json.loads(json.dumps(contract))
    doc["families"]["E2"]["status"] = "PASSED_COST_SCREEN"
    edited.write_text(json.dumps(doc))
    monkeypatch.setattr(R, "CONTRACT_JSON", edited)
    monkeypatch.setattr(R, "sha256_file", lambda p: json.loads(R.FREEZE.read_text())[
        "machine_readable_sha256" if p == edited else "sha256"])
    with pytest.raises(RuntimeError, match="E2 must stay rejected"):
        R.load_contract()


def test_e2_is_rejected_at_contract_stage_with_a_reason(contract):
    e2 = contract["families"]["E2"]
    assert e2["status"] == "REJECTED_AT_CONTRACT"
    assert e2["backtested"] is False
    assert "cost" in e2["reason"].lower()


def test_four_candidates_and_no_e2_among_them(contract):
    assert len(contract["candidates"]) == 4
    assert {c["family"] for c in contract["candidates"]} == {"E1", "E3", "E4"}
    assert all(c["family"] != "E2" for c in contract["candidates"])


def test_prefreeze_declares_no_returns(contract):
    block = contract["prefreeze_computation"]
    assert block["returns_computed"] is False
    assert block["pnl_computed"] is False
    assert block["trades_simulated"] is False


def test_the_cost_first_screen_is_stated_with_its_derivation(contract):
    screen = contract["cost_first"]
    assert screen["roundtrip_cost_bp"] == 11.25
    assert "6x" in screen["screen_rule"]
    assert screen["applied_before_backtest"] is True
    assert "22.5" in screen["screen_derivation"]


def test_threshold_ladder_was_fixed_before_measurement(contract):
    block = contract["threshold_selection"]
    assert block["ladder"] == [4, 5, 6, 7, 8, 10]
    assert block["ladder_fixed_before_measurement"] is True
    assert block["pnl_used"] is False
    assert block["rule"].startswith("the most extreme")


# --- merge ----------------------------------------------------------------------------------

def mask_from(bits: str) -> np.ndarray:
    return np.array([c == "1" for c in bits])


def test_a_run_of_consecutive_bars_is_one_event():
    events = EV.merge(mask_from("0011111000"), cooldown_bars=0)
    assert len(events) == 1
    assert (events[0].start, events[0].end) == (2, 6)


def test_thirty_consecutive_bars_are_not_thirty_signals():
    events = EV.merge(mask_from("0" + "1" * 30 + "0"), cooldown_bars=0)
    assert len(events) == 1


def test_separate_runs_are_separate_events_without_a_cooldown():
    events = EV.merge(mask_from("110011"), cooldown_bars=0)
    assert [(e.start, e.end) for e in events] == [(0, 1), (4, 5)]


def test_the_cooldown_swallows_the_next_run():
    events = EV.merge(mask_from("110011"), cooldown_bars=10)
    assert [(e.start, e.end) for e in events] == [(0, 1)]


def test_the_cooldown_is_measured_from_the_end_of_the_run():
    # run ends at index 1; cooldown 2 means the next event may start at index 4
    assert [(e.start, e.end) for e in EV.merge(mask_from("1100100"), cooldown_bars=2)] == \
        [(0, 1), (4, 4)]
    assert [(e.start, e.end) for e in EV.merge(mask_from("1101000"), cooldown_bars=2)] == \
        [(0, 1)]


def test_an_empty_mask_produces_no_events():
    assert EV.merge(np.zeros(100, dtype=bool), cooldown_bars=5) == []


def test_a_fully_true_mask_is_one_event():
    events = EV.merge(np.ones(500, dtype=bool), cooldown_bars=240)
    assert len(events) == 1
    assert (events[0].start, events[0].end) == (0, 499)


def test_events_come_back_in_time_order():
    rng = np.random.default_rng(7)
    events = EV.merge(rng.random(5_000) < 0.02, cooldown_bars=50)
    starts = [e.start for e in events]
    assert starts == sorted(starts)


# --- robust z -------------------------------------------------------------------------------

def test_robust_z_uses_only_earlier_days():
    grid = synthetic_grid(days=35)
    values = np.arange(len(grid["ts"]), dtype=float)
    full = EV.robust_z(values, grid["ts"])
    tampered = values.copy()
    cut = 33 * EV.DAY_BARS
    tampered[cut:] *= 1000
    partial = EV.robust_z(tampered, grid["ts"])
    np.testing.assert_allclose(full[:cut], partial[:cut], equal_nan=True)


def test_robust_z_excludes_the_scored_day_from_its_own_statistics():
    grid = synthetic_grid(days=32)
    values = np.zeros(len(grid["ts"]))
    day = 31
    values[day * EV.DAY_BARS:(day + 1) * EV.DAY_BARS] = 100.0
    z = EV.robust_z(values, grid["ts"])
    # The previous 30 days are all zero, so MAD is zero and the day is left unscored rather than
    # divided by nothing.
    assert np.isnan(z[day * EV.DAY_BARS])


def test_robust_z_is_nan_before_the_window_is_full():
    grid = synthetic_grid(days=32)
    rng = np.random.default_rng(3)
    z = EV.robust_z(rng.normal(size=len(grid["ts"])), grid["ts"])
    assert np.isnan(z[:30 * EV.DAY_BARS]).all()
    assert np.isfinite(z[31 * EV.DAY_BARS:]).any()


def test_robust_z_scales_like_a_sigma():
    grid = synthetic_grid(days=33)
    rng = np.random.default_rng(11)
    values = rng.normal(0.0, 2.0, len(grid["ts"]))
    z = EV.robust_z(values, grid["ts"])
    finite = z[np.isfinite(z)]
    assert 0.9 < float(np.std(finite)) < 1.15


def test_a_flat_series_has_no_z():
    grid = synthetic_grid(days=32)
    z = EV.robust_z(np.ones(len(grid["ts"])), grid["ts"])
    assert np.isnan(z).all()


# --- condition building -----------------------------------------------------------------------

def test_condition_keys_are_read_literally():
    features = {"disp15": np.array([-1.0, 0.0, 1.0]), "volratio": np.array([5.0, 1.0, 5.0])}
    z = {"disp15": np.array([-6.0, 0.0, 6.0])}
    mask = EV.condition_mask(z, features, {"z_disp15_max": -5.0, "volratio_min": 4.0})
    np.testing.assert_array_equal(mask, [True, False, False])


def test_a_condition_key_without_min_or_max_is_refused():
    features = {"disp15": np.zeros(3)}
    with pytest.raises(ValueError, match="_min or _max"):
        EV.condition_mask({"disp15": np.zeros(3)}, features, {"z_disp15": 1.0})


def test_every_contracted_condition_builds(contract):
    grid = _grid_with_columns()
    for candidate in contract["candidates"]:
        signals = EV.build(grid, candidate, cooldown_bars=240)
        assert signals.candidate_id == candidate["id"]
        assert signals.side == candidate["side"]


def test_relaxing_a_threshold_can_only_add_events(contract):
    grid = _grid_with_columns()
    candidate = contract["candidates"][0]
    base = EV.build(grid, candidate, cooldown_bars=240)
    override = R.relax_threshold(candidate)
    looser = EV.build(grid, candidate, cooldown_bars=240, threshold_override=override)
    assert override == {"z_disp15_max": -8.0}
    assert len(looser.events) >= len(base.events)


def test_the_short_candidate_uses_the_opposite_sign(contract):
    long_candidate = next(c for c in contract["candidates"] if c["id"] == "C1")
    short_candidate = next(c for c in contract["candidates"] if c["id"] == "C2")
    assert long_candidate["condition"]["z_disp15_max"] == -10.0
    assert short_candidate["condition"]["z_disp15_min"] == 10.0
    assert short_candidate["side"] == "SHORT"
    assert "short_justification" in short_candidate


def _grid_with_columns() -> dict[str, np.ndarray]:
    grid = synthetic_grid(days=40, oi_slope=-2e-7, vol=1.2e-3)
    n = len(grid["ts"])
    rng = np.random.default_rng(20260928)
    grid["index_close"] = grid["close"] * np.exp(-rng.normal(0.0, 3e-4, n))
    grid["mark_low"] = grid["close"] * 0.998
    grid["mark_high"] = grid["close"] * 1.002
    grid["mark_close"] = grid["close"]
    grid["funding_ts"] = np.arange(grid["ts"][0], grid["ts"][-1], 8 * 3_600_000, dtype=np.int64)
    grid["funding_rate"] = np.zeros(len(grid["funding_ts"]))
    return grid
