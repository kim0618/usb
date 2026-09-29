"""The event lifecycle, the three confirmation detectors, and PIT safety.

The lifecycle is the point of D5.5, so the tests build price paths where the right answer is
obvious by construction: a path that breaks out, a path that retraces, a path that does neither.
`NO_TRADE` is asserted as a first-class outcome rather than treated as a gap in coverage.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from app.crypto.research.d5_4.events import Event
from app.crypto.research.d5_5 import confirmation as CF
from app.crypto.research.d5_5 import runner as R

MIN_MARGIN = 22.5 / 1e4


@pytest.fixture(scope="module")
def contract():
    return R.load_contract()


def path(closes, *, lows=None, highs=None, opens=None) -> dict[str, np.ndarray]:
    """A bar grid built from a close path; extremes default to the closes."""
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    return {"ts": np.arange(n, dtype=np.int64) * 60_000,
            "close": closes,
            "open": np.asarray(opens if opens is not None else closes, dtype=float),
            "mark_low": np.asarray(lows if lows is not None else closes, dtype=float),
            "mark_high": np.asarray(highs if highs is not None else closes, dtype=float)}


def event_at(start: int, end: int) -> Event:
    return Event(start=start, end=end, displacement=0.05)


# --- contract -----------------------------------------------------------------------------

def test_contract_hash_is_checked(contract):
    assert contract["record"] == "CRYPTO_D5_5_DIRECTION_CONFIRMATION_CONTRACT_V1"
    freeze = json.loads(R.FREEZE.read_text())
    assert R.sha256_file(R.CONTRACT_JSON) == freeze["machine_readable_sha256"]
    assert R.sha256_file(R.CONTRACT_MD) == freeze["sha256"]


def test_a_tampered_contract_is_refused(tmp_path, contract, monkeypatch):
    edited = tmp_path / "contract.json"
    doc = json.loads(json.dumps(contract))
    doc["confirmation_common"]["window_bars"] = 90
    edited.write_text(json.dumps(doc))
    monkeypatch.setattr(R, "CONTRACT_JSON", edited)
    with pytest.raises(R.ContractHashMismatch):
        R.load_contract()


def test_the_event_detector_must_stay_the_frozen_one(tmp_path, contract, monkeypatch):
    edited = tmp_path / "contract.json"
    doc = json.loads(json.dumps(contract))
    doc["event_source"]["definition"] = "abs(z(disp15)) >= 8.0 AND z(oichg15) <= -2.5"
    edited.write_text(json.dumps(doc))
    monkeypatch.setattr(R, "CONTRACT_JSON", edited)
    monkeypatch.setattr(R, "sha256_file", lambda p: json.loads(R.FREEZE.read_text())[
        "machine_readable_sha256" if p == edited else "sha256"])
    with pytest.raises(RuntimeError, match="frozen D5.4 one"):
        R.load_contract()


def test_the_symmetric_bracket_cannot_be_reinstated(tmp_path, contract, monkeypatch):
    edited = tmp_path / "contract.json"
    doc = json.loads(json.dumps(contract))
    doc["exits"]["symmetric_bracket_reused"] = True
    edited.write_text(json.dumps(doc))
    monkeypatch.setattr(R, "CONTRACT_JSON", edited)
    monkeypatch.setattr(R, "sha256_file", lambda p: json.loads(R.FREEZE.read_text())[
        "machine_readable_sha256" if p == edited else "sha256"])
    with pytest.raises(RuntimeError, match="symmetric bracket"):
        R.load_contract()


def test_event_source_is_the_d5_4_detector_unioned(contract):
    source = contract["event_source"]
    assert source["definition"] == "abs(z(disp15)) >= 10.0 AND z(oichg15) <= -2.5"
    assert source["direction_agnostic"] is True
    assert "d5_4" in source["code_reused"]
    assert source["excluded"]["E2"].startswith("already rejected")


def test_three_candidates_all_sharing_one_event_source(contract):
    assert len(contract["candidates"]) == 3
    assert [c["id"] for c in contract["candidates"]] == ["A", "B", "C"]
    for candidate in contract["candidates"]:
        assert candidate["sides"] == {"LONG": True, "SHORT": True}


def test_prefreeze_declares_nothing_after_confirmation_was_examined(contract):
    block = contract["prefreeze_computation"]
    assert block["returns_computed"] is False
    assert block["pnl_computed"] is False
    assert block["path_after_confirmation_examined"] is False


def test_s10_is_the_stated_core_gate(contract):
    rules = {r["id"]: r["rule"] for r in contract["verdict_rules"]["SURVIVE"]}
    assert "stop-out share" in rules["S10"]
    assert "coin-flip" not in rules["S10"]  # the rationale lives beside it, not inside it
    assert "D5.4" in contract["verdict_rules"]["S10_rationale"]


# --- shock window -------------------------------------------------------------------------

def test_the_shock_window_reaches_back_before_the_event():
    grid = path(np.arange(100, 200, dtype=float))
    window = CF.shock_window(grid, event_at(50, 52))
    assert window.start == 50 - CF.SHOCK_LOOKBACK
    assert window.end == 52
    assert window.pre_price == grid["close"][36]


def test_the_shock_window_clamps_at_the_start_of_the_grid():
    grid = path(np.arange(100, 130, dtype=float))
    window = CF.shock_window(grid, event_at(3, 5))
    assert window.start == 0


def test_the_shock_range_spans_the_window_extremes():
    closes = np.full(60, 100.0)
    closes[40:46] = 90.0            # the shock
    grid = path(closes)
    window = CF.shock_window(grid, event_at(45, 46))
    assert window.high == 100.0 and window.low == 90.0
    assert window.range_fraction == pytest.approx(10.0 / 95.0)


# --- candidate A: breakout ------------------------------------------------------------------

def breakout_grid(after: list[float]) -> dict[str, np.ndarray]:
    closes = [100.0] * 30 + [90.0] * 2 + after
    return path(closes)


def test_a_confirms_long_on_an_upside_break():
    grid = breakout_grid([100.0, 101.0, 120.0])
    result = CF.confirm_breakout(grid, event_at(31, 31), window_bars=10,
                                 margin_fraction=0.25, min_margin=MIN_MARGIN)
    assert result.state == CF.CONFIRMED_LONG
    assert result.side == CF.LONG
    assert result.stop_price == 100.0          # the broken level
    assert result.target_price is None


def test_a_confirms_short_on_a_downside_break():
    grid = breakout_grid([89.0, 88.0, 70.0])
    result = CF.confirm_breakout(grid, event_at(31, 31), window_bars=10,
                                 margin_fraction=0.25, min_margin=MIN_MARGIN)
    assert result.state == CF.CONFIRMED_SHORT
    assert result.stop_price == 90.0


def test_a_expires_when_price_stays_inside_the_range():
    grid = breakout_grid([95.0] * 10)
    result = CF.confirm_breakout(grid, event_at(31, 31), window_bars=10,
                                 margin_fraction=0.25, min_margin=MIN_MARGIN)
    assert result.state == CF.EXPIRED
    assert result.confirmed is False
    assert result.side is None


def test_a_needs_the_margin_not_just_the_level():
    """Touching the range edge is not a break; the margin has to be cleared."""
    grid = breakout_grid([100.5] * 10)          # above 100 but far below 100 * (1 + 0.25R)
    result = CF.confirm_breakout(grid, event_at(31, 31), window_bars=10,
                                 margin_fraction=0.25, min_margin=MIN_MARGIN)
    assert result.state == CF.EXPIRED


def test_a_respects_the_cost_floor_when_the_range_is_tiny():
    closes = [100.0] * 30 + [99.99] * 2 + [100.05] * 5
    grid = path(closes)
    result = CF.confirm_breakout(grid, event_at(31, 31), window_bars=5,
                                 margin_fraction=0.25, min_margin=MIN_MARGIN)
    assert result.state == CF.EXPIRED          # 5bp above is inside the 22.5bp floor


def test_a_stops_looking_when_the_window_closes():
    grid = breakout_grid([95.0] * 5 + [150.0])
    inside = CF.confirm_breakout(grid, event_at(31, 31), window_bars=10,
                                 margin_fraction=0.25, min_margin=MIN_MARGIN)
    outside = CF.confirm_breakout(grid, event_at(31, 31), window_bars=3,
                                  margin_fraction=0.25, min_margin=MIN_MARGIN)
    assert inside.confirmed and outside.state == CF.EXPIRED


def test_a_takes_whichever_side_breaks_first():
    grid = breakout_grid([70.0, 130.0])
    result = CF.confirm_breakout(grid, event_at(31, 31), window_bars=10,
                                 margin_fraction=0.25, min_margin=MIN_MARGIN)
    assert result.side == CF.SHORT and result.bar == 32


# --- candidate B: retracement ----------------------------------------------------------------

def test_b_confirms_long_after_half_the_drop_is_given_back():
    closes = [100.0] * 30 + [90.0] * 2 + [92.0, 96.0]
    grid = path(closes)
    result = CF.confirm_retracement(grid, event_at(31, 31), window_bars=10,
                                    retrace_fraction=0.5, min_margin=MIN_MARGIN,
                                    displacement_sign=-1.0)
    assert result.state == CF.CONFIRMED_LONG
    assert result.level == pytest.approx(95.0)      # 90 + 0.5 * (100 - 90)
    assert result.stop_price == 90.0                # the shock low
    assert result.target_price == 100.0             # the pre-shock price
    assert result.bar == 33


def test_b_confirms_short_after_half_the_rally_is_given_back():
    closes = [100.0] * 30 + [110.0] * 2 + [108.0, 104.0]
    grid = path(closes)
    result = CF.confirm_retracement(grid, event_at(31, 31), window_bars=10,
                                    retrace_fraction=0.5, min_margin=MIN_MARGIN,
                                    displacement_sign=+1.0)
    assert result.state == CF.CONFIRMED_SHORT
    assert result.level == pytest.approx(105.0)
    assert result.stop_price == 110.0
    assert result.target_price == 100.0


def test_b_expires_when_the_retracement_never_arrives():
    closes = [100.0] * 30 + [90.0] * 2 + [90.5] * 10
    grid = path(closes)
    result = CF.confirm_retracement(grid, event_at(31, 31), window_bars=10,
                                    retrace_fraction=0.5, min_margin=MIN_MARGIN,
                                    displacement_sign=-1.0)
    assert result.state == CF.EXPIRED


def test_b_measures_from_the_pre_shock_price_not_a_running_low():
    """The discarded definition bounced off the running low and confirmed 99% of the time.

    Here a new low does not move the level: it stays anchored to the pre-shock price.
    """
    def confirm(grid):
        return CF.confirm_retracement(grid, event_at(31, 31), window_bars=10,
                                      retrace_fraction=0.5, min_margin=MIN_MARGIN,
                                      displacement_sign=-1.0)

    # 94 is short of the level whether or not a new low came first.
    assert confirm(path([100.0] * 30 + [90.0] * 2 + [94.0])).state == CF.EXPIRED
    assert confirm(path([100.0] * 30 + [90.0] * 2 + [80.0, 94.0])).state == CF.EXPIRED

    # 96 clears it, and the level is the same 95 in both paths: the new low did not lower the bar.
    plain = confirm(path([100.0] * 30 + [90.0] * 2 + [96.0]))
    after_new_low = confirm(path([100.0] * 30 + [90.0] * 2 + [80.0, 96.0]))
    assert plain.confirmed and after_new_low.confirmed
    assert plain.level == after_new_low.level == pytest.approx(95.0)
    assert plain.stop_price == after_new_low.stop_price == 90.0


def test_b_deeper_retracement_fraction_is_harder():
    closes = [100.0] * 30 + [90.0] * 2 + [96.0]
    grid = path(closes)
    half = CF.confirm_retracement(grid, event_at(31, 31), window_bars=10,
                                  retrace_fraction=0.5, min_margin=MIN_MARGIN,
                                  displacement_sign=-1.0)
    deep = CF.confirm_retracement(grid, event_at(31, 31), window_bars=10,
                                  retrace_fraction=0.7, min_margin=MIN_MARGIN,
                                  displacement_sign=-1.0)
    assert half.confirmed and deep.state == CF.EXPIRED


# --- candidate C: compression then expansion ---------------------------------------------------

def compression_case(expansion: list[float]):
    closes = [100.0] * 30 + [90.0] * 2 + [95.0] * 20 + expansion
    grid = path(closes)
    rv15 = np.full(len(closes), 1.0)
    rv15[31] = 1.0                 # reference volatility at the event
    rv15[40:] = 0.2                # compressed from bar 40
    return grid, rv15


def test_c_confirms_long_on_expansion_after_compression():
    grid, rv15 = compression_case([120.0])
    result = CF.confirm_compression_expansion(grid, event_at(31, 31), window_bars=40,
                                              compression_fraction=0.5,
                                              min_margin=MIN_MARGIN, rv15=rv15)
    assert result.state == CF.CONFIRMED_LONG
    assert result.detail["compression_bar"] == 40


def test_c_confirms_short_on_a_downside_expansion():
    grid, rv15 = compression_case([70.0])
    result = CF.confirm_compression_expansion(grid, event_at(31, 31), window_bars=40,
                                              compression_fraction=0.5,
                                              min_margin=MIN_MARGIN, rv15=rv15)
    assert result.state == CF.CONFIRMED_SHORT


def test_c_expires_when_volatility_never_compresses():
    grid, rv15 = compression_case([120.0])
    rv15 = np.full(len(rv15), 1.0)
    result = CF.confirm_compression_expansion(grid, event_at(31, 31), window_bars=40,
                                              compression_fraction=0.5,
                                              min_margin=MIN_MARGIN, rv15=rv15)
    assert result.state == CF.EXPIRED


def test_c_expires_when_compression_never_expands():
    grid, rv15 = compression_case([95.0])
    result = CF.confirm_compression_expansion(grid, event_at(31, 31), window_bars=40,
                                              compression_fraction=0.5,
                                              min_margin=MIN_MARGIN, rv15=rv15)
    assert result.state == CF.EXPIRED


def test_c_needs_all_three_stages_inside_the_window():
    grid, rv15 = compression_case([120.0])
    tight = CF.confirm_compression_expansion(grid, event_at(31, 31), window_bars=5,
                                             compression_fraction=0.5,
                                             min_margin=MIN_MARGIN, rv15=rv15)
    assert tight.state == CF.EXPIRED


def test_c_stop_is_the_far_side_of_the_compression_range():
    grid, rv15 = compression_case([120.0])
    result = CF.confirm_compression_expansion(grid, event_at(31, 31), window_bars=40,
                                              compression_fraction=0.5,
                                              min_margin=MIN_MARGIN, rv15=rv15)
    assert result.stop_price == result.detail["compression_low"]
    assert result.target_price is None


# --- PIT ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("detector", ["A", "B", "C"])
def test_confirmation_never_reads_past_the_bar_it_names(detector):
    """Corrupting everything after the confirming bar must not move the confirmation."""
    closes = [100.0] * 30 + [90.0] * 2 + [92.0, 96.0, 99.0] + [95.0] * 20 + [120.0]
    grid = path(closes)
    rv15 = np.full(len(closes), 1.0)
    rv15[40:] = 0.2

    def confirm(g):
        if detector == "A":
            return CF.confirm_breakout(g, event_at(31, 31), window_bars=40,
                                       margin_fraction=0.25, min_margin=MIN_MARGIN)
        if detector == "B":
            return CF.confirm_retracement(g, event_at(31, 31), window_bars=40,
                                          retrace_fraction=0.5, min_margin=MIN_MARGIN,
                                          displacement_sign=-1.0)
        return CF.confirm_compression_expansion(g, event_at(31, 31), window_bars=40,
                                                compression_fraction=0.5,
                                                min_margin=MIN_MARGIN, rv15=rv15)

    before = confirm(grid)
    if not before.confirmed:
        pytest.skip(f"{detector} did not confirm on this path")
    tampered = {k: v.copy() for k, v in grid.items()}
    for key in ("close", "open", "mark_low", "mark_high"):
        tampered[key][before.bar + 1:] *= 5.0
    after = confirm(tampered)
    assert (after.state, after.bar, after.side) == (before.state, before.bar, before.side)


def test_truncating_the_grid_at_the_confirming_bar_keeps_the_answer():
    closes = [100.0] * 30 + [90.0] * 2 + [92.0, 96.0] + [200.0] * 5
    grid = path(closes)
    full = CF.confirm_retracement(grid, event_at(31, 31), window_bars=40,
                                  retrace_fraction=0.5, min_margin=MIN_MARGIN,
                                  displacement_sign=-1.0)
    cut = full.bar + 1
    short = {k: v[:cut] for k, v in grid.items()}
    truncated = CF.confirm_retracement(short, event_at(31, 31), window_bars=40,
                                       retrace_fraction=0.5, min_margin=MIN_MARGIN,
                                       displacement_sign=-1.0)
    assert (truncated.state, truncated.bar) == (full.state, full.bar)


# --- detector binding ---------------------------------------------------------------------------

def test_detector_for_binds_each_candidate(contract):
    grid = path([100.0] * 60)
    features = {"disp15": np.full(60, -0.05), "rv15": np.full(60, 1.0)}
    for candidate in contract["candidates"]:
        detector = CF.detector_for(candidate, grid=grid, features=features,
                                   window_bars=60, min_margin=MIN_MARGIN)
        assert detector(event_at(31, 31)).state == CF.EXPIRED


def test_an_unknown_candidate_is_refused():
    with pytest.raises(ValueError, match="unknown candidate"):
        CF.detector_for({"id": "Z"}, grid=path([1.0]), features={}, window_bars=1,
                        min_margin=MIN_MARGIN)


def test_overrides_reach_the_detector(contract):
    closes = [100.0] * 30 + [90.0] * 2 + [96.0]
    grid = path(closes)
    features = {"disp15": np.full(len(closes), -0.05),
                "rv15": np.full(len(closes), 1.0)}
    candidate = next(c for c in contract["candidates"] if c["id"] == "B")
    base = CF.detector_for(candidate, grid=grid, features=features, window_bars=10,
                           min_margin=MIN_MARGIN)
    strict = CF.detector_for(candidate, grid=grid, features=features, window_bars=10,
                             min_margin=MIN_MARGIN, overrides={"retrace_fraction": 0.9})
    assert base(event_at(31, 31)).confirmed
    assert strict(event_at(31, 31)).state == CF.EXPIRED
