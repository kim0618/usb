"""D4-S: the audit's citation-form test, the A/B/C classification, and what did NOT change.

The finding this file encodes. Final Tier A V3's audit reported 17 `unsourced_material_claims`
(SCCO 7, GOOG 4, BSY 6) and D4.3A's reported 19. Every one of them was a claim using `ClaimV2`'s
COMPOUND citation form - `evidence_ids` with `source_id` null - which is not merely allowed but
MANDATED by `ClaimV2._exactly_one_citation_form`:

    if atomic and (self.source_id is None or self.evidence_id is None): raise
    if atomic and compound: raise

so a compound claim MUST leave `source_id` null, while the audit tested `not claim.get("source_id")`
and called the result "no citation". The audit was demanding what the schema forbids. That is
provable from the two contracts alone, with no reference to any run's numbers, which is why fixing
it is not a threshold tuned to a result: `test_the_schema_forbids_what_the_audit_used_to_demand`
below is the proof, and it would hold if no live run had ever happened.

E2's threshold is untouched at zero (D4-S §16). What D4-S froze is the MEANING of its denominator.
"""

from __future__ import annotations

import pytest
from d4_helpers import NOW, SOURCE_ID, claim, d4_content, expectation_bundle, package
from pydantic import ValidationError

from app.backtest.strategy_h_v2.expectation.analysis_schema import HExpectationGapAnalysisV1
from app.backtest.strategy_h_v2.expectation.code_facts import build_code_fact_index, code_source_id
from app.backtest.strategy_h_v2.expectation.d4_1_contract import (
    E2_MAX_UNSOURCED_MATERIAL_GAP_CLAIMS,
)
from app.backtest.strategy_h_v2.expectation.gap_contract import ExpectationGapState
from app.backtest.strategy_h_v2.research.schema_v2 import ClaimV2
from app.dev.audit_strategy_h_v2_d4_1 import (
    CLAIM_SOURCING_A,
    CLAIM_SOURCING_B,
    CLAIM_SOURCING_C,
    audit_output,
    classify_claim_sourcing,
    material_source_required_defects,
)

PKG = package()
BUNDLE = expectation_bundle(pre_event_price_context={
    "last_close": 189.88,
    "close_252s_low": 106.88,
    "return_3m": {"value": 0.18, "status": "COMPLETE",
                  "from_session": "2026-06-16", "to_session": "2026-09-16"},
})
FACTS = build_code_fact_index(BUNDLE)
CODE_EVIDENCE = sorted(FACTS)
DOC_EVIDENCE = f"{SOURCE_ID}:CHUNK:0"


def compound(text: str, evidence_ids: list[str], claim_type: str = "INTERPRETATION") -> dict:
    """`ClaimV2`'s compound form: two or more evidence ids, and `source_id` left null BY CONTRACT."""
    return {"text": text, "claim_type": claim_type, "source_id": None, "evidence_id": None,
            "evidence_ids": evidence_ids, "confidence": "MEDIUM"}


# --- the proof, independent of any run ----------------------------------------------------------

def test_the_schema_forbids_what_the_audit_used_to_demand():
    """A compound claim that also carries `source_id` does not validate. So requiring `source_id`
    on every material claim is requiring an output the schema rejects - the audit's old test could
    only ever have been wrong, whatever any run happened to measure."""
    with pytest.raises(ValidationError):
        ClaimV2(text="A compound claim.", claim_type="INTERPRETATION", source_id=SOURCE_ID,
                evidence_ids=[DOC_EVIDENCE, CODE_EVIDENCE[0]], confidence="MEDIUM")

    ok = ClaimV2(text="A compound claim.", claim_type="INTERPRETATION",
                 evidence_ids=[DOC_EVIDENCE, CODE_EVIDENCE[0]], confidence="MEDIUM")
    assert ok.source_id is None


def test_a_correctly_cited_compound_claim_is_no_longer_counted_as_uncited():
    output = _output(gap_rationale=[compound("Two facts, read together.",
                                             [DOC_EVIDENCE, CODE_EVIDENCE[0]])])
    defects = audit_output(output, package=PKG, bundle=BUNDLE)
    assert defects["unsourced_material_claims"] == []


def test_a_genuinely_uncited_material_claim_is_still_counted():
    """Built WITHOUT schema validation on purpose: `ClaimV2` rejects this claim outright, and the
    audit is a second, independent reading of stored bytes rather than a re-run of the schema. It
    has to catch this even though a response containing it could not have been accepted live."""
    output = _output(schema_valid=False, gap_rationale=[{
        "text": "No citation at all.", "claim_type": "INTERPRETATION", "source_id": None,
        "evidence_id": None, "evidence_ids": [], "confidence": "MEDIUM"}])
    defects = audit_output(output, package=PKG, bundle=BUNDLE)
    reasons = [d["reason"] for d in defects["unsourced_material_claims"]]
    assert reasons == ["no citation"]


def test_a_compound_claim_citing_an_unresolvable_id_is_still_counted():
    """Also unvalidated: an orphan evidence id fails `_sources_cover_citations`. Same reason - the
    fix to the citation FORM must not become a blanket pass for the citation's CONTENT."""
    output = _output(schema_valid=False,
                     gap_rationale=[compound("Cites something that does not exist.",
                                             [DOC_EVIDENCE, "SEC:9:GHOST:CHUNK:0"])])
    defects = audit_output(output, package=PKG, bundle=BUNDLE)
    assert defects["unsourced_material_claims"], "an unresolvable id must still be a defect"
    assert any("unresolvable" in d["reason"] for d in defects["unsourced_material_claims"])


# --- A / B / C classification (§12-§15) ---------------------------------------------------------

def test_a_claim_leaning_on_a_document_chunk_is_a_type_a():
    assert classify_claim_sourcing(
        claim("Revenue grew, per the filing."), code_facts=FACTS) == CLAIM_SOURCING_A


def test_a_claim_citing_only_code_owned_facts_is_a_type_b():
    assert classify_claim_sourcing(
        compound("The computed reaction and the computed context agree.",
                 CODE_EVIDENCE[:2]), code_facts=FACTS) == CLAIM_SOURCING_B


def test_a_claim_mixing_a_document_and_a_code_fact_is_a_type_a():
    """One uncited filing assertion is not excused by the code facts standing beside it."""
    assert classify_claim_sourcing(
        compound("A filing statement read against a computed number.",
                 [DOC_EVIDENCE, CODE_EVIDENCE[0]]), code_facts=FACTS) == CLAIM_SOURCING_A


def test_an_unknown_typed_claim_is_a_type_c():
    """The schema exempts UNKNOWN from citation outright, so asking what it cites is meaningless."""
    assert classify_claim_sourcing(
        {"text": "Timing cannot be determined.", "claim_type": "UNKNOWN", "source_id": None,
         "evidence_id": None, "evidence_ids": [], "confidence": "UNKNOWN"},
        code_facts=FACTS) == CLAIM_SOURCING_C


def test_meta_limitation_prose_is_never_walked_as_a_material_claim():
    """§15. `limitations` and `unknown_fields` are plain strings, not claims - the claim walker
    cannot reach them, so a limitation can never be counted as an unsourced material claim. This is
    structural, not a new exclusion added here."""
    output = _output(limitations=["Available evidence is insufficient to date the conversion."],
                     unknown_fields=["consensus"])
    defects = audit_output(output, package=PKG, bundle=BUNDLE)
    assert defects["unsourced_material_claims"] == []
    paths = [s["path"] for s in defects["expectation_claim_sourcing"]]
    assert not any("limitations" in p or "unknown_fields" in p for p in paths)


def test_e2s_numerator_counts_only_type_a_and_keeps_its_frozen_threshold():
    output = _output(gap_rationale=[compound("Two computed facts.", CODE_EVIDENCE[:2])])
    defects = audit_output(output, package=PKG, bundle=BUNDLE)
    assert material_source_required_defects(defects) == []
    assert E2_MAX_UNSOURCED_MATERIAL_GAP_CLAIMS == 0


def test_every_claim_is_classified_exactly_once():
    output = _output(gap_rationale=[compound("Two computed facts.", CODE_EVIDENCE[:2])],
                     supporting_claims=[claim("A filing fact.")])
    defects = audit_output(output, package=PKG, bundle=BUNDLE)
    sourcing = defects["expectation_claim_sourcing"]
    assert len(sourcing) == len({s["path"] for s in sourcing})
    assert {s["classification"] for s in sourcing} <= {
        CLAIM_SOURCING_A, CLAIM_SOURCING_B, CLAIM_SOURCING_C}


# --- the newly visible M8 blind spot is reported, not closed (§13) -------------------------------

def test_m8s_scope_stays_atomic_and_the_compound_gap_is_reported_separately():
    """Before D4-S the citation-form bug `continue`d compound claims out of the loop, so M8 has
    never been applied to one. Widening it now would change what a frozen gate measures after its
    results exist, so the finding is surfaced in its own key, wired to no gate."""
    fact = FACTS[CODE_EVIDENCE[0]]
    output = _output(gap_rationale=[compound(
        f"The computed value was 99.99 rather than {fact.value}.",
        [CODE_EVIDENCE[0], CODE_EVIDENCE[1]])])
    defects = audit_output(output, package=PKG, bundle=BUNDLE)
    assert defects["code_owned_numeric_defects"] == [], "M8's scope must not widen here"
    gap = [g for g in defects["compound_claim_coverage_gap"]
           if g["check"] == "code_owned_numeric"]
    assert gap, "the blind spot must be reported rather than silently kept"


def test_an_atomic_claim_restating_a_code_fact_wrongly_is_still_an_m8_defect():
    fact = FACTS[CODE_EVIDENCE[0]]
    output = _output(gap_rationale=[{
        "text": "The computed reaction was 99.99 percent.", "claim_type": "INTERPRETATION",
        "source_id": code_source_id(BUNDLE.bundle_id), "evidence_id": CODE_EVIDENCE[0],
        "evidence_ids": [], "confidence": "MEDIUM"}])
    defects = audit_output(output, package=PKG, bundle=BUNDLE)
    assert defects["code_owned_numeric_defects"], f"fact {fact.value} must still be enforced"


# --- regression: what D4-S must not have moved ---------------------------------------------------

def test_the_gap_enum_is_unchanged():
    assert [s.value for s in ExpectationGapState] == [
        "WIDE_POSITIVE", "POSITIVE", "NEUTRAL", "NEGATIVE", "WIDE_NEGATIVE", "UNKNOWN"]


def test_the_audit_still_reports_every_frozen_defect_category():
    defects = audit_output(_output(), package=PKG, bundle=BUNDLE)
    for key in ("unsourced_material_claims", "future_source_leaks", "code_owned_numeric_defects",
                "fabricated_consensus", "decision_field_leaks", "decision_vocabulary_leaks",
                "unknown_discipline_violations", "unsupported_priced_in"):
        assert key in defects, key


def test_the_expectation_state_travels_with_the_audit_record():
    """§19: one object, read by the runtime validator, the audit and the manual helper alike."""
    defects = audit_output(_output(), package=PKG, bundle=BUNDLE)
    assert defects["expectation_state"]["market_expectation_claim_allowed"] is False
    assert defects["expectation_state"]["status"] == "UNKNOWN"


def _output(*, schema_valid: bool = True, **overrides) -> dict:
    content = d4_content(**overrides)
    content.update({
        "schema_version": "h_expectation_gap_analysis_v2",
        "analysis_id": "A-1", "version": 1, "candidate_id": "ACME", "ticker": "ACME",
        "decision_time": NOW.isoformat(), "created_at": NOW.isoformat(),
        "research_input_id": "RUN-1:ACME", "research_input_checksum": "x",
        "expectation_evidence_id": BUNDLE.bundle_id, "expectation_evidence_checksum": "y",
        "model_name": "claude-opus-5-5", "model_version": "opus",
        "prompt_version": "h_v2_d4_expectation_gap_v2",
    })
    cited = {SOURCE_ID, code_source_id(BUNDLE.bundle_id)}
    content["sources"] = sorted(set(content.get("sources") or []) | cited)
    if schema_valid:
        HExpectationGapAnalysisV1.model_validate(content)
    return content
