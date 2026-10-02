"""H-V2-D5-P1: debt composition, instant freshness, net debt, enterprise value and provenance.

The data-backed tests read `data/runtime/`, which is gitignored, and skip when it is absent. The pure
tests do not skip: the contract they assert is code, not data.

Every fixture below is the shape of a real filing in the measured corpus, named in the test, so a
test that stops holding points at the issuer whose data it was built from.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.backtest.strategy_h0.facts import FIELD_SPECS, CanonicalFact
from app.backtest.strategy_h0.h0_5 import MAX_SHARES_STALENESS_DAYS, SharesResolution
from app.backtest.strategy_h_v2.valuation.capital_structure import (
    CAPITAL_STRUCTURE_FIELD_SPECS,
    CASH_EXCLUDED_FROM_EV,
    CASH_FOR_EV_FIELD,
    CASH_FOR_EV_TAGS,
    CURRENT_DEBT_TAGS,
    DEBT_LEASE_BUNDLED_FIELD,
    DEBT_SLOT_FIELDS,
    DEBT_SLOT_TAGS,
    DEBT_TAGS_THAT_ARE_NOT_A_BALANCE,
    EV_DEBT_SCOPE,
    LEASE_BUNDLED_DEBT_TAGS,
    LEASE_LIABILITY_TAGS_OUT_OF_SCOPE,
    LEASE_TREATMENT,
    MAX_INSTANT_STALENESS_DAYS,
    NEVER_CONSTRUCTED,
    NONCURRENT_DEBT_TAGS,
    REPORTED_TOTAL_DEBT_TAGS,
    TAG_PRIORITY_IS_A_PREFERENCE_FOR,
    CapitalStructureStatus,
    DebtMethod,
    DebtSlot,
    DebtStatus,
    InstantStatus,
    compose_total_debt,
    enterprise_value,
    method_feasibility,
    net_debt_from,
    resolve_cash_for_ev,
    resolve_net_debt,
    resolve_valuation_instant,
)
from app.backtest.strategy_h_v2.valuation.fundamental_fields import VALUATION_FIELD_SPECS
from app.backtest.strategy_h_v2.valuation.market_cap_gate import (
    MarketCapResolution,
    MarketCapStatus,
    ShareClassState,
    valuation_market_cap,
)

from conftest import utc

DECISION_TIME = utc(2026, 9, 28)
DECISION_DATE = date(2026, 9, 16)
#: The balance-sheet date nine of the ten measured issuers report, 78 days before the decision date.
Q2 = date(2026, 6, 30)
#: An earlier balance-sheet date that is still inside the staleness bound, so a test about choosing
#: between two dates measures the choice rather than the bound.
EARLIER_Q = date(2026, 5, 31)


def instant(field: str, tag: str, value: float, end: date, *, accession: str = "ACC-1",
            form: str = "10-Q", accepted_at: datetime | None = None,
            unit: str = "USD") -> CanonicalFact:
    """One balance-sheet fact, with its tag stated rather than looked up: the tag is the subject."""
    return CanonicalFact(
        field=field, taxonomy="us-gaap", tag=tag, unit=unit, value=value, start=None, end=end,
        filed=end + timedelta(days=30),
        accepted_at=accepted_at or datetime.combine(end + timedelta(days=30), datetime.min.time(),
                                                    tzinfo=timezone.utc),
        accession=accession, form=form, fiscal_year=end.year, fiscal_period="Q2", frame=None)


def cash(value: float, end: date = Q2, **kw) -> CanonicalFact:
    return instant(CASH_FOR_EV_FIELD, CASH_FOR_EV_TAGS[0], value, end, **kw)


def debt(slot: DebtSlot, tag: str, value: float, end: date = Q2, **kw) -> CanonicalFact:
    assert tag in DEBT_SLOT_TAGS[slot], f"{tag} is not a {slot.value} tag"
    return instant(DEBT_SLOT_FIELDS[slot], tag, value, end, **kw)


def ok_market_cap(value: float) -> MarketCapResolution:
    """A market cap that passed the share-class gate. No issuer reaches this state from stored data,
    which is why the enterprise-value arithmetic has to be tested from a fixture."""
    return MarketCapResolution(MarketCapStatus.OK, value, "fixture: single class",
                               SharesResolution(None, "fixture"))


# -------------------------------------------------------------------------------------------------
# C. The instant freshness contract
# -------------------------------------------------------------------------------------------------

def test_the_instant_staleness_bound_is_the_one_h0_5_already_validated():
    """A second number for the same question is a second thing to keep in agreement."""
    assert MAX_INSTANT_STALENESS_DAYS == MAX_SHARES_STALENESS_DAYS == 135


def test_recent_cash_resolves_with_its_age_recorded():
    result = resolve_cash_for_ev([cash(8_717_000)], DECISION_TIME, DECISION_DATE)
    assert result.status is InstantStatus.OK
    assert result.value == 8_717_000
    assert result.period_end == Q2
    assert result.age_days == (DECISION_DATE - Q2).days == 78


def test_stale_cash_is_refused_and_names_its_age():
    old = DECISION_DATE - timedelta(days=MAX_INSTANT_STALENESS_DAYS + 1)
    result = resolve_cash_for_ev([cash(1_000_000, end=old)], DECISION_TIME, DECISION_DATE)
    assert result.status is InstantStatus.STALE
    assert result.value is None
    assert str(MAX_INSTANT_STALENESS_DAYS) in result.reason and str(old) in result.reason


def test_cash_exactly_at_the_bound_is_still_fresh():
    """The bound is `> bound` in H0.5 and is `> bound` here; an off-by-one would silently move it."""
    edge = DECISION_DATE - timedelta(days=MAX_INSTANT_STALENESS_DAYS)
    assert resolve_cash_for_ev([cash(5.0, end=edge)], DECISION_TIME, DECISION_DATE).ok


def test_a_balance_sheet_dated_after_the_decision_date_is_not_used():
    future = DECISION_DATE + timedelta(days=10)
    result = resolve_cash_for_ev([cash(1.0, end=future, accepted_at=utc(2026, 9, 1))],
                                 DECISION_TIME, DECISION_DATE)
    assert result.status is InstantStatus.MISSING


def test_a_fact_accepted_after_the_decision_time_cannot_enter():
    late = cash(8_717_000, accepted_at=DECISION_TIME + timedelta(seconds=1))
    assert resolve_cash_for_ev([late], DECISION_TIME, DECISION_DATE).status is InstantStatus.MISSING


# -------------------------------------------------------------------------------------------------
# D. The cash contract
# -------------------------------------------------------------------------------------------------

def test_cash_for_ev_is_one_tag_and_the_excluded_ones_are_named_with_reasons():
    assert CASH_FOR_EV_TAGS == ("CashAndCashEquivalentsAtCarryingValue",)
    excluded = {tag for tag, _ in CASH_EXCLUDED_FROM_EV}
    assert "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents" in excluded
    assert not excluded & set(CASH_FOR_EV_TAGS)
    assert all(reason.strip() for _, reason in CASH_EXCLUDED_FROM_EV)


def test_the_restricted_inclusive_tag_is_not_a_fallback_for_the_unrestricted_one():
    """COLL's shape: 129,467k unrestricted and 150,377k restricted-inclusive at 2026-06-30. When only
    the restricted-inclusive tag exists, cash is MISSING rather than overstated."""
    restricted_only = instant(
        CASH_FOR_EV_FIELD, "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        150_377_000, Q2)
    result = resolve_cash_for_ev([restricted_only], DECISION_TIME, DECISION_DATE)
    assert result.status is InstantStatus.MISSING
    assert result.value is None


# -------------------------------------------------------------------------------------------------
# F/G. Debt composition and double counting
# -------------------------------------------------------------------------------------------------

def test_a_reported_total_is_used_as_reported():
    result = compose_total_debt([debt(DebtSlot.REPORTED_TOTAL, "LongTermDebt", 2_239_000_000)],
                                DECISION_TIME, DECISION_DATE)
    assert result.status is DebtStatus.OK
    assert result.method is DebtMethod.REPORTED_TOTAL
    assert result.value == 2_239_000_000
    assert [slot for slot, _ in result.components] == [DebtSlot.REPORTED_TOTAL]


def test_a_reported_total_prevents_the_double_count_of_its_own_components():
    """AEYE at 2026-06-30: total 16,418k, current 850k, noncurrent 15,568k, and 850 + 15,568 = 16,418.
    Summing all three is the defect; the hierarchy makes it unreachable."""
    facts = [debt(DebtSlot.REPORTED_TOTAL, "LongTermDebt", 16_418_000),
             debt(DebtSlot.CURRENT, "LongTermDebtCurrent", 850_000),
             debt(DebtSlot.NONCURRENT, "LongTermDebtNoncurrent", 15_568_000)]
    result = compose_total_debt(facts, DECISION_TIME, DECISION_DATE)
    assert result.value == 16_418_000
    assert result.value != 850_000 + 15_568_000 + 16_418_000
    assert result.method is DebtMethod.REPORTED_TOTAL
    assert len(result.components) == 1
    assert result.reported_total_minus_components == 0.0


def test_the_reported_total_versus_components_difference_is_recorded_not_gated():
    """A filer whose total does not equal its parts still gets its total, with the gap on the record."""
    facts = [debt(DebtSlot.REPORTED_TOTAL, "LongTermDebt", 1_000.0),
             debt(DebtSlot.CURRENT, "LongTermDebtCurrent", 100.0),
             debt(DebtSlot.NONCURRENT, "LongTermDebtNoncurrent", 850.0)]
    result = compose_total_debt(facts, DECISION_TIME, DECISION_DATE)
    assert result.status is DebtStatus.OK and result.value == 1_000.0
    assert result.reported_total_minus_components == 50.0


def test_current_plus_noncurrent_composes_when_no_total_is_reported():
    """IDCC at 2026-06-30: current 378,239k, noncurrent 10,861k, and no total tag at that date."""
    facts = [debt(DebtSlot.CURRENT, "LongTermDebtCurrent", 378_239_000),
             debt(DebtSlot.NONCURRENT, "LongTermDebtNoncurrent", 10_861_000)]
    result = compose_total_debt(facts, DECISION_TIME, DECISION_DATE)
    assert result.status is DebtStatus.OK
    assert result.method is DebtMethod.CURRENT_PLUS_NONCURRENT
    assert result.value == 389_100_000
    assert [slot for slot, _ in result.components] == [DebtSlot.CURRENT, DebtSlot.NONCURRENT]


def test_long_term_debt_current_alone_is_not_total_debt():
    """The D5-D0 defect, as a test. `FIELD_SPECS["total_debt"]` ranks the current-portion tags above
    `LongTermDebt`, so the old resolver answered 378,239k for IDCC and 0 for DORM. A current portion
    with no noncurrent counterpart is now INCOMPLETE, never a total."""
    assert FIELD_SPECS["total_debt"].tags.index("LongTermDebtCurrent") < \
        FIELD_SPECS["total_debt"].tags.index("LongTermDebt")
    result = compose_total_debt([debt(DebtSlot.CURRENT, "LongTermDebtCurrent", 378_239_000)],
                                DECISION_TIME, DECISION_DATE)
    assert result.status is DebtStatus.INCOMPLETE_COMPONENTS
    assert result.value is None


def test_a_missing_noncurrent_component_refuses_rather_than_treating_it_as_zero():
    """FRPT and CRK at 2026-06-30: noncurrent borrowings reported, no current tag at that date."""
    result = compose_total_debt([debt(DebtSlot.NONCURRENT, "ConvertibleDebtNoncurrent", 398_443_000)],
                                DECISION_TIME, DECISION_DATE)
    assert result.status is DebtStatus.INCOMPLETE_COMPONENTS
    assert result.value is None
    assert "not zero" in result.reason


def test_a_zero_debt_requires_a_fact_that_says_zero():
    """TG at 2026-06-30: ShortTermBorrowings 0 and LongTermDebtNoncurrent 46,000k. The zero is a
    reported figure, so it composes; and a fully zero debt composes the same way."""
    facts = [debt(DebtSlot.CURRENT, "ShortTermBorrowings", 0.0),
             debt(DebtSlot.NONCURRENT, "LongTermDebtNoncurrent", 46_000_000)]
    result = compose_total_debt(facts, DECISION_TIME, DECISION_DATE)
    assert result.status is DebtStatus.OK and result.value == 46_000_000
    zeros = [debt(DebtSlot.CURRENT, "ShortTermBorrowings", 0.0),
             debt(DebtSlot.NONCURRENT, "LongTermDebtNoncurrent", 0.0)]
    both_zero = compose_total_debt(zeros, DECISION_TIME, DECISION_DATE)
    assert both_zero.status is DebtStatus.OK and both_zero.value == 0.0
    assert len(both_zero.components) == 2


def test_no_debt_evidence_at_all_is_unknown_and_never_zero():
    result = compose_total_debt([cash(173_167_000)], DECISION_TIME, DECISION_DATE)
    assert result.status is DebtStatus.MISSING
    assert result.value is None


def test_two_disagreeing_tags_in_one_slot_are_ambiguous_and_never_summed():
    """COLL at 2026-06-30: LongTermLoansPayable 797,824k and ConvertibleLongTermNotesPayable
    238,733k, both noncurrent. Their sum may be COLL's noncurrent debt; the filing does not say so."""
    facts = [debt(DebtSlot.CURRENT, "LoansPayableCurrent", 55_000_000),
             debt(DebtSlot.NONCURRENT, "LongTermLoansPayable", 797_824_000),
             debt(DebtSlot.NONCURRENT, "ConvertibleLongTermNotesPayable", 238_733_000)]
    result = compose_total_debt(facts, DECISION_TIME, DECISION_DATE)
    assert result.status is DebtStatus.AMBIGUOUS_TAGS
    assert result.value is None
    assert "797824000" in result.reason and "238733000" in result.reason


def test_two_agreeing_tags_in_one_slot_collapse_to_the_higher_priority_one():
    """Synonym duplicates are the case `resolve_fact` already collapses; one value is one answer."""
    facts = [debt(DebtSlot.CURRENT, "LongTermDebtCurrent", 10_000_000, accession="ACC-A"),
             debt(DebtSlot.CURRENT, "NotesPayableCurrent", 10_000_000, accession="ACC-B"),
             debt(DebtSlot.NONCURRENT, "LongTermDebtNoncurrent", 90_000_000)]
    result = compose_total_debt(facts, DECISION_TIME, DECISION_DATE)
    assert result.status is DebtStatus.OK and result.value == 100_000_000
    current = next(fact for slot, fact in result.components if slot is DebtSlot.CURRENT)
    assert current.tag == "LongTermDebtCurrent"


def test_a_credit_facility_disclosure_is_not_a_drawn_balance():
    """SPSC: `LineOfCreditFacilityAmountOutstanding` 0 at 2013-12-31 and a facility capacity, and no
    borrowing balance since. 'No canonical debt tag' and 'zero debt' are different answers."""
    declared = {tag for tag, _ in DEBT_TAGS_THAT_ARE_NOT_A_BALANCE}
    assert "LineOfCreditFacilityAmountOutstanding" in declared
    assert "LineOfCreditFacilityMaximumBorrowingCapacity" in declared
    slotted = {tag for tags in DEBT_SLOT_TAGS.values() for tag in tags}
    assert not declared & slotted


def test_a_stale_balance_sheet_is_refused_instead_of_being_used():
    """COLL's `LongTermDebt` 11,500k is dated 2019-12-31, and D5-D0 measured it resolving OK."""
    ancient = date(2019, 12, 31)
    result = compose_total_debt(
        [debt(DebtSlot.REPORTED_TOTAL, "LongTermDebt", 11_500_000, end=ancient)],
        DECISION_TIME, DECISION_DATE)
    assert result.status is DebtStatus.STALE
    assert result.value is None
    assert str(ancient) in result.reason and "2463" in result.reason or "days" in result.reason


def test_components_from_two_balance_sheet_dates_are_never_added():
    """§12's example: current debt recent, noncurrent debt three years old."""
    facts = [debt(DebtSlot.CURRENT, "LongTermDebtCurrent", 50_000_000),
             debt(DebtSlot.NONCURRENT, "LongTermDebtNoncurrent", 900_000_000,
                  end=Q2 - timedelta(days=3 * 365))]
    result = compose_total_debt(facts, DECISION_TIME, DECISION_DATE)
    assert result.status is DebtStatus.INCOMPLETE_COMPONENTS
    assert result.value != 950_000_000


def test_lease_liabilities_are_outside_every_slot():
    slotted = {tag for tags in DEBT_SLOT_TAGS.values() for tag in tags}
    assert not slotted & set(LEASE_LIABILITY_TAGS_OUT_OF_SCOPE)
    assert not slotted & set(LEASE_BUNDLED_DEBT_TAGS)
    assert LEASE_TREATMENT == "DEFERRED"
    assert "BORROWINGS_ONLY" in EV_DEBT_SCOPE


def test_a_lease_bundled_tag_on_the_same_balance_sheet_is_recorded_as_evidence():
    """WBD at 2026-06-30: LongTermDebt 32,023,000k, and LongTermDebtAndCapitalLeaseObligations
    30,530,000k plus its current 1,493,000k summing to exactly that. The scope question travels with
    the number instead of being resolved by assumption."""
    facts = [debt(DebtSlot.REPORTED_TOTAL, "LongTermDebt", 32_023_000_000),
             instant(DEBT_LEASE_BUNDLED_FIELD, "LongTermDebtAndCapitalLeaseObligations",
                     30_530_000_000, Q2),
             instant(DEBT_LEASE_BUNDLED_FIELD, "LongTermDebtAndCapitalLeaseObligationsCurrent",
                     1_493_000_000, Q2)]
    result = compose_total_debt(facts, DECISION_TIME, DECISION_DATE)
    assert result.status is DebtStatus.OK and result.value == 32_023_000_000
    assert dict(result.lease_bundled_tags_at_same_end) == {
        "LongTermDebtAndCapitalLeaseObligations": 30_530_000_000,
        "LongTermDebtAndCapitalLeaseObligationsCurrent": 1_493_000_000,
    }


def test_the_lease_bundled_evidence_field_cannot_make_a_balance_sheet_date_eligible():
    """Evidence may annotate a date a slot reports on; it may not create one."""
    facts = [instant(DEBT_LEASE_BUNDLED_FIELD, "LongTermDebtAndCapitalLeaseObligations",
                     30_530_000_000, Q2)]
    assert compose_total_debt(facts, DECISION_TIME, DECISION_DATE).status is DebtStatus.MISSING


# -------------------------------------------------------------------------------------------------
# J. Net debt
# -------------------------------------------------------------------------------------------------

def test_net_debt_is_total_debt_less_cash_at_one_balance_sheet_date():
    facts = [debt(DebtSlot.REPORTED_TOTAL, "LongTermDebt", 16_418_000), cash(8_717_000)]
    result = resolve_net_debt(facts, DECISION_TIME, DECISION_DATE)
    assert result.status is CapitalStructureStatus.OK
    assert result.value == 7_701_000
    assert result.period_end == Q2


def test_a_net_cash_issuer_has_negative_net_debt():
    """IDCC at 2026-06-30: debt 389,100k against cash 615,590k. Clamping this at zero would overstate
    the enterprise value of every cash-rich issuer."""
    facts = [debt(DebtSlot.CURRENT, "LongTermDebtCurrent", 378_239_000),
             debt(DebtSlot.NONCURRENT, "LongTermDebtNoncurrent", 10_861_000),
             cash(615_590_000)]
    result = resolve_net_debt(facts, DECISION_TIME, DECISION_DATE)
    assert result.ok and result.value == -226_490_000


def test_cash_and_debt_from_two_balance_sheets_are_never_netted():
    debt_leg = compose_total_debt([debt(DebtSlot.REPORTED_TOTAL, "LongTermDebt", 100.0)],
                                  DECISION_TIME, DECISION_DATE)
    cash_leg = resolve_cash_for_ev([cash(40.0, end=EARLIER_Q)], DECISION_TIME, DECISION_DATE)
    assert debt_leg.ok and cash_leg.ok and debt_leg.period_end != cash_leg.period_end
    result = net_debt_from(debt_leg, cash_leg)
    assert result.status is CapitalStructureStatus.COMPONENT_DATE_MISMATCH
    assert result.value is None


def test_net_debt_picks_the_newest_date_where_both_legs_resolve():
    """DORM's reminder that the dates are not interchangeable: its balance sheet is 2026-06-27."""
    newer, older = Q2, EARLIER_Q
    facts = [cash(10.0, end=newer), cash(20.0, end=older),
             debt(DebtSlot.REPORTED_TOTAL, "LongTermDebt", 100.0, end=older)]
    result = resolve_net_debt(facts, DECISION_TIME, DECISION_DATE)
    assert result.ok and result.period_end == older and result.value == 80.0


def test_a_missing_cash_leg_makes_net_debt_unknown_and_not_the_debt_itself():
    facts = [debt(DebtSlot.REPORTED_TOTAL, "LongTermDebt", 100.0)]
    result = resolve_net_debt(facts, DECISION_TIME, DECISION_DATE)
    assert result.status is CapitalStructureStatus.MISSING_CASH
    assert result.value is None


# -------------------------------------------------------------------------------------------------
# K. Enterprise value
# -------------------------------------------------------------------------------------------------

def _net_debt(debt_value: float, cash_value: float):
    facts = [debt(DebtSlot.REPORTED_TOTAL, "LongTermDebt", debt_value), cash(cash_value)]
    return resolve_net_debt(facts, DECISION_TIME, DECISION_DATE)


def test_enterprise_value_is_market_cap_plus_total_debt_less_cash():
    net = _net_debt(2_239_000_000, 2_103_000_000)
    ev = enterprise_value(ok_market_cap(1_000_000_000), net, DECISION_TIME, DECISION_DATE)
    assert ev.status is CapitalStructureStatus.OK
    assert ev.value == 1_000_000_000 + 2_239_000_000 - 2_103_000_000


def test_the_two_orderings_of_the_enterprise_value_identity_agree():
    """§15: market cap + total debt - cash, and market cap + net debt, are one number."""
    net = _net_debt(1_034_657_000, 49_561_000)
    ev = enterprise_value(ok_market_cap(3_210_987_654.32), net, DECISION_TIME, DECISION_DATE)
    assert ev.components_sum == pytest.approx(ev.value, rel=1e-12, abs=0.0)


def test_an_unresolved_share_class_makes_the_enterprise_value_unknown():
    """§13's fail-closed rule, and the state every stored issuer is actually in."""
    net = _net_debt(100.0, 40.0)
    blocked = MarketCapResolution(MarketCapStatus.UNKNOWN_SHARE_CLASS_UNRESOLVED, None,
                                  "no detector")
    ev = enterprise_value(blocked, net, DECISION_TIME, DECISION_DATE)
    assert ev.status is CapitalStructureStatus.MULTI_CLASS_UNKNOWN
    assert ev.value is None


def test_multiple_share_classes_make_the_enterprise_value_unknown():
    net = _net_debt(100.0, 40.0)
    blocked = MarketCapResolution(MarketCapStatus.UNKNOWN_MULTIPLE_SHARE_CLASSES, None, "two classes")
    ev = enterprise_value(blocked, net, DECISION_TIME, DECISION_DATE)
    assert ev.status is CapitalStructureStatus.MULTI_CLASS_UNKNOWN
    assert ev.value is None


@pytest.mark.parametrize("state", [ShareClassState.UNRESOLVED, ShareClassState.MULTIPLE_CLASSES])
def test_the_share_class_gate_runs_before_shares_and_price(state):
    """P0's gate ordering, asserted from P1's side: with no shares and no price supplied either, the
    refusal still names the share class, so an enterprise value that is NOT_READY points at the blocker
    that actually stopped it rather than the next one down."""
    market_cap = valuation_market_cap([], DECISION_DATE, [], None, state)
    assert market_cap.value is None and not market_cap.valuation_ready
    ev = enterprise_value(market_cap, _net_debt(100.0, 40.0), DECISION_TIME, DECISION_DATE)
    assert ev.status is CapitalStructureStatus.MULTI_CLASS_UNKNOWN
    assert ev.value is None


@pytest.mark.parametrize("status", [MarketCapStatus.UNKNOWN_SHARES, MarketCapStatus.UNKNOWN_PRICE])
def test_a_missing_market_cap_makes_the_enterprise_value_unknown(status):
    ev = enterprise_value(MarketCapResolution(status, None, "absent"), _net_debt(100.0, 40.0),
                          DECISION_TIME, DECISION_DATE)
    assert ev.status is CapitalStructureStatus.MARKET_CAP_UNKNOWN
    assert ev.value is None


def test_an_unknown_debt_leg_makes_the_enterprise_value_unknown():
    net = resolve_net_debt([cash(40.0)], DECISION_TIME, DECISION_DATE)
    ev = enterprise_value(ok_market_cap(1_000.0), net, DECISION_TIME, DECISION_DATE)
    assert ev.status is CapitalStructureStatus.MISSING_DEBT
    assert ev.value is None


def test_a_stale_balance_sheet_cannot_be_combined_with_a_current_market_cap():
    """§16: a 2026 market capitalisation and a 2019 balance sheet make no enterprise value."""
    ancient = date(2019, 12, 31)
    facts = [debt(DebtSlot.REPORTED_TOTAL, "LongTermDebt", 11_500_000, end=ancient),
             cash(129_467_000, end=ancient)]
    net = resolve_net_debt(facts, DECISION_TIME, DECISION_DATE)
    ev = enterprise_value(ok_market_cap(2_000_000_000), net, DECISION_TIME, DECISION_DATE)
    assert ev.value is None
    assert ev.status is not CapitalStructureStatus.OK


# -------------------------------------------------------------------------------------------------
# L. Provenance
# -------------------------------------------------------------------------------------------------

def test_the_provenance_record_carries_every_component_fact_and_the_bound():
    facts = [debt(DebtSlot.CURRENT, "LongTermDebtCurrent", 378_239_000, accession="ACC-Q2"),
             debt(DebtSlot.NONCURRENT, "LongTermDebtNoncurrent", 10_861_000, accession="ACC-Q2"),
             cash(615_590_000, accession="ACC-Q2")]
    net = resolve_net_debt(facts, DECISION_TIME, DECISION_DATE)
    record = enterprise_value(ok_market_cap(5_000_000_000), net, DECISION_TIME,
                              DECISION_DATE).to_dict()
    assert record["decision_time"] == DECISION_TIME.isoformat()
    assert record["decision_date"] == DECISION_DATE.isoformat()
    assert record["staleness_bound_days"] == MAX_INSTANT_STALENESS_DAYS
    debt_record = record["net_debt"]["debt"]
    assert debt_record["construction_method"] == DebtMethod.CURRENT_PLUS_NONCURRENT.value
    assert debt_record["scope"] == EV_DEBT_SCOPE
    assert debt_record["lease_treatment"] == LEASE_TREATMENT
    assert len(debt_record["components"]) == 2
    for component in debt_record["components"]:
        assert component["fact_id"] and component["tag"] and component["acceptance_time"]
        assert component["end"] == Q2.isoformat()
    cash_record = record["net_debt"]["cash"]
    assert cash_record["fact"]["fact_id"].startswith("ACC-Q2:")
    assert cash_record["age_days"] == 78


def test_the_provenance_record_is_deterministic_across_repeated_resolution():
    facts = [debt(DebtSlot.REPORTED_TOTAL, "LongTermDebt", 16_418_000), cash(8_717_000)]
    runs = [enterprise_value(ok_market_cap(1.0),
                             resolve_net_debt(facts, DECISION_TIME, DECISION_DATE),
                             DECISION_TIME, DECISION_DATE).to_dict() for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


def test_a_refusal_never_carries_a_value():
    """The one way a valuation pipeline invents a number is a refusal that still reports one."""
    refusals = [
        compose_total_debt([], DECISION_TIME, DECISION_DATE),
        compose_total_debt([debt(DebtSlot.CURRENT, "LongTermDebtCurrent", 5.0)],
                           DECISION_TIME, DECISION_DATE),
        resolve_cash_for_ev([], DECISION_TIME, DECISION_DATE),
        resolve_net_debt([], DECISION_TIME, DECISION_DATE),
    ]
    for refusal in refusals:
        assert refusal.value is None, refusal


# -------------------------------------------------------------------------------------------------
# The registries, and what they must not disturb
# -------------------------------------------------------------------------------------------------

def test_the_capital_structure_registry_is_a_strict_superset_that_shares_its_specs():
    assert set(VALUATION_FIELD_SPECS) < set(CAPITAL_STRUCTURE_FIELD_SPECS)
    assert all(CAPITAL_STRUCTURE_FIELD_SPECS[name] is spec
               for name, spec in VALUATION_FIELD_SPECS.items())
    assert len(FIELD_SPECS) == 12, "D1's total_field_count must not move"
    assert "cash_for_ev" not in FIELD_SPECS and "debt_reported_total" not in FIELD_SPECS


def test_the_canonical_total_debt_field_is_left_exactly_as_it_was():
    """Historical D1/D2/D3/D4 outcomes are not modified by this step, so the broken field stays."""
    assert FIELD_SPECS["total_debt"].tags == (
        "LongTermDebtAndFinanceLeaseObligationsCurrent",
        "LongTermDebtCurrent", "LongTermDebtNoncurrent", "LongTermDebt")


def test_every_slot_tag_appears_in_exactly_one_slot():
    seen: dict[str, DebtSlot] = {}
    for slot, tags in DEBT_SLOT_TAGS.items():
        assert len(set(tags)) == len(tags), f"{slot} repeats a tag"
        for tag in tags:
            assert tag not in seen, f"{tag} is in both {seen.get(tag)} and {slot}"
            seen[tag] = slot
    assert set(seen) == set(REPORTED_TOTAL_DEBT_TAGS) | set(CURRENT_DEBT_TAGS) | set(
        NONCURRENT_DEBT_TAGS)


def test_equity_keeps_the_tag_priority_that_is_a_preference_between_definitions():
    """An issuer with a minority interest reports both equity definitions at different values. Price
    to book wants the one excluding non-controlling interests, which is why it is first - and
    refusing on the disagreement cost two of ten issuers for nothing."""
    assert FIELD_SPECS["equity"].tags[0] == "StockholdersEquity"
    facts = [instant("equity", "StockholdersEquity", 500_000_000, Q2),
             instant("equity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                     560_000_000, Q2)]
    result = resolve_valuation_instant(facts, "equity", DECISION_TIME, DECISION_DATE)
    assert result.status is InstantStatus.OK
    assert result.value == 500_000_000
    assert "equity" in TAG_PRIORITY_IS_A_PREFERENCE_FOR


def test_equity_is_bounded_by_the_same_staleness_rule():
    old = DECISION_DATE - timedelta(days=MAX_INSTANT_STALENESS_DAYS + 1)
    facts = [instant("equity", "StockholdersEquity", 500_000_000, old)]
    assert resolve_valuation_instant(facts, "equity", DECISION_TIME,
                                     DECISION_DATE).status is InstantStatus.STALE


def test_a_composed_field_is_not_resolvable_as_a_single_line():
    with pytest.raises(ValueError):
        resolve_valuation_instant([], "cash_for_ev", DECISION_TIME, DECISION_DATE)
    with pytest.raises(KeyError):
        resolve_valuation_instant([], "no_such_field", DECISION_TIME, DECISION_DATE)


# -------------------------------------------------------------------------------------------------
# N. Feasibility is a verdict about inputs
# -------------------------------------------------------------------------------------------------

def test_method_feasibility_reports_numerator_and_denominator_separately():
    from app.backtest.strategy_h_v2.valuation.ttm import TtmResult, TtmStatus

    def ttm_ok(field: str) -> TtmResult:
        return TtmResult(field=field, status=TtmStatus.OK, decision_time=DECISION_TIME,
                         reason="fixture", value=1.0, unit="USD",
                         period_start=date(2025, 7, 1), period_end=Q2, duration_days=364)

    bundle = {name: ttm_ok(name) for name in
              ("revenue", "operating_income", "free_cash_flow", "depreciation_amortization")}
    bundle["eps_diluted"] = TtmResult(field="eps_diluted", status=TtmStatus.MISSING_COMPONENT,
                                      decision_time=DECISION_TIME, reason="fixture")
    blocked = enterprise_value(
        MarketCapResolution(MarketCapStatus.UNKNOWN_SHARE_CLASS_UNRESOLVED, None, "no detector"),
        _net_debt(100.0, 40.0), DECISION_TIME, DECISION_DATE)
    feasibility = method_feasibility(blocked, bundle)
    assert set(feasibility) == {"EV/Sales", "EV/EBIT", "EV/EBITDA", "EV/FCF", "P/E", "P/B", "P/FCF"}
    for method in ("EV/Sales", "EV/EBIT", "EV/EBITDA", "EV/FCF"):
        assert feasibility[method].denominator_ready is True
        assert feasibility[method].numerator_ready is False
        assert feasibility[method].feasible is False
    assert feasibility["P/E"].denominator_ready is False

    ready = enterprise_value(ok_market_cap(1_000.0), _net_debt(100.0, 40.0), DECISION_TIME,
                             DECISION_DATE)
    assert method_feasibility(ready, bundle)["EV/Sales"].feasible is True


def test_feasibility_returns_verdicts_and_no_values():
    from app.backtest.strategy_h_v2.valuation.ttm import TtmResult, TtmStatus
    bundle = {"revenue": TtmResult(field="revenue", status=TtmStatus.OK,
                                   decision_time=DECISION_TIME, reason="fixture", value=12345.0,
                                   unit="USD", period_end=Q2)}
    record = method_feasibility(
        enterprise_value(ok_market_cap(1_000.0), _net_debt(100.0, 40.0), DECISION_TIME,
                         DECISION_DATE),
        bundle)["EV/Sales"].to_dict()
    assert set(record) == {"method", "numerator_ready", "denominator_ready", "feasible", "reason"}
    assert 12345.0 not in record.values()


def test_each_prohibition_states_itself():
    """D4-E7R's house rule: a prohibition whose negation lives only in the constant's name trips this
    project's own decision-leakage detector when quoted."""
    assert len(NEVER_CONSTRUCTED) >= 5
    for line in NEVER_CONSTRUCTED:
        assert any(word in line for word in ("never", "only", "requires", "UNKNOWN", "STALE",
                                             "AMBIGUOUS")), line


# -------------------------------------------------------------------------------------------------
# M. The measured corpus. Skips when the gitignored store is absent.
# -------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def measured():
    audit = pytest.importorskip("app.dev.audit_strategy_h_v2_d5_p1")
    if not audit.D2_1_PACKAGES.exists() or not audit.DAILY_PANEL_DIR.exists():
        pytest.skip("the D2.1 packages or the daily panel are not in this checkout")
    report = audit.audit(audit.D4_ISSUERS)
    if report["issuers"] != len(audit.D4_ISSUERS):
        pytest.skip(f"only {report['issuers']} of the ten issuers are stored locally")
    return report


def test_measured_acceptance_criteria_are_all_empty(measured):
    """§21-§23: the criteria that decide whether this step holds, counted on real filings."""
    acceptance = measured["acceptance"]
    for criterion in ("stale_instant_accepted", "debt_zero_without_fact_evidence",
                      "debt_double_counted", "value_reported_on_a_refusal",
                      "multi_class_unsafe_enterprise_value", "future_components",
                      "net_debt_across_two_balance_sheets",
                      "reported_total_versus_components_nonzero"):
        assert acceptance[criterion] == [], (criterion, acceptance[criterion])


def test_measured_coverage_is_what_the_document_reports(measured):
    coverage = measured["coverage"]
    assert coverage["cash"]["ok"] == 10
    assert coverage["total_debt"]["ok"] == 6
    assert coverage["net_debt"]["ok"] == 6
    assert coverage["equity"]["ok"] == 10
    assert coverage["market_cap"]["ok"] == 0
    assert coverage["enterprise_value"]["ok"] == 0


def test_measured_enterprise_value_is_blocked_only_by_the_share_class_gate(measured):
    """The six issuers whose cash, debt, shares and price all resolve. Named as a refusal count,
    because a conditional is not coverage."""
    assert measured["acceptance"]["enterprise_value_blocked_only_by_share_class"] == [
        "AEYE", "FG", "VRRM", "IDCC", "DORM", "TG"]


def test_measured_debt_repairs_the_two_defects_d5_d0_named(measured):
    """IDCC resolved to its current portion alone and DORM to zero. Both now compose."""
    rows = {row["ticker"]: row for row in measured["rows"]}
    idcc = rows["IDCC"]["debt"]
    assert idcc["status"] == DebtStatus.OK.value and idcc["value"] == 389_100_000
    assert idcc["construction_method"] == DebtMethod.CURRENT_PLUS_NONCURRENT.value
    dorm = rows["DORM"]["debt"]
    assert dorm["status"] == DebtStatus.OK.value and dorm["value"] == 440_479_000
    assert rows["IDCC"]["net_debt"]["net_debt"] == -226_490_000


def test_measured_residual_scan_finds_no_unaccounted_borrowing_balance(measured):
    """A composition is complete only over the tags it knows, so the audit looks at what else the
    filer tagged on the same balance sheet. Everything it returns must be a non-borrowing."""
    allowed = ("AssetRetirementObligation", "RevenueRemainingPerformanceObligation",
               "PurchaseObligation", "ContractualObligation", "SupplierFinanceProgramObligation",
               "FinanceLeaseLiabilityPayments")
    for ticker, hits in measured["acceptance"]["unslotted_nonzero_debt_named_tags"]:
        for tag, _ in hits:
            assert tag.startswith(allowed), (ticker, tag)


def test_measured_ttm_outcomes_are_unmoved_by_the_wider_registry(measured):
    """P0.1's measured TTM coverage, reproduced through the capital-structure registry. A registry
    that moved them would be changing a historical H result."""
    rows = measured["rows"]
    ok = {field: sum(1 for row in rows if row["ttm"][field]["status"] == "OK")
          for field in rows[0]["ttm"]}
    assert ok == {"revenue": 9, "operating_income": 8, "net_income": 10, "eps_diluted": 1,
                  "operating_cash_flow": 10, "capex": 8, "depreciation_amortization": 7,
                  "free_cash_flow": 8}
