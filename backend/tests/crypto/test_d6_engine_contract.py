"""The contract loader: hash gate, structural validation and the string bindings.

The string bindings are the load-bearing part. Several fixed values live inside the frozen
contract only as English ("last 1m bar close older than 90s"), and this package refuses to parse
English. Instead it asserts the exact literal and uses a number written next to it. These tests
prove that changing the contract text breaks the build loudly rather than leaving a stale number
in force.
"""
from __future__ import annotations

import json

import pytest

from app.crypto.research.d6 import contract as C


@pytest.fixture(scope="module")
def loaded() -> C.Contract:
    return C.load()


def write(tmp_path, doc: dict) -> "object":
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(doc))
    return path


@pytest.fixture
def doc(loaded) -> dict:
    return json.loads(json.dumps(loaded.doc))


# --- hash gate -----------------------------------------------------------------------------

def test_loads_the_frozen_contract(loaded):
    assert loaded.sha256 == C.sha256_file(C.CONTRACT_JSON)
    freeze = json.loads(C.FREEZE_JSON.read_text())
    assert loaded.sha256 == freeze["machine_readable_sha256"]
    assert loaded.strategy_id == "FDN-V1"
    assert loaded.strategy_name == "FORCED_DELEVERAGING_NORMALIZATION_V1"


def test_hash_mismatch_refuses_to_load(tmp_path, doc):
    path = write(tmp_path, doc)
    with pytest.raises(C.ContractHashMismatch) as excinfo:
        C.load(path, expected_sha256="0" * 64)
    assert "CONTRACT_HASH_MISMATCH" in str(excinfo.value)


def test_any_edit_changes_the_hash(tmp_path, doc):
    """A one-character change to a threshold must not slip through."""
    doc["score"]["thresholds"]["LONG_ENTRY_MIN_SCORE"] = 49
    path = write(tmp_path, doc)
    with pytest.raises(C.ContractHashMismatch):
        C.load(path, expected_sha256=json.loads(C.FREEZE_JSON.read_text())["machine_readable_sha256"])


def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(C.ContractIncomplete):
        C.load(tmp_path / "absent.json", expected_sha256="x")


# --- structural validation -----------------------------------------------------------------

def _load_edited(tmp_path, doc):
    path = write(tmp_path, doc)
    return C.load(path, expected_sha256=C.sha256_file(path))


def test_missing_required_field_is_refused(tmp_path, doc):
    del doc["hard_filters"]
    with pytest.raises(C.ContractIncomplete):
        _load_edited(tmp_path, doc)


def test_score_that_does_not_total_the_scale_is_refused(tmp_path, doc):
    doc["score"]["long_components"][0]["max"] = 30
    with pytest.raises(C.ContractMismatch, match="scale_max"):
        _load_edited(tmp_path, doc)


def test_duplicate_component_input_is_refused(tmp_path, doc):
    doc["score"]["long_components"][1]["input"] = "f_basis"
    with pytest.raises(C.ContractMismatch, match="duplicate"):
        _load_edited(tmp_path, doc)


def test_component_reading_an_undeclared_feature_is_refused(tmp_path, doc):
    doc["score"]["long_components"][0]["input"] = "f_invented"
    with pytest.raises(C.ContractMismatch, match="undeclared"):
        _load_edited(tmp_path, doc)


@pytest.mark.parametrize("threshold", [0, 100, 101, -5])
def test_out_of_range_entry_threshold_is_refused(tmp_path, doc, threshold):
    doc["score"]["thresholds"]["LONG_ENTRY_MIN_SCORE"] = threshold
    with pytest.raises(C.ContractMismatch):
        _load_edited(tmp_path, doc)


def test_threshold_reachable_by_mandatory_minimums_alone_is_refused(tmp_path, doc):
    doc["score"]["thresholds"]["LONG_ENTRY_MIN_SCORE"] = 20
    with pytest.raises(C.ContractMismatch, match="mandatory"):
        _load_edited(tmp_path, doc)


def test_enabling_short_in_v1_is_refused(tmp_path, doc):
    doc["score"]["thresholds"]["SHORT_ENTRY_MIN_SCORE"] = 50
    with pytest.raises(C.ContractMismatch, match="SHORT"):
        _load_edited(tmp_path, doc)


def test_short_is_disabled_in_the_frozen_contract(loaded):
    assert loaded.short_enabled is False


def test_unordered_volatility_cutoffs_are_refused(tmp_path, doc):
    doc["volatility_regime"]["low_below"] = 0.5
    with pytest.raises(C.ContractMismatch, match="ordered"):
        _load_edited(tmp_path, doc)


def test_refitted_volatility_cutoffs_are_refused(tmp_path, doc):
    doc["volatility_regime"]["recomputed_for_d6"] = True
    with pytest.raises(C.ContractMismatch, match="inherited"):
        _load_edited(tmp_path, doc)


@pytest.mark.parametrize("path,value,match", [
    (("scope", "maker_allowed"), True, "maker"),
    (("costs", "maker_used"), True, "maker"),
    (("scope", "liquidation_feature_allowed"), True, "liquidation"),
    (("scope", "ml_allowed"), True, "ML"),
    (("scope", "llm_decision_allowed"), True, "ML"),
    (("exit", "other_exits_allowed"), True, "exits"),
])
def test_banned_capabilities_are_refused(tmp_path, doc, path, value, match):
    doc[path[0]][path[1]] = value
    with pytest.raises(C.ContractMismatch, match=match):
        _load_edited(tmp_path, doc)


def test_leverage_above_the_risk_ceiling_is_refused(tmp_path, doc):
    doc["leverage"]["auto_v1"] = 3.0
    with pytest.raises(C.ContractMismatch, match="leverage"):
        _load_edited(tmp_path, doc)


def test_unfixed_leverage_is_refused(tmp_path, doc):
    doc["leverage"]["fixed"] = False
    with pytest.raises(C.ContractMismatch, match="fixed"):
        _load_edited(tmp_path, doc)


# --- string bindings -----------------------------------------------------------------------

def test_every_binding_is_present_in_the_frozen_contract(loaded):
    for label in ("stop", "sizing", "data_stale", "oi_stale", "insufficient_history",
                  "feature_nan", "vol_low", "spread_wide", "cooldown", "daily_loss_guard",
                  "consecutive_loss", "funding_window"):
        assert label in loaded.bound, label


def test_bound_values_are_the_contract_values(loaded):
    assert loaded.stop == {"sigma_multiplier": 2.5, "horizon_bars": 240,
                           "dist_min": 0.01, "dist_max": 0.10}
    assert loaded.sizing == {"risk_budget": 0.005, "notional_cap_over_equity": 1.0}
    assert loaded.bound["data_stale"]["max_age_ms"] == 90_000
    assert loaded.bound["oi_stale"]["max_age_ms"] == 900_000
    assert loaded.bound["cooldown"]["cooldown_ms"] == 3_600_000
    assert loaded.bound["funding_window"]["min_lead_ms"] == 300_000
    assert loaded.bound["consecutive_loss"] == {"max_consecutive": 4, "block_ms": 86_400_000}


def test_changing_the_stop_formula_text_breaks_the_binding(tmp_path, doc):
    doc["exit"]["risk"]["formula"] = "entry * (1 - clip(3.0 * f_rv24h * sqrt(240), 0.01, 0.10))"
    with pytest.raises(C.ContractMismatch, match="bound to"):
        _load_edited(tmp_path, doc)


def test_changing_a_filter_threshold_text_breaks_the_binding(tmp_path, doc):
    for spec in doc["hard_filters"]:
        if spec["id"] == "H1":
            spec["threshold"] = "last 1m bar close older than 120s"
    with pytest.raises(C.ContractMismatch, match="H1"):
        _load_edited(tmp_path, doc)


def test_numeric_field_disagreeing_with_a_bound_string_is_refused(tmp_path, doc):
    doc["entry"]["cooldown_min"] = 30
    with pytest.raises(C.ContractMismatch, match="cooldown_min"):
        _load_edited(tmp_path, doc)


def test_risk_budget_stated_twice_must_agree(tmp_path, doc):
    doc["sizing"]["risk_budget_pct_of_equity"] = 1.0
    with pytest.raises(C.ContractMismatch, match="risk budget"):
        _load_edited(tmp_path, doc)


def test_stop_horizon_and_max_hold_must_agree(tmp_path, doc):
    doc["exit"]["max_hold_min"] = 120
    with pytest.raises(C.ContractMismatch, match="max_hold_min|stop horizon"):
        _load_edited(tmp_path, doc)


# --- exposed values used by the engine -----------------------------------------------------

def test_engine_reads_thresholds_only_from_the_contract(loaded):
    assert loaded.long_entry_min_score == 50
    assert loaded.score_separation_min == 10
    assert loaded.max_hold_min == 240
    assert loaded.leverage == 1.0
    assert loaded.qty_step == 0.001
    assert loaded.min_order_qty == 0.001
    assert loaded.min_notional_usdt == 5
    assert loaded.bucket_quantiles == (0.10, 0.30, 0.70, 0.90)
    assert loaded.bucket_window_days == 30
    assert loaded.bucketed_features == ("f_basis", "f_oi1h", "f_drop1h")
    assert loaded.feature_ids == ("f_basis", "f_oi1h", "f_drop1h", "f_rv24h")
    assert loaded.hard_filter_ids == tuple(f"H{i}" for i in range(1, 14))
