"""B-E0 contract, universe, statistics, lift and gate tests.

The point of most of these is that the pre-registration is machine-checked rather than merely
written: the declared capital really is the one Strategy A's paper account uses, the cost levels
really do add up, the decision order really is exhaustive, and the verdict really does come out
of the contract's numbers rather than out of this code.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path

import pytest

from app.backtest.strategy_b_e0 import metrics
from app.backtest.strategy_b_e0.contract import (
    CHECKSUM_PATH, CONTRACT_PATH, ContractChanged, ContractInvalid, Contract, declared_checksum,
    load_contract)
from app.backtest.strategy_b_e0.gate import (
    Interval, LiftOutcome, SampleCounts, Verdict, evaluate)
from app.backtest.strategy_b_e0.identity import RunMode, identity_lines
from app.backtest.strategy_b_e0.preflight import Check, PreflightReport, run_preflight
from app.backtest.strategy_b_e0.universe import (
    UniverseInvalid, UniverseMember, load_universe, write_universe)
from app.backtest.strategy_c_selection.rules import canonical_checksum
from app.dev.bootstrap_paper_account import PAPER_INITIAL_CASH
from app.strategy_b.config import StrategyBConfig

SESSIONS = tuple(date(2026, 5, 18) + timedelta(days=i) for i in range(10))


@pytest.fixture(scope="module")
def contract() -> Contract:
    return load_contract()


# ---- contract ------------------------------------------------------------------------------


def test_contract_round_trips_and_matches_its_declared_checksum(contract):
    raw = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert canonical_checksum(raw) == contract.canonical_checksum == declared_checksum()


def test_canonical_checksum_ignores_key_order_but_not_values(contract):
    raw = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    reordered = dict(reversed(list(raw.items())))
    assert canonical_checksum(reordered) == contract.canonical_checksum
    changed = dict(raw)
    changed["capital_policy"] = dict(raw["capital_policy"]) | {"initial_capital_usd": "10000"}
    assert canonical_checksum(changed) != contract.canonical_checksum


def test_a_contract_whose_checksum_moved_is_refused(tmp_path, contract):
    raw = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    raw["capital_policy"]["initial_capital_usd"] = "10000"
    path = tmp_path / "b_e0_contract_v1.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    (tmp_path / "b_e0_contract_v1.sha256").write_text(
        CHECKSUM_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ContractChanged):
        load_contract(path)


def test_initial_capital_is_the_common_strategy_value(contract):
    assert contract.initial_capital_usd == "7428.92"
    assert Decimal(contract.initial_capital_usd) == PAPER_INITIAL_CASH
    assert contract.raw["capital_policy"]["policy"] == "COMMON_STRATEGY_INITIAL_CAPITAL"
    assert contract.raw["capital_policy"]["independent_accounts"] is True
    assert contract.fx_model == "NONE"


def test_no_stale_ten_thousand_is_used_as_b_capital(contract):
    """10000 may appear only where it describes the KRW figure or A's own baseline."""
    capital = contract.raw["capital_policy"]
    assert capital["initial_capital_usd"] != "10000"
    conflict = capital["conflict_on_record"]["fact"]
    assert "STARTING_CASH" in conflict and "10000" in conflict


def test_declared_risk_values_match_the_code_they_claim_to_copy(contract):
    risk = StrategyBConfig().risk
    declared = contract.raw["sizing"]
    assert declared["risk_per_trade_pct"] == risk.risk_per_trade_pct
    assert declared["max_position_pct"] == risk.max_position_pct
    assert declared["max_open_positions"] == risk.max_open_positions
    assert declared["daily_loss_limit_r"] == risk.daily_loss_limit_r


def test_derived_risk_values_are_that_capital_times_those_ratios(contract):
    capital = Decimal(contract.initial_capital_usd)
    risk = StrategyBConfig().risk
    derived = contract.raw["sizing"]["derived_at_initial_capital"]
    assert Decimal(derived["risk_amount_usd"]) == capital * Decimal(str(risk.risk_per_trade_pct)) / 100
    assert Decimal(derived["position_cap_usd"]) == capital * Decimal(str(risk.max_position_pct)) / 100
    # and they are declared as a first-trade display, not as the rule
    assert contract.raw["sizing"]["equity_basis"] == "CURRENT_REALISED_EQUITY"


def test_cost_levels_decompose_and_round_trip_is_twice_the_side(contract):
    levels = contract.cost_levels
    assert set(levels) == {"BASE", "STRESS_30", "STRESS_50", "ZERO_COST"}
    assert levels["BASE"].all_in_bps_per_side == 25.0
    assert levels["BASE"].round_trip_bps == 50.0
    assert levels["STRESS_30"].round_trip_bps == 60.0
    assert levels["STRESS_50"].round_trip_bps == 100.0
    for level in levels.values():
        assert (level.commission_bps_per_side + level.execution_cost_bps_per_side
                == pytest.approx(level.all_in_bps_per_side))


def test_commission_is_held_fixed_across_the_stress_levels(contract):
    levels = contract.cost_levels
    priced = [levels[name] for name in ("BASE", "STRESS_30", "STRESS_50")]
    assert {level.commission_bps_per_side for level in priced} == {10.0}
    assert [level.execution_cost_bps_per_side for level in priced] == [15.0, 20.0, 40.0]


def test_exactly_one_run_feeds_the_verdict_and_it_is_next_bar_open(contract):
    feeding = [spec for spec in contract.runs if spec.verdict_input]
    assert [spec.label for spec in feeding] == ["BASE"] == [contract.authoritative_label]
    assert feeding[0].fill_scenario == "NEXT_BAR_OPEN"
    counterfactual = contract.run("CF_SIGNAL_BAR")
    assert counterfactual.fill_scenario == "SIGNAL_BAR" and not counterfactual.verdict_input


def test_contract_rejects_a_second_verdict_run(contract):
    raw = json.loads(json.dumps(contract.raw))
    raw["runs"][1]["verdict_input"] = True
    with pytest.raises(ContractInvalid, match="exactly one run"):
        Contract(raw, "x") and _validate_through_loader(raw)


def _validate_through_loader(raw) -> None:
    from app.backtest.strategy_b_e0.contract import _validate
    _validate(Contract(raw, "x"))


def test_decision_order_is_exhaustive_over_the_gate_states(contract):
    order = [step["outcome"] for step in contract.raw["verdict"]["decision_order"]]
    assert sorted(order) == sorted(contract.gate_states)
    assert len(set(order)) == len(order)


def test_bootstrap_settings_are_pinned(contract):
    stats = contract.statistics
    assert (stats.resample_unit, stats.block_length) == ("trading session", 1)
    assert (stats.replicates, stats.seed) == (10000, 20260921)
    assert stats.interval_method == "PERCENTILE" and stats.confidence == 0.95


def test_forbidden_flags_are_declared(contract):
    assert set(contract.forbidden_flags) == {
        "--allow-partial", "--ignore-checksum", "--skip-missing", "--force"}


# ---- gate ----------------------------------------------------------------------------------


def _gate(contract, *, trades=40, sessions=25, point=0.2, lower=0.05, upper=0.4,
          lift=LiftOutcome.LIFT_PASS, ready=True, error=None):
    return evaluate(contract, sample=SampleCounts(trades, sessions),
                    mean_net_r=None if point is None else Interval(point, lower, upper),
                    lift=lift, dataset_ready=ready, error=error)


def test_gate_pass_needs_every_condition(contract):
    assert _gate(contract).verdict is Verdict.PASS
    assert _gate(contract, point=0.05).verdict is Verdict.BORDERLINE      # below 0.10
    assert _gate(contract, lower=-0.01).verdict is Verdict.BORDERLINE     # CI touches zero
    assert _gate(contract, lift=LiftOutcome.LIFT_FAIL).verdict is Verdict.BORDERLINE
    assert _gate(contract, lift=LiftOutcome.LIFT_INSUFFICIENT).verdict is Verdict.BORDERLINE


def test_gate_decision_order_is_respected(contract):
    # dataset beats everything, including an error and a fine-looking interval
    assert _gate(contract, ready=False, error="boom").verdict is Verdict.DATASET_NOT_READY
    assert _gate(contract, error="boom").verdict is Verdict.ERROR
    # sample gate beats a significantly negative interval
    assert _gate(contract, trades=29, upper=-0.5).verdict is Verdict.INSUFFICIENT_SAMPLE
    assert _gate(contract, sessions=19, upper=-0.5).verdict is Verdict.INSUFFICIENT_SAMPLE
    assert _gate(contract, point=-0.3, lower=-0.6, upper=-0.1).verdict is Verdict.FAIL


def test_gate_at_the_declared_boundaries(contract):
    assert _gate(contract, trades=30, sessions=20).verdict is Verdict.PASS
    assert _gate(contract, point=0.10, lower=0.0001).verdict is Verdict.PASS
    # an upper bound of exactly zero is FAIL, a lower bound of exactly zero is not PASS
    assert _gate(contract, point=-0.1, lower=-0.2, upper=0.0).verdict is Verdict.FAIL
    assert _gate(contract, lower=0.0).verdict is Verdict.BORDERLINE


def test_missing_interval_past_the_sample_gate_is_an_error_not_a_verdict(contract):
    assert _gate(contract, point=None).verdict is Verdict.ERROR


def test_gate_result_serialises_every_state(contract):
    body = _gate(contract).as_dict()
    assert body["verdict"] == "PASS"
    assert body["mean_net_r"]["ci_lower"] == 0.05
    assert body["thresholds"]["min_mean_net_r"] == contract.min_mean_net_r


# ---- statistics ----------------------------------------------------------------------------


def test_session_series_places_values_and_counts_silent_days(contract):
    data = metrics.series({SESSIONS[0]: [1.0, 3.0], SESSIONS[2]: [2.0]}, SESSIONS)
    assert data.n == 3
    assert data.point == pytest.approx(2.0)
    assert data.total.sum() == pytest.approx(6.0)


def test_series_refuses_a_session_outside_the_run(contract):
    with pytest.raises(ValueError, match="not one of the run's sessions"):
        metrics.series({date(2020, 1, 2): [1.0]}, SESSIONS)


def test_bootstrap_is_deterministic_and_brackets_the_point(contract):
    values = {day: [0.5, -0.2, 0.9] for day in SESSIONS}
    data = metrics.series(values, SESSIONS)
    first = metrics.mean_interval(data, contract.statistics)
    second = metrics.mean_interval(data, contract.statistics)
    assert (first.point, first.lower, first.upper) == (second.point, second.lower, second.upper)
    assert first.lower <= first.point <= first.upper


def test_a_constant_cohort_has_a_degenerate_interval(contract):
    data = metrics.series({day: [1.0] for day in SESSIONS}, SESSIONS)
    interval = metrics.mean_interval(data, contract.statistics)
    assert interval.lower == pytest.approx(1.0) and interval.upper == pytest.approx(1.0)


def test_difference_is_taken_inside_each_draw(contract):
    left = metrics.series({day: [1.0] for day in SESSIONS}, SESSIONS)
    right = metrics.series({day: [0.25] for day in SESSIONS}, SESSIONS)
    difference = metrics.difference_interval(left, right, contract.statistics)
    assert difference.point == pytest.approx(0.75)
    assert difference.lower == pytest.approx(0.75) and difference.upper == pytest.approx(0.75)


def test_difference_refuses_mismatched_session_grids(contract):
    left = metrics.series({SESSIONS[0]: [1.0]}, SESSIONS)
    right = metrics.series({SESSIONS[0]: [1.0]}, SESSIONS[:5])
    with pytest.raises(ValueError, match="same session grid"):
        metrics.difference_interval(left, right, contract.statistics)


# ---- universe ------------------------------------------------------------------------------


def _members() -> list[UniverseMember]:
    return [
        UniverseMember("AAA", (SESSIONS[0], SESSIONS[1]), SESSIONS[0], SESSIONS[1]),
        UniverseMember("BBB", (SESSIONS[1],), SESSIONS[0], SESSIONS[2]),
    ]


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "universe.json"
    write_universe(path, scope_start=SESSIONS[0], scope_end=SESSIONS[-1], sessions=SESSIONS,
                   members=_members(), exclusions={"CON": "reserved device name"},
                   built_from={"document": "b_fetch_universe_q1.json", "digest": "abc"})
    return path


def test_universe_counts_come_from_the_artifact(tmp_path):
    universe = load_universe(_write(tmp_path))
    assert universe.symbol_count == 2 and universe.symbols == ("AAA", "BBB")
    assert universe.exclusions == {"CON": "reserved device name"}


def test_universe_membership_is_per_session_not_the_union(tmp_path):
    universe = load_universe(_write(tmp_path))
    assert universe.symbols_for(SESSIONS[0]) == ("AAA",)
    assert universe.symbols_for(SESSIONS[1]) == ("AAA", "BBB")
    assert universe.symbols_for(SESSIONS[5]) == ()
    # the union leaking into a session is exactly what the violation check catches
    assert universe.membership_violations(SESSIONS[0], ("AAA", "BBB")) == ("BBB",)
    assert universe.membership_violations(SESSIONS[1], ("AAA", "BBB")) == ()


def test_universe_hash_changes_with_content_and_is_stable_otherwise(tmp_path):
    first = load_universe(_write(tmp_path))
    again = load_universe(first.path)
    assert first.sha256 == again.sha256
    other = tmp_path / "second"
    other.mkdir()
    second = write_universe(other / "universe.json", scope_start=SESSIONS[0],
                            scope_end=SESSIONS[-1], sessions=SESSIONS,
                            members=_members()[:1], exclusions={},
                            built_from={"document": "b_fetch_universe_q1.json", "digest": "abc"})
    assert second.sha256 != first.sha256


def test_universe_refuses_a_wrong_expected_hash(tmp_path):
    from app.backtest.strategy_b_e0.universe import UniverseChanged
    path = _write(tmp_path)
    with pytest.raises(UniverseChanged):
        load_universe(path, expected_sha256="0" * 64)


def test_universe_is_never_overwritten(tmp_path):
    path = _write(tmp_path)
    with pytest.raises(UniverseInvalid, match="already exists"):
        write_universe(path, scope_start=SESSIONS[0], scope_end=SESSIONS[-1], sessions=SESSIONS,
                       members=_members(), exclusions={}, built_from={})


def test_universe_rejects_a_scope_session_outside_the_grid(tmp_path):
    path = tmp_path / "bad.json"
    body = {
        "schema": "b-e0-run-universe-v1",
        "scope_start": SESSIONS[0].isoformat(), "scope_end": SESSIONS[-1].isoformat(),
        "sessions": [SESSIONS[0].isoformat()],
        "symbols": [{"symbol": "AAA", "scope_sessions": [SESSIONS[3].isoformat()],
                     "fetch_start": SESSIONS[0].isoformat(),
                     "fetch_end": SESSIONS[3].isoformat()}],
        "exclusions": [], "built_from": {},
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(UniverseInvalid, match="not a session"):
        load_universe(path)


def test_universe_rejects_a_member_that_is_also_excluded(tmp_path):
    path = tmp_path / "bad.json"
    body = {
        "schema": "b-e0-run-universe-v1",
        "scope_start": SESSIONS[0].isoformat(), "scope_end": SESSIONS[-1].isoformat(),
        "sessions": [SESSIONS[0].isoformat()],
        "symbols": [{"symbol": "AAA", "scope_sessions": [SESSIONS[0].isoformat()],
                     "fetch_start": SESSIONS[0].isoformat(),
                     "fetch_end": SESSIONS[0].isoformat()}],
        "exclusions": [{"symbol": "AAA", "reason": "no"}], "built_from": {},
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(UniverseInvalid, match="both members and exclusions"):
        load_universe(path)


# ---- preflight -----------------------------------------------------------------------------


class _Facts:
    """A dataset that is whole unless a test says otherwise."""

    def __init__(self, **holes):
        self.holes = holes

    def missing_scope_pairs(self, pairs):
        return self.holes.get("pairs", ())

    def symbols_missing_warmup(self, universe, sessions):
        return self.holes.get("warmup", ())

    def symbols_missing_splits(self, symbols):
        return self.holes.get("splits", ())

    def sessions_missing_daily(self, sessions):
        return self.holes.get("daily", ())

    def schema_mismatches(self):
        return self.holes.get("schema", {})

    def checksum_mismatches(self, pairs):
        return self.holes.get("checksums", ())

    def collection_completeness(self):
        return {"completeness_pct": 100.0, "absent_symbols": []}


def _universe_for_contract(tmp_path, contract):
    path = tmp_path / "u.json"
    start = date.fromisoformat(contract.scope_start)
    end = date.fromisoformat(contract.scope_end)
    return write_universe(path, scope_start=start, scope_end=end, sessions=[start, end],
                          members=[UniverseMember("AAA", (start, end), start, end)],
                          exclusions={}, built_from={})


def test_preflight_passes_on_a_whole_dataset(tmp_path, contract):
    universe = _universe_for_contract(tmp_path, contract)
    report = run_preflight(contract, universe, _Facts(), contract_path=CONTRACT_PATH)
    assert report.ready, report.as_dict()["failed"]


def test_preflight_reports_every_failure_not_just_the_first(tmp_path, contract):
    universe = _universe_for_contract(tmp_path, contract)
    facts = _Facts(pairs=[("AAA", date.fromisoformat(contract.scope_start))],
                   warmup=["AAA"], splits=["AAA"])
    report = run_preflight(contract, universe, facts, contract_path=CONTRACT_PATH)
    assert not report.ready
    assert {check.name for check in report.failures} == {
        "scope_bars_present", "warmup_present", "splits_present"}


def test_preflight_catches_the_union_leaking_into_a_session(tmp_path, contract):
    universe = _universe_for_contract(tmp_path, contract)
    start = date.fromisoformat(contract.scope_start)
    report = run_preflight(contract, universe, _Facts(),
                           planned_symbols_by_session={start: ["AAA", "ZZZ"]},
                           contract_path=CONTRACT_PATH)
    leak = next(check for check in report.checks if check.name == "per_session_membership")
    assert not leak.passed and leak.items == (f"ZZZ {start}",)


def test_preflight_notices_a_universe_from_a_different_window(tmp_path, contract):
    path = tmp_path / "other.json"
    universe = write_universe(path, scope_start=date(2020, 1, 2), scope_end=date(2020, 1, 3),
                              sessions=[date(2020, 1, 2)],
                              members=[UniverseMember("AAA", (date(2020, 1, 2),),
                                                      date(2020, 1, 2), date(2020, 1, 2))],
                              exclusions={}, built_from={})
    report = run_preflight(contract, universe, _Facts(), contract_path=CONTRACT_PATH)
    assert not report.ready
    assert "universe_window_matches_contract" in {check.name for check in report.failures}


def test_preflight_report_serialises(tmp_path, contract):
    universe = _universe_for_contract(tmp_path, contract)
    body = run_preflight(contract, universe, _Facts(), contract_path=CONTRACT_PATH).as_dict()
    assert body["ready"] is True and body["failed"] == []
    assert body["collection_completeness"]["completeness_pct"] == 100.0


# ---- identity ------------------------------------------------------------------------------


def test_identity_carries_capital_costs_and_universe_and_is_deterministic(tmp_path, contract):
    universe = _universe_for_contract(tmp_path, contract)
    spec = contract.run(contract.authoritative_label)
    kwargs = dict(dataset_identity="ds", dataset_digest="dg", code="code",
                  mode=RunMode("STRICT"))
    lines = identity_lines(contract, spec, universe, **kwargs)
    assert identity_lines(contract, spec, universe, **kwargs) == lines
    joined = "\n".join(lines)
    assert "initial_capital_usd=7428.92" in joined
    assert "fill_scenario=NEXT_BAR_OPEN" in joined
    assert "commission_bps_per_side=10.0" in joined
    assert "execution_cost_bps_per_side=15.0" in joined
    assert f"universe_sha256={universe.sha256}" in joined
    assert f"contract_canonical_checksum={contract.canonical_checksum}" in joined
    assert "bootstrap_seed=20260921" in joined
    # no wall clock, machine or git state in the digest input
    assert not any(word in joined for word in ("timestamp", "hostname", "git"))


def test_a_different_cost_level_is_a_different_identity(tmp_path, contract):
    universe = _universe_for_contract(tmp_path, contract)
    kwargs = dict(dataset_identity="ds", dataset_digest="dg", code="code",
                  mode=RunMode("STRICT"))
    base = identity_lines(contract, contract.run("BASE"), universe, **kwargs)
    stressed = identity_lines(contract, contract.run("STRESS_50"), universe, **kwargs)
    assert base != stressed


def test_only_declared_run_modes_exist():
    RunMode("STRICT")
    RunMode("PREFLIGHT_ONLY")
    with pytest.raises(ValueError, match="unknown run mode"):
        RunMode("YOLO")


# ---- CLI -----------------------------------------------------------------------------------


def test_cli_verify_reports_the_contract(capsys):
    from app.dev.run_strategy_b_e0 import main
    assert main(["verify"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["initial_capital_usd"] == "7428.92"
    assert body["authoritative_label"] == "BASE"
    assert body["cost_levels"]["STRESS_50"] == 50.0


@pytest.mark.parametrize("flag", ["--allow-partial", "--ignore-checksum", "--skip-missing",
                                  "--force"])
def test_cli_refuses_every_forbidden_flag(flag, tmp_path):
    from app.dev.run_strategy_b_e0 import main
    with pytest.raises(SystemExit):
        main(["run", "--universe", str(tmp_path / "u.json"), "--dataset", str(tmp_path),
              "--out", str(tmp_path), flag])


def test_preflight_refuses_a_contract_that_disagrees_with_the_running_config(contract):
    """No number in this package is hardcoded; the guard is that the two must agree."""
    from dataclasses import replace
    from app.backtest.strategy_b_e0.preflight import config_agreement

    config = StrategyBConfig()
    assert config_agreement(contract, config).passed

    drifted = replace(config, candidate=replace(config.candidate, signal_ttl_minutes=5))
    check = config_agreement(contract, drifted)
    assert not check.passed
    assert check.items == ("signal_ttl_minutes: contract 2 != config 5",)


def test_signal_ttl_block_is_precommitted_and_matches_the_code(contract):
    block = contract.raw["signal_ttl"]
    assert block["signal_ttl_minutes"] == StrategyBConfig().candidate.signal_ttl_minutes == 2
    assert block["status"] == "RESEARCH_DEFAULT_PRECOMMITTED"
    assert block["units"] == "WALL_CLOCK_MINUTES"
    assert block["anchor"] == "ENTRY_SIGNALLED"
    assert block["boundary"] == "INCLUSIVE"


def test_the_fill_window_gap_is_recorded_as_resolved(contract):
    """The contract must not claim a window the engine does not reach, in either direction."""
    gap = contract.raw["execution_model"]["fill_rule"]["implementation_gap"]
    assert gap["status"] == "RESOLVED"
    assert "fix" in gap and "measured_after" in gap
    assert all(case["agree"] for case in contract.raw["execution_model"]["edge_cases_to_test"])
