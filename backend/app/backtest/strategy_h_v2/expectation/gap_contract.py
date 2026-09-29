"""D4 Expectation Gap contract: every frozen enum and every deterministic rule, decided BEFORE any
D4 live output exists.

This module holds the decisions, not the plumbing. Everything here was fixed while writing the D4
contract document and before a single D4.1 model call, so a rule cannot later be softened to make a
run look better - the same discipline `research/d3_3_contract.py` applied to D3.3's L1-L7 gates.

Two things this module deliberately does NOT contain:

1. Any investment-decision vocabulary. `ExpectationGapState` is the D0 §L enum verbatim and stops
   there - there is no APPROVE/WATCH/REJECT enum in this package, and `D6ApprovePrecondition` below
   is a *precondition record* for a stage that does not exist yet, not a decision. See its own
   docstring for why that distinction is enforced rather than merely intended.
2. Any valuation judgement. D0 §L's own `WIDE_POSITIVE` criteria include "valuation has not yet
   re-rated to reflect it", which D4 cannot evaluate because D5 has not run. That clause is
   recorded as an explicitly deferred, unmet conjunct (`WIDE_POSITIVE_VALUATION_CONJUNCT_DEFERRED`)
   rather than silently dropped, so D6 has to re-check it instead of inheriting a D4 verdict that
   quietly skipped a criterion D0 froze.
"""

from __future__ import annotations

from enum import StrEnum

from app.backtest.strategy_h_v2.research.schema import Confidence

#: The D0 architecture contract is unchanged by D4 - same value as `schema.CONTRACT_VERSION`,
#: restated rather than imported for the reason `schema_v2.py` gives for doing the same.
CONTRACT_VERSION = "h_v2_d0_v1"
#: This module's own frozen-rule version. A change to any rule below is a new value here, never an
#: edit in place once D4.1 has run against it.
GAP_CONTRACT_VERSION = "h_v2_d4_gap_contract_v1"


# ---------------------------------------------------------------------------------------------
# Frozen enums
# ---------------------------------------------------------------------------------------------


class ExpectationGapState(StrEnum):
    """D0 §L's frozen six states, verbatim. Never converted to, derived from, or accompanied by a
    numeric score - D0 §L defers a numeric gap until a PIT consensus/estimate-revision source is
    proven available, and §13's measurement below confirms none is."""

    WIDE_POSITIVE = "WIDE_POSITIVE"
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"
    WIDE_NEGATIVE = "WIDE_NEGATIVE"
    UNKNOWN = "UNKNOWN"


POSITIVE_STATES = frozenset({ExpectationGapState.WIDE_POSITIVE, ExpectationGapState.POSITIVE})
NEGATIVE_STATES = frozenset({ExpectationGapState.WIDE_NEGATIVE, ExpectationGapState.NEGATIVE})


class PricedInAssessment(StrEnum):
    """How much of the fundamental change the available expectation evidence suggests is already
    reflected. This is the single strongest inference D4 makes, so the schema requires evidence_ids,
    a confidence and explicit limitations on it (D4 brief §23) - it is not a field that may be
    asserted bare."""

    LIKELY_NOT_PRICED = "LIKELY_NOT_PRICED"
    PARTIALLY_PRICED = "PARTIALLY_PRICED"
    LIKELY_PRICED = "LIKELY_PRICED"
    OVER_PRICED_EXPECTATION = "OVER_PRICED_EXPECTATION"
    """Available evidence suggests expectations have run ahead of the evidenced progress - NOT a
    statement that the stock is overvalued. Valuation is D5's; this is about expectation, and the
    two are separated on purpose because conflating them is exactly how an expectation layer turns
    into an unlicensed valuation layer."""
    UNKNOWN = "UNKNOWN"


class GuidanceState(StrEnum):
    """D4 brief §7. `MIXED` is a first-class outcome, never collapsed into a single positive or
    negative score: revenue guidance raised while margin guidance is cut is two facts, and a layer
    that averages them has destroyed the only information that mattered."""

    RAISED = "RAISED"
    MAINTAINED = "MAINTAINED"
    LOWERED = "LOWERED"
    INITIATED = "INITIATED"
    WITHDRAWN = "WITHDRAWN"
    MIXED = "MIXED"
    NOT_PROVIDED = "NOT_PROVIDED"
    UNKNOWN = "UNKNOWN"


class ResultVsCompanyGuidance(StrEnum):
    """D4 brief §8. Deliberately named after the COMPANY's own prior guidance, never after analyst
    consensus: with no consensus source available (§13), "beat" and "miss" are words D4 has no
    standing to use, and a state named `ABOVE_CONSENSUS` would be a fabrication wearing an enum."""

    ABOVE_COMPANY_GUIDANCE = "ABOVE_COMPANY_GUIDANCE"
    WITHIN_COMPANY_GUIDANCE = "WITHIN_COMPANY_GUIDANCE"
    BELOW_COMPANY_GUIDANCE = "BELOW_COMPANY_GUIDANCE"
    NO_PRIOR_GUIDANCE = "NO_PRIOR_GUIDANCE"
    UNKNOWN = "UNKNOWN"


class EvidenceAvailability(StrEnum):
    """Per-block availability in the expectation evidence bundle. `SOURCE_NOT_AVAILABLE` is a
    positive statement that no source for this block is connected to this repository - it is never
    rewritten to a neutral value or a zero (D4 brief §13)."""

    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    NOT_FOUND_FOR_CANDIDATE = "NOT_FOUND_FOR_CANDIDATE"
    """A source type this repository CAN read, searched for this candidate, and not present."""
    SOURCE_NOT_AVAILABLE = "SOURCE_NOT_AVAILABLE"
    """No provider for this block is connected at all - true for every candidate, not this one."""


class Materiality(StrEnum):
    MATERIAL = "MATERIAL"
    IMMATERIAL = "IMMATERIAL"
    UNKNOWN = "UNKNOWN"


class RealizationStatus(StrEnum):
    """Whether a why-now item has already happened. D4 brief §22's re-rating window only exists for
    something not yet realized, so this is a required field rather than an optional annotation."""

    UNREALIZED = "UNREALIZED"
    PARTIALLY_REALIZED = "PARTIALLY_REALIZED"
    REALIZED = "REALIZED"
    UNKNOWN = "UNKNOWN"


class D6ApprovePrecondition(StrEnum):
    """D0 §L's frozen APPROVE interaction rule, recorded as a PRECONDITION for a stage that has not
    been built.

    `SATISFIED` does not mean approve, recommend, or buy. It means exactly one thing: the D0 §L
    expectation-gap clause would not by itself block APPROVE, *if* D5 valuation and D6 decision
    later run and independently satisfy every other D0 §N1 requirement - of which this is one of
    seven. D4 records it because D0 froze the rule and a later stage must be able to check that D4
    did not quietly ignore it; D4 does not act on it, and no code in this package reads it.
    """

    SATISFIED = "SATISFIED"
    BLOCKED = "BLOCKED"


#: D0 §L's WIDE_POSITIVE criteria include a valuation conjunct D4 structurally cannot evaluate.
#: Recorded verbatim as an unmet-and-deferred conjunct on every WIDE_POSITIVE D4 produces.
WIDE_POSITIVE_VALUATION_CONJUNCT_DEFERRED = (
    "D0 §L WIDE_POSITIVE also requires 'valuation has not yet re-rated to reflect it'. D5 "
    "Valuation has not run, so this conjunct is UNEVALUATED, not met. A D4 WIDE_POSITIVE is "
    "therefore provisional on D5 and must be re-checked by D6, never inherited as satisfied."
)


# ---------------------------------------------------------------------------------------------
# C1-C7: the deterministic rules, frozen before any D4 result
# ---------------------------------------------------------------------------------------------
#
# Each rule states WHICH WAY it errs and why. They are all one-directional: every one of them can
# only lower a state/confidence or force UNKNOWN, never raise one. A contract whose rules could
# promote an output would be a scoring model, and D4 brief §15 forbids turning the gap into a score.


class ContractRule(StrEnum):
    C1_POSITIVE_NEEDS_NON_PRICE_EVIDENCE = "C1_POSITIVE_NEEDS_NON_PRICE_EVIDENCE"
    C2_WIDE_POSITIVE_CONJUNCTION = "C2_WIDE_POSITIVE_CONJUNCTION"
    C3_UNRESOLVED_MATERIAL_CONFLICT_CEILING = "C3_UNRESOLVED_MATERIAL_CONFLICT_CEILING"
    C4_NO_CONSENSUS_CONFIDENCE_CEILING = "C4_NO_CONSENSUS_CONFIDENCE_CEILING"
    C5_UNKNOWN_GAP_FORCES_UNKNOWN_CONFIDENCE = "C5_UNKNOWN_GAP_FORCES_UNKNOWN_CONFIDENCE"
    C6_NO_EXPECTATION_EVIDENCE_FORCES_UNKNOWN = "C6_NO_EXPECTATION_EVIDENCE_FORCES_UNKNOWN"
    C7_PRICED_IN_NEEDS_EVIDENCE = "C7_PRICED_IN_NEEDS_EVIDENCE"


#: C1. An affirmative POSITIVE needs at least one expectation-evidence item that is NOT price
#: history: guidance, a result-vs-prior-guidance comparison, or a sourced management expectation
#: signal.
#:
#: The asymmetry is deliberate and is the single most important rule in this contract. Price
#: history can show that the market HAS MOVED; it can never show that the market is BEHIND. A stock
#: that has not re-rated is exactly as consistent with "the market has not noticed yet" as with
#: "the market has noticed and disagrees for a reason not in our evidence pool" - and D4 has no
#: evidence that separates those two, because separating them is what a consensus feed is for. So
#: price context alone may support NEGATIVE (a large completed re-rating IS direct evidence that
#: expectations moved) but never POSITIVE. This is the mechanical form of brief §19: a great
#: company is not a positive gap.
C1_NON_PRICE_EVIDENCE_REQUIRED_FOR_POSITIVE = True

#: C2. WIDE_POSITIVE's structural preconditions, checked against the immutable D3 input rather than
#: taken on the model's word. Every one must hold; D4 cannot check D0's fifth (valuation) conjunct
#: at all - see `WIDE_POSITIVE_VALUATION_CONJUNCT_DEFERRED`.
C2_WIDE_POSITIVE_REQUIRES = (
    "material evidenced fundamental/business improvement in the D3 input",
    "at least one future-business item staged above STORY",
    "at least one catalyst candidate whose why-now realization status is not REALIZED",
    "at least one non-price expectation-evidence item that lags the evidenced improvement",
    "an event price reaction that does not fully incorporate the change, or no such reaction yet",
)

#: C3. A material unresolved conflict caps confidence at MEDIUM. Brief §26 asked whether such a
#: ceiling should exist; it should, and here is the reason rather than the assertion: D3's own
#: conflict structure exists because two official sources disagreeing is a finding, not a defect.
#: If that finding is material and nothing in the record reconciles it, then by construction part
#: of the reality side of the comparison is unsettled - and HIGH confidence in a comparison with an
#: unsettled input is not a confidence level, it is an oversight.
C3_UNRESOLVED_MATERIAL_CONFLICT_CEILING = Confidence.MEDIUM

#: C4. With no consensus AND no estimate-revision source, confidence is capped at MEDIUM.
#:
#: Measured, not assumed: across all 2,010 D2.1 candidate packages, `earnings.status` is `UNKNOWN`
#: in 2,010 of 2,010 (100%) - there is no PIT consensus anywhere in this repository, for any
#: candidate. So this ceiling binds on every candidate D4 will ever see under the current data
#: layer, and the honest reading is that no H-V2 candidate can reach HIGH expectation-gap
#: confidence until a consensus provider is connected. That does not stall the pipeline: D0 §L's
#: APPROVE rule accepts MEDIUM. It does mean D4 must never report HIGH, and this constant is why.
C4_NO_CONSENSUS_CONFIDENCE_CEILING = Confidence.MEDIUM

#: C7. `priced_in_assessment` may only be a value other than UNKNOWN when it carries at least this
#: many evidence ids, a confidence, and at least one stated limitation.
C7_PRICED_IN_MIN_EVIDENCE_IDS = 1


_CONFIDENCE_ORDER = (Confidence.UNKNOWN, Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH)


def _rank(confidence: Confidence) -> int:
    return _CONFIDENCE_ORDER.index(confidence)


def apply_confidence_ceiling(confidence: Confidence, ceiling: Confidence) -> Confidence:
    """Lower `confidence` to `ceiling` if it exceeds it; never raises it. `UNKNOWN` is the floor,
    not a wildcard - an UNKNOWN confidence is left UNKNOWN by every ceiling."""
    if confidence == Confidence.UNKNOWN:
        return Confidence.UNKNOWN
    return ceiling if _rank(confidence) > _rank(ceiling) else confidence


def confidence_ceiling(
    *, consensus_available: bool, estimate_revisions_available: bool,
    has_unresolved_material_conflict: bool,
) -> tuple[Confidence, tuple[ContractRule, ...]]:
    """The binding ceiling and which rules produced it. Both C3 and C4 can fire at once; the
    tighter one wins and both are reported, so a reader is never left guessing which rule bound."""
    ceiling = Confidence.HIGH
    fired: list[ContractRule] = []
    if not consensus_available and not estimate_revisions_available:
        ceiling = C4_NO_CONSENSUS_CONFIDENCE_CEILING
        fired.append(ContractRule.C4_NO_CONSENSUS_CONFIDENCE_CEILING)
    if has_unresolved_material_conflict:
        if _rank(C3_UNRESOLVED_MATERIAL_CONFLICT_CEILING) < _rank(ceiling):
            ceiling = C3_UNRESOLVED_MATERIAL_CONFLICT_CEILING
        fired.append(ContractRule.C3_UNRESOLVED_MATERIAL_CONFLICT_CEILING)
    return ceiling, tuple(fired)


def d6_approve_precondition(
    state: ExpectationGapState, confidence: Confidence,
) -> D6ApprovePrecondition:
    """D0 §L, mechanically: state in {POSITIVE, WIDE_POSITIVE} AND confidence in {HIGH, MEDIUM}.

    Read `D6ApprovePrecondition`'s docstring before using this for anything. It is a record that D4
    checked a frozen D0 rule, not an approval and not an input to one - D5 has not run.
    """
    if state in POSITIVE_STATES and confidence in (Confidence.HIGH, Confidence.MEDIUM):
        return D6ApprovePrecondition.SATISFIED
    return D6ApprovePrecondition.BLOCKED
