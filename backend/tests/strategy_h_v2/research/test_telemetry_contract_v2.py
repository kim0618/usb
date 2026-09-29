"""D3.2R repair telemetry + raw response retention contract tests (brief §14/§15/§20). Exercised
against real D3.1 repair data (via fixtures), 0 model calls."""

from __future__ import annotations

import pytest

from app.backtest.strategy_h_v2.research.telemetry_contract_v2 import (
    RawResponseRecordV1,
    RepairTelemetryRecordV1,
    checksum,
    extract_failure_field,
)


def test_checksum_deterministic():
    assert checksum("abc") == checksum("abc")
    assert checksum("abc") != checksum("abd")


def test_extract_failure_field_matches_real_error_shapes():
    """Real error strings from `schema.py`/`validate.py` - see D3.1's own MRVI repair round."""
    assert extract_failure_field(
        "Value error, stage=COMMERCIALIZING requires at least 2 evidence flag(s), got 1 - a "
        "future-business item cannot be staged above what its own evidence flags support "
        "@ future_business.0"
    ) == "future_business.0"
    assert extract_failure_field("JSON_PARSE_ERROR: Expecting value: line 1 column 1") is None


def test_repair_telemetry_from_real_mrvi_repair_round():
    """MRVI's actual first repair round (D3.1 manifest, D3.2 §J.7) - the exact case this contract
    exists to make reproducible next time."""
    record = RepairTelemetryRecordV1.from_repair_record(
        candidate_id="MRVI", initial_output_checksum=checksum("<MRVI initial raw output>"),
        validation_contract_version="h_v2_d3_1_batch2_v1", repair_prompt_version="v1",
        final_status="SCHEMA_VALIDATION_FAILED", reason="EVIDENCE_RULE",
        errors=["Value error, stage=COMMERCIALIZING requires at least 2 evidence flag(s), got 1 - "
               "a future-business item cannot be staged above what its own evidence flags support "
               "@ future_business.0"],
        attempt=0, rejected_output_preview="<truncated 1500-char preview>",
    )
    assert record.candidate_id == "MRVI"
    assert record.failure_codes == ["EVIDENCE_RULE"]
    assert record.failure_fields == ["future_business.0"]
    assert record.final_status == "SCHEMA_VALIDATION_FAILED"
    # The honest gap this contract exists to name, not paper over:
    assert record.repaired_output_checksum is None
    assert record.raw_response_ref is None


def test_repair_telemetry_round_trips_through_to_dict():
    record = RepairTelemetryRecordV1(
        candidate_id="LNG", initial_output_checksum="x" * 64,
        validation_contract_version="h_v2_d3_1_batch2_v1", failure_codes=["PROHIBITED_LANGUAGE"],
        failure_fields=["fundamental_change.1.explanation.0.text"], repair_attempt=0,
        repair_prompt_version="v1", repaired_output_checksum="y" * 64, final_status="OK",
        raw_response_ref="y" * 64,
    )
    as_dict = record.to_dict()
    assert as_dict["candidate_id"] == "LNG"
    assert as_dict["raw_response_ref"] == "y" * 64


def test_raw_response_record_captures_full_text_untruncated():
    """The specific fix for MRVI's gap: no 1,500-char ceiling anywhere in this path."""
    long_text = "x" * 50_000
    record = RawResponseRecordV1.capture(
        candidate_id="MRVI", attempt=0, role="initial", raw_text=long_text,
        prompt_version="h_v2_d3_research_v2",
    )
    assert len(record.raw_text) == 50_000
    assert record.checksum == checksum(long_text)


def test_raw_response_record_rejects_unknown_role():
    with pytest.raises(ValueError):
        RawResponseRecordV1.capture(candidate_id="X", attempt=0, role="final",
                                    raw_text="text", prompt_version="v")


def test_repair_telemetry_and_raw_response_link_by_checksum():
    """A repair round's `raw_response_ref` should equal the corresponding raw response's own
    `checksum` - demonstrating the two contracts are meant to be joined this way, not just
    coexist."""
    raw = RawResponseRecordV1.capture(candidate_id="LNG", attempt=1, role="repair",
                                      raw_text="<repaired output>",
                                      prompt_version="h_v2_d3_research_v2")
    telemetry = RepairTelemetryRecordV1(
        candidate_id="LNG", initial_output_checksum="x" * 64,
        validation_contract_version="h_v2_d3_1_batch2_v1", repair_attempt=1,
        repaired_output_checksum=raw.checksum, final_status="OK", raw_response_ref=raw.checksum,
    )
    assert telemetry.raw_response_ref == raw.checksum == telemetry.repaired_output_checksum
