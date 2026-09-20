"""Scanner gate, score and HOD breakout detection (B-F0 sections 4, 5, 6, 7)."""

from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from app.strategy_b.config import ScannerConfig, StrategyBConfig
from app.strategy_b.eligibility import IneligibleReason, evaluate_eligibility
from app.strategy_b.errors import InvalidConfig
from app.strategy_b.models import (
    Availability, CorporateActionFlag, FeatureSnapshot, HaltStatus, Measured, RvolStatus, Session,
    SetupType, TapeDensity,
)
from app.strategy_b.scanner import GateReason, rank, scan
from app.strategy_b.scope import ScopeDecision, ScopeExclusion
from app.strategy_b.session import AggregationScope
from app.strategy_b.setups import SetupRejection, detect_hod_breakout
from tests.strategy_b.fixtures import (
    D, SYMBOL, at, breakout_bars, et, in_scope, make_bar, make_tape,
    passing_snapshot as snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = REPO_ROOT / "docs/backtest/strategy_b/b_fsm_rules_v1.json"
DECLARED_RULES_CHECKSUM = "54c6063015999ac56d33c69c21d1463e68d9727c13d65c3ff8d47db3ec5e2c13"
CONFIG = StrategyBConfig()


def scan_it(snap: FeatureSnapshot, scanner: ScannerConfig = CONFIG.scanner):
    return scan(snap, scanner=scanner, candidate=CONFIG.candidate)


# ---- gate ------------------------------------------------------------------------------

def test_a_gate_pass_scores_the_hand_computed_value() -> None:
    decision = scan_it(snapshot())
    # momentum max(3/2, 1/4, 1/6) = 1.5 -> 0.75; rvol 5/3 -> 0.8333; liquidity 500k/250k = 2 -> 1.0
    assert decision.passed
    assert decision.score == pytest.approx(0.5 * 0.75 + 0.3 * (5 / 3 / 2) + 0.2 * 1.0)
    assert decision.reasons == ()


def test_momentum_legs_are_or_and_liquidity_gates_are_and() -> None:
    only_5m = snapshot(return_1m=Measured.of(0.1), return_3m=Measured.of(0.2),
                       return_5m=Measured.of(6.0))
    assert scan_it(only_5m).passed

    no_leg = snapshot(return_1m=Measured.of(1.9), return_3m=Measured.of(3.9),
                      return_5m=Measured.of(5.9))
    assert scan_it(no_leg).reasons == (GateReason.NO_MOMENTUM_LEG,)

    thin = snapshot(rolling_dollar_volume=Measured.of(249_999.0))
    assert thin.rvol.value == 5.0 and GateReason.DOLLAR_VOLUME_BELOW_MIN in scan_it(thin).reasons


def test_unknown_values_fail_closed_but_partial_rvol_passes() -> None:
    unknown_rvol = snapshot(rvol=Measured.missing(Availability.INSUFFICIENT_HISTORY),
                            rvol_status=RvolStatus.UNKNOWN)
    assert scan_it(unknown_rvol).reasons == (GateReason.RVOL_UNKNOWN,)
    assert scan_it(unknown_rvol).score is None

    no_dollar_volume = snapshot(rolling_dollar_volume=Measured.missing(Availability.NO_SOURCE_VWAP))
    assert scan_it(no_dollar_volume).reasons == (GateReason.DOLLAR_VOLUME_UNKNOWN,)

    partial = snapshot(rvol_status=RvolStatus.PARTIAL)
    assert scan_it(partial).passed


def test_a_missing_leg_is_not_read_as_a_zero_return() -> None:
    """Only the 5m leg is observable and it qualifies: the missing legs must not veto it."""
    decision = scan_it(snapshot(return_1m=Measured.missing(Availability.NO_DATA),
                                return_3m=Measured.missing(Availability.INSUFFICIENT_HISTORY),
                                return_5m=Measured.of(12.0)))
    assert decision.passed and decision.momentum_ratio == pytest.approx(2.0)

    nothing = scan_it(snapshot(return_1m=Measured.missing(Availability.NO_DATA),
                               return_3m=Measured.missing(Availability.NO_DATA),
                               return_5m=Measured.missing(Availability.NO_DATA)))
    assert nothing.momentum_ratio is None and GateReason.NO_MOMENTUM_LEG in nothing.reasons


def test_only_the_regular_session_inside_the_scan_window_passes() -> None:
    assert scan_it(snapshot(session=Session.PREMARKET)).reasons == (GateReason.NOT_REGULAR_SESSION,)
    assert scan_it(snapshot(as_of=et(9, 34))).reasons == (GateReason.OUTSIDE_SCAN_WINDOW,)
    assert scan_it(snapshot(as_of=et(15, 31))).reasons == (GateReason.OUTSIDE_SCAN_WINDOW,)
    assert scan_it(snapshot(as_of=et(9, 35))).passed
    assert scan_it(snapshot(as_of=et(15, 30))).passed


def test_score_saturates_so_one_runaway_mover_cannot_own_the_ranking() -> None:
    huge = scan_it(snapshot(return_1m=Measured.of(400.0), rvol=Measured.of(900.0),
                            rolling_dollar_volume=Measured.of(90_000_000.0)))
    assert huge.score == pytest.approx(1.0)
    assert huge.momentum_ratio == pytest.approx(200.0)


def test_rank_is_deterministic_and_drops_failures() -> None:
    passing = [scan_it(snapshot(symbol=s, rolling_dollar_volume=Measured.of(v)))
               for s, v in (("CCC", 500_000.0), ("AAA", 500_000.0), ("BBB", 900_000.0))]
    failing = scan_it(snapshot(symbol="ZZZ", session=Session.AFTER))
    ordered = rank([*passing, failing])
    assert [d.symbol for d in ordered] == ["BBB", "AAA", "CCC"]
    assert rank(list(reversed([*passing, failing]))) == ordered


def test_the_scan_window_is_config_driven_and_refuses_junk() -> None:
    narrow = replace(CONFIG.scanner, scan_window_start_et="10:00", scan_window_end_et="10:05")
    assert scan_it(snapshot(as_of=et(9, 45)), narrow).reasons == (GateReason.OUTSIDE_SCAN_WINDOW,)
    assert scan_it(snapshot(as_of=et(10, 3)), narrow).passed
    with pytest.raises(InvalidConfig):
        replace(CONFIG.scanner, scan_window_start_et="9:35")
    with pytest.raises(InvalidConfig):
        replace(CONFIG.scanner, scan_window_start_et="15:30", scan_window_end_et="09:35")


# ---- eligibility -----------------------------------------------------------------------

def test_eligibility_rejects_corporate_actions_but_keeps_a_recent_split() -> None:
    assert evaluate_eligibility(snapshot(), in_scope()).eligible
    for flag in (CorporateActionFlag.SPLIT_ON_DAY, CorporateActionFlag.IPO_WARMUP,
                 CorporateActionFlag.DELISTING_WINDOW, CorporateActionFlag.SYMBOL_CHANGE,
                 CorporateActionFlag.CA_SUSPECT):
        decision = evaluate_eligibility(snapshot(corporate_action_flags=frozenset({flag})),
                                        in_scope())
        assert decision.reasons == (IneligibleReason.CORPORATE_ACTION,)
        assert decision.corporate_actions == (flag,)
    recent = snapshot(corporate_action_flags=frozenset({CorporateActionFlag.RECENT_SPLIT}))
    assert evaluate_eligibility(recent, in_scope()).eligible


def test_unknown_halt_passes_and_unknown_density_does_not() -> None:
    assert evaluate_eligibility(snapshot(halt_inferred=HaltStatus.UNKNOWN), in_scope()).eligible
    halted = evaluate_eligibility(snapshot(halt_inferred=HaltStatus.HALT_INFERRED), in_scope())
    assert halted.reasons == (IneligibleReason.HALT_INFERRED,)
    assert evaluate_eligibility(snapshot(sparse_status=TapeDensity.SPARSE), in_scope()).eligible
    assert evaluate_eligibility(snapshot(sparse_status=TapeDensity.VERY_SPARSE),
                                in_scope()).reasons == (IneligibleReason.TAPE_TOO_SPARSE,)
    assert evaluate_eligibility(snapshot(sparse_status=TapeDensity.UNKNOWN),
                                in_scope()).reasons == (IneligibleReason.TAPE_DENSITY_UNKNOWN,)


def test_a_symbol_without_a_scope_decision_never_enters_the_pool() -> None:
    assert evaluate_eligibility(snapshot(), None).reasons == (IneligibleReason.OUT_OF_SCOPE,)
    excluded = in_scope(False, exclusions=(ScopeExclusion.PRICE_BELOW_FLOOR,))
    decision = evaluate_eligibility(snapshot(), excluded)
    assert decision.reasons == (IneligibleReason.OUT_OF_SCOPE,)
    assert decision.scope_exclusions == (ScopeExclusion.PRICE_BELOW_FLOOR,)


# ---- HOD breakout ------------------------------------------------------------------------

def detect(bars, as_of, config=CONFIG.hod_breakout):
    return detect_hod_breakout(make_tape(bars), as_of, scope=AggregationScope.EXTENDED_DAY,
                               config=config)


def test_hod_breakout_arms_with_the_declared_trigger_and_stop() -> None:
    decision = detect(breakout_bars(), et(9, 50))
    setup = decision.setup
    assert decision.rejection is None and setup is not None
    assert setup.setup is SetupType.HOD_BREAKOUT
    assert setup.hod == 11.00 and setup.hod_bar_timestamp == et(9, 45)
    assert setup.consolidation_bars == 4
    assert setup.initial_stop == 10.75 == setup.consolidation_low
    assert setup.trigger_price == pytest.approx(11.00 * 1.001)
    assert setup.risk_per_share(11.011) == pytest.approx(0.261)


def test_window_length_bounds_are_actual_bars() -> None:
    assert detect(breakout_bars(2), et(9, 48)).rejection is SetupRejection.WINDOW_TOO_SHORT
    assert detect(breakout_bars(3), et(9, 49)).setup is not None
    long_hold = breakout_bars(16)
    assert detect(long_hold, et(10, 2)).rejection is SetupRejection.WINDOW_TOO_LONG


def test_a_deep_pullback_is_not_a_hold() -> None:
    # 3% of 11.00 is 0.33, so a low under 10.67 breaks the window.
    assert detect(breakout_bars(low=10.68), et(9, 50)).setup is not None
    assert detect(breakout_bars(low=10.66), et(9, 50)).rejection is SetupRejection.PULLBACK_TOO_DEEP


def test_the_window_starts_at_the_first_bar_that_reached_the_high() -> None:
    """A repeat touch of the same high is not a new high, so the window keeps counting."""
    bars = breakout_bars(3)
    bars.append(make_bar(9, 49, 10.88, 800.0, open_=10.86, high=11.00, low=10.80))
    bars.append(make_bar(9, 50, 10.87, 700.0, open_=10.88, high=10.95, low=10.80))
    setup = detect(bars, et(9, 51)).setup
    assert setup is not None
    assert setup.hod_bar_timestamp == et(9, 45) and setup.consolidation_bars == 5


def test_the_high_of_the_day_is_a_running_extreme_and_future_bars_cannot_change_it() -> None:
    bars = breakout_bars()
    with_future = [*bars, make_bar(9, 55, 12.50, 9000.0, open_=11.0, high=12.60, low=10.95)]
    early, late = detect(bars, et(9, 50)).setup, detect(with_future, et(9, 50)).setup
    assert early is not None and late is not None
    assert (early.hod, early.trigger_price, early.initial_stop) == (
        late.hod, late.trigger_price, late.initial_stop)


def test_an_empty_or_pre_open_cut_yields_no_setup() -> None:
    assert detect(breakout_bars(), et(9, 30)).rejection is SetupRejection.NO_BARS


def test_the_peak_search_matches_a_brute_force_scan() -> None:
    """The binary search over the prefix maximum must agree with the obvious O(n) answer."""
    highs = [10.0, 10.4, 10.2, 10.9, 10.9, 10.75, 10.8, 10.82, 10.78, 10.85, 10.76, 10.8]
    bars = [make_bar(*at(k), h - 0.06, 1000.0, open_=h - 0.07, high=h, low=h - 0.09)
            for k, h in enumerate(highs)]
    setup = detect(bars, et(9, 52)).setup
    expected_index = highs.index(max(highs))
    assert setup is not None
    assert setup.hod == max(highs)
    assert setup.hod_bar_timestamp == et(*at(expected_index))
    assert setup.consolidation_bars == len(highs) - expected_index - 1


# ---- declaration binding -------------------------------------------------------------------

def test_the_declared_rules_file_still_hashes_to_the_registered_checksum() -> None:
    raw = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    body = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == DECLARED_RULES_CHECKSUM


def test_every_declared_number_equals_the_config_default() -> None:
    """The declaration and the code must never drift apart: one number, two places, one test."""
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    scanner, candidate = CONFIG.scanner, CONFIG.candidate
    legs = {c["feature"]: c["threshold"] for c in rules["scanner"]["momentum_legs"]["conditions"]}
    assert legs == {"return_1m": scanner.return_1m_threshold,
                    "return_3m": scanner.return_3m_threshold,
                    "return_5m": scanner.return_5m_threshold}
    liquidity = {c["feature"]: c["threshold"] for c in rules["scanner"]["liquidity_gates"]["conditions"]}
    assert liquidity == {"rolling_dollar_volume": scanner.min_dollar_volume,
                         "rvol": scanner.min_rvol}
    assert rules["scanner"]["window_et"] == {"start": scanner.scan_window_start_et,
                                             "end": scanner.scan_window_end_et}
    assert rules["scanner"]["spread_gate"]["max_spread_pct"] == scanner.max_spread_pct
    assert rules["scanner"]["spread_gate"]["applied"] is False

    assert rules["score"]["threshold"] == candidate.score_threshold
    assert rules["score"]["weights"] == {"momentum": candidate.score_weight_momentum,
                                         "rvol": candidate.score_weight_rvol,
                                         "liquidity": candidate.score_weight_liquidity}
    assert rules["score"]["cap"] == f"min(x, {candidate.score_ratio_cap}) / {candidate.score_ratio_cap}"

    assert rules["fsm"]["ttl_minutes"] == {"candidate": candidate.candidate_ttl_minutes,
                                           "setup": candidate.setup_ttl_minutes,
                                           "signal": candidate.signal_ttl_minutes}
    assert rules["fsm"]["reentry"]["max_entries_per_symbol_per_day"] == CONFIG.risk.max_entries_per_symbol

    hod = rules["setups"]["HOD_BREAKOUT"]
    assert hod["enabled"] is True
    assert (hod["consolidation_min_bars"], hod["consolidation_max_bars"]) == (
        CONFIG.hod_breakout.consolidation_min_bars, CONFIG.hod_breakout.consolidation_max_bars)
    assert hod["max_pullback_pct"] == CONFIG.hod_breakout.max_pullback_pct
    assert hod["breakout_buffer_pct"] == CONFIG.hod_breakout.breakout_buffer_pct
    assert rules["setups"]["FIRST_PULLBACK"]["enabled"] is False

    entry, exit_rules = rules["entry"], rules["exit"]
    assert entry["price_drift_tolerance_pct"] == candidate.price_drift_tolerance_pct
    assert entry["sizing"]["risk_per_trade_pct"] == CONFIG.risk.risk_per_trade_pct
    assert entry["sizing"]["max_position_pct"] == CONFIG.risk.max_position_pct
    assert entry["sizing"]["max_open_positions"] == CONFIG.risk.max_open_positions
    assert entry["sizing"]["daily_loss_limit_r"] == CONFIG.risk.daily_loss_limit_r
    assert exit_rules["partial_take_profit_r"] == CONFIG.exit.partial_take_profit_r
    assert exit_rules["partial_exit_fraction"] == CONFIG.exit.partial_exit_fraction
    assert exit_rules["trailing_model"] == CONFIG.exit.trailing_model.value
    assert exit_rules["time_stop_minutes"] == CONFIG.exit.time_stop_minutes
    assert exit_rules["eod_exit_et"] == CONFIG.exit.eod_exit_et
