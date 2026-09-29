"""D4.2 Defect 2: the direction allow-list has ONE definition, and the model can read it.

D4.1 failed here on its first two live candidates, and the failure was not a model failure. The
Python field validator enforced seven tokens; the JSON schema the model was shown said
`{"title": "Direction", "type": "string"}`. Asked for a string, the model supplied strings -
`INCREASED`, `QUANTIFIED_AND_EXTENDED_TO_2027`, and one sentence containing three tonnages - and
was rejected by a rule it had no way to see.

These tests assert the property that would have prevented that: validator, Pydantic field, prompt
schema and test fixtures all resolve to `ManagementSignalDirection`, and a disagreement between any
two of them fails here rather than in a live run.
"""

from __future__ import annotations

import pytest
from d4_helpers import EVIDENCE_ID

from app.backtest.strategy_h_v2.expectation.analysis_schema import (
    ExpectationConflictV1,
    ManagementSignalChangeV1,
)
from app.backtest.strategy_h_v2.expectation.contract_v2 import (
    V1_FROZEN_CONFLICT_ORIGINS,
    V1_FROZEN_DIRECTIONS,
    ConflictOrigin,
    ManagementSignalDirection,
)
from app.backtest.strategy_h_v2.expectation.gap_contract import EvidenceAvailability
from app.backtest.strategy_h_v2.expectation.prompt import content_only_schema
from app.backtest.strategy_h_v2.research.schema import Confidence


def _enum_members(schema: dict, field_path: tuple[str, ...]) -> list[str]:
    """The members the MODEL sees for a field, resolved through `$ref` exactly as a reader would.

    Deliberately reads the generated schema rather than the Python enum: the defect was that those
    two disagreed, so a test that consulted the enum would have passed all the way through D4.1.
    """
    defs = schema.get("$defs") or {}
    node = schema
    for name in field_path[:-1]:
        ref = (node.get("properties") or {}).get(name) or {}
        target = ref.get("$ref") or (ref.get("items") or {}).get("$ref")
        if target is None:
            for candidate in ref.get("anyOf") or []:
                target = target or candidate.get("$ref")
        node = defs[target.split("/")[-1]]
    field = (node.get("properties") or {})[field_path[-1]]
    ref = field.get("$ref") or (field.get("allOf") or [{}])[0].get("$ref")
    if ref is None:
        for candidate in field.get("anyOf") or []:
            ref = ref or candidate.get("$ref")
    if ref is None:
        return field.get("enum") or []
    return defs[ref.split("/")[-1]].get("enum") or []


# --- the single source of truth ------------------------------------------------------------------

def test_the_enum_exposes_exactly_the_seven_values_v1_enforced():
    assert {d.value for d in ManagementSignalDirection} == V1_FROZEN_DIRECTIONS
    assert len(V1_FROZEN_DIRECTIONS) == 7


def test_the_json_schema_shown_to_the_model_lists_exactly_those_values():
    """The assertion D4.1 needed and did not have."""
    members = _enum_members(
        content_only_schema(),
        ("market_expectation_evidence", "management_signal_changes", "direction"),
    )
    assert sorted(members) == sorted(V1_FROZEN_DIRECTIONS)


def test_validator_field_and_schema_all_resolve_to_one_definition():
    """Not "they happen to agree" - the same object. A copy that agreed today is exactly what
    produced the D4.1 defect: two statements of one rule, only one of them visible."""
    field = ManagementSignalChangeV1.model_fields["direction"]
    assert field.annotation is ManagementSignalDirection
    schema_members = set(_enum_members(
        content_only_schema(),
        ("market_expectation_evidence", "management_signal_changes", "direction"),
    ))
    assert schema_members == {d.value for d in ManagementSignalDirection} == V1_FROZEN_DIRECTIONS


# --- rejection ------------------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "INCREASED",
    "QUANTIFIED_AND_EXTENDED_TO_2027",
    "UP (small): 911,400 -> 915,400 -> 917,000 tonnes across three consecutive filings",
    "strengthened",
    "",
])
def test_an_invalid_direction_is_rejected(value: str):
    """The first three are verbatim from D4.1's live responses."""
    with pytest.raises(ValueError):
        ManagementSignalChangeV1(topic="ramp", direction=value, confidence=Confidence.LOW,
                                 evidence_ids=[EVIDENCE_ID, EVIDENCE_ID])


@pytest.mark.parametrize("value", sorted(V1_FROZEN_DIRECTIONS))
def test_every_frozen_direction_is_accepted(value: str):
    change = ManagementSignalChangeV1(topic="ramp", direction=value, confidence=Confidence.LOW,
                                      evidence_ids=[EVIDENCE_ID, EVIDENCE_ID])
    assert change.direction == value


def test_the_rejection_message_names_the_members():
    """D4.1's repair rounds did get the member list, in the error text - but only after paying for
    a call. Naming them in the schema is what makes the first call informed."""
    with pytest.raises(ValueError, match="STRENGTHENED"):
        ManagementSignalChangeV1(topic="ramp", direction="INCREASED", confidence=Confidence.LOW,
                                 evidence_ids=[EVIDENCE_ID, EVIDENCE_ID])


# --- the same defect class elsewhere (brief §8 audit) --------------------------------------------

def test_conflict_origin_is_exposed_as_an_enum_too():
    assert {o.value for o in ConflictOrigin} == V1_FROZEN_CONFLICT_ORIGINS
    members = _enum_members(content_only_schema(), ("conflicts", "origin"))
    assert sorted(members) == sorted(V1_FROZEN_CONFLICT_ORIGINS)


def test_an_invalid_conflict_origin_is_rejected():
    with pytest.raises(ValueError):
        ExpectationConflictV1(
            topic="guidance", description="two filings disagree",
            evidence_ids=[EVIDENCE_ID, EVIDENCE_ID], resolution_status="UNRESOLVED",
            materiality="MATERIAL", origin="D5_VALUATION", confidence=Confidence.LOW,
        )


def test_the_availability_statuses_are_exposed_as_enums():
    """V1 typed `consensus_status` and `estimate_revisions_status` as `str` and checked them
    against the bundle afterwards, so the model's only feedback was a mismatch error naming a value
    it had never been offered. The legal set is unchanged - it is now visible."""
    schema = content_only_schema()
    for field in ("consensus_status", "estimate_revisions_status"):
        members = _enum_members(schema, ("market_expectation_evidence", field))
        assert sorted(members) == sorted(a.value for a in EvidenceAvailability)
