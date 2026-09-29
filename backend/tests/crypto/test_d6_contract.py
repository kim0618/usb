"""D6-A contract validation.

This is a design stage. There is no strategy engine to test, so what is testable is the
contract itself: that the frozen document and its machine-readable copy agree, that the score
adds up, that every feature it names is actually available with a PIT rule, and that the bans
the contract declares (maker, liquidation, high leverage, manual/auto sharing) are not quietly
violated by the contract's own fields.

No engine, research module or runtime data is modified or executed here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_MD = ROOT / "docs/crypto/CRYPTO_D6_AUTO_STRATEGY_DESIGN_CONTRACT_V1.md"
CONTRACT_JSON = ROOT / "data/research/crypto/d6/strategy_contract_v1.json"
FREEZE = ROOT / "data/runtime/crypto/d6/contract_freeze_v1.json"
PREFREEZE_QC = ROOT / "data/runtime/crypto/d6/prefreeze_qc_v1.json"
FEE_SOURCE = ROOT / "data/runtime/crypto/reference/fee_source_verification_v1.json"

REQUIRED_TOP_LEVEL = {
    "record", "authority", "verdict", "scope", "candidates", "features", "bucketing",
    "volatility_regime", "hard_filters", "score", "entry", "exit", "leverage", "sizing",
    "account_risk", "costs", "datasets", "validation", "verdict_gates",
    "manual_auto_isolation", "auto_ui_contract", "liquidation_integration",
    "promotion_path", "prefreeze_computation",
}


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT_JSON.read_text())


@pytest.fixture(scope="module")
def freeze() -> dict:
    return json.loads(FREEZE.read_text())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- freeze -------------------------------------------------------------------------------

def test_contract_matches_its_freeze(freeze):
    """The whole point of a preregistration: the document cannot drift after freezing."""
    assert sha256(CONTRACT_MD) == freeze["sha256"]
    assert sha256(CONTRACT_JSON) == freeze["machine_readable_sha256"]


def test_freeze_declares_no_returns_were_computed(freeze):
    assert "zero forward returns" in freeze["computed_before_freeze"]
    qc = json.loads(PREFREEZE_QC.read_text())
    assert qc["declaration"].endswith("PnL: NOT computed.")


def test_verdict_is_one_of_the_two_allowed(freeze, contract):
    allowed = {"DESIGN_READY", "NO_DEFENSIBLE_COMPOSITE_STRATEGY"}
    assert freeze["verdict"] in allowed
    assert contract["verdict"] == freeze["verdict"]


# --- required fields ----------------------------------------------------------------------

def test_required_fields_present(contract):
    assert REQUIRED_TOP_LEVEL <= set(contract)


def test_markdown_is_the_authority(contract):
    assert contract["authority"] == "docs/crypto/CRYPTO_D6_AUTO_STRATEGY_DESIGN_CONTRACT_V1.md"
    assert CONTRACT_MD.exists()


def test_candidate_count_within_limit(contract):
    assert len(contract["candidates"]) <= 3
    designed = [c for c in contract["candidates"] if c["status"] == "DESIGNED"]
    assert len(designed) >= 1
    for cand in contract["candidates"]:
        if cand["status"] == "REJECTED":
            assert cand["reason"], f"{cand['id']} rejected without a stated reason"


# --- score --------------------------------------------------------------------------------

def test_score_components_total_the_declared_scale(contract):
    score = contract["score"]
    assert sum(c["max"] for c in score["long_components"]) == score["scale_max"] == 100


def test_no_duplicate_score_components(contract):
    names = [c["name"] for c in contract["score"]["long_components"]]
    inputs = [c["input"] for c in contract["score"]["long_components"]]
    assert len(set(names)) == len(names)
    assert len(set(inputs)) == len(inputs), "two components reading the same feature is double counting"


def test_score_weights_were_not_pnl_optimized(contract):
    score = contract["score"]
    assert score["weighting"] == "equal_by_category"
    assert score["weight_selection_method"] == "PREREGISTERED_EQUAL_WEIGHT_NOT_PNL_OPTIMIZED"
    maxima = {c["max"] for c in score["long_components"]}
    assert len(maxima) == 1, "equal weighting means every category carries the same maximum"


def test_entry_threshold_is_reachable_but_not_trivial(contract):
    score = contract["score"]
    threshold = score["thresholds"]["LONG_ENTRY_MIN_SCORE"]
    assert 0 < threshold < score["scale_max"]
    mandatory = sum(m["min"] for m in score["mandatory_minimums"])
    assert mandatory < threshold, "the mandatory minimums alone must not clear the threshold"


def test_hold_is_a_supported_state(contract):
    score = contract["score"]
    assert set(score["states"]) == {"LONG", "SHORT", "HOLD"}
    assert score["default_state"] == "HOLD"
    assert score["thresholds"]["SCORE_SEPARATION_MIN"] > 0


def test_short_is_disabled_rather_than_invented(contract):
    cand = contract["candidates"][0]
    assert cand["sides"]["SHORT"] == "SHORT_DISABLED_FOR_V1"
    assert contract["score"]["thresholds"]["SHORT_ENTRY_MIN_SCORE"] is None


# --- features: availability and PIT --------------------------------------------------------

def test_every_score_input_is_a_declared_feature(contract):
    declared = {f["id"] for f in contract["features"]}
    for component in contract["score"]["long_components"]:
        assert component["input"] in declared


def test_every_feature_has_a_pit_rule_and_an_available_dataset(contract):
    datasets = {d["name"] for d in contract["datasets"]}
    for feature in contract["features"]:
        assert feature["pit"], f"{feature['id']} has no PIT rule"
        assert feature["lookback_bars"] >= 0
        for source in feature["inputs"]:
            assert source in datasets, f"{feature['id']} reads {source}, which is not a declared dataset"


def test_open_interest_keeps_its_five_minute_delay(contract):
    oi = next(f for f in contract["features"] if f["id"] == "f_oi1h")
    assert oi["pit"] == "record_ts_plus_5min"


def test_no_new_features_were_invented(contract):
    assert contract["new_features_count"] == 0
    for feature in contract["features"]:
        assert feature["inherited_from"], f"{feature['id']} has no upstream contract"


def test_volatility_cutoffs_are_inherited_not_refitted(contract):
    regime = contract["volatility_regime"]
    assert regime["recomputed_for_d6"] is False
    assert regime["low_below"] < regime["high_above"]
    assert "D5.1" in regime["source"]


def test_orderbook_is_marked_unavailable_for_history(contract):
    book = next(d for d in contract["datasets"] if d["name"] == "orderbook")
    assert book["source"] == "NONE_HISTORICAL"
    assert "synthetic" in book["pit"]


# --- hard filters vs score ------------------------------------------------------------------

def test_hard_filters_are_distinct_and_thresholded(contract):
    ids = [f["id"] for f in contract["hard_filters"]]
    names = [f["name"] for f in contract["hard_filters"]]
    assert len(set(ids)) == len(ids)
    assert len(set(names)) == len(names)
    for f in contract["hard_filters"]:
        assert f["threshold"], f"{f['id']} is a filter with no threshold"


def test_low_volatility_is_blocked_by_a_filter_and_scores_zero(contract):
    assert any(f["name"] == "VOL_LOW" for f in contract["hard_filters"])
    vol = next(c for c in contract["score"]["long_components"] if c["input"] == "f_rv24h")
    assert vol["levels"]["LOW"] == 0


# --- bans the contract declares --------------------------------------------------------------

def test_maker_execution_is_not_used(contract):
    assert contract["scope"]["maker_allowed"] is False
    assert contract["scope"]["execution"] == "taker_market_only"
    assert contract["costs"]["maker_used"] is False
    assert contract["entry"]["order_type"] == "MARKET"
    assert contract["entry"]["liquidity"] == "taker"


def test_liquidation_data_is_not_a_v1_feature(contract):
    assert contract["scope"]["liquidation_feature_allowed"] is False
    liq = contract["liquidation_integration"]
    assert liq["v1_usage"] == "NONE"
    assert liq["classification"] == "FORWARD_ONLY / FUTURE_V2"
    feature_text = json.dumps(contract["features"]).lower()
    assert "liquidation" not in feature_text


def test_no_machine_learning_or_llm_in_the_decision(contract):
    assert contract["scope"]["ml_allowed"] is False
    assert contract["scope"]["llm_decision_allowed"] is False


# --- leverage and sizing ----------------------------------------------------------------------

def test_leverage_policy_is_conservative_and_fixed(contract):
    lev = contract["leverage"]
    assert lev["auto_v1"] == 1.0
    assert lev["fixed"] is True
    assert 20 in lev["forbidden_for_auto"] and 50 in lev["forbidden_for_auto"]
    assert lev["auto_v1"] <= contract["account_risk"]["max_leverage"]


def test_manual_settings_are_not_changed_by_this_contract(contract):
    lev = contract["leverage"]
    assert lev["manual_presets_changed"] is False
    assert lev["safe_max_changed"] is False


def test_safe_max_is_only_an_upper_bound(contract):
    assert contract["sizing"]["safe_max_role"] == "UPPER_BOUND_CONSTRAINT_ONLY"


def test_risk_budget_is_consistent_with_the_daily_guard(contract):
    risk = contract["account_risk"]
    assert risk["max_loss_per_trade_pct"] * risk["consecutive_loss_guard"] == risk["daily_loss_guard_pct"]
    assert risk["max_concurrent_positions"] == 1
    assert risk["max_capital_allocation_per_position"] <= 1.0


def test_measured_sizing_stays_inside_the_cap(contract):
    measured = contract["sizing"]["measured_notional_over_equity"]
    assert measured["p95"] <= contract["sizing"]["max_notional_over_equity"]
    assert measured["capped_share"] == 0.0


# --- costs ------------------------------------------------------------------------------------

def test_fee_rates_match_the_verified_source(contract):
    source = json.loads(FEE_SOURCE.read_text())["adopted"]
    assert contract["costs"]["taker_rate"] == float(source["taker_rate"])
    assert contract["costs"]["maker_rate"] == float(source["maker_rate"])
    assert contract["costs"]["tier_assumption"].endswith("ASSUMED")


def test_costs_are_read_from_the_single_source(contract):
    assert contract["costs"]["read_via"] == "app.crypto.paper.fees.load_scenarios"


# --- exits --------------------------------------------------------------------------------------

def test_exit_rules_are_limited_to_the_three_allowed(contract):
    exit_rules = contract["exit"]
    assert exit_rules["other_exits_allowed"] is False
    assert exit_rules["primary"]["minutes"] == exit_rules["max_hold_min"]
    assert exit_rules["risk"]["price_basis"] == "mark"
    assert exit_rules["risk"]["same_bar_rule"] == "stop wins when both touched"


def test_horizon_is_a_primary_grade_one(contract):
    """8h was REFERENCE_ONLY in both upstream contracts and cannot be the candidate horizon."""
    assert contract["candidates"][0]["horizon_min"] == 240
    assert contract["exit"]["max_hold_min"] <= 240


# --- validation ------------------------------------------------------------------------------------

def test_validation_declares_the_selection_bias(contract):
    val = contract["validation"]
    assert val["untouched_holdout"] is False
    assert "selection_bias" in val and val["selection_bias"].startswith("DECLARED")
    assert val["rolling_calibration"] is False


def test_only_one_arm_can_decide(contract):
    val = contract["validation"]
    assert val["judging_arm"] == "A-MAIN"
    assert val["judging_arm"] in val["arms"]
    assert not any(arm.startswith("A-REF") and arm == val["judging_arm"] for arm in val["arms"])


def test_gates_are_unique_and_stated(contract):
    ids = [g["id"] for g in contract["verdict_gates"]]
    assert len(set(ids)) == len(ids)
    for gate in contract["verdict_gates"]:
        assert gate["rule"].strip()


def test_promotion_path_requires_shadow_before_paper(contract):
    path = contract["promotion_path"]
    assert path.index("D6-C") < path.index("D6-D") < path.index("D7")


# --- manual / auto isolation ---------------------------------------------------------------------------

def test_only_market_data_is_shared_between_manual_and_auto(contract):
    iso = contract["manual_auto_isolation"]
    assert iso["shared"] == ["read_only_market_data"]
    for forbidden in ("active_position", "wallet_balance", "realized_pnl", "reset_anchor", "trade_stats"):
        assert forbidden in iso["never_shared"]
    assert iso["one_session_per_run_dir"] is True


def test_auto_ui_has_no_manual_controls(contract):
    ui = contract["auto_ui_contract"]
    assert ui["chart"] is False
    assert ui["manual_order_buttons"] is False
    assert "current_decision" in ui["fields"]
