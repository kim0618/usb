"""H-V2-D6: the integrated decision engine.

D3 says what the company is, D4 says what the market expects of it, D5 says what the price implies.
This module is the first place in Strategy H where all three meet, and the only thing it produces is
one of `APPROVE`, `WATCH`, `REJECT` per issuer with the clause-by-clause reason it landed there.

**It owns no facts and no numbers.** Every input is a projection of an output another step already
published and checksummed: `D3Evidence` of a stored `h_research_interpretation_v2`, `D4Evidence` of a
stored `h_expectation_gap_analysis_v2`, `D5Evidence` of a D5-D2 valuation row. Nothing here recomputes
a multiple, re-reads a filing, re-grades an expectation gap or adjusts a target price - `D5Evidence`
carries TP1 and TP2 as the floats D5 published and `D6_OWNS_NO_NUMBERS` is the test that it does.

**The decision is a conjunction, never a sum.** D0 §N1 lists the minimum evidence APPROVE requires
and every item must hold; D0 §N3 lists triggers any one of which is sufficient for REJECT. Both lists
are quoted from D0 in `APPROVE_CLAUSES` and `REJECT_TRIGGERS` rather than restated, because a decision
contract that gets paraphrased per step is a decision contract that drifts. There is no score in this
module and `test_no_score_field_exists_anywhere` asserts there is no field one could put one in.

**WATCH is the residual, and that is D0's design rather than this step's convenience.** D0 §N2's
triggers are all forms of "the thesis is not complete yet", so an issuer that neither earns APPROVE nor
fires a REJECT trigger is a WATCH. D6 therefore needs no fourth state and does not invent one: see
`WATCH_IS_THE_ABSTENTION`. What D6 *does* need is the distinction between an issuer the engine judged
and an issuer the engine could not reach, and that is `Eligibility` - a statement about the sample, not
a fourth decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping, Sequence

D6_CONTRACT_VERSION = "h_v2_d6_integrated_decision_v1"

#: The decision contract this engine implements, unmodified. D0 §N is the authority for the three
#: states, for APPROVE's conjunction and for the REJECT triggers; D0 §L is the authority for the
#: expectation-gap precondition; D5-D0 §M is the authority for how a valuation may and may not move a
#: gap. This step adds no criterion to any of them.
UPSTREAM_CONTRACTS: tuple[str, ...] = (
    "h_v2_d0_v1",                       # D0 §N decision contract, §L expectation gap
    "h_v2_d4_gap_contract_v1",          # D4's frozen gap states and APPROVE precondition
    "h_v2_d5_d0_valuation_v1",          # D5-D0 §M D4 integration, §N not-ready semantics
    "h_v2_d5_d2_fair_value_v1",         # D5-D2's TP1/TP2, upsides and confidence
)

D6_OWNS_NO_NUMBERS = (
    "D6 performs no arithmetic on a price, a multiple, a fundamental or a target. Every number it "
    "reports is carried through from the D5 row it was given, and the only numeric operation in this "
    "module is a comparison against zero to ask whether an upside D5 already computed is positive. "
    "A D6 that recomputed an upside would be a second valuation engine wearing a decision engine's "
    "name, and the first time the two disagreed there would be no way to tell which one was wrong."
)

WATCH_IS_THE_ABSTENTION = (
    "D0 §N freezes three outcomes and D6 does not add a fourth. The audit §18 asked for was run and "
    "it comes out in D0's favour: every §N2 WATCH trigger is a form of 'this thesis is not complete "
    "yet' - insufficient margin of safety, an undated catalyst, a positive gap at LOW confidence, a "
    "thesis awaiting the next print - so WATCH already IS the abstention state, and a "
    "DECISION_NOT_READY would duplicate it while implying the engine failed rather than that the "
    "evidence is incomplete. What WATCH must never be is a stand-in for evidence that does not "
    "exist: an issuer whose D4 never produced an output is not a WATCH, it is outside the sample, "
    "and that is `Eligibility` below."
)

D4_ABSENCE_IS_NOT_A_READING = (
    "`D4_NOT_EVALUATED` and `D4_REFUSED_NO_FINAL_OUTPUT` are absences of evidence. `UNKNOWN` is a "
    "measured finding: D4 examined the issuer and found the expectation evidence absent or "
    "contradictory, which is a judgement the contract recorded. Mapping either absence onto UNKNOWN "
    "or onto NEUTRAL would let a decision inherit a confidence nothing supports - NEUTRAL in "
    "particular asserts that the market's pricing is a reasonable reflection of the trajectory, "
    "which is a claim, not a blank. So the two absences make an issuer ineligible and the engine "
    "declines to decide, while UNKNOWN is decided on: it blocks APPROVE under D0 §L and routes to "
    "WATCH."
)

#: What D6 may not produce, carried forward from D5-D2 §Q unchanged. D5's `NEVER_PRODUCED` forbade an
#: APPROVE / WATCH / REJECT because producing one was D6's job and not D5's; everything else on that
#: list is still forbidden here, and `test_no_execution_field_exists_anywhere` asserts the absence by
#: name rather than promising it. A decision is a judgement about a thesis. An entry level, a stop or
#: a size is a judgement about a position, and nothing in D1 through D6 has looked at a portfolio.
D6_NEVER_PRODUCED: tuple[str, ...] = (
    "an entry level, an entry zone or an entry price",
    "an exit level, a stop, a trailing stop or a take-profit level",
    "a position size, a share count, a notional or a portfolio weight",
    "an order of any kind, paper or live",
    "a ranking of the eligible issuers against each other",
    "a numeric composite that determines APPROVE, WATCH or REJECT",
    "a target price, a fair value or a multiple of its own - every number comes from D5",
    "a forward return, a realized return or any outcome label",
)

#: Field-name tokens that may not appear on any dataclass in this module. `score` and its relatives
#: enforce §19; the execution tokens enforce `D6_NEVER_PRODUCED`.
BANNED_D6_FIELD_NAME_TOKENS: tuple[str, ...] = (
    "score", "points", "rating", "rank", "weight", "composite",
    "entry", "exit", "stop", "size", "shares", "quantity", "notional", "order",
    "return", "pnl", "performance",
)

VALUATION_DOES_NOT_OVERRIDE = (
    "D5-D0 §M, carried unchanged: a cheap valuation does not promote a NEGATIVE expectation gap and "
    "an expensive one does not demote a POSITIVE gap. In this engine that is structural rather than "
    "promised - the expectation clause and the valuation clauses are separate conjuncts of one AND, "
    "so neither can compensate for the other, and a large TP1 upside cannot carry an issuer past a "
    "gap that fails D0 §L. The same AND is why quality cannot carry a negative upside either."
)


# -------------------------------------------------------------------------------------------------
# States
# -------------------------------------------------------------------------------------------------

class Decision(StrEnum):
    """D0 §N's three-way outcome. There is no fourth member and no numeric composite."""

    APPROVE = "APPROVE"
    WATCH = "WATCH"
    REJECT = "REJECT"


class Eligibility(StrEnum):
    """Whether all three layers exist for an issuer at all - a statement about the SAMPLE.

    `NOT_DECISION_ELIGIBLE` is not a decision and is never reported in the APPROVE / WATCH / REJECT
    counts. An issuer lands here when a layer produced no output, and the honest consequence is that
    the engine has nothing to integrate rather than that the issuer is unattractive.
    """

    DECISION_ELIGIBLE = "DECISION_ELIGIBLE"
    NOT_DECISION_ELIGIBLE = "NOT_DECISION_ELIGIBLE"


class D3Provenance(StrEnum):
    VALID = "D3_VALID"
    NOT_EVALUATED = "D3_NOT_EVALUATED"
    REFUSED_NO_FINAL_OUTPUT = "D3_REFUSED_NO_FINAL_OUTPUT"


class D4Provenance(StrEnum):
    """Three distinct states, and `D4_ABSENCE_IS_NOT_A_READING` is why they are not two."""

    EVALUATED = "D4_EVALUATED"
    REFUSED_NO_FINAL_OUTPUT = "D4_REFUSED_NO_FINAL_OUTPUT"
    NOT_EVALUATED = "D4_NOT_EVALUATED"


class ExpectationGap(StrEnum):
    """D0 §L's frozen categorical states, read from D4 and never recomputed."""

    WIDE_POSITIVE = "WIDE_POSITIVE"
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"
    WIDE_NEGATIVE = "WIDE_NEGATIVE"
    UNKNOWN = "UNKNOWN"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


#: D0 §L, verbatim: "expectation_gap.state must be POSITIVE or WIDE_POSITIVE, and
#: expectation_gap.confidence must be HIGH or MEDIUM, for APPROVE to be reachable."
GAP_STATES_PERMITTING_APPROVE: frozenset[ExpectationGap] = frozenset(
    {ExpectationGap.POSITIVE, ExpectationGap.WIDE_POSITIVE})
GAP_CONFIDENCE_PERMITTING_APPROVE: frozenset[Confidence] = frozenset(
    {Confidence.HIGH, Confidence.MEDIUM})

#: D0 §N3's last trigger: a gap in either adverse state is sufficient for REJECT on its own.
ADVERSE_GAP_STATES: frozenset[ExpectationGap] = frozenset(
    {ExpectationGap.NEGATIVE, ExpectationGap.WIDE_NEGATIVE})

#: D0 §N1's final clause names three fields that may not be unknown. The tokens are matched against
#: D3's free-text `unknown_fields` entries, and a match is reported with the entry that produced it
#: so a false positive is visible rather than silent.
APPROVE_BLOCKING_UNKNOWN_FIELDS: tuple[str, ...] = (
    "business_model", "fundamental_change", "expectation_gap")

#: The valuation confidences D6 will let carry an APPROVE. This is the one clause D0 did not freeze,
#: because D0 deferred valuation arithmetic to D5 entirely, so it is derived here from D5-D0 §N's own
#: sentence - "one method is a number and two agreeing or disagreeing methods are a valuation" - which
#: is exactly what `valuation_confidence` demotes to LOW for. It is declared before any result is seen
#: and it is reported as D6's clause rather than as D0's.
VALUATION_CONFIDENCE_PERMITTING_APPROVE: frozenset[str] = frozenset({"HIGH", "MEDIUM"})


# -------------------------------------------------------------------------------------------------
# Inputs - projections of stored outputs, never sources of fact
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class D3Evidence:
    """What D0 §N1 needs from a stored `h_research_interpretation_v2`, and nothing else.

    `future_business_evidence_present` is the one field that required reading D0 §J rather than a
    label: §J lists six kinds of evidence that make a future-business claim REAL BUSINESS, and D3's
    v2 schema carries exactly those as per-entry booleans. So the flag is "at least one entry carries
    at least one of §J's evidence kinds", which is §J's test and not a re-reading of the filings.
    """

    ticker: str
    provenance: D3Provenance
    research_id: str | None
    research_output_checksum: str | None
    research_completeness: str | None
    business_model_populated: bool
    fundamental_change_count: int
    fundamental_change_anchored: bool
    """Every fundamental-change entry carries a source id. D0 §I: 'margin improved' without a named
    driver and a citation is an incomplete answer and must be flagged, not accepted."""
    growth_durability_state: str | None
    future_business_stages: tuple[str, ...]
    future_business_evidence_present: bool
    catalyst_count: int
    catalyst_material_and_timed: bool
    """At least one catalyst with materiality HIGH/MEDIUM and timing confidence HIGH/MEDIUM. D0 §N2
    makes an undated catalyst a WATCH trigger rather than a REJECT one, so this gates APPROVE only."""
    competitive_position_count: int
    risk_count: int
    invalidation_count: int
    unknown_fields: tuple[str, ...]
    evidence_conflict_count: int

    @property
    def blocking_unknown_fields(self) -> tuple[str, ...]:
        hits: list[str] = []
        for entry in self.unknown_fields:
            lowered = entry.lower()
            for token in APPROVE_BLOCKING_UNKNOWN_FIELDS:
                if token in lowered or token.replace("_", " ") in lowered:
                    hits.append(entry)
                    break
        return tuple(hits)


@dataclass(frozen=True)
class D4Evidence:
    """What D0 §L and §N need from a stored `h_expectation_gap_analysis_v2`.

    `d6_approve_precondition` is D4's own code-owned verdict on whether §L permits APPROVE. D6 reads
    it AND recomputes §L's rule from the state and the confidence, then reports any disagreement as a
    defect instead of choosing a winner: two implementations of one frozen rule that disagree is a
    finding, and silently preferring either would hide it.
    """

    ticker: str
    provenance: D4Provenance
    analysis_id: str | None
    final_output_checksum: str | None
    research_input_id: str | None
    """The D3 output this D4 analysis consumed, as D4 recorded it. It exists so the chain can be
    checked end to end rather than assumed: the D3 record D6 reads must be the one D4 read."""
    research_input_checksum: str | None
    gap: ExpectationGap | None
    gap_confidence: Confidence | None
    confidence_ceiling: str | None
    d6_approve_precondition: str | None
    conflict_count: int
    unknown_field_count: int
    terminal_failure_codes: tuple[str, ...]

    @property
    def gap_permits_approve(self) -> bool:
        """D0 §L's frozen rule, evaluated here from the state and the confidence."""
        return (self.provenance is D4Provenance.EVALUATED
                and self.gap in GAP_STATES_PERMITTING_APPROVE
                and self.gap_confidence in GAP_CONFIDENCE_PERMITTING_APPROVE)

    def chain_break(self, d3: "D3Evidence") -> str | None:
        """Whether the D3 record D6 read is the D3 record this D4 analysis was computed from.

        D4 stored both the research id and the checksum of the research output it consumed, so this is
        a real check rather than a naming convention. A break here means D6 is integrating a company
        thesis with an expectation gap derived from a different one, which would be invisible in every
        other output: both legs would look internally consistent.
        """
        if self.provenance is not D4Provenance.EVALUATED:
            return None
        if d3.provenance is not D3Provenance.VALID:
            return f"D4 consumed {self.research_input_id} but D3 projects as {d3.provenance.value}"
        if self.research_input_id is not None and d3.research_id != self.research_input_id:
            return (f"D4 consumed research_input_id={self.research_input_id} while D6 read "
                    f"research_id={d3.research_id}")
        if (self.research_input_checksum is not None
                and d3.research_output_checksum is not None
                and self.research_input_checksum != d3.research_output_checksum):
            return (f"D4 consumed research output checksum {self.research_input_checksum} while D6 "
                    f"read {d3.research_output_checksum}")
        return None

    @property
    def precondition_disagreement(self) -> str | None:
        if self.provenance is not D4Provenance.EVALUATED or self.d6_approve_precondition is None:
            return None
        d4_says = self.d6_approve_precondition == "PERMITTED"
        if d4_says is self.gap_permits_approve:
            return None
        return (f"D4 published d6_approve_precondition={self.d6_approve_precondition} while D0 §L "
                f"evaluated on gap={self.gap} confidence={self.gap_confidence} gives "
                f"{'PERMITTED' if self.gap_permits_approve else 'BLOCKED'}")


@dataclass(frozen=True)
class D5Evidence:
    """A D5-D2 valuation row, carried through. Every float here was published by D5-D2.

    There is no field for a target price D6 computed, because D6 computes none. `upside_to_tp1` and
    `upside_to_tp2` are D5's own `upside_to_TP1` / `upside_to_TP2`, and the only thing D6 does with
    them is ask whether they are greater than zero.
    """

    ticker: str
    status: str
    """`VALUED` or `VALUATION_NOT_READY`, as D5-D2's `ValuationStatus` wrote it."""
    confidence: str
    """`HIGH` / `MEDIUM` / `LOW` / `NOT_READY`, as D5-D2's `ValuationConfidence` wrote it."""
    primary_method: str | None
    secondary_method: str | None
    contract_window: str | None
    current_price: float | None
    tp1: float | None
    tp2: float | None
    bear_anchor: float | None
    upside_to_tp1: float | None
    upside_to_tp2: float | None
    downside_to_bear: float | None
    reconciliation: str | None
    confidence_drivers: tuple[str, ...] = ()
    valuation_risk: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status == "VALUED" and self.tp1 is not None

    @property
    def base_case_supports_upside(self) -> bool:
        """D0 §N1: 'valuation view supports upside under at least the Base case'. TP1 IS the Base
        case fair value per D5-D0 §L, so the clause is `upside_to_TP1 > 0` and nothing more."""
        return self.upside_to_tp1 is not None and self.upside_to_tp1 > 0.0

    @property
    def fully_priced(self) -> bool:
        """D0 §N3: 'valuation already reflects the full positive case (fully priced)'. The full
        positive case is the Bull leg, so this is TP2 at or below the price - not TP1."""
        return self.upside_to_tp2 is not None and self.upside_to_tp2 <= 0.0


# -------------------------------------------------------------------------------------------------
# Clauses
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Clause:
    """One APPROVE conjunct or one REJECT trigger, with the evidence that decided it.

    `basis` must name the stored field that settled the clause. A clause whose basis reads like a
    judgement is a judgement, and the point of writing them down is that a later reader can check
    each one against the artifact without re-deriving the decision.
    """

    name: str
    contract_reference: str
    holds: bool
    basis: str

    def to_dict(self) -> dict:
        return {"name": self.name, "contract_reference": self.contract_reference,
                "holds": self.holds, "basis": self.basis}


#: D0 §N1, quoted. Each entry is (clause name, the contract line it implements).
APPROVE_CLAUSES: tuple[tuple[str, str], ...] = (
    ("business_understood", "D0 §N1: business understood (§F/§I answered, not UNKNOWN)"),
    ("fundamental_change_anchored",
     "D0 §N1: fundamental change evidence exists and is anchored to the bundle (§I)"),
    ("future_business_or_catalyst_meaningful",
     "D0 §N1: future-business or catalyst evidence is meaningful, i.e. not STORY_UNVERIFIED alone"),
    ("expectation_gap_permits_approve",
     "D0 §N1 / §L: expectation_gap.state in {POSITIVE, WIDE_POSITIVE} AND confidence in "
     "{HIGH, MEDIUM}"),
    ("valuation_supports_base_case_upside",
     "D0 §N1: valuation view supports upside under at least the Base case"),
    ("valuation_confidence_sufficient",
     "D6 clause derived from D5-D0 §N: a single uncross-checked method is a number rather than a "
     "valuation, so a LOW or NOT_READY valuation confidence cannot carry an APPROVE"),
    ("risks_with_an_invalidation_condition",
     "D0 §N1: risks identified with at least one explicit invalidation condition"),
    ("no_approve_blocking_unknown_field",
     "D0 §N1: unknown_fields does not include business model, fundamental_change or expectation_gap"),
)

#: D0 §N3, quoted. `MECHANICALLY_EVALUABLE` records which of the seven D6 can decide from stored
#: evidence and which it cannot, because a trigger that is silently never evaluated reads in a report
#: exactly like a trigger that never fired.
REJECT_TRIGGERS: tuple[tuple[str, str, bool], ...] = (
    ("thesis_unsupported_by_the_evidence_bundle",
     "D0 §N3: thesis unsupported by the evidence bundle", True),
    ("future_business_story_only_without_corroborating_catalyst",
     "D0 §N3: future business is STORY_UNVERIFIED with no corroborating catalyst", True),
    ("valuation_fully_prices_the_positive_case",
     "D0 §N3: valuation already reflects the full positive case (fully priced)", True),
    ("structural_deterioration_without_offsetting_catalyst",
     "D0 §N3: structural deterioration evident with no offsetting catalyst", False),
    ("risk_unacceptable_against_any_plausible_upside",
     "D0 §N3: risk level assessed as unacceptable relative to any plausible upside", False),
    ("no_catalyst_identifiable_in_the_thesis_horizon",
     "D0 §N3: no catalyst identifiable within the thesis horizon", True),
    ("adverse_expectation_gap",
     "D0 §N3: expectation_gap.state in {NEGATIVE, WIDE_NEGATIVE}", True),
)

NOT_MECHANICALLY_EVALUABLE = (
    "Two of D0 §N3's seven triggers are judgements this engine cannot make from stored evidence. "
    "'Structural deterioration with no offsetting catalyst' and 'risk unacceptable relative to any "
    "plausible upside' both require weighing evidence rather than reading a field, and the stored D3 "
    "output carries no field that settles either: `growth_durability` has a MIXED state that is not "
    "deterioration, and `risks` is a list whose length says nothing about severity. Implementing "
    "them from a proxy - MIXED as deterioration, or a risk count as severity - would manufacture "
    "REJECTs out of a threshold nobody set. So they are declared NOT_EVALUATED per issuer and "
    "reported that way, which makes the gap in D6's coverage visible instead of making D6 look "
    "complete."
)


def approve_clauses(d3: D3Evidence, d4: D4Evidence, d5: D5Evidence) -> tuple[Clause, ...]:
    """D0 §N1 evaluated clause by clause. Every conjunct must hold; none of them is weighted."""
    reference = dict(APPROVE_CLAUSES)
    out: list[Clause] = []

    def add(name: str, holds: bool, basis: str) -> None:
        out.append(Clause(name=name, contract_reference=reference[name], holds=holds, basis=basis))

    add("business_understood",
        d3.provenance is D3Provenance.VALID and d3.business_model_populated,
        f"D3 {d3.provenance.value}, business_model populated={d3.business_model_populated}, "
        f"research_completeness={d3.research_completeness}")

    add("fundamental_change_anchored",
        d3.fundamental_change_count > 0 and d3.fundamental_change_anchored,
        f"D3 fundamental_change entries={d3.fundamental_change_count}, every entry carries a "
        f"source id={d3.fundamental_change_anchored}")

    add("future_business_or_catalyst_meaningful",
        d3.future_business_evidence_present or d3.catalyst_material_and_timed,
        f"D3 future_business stages={list(d3.future_business_stages)} with at least one §J evidence "
        f"kind={d3.future_business_evidence_present}; catalysts={d3.catalyst_count} with at least "
        f"one HIGH/MEDIUM materiality AND HIGH/MEDIUM timing={d3.catalyst_material_and_timed}")

    add("expectation_gap_permits_approve",
        d4.gap_permits_approve,
        f"D4 {d4.provenance.value}"
        + ("" if d4.gap is None else
           f", gap={d4.gap.value}, confidence="
           f"{'None' if d4.gap_confidence is None else d4.gap_confidence.value}, D4's own "
           f"d6_approve_precondition={d4.d6_approve_precondition}"))

    add("valuation_supports_base_case_upside",
        d5.base_case_supports_upside,
        f"D5 status={d5.status}, TP1={d5.tp1}, upside_to_TP1={d5.upside_to_tp1}")

    add("valuation_confidence_sufficient",
        d5.confidence in VALUATION_CONFIDENCE_PERMITTING_APPROVE,
        f"D5 valuation confidence={d5.confidence} "
        f"(permitting: {sorted(VALUATION_CONFIDENCE_PERMITTING_APPROVE)})")

    add("risks_with_an_invalidation_condition",
        d3.risk_count > 0 and d3.invalidation_count > 0,
        f"D3 risks={d3.risk_count}, invalidation_candidates={d3.invalidation_count}")

    blocking = d3.blocking_unknown_fields
    add("no_approve_blocking_unknown_field", not blocking,
        f"D3 unknown_fields matching {list(APPROVE_BLOCKING_UNKNOWN_FIELDS)}: {list(blocking)}")

    return tuple(out)


def reject_triggers(d3: D3Evidence, d4: D4Evidence, d5: D5Evidence) -> tuple[Clause, ...]:
    """D0 §N3's triggers. `holds=True` means the trigger FIRED, and any one is sufficient."""
    reference = {name: (ref, mech) for name, ref, mech in REJECT_TRIGGERS}
    out: list[Clause] = []

    def add(name: str, fired: bool, basis: str) -> None:
        ref, mechanical = reference[name]
        out.append(Clause(name=name, contract_reference=ref, holds=fired,
                          basis=basis if mechanical else "NOT_EVALUATED: " + basis))

    add("thesis_unsupported_by_the_evidence_bundle",
        not d3.business_model_populated or d3.fundamental_change_count == 0,
        f"D3 business_model populated={d3.business_model_populated}, fundamental_change "
        f"entries={d3.fundamental_change_count}")

    story_only = (bool(d3.future_business_stages)
                  and not d3.future_business_evidence_present
                  and not d3.catalyst_material_and_timed)
    add("future_business_story_only_without_corroborating_catalyst", story_only,
        f"D3 future_business stages={list(d3.future_business_stages)}, any §J evidence "
        f"kind={d3.future_business_evidence_present}, corroborating material+timed "
        f"catalyst={d3.catalyst_material_and_timed}")

    add("valuation_fully_prices_the_positive_case", d5.fully_priced,
        f"D5 TP2={d5.tp2}, upside_to_TP2={d5.upside_to_tp2} - the Bull leg is the full positive case")

    add("structural_deterioration_without_offsetting_catalyst", False,
        f"no stored field settles deterioration; D3 growth_durability="
        f"{d3.growth_durability_state} is a state, not a severity")

    add("risk_unacceptable_against_any_plausible_upside", False,
        f"no stored field settles severity; D3 carries {d3.risk_count} risks and a count is not a "
        f"risk level")

    add("no_catalyst_identifiable_in_the_thesis_horizon", d3.catalyst_count == 0,
        f"D3 catalyst_candidates={d3.catalyst_count}; D0 §N2 makes an UNDATED catalyst a WATCH "
        f"trigger, so only the absence of any candidate fires this")

    add("adverse_expectation_gap",
        d4.provenance is D4Provenance.EVALUATED and d4.gap in ADVERSE_GAP_STATES,
        f"D4 gap={None if d4.gap is None else d4.gap.value}")

    return tuple(out)


#: D0 §N2's triggers, matched for the rationale only. They never change the outcome: WATCH is where
#: an issuer lands by being neither APPROVE nor REJECT, so a §N2 match explains a WATCH rather than
#: causing one, and an issuer can be a WATCH with no §N2 trigger matched at all.
def watch_triggers(d3: D3Evidence, d4: D4Evidence, d5: D5Evidence) -> tuple[Clause, ...]:
    out: list[Clause] = []

    def add(name: str, matched: bool, basis: str) -> None:
        out.append(Clause(name=name, contract_reference="D0 §N2", holds=matched, basis=basis))

    add("insufficient_margin_of_safety",
        d5.ready and not d5.base_case_supports_upside,
        f"D5 upside_to_TP1={d5.upside_to_tp1} on a VALUED issuer")
    add("catalyst_plausible_but_not_dated_or_confirmed",
        d3.catalyst_count > 0 and not d3.catalyst_material_and_timed,
        f"D3 catalysts={d3.catalyst_count}, none with both HIGH/MEDIUM materiality and HIGH/MEDIUM "
        f"timing confidence")
    add("positive_gap_at_low_or_unknown_confidence",
        d4.gap in GAP_STATES_PERMITTING_APPROVE
        and d4.gap_confidence not in GAP_CONFIDENCE_PERMITTING_APPROVE,
        f"D4 gap={None if d4.gap is None else d4.gap.value}, confidence="
        f"{None if d4.gap_confidence is None else d4.gap_confidence.value}")
    add("thesis_needs_confirmation_from_the_next_print",
        d4.gap is ExpectationGap.UNKNOWN or d4.gap is ExpectationGap.NEUTRAL,
        f"D4 gap={None if d4.gap is None else d4.gap.value}: no gap in either direction is "
        f"established, so the thesis awaits evidence rather than failing")
    add("future_business_presently_story_only",
        bool(d3.future_business_stages) and not d3.future_business_evidence_present,
        f"D3 future_business stages={list(d3.future_business_stages)} with no §J evidence kind")
    add("valuation_confidence_low",
        d5.confidence not in VALUATION_CONFIDENCE_PERMITTING_APPROVE and d5.ready,
        f"D5 valuation confidence={d5.confidence} on a VALUED issuer")

    return tuple(out)


# -------------------------------------------------------------------------------------------------
# Eligibility, then the decision
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class EligibilityAssessment:
    eligibility: Eligibility
    blockers: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"eligibility": self.eligibility.value, "blockers": list(self.blockers)}


def assess_eligibility(d3: D3Evidence, d4: D4Evidence, d5: D5Evidence) -> EligibilityAssessment:
    """All three layers must have produced an output before the engine will decide anything.

    This is the §7 join, and the three requirements are deliberately about EXISTENCE rather than
    about content: a NEUTRAL gap is an output and makes the issuer eligible, a refused D4 is not an
    output and does not. The one that needs stating is D5: `VALUATION_NOT_READY` is a complete and
    correct D5 output (D5-D0 §N), but it is an abstention from valuing, so the issuer has no third
    leg and D6 cannot integrate three layers out of two.
    """
    blockers: list[str] = []
    if d3.provenance is not D3Provenance.VALID:
        blockers.append(f"D3 {d3.provenance.value}: no valid company research output")
    if d4.provenance is not D4Provenance.EVALUATED:
        blockers.append(f"D4 {d4.provenance.value}: no expectation gap was produced, and this is an "
                        f"absence of evidence rather than a neutral or unknown reading")
    if not d5.ready:
        blockers.append(f"D5 {d5.status} (confidence {d5.confidence}): the valuation abstained, so "
                        f"there is no price leg to integrate")
    return EligibilityAssessment(
        Eligibility.DECISION_ELIGIBLE if not blockers else Eligibility.NOT_DECISION_ELIGIBLE,
        tuple(blockers))


@dataclass(frozen=True)
class DecisionRecord:
    """One issuer's integrated judgement, with the whole derivation attached.

    Reproducibility is the point of the shape: `decision` is a pure function of `reject_fired` and
    `approve_blockers`, both of which are listed, and every clause carries the stored field that
    settled it. Given the three input artifacts a reader can re-derive the label without re-reading a
    filing and without trusting a narrative.
    """

    ticker: str
    contract_version: str
    eligibility: EligibilityAssessment
    decision: Decision | None
    approve: tuple[Clause, ...]
    reject: tuple[Clause, ...]
    watch: tuple[Clause, ...]
    provenance: Mapping[str, str | None] = field(default_factory=dict)

    @property
    def reject_fired(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.reject if c.holds)

    @property
    def approve_blockers(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.approve if not c.holds)

    @property
    def watch_matched(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.watch if c.holds)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "contract_version": self.contract_version,
            "eligibility": self.eligibility.to_dict(),
            "decision": None if self.decision is None else self.decision.value,
            "reject_fired": list(self.reject_fired),
            "approve_blockers": list(self.approve_blockers),
            "watch_matched": list(self.watch_matched),
            "approve_clauses": [c.to_dict() for c in self.approve],
            "reject_triggers": [c.to_dict() for c in self.reject],
            "watch_triggers": [c.to_dict() for c in self.watch],
            "provenance": dict(self.provenance),
        }


def decide(d3: D3Evidence, d4: D4Evidence, d5: D5Evidence) -> DecisionRecord:
    """The whole engine. Pure, total, and free of every number but a sign test.

    Order of resolution, and it is a reading of D0 rather than a preference: REJECT triggers are
    checked first because D0 §N3 makes any one of them sufficient, while §N1 makes APPROVE a
    conjunction that a fired trigger would contradict anyway - 'fully priced' is the negation of
    'supports upside under at least the Base case', and an adverse gap is the negation of §L's
    precondition. An issuer that is neither is a WATCH, per `WATCH_IS_THE_ABSTENTION`.
    """
    eligibility = assess_eligibility(d3, d4, d5)
    approve = approve_clauses(d3, d4, d5)
    reject = reject_triggers(d3, d4, d5)
    watch = watch_triggers(d3, d4, d5)
    provenance = {
        "d3_research_id": d3.research_id,
        "d3_output_checksum": d3.research_output_checksum,
        "d4_analysis_id": d4.analysis_id,
        "d4_output_checksum": d4.final_output_checksum,
        "d5_primary_method": d5.primary_method,
        "d5_contract_window": d5.contract_window,
    }

    if eligibility.eligibility is Eligibility.NOT_DECISION_ELIGIBLE:
        return DecisionRecord(ticker=d3.ticker, contract_version=D6_CONTRACT_VERSION,
                              eligibility=eligibility, decision=None, approve=approve,
                              reject=reject, watch=watch, provenance=provenance)

    if any(c.holds for c in reject):
        decision = Decision.REJECT
    elif all(c.holds for c in approve):
        decision = Decision.APPROVE
    else:
        decision = Decision.WATCH

    return DecisionRecord(ticker=d3.ticker, contract_version=D6_CONTRACT_VERSION,
                          eligibility=eligibility, decision=decision, approve=approve,
                          reject=reject, watch=watch, provenance=provenance)


# -------------------------------------------------------------------------------------------------
# Audit
# -------------------------------------------------------------------------------------------------

def audit_decisions(records: Sequence[DecisionRecord],
                    d5_rows: Mapping[str, D5Evidence],
                    d5_published: Mapping[str, Mapping[str, float | None]],
                    chain_breaks: Mapping[str, str] | None = None) -> dict:
    """Every D6 defect class as a list of named offenders, never a count.

    `d5_published` is the D5-D2 row as it was published, keyed by ticker, and the check is that the
    numbers D6 reports are the same objects by value. It is the one audit that cannot be satisfied by
    care: either the floats match bit for bit or D6 has become a second valuation engine.
    """
    mutated: list[str] = []
    approved_without_all_clauses: list[str] = []
    decided_while_ineligible: list[str] = []
    eligible_without_decision: list[str] = []
    approve_with_adverse_or_absent_gap: list[str] = []
    approve_without_positive_upside: list[str] = []
    decision_from_absent_evidence: list[str] = []
    provenance_chain_broken: list[str] = []

    for record in records:
        d5 = d5_rows[record.ticker]
        published = d5_published.get(record.ticker, {})
        for key, ours in (("TP1", d5.tp1), ("TP2", d5.tp2), ("BEAR_ANCHOR", d5.bear_anchor),
                          ("upside_to_TP1", d5.upside_to_tp1),
                          ("upside_to_TP2", d5.upside_to_tp2),
                          ("downside_to_bear", d5.downside_to_bear),
                          ("current_price", d5.current_price)):
            if key in published and published[key] != ours:
                mutated.append(f"{record.ticker} {key}: {ours} != published {published[key]}")

        ineligible = record.eligibility.eligibility is Eligibility.NOT_DECISION_ELIGIBLE
        if ineligible and record.decision is not None:
            decided_while_ineligible.append(f"{record.ticker}: {record.decision}")
        if not ineligible and record.decision is None:
            eligible_without_decision.append(record.ticker)

        if record.decision is Decision.APPROVE:
            if record.approve_blockers:
                approved_without_all_clauses.append(
                    f"{record.ticker}: blockers {list(record.approve_blockers)}")
            gap_clause = next(c for c in record.approve
                              if c.name == "expectation_gap_permits_approve")
            if not gap_clause.holds:
                approve_with_adverse_or_absent_gap.append(record.ticker)
            if not d5.base_case_supports_upside:
                approve_without_positive_upside.append(
                    f"{record.ticker}: upside_to_TP1={d5.upside_to_tp1}")

        if record.decision is not None:
            blockers = record.eligibility.blockers
            if blockers:
                decision_from_absent_evidence.append(f"{record.ticker}: {list(blockers)}")

    for ticker, break_reason in (chain_breaks or {}).items():
        provenance_chain_broken.append(f"{ticker}: {break_reason}")

    return {
        "d5_number_mutated_by_d6": mutated,
        "approve_without_every_clause_holding": approved_without_all_clauses,
        "decision_produced_for_an_ineligible_issuer": decided_while_ineligible,
        "eligible_issuer_without_a_decision": eligible_without_decision,
        "approve_on_an_adverse_or_absent_expectation_gap": approve_with_adverse_or_absent_gap,
        "approve_without_a_positive_base_case_upside": approve_without_positive_upside,
        "decision_resting_on_an_absent_layer": decision_from_absent_evidence,
        "d3_to_d4_provenance_chain_broken": provenance_chain_broken,
    }
