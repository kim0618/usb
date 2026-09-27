"""F0 rules freeze: checksum guard, static contract checks and identity. Reads no market data."""

import copy
import hashlib
import json

import pytest

from app.backtest.strategy_f0_regular_after import config, identity, preflight


def _payload():
    return json.loads(config.RULES_PATH.read_text(encoding="utf-8"))


def test_declared_checksum_matches_rules_and_sha_file():
    rules = config.load_rules()
    record = json.loads(config.SHA_PATH.read_text(encoding="utf-8"))
    assert rules.checksum == config.DECLARED_RULES_CHECKSUM == record["canonical_sha256"]
    assert hashlib.sha256(config.RULES_PATH.read_bytes()).hexdigest() == record["file_sha256"]
    assert record["frozen_before"] == "F0_RESULT_EXECUTION"
    assert record["frozen_at_kst"].startswith("2026-09-27")


def test_prereg_document_is_the_frozen_one():
    record = json.loads(config.SHA_PATH.read_text(encoding="utf-8"))
    doc = config.RULES_PATH.parent / record["document"]
    assert hashlib.sha256(doc.read_bytes()).hexdigest() == record["document_file_sha256"]


def test_checksum_is_deterministic_and_order_free():
    payload = _payload()
    reordered = json.loads(json.dumps(payload, sort_keys=True))
    assert config.canonical_checksum(payload) == config.canonical_checksum(reordered)


def test_one_character_edit_fails_closed(tmp_path):
    text = config.RULES_PATH.read_text(encoding="utf-8").replace('"min_trades": 300', '"min_trades": 299')
    edited = tmp_path / "rules.json"
    edited.write_text(text, encoding="utf-8")
    with pytest.raises(config.RulesChanged):
        config.load_rules(edited)


def test_missing_rules_file_is_a_hard_fail(tmp_path):
    with pytest.raises(config.F0HardFail):
        config.load_rules(tmp_path / "absent.json")


def test_static_preflight_passes():
    failed = [r for r in preflight.validate(_payload()) if not r["ok"]]
    assert failed == []


@pytest.mark.parametrize("mutate, check", [
    (lambda p: p["features"]["definitions"]["day_return"].update(window=[959, 966]), "no_future_field:day_return"),
    (lambda p: p["features"]["definitions"]["close_vs_VWAP"]["inputs"].append("post:m=960:close"), "no_future_field:close_vs_VWAP"),
    (lambda p: p["hypotheses"]["H3"]["all"][0].__setitem__(2, 1.5), "hypothesis_thresholds:H3"),
    (lambda p: p["gates"]["statistical"].pop("seed"), "bootstrap_seed_and_iterations"),
    (lambda p: p["execution"]["primary_entry"].update(if_missing="NEXT_BAR"), "primary_entry_defined"),
    (lambda p: p["gates"]["cost"].update(primary_bp=20), "cost_grid"),
    (lambda p: p["verdict"]["vocabulary"].append("BORDERLINE"), "verdict_contract"),
])
def test_preflight_detects_contract_violations(mutate, check):
    payload = copy.deepcopy(_payload())
    mutate(payload)
    failed = {r["check"] for r in preflight.validate(payload) if not r["ok"]}
    assert check in failed


def test_run_identity_refuses_undeclared_components():
    parts = {k: "x" for k in identity.COMPONENTS}
    digest, run_id = identity.run_identity(parts)
    assert run_id == f"f0-{digest[:12]}"
    with pytest.raises(ValueError):
        identity.run_identity({**parts, "wall_clock": "now"})
