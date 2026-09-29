"""D3.2F research-attempt record + immutable storage tests (brief §17). 0 model calls."""

from __future__ import annotations

import pytest

from app.backtest.strategy_h_v2.research.research_attempt_v2 import (
    AttemptAlreadyExistsError,
    RepairAttemptRecordV1,
    ResearchAttemptRecordV1,
    compute_model_mismatch,
    extract_usage,
    load_attempt,
    store_attempt,
)
from app.backtest.strategy_h_v2.research.telemetry_contract_v2 import RawResponseRecordV1


def _raw(role="initial", attempt=0, text="response text"):
    return RawResponseRecordV1.capture(candidate_id="LUV", attempt=attempt, role=role,
                                       raw_text=text, prompt_version="h_v2_d3_research_v2")


def _record(**overrides) -> ResearchAttemptRecordV1:
    kwargs = dict(
        attempt_id="LUV-attempt-1", research_run_id="D3_3-RUN-1", candidate_id="LUV", ticker="LUV",
        cik="0000092380", input_package_id="P-1:LUV", input_package_checksum="deadbeef",
        model_requested="claude-opus-5-5", canonical_model="claude-opus-5-5", model_mismatch=False,
        prompt_version="h_v2_d3_research_v2", schema_version="h_research_interpretation_v2",
        validation_contract_version="h_v2_d3_2_validation_contract_v2", started_at="2026-09-29T00:00:00Z",
        completed_at="2026-09-29T00:01:00Z", initial_raw_response=_raw(),
        initial_parse_status="PARSED", initial_validation_status="OK", final_status="OK",
    )
    kwargs.update(overrides)
    return ResearchAttemptRecordV1(**kwargs)


# --- full raw response persistence (brief §4/§17) ------------------------------------------------

def test_full_raw_response_not_truncated():
    long_text = "x" * 40_000
    record = _record(initial_raw_response=_raw(text=long_text))
    assert len(record.to_dict()["initial_raw_response"]["raw_text"]) == 40_000


def test_repair_response_persistence():
    repair = RepairAttemptRecordV1(
        attempt_number=0, failure_codes_before=["EVIDENCE_RULE"],
        repair_prompt_version="h_v2_d3_research_v2", raw_repair_response=_raw(role="repair", text="y" * 5000),
        validation_after="OK", cost_usd=0.55,
    )
    record = _record(repair_attempts=[repair])
    as_dict = record.to_dict()
    assert len(as_dict["repair_attempts"]) == 1
    assert len(as_dict["repair_attempts"][0]["raw_repair_response"]["raw_text"]) == 5000
    assert as_dict["repair_attempts"][0]["failure_codes_before"] == ["EVIDENCE_RULE"]


# --- immutability (brief §5/§17) -----------------------------------------------------------------

def test_immutable_attempt_cannot_be_overwritten(tmp_path):
    record = _record()
    path = store_attempt(tmp_path, record)
    assert path.exists()
    with pytest.raises(AttemptAlreadyExistsError):
        store_attempt(tmp_path, record)


def test_retry_uses_a_new_attempt_id_not_a_collision(tmp_path):
    first = _record(attempt_id="LUV-attempt-1")
    second = _record(attempt_id="LUV-attempt-2")
    path1 = store_attempt(tmp_path, first)
    path2 = store_attempt(tmp_path, second)
    assert path1 != path2
    assert path1.exists() and path2.exists()


def test_load_attempt_reads_back_full_fidelity(tmp_path):
    record = _record(initial_raw_response=_raw(text="z" * 10_000))
    path = store_attempt(tmp_path, record)
    loaded = load_attempt(path)
    assert len(loaded["initial_raw_response"]["raw_text"]) == 10_000
    assert loaded["ticker"] == "LUV"


# --- requested/canonical model mismatch (brief §7/§17) -------------------------------------------

def test_model_match_no_mismatch():
    assert compute_model_mismatch("claude-opus-5-5", "claude-opus-5-5") is False


def test_model_mismatch_detected():
    assert compute_model_mismatch("claude-opus-5-5", "claude-sonnet-5") is True


def test_model_mismatch_unknown_canonical_is_not_flagged_as_mismatch():
    """No canonical model reported (e.g. the call errored before usage was known) is not itself
    evidence of a mismatch - it is a missing fact, handled by `final_status`, not this flag."""
    assert compute_model_mismatch("claude-opus-5-5", None) is False


def test_record_carries_mismatch_flag_end_to_end():
    record = _record(canonical_model="claude-sonnet-5",
                     model_mismatch=compute_model_mismatch("claude-opus-5-5", "claude-sonnet-5"))
    assert record.to_dict()["model_mismatch"] is True


# --- usage extraction (brief §3) - honest absence, not a fabricated field ------------------------

def test_extract_usage_returns_none_when_absent():
    assert extract_usage({"result": "text", "total_cost_usd": 1.0}) == (None, None)


def test_extract_usage_reads_top_level_usage_object_if_present():
    assert extract_usage({"usage": {"input_tokens": 100, "output_tokens": 50}}) == (100, 50)
