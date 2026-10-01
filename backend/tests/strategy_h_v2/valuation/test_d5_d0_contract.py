"""H-V2-D5-D0: the Valuation Fundamentals contract.

These tests exist to stop two different things. The first is the obvious one: a valuation layer that
computes a multiple from a negative denominator, a partial-year flow, a stale balance sheet or a debt
figure that is not total debt. The second is the one that only a test can catch - a later step
quietly relaxing a refusal because it is inconvenient. So the refusals are asserted as hard facts,
including the ones that make D5 v1 look unproductive.

Nothing here runs a valuation. The last group asserts that: no model call, no target-price
arithmetic, and no realized-return input anywhere in the package.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.backtest.strategy_h0.facts import FIELD_SPECS
from app.backtest.strategy_h_v2.valuation import d5_d0_contract as C
from app.backtest.strategy_h_v2.valuation.d5_d0_contract import (
    FieldAvailability,
    MethodId,
    MethodStatus,
    PeriodFamily,
    Scenario,
    ValuationCompleteness,
)


# --- Period consistency (brief §7), the dominant correctness problem ---------------------------

class TestPeriodFamily:
    def test_the_measured_ytd_cash_flow_is_classified_as_ytd_not_annual(self):
        """AEYE's `operating_cash_flow` at the D2.1 cutoff: start 2026-01-01, end 2026-06-30,
        status OK, 180 days. A valuation that read that as annual would be wrong by about half."""
        assert C.period_family(date(2026, 1, 1), date(2026, 6, 30)) is PeriodFamily.YTD

    def test_a_discrete_quarter_and_a_year_are_distinguished(self):
        assert C.period_family(date(2026, 4, 1), date(2026, 6, 30)) is PeriodFamily.QUARTER
        assert C.period_family(date(2025, 7, 1), date(2026, 6, 30)) is PeriodFamily.ANNUAL
        assert C.period_family(date(2026, 1, 1), date(2026, 9, 30)) is PeriodFamily.YTD

    def test_an_instant_fact_is_an_instant(self):
        assert C.period_family(None, date(2026, 6, 30)) is PeriodFamily.INSTANT

    def test_an_unclassifiable_span_is_unknown_and_not_the_nearest_family(self):
        for start, end in ((date(2026, 1, 1), date(2026, 2, 15)),
                           (date(2026, 1, 1), date(2026, 5, 1)),
                           (date(2024, 1, 1), date(2026, 1, 1))):
            assert C.period_family(start, end) is PeriodFamily.UNKNOWN, (start, end)

    def test_a_missing_end_is_unknown(self):
        assert C.period_family(date(2026, 1, 1), None) is PeriodFamily.UNKNOWN

    def test_a_first_quarter_is_ytd_when_the_fiscal_year_start_is_known(self):
        """Q1 and Q1-year-to-date are the same span. With the fiscal year start supplied the
        contract calls it YTD, which keeps it from being compared against a Q3 YTD figure."""
        assert C.period_family(date(2026, 1, 1), date(2026, 3, 31),
                              fiscal_year_start=date(2026, 1, 1)) is PeriodFamily.YTD
        assert C.period_family(date(2026, 1, 1), date(2026, 3, 31)) is PeriodFamily.QUARTER
        assert "fiscal-year start" in C.YTD_Q1_INDISTINGUISHABLE


class TestPeriodComparability:
    def test_a_quarter_over_a_ytd_is_not_a_growth_rate(self):
        assert not C.growth_periods_comparable(PeriodFamily.QUARTER, PeriodFamily.YTD)
        assert not C.growth_periods_comparable(PeriodFamily.TTM, PeriodFamily.ANNUAL)
        assert not C.growth_periods_comparable(PeriodFamily.UNKNOWN, PeriodFamily.UNKNOWN)

    def test_same_family_growth_is_comparable(self):
        for family in (PeriodFamily.QUARTER, PeriodFamily.YTD, PeriodFamily.TTM,
                       PeriodFamily.ANNUAL, PeriodFamily.INSTANT):
            assert C.growth_periods_comparable(family, family), family

    def test_only_ttm_and_annual_may_be_a_multiple_denominator(self):
        assert C.denominator_period_valid(PeriodFamily.TTM)
        assert C.denominator_period_valid(PeriodFamily.ANNUAL)
        for family in (PeriodFamily.QUARTER, PeriodFamily.YTD, PeriodFamily.INSTANT,
                       PeriodFamily.UNKNOWN):
            assert not C.denominator_period_valid(family), family

    def test_annualizing_a_partial_period_is_forbidden_in_writing(self):
        for phrase in ("Interpolating a missing quarter", "annualizing a partial year",
                       "multiplying a quarter by four"):
            assert phrase in C.TTM_CONSTRUCTION_REQUIRED, phrase


# --- Negative and invalid denominators (brief §11) ---------------------------------------------

class TestDenominators:
    @pytest.mark.parametrize("value", [0.0, -1.0, -1_000_000.0])
    def test_a_non_positive_denominator_is_not_applicable(self, value):
        """A P/E of -14 reads like a cheap stock and means a loss."""
        assert not C.denominator_applicable(value, PeriodFamily.TTM)

    def test_a_missing_denominator_is_not_applicable(self):
        assert not C.denominator_applicable(None, PeriodFamily.TTM)

    def test_a_positive_denominator_on_a_partial_period_is_still_not_applicable(self):
        assert not C.denominator_applicable(100.0, PeriodFamily.YTD)
        assert C.denominator_applicable(100.0, PeriodFamily.TTM)

    def test_every_method_spec_names_what_forbids_it(self):
        for spec in C.METHOD_SPECS:
            assert spec.forbidden_when, spec.method
            assert spec.appropriate_when, spec.method
            assert spec.required_fields, spec.method


# --- Market cap and EV (brief §8, §9) ----------------------------------------------------------

class TestMarketCapAndEnterpriseValue:
    def test_the_market_cap_primitive_is_h0_5s_and_is_not_restated_loosely(self):
        assert "unadjusted regular-session close" in C.MARKET_CAP_PRIMITIVE
        assert "raw PIT shares" in C.MARKET_CAP_PRIMITIVE
        assert "fallback is forbidden" in C.MARKET_CAP_PRIMITIVE

    def test_the_h0_5_primitive_still_fails_closed_on_multi_class(self):
        """The capability is real. The test is here because the DETECTOR is not."""
        from app.backtest.strategy_h0.h0_5 import resolve_pit_shares
        assert resolve_pit_shares([], date(2026, 6, 30), [],
                                  multiple_share_classes=True).fact is None

    def test_multi_class_detection_is_recorded_as_unwired_not_as_coverage(self):
        """The flag's only True caller anywhere is a test, so D5 may not claim multi-class safety."""
        import inspect

        from app.backtest.strategy_h0 import h0_5
        assert "multiple_share_classes" in inspect.getsource(h0_5)
        assert "nothing in the production pipeline ever sets that flag" in \
            C.MULTI_CLASS_IS_UNDETECTED
        assert "its only True caller is a test" in C.MULTI_CLASS_IS_UNDETECTED
        assert "may not treat the capability as coverage" in C.MULTI_CLASS_IS_UNDETECTED
        assert "UNDETECTED" in next(
            f.note for f in C.VALUATION_FIELDS if f.name == "shares_outstanding")
        assert MethodId.PB in dict(
            (name, methods) for name, _, methods in C.BLOCKING_DATA_REPAIRS
        )["multi-class detection"], "it conditions every method, including the applicable one"

    def test_enterprise_value_is_unknown_because_total_debt_is_not_total_debt(self):
        assert C.DEBT_COMPOSITION_CONTRACT == "NOT_IMPLEMENTED"
        ev_methods = [s for s in C.METHOD_SPECS if s.method.value.startswith("EV/")]
        assert len(ev_methods) == 3
        for spec in ev_methods:
            assert spec.v1_status is MethodStatus.BLOCKED_DATA, spec.method
            assert "total_debt composition" in spec.blocker, spec.method

    def test_the_resolver_picks_one_debt_tag_and_never_sums(self):
        """The mechanism, on a fixture rather than on a claim: a filer reporting the current
        portion, the noncurrent portion and the total resolves to the CURRENT portion, with status
        OK and no ambiguity flag."""
        from datetime import datetime, timezone

        from app.backtest.strategy_h0.facts import CanonicalFact, FactStatus, resolve_fact

        def fact(tag: str, value: float) -> CanonicalFact:
            return CanonicalFact(
                field="total_debt", taxonomy="us-gaap", tag=tag, unit="USD", value=value,
                start=None, end=date(2026, 6, 30), filed=date(2026, 7, 20),
                accepted_at=datetime(2026, 7, 20, 12, tzinfo=timezone.utc),
                accession="0000000000-26-000001", form="10-Q",
                fiscal_year=2026, fiscal_period="Q2", frame=None)

        resolved = resolve_fact(
            [fact("LongTermDebtCurrent", 50_000_000),
             fact("LongTermDebtNoncurrent", 900_000_000),
             fact("LongTermDebt", 950_000_000)],
            "total_debt", datetime(2026, 8, 1, tzinfo=timezone.utc))
        assert resolved.status is FactStatus.OK, "silently OK is what makes this dangerous"
        assert resolved.fact.tag == "LongTermDebtCurrent"
        assert resolved.fact.value == 50_000_000
        assert FIELD_SPECS["total_debt"].tags[0].endswith("Current")

    def test_no_short_term_borrowing_tag_is_canonical(self):
        canonical = set(FIELD_SPECS["total_debt"].tags)
        for absent in ("ShortTermBorrowings", "NotesPayableCurrent", "DebtCurrent",
                       "CommercialPaper", "LineOfCreditFacilityAmountOutstanding",
                       "FinanceLeaseLiabilityCurrent", "FinanceLeaseLiabilityNoncurrent"):
            assert absent not in canonical, absent
        assert canonical == {"LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent",
                             "LongTermDebtNoncurrent", "LongTermDebt"}

    def test_balance_sheet_staleness_is_recorded_as_unbounded(self):
        assert "no staleness bound" in C.DEBT_STALENESS_UNBOUNDED
        assert "2,463 days" in C.DEBT_STALENESS_UNBOUNDED, "COLL, measured"

    def test_shares_have_a_staleness_bound_and_balance_sheet_fields_do_not(self):
        """The asymmetry is the finding: H0.5 bounded shares and nothing bounded the rest."""
        from app.backtest.strategy_h0.h0_5 import MAX_SHARES_STALENESS_DAYS
        assert MAX_SHARES_STALENESS_DAYS == 135
        assert str(MAX_SHARES_STALENESS_DAYS) in next(
            f.note for f in C.VALUATION_FIELDS if f.name == "shares_outstanding")


# --- Field contract (brief §6) -----------------------------------------------------------------

class TestFieldContract:
    def test_every_canonical_field_is_accounted_for(self):
        named = {f.name for f in C.VALUATION_FIELDS if f.source.startswith(C._SEC)}
        assert named >= set(FIELD_SPECS), sorted(set(FIELD_SPECS) - named)

    def test_every_field_declares_its_missing_behaviour(self):
        for field in C.VALUATION_FIELDS:
            assert field.missing_behavior, field.name
            assert field.unit and field.period_type and field.source, field.name

    def test_no_field_falls_back_to_a_current_value(self):
        for field in C.VALUATION_FIELDS:
            assert "fallback" not in field.missing_behavior or "no current-value fallback" in \
                field.missing_behavior, field.name

    def test_the_income_statement_is_blocked_and_the_balance_sheet_is_not(self):
        """The measured asymmetry that decides what D5 v1 can be."""
        by_name = {f.name: f for f in C.VALUATION_FIELDS}
        for name in ("revenue", "gross_profit", "operating_income", "net_income", "eps_diluted"):
            assert by_name[name].availability is FieldAvailability.BLOCKED_DURATION_AMBIGUITY, name
        for name in ("cash", "assets", "equity", "shares_outstanding", "operating_cash_flow",
                     "capex"):
            assert by_name[name].availability is FieldAvailability.AVAILABLE, name
        assert by_name["total_debt"].availability is FieldAvailability.BLOCKED_COMPOSITION

    def test_d_and_a_and_interest_are_recorded_as_reported_but_not_canonical(self):
        by_name = {f.name: f for f in C.VALUATION_FIELDS}
        for name in ("depreciation_amortization", "interest_expense"):
            assert by_name[name].availability is FieldAvailability.NOT_CANONICAL, name
            assert name not in FIELD_SPECS
        assert "EBITDA's blocker is operating_income, not D&A" in \
            by_name["depreciation_amortization"].note

    def test_derived_fields_are_never_model_supplied(self):
        derived = {f.name for f in C.VALUATION_FIELDS
                   if f.availability is FieldAvailability.DERIVED}
        assert derived == {"free_cash_flow", "net_debt", "market_cap", "enterprise_value"}
        for name in derived:
            assert name in " ".join(C.CODE_OWNED) or name.replace("_", " ") in \
                " ".join(C.CODE_OWNED), name


# --- Methods (brief §10, §12) ------------------------------------------------------------------

class TestMethods:
    def test_exactly_one_method_is_applicable_today_and_it_is_measured_not_assumed(self):
        assert C.v1_applicable_methods() == (MethodId.PB,)
        assert C.ONLY_METHOD_APPLICABLE_TODAY is MethodId.PB

    def test_one_applicable_method_cannot_reach_complete(self):
        """So 'P/B works' is not 'D5 can value these issuers'."""
        assert C.MIN_METHODS_FOR_COMPLETE == 2
        assert C.valuation_completeness(
            market_cap_valid=True, applicable_methods=1,
            scenarios_have_provenance=True) is ValuationCompleteness.PARTIAL

    def test_forward_methods_are_blocked_on_the_absent_provider_not_on_data(self):
        spec = next(s for s in C.METHOD_SPECS if s.method is MethodId.FORWARD_ANY)
        assert spec.v1_status is MethodStatus.BLOCKED_NO_PROVIDER
        assert C.method_status(spec, frozenset({"pit_consensus"})) is \
            MethodStatus.BLOCKED_NO_PROVIDER, (
            "a field named pit_consensus appearing from somewhere must not unblock it"
        )

    def test_method_status_requires_every_field(self):
        spec = next(s for s in C.METHOD_SPECS if s.method is MethodId.PE)
        assert C.method_status(spec, frozenset({"market_cap"})) is MethodStatus.BLOCKED_DATA
        assert C.method_status(spec, frozenset({"market_cap", "net_income"})) is \
            MethodStatus.APPLICABLE

    def test_there_is_no_default_multiple(self):
        assert "no default multiple" in C.NO_UNIVERSAL_MULTIPLE
        assert "PER 20x" in C.NO_UNIVERSAL_MULTIPLE
        assert "OBSERVED value" in C.NO_UNIVERSAL_MULTIPLE
        assert "it never names the number" in C.NO_UNIVERSAL_MULTIPLE

    def test_d5_is_not_a_ranking_engine(self):
        assert "not a stock screen" in C.VALUATION_IS_NOT_A_RANKING_ENGINE
        assert "H-PV1" in C.VALUATION_IS_NOT_A_RANKING_ENGINE

    def test_the_blocking_repairs_are_ordered_by_what_they_unblock(self):
        counts = [len(methods) for _, _, methods in C.BLOCKING_DATA_REPAIRS[:4]]
        assert counts == sorted(counts, reverse=True), counts
        assert C.BLOCKING_DATA_REPAIRS[0][0] == "duration-family narrowing"
        assert len(C.BLOCKING_DATA_REPAIRS[0][2]) == 8


# --- Peers and historical range (brief §13, §14) -----------------------------------------------

class TestPeersAndHistory:
    def test_a_sector_code_is_not_peer_eligibility(self):
        assert "not peer eligibility" in C.PEER_SECTOR_CODE_INSUFFICIENT
        assert "argued, not assumed" in C.PEER_SECTOR_CODE_INSUFFICIENT
        assert len(C.PEER_ELIGIBILITY) >= 5
        assert C.PEER_MIN_ELIGIBLE == 3

    def test_peer_multiples_are_code_owned_even_when_the_model_proposes_the_peer(self):
        assert "may PROPOSE peers" in C.PEER_SECTOR_CODE_INSUFFICIENT
        assert "peer multiples" in " ".join(C.CODE_OWNED)

    def test_the_historical_range_is_named_for_the_two_years_it_actually_has(self):
        assert C.HISTORICAL_RANGE_LABEL == "RECENT_2Y_RANGE"
        assert C.HISTORICAL_PANEL_SESSIONS == 501
        assert C.HISTORICAL_PANEL_FIRST_SESSION == date(2024, 9, 17)
        assert C.HISTORICAL_PANEL_LAST_SESSION == date(2026, 9, 16)
        span_days = (C.HISTORICAL_PANEL_LAST_SESSION - C.HISTORICAL_PANEL_FIRST_SESSION).days
        assert 720 <= span_days <= 740, span_days

    def test_a_long_term_label_is_forbidden(self):
        import inspect
        source = inspect.getsource(C)
        assert "`LONG_TERM_HISTORICAL_RANGE` is a forbidden label in v1" in source
        assert "one macro regime and at most eight reported quarters" in source, (
            "the reason has to be the window's content, not modesty about it"
        )

    def test_a_decision_time_past_the_panel_has_no_market_cap(self):
        assert "UNKNOWN rather than the last available close carried forward" in \
            C.PRICE_PANEL_STALENESS


# --- Guidance (brief §16) ----------------------------------------------------------------------

class TestGuidance:
    def test_guidance_reuses_d4_brs_operand_completeness_unchanged(self):
        from app.backtest.strategy_h_v2.expectation.comparability import REQUIRED_OPERANDS
        assert "REQUIRED_OPERANDS" in C.GUIDANCE_OPERAND_CONTRACT
        assert REQUIRED_OPERANDS, "the D4-BR contract this one points at must still exist"

    def test_incomplete_guidance_may_not_be_interpolated(self):
        assert "may not be completed" in C.GUIDANCE_OPERAND_CONTRACT
        assert "may not be interpolated" in C.GUIDANCE_OPERAND_CONTRACT
        assert "UNKNOWN is a valid scenario state" in C.GUIDANCE_OPERAND_CONTRACT

    def test_guidance_is_not_a_consensus_substitute(self):
        assert "not the market's expectation" in C.GUIDANCE_IS_NOT_CONSENSUS


# --- Scenarios, fair value, TP1/TP2, upside (brief §17-§20) ------------------------------------

class TestScenariosAndTargets:
    def test_three_scenarios_each_need_input_provenance(self):
        assert [s.value for s in Scenario] == ["BEAR", "BASE", "BULL"]
        assert len(C.SCENARIO_INPUT_PROVENANCE) == 3
        assert "the model may not supply one" in C.SCENARIO_ASSUMPTIONS_ARE_NOT_FREE

    def test_fair_value_is_a_range_and_the_arithmetic_is_code_owned(self):
        assert "lower / base / upper" in C.FAIR_VALUE_IS_A_RANGE
        assert "never a single point" in C.FAIR_VALUE_IS_A_RANGE
        assert "valuation range arithmetic" in C.CODE_OWNED

    def test_tp1_and_tp2_are_defined_here_because_nothing_defined_them_before(self):
        assert "TP1" in C.UNDEFINED_BEFORE_D5 and "TP2" in C.UNDEFINED_BEFORE_D5
        assert "not a reconstruction of an earlier intent" in C.TP_IS_DEFINED_HERE_NOT_RESTORED
        names = [d.name for d in C.TP_DEFINITIONS]
        assert names == ["TP1", "TP2", "BEAR_ANCHOR"]

    def test_each_target_price_is_a_scenario_metric_times_an_observed_multiple(self):
        for definition in C.TP_DEFINITIONS:
            assert " x " in definition.arithmetic, definition.name
            assert definition.provenance_required, definition.name
        tp1 = next(d for d in C.TP_DEFINITIONS if d.name == "TP1")
        tp2 = next(d for d in C.TP_DEFINITIONS if d.name == "TP2")
        assert tp1.scenario is Scenario.BASE and tp2.scenario is Scenario.BULL
        assert "TP2 is not TP1 plus a margin" in tp2.definition

    def test_tp1_is_tied_to_d0s_base_case_language(self):
        tp1 = next(d for d in C.TP_DEFINITIONS if d.name == "TP1")
        assert "supports upside under at least the Base case" in tp1.definition
        assert any("supports upside under at least the Base case" in o
                   for o in C.D0_INHERITED_OBLIGATIONS)

    def test_no_target_price_may_be_fitted_to_realized_returns(self):
        assert "no realized-return, forward-return or price-outcome input" in C.NO_RETURN_FITTING
        assert "curve fit" in C.NO_RETURN_FITTING
        from app.backtest.strategy_h_v2.research.validation_v2 import decision_leakage_findings
        assert decision_leakage_findings(C.NO_RETURN_FITTING) == (), (
            "the prohibition must state its own denial in every clause that names the thing"
        )

    def test_upside_is_three_numbers_and_not_a_decision(self):
        assert len(C.UPSIDE_MEASURES) == 3
        for measure in C.UPSIDE_MEASURES:
            assert "current_unadjusted_close" in measure
        for banned in ("buy", "sell", "approve", "watch", "reject", "position size"):
            assert banned in C.UPSIDE_IS_NOT_A_DECISION, banned
        assert "D6 does not exist" in C.UPSIDE_IS_NOT_A_DECISION


# --- D4 integration and preserved limitations (brief §21, §22) ---------------------------------

class TestD4Integration:
    def test_d5_does_not_overwrite_d4(self):
        joined = " ".join(C.D4_INTEGRATION_RULES)
        assert "never rewrites it" in joined
        assert "does not promote a NEGATIVE expectation gap" in joined
        assert "does not demote a POSITIVE gap" in joined

    def test_the_wide_positive_valuation_conjunct_is_d5s_to_evaluate(self):
        from app.backtest.strategy_h_v2.expectation.gap_contract import (
            WIDE_POSITIVE_VALUATION_CONJUNCT_DEFERRED,
        )
        assert "UNEVALUATED" in WIDE_POSITIVE_VALUATION_CONJUNCT_DEFERRED
        assert any("WIDE_POSITIVE" in o for o in C.D0_INHERITED_OBLIGATIONS)

    def test_c1_and_r3_are_carried_forward_unresolved(self):
        assert C.D4_LIMITATIONS_CARRIED_FORWARD["C1"].startswith("NOT_EVALUATED")
        assert "three consecutive graded runs" in C.D4_LIMITATIONS_CARRIED_FORWARD["C1"]
        assert C.D4_LIMITATIONS_CARRIED_FORWARD["R3"].startswith("OBSERVED / DEFERRED")
        assert "Unchanged by D5" in C.D4_LIMITATIONS_CARRIED_FORWARD["R3"]

    def test_the_d4_banned_field_list_is_unchanged_and_is_a_layer_boundary(self):
        from app.backtest.strategy_h_v2.expectation.analysis_schema import BANNED_D4_FIELD_NAMES
        for name in ("valuation", "valuation_view", "fair_value", "upside", "tp1", "tp2",
                     "entry", "exit", "position_size"):
            assert name in BANNED_D4_FIELD_NAMES, name
        assert "layer boundary" in C.D4_BANNED_FIELDS_ARE_A_LAYER_BOUNDARY

    def test_d5_inherits_the_repaired_decision_leakage_authority(self):
        from app.backtest.strategy_h_v2.research.validation_v2 import decision_leakage_findings
        assert "E7R" in C.D4_LIMITATIONS_CARRIED_FORWARD
        assert decision_leakage_findings("Buy the stock."), "the authority must still be live"


# --- Completeness and refusals (brief §23, §28) ------------------------------------------------

class TestCompleteness:
    def test_no_safely_computable_method_is_not_ready(self):
        assert C.valuation_completeness(
            market_cap_valid=True, applicable_methods=0,
            scenarios_have_provenance=True) is ValuationCompleteness.NOT_READY

    def test_an_invalid_market_cap_is_not_ready_however_many_methods_there_are(self):
        assert C.valuation_completeness(
            market_cap_valid=False, applicable_methods=5,
            scenarios_have_provenance=True) is ValuationCompleteness.NOT_READY

    def test_scenarios_without_provenance_cap_the_state_at_partial(self):
        assert C.valuation_completeness(
            market_cap_valid=True, applicable_methods=3,
            scenarios_have_provenance=False) is ValuationCompleteness.PARTIAL

    def test_not_ready_is_an_output_and_not_a_failure(self):
        assert "valid, complete D5 output" in C.NOT_READY_IS_AN_OUTPUT
        assert "never asked to produce a target price" in C.NOT_READY_IS_AN_OUTPUT
        assert "only an empty output is a failure" in C.NOT_READY_IS_AN_OUTPUT

    def test_the_gate_list_is_coarse_and_says_so(self):
        assert len(C.V_GATES) == 8
        assert [g.gate for g in C.V_GATES] == [f"V{i}" for i in range(1, 9)]
        assert "does not repeat" in C.GATE_PHILOSOPHY
        assert "reported as a limitation, not converted into a gate" in C.GATE_PHILOSOPHY


# --- Responsibility split (brief §5) -----------------------------------------------------------

class TestResponsibilitySplit:
    @pytest.mark.parametrize("item", [
        "market cap", "enterprise value", "multiple", "growth rate", "margin", "net debt",
        "DCF arithmetic", "valuation range arithmetic", "target-price arithmetic",
    ])
    def test_every_arithmetic_item_the_brief_names_is_code_owned(self, item):
        assert any(item in owned for owned in C.CODE_OWNED), item

    def test_the_ai_owns_interpretation_and_never_a_number(self):
        joined = " ".join(C.AI_OWNED)
        assert "never their numbers" in joined
        assert "where in an OBSERVED range to sit" in joined
        assert "rejected rather than checked" in C.AI_MUST_NOT_COMPUTE

    def test_no_arithmetic_item_appears_on_both_lists(self):
        for owned in C.CODE_OWNED:
            assert owned not in C.AI_OWNED, owned


# --- D5-D1 pilot (brief §24, §26), proposed only -----------------------------------------------

class TestPilotProposal:
    def test_the_sample_regenerates_from_the_universe_rather_than_from_its_own_hash(self):
        regenerated = C.regenerate_d5_d1_sample()
        assert regenerated == C.D5_D1_SAMPLE
        assert C.d5_d1_checksum(regenerated) == C.D5_D1_CHECKSUM

    def test_the_sample_is_twelve_distinct_issuers(self):
        assert len(C.D5_D1_SAMPLE) == C.D5_D1_N == 12
        assert len({t for t, _ in C.D5_D1_SAMPLE}) == 12
        assert len({c for _, c in C.D5_D1_SAMPLE}) == 12

    def test_the_sample_is_performance_blind(self):
        """Not selected by any D3 or D4 outcome, and not chosen to avoid them either: the sample is
        a seeded hash over the whole D2.1 package universe."""
        import inspect
        source = inspect.getsource(C.regenerate_d5_d1_sample)
        assert "No exclusion list" in source
        assert "performance-blind" in source
        for forbidden in ("expectation_gap", "final_status", "verdict", "d4_final",
                          "gateaudit", "upside"):
            assert forbidden not in source, forbidden

    def test_the_pilot_forbids_returns_and_published_valuations(self):
        joined = " ".join(C.D5_D1_FORBIDDEN)
        assert "no realized return and no forward return" in joined
        assert "no issuer is selected or excluded by its D3 or D4 outcome" in joined
        assert "no valuation of any issuer is published" in joined
        assert "costs $0" in joined

    def test_each_prohibition_states_its_own_denial(self):
        """So the list survives being quoted, and so it does not trip the project's own detector -
        which is the D4-BR defect, restated as a documentation rule."""
        from app.backtest.strategy_h_v2.research.validation_v2 import decision_leakage_findings
        for item in C.D5_D1_FORBIDDEN:
            assert item.lower().startswith("no "), item
            assert decision_leakage_findings(item) == (), item

    def test_the_pilot_measures_the_things_this_contract_could_not_measure(self):
        joined = " ".join(C.D5_D1_MEASURES)
        for subject in ("duration-family distribution", "TTM constructibility",
                        "debt-composition exposure", "multi-class detection rate",
                        "cash-versus-debt period alignment"):
            assert subject in joined, subject


def _code_only(source: str) -> str:
    """Source with docstrings and comments removed, so a module that NAMES a banned call in order to
    promise it does not make one is not read as making it."""
    import io
    import tokenize

    kept: list[str] = []
    previous = tokenize.INDENT
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            continue
        if token.type == tokenize.STRING and previous in (
                tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.NL):
            continue
        kept.append(token.string)
        if token.type not in (tokenize.NL, tokenize.NEWLINE):
            previous = token.type
        else:
            previous = token.type
    return " ".join(kept)


# --- The prohibitions this step is under (brief §1, §25, §33) ----------------------------------

class TestNothingIsExecuted:
    def test_the_contract_executes_no_valuation(self):
        assert C.contract_summary()["executes_valuation"] is False

    def test_the_valuation_package_makes_no_model_call(self):
        import inspect
        import pkgutil

        from app.backtest.strategy_h_v2 import valuation

        for module in pkgutil.iter_modules(valuation.__path__):
            mod = __import__(f"app.backtest.strategy_h_v2.valuation.{module.name}",
                             fromlist=["x"])
            source = _code_only(inspect.getsource(mod))
            for banned in ("call_opus", "subprocess", "CLAUDE_CODE_EXECPATH", "max-budget-usd",
                           "anthropic", "requests.", "urllib"):
                assert banned not in source, f"{module.name}: {banned}"

    def test_the_package_has_no_realized_return_input(self):
        import inspect
        import pkgutil

        from app.backtest.strategy_h_v2 import valuation

        for module in pkgutil.iter_modules(valuation.__path__):
            mod = __import__(f"app.backtest.strategy_h_v2.valuation.{module.name}",
                             fromlist=["x"])
            source = _code_only(inspect.getsource(mod))
            for banned in ("forward_return", "realized_return", "future_return", "fwd_ret"):
                assert banned not in source, f"{module.name}: {banned}"

    def test_the_contract_computes_no_target_price(self):
        """TP1/TP2 are DEFINED here as formulas in prose. No function evaluates one, and none may
        be added to this module: a D5-D0 that could produce a target price would have produced one."""
        import inspect
        source = inspect.getsource(C)
        assert "def tp1" not in source and "def tp2" not in source
        assert "def fair_value" not in source
        assert "def target_price" not in source
        for definition in C.TP_DEFINITIONS:
            assert not hasattr(C, definition.name.lower()), definition.name

    def test_no_decision_vocabulary_leaks_into_the_contract_text(self):
        """D5's own prose is held to the repaired E7 authority, with one scoped exception that is
        itself a finding rather than a concession.

        E7R's decision-token family matches an uppercase APPROVE / WATCH / REJECT, case-sensitively,
        because that is the D6 enum leaking into an output. This contract QUOTES that enum when it
        restates D0's APPROVE preconditions, so the family fires on documentation. The gate is not
        weakened to accommodate it: E7 grades model OUTPUT, and a contract constant is not one.
        What this test pins is that the only finding anywhere in the contract text is that family,
        and that every other family is silent.
        """
        from app.backtest.strategy_h_v2.research.validation_v2 import decision_leakage_findings

        def strings():
            """Prose only. A bare identifier is not prose, and the project already separates the
            two: `AUDIT_BANNED_FIELDS` checks field NAMES while the classifier checks sentences.
            `UNDEFINED_BEFORE_D5` is a list of banned identifiers including "exit", which is an
            imperative when read as a clause and a field name when read as what it is."""
            for attr in sorted(dir(C)):
                if not attr.isupper():
                    continue
                value = getattr(C, attr)
                if isinstance(value, str):
                    candidates = [value]
                elif isinstance(value, (tuple, list)):
                    candidates = [v for v in value if isinstance(v, str)]
                elif isinstance(value, dict):
                    candidates = [v for v in value.values() if isinstance(v, str)]
                else:
                    candidates = []
                for item in candidates:
                    if " " in item.strip():
                        yield attr, item

        quoting_the_enum = set()
        for attr, text in strings():
            findings = decision_leakage_findings(text)
            if not findings:
                continue
            families = {f.term for f in findings}
            assert families == {"decision token"}, f"{attr}: {families} in {text[:120]}"
            quoting_the_enum.add(attr)

        assert quoting_the_enum == {"D0_INHERITED_OBLIGATIONS"}, (
            "only the verbatim quotes of D0's APPROVE/WATCH/REJECT enum may fire; rewording a "
            f"quotation to satisfy a detector would misquote D0. Fired: {quoting_the_enum}"
        )

    def test_banned_identifiers_are_listed_as_names_not_as_prose(self):
        """`UNDEFINED_BEFORE_D5` holds identifiers, and "exit" alone reads as an imperative. It is
        listed as a name because that is what it is - the same separation `AUDIT_BANNED_FIELDS`
        already makes between a field name and a sentence."""
        from app.backtest.strategy_h_v2.research.validation_v2 import decision_leakage_findings
        assert all(" " not in name for name in C.UNDEFINED_BEFORE_D5)
        assert decision_leakage_findings("exit"), "as a clause it would be an instruction"

    def test_the_contract_text_contains_no_investment_action_or_target_price_language(self):
        """The families that would actually matter, asserted silent over the same prose."""
        from app.backtest.strategy_h_v2.research.validation_v2 import (
            LanguageVerdict,
            classify_investment_language,
        )

        for attr in sorted(dir(C)):
            if not attr.isupper():
                continue
            value = getattr(C, attr)
            if isinstance(value, str):
                texts = [value]
            elif isinstance(value, (tuple, list)):
                texts = [v for v in value if isinstance(v, str)]
            elif isinstance(value, dict):
                texts = [v for v in value.values() if isinstance(v, str)]
            else:
                texts = []
            texts = [t for t in texts if " " in t.strip()]
            for text in texts:
                for match in classify_investment_language(text):
                    if match.verdict is not LanguageVerdict.VIOLATION:
                        continue
                    assert match.term == "decision token", f"{attr}: {match.term}"
