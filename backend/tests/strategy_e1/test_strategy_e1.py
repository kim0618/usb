"""Unit, edge-case and determinism tests for the Strategy E1 premarket modules.

Every test runs on a synthetic tape or a synthetic row table. None touches the workspace, the
frozen snapshot, the live minute tape or the network.
"""

from datetime import date

import numpy as np
import pytest

from app.backtest.strategy_e0_overnight.minute import SymbolTape, ordinal
from app.backtest.strategy_e1_premarket import evaluate, gate, pit_audit
from app.backtest.strategy_e1_premarket import premarket as P
from app.backtest.strategy_e1_premarket.config import (
    DECLARED_RULES_CHECKSUM, RulesChanged, canonical_checksum, load_rules,
)

DAY = date(2026, 3, 5)


def _tape(bars: dict[int, float], *, symbol: str = "AAA", volume: float = 100.0,
          days: tuple[date, ...] = (DAY,)) -> SymbolTape:
    minutes, values, ordinals = [], [], []
    for day in days:
        for m in sorted(bars):
            minutes.append(m)
            values.append(bars[m])
            ordinals.append(ordinal(day))
    minute = np.array(minutes, dtype=np.int32)
    price = np.array(values, dtype=float)
    return SymbolTape(symbol=symbol, et_day=np.array(ordinals, dtype=np.int64), minute=minute,
                      open=price.copy(), high=price * 1.001, low=price * 0.999, close=price,
                      volume=np.full(minute.size, volume), vwap=price.copy(),
                      sources={}, overlap_sessions=0)


def _full_session(pm: dict[int, float] | None = None) -> dict[int, float]:
    bars = {m: 100.0 for m in range(P.OPEN_MIN, P.OPEN_MIN + 20)}
    bars.update(pm or {240: 99.0, 480: 99.5, 540: 100.0, 560: 100.5})
    return bars


# -- declaration -----------------------------------------------------------------------------

def test_declared_rules_still_hash_to_the_frozen_checksum():
    rules = load_rules()
    assert rules.checksum == DECLARED_RULES_CHECKSUM
    assert canonical_checksum(rules.raw) == DECLARED_RULES_CHECKSUM


def test_load_rules_refuses_an_edited_declaration(tmp_path):
    import json
    rules = load_rules()
    edited = json.loads(json.dumps(rules.raw))
    edited["gate"]["pass_requires_all"]["min_mean_lift"] = 0.0
    path = tmp_path / "edited.json"
    path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(RulesChanged):
        load_rules(path)


def test_declared_hypotheses_are_transcribed_one_for_one():
    declared = load_rules().hypotheses
    assert set(declared) == set(evaluate.HYPOTHESES)
    assert declared["H2"]["rule"] == "premarket_gap > 0 and premarket_rvol >= 3.0"
    assert declared["H3"]["rule"] == ("position_in_premarket_range >= 0.8 "
                                      "and return_last30m > 0")


def test_primary_label_is_the_declared_one():
    from app.backtest.strategy_e1_premarket.run import PRIMARY
    assert load_rules().primary_label.lower() == PRIMARY.lower()


# -- the decision-time boundary ---------------------------------------------------------------

def test_no_feature_reads_a_bar_at_or_after_0925():
    """The 09:25..09:29 bars and the whole regular session must be invisible to the features."""
    bars = _full_session()
    bars.update({565: 999.0, 569: 999.0})     # 09:25 and 09:29, must not be seen
    rows = P.session_rows(_tape(bars))
    block = rows[ordinal(DAY)].premarket
    assert block["pm_last_price"] == pytest.approx(100.5)
    assert block["pm_high"] == pytest.approx(100.5 * 1.001)
    assert block["pm_bars"] == 4.0


def test_window_return_needs_two_bars_in_the_window():
    single = _full_session({240: 99.0, 480: 99.5, 545: 100.0})   # one bar in 09:00..09:24
    block = P.session_rows(_tape(single))[ordinal(DAY)].premarket
    assert not np.isfinite(block["return_0900_0925"])
    pair = _full_session({240: 99.0, 480: 99.5, 545: 100.0, 560: 101.0})
    block = P.session_rows(_tape(pair))[ordinal(DAY)].premarket
    assert block["return_0900_0925"] == pytest.approx(101.0 / 100.0 - 1.0)


def test_earlier_decision_time_sees_less():
    """At 09:00 the last closed bar is the one starting at 08:59, so 09:00 itself is invisible."""
    bars = _full_session({240: 99.0, 480: 99.5, 540: 100.0, 560: 105.0})
    late = P.session_rows(_tape(bars), decision_last=P.DECISION_TIMES["0925"])
    early = P.session_rows(_tape(bars), decision_last=P.DECISION_TIMES["0900"])
    assert late[ordinal(DAY)].premarket["pm_last_price"] == pytest.approx(105.0)
    assert early[ordinal(DAY)].premarket["pm_last_price"] == pytest.approx(99.5)


def test_window_return_is_truncated_at_the_decision_time():
    """A window return always ends at the decision time, so the same name means less at 09:15."""
    bars = _full_session({240: 99.0, 480: 99.5, 541: 100.0, 550: 101.0, 560: 105.0})
    late = P.session_rows(_tape(bars), decision_last=P.DECISION_TIMES["0925"])
    mid = P.session_rows(_tape(bars), decision_last=P.DECISION_TIMES["0915"])
    early = P.session_rows(_tape(bars), decision_last=P.DECISION_TIMES["0900"])
    assert late[ordinal(DAY)].premarket["return_0900_0925"] == pytest.approx(105.0 / 100.0 - 1.0)
    assert mid[ordinal(DAY)].premarket["return_0900_0925"] == pytest.approx(101.0 / 100.0 - 1.0)
    # The 09:00 decision time cannot see any 09:00+ bar, so the feature has no value at all.
    assert not np.isfinite(early[ordinal(DAY)].premarket["return_0900_0925"])


def test_pit_decision_boundary_audit_passes_on_the_real_builder():
    tape = _tape(_full_session(), days=(DAY,))
    result = pit_audit.decision_boundary([tape])
    assert result["verdict"] == "PASS"
    assert result["sessions_compared"] == 1


def test_pit_decision_boundary_audit_catches_a_planted_leak(monkeypatch):
    """A premarket feature that reads the 09:30 open must make the audit fail."""
    real = P.premarket_block

    def leaky(minute, open_, high, low, close, volume, vwap, decision_last=P.DECISION_LAST_BAR):
        block = real(minute, open_, high, low, close, volume, vwap, decision_last)
        if block is None:
            return None
        anchor = np.flatnonzero(minute == P.OPEN_MIN)
        if anchor.size:
            block["pm_last_price"] = float(open_[anchor[0]])   # look-ahead, on purpose
        return block

    monkeypatch.setattr(P, "premarket_block", leaky)
    result = pit_audit.decision_boundary([_tape(_full_session())])
    assert result["verdict"] == "FAIL"
    assert "pm_last_price" in result["mismatched_features"]


def test_join_boundary_audit_rejects_an_off_by_one():
    grid = [date(2026, 3, 3), date(2026, 3, 4), date(2026, 3, 5)]
    sessions = np.array([date(2026, 3, 5)], dtype=object)
    good = pit_audit.join_boundary(sessions, grid, np.array([1]), grid)
    assert good["verdict"] == "PASS"
    bad = pit_audit.join_boundary(sessions, grid, np.array([2]), grid)
    assert bad["verdict"] == "FAIL" and bad["wrong"] == 1


# -- labels ----------------------------------------------------------------------------------

def test_labels_are_anchored_on_the_0930_open():
    bars = _full_session()
    bars[P.OPEN_MIN] = 100.0
    bars[P.OPEN_MIN + 4] = 102.0
    bars[P.OPEN_MIN + 14] = 103.0
    block = P.session_rows(_tape(bars))[ordinal(DAY)].opening
    out = P.labels(block)
    assert out["R_5m"] == pytest.approx(102.0 / 100.0 - 1.0)
    assert out["R_15m"] == pytest.approx(103.0 / 100.0 - 1.0)
    assert out["MFE_15m"] >= out["R_15m"]
    assert out["MAE_15m"] <= 0.0


def test_strict_reading_is_undefined_when_the_exact_bar_is_absent():
    bars = {m: 100.0 for m in (240, 480, 540, 560)}
    bars.update({P.OPEN_MIN: 100.0, P.OPEN_MIN + 2: 101.0})   # no 09:34, no 09:44
    out = P.labels(P.session_rows(_tape(bars))[ordinal(DAY)].opening)
    assert out["R_5m"] == pytest.approx(101.0 / 100.0 - 1.0)
    assert not np.isfinite(out["R_5m_strict"])


def test_premarket_rvol_uses_only_prior_sessions():
    days = tuple(date(2026, 3, d) for d in (2, 3, 4, 5, 6, 9, 10))
    tape = _tape(_full_session(), days=days)
    rows = P.session_rows(tape, rvol_minimum=2)
    ordered = [rows[ordinal(d)].pm_rvol for d in days]
    assert not np.isfinite(ordered[0]) and not np.isfinite(ordered[1])
    assert all(np.isfinite(x) for x in ordered[2:])
    assert ordered[-1] == pytest.approx(1.0)


# -- masks and statistics ----------------------------------------------------------------------

def _features(**overrides):
    base = {
        "premarket_gap": np.array([0.02, -0.01, 0.03, 0.04]),
        "premarket_rvol": np.array([5.0, 5.0, 1.0, np.nan]),
        "position_in_premarket_range": np.array([0.9, 0.9, 0.5, 0.85]),
        "return_0900_0925": np.array([0.01, 0.01, 0.01, np.nan]),
        "return_last30m": np.array([0.01, -0.01, 0.01, 0.01]),
        "relative_strength_vs_spy": np.array([0.01, 0.01, -0.01, 0.01]),
        "premarket_dollar_volume": np.array([2e6, 2e6, 2e6, 5e5]),
    }
    base.update(overrides)
    return base


def test_masks_match_the_declaration():
    f = _features()
    assert evaluate.mask("H1", f).tolist() == [True, False, True, False]
    assert evaluate.mask("H2", f).tolist() == [True, False, False, False]
    assert evaluate.mask("H3", f).tolist() == [True, False, False, True]
    assert evaluate.mask("H4", f).tolist() == [True, False, False, False]
    assert evaluate.mask("H5", f).tolist() == [True, False, False, False]


def test_undefined_feature_excludes_the_row_rather_than_counting_as_zero():
    f = _features(premarket_rvol=np.array([np.nan, np.nan, np.nan, np.nan]))
    assert not evaluate.mask("H2", f).any()
    assert not evaluate.mask("H5", f).any()


def test_core_comparison_sets_separate_gap_volume_and_strength():
    f = _features()
    assert evaluate.mask("gap_only", f).tolist() == [True, False, True, True]
    assert evaluate.mask("gap_plus_volume", f).tolist() == evaluate.mask("H2", f).tolist()
    assert evaluate.mask("gap_plus_closing_strength", f).tolist() == [True, False, False, True]


def test_half_percent_thresholds_are_named_for_what_they_are():
    rules = load_rules()
    block = evaluate.summarise(np.array([-0.02, -0.006, 0.0, 0.004, 0.007, 0.03]), rules)
    assert block["p_ge_0_5pct"] == pytest.approx(2 / 6)
    assert block["p_le_minus_0_5pct"] == pytest.approx(2 / 6)
    assert "p_gap_ge_0pct" not in block


def test_volatility_selector_is_detected():
    baseline = {"n": 100, "mean": 0.0, "median": 0.0, "win_rate": 0.5,
                "p_gap_ge_1pct": 0.10, "p_gap_le_minus_1pct": 0.10}
    candidate = {"n": 50, "mean": 0.002, "median": -0.001, "win_rate": 0.48,
                 "p_gap_ge_1pct": 0.25, "p_gap_le_minus_1pct": 0.24}
    lifts = evaluate.lift(candidate, baseline)
    assert evaluate.volatility_selector(candidate, baseline, lifts) is True
    directional = dict(candidate, median=0.002, p_gap_le_minus_1pct=0.05)
    assert evaluate.volatility_selector(directional, baseline,
                                        evaluate.lift(directional, baseline)) is False


# -- gate ------------------------------------------------------------------------------------

def _candidate(**overrides):
    block = {
        "name": "X",
        "summary": {"n": 5_000, "mean": 0.003, "median": 0.001, "win_rate": 0.55,
                    "p_gap_ge_1pct": 0.2, "p_gap_le_minus_1pct": 0.05},
        "lift": {"mean": 0.0025, "median": 0.0008, "win_rate": 0.05, "downside_ratio": 1.1},
        "quarterly": [{"quarter": f"2025Q{i}", "n": 10, "lift": {"mean": 0.002}}
                      for i in range(1, 9)],
        "extreme_removal": [{"removal": "drop_top_1pct", "mean_lift": 0.001}],
        "symbol_concentration": {"top_share_of_total_excess": 0.2},
        "bootstrap": {"mean_lift_ci": [0.0005, 0.004]},
        "volatility_selector": False,
    }
    block.update(overrides)
    return block


def test_gate_passes_only_when_every_criterion_holds():
    result = gate.evaluate(_candidate(), load_rules())
    assert result["verdict"] == gate.PASS and result["failed"] == []


def test_gate_refuses_a_volatility_selector_even_with_every_number_met():
    result = gate.evaluate(_candidate(volatility_selector=True), load_rules())
    assert result["verdict"] == gate.FAIL
    assert result["volatility_selector"] is True
    assert result["failed"] == []


@pytest.mark.parametrize("field, value", [
    ("lift", {"mean": 0.0001, "median": 0.0008, "win_rate": 0.05, "downside_ratio": 1.1}),
    ("lift", {"mean": 0.0025, "median": 0.0008, "win_rate": 0.05, "downside_ratio": 3.0}),
    ("symbol_concentration", {"top_share_of_total_excess": 0.9}),
    ("extreme_removal", [{"removal": "drop_top_1pct", "mean_lift": -0.01}]),
])
def test_gate_refuses_when_one_criterion_fails(field, value):
    result = gate.evaluate(_candidate(**{field: value}), load_rules())
    assert result["verdict"] != gate.PASS and result["failed"]


def test_gate_is_inconclusive_when_both_secondary_horizons_pass():
    rules = load_rules()
    weak = _candidate(lift={"mean": 0.0001, "median": 0.0, "win_rate": 0.0,
                            "downside_ratio": 1.0},
                      bootstrap={"mean_lift_ci": [-0.001, 0.004]})
    secondary = {"R_1m": _candidate(), "R_15m": _candidate()}
    assert gate.evaluate(weak, rules, secondary)["verdict"] == gate.INCONCLUSIVE
    assert gate.evaluate(weak, rules, None)["verdict"] == gate.FAIL


def test_empty_candidate_is_a_fail_not_a_crash():
    result = gate.evaluate({"name": "empty", "summary": {"n": 0}}, load_rules())
    assert result["verdict"] == gate.FAIL


def test_combine_reports_the_strongest_outcome():
    assert gate.combine([{"verdict": "FAIL"}, {"verdict": "INCONCLUSIVE"}]) == gate.INCONCLUSIVE
    assert gate.combine([{"verdict": "FAIL"}, {"verdict": "PASS"}]) == gate.PASS
    assert gate.combine([{"verdict": "FAIL"}]) == gate.FAIL
