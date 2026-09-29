"""D3.1 repair-reason classification.

Every bounded schema-repair round must be attributable to a cause, not just counted (D3.1 brief
§13). The classifier maps raw validation-error strings to one fixed category so repair pressure can
be traced to a specific contract rule rather than treated as unexplained model noise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class RepairReason(StrEnum):
    JSON_FORMATTING = "JSON_FORMATTING"
    """The response was not one parseable JSON object (fence, prose, truncation)."""
    CITATION = "CITATION"
    """A source_id/evidence_id was missing, unknown, inconsistent, or out of range."""
    SCHEMA = "SCHEMA"
    """A required field was missing, an extra field was present, or a type was wrong."""
    ENUM = "ENUM"
    """A value outside a frozen enum (stage, claim_type, confidence, category, ...)."""
    PROHIBITED_LANGUAGE = "PROHIBITED_LANGUAGE"
    """Investment-decision/valuation language reached a text field."""
    EVIDENCE_RULE = "EVIDENCE_RULE"
    """A contract rule about evidence sufficiency fired (future-business stage floor,
    SUPPORTED-needs-claims, catalyst/risk/invalidation must cite a source)."""
    NUMERIC_MUTATION = "NUMERIC_MUTATION"
    """A code-owned state was restated differently from the evidence bundle."""
    TOKEN_CONTEXT = "TOKEN_CONTEXT"
    """The model hit an output/context limit."""
    OTHER = "OTHER"


#: Ordered most-specific-first: the first matching rule wins, so a citation error is never
#: mislabeled as a generic SCHEMA error just because Pydantic phrased it as a value error.
_RULES: tuple[tuple[RepairReason, tuple[str, ...]], ...] = (
    (RepairReason.JSON_FORMATTING, ("json_parse_error", "expecting value", "unterminated",
                                     "invalid control character", "extra data")),
    (RepairReason.NUMERIC_MUTATION, ("does not match the evidence bundle",)),
    (RepairReason.CITATION, ("orphan source_id", "orphan evidence_id", "requires source_id",
                              "requires evidence_id", "unknown evidence_id",
                              "does not belong to source",
                              "sources[] is missing", "must cite at least one source",
                              "must be evidence-supported", "must cite the evidence")),
    (RepairReason.EVIDENCE_RULE, ("requires at least", "evidence flag", "requires revenue or backlog",
                                   "requires at least one cited claim")),
    (RepairReason.PROHIBITED_LANGUAGE, ("prohibited investment language",)),
    (RepairReason.ENUM, ("should be", "is not a valid enumeration", "input should be")),
    (RepairReason.TOKEN_CONTEXT, ("max_tokens", "context window", "output limit")),
    (RepairReason.SCHEMA, ("field required", "extra_forbidden", "extra inputs are not permitted",
                            "missing", "value_error", "type_error", "@ ")),
)


def classify_repair_reason(errors: list[str]) -> RepairReason:
    blob = " || ".join(errors).lower()
    for reason, needles in _RULES:
        if any(needle in blob for needle in needles):
            return reason
    return RepairReason.OTHER


@dataclass
class RepairRecord:
    """One repair round: what failed, how it was classified, and what it cost."""

    attempt: int
    reason: RepairReason
    errors: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    rejected_output_preview: str = ""
    """The head of the output that failed. D3 stored only a repair *count*, so when ticker A needed
    one repair the cause was already unrecoverable by the time anyone asked. Keeping the rejected
    text means a future repair never becomes unexplainable in the same way."""

    def to_dict(self) -> dict:
        return {
            "attempt": self.attempt, "reason": self.reason.value,
            "errors": self.errors[:5], "error_count": len(self.errors),
            "cost_usd": self.cost_usd,
            "rejected_output_preview": self.rejected_output_preview[:1500],
        }
