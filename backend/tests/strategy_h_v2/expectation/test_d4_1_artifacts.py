"""D4.1 post-execution artifact integrity tests (brief §28).

These validate what a real D4.1 run produced: telemetry completeness at full fidelity, analysis
immutability, checksum integrity, requested-vs-canonical model verification, consensus absence,
C1/C4 enforcement, code-owned numeric ownership, the gap enum, and the absence of any decision
field.

The artifacts live under `data/runtime/` (gitignored), so every test SKIPS when no D4.1 run manifest
is present rather than failing - a fresh checkout has no run to audit, and a test that fails for
that reason is noise. When a run IS present they are strict.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.backtest.strategy_h_v2.expectation.analysis_schema import (
    BANNED_D4_FIELD_NAMES,
    SCHEMA_VERSION_V1 as D4_SCHEMA_VERSION_V1,
)
from app.backtest.strategy_h_v2.expectation.d4_1_contract import (
    D4_1_HARD_BUDGET_USD,
    TIER_A_TICKERS,
    TIER_B_SAMPLE,
)
from app.backtest.strategy_h_v2.expectation.gap_contract import GAP_CONTRACT_VERSION
from app.backtest.strategy_h_v2.expectation.prompt import PROMPT_VERSION_V1
from app.backtest.strategy_h_v2.expectation.validate import VALIDATION_CONTRACT_VERSION_V1
from app.backtest.strategy_h_v2.research.telemetry_contract_v2 import checksum

RUN_ROOT = Path("data/runtime/strategy_h_v2/d4_1")
ANALYSES_ROOT = RUN_ROOT / "analyses"
REQUESTED_MODEL = "claude-opus-5-5"

REQUIRED_TELEMETRY_FIELDS = (
    "schema_version", "analysis_id", "analysis_run_id", "candidate_id", "ticker",
    "research_input_id", "research_output_checksum", "expectation_evidence_id",
    "expectation_evidence_checksum", "input_package_id", "input_package_checksum",
    "model_requested", "canonical_model", "model_mismatch", "prompt_version",
    "schema_version_out", "gap_contract_version", "validation_contract_version",
    "started_at", "completed_at", "initial_raw_response", "initial_parse_status",
    "initial_validation_status", "initial_failure_codes", "initial_failure_details",
    "repair_rounds", "final_raw_response", "final_output", "final_output_checksum",
    "final_status", "applied_contract_rules", "input_tokens", "output_tokens", "cost_usd",
)

GAP_STATES = {"WIDE_POSITIVE", "POSITIVE", "NEUTRAL", "NEGATIVE", "WIDE_NEGATIVE", "UNKNOWN"}


def _manifests() -> list[Path]:
    return sorted(RUN_ROOT.glob("D4_1_*.manifest.json"))


def _records() -> list[dict]:
    if not ANALYSES_ROOT.exists():
        return []
    out = []
    for path in sorted(ANALYSES_ROOT.rglob("*.json")):
        if path.name == "expectation_evidence.json":
            continue
        out.append(json.loads(path.read_text()))
    return out


HAS_RUN = pytest.mark.skipif(not _manifests(), reason="no D4.1 run present")


@HAS_RUN
def test_manifests_use_the_frozen_sample_and_nothing_else():
    allowed = set(TIER_A_TICKERS) | {e.ticker for e in TIER_B_SAMPLE}
    for path in _manifests():
        manifest = json.loads(path.read_text())
        assert {r["ticker"] for r in manifest["results"]} <= allowed


@HAS_RUN
def test_every_result_has_a_stored_analysis_file():
    for path in _manifests():
        manifest = json.loads(path.read_text())
        for result in manifest["results"]:
            if result["final_status"] == "D3_LEG_FAILED":
                continue
            stored = ANALYSES_ROOT / manifest["run_id"] / result["ticker"]
            assert (stored / f"{result['analysis_id']}.json").exists()
            assert (stored / "expectation_evidence.json").exists(), (
                "the analysis checksums a bundle, so the bundle must be stored beside it"
            )


@HAS_RUN
def test_telemetry_has_every_required_field():
    for record in _records():
        missing = [f for f in REQUIRED_TELEMETRY_FIELDS if f not in record]
        assert not missing, f"{record.get('analysis_id')} is missing {missing}"


@HAS_RUN
def test_raw_responses_are_full_text_not_previews():
    """The MRVI failure D4's ledger exists to prevent: a stored response that is only a preview is
    unauditable, so the checksum must be of the text actually stored."""
    for record in _records():
        for response in [record["initial_raw_response"]] + [
            r["raw_repair_response"] for r in record["repair_rounds"]
        ] + ([record["final_raw_response"]] if record["final_raw_response"] else []):
            assert checksum(response["raw_text"]) == response["checksum"]
            assert not response["raw_text"].endswith("...")


@HAS_RUN
def test_final_output_checksum_integrity():
    for record in _records():
        if record["final_status"] != "OK":
            continue
        recomputed = checksum(json.dumps(record["final_output"], sort_keys=True, default=str))
        assert recomputed == record["final_output_checksum"]


@HAS_RUN
def test_analysis_files_are_one_per_id_never_overwritten():
    ids = [r["analysis_id"] for r in _records()]
    assert len(ids) == len(set(ids))


@HAS_RUN
def test_canonical_model_matches_the_requested_model():
    for record in _records():
        assert record["model_requested"] == REQUESTED_MODEL
        assert record["canonical_model"] is not None, "a live response must report its own model"
        assert record["model_mismatch"] is False
        assert record["canonical_model"] == REQUESTED_MODEL


@HAS_RUN
def test_frozen_versions_used_with_no_fallback():
    """D4.1's records are pinned to the V1 literals they were produced under, not to whatever the
    modules currently export.

    This assertion used to read the live constants, which made it an assertion about today's code
    rather than about a finished run. D4.2 bumped three of those constants - prompt, output schema
    and validation contract - so the version-pinned form is now the only one that says what it
    means: these artifacts came from Contract V1, and a later contract does not retroactively
    reissue them. `gap_contract_version` is compared against the live constant on purpose: the gap
    contract is unchanged by V2, and this line is where that would stop being true.
    """
    for record in _records():
        assert record["prompt_version"] == PROMPT_VERSION_V1 == "h_v2_d4_expectation_gap_v1"
        assert record["schema_version_out"] == D4_SCHEMA_VERSION_V1
        assert record["gap_contract_version"] == GAP_CONTRACT_VERSION
        assert record["validation_contract_version"] == VALIDATION_CONTRACT_VERSION_V1


@HAS_RUN
def test_hard_budget_never_breached_across_every_tier():
    total = sum(json.loads(p.read_text())["total_cost_usd"] for p in _manifests())
    assert total <= D4_1_HARD_BUDGET_USD


@HAS_RUN
def test_consensus_stays_unavailable_in_every_output():
    for record in _records():
        if record["final_status"] != "OK":
            continue
        evidence = record["final_output"]["market_expectation_evidence"]
        assert evidence["consensus_status"] == "SOURCE_NOT_AVAILABLE"
        assert evidence["estimate_revisions_status"] == "SOURCE_NOT_AVAILABLE"


@HAS_RUN
def test_c4_confidence_ceiling_holds_with_no_consensus_source():
    for record in _records():
        if record["final_status"] != "OK":
            continue
        output = record["final_output"]
        assert output["confidence_ceiling"] == "MEDIUM"
        assert output["expectation_gap_confidence"] != "HIGH"
        assert "C4_NO_CONSENSUS_CEILING" in "".join(output["applied_contract_rules"]) or any(
            "C4" in rule for rule in output["applied_contract_rules"]
        )


@HAS_RUN
def test_c1_positive_gap_rests_on_non_price_expectation_evidence():
    """Restated here rather than imported: a rule checked only by the code that enforces it proves
    nothing about a real output."""
    for record in _records():
        if record["final_status"] != "OK":
            continue
        output = record["final_output"]
        if output["expectation_gap"] not in ("POSITIVE", "WIDE_POSITIVE"):
            continue
        evidence = output["market_expectation_evidence"]
        non_price = (
            [a for a in evidence["guidance_assessments"]
             if a["state"] not in ("NOT_PROVIDED", "UNKNOWN")]
            + [r for r in evidence["result_vs_guidance"]
               if r["state"] in ("ABOVE_COMPANY_GUIDANCE", "WITHIN_COMPANY_GUIDANCE",
                                 "BELOW_COMPANY_GUIDANCE")]
            + list(evidence["management_signal_changes"])
        )
        assert non_price, f"{record['ticker']} has a positive gap with no non-price evidence"
        assert output["fundamental_reality_summary"]["improvement_claims"]


@HAS_RUN
def test_gap_and_confidence_use_the_frozen_enums():
    for record in _records():
        if record["final_status"] != "OK":
            continue
        output = record["final_output"]
        assert output["expectation_gap"] in GAP_STATES
        assert output["expectation_gap_confidence"] in {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}
        assert (output["expectation_gap"] == "UNKNOWN") == (
            output["expectation_gap_confidence"] == "UNKNOWN"), "rule C5"


@HAS_RUN
def test_no_decision_or_valuation_field_at_any_depth():
    def walk(node, path="root"):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in BANNED_D4_FIELD_NAMES, f"{path}.{key} is a prohibited D4 field"
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    for record in _records():
        if record["final_status"] == "OK":
            walk(record["final_output"])


@HAS_RUN
def test_wide_positive_carries_the_deferred_valuation_conjunct():
    for record in _records():
        if record["final_status"] != "OK":
            continue
        output = record["final_output"]
        if output["expectation_gap"] == "WIDE_POSITIVE":
            assert output["wide_positive_deferred_conjunct"], (
                "a D4 WIDE_POSITIVE must record that D0's valuation conjunct is UNEVALUATED"
            )
        else:
            assert output["wide_positive_deferred_conjunct"] is None


@HAS_RUN
def test_code_owned_numeric_ownership_is_recorded_not_recomputed():
    """The four contract-residue fields are code-filled. Their presence on every stored output is
    the record that D4 applied the frozen rules rather than letting the model report them."""
    for record in _records():
        if record["final_status"] != "OK":
            continue
        output = record["final_output"]
        assert output["applied_contract_rules"], "no rule fired at all - the ceiling always does"
        assert output["confidence_ceiling"]
        assert output["d6_approve_precondition"] in ("SATISFIED", "BLOCKED")
        assert output["applied_contract_rules"] == record["applied_contract_rules"]


@HAS_RUN
def test_token_metadata_is_null_or_real_never_invented():
    for record in _records():
        for field in ("input_tokens", "output_tokens"):
            value = record[field]
            assert value is None or (isinstance(value, int) and value > 0)
