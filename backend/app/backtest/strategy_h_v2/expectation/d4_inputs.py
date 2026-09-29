"""Defect 4's repair: the symbols D4 needs that today exist only in an uncommitted working tree.

D4's package imported three things that a clean checkout does not have:

  `research.validate.valid_evidence_ids`   - added by an uncommitted edit to `research/validate.py`
  `research.schema.ConflictResolution`     - added by an uncommitted edit to `research/schema.py`
  `research.repair.classify_repair_reason` - an untracked file (runner path only)

That is a reproducibility blocker rather than a bug: the D4 code is correct and the tests pass, but
they pass only on one machine's unsaved edits. D4.1 did not create it and D4.2 may not fix it by
editing those files - they are the user's uncommitted work (brief §14). So the functionality D4
needs is implemented here, in a D4-owned committed module, and D4 imports it from here.

Duplication is the cost, and it is paid deliberately rather than pretended away. Each definition
below is checked against the working-tree original by `test_clean_checkout.py` WHEN that original
is importable: identical members, identical classification over a fixed corpus. A drift is a test
failure, not a silent fork. What is NOT duplicated is `ClaimV2` - D4's contract reuses it by
identity (analysis_schema's docstring says why), and a D4-owned copy of the claim contract would
be the semantic change this whole stage exists to avoid.
"""

from __future__ import annotations

from enum import StrEnum

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1

D4_INPUTS_CONTRACT_VERSION = "h_v2_d4_inputs_v2"


# ---------------------------------------------------------------------------------------------
# `research.validate.valid_evidence_ids`
# ---------------------------------------------------------------------------------------------

def package_evidence_ids(package: AIResearchInputV1) -> set[str]:
    """The chunk ids that exist IN THIS CANDIDATE'S package - nothing else is citable.

    Per-candidate on purpose (D3.1 §2.1): another company's chunks are simply absent from this set,
    so a cross-company citation is rejected by the same check that rejects an invented chunk index.
    Same body as the working tree's `valid_evidence_ids`; `test_clean_checkout.py` asserts the two
    agree on real packages whenever that function can be imported.
    """
    return {chunk.evidence_id for chunk in package.chunks}


# ---------------------------------------------------------------------------------------------
# `research.schema.ConflictResolution`
# ---------------------------------------------------------------------------------------------

class ConflictResolution(StrEnum):
    """Whether the official record itself settles a disagreement between two sources.

    Values are the working tree's verbatim. This is not a new enum and D4 does not get to decide
    what resolving a conflict means - it gets to be importable.
    """

    RESOLVED = "RESOLVED"
    """A later official source supersedes the earlier one and says so."""
    UNRESOLVED = "UNRESOLVED"
    """Both sources stand; the record does not reconcile them."""
    UNKNOWN = "UNKNOWN"
    """It cannot be determined from the collected evidence which reading holds."""


# ---------------------------------------------------------------------------------------------
# `research.repair.classify_repair_reason`
# ---------------------------------------------------------------------------------------------

class D4RepairReason(StrEnum):
    """Why a bounded repair round was needed. Members and spellings are the D3.1 classifier's, so a
    D4.2 telemetry record stays readable next to a D4.1 one."""

    JSON_FORMATTING = "JSON_FORMATTING"
    CITATION = "CITATION"
    SCHEMA = "SCHEMA"
    ENUM = "ENUM"
    PROHIBITED_LANGUAGE = "PROHIBITED_LANGUAGE"
    EVIDENCE_RULE = "EVIDENCE_RULE"
    NUMERIC_MUTATION = "NUMERIC_MUTATION"
    TOKEN_CONTEXT = "TOKEN_CONTEXT"
    OTHER = "OTHER"


#: Ordered most-specific-first: the first matching rule wins, so a citation error is never
#: mislabeled as a generic SCHEMA error just because Pydantic phrased it as a value error.
_RULES: tuple[tuple[D4RepairReason, tuple[str, ...]], ...] = (
    (D4RepairReason.JSON_FORMATTING, ("json_parse_error", "expecting value", "unterminated",
                                      "invalid control character", "extra data")),
    (D4RepairReason.NUMERIC_MUTATION, ("does not match the evidence bundle",)),
    (D4RepairReason.CITATION, ("orphan source_id", "orphan evidence_id", "requires source_id",
                               "requires evidence_id", "unknown evidence_id",
                               "does not belong to source",
                               "sources[] is missing", "must cite at least one source",
                               "must be evidence-supported", "must cite the evidence")),
    (D4RepairReason.EVIDENCE_RULE, ("requires at least", "evidence flag",
                                    "requires revenue or backlog",
                                    "requires at least one cited claim")),
    (D4RepairReason.PROHIBITED_LANGUAGE, ("prohibited investment language",)),
    (D4RepairReason.ENUM, ("should be", "is not a valid enumeration", "input should be")),
    (D4RepairReason.TOKEN_CONTEXT, ("max_tokens", "context window", "output limit")),
    (D4RepairReason.SCHEMA, ("field required", "extra_forbidden", "extra inputs are not permitted",
                             "missing", "value_error", "type_error", "@ ")),
)


def classify_d4_repair_reason(errors: list[str]) -> D4RepairReason:
    blob = " || ".join(errors).lower()
    for reason, needles in _RULES:
        if any(needle in blob for needle in needles):
            return reason
    return D4RepairReason.OTHER
