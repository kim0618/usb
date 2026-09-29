"""`ExpectationEvidenceBundleV1` - D4's code-owned / source-linked expectation evidence (brief §5).

The D2 counterpart of this bundle (`evidence/bundle.py`'s `EvidenceBundleV2`) states the rule this
one inherits: every field is CODE_OWNED, and nothing in it may hold an interpretation. D4 keeps
that rule and adds one distinction D2 never needed - between a code-generated field and a VERBATIM
SOURCE EXCERPT.

That distinction matters because D2's `_no_interpretive_language` check scans the whole blob for
substrings like "approve" and "buy". Running it over verbatim filing prose would reject perfectly
ordinary text: "the Board approved the repurchase" contains "approve", and D3.1 §I.2 already
measured what that class of check does to real filings (552 `fair value` hits across 3 candidates,
0 of them an investment opinion). So the interpretive-language check here runs over the STRUCTURED
fields this module generates - statuses, numbers, enum values - and never over an excerpt, whose
job is precisely to be the source's own words. `validation_v2.classify_investment_language` remains
the right tool for excerpt text, and D4's own validator applies it to the AI's OUTPUT, where an
investment opinion would actually be D4's rather than a filer's.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.backtest.strategy_h_v2.evidence.bundle import BANNED_INTERPRETIVE_SUBSTRINGS
from app.backtest.strategy_h_v2.evidence.sources import SOURCE_BOUNDARY, SourceProvenance
from app.backtest.strategy_h_v2.expectation.gap_contract import (
    CONTRACT_VERSION,
    EvidenceAvailability,
)

SCHEMA_VERSION = "h_v2_expectation_evidence_bundle_v1"

#: Per-candidate cap on located excerpts, so the D4 prompt stays bounded the way D3's evidence
#: budget is (`prompt_builder.MAX_EVIDENCE_CHARS`). Selection is deterministic (most recent source
#: first, then the chunk's own stable order) - never re-ranked by any content heuristic.
MAX_EXCERPTS_PER_BLOCK = 40
MAX_EXCERPT_CHARS = 1200


def _reject_interpretive(value: Any, where: str) -> None:
    blob = str(value).lower()
    for banned in BANNED_INTERPRETIVE_SUBSTRINGS:
        if banned in blob:
            raise ValueError(
                f"interpretive language {banned!r} is not permitted in {where} - the expectation "
                "evidence bundle is code-owned and holds no judgement"
            )


class LocatedExcerpt(BaseModel):
    """One verbatim passage from one evidence chunk, with the pointer needed to cite it.

    `matched_terms` records WHY code surfaced this passage, so a reader can tell a real guidance
    sentence from a keyword collision without re-running the locator. It is not a score and is
    never summed.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    evidence_id: str
    source_type: str
    published_at: datetime | None
    text: str
    matched_terms: list[str] = Field(default_factory=list)
    source_boundary: str = SOURCE_BOUNDARY

    @field_validator("text")
    @classmethod
    def _bounded(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a located excerpt must carry the source's actual words")
        if len(value) > MAX_EXCERPT_CHARS:
            raise ValueError(f"excerpt exceeds {MAX_EXCERPT_CHARS} chars - truncate at the locator")
        return value

    @model_validator(mode="after")
    def _evidence_belongs_to_source(self) -> "LocatedExcerpt":
        if not self.evidence_id.startswith(f"{self.source_id}:"):
            raise ValueError(
                f"evidence_id {self.evidence_id!r} does not belong to source {self.source_id!r}"
            )
        return self


class EvidenceBlock(BaseModel):
    """A block of located evidence plus its honest availability state.

    A block with `status=SOURCE_NOT_AVAILABLE` and an empty `excerpts` list is a positive finding
    (no provider is connected), not a missing value: D4 brief §13's rule that consensus absence is
    never rewritten as neutral is enforced by `_status_matches_content` below, which refuses the
    combination that would hide it.
    """

    model_config = ConfigDict(extra="forbid")

    status: EvidenceAvailability
    excerpts: list[LocatedExcerpt] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def _status_matches_content(self) -> "EvidenceBlock":
        if self.status == EvidenceAvailability.SOURCE_NOT_AVAILABLE and self.excerpts:
            raise ValueError(
                "SOURCE_NOT_AVAILABLE means no provider is connected at all - it cannot carry "
                "excerpts"
            )
        if self.status == EvidenceAvailability.AVAILABLE and not self.excerpts:
            raise ValueError(
                "AVAILABLE with no excerpts is how an empty result gets read as a full one - use "
                "NOT_FOUND_FOR_CANDIDATE"
            )
        if self.status in (EvidenceAvailability.SOURCE_NOT_AVAILABLE,
                           EvidenceAvailability.NOT_FOUND_FOR_CANDIDATE) and not self.note:
            raise ValueError("an absent block must state why, so absence is never anonymous")
        return self


class EventPriceReaction(BaseModel):
    """One official event and the code-computed price reaction around it.

    Every field here is produced by `price_engine`. `alignment_ambiguous` is carried rather than
    resolved: see that module's docstring for why a 16:00 ET close cannot be pinned to a single UTC
    instant without an exchange calendar this repository does not have.
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str
    event_kind: str
    """`EARNINGS_RESULTS`, `REGULATION_FD_DISCLOSURE`, `SEC_10Q`, ... - the code-owned category the
    D2 source manifest already assigned. Never a characterization of the event's importance."""
    available_at: datetime | None
    event_session: date | None
    prior_session: date | None
    alignment_ambiguous: bool
    ambiguity_reason: str | None
    return_1d: dict[str, Any]
    return_3d: dict[str, Any]
    benchmark_adjusted_1d: dict[str, Any]
    benchmark_adjusted_3d: dict[str, Any]
    pre_event_return_5d: dict[str, Any]
    pre_event_return_20d: dict[str, Any]

    @model_validator(mode="after")
    def _no_interpretive(self) -> "EventPriceReaction":
        _reject_interpretive(self.event_kind, "EventPriceReaction.event_kind")
        return self


class ExpectationEvidenceBundleV1(BaseModel):
    """D4's immutable, per-candidate expectation evidence input.

    Holds no gap state, no confidence, no priced-in assessment and no why-now: those are
    `HExpectationGapAnalysisV1`'s fields, produced by the AI from THIS bundle plus the immutable D3
    research output. Keeping them out of this schema is what makes "the evidence layer cannot
    prejudge the interpretation layer" a structural fact rather than a convention.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    contract_version: str = Field(default=CONTRACT_VERSION)
    bundle_id: str
    company_id: str
    ticker: str
    decision_time: datetime
    data_cutoff: datetime
    generated_at: datetime

    research_input_package_id: str
    """The D2.1 package this candidate's D3 research read - the link that makes D4's evidence and
    D3's evidence provably the same candidate at the same cutoff."""

    guidance: EvidenceBlock
    earnings_history: EvidenceBlock
    management_expectation_signals: EvidenceBlock
    consensus: EvidenceBlock
    estimate_revisions: EvidenceBlock

    price_reaction: list[EventPriceReaction] = Field(default_factory=list)
    pre_event_price_context: dict[str, Any] = Field(default_factory=dict)
    valuation_context_stub: dict[str, Any] = Field(default_factory=dict)

    source_manifest: list[SourceProvenance] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)

    @field_validator("decision_time", "data_cutoff", "generated_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("must be timezone-aware")
        return value

    @field_validator("unknown_fields")
    @classmethod
    def _sorted_unique(cls, values: list[str]) -> list[str]:
        return sorted({v.strip() for v in values if v.strip()})

    @field_validator("pre_event_price_context", "valuation_context_stub", mode="after")
    @classmethod
    def _no_interpretive_language(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Runs over CODE-GENERATED structured fields only - never over `LocatedExcerpt.text`. See
        this module's docstring for the measured reason that distinction exists."""
        _reject_interpretive(value, "a code-owned expectation context block")
        return value

    @model_validator(mode="after")
    def _no_orphan_event_sources(self) -> "ExpectationEvidenceBundleV1":
        known = {s.source_id for s in self.source_manifest}
        if not known:
            return self
        for reaction in self.price_reaction:
            if reaction.source_id not in known:
                raise ValueError(f"orphan price-reaction source_id {reaction.source_id!r}")
        for block in (self.guidance, self.earnings_history,
                      self.management_expectation_signals):
            for excerpt in block.excerpts:
                if excerpt.source_id not in known:
                    raise ValueError(f"orphan excerpt source_id {excerpt.source_id!r}")
        return self

    def valid_evidence_ids(self) -> frozenset[str]:
        """Every evidence_id a D4 claim may cite FROM THIS BUNDLE. A D4 claim may also cite the D3
        input package's chunks - `validate.py` unions the two, and neither set is inferred from the
        other."""
        return frozenset(
            excerpt.evidence_id
            for block in (self.guidance, self.earnings_history,
                          self.management_expectation_signals)
            for excerpt in block.excerpts
        )
