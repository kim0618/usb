from __future__ import annotations

import pytest

from app.backtest.strategy_h_v2.research.repair import (
    RepairReason,
    RepairRecord,
    classify_repair_reason,
)


@pytest.mark.parametrize(
    ("errors", "expected"),
    [
        (["JSON_PARSE_ERROR: Expecting value: line 1 column 1 (char 0)"],
         RepairReason.JSON_FORMATTING),
        (["fundamental_change['revenue'].code_owned_state='ACCELERATING' does not match the "
          "evidence bundle's actual state 'STABLE'"], RepairReason.NUMERIC_MUTATION),
        (["unknown evidence_id 'SEC:1:A-1:CHUNK:13' - not an evidence chunk in this candidate's "
          "input package @ business_model.revenue_drivers.0"], RepairReason.CITATION),
        (["orphan source_id 'SEC:1:NOPE' not present in the input package @ risks.0"],
         RepairReason.CITATION),
        (["claim_type=FACT requires evidence_id - only UNKNOWN claims may omit the specific "
          "evidence chunk @ business_model.revenue_drivers.0"], RepairReason.CITATION),
        (["stage=REAL_BUSINESS requires at least 2 evidence flags @ future_business.0"],
         RepairReason.EVIDENCE_RULE),
        (["text contains prohibited investment language: 'price target' @ risks.0.description"],
         RepairReason.PROHIBITED_LANGUAGE),
        (["Input should be 'STORY', 'EARLY_EVIDENCE' or 'REAL_BUSINESS' @ future_business.0.stage"],
         RepairReason.ENUM),
        (["Field required @ growth_durability"], RepairReason.SCHEMA),
        (["something nobody has seen before"], RepairReason.OTHER),
    ],
)
def test_classification(errors, expected):
    assert classify_repair_reason(errors) is expected


def test_citation_beats_generic_schema_wording():
    """Pydantic phrases a citation failure as a plain value error, so rule order - not wording -
    has to decide. A repair count that blames SCHEMA for every citation bug is the exact blind
    spot D3 left behind (D3.1 §13)."""
    errors = ["unknown evidence_id 'X' @ risks.0", "Field required @ growth_durability"]
    assert classify_repair_reason(errors) is RepairReason.CITATION


def test_record_is_serializable_and_caps_stored_errors():
    record = RepairRecord(attempt=0, reason=RepairReason.CITATION,
                          errors=[f"e{i}" for i in range(9)], cost_usd=0.55)
    payload = record.to_dict()
    assert payload["reason"] == "CITATION"
    assert payload["error_count"] == 9
    assert len(payload["errors"]) == 5
    assert payload["cost_usd"] == 0.55
