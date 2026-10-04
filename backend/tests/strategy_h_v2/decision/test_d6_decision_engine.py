"""H-V2-D6: the integrated decision engine - conjunction, abstention, provenance, immutability.

Pure tests throughout. Every D3, D4 and D5 projection is built by hand, so what is asserted is the
decision contract rather than the state of `data/runtime/`. Where a test needs a number it uses the
measured one from the pilot - VRRM's 3.54 close and 64.7% net debt share, GNW's entirely-below-price
range, SCCO's single method - so that a test that stops holding points at the row it came from.

The sharpest tests here are the negative ones. An engine that produces APPROVE is easy to write; the
contract is that it declines to, in six specific ways, and that it never reaches a decision at all on
an issuer missing a layer.
"""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from app.backtest.strategy_h_v2.decision.d6_contract import (
    ADVERSE_GAP_STATES,
    APPROVE_BLOCKING_UNKNOWN_FIELDS,
    BANNED_D6_FIELD_NAME_TOKENS,
    D6_NEVER_PRODUCED,
    APPROVE_CLAUSES,
    Clause,
    Confidence,
    D3Evidence,
    D3Provenance,
    D4Evidence,
    D4Provenance,
    D5Evidence,
    D6_CONTRACT_VERSION,
    Decision,
    DecisionRecord,
    Eligibility,
    EligibilityAssessment,
    ExpectationGap,
    GAP_CONFIDENCE_PERMITTING_APPROVE,
    GAP_STATES_PERMITTING_APPROVE,
    REJECT_TRIGGERS,
    VALUATION_CONFIDENCE_PERMITTING_APPROVE,
    approve_clauses,
    assess_eligibility,
    audit_decisions,
    decide,
    reject_triggers,
    watch_triggers,
)


# -------------------------------------------------------------------------------------------------
# Fixtures: the only combination that satisfies every APPROVE conjunct, and its negations
# -------------------------------------------------------------------------------------------------

def d3_strong(ticker: str = "TEST") -> D3Evidence:
    """A D3 output that satisfies every §N1 company clause. Nothing in the pilot looks like this."""
    return D3Evidence(
        ticker=ticker, provenance=D3Provenance.VALID, research_id="D3.3-TEST",
        research_output_checksum="c0ffee", research_completeness="PARTIAL",
        business_model_populated=True, fundamental_change_count=6,
        fundamental_change_anchored=True, growth_durability_state="DURABLE",
        future_business_stages=("REAL_BUSINESS",), future_business_evidence_present=True,
        catalyst_count=3, catalyst_material_and_timed=True, competitive_position_count=4,
        risk_count=7, invalidation_count=5, unknown_fields=("earnings_date",),
        evidence_conflict_count=0)


def d4_positive(ticker: str = "TEST") -> D4Evidence:
    return D4Evidence(
        ticker=ticker, provenance=D4Provenance.EVALUATED, analysis_id="TEST-a1",
        final_output_checksum="beef", gap=ExpectationGap.POSITIVE,
        gap_confidence=Confidence.MEDIUM, confidence_ceiling="MEDIUM",
        research_input_id="D3.3-TEST", research_input_checksum="c0ffee",
        d6_approve_precondition="PERMITTED", conflict_count=0, unknown_field_count=3,
        terminal_failure_codes=())


def d5_cheap(ticker: str = "TEST") -> D5Evidence:
    """A VALUED row with positive upside on both legs and MEDIUM confidence."""
    return D5Evidence(
        ticker=ticker, status="VALUED", confidence="MEDIUM", primary_method="EV/EBIT",
        secondary_method="EV/Sales", contract_window="FULL_2Y", current_price=100.0,
        tp1=130.0, tp2=160.0, bear_anchor=90.0, upside_to_tp1=0.30, upside_to_tp2=0.60,
        downside_to_bear=-0.10, reconciliation="CORROBORATES")


def test_the_only_combination_that_approves_does_approve():
    """The positive control. Without it, every negative test below could pass on a broken engine."""
    record = decide(d3_strong(), d4_positive(), d5_cheap())

    assert record.eligibility.eligibility is Eligibility.DECISION_ELIGIBLE
    assert record.decision is Decision.APPROVE
    assert record.approve_blockers == ()
    assert record.reject_fired == ()
    assert record.contract_version == D6_CONTRACT_VERSION


def test_every_approve_clause_is_individually_necessary():
    """§N1 is a conjunction, so breaking any one conjunct must remove the APPROVE.

    Parametrising over the clause NAMES rather than over a hand-written list of mutations is what
    makes this test survive a new clause being added: a conjunct nobody wrote a negation for would
    silently never be exercised.
    """
    negations = {
        "business_understood": (lambda d3: replace(d3, business_model_populated=False), None, None),
        "fundamental_change_anchored": (
            lambda d3: replace(d3, fundamental_change_anchored=False), None, None),
        "future_business_or_catalyst_meaningful": (
            lambda d3: replace(d3, future_business_evidence_present=False,
                               catalyst_material_and_timed=False), None, None),
        "expectation_gap_permits_approve": (
            None, lambda d4: replace(d4, gap=ExpectationGap.NEUTRAL,
                                     gap_confidence=Confidence.LOW,
                                     d6_approve_precondition="BLOCKED"), None),
        "valuation_supports_base_case_upside": (
            None, None, lambda d5: replace(d5, tp1=95.0, upside_to_tp1=-0.05)),
        "valuation_confidence_sufficient": (
            None, None, lambda d5: replace(d5, confidence="LOW")),
        "risks_with_an_invalidation_condition": (
            lambda d3: replace(d3, invalidation_count=0), None, None),
        "no_approve_blocking_unknown_field": (
            lambda d3: replace(d3, unknown_fields=("expectation_gap",)), None, None),
    }
    assert set(negations) == {name for name, _ in APPROVE_CLAUSES}

    for clause_name, (break_d3, break_d4, break_d5) in negations.items():
        d3 = break_d3(d3_strong()) if break_d3 else d3_strong()
        d4 = break_d4(d4_positive()) if break_d4 else d4_positive()
        d5 = break_d5(d5_cheap()) if break_d5 else d5_cheap()
        record = decide(d3, d4, d5)

        assert record.decision is not Decision.APPROVE, clause_name
        assert clause_name in record.approve_blockers, clause_name


# -------------------------------------------------------------------------------------------------
# D0 §L: the expectation-gap precondition
# -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("gap", list(ExpectationGap))
@pytest.mark.parametrize("confidence", list(Confidence))
def test_gap_precondition_is_exactly_d0_section_l(gap: ExpectationGap, confidence: Confidence):
    """Over the full 6 x 4 grid, APPROVE is reachable on exactly the eight cells §L permits."""
    d4 = replace(d4_positive(), gap=gap, gap_confidence=confidence,
                 d6_approve_precondition=None)
    expected = gap in GAP_STATES_PERMITTING_APPROVE and confidence in GAP_CONFIDENCE_PERMITTING_APPROVE

    assert d4.gap_permits_approve is expected

    record = decide(d3_strong(), d4, d5_cheap())
    if gap in ADVERSE_GAP_STATES:
        assert record.decision is Decision.REJECT
    else:
        assert (record.decision is Decision.APPROVE) is expected


def test_a_negative_gap_rejects_however_cheap_the_valuation_is():
    """D5-D0 §M: a cheap valuation does not promote a NEGATIVE gap."""
    very_cheap = replace(d5_cheap(), tp1=400.0, tp2=800.0, upside_to_tp1=3.0, upside_to_tp2=7.0)
    record = decide(d3_strong(), replace(d4_positive(), gap=ExpectationGap.WIDE_NEGATIVE,
                                         d6_approve_precondition="BLOCKED"), very_cheap)

    assert record.decision is Decision.REJECT
    assert "adverse_expectation_gap" in record.reject_fired


def test_an_expensive_valuation_does_not_demote_the_gap_it_blocks_the_approve():
    """The other half of D5-D0 §M. The gap clause still HOLDS - only the valuation clause fails."""
    record = decide(d3_strong(), d4_positive(),
                    replace(d5_cheap(), tp1=95.0, upside_to_tp1=-0.05))

    gap_clause = next(c for c in record.approve if c.name == "expectation_gap_permits_approve")
    assert gap_clause.holds is True
    assert record.approve_blockers == ("valuation_supports_base_case_upside",)
    assert record.decision is Decision.WATCH


# -------------------------------------------------------------------------------------------------
# D4 absence: three states, and none of them is a reading
# -------------------------------------------------------------------------------------------------

def _absent(provenance: D4Provenance) -> D4Evidence:
    return D4Evidence(ticker="TEST", provenance=provenance, analysis_id=None,
                      final_output_checksum=None, research_input_id=None,
                      research_input_checksum=None, gap=None, gap_confidence=None,
                      confidence_ceiling=None, d6_approve_precondition=None, conflict_count=0,
                      unknown_field_count=0, terminal_failure_codes=())


@pytest.mark.parametrize("provenance",
                         [D4Provenance.NOT_EVALUATED, D4Provenance.REFUSED_NO_FINAL_OUTPUT])
def test_an_absent_d4_makes_the_issuer_ineligible_and_produces_no_decision(provenance):
    """§10. An absence of evidence is not NEUTRAL, not UNKNOWN, and not a WATCH."""
    record = decide(d3_strong(), _absent(provenance), d5_cheap())

    assert record.eligibility.eligibility is Eligibility.NOT_DECISION_ELIGIBLE
    assert record.decision is None
    assert any(provenance.value in blocker for blocker in record.eligibility.blockers)


def test_unknown_is_decided_on_while_an_absence_is_not():
    """The distinction that §10 exists to protect, asserted as a difference in OUTCOME."""
    unknown = replace(d4_positive(), gap=ExpectationGap.UNKNOWN,
                      gap_confidence=Confidence.UNKNOWN, d6_approve_precondition="BLOCKED")

    on_unknown = decide(d3_strong(), unknown, d5_cheap())
    on_absence = decide(d3_strong(), _absent(D4Provenance.NOT_EVALUATED), d5_cheap())

    assert on_unknown.eligibility.eligibility is Eligibility.DECISION_ELIGIBLE
    assert on_unknown.decision is Decision.WATCH
    assert on_absence.decision is None


def test_the_three_d4_provenance_states_are_distinct_values():
    assert len({p.value for p in D4Provenance}) == 3
    assert D4Provenance.NOT_EVALUATED.value != D4Provenance.REFUSED_NO_FINAL_OUTPUT.value
    assert "UNKNOWN" not in {p.value for p in D4Provenance}


def test_a_d4_precondition_disagreement_is_reported_rather_than_resolved():
    """Two implementations of one frozen rule disagreeing is a finding, not a tie to break."""
    agreeing = replace(d4_positive(), d6_approve_precondition="PERMITTED")
    disagreeing = replace(d4_positive(), gap=ExpectationGap.NEUTRAL,
                          gap_confidence=Confidence.LOW, d6_approve_precondition="PERMITTED")

    assert agreeing.precondition_disagreement is None
    assert disagreeing.precondition_disagreement is not None
    assert "BLOCKED" in disagreeing.precondition_disagreement


# -------------------------------------------------------------------------------------------------
# Valuation: NOT_READY, LOW confidence, fully priced
# -------------------------------------------------------------------------------------------------

def test_a_valuation_not_ready_issuer_is_ineligible_and_is_neither_positive_nor_negative():
    """§5 and §6. NOT_READY is a complete D5 output and carries no directional content."""
    not_ready = D5Evidence(
        ticker="TEST", status="VALUATION_NOT_READY", confidence="NOT_READY", primary_method=None,
        secondary_method=None, contract_window=None, current_price=100.0, tp1=None, tp2=None,
        bear_anchor=None, upside_to_tp1=None, upside_to_tp2=None, downside_to_bear=None,
        reconciliation=None)
    record = decide(d3_strong(), d4_positive(), not_ready)

    assert record.decision is None
    assert record.eligibility.eligibility is Eligibility.NOT_DECISION_ELIGIBLE
    assert not_ready.base_case_supports_upside is False
    assert not_ready.fully_priced is False, "a missing TP2 is not 'fully priced'"
    assert "valuation_fully_prices_the_positive_case" not in \
        [c.name for c in record.reject if c.holds]


def test_a_low_confidence_valuation_cannot_carry_an_approve_but_is_not_a_reject():
    """§6: LOW may not be used at MEDIUM's strength, and it is not an adverse finding either."""
    record = decide(d3_strong(), d4_positive(), replace(d5_cheap(), confidence="LOW"))

    assert record.decision is Decision.WATCH
    assert record.approve_blockers == ("valuation_confidence_sufficient",)
    assert record.reject_fired == ()
    assert "valuation_confidence_low" in record.watch_matched
    assert "LOW" not in VALUATION_CONFIDENCE_PERMITTING_APPROVE


def test_a_great_company_that_is_fully_priced_is_rejected_on_the_bull_leg():
    """GNW's shape: every leg of the range below the price. TP2 is the full positive case."""
    overvalued = replace(d5_cheap(), tp1=87.4, tp2=96.4, bear_anchor=76.7,
                         upside_to_tp1=-0.126, upside_to_tp2=-0.036, downside_to_bear=-0.233)
    record = decide(d3_strong(), d4_positive(), overvalued)

    assert overvalued.fully_priced is True
    assert record.decision is Decision.REJECT
    assert "valuation_fully_prices_the_positive_case" in record.reject_fired


def test_a_negative_tp1_with_a_positive_tp2_is_a_watch_not_a_reject():
    """§11's line: insufficient Base-case upside blocks APPROVE; only the Bull leg rejects."""
    adbe_shaped = replace(d5_cheap(), tp1=248.23, tp2=272.47, current_price=250.50,
                          upside_to_tp1=-0.009, upside_to_tp2=0.088)
    record = decide(d3_strong(), d4_positive(), adbe_shaped)

    assert adbe_shaped.fully_priced is False
    assert record.decision is Decision.WATCH
    assert "insufficient_margin_of_safety" in record.watch_matched


def test_a_cheap_valuation_cannot_carry_a_weak_thesis():
    """§11's other direction: upside is a necessary condition, never a sufficient one."""
    weak = replace(d3_strong(), business_model_populated=False, fundamental_change_count=0,
                   fundamental_change_anchored=False)
    very_cheap = replace(d5_cheap(), tp1=400.0, upside_to_tp1=3.0, tp2=800.0, upside_to_tp2=7.0)
    record = decide(weak, d4_positive(), very_cheap)

    assert record.decision is Decision.REJECT
    assert "thesis_unsupported_by_the_evidence_bundle" in record.reject_fired


# -------------------------------------------------------------------------------------------------
# D0 §J / §K: story-only future business, and the undated catalyst
# -------------------------------------------------------------------------------------------------

def test_story_only_future_business_with_no_corroborating_catalyst_rejects():
    story = replace(d3_strong(), future_business_stages=("STORY",),
                    future_business_evidence_present=False, catalyst_count=2,
                    catalyst_material_and_timed=False)
    record = decide(story, d4_positive(), d5_cheap())

    assert record.decision is Decision.REJECT
    assert "future_business_story_only_without_corroborating_catalyst" in record.reject_fired


def test_story_only_future_business_with_a_material_timed_catalyst_is_not_a_reject():
    """D0 §J: STORY_UNVERIFIED future business may still matter as a catalyst (§K)."""
    story = replace(d3_strong(), future_business_stages=("STORY",),
                    future_business_evidence_present=False, catalyst_material_and_timed=True)
    record = decide(story, d4_positive(), d5_cheap())

    assert record.reject_fired == ()
    assert record.decision is Decision.APPROVE


def test_an_undated_catalyst_is_a_watch_trigger_and_never_a_reject():
    """D0 §N2 lists it as WATCH; D0 §N3 rejects only on the ABSENCE of any candidate.

    The §N2 trigger matching is not by itself a demotion, and this test is where that is at its most
    counter-intuitive: an issuer whose only catalyst is undated still APPROVEs when §N1's clause is
    satisfied by the OTHER half of its disjunction, because §N1 reads 'future-business OR catalyst
    evidence is meaningful'. Reading the matched §N2 trigger as a demotion would quietly turn §N1's
    disjunction into a conjunction.
    """
    undated = replace(d3_strong(), catalyst_count=3, catalyst_material_and_timed=False)
    record = decide(undated, d4_positive(), d5_cheap())

    assert "no_catalyst_identifiable_in_the_thesis_horizon" not in record.reject_fired
    assert "catalyst_plausible_but_not_dated_or_confirmed" in record.watch_matched
    assert record.decision is Decision.APPROVE, "the future-business leg of §N1's OR holds"

    # Remove the other leg of the disjunction and the undated catalyst can no longer carry §N1.
    only_undated = replace(undated, future_business_evidence_present=False,
                           future_business_stages=())
    blocked = decide(only_undated, d4_positive(), d5_cheap())
    assert blocked.decision is Decision.WATCH
    assert "future_business_or_catalyst_meaningful" in blocked.approve_blockers
    assert blocked.reject_fired == (), "an undated catalyst is never a REJECT"

    none_at_all = replace(undated, catalyst_count=0, future_business_evidence_present=True)
    assert "no_catalyst_identifiable_in_the_thesis_horizon" in \
        decide(none_at_all, d4_positive(), d5_cheap()).reject_fired


# -------------------------------------------------------------------------------------------------
# Numbers: D6 owns none
# -------------------------------------------------------------------------------------------------

def test_d6_never_mutates_a_d5_target_price():
    """The targets the record reports are the floats D5 published, compared at full precision."""
    d5 = replace(d5_cheap(), tp1=343.17949756097563, tp2=417.9023119097561,
                 current_price=289.93, upside_to_tp1=0.18366547743069663,
                 upside_to_tp2=0.44139969956097563)
    record = decide(d3_strong(), d4_positive(), d5)

    published = {"TP1": 343.17949756097563, "TP2": 417.9023119097561,
                 "upside_to_TP1": 0.18366547743069663,
                 "upside_to_TP2": 0.44139969956097563, "current_price": 289.93}
    defects = audit_decisions([record], {"TEST": d5}, {"TEST": published})

    assert defects["d5_number_mutated_by_d6"] == []
    assert d5.tp1 == 343.17949756097563
    assert d5.tp2 == 417.9023119097561


def test_the_audit_catches_a_mutated_target_price():
    """The immutability audit must be able to fail, or it proves nothing."""
    d5 = replace(d5_cheap(), tp1=130.0)
    record = decide(d3_strong(), d4_positive(), d5)

    defects = audit_decisions([record], {"TEST": d5}, {"TEST": {"TP1": 130.00000000001}})

    assert len(defects["d5_number_mutated_by_d6"]) == 1
    assert "TP1" in defects["d5_number_mutated_by_d6"][0]


def test_no_score_or_execution_field_exists_anywhere_in_the_decision_module():
    """§19 and §24. Decision and score are separated by there being no score to gate on.

    The same shape D5-D1 used for 'no fair value here': rather than promising that a score is not
    used and that no position is sized, assert that no dataclass in the module has a field either
    could occupy. A promise is checked by reading; this is checked by running.
    """
    for cls in (D3Evidence, D4Evidence, D5Evidence, Clause, EligibilityAssessment, DecisionRecord):
        for f in fields(cls):
            offending = [token for token in BANNED_D6_FIELD_NAME_TOKENS
                         if token in f.name.lower()]
            assert not offending, (cls.__name__, f.name, offending)


def test_the_record_emits_no_execution_or_score_key():
    """The dict a later step would read, checked key by key rather than by inspection."""
    as_dict = decide(d3_strong(), d4_positive(), d5_cheap()).to_dict()

    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from keys(v)

    for key in keys(as_dict):
        offending = [t for t in BANNED_D6_FIELD_NAME_TOKENS if t in key.lower()]
        assert not offending, (key, offending)

    assert len(D6_NEVER_PRODUCED) == 8


def test_the_decision_is_a_pure_function_of_the_clause_lists():
    """Reproducibility, asserted structurally: nothing outside the two lists can move the label."""
    for d5 in (d5_cheap(), replace(d5_cheap(), confidence="LOW"),
               replace(d5_cheap(), tp1=50.0, upside_to_tp1=-0.5, tp2=60.0, upside_to_tp2=-0.4)):
        for gap in list(ExpectationGap):
            record = decide(d3_strong(), replace(d4_positive(), gap=gap), d5)
            if record.decision is None:
                continue
            if record.reject_fired:
                assert record.decision is Decision.REJECT
            elif record.approve_blockers:
                assert record.decision is Decision.WATCH
            else:
                assert record.decision is Decision.APPROVE


def test_watch_triggers_never_change_the_outcome():
    """§N2 triggers explain a WATCH and do not cause one: an issuer can WATCH with none matched."""
    d3 = replace(d3_strong(), risk_count=0)
    record = decide(d3, d4_positive(), d5_cheap())

    assert record.decision is Decision.WATCH
    assert record.watch_matched == ()
    assert record.approve_blockers == ("risks_with_an_invalidation_condition",)


# -------------------------------------------------------------------------------------------------
# Eligibility is a statement about the sample, not a fourth decision
# -------------------------------------------------------------------------------------------------

def test_eligibility_requires_all_three_layers_and_names_every_missing_one():
    nothing = assess_eligibility(
        D3Evidence(ticker="T", provenance=D3Provenance.NOT_EVALUATED, research_id=None,
                   research_output_checksum=None, research_completeness=None,
                   business_model_populated=False, fundamental_change_count=0,
                   fundamental_change_anchored=False, growth_durability_state=None,
                   future_business_stages=(), future_business_evidence_present=False,
                   catalyst_count=0, catalyst_material_and_timed=False,
                   competitive_position_count=0, risk_count=0, invalidation_count=0,
                   unknown_fields=(), evidence_conflict_count=0),
        _absent(D4Provenance.NOT_EVALUATED),
        D5Evidence(ticker="T", status="VALUATION_NOT_READY", confidence="NOT_READY",
                   primary_method=None, secondary_method=None, contract_window=None,
                   current_price=None, tp1=None, tp2=None, bear_anchor=None, upside_to_tp1=None,
                   upside_to_tp2=None, downside_to_bear=None, reconciliation=None))

    assert nothing.eligibility is Eligibility.NOT_DECISION_ELIGIBLE
    assert len(nothing.blockers) == 3
    assert assess_eligibility(d3_strong(), d4_positive(), d5_cheap()).eligibility \
        is Eligibility.DECISION_ELIGIBLE


def test_there_is_no_fourth_decision_state():
    """§18's audit, asserted. WATCH is the abstention and the enum has exactly three members."""
    assert {d.value for d in Decision} == {"APPROVE", "WATCH", "REJECT"}
    assert "DECISION_NOT_READY" not in {d.value for d in Decision}
    assert "NOT_DECISION_ELIGIBLE" not in {d.value for d in Decision}


def test_a_neutral_gap_at_low_confidence_lands_on_watch_with_a_stated_reason():
    """The pilot's most common shape - and it must be a WATCH, not a REJECT or an abstention."""
    neutral = replace(d4_positive(), gap=ExpectationGap.NEUTRAL, gap_confidence=Confidence.LOW,
                      d6_approve_precondition="BLOCKED")
    record = decide(d3_strong(), neutral, d5_cheap())

    assert record.decision is Decision.WATCH
    assert record.approve_blockers == ("expectation_gap_permits_approve",)
    assert "thesis_needs_confirmation_from_the_next_print" in record.watch_matched


# -------------------------------------------------------------------------------------------------
# §N1's unknown-field clause, and the triggers D6 cannot evaluate
# -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("token", APPROVE_BLOCKING_UNKNOWN_FIELDS)
def test_each_blocking_unknown_field_blocks_approve(token: str):
    d3 = replace(d3_strong(), unknown_fields=(f"{token} (code-owned UNKNOWN)",))
    record = decide(d3, d4_positive(), d5_cheap())

    assert record.decision is Decision.WATCH
    assert "no_approve_blocking_unknown_field" in record.approve_blockers
    assert d3.blocking_unknown_fields


def test_an_unrelated_unknown_field_does_not_block_approve():
    """`fundamentals.revenue (AMBIGUOUS)` is not `fundamental_change`, and the matcher must know."""
    d3 = replace(d3_strong(), unknown_fields=(
        "earnings_date", "fundamentals.revenue (AMBIGUOUS)", "market_share", "gross_profit"))

    assert d3.blocking_unknown_fields == ()
    assert decide(d3, d4_positive(), d5_cheap()).decision is Decision.APPROVE


def test_the_two_unevaluable_reject_triggers_never_fire_and_say_so():
    """A trigger that is silently never evaluated reads exactly like one that never fired."""
    unevaluable = {name for name, _ref, mechanical in REJECT_TRIGGERS if not mechanical}
    assert unevaluable == {"structural_deterioration_without_offsetting_catalyst",
                           "risk_unacceptable_against_any_plausible_upside"}

    triggers = {c.name: c for c in reject_triggers(d3_strong(), d4_positive(), d5_cheap())}
    assert set(triggers) == {name for name, _ref, _m in REJECT_TRIGGERS}
    for name in unevaluable:
        assert triggers[name].holds is False
        assert triggers[name].basis.startswith("NOT_EVALUATED: ")


def test_every_clause_names_the_contract_line_it_implements():
    """A clause whose basis reads like a judgement is a judgement."""
    record = decide(d3_strong(), d4_positive(), d5_cheap())

    for clause in record.approve + record.reject:
        assert clause.contract_reference.startswith(("D0 ", "D6 ")), clause.name
        assert clause.basis, clause.name


def test_the_record_round_trips_to_a_dict_with_its_whole_derivation():
    record = decide(d3_strong(), d4_positive(), d5_cheap())
    as_dict = record.to_dict()

    assert as_dict["decision"] == "APPROVE"
    assert len(as_dict["approve_clauses"]) == len(APPROVE_CLAUSES)
    assert len(as_dict["reject_triggers"]) == len(REJECT_TRIGGERS)
    assert as_dict["provenance"]["d3_output_checksum"] == "c0ffee"
    assert as_dict["provenance"]["d4_output_checksum"] == "beef"


# -------------------------------------------------------------------------------------------------
# The D6 audit
# -------------------------------------------------------------------------------------------------

def test_the_audit_catches_a_decision_on_an_ineligible_issuer():
    """Hand-built because the engine cannot produce it - which is what the audit is guarding."""
    d5 = d5_cheap()
    forged = DecisionRecord(
        ticker="TEST", contract_version=D6_CONTRACT_VERSION,
        eligibility=EligibilityAssessment(Eligibility.NOT_DECISION_ELIGIBLE,
                                          ("D4 D4_NOT_EVALUATED",)),
        decision=Decision.APPROVE, approve=approve_clauses(d3_strong(), d4_positive(), d5),
        reject=reject_triggers(d3_strong(), d4_positive(), d5),
        watch=watch_triggers(d3_strong(), d4_positive(), d5))

    defects = audit_decisions([forged], {"TEST": d5}, {})

    assert defects["decision_produced_for_an_ineligible_issuer"]
    assert defects["decision_resting_on_an_absent_layer"]


def test_a_clean_run_has_no_d6_defects():
    d5 = d5_cheap()
    records = [decide(d3_strong(), d4_positive(), d5),
               decide(d3_strong(), _absent(D4Provenance.REFUSED_NO_FINAL_OUTPUT), d5)]

    defects = audit_decisions(records, {"TEST": d5}, {"TEST": {"TP1": 130.0, "TP2": 160.0}})

    assert all(offenders == [] for offenders in defects.values()), defects


# -------------------------------------------------------------------------------------------------
# The D3 -> D4 provenance chain
# -------------------------------------------------------------------------------------------------

def test_an_intact_chain_reports_no_break():
    """D4 recorded the id and the checksum of the D3 output it consumed, so this is checkable."""
    assert d4_positive().chain_break(d3_strong()) is None


def test_a_different_d3_output_than_d4_consumed_is_a_chain_break():
    """The defect this exists to catch would be invisible everywhere else: both legs look fine."""
    other_research = replace(d3_strong(), research_id="D3.3-OTHER",
                             research_output_checksum="deadbeef")

    break_reason = d4_positive().chain_break(other_research)

    assert break_reason is not None
    assert "D3.3-OTHER" in break_reason


def test_a_matching_id_with_a_different_checksum_is_still_a_chain_break():
    """The id is a name and the checksum is the content. The content is what has to match."""
    edited = replace(d3_strong(), research_output_checksum="deadbeef")

    break_reason = d4_positive().chain_break(edited)

    assert break_reason is not None
    assert "checksum" in break_reason


def test_an_absent_d4_has_no_chain_to_break():
    assert _absent(D4Provenance.NOT_EVALUATED).chain_break(d3_strong()) is None
    assert _absent(D4Provenance.REFUSED_NO_FINAL_OUTPUT).chain_break(d3_strong()) is None


def test_the_audit_surfaces_a_chain_break_as_its_own_defect_class():
    d5 = d5_cheap()
    record = decide(d3_strong(), d4_positive(), d5)

    clean = audit_decisions([record], {"TEST": d5}, {})
    broken = audit_decisions([record], {"TEST": d5}, {},
                             {"TEST": "D4 consumed X while D6 read Y"})

    assert clean["d3_to_d4_provenance_chain_broken"] == []
    assert broken["d3_to_d4_provenance_chain_broken"] == ["TEST: D4 consumed X while D6 read Y"]
