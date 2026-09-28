from __future__ import annotations

from conftest import utc

from app.backtest.strategy_h_v2.eligibility import (
    CandidateStatus,
    EligibilityReason,
    EligibilityStatus,
    FundamentalsCoverage,
    MIN_RESOLVED_CANONICAL_FIELDS,
    MarketSnapshot,
    evaluate_eligibility,
)
from app.backtest.strategy_h_v2.universe import SecurityTypeStatus, UniverseRow

KNOWN_AT = utc(2026, 9, 28)

GOOD_ROW = UniverseRow(
    ticker="AAPL", cik="0000320193", security_id="BBG001S5N8V8", exchange="XNAS",
    exchange_supported=True, security_type_status=SecurityTypeStatus.COMMON_STOCK,
    market="stocks", locale="us", snapshot_date="2026-09-28", known_at=KNOWN_AT,
)
GOOD_MARKET = MarketSnapshot(latest_close=200.0, trailing_sessions=200, trailing_avg_dollar_volume=5_000_000_000.0)
GOOD_FUNDAMENTALS = FundamentalsCoverage(
    resolved_field_count=10, total_field_count=12, ambiguous_field_count=0, facts_fetched=True,
    earliest_fact_age_days=1500, negative_equity=False, material_dilution=False,
)


def test_status_alias_is_the_same_enum():
    assert EligibilityStatus is CandidateStatus


def test_eligible_when_everything_clears():
    result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, GOOD_FUNDAMENTALS)
    assert result.status == CandidateStatus.ELIGIBLE
    assert result.reasons == ()


# --- INELIGIBLE: real, known facts about the security/market -----------------------------------

def test_not_common_stock_is_ineligible():
    row = UniverseRow(**{**GOOD_ROW.__dict__, "security_type_status": SecurityTypeStatus.NOT_COMMON_STOCK})
    result = evaluate_eligibility(row, GOOD_MARKET, GOOD_FUNDAMENTALS)
    assert result.status == CandidateStatus.INELIGIBLE
    assert EligibilityReason.NOT_COMMON_STOCK in result.reasons


def test_unsupported_exchange_is_ineligible():
    row = UniverseRow(**{**GOOD_ROW.__dict__, "exchange_supported": False})
    result = evaluate_eligibility(row, GOOD_MARKET, GOOD_FUNDAMENTALS)
    assert result.status == CandidateStatus.INELIGIBLE
    assert EligibilityReason.UNSUPPORTED_EXCHANGE in result.reasons


def test_missing_cik_is_ineligible():
    row = UniverseRow(**{**GOOD_ROW.__dict__, "cik": None})
    result = evaluate_eligibility(row, GOOD_MARKET, GOOD_FUNDAMENTALS)
    assert result.status == CandidateStatus.INELIGIBLE
    assert EligibilityReason.MISSING_CIK in result.reasons


def test_extreme_low_price_is_ineligible():
    market = MarketSnapshot(latest_close=0.25, trailing_sessions=200, trailing_avg_dollar_volume=10_000_000.0)
    result = evaluate_eligibility(GOOD_ROW, market, GOOD_FUNDAMENTALS)
    assert result.status == CandidateStatus.INELIGIBLE
    assert EligibilityReason.EXTREME_LOW_PRICE in result.reasons


def test_confirmed_low_liquidity_is_ineligible():
    market = MarketSnapshot(latest_close=50.0, trailing_sessions=200, trailing_avg_dollar_volume=1_000.0)
    result = evaluate_eligibility(GOOD_ROW, market, GOOD_FUNDAMENTALS)
    assert result.status == CandidateStatus.INELIGIBLE
    assert EligibilityReason.LOW_LIQUIDITY in result.reasons


def test_confirmed_negative_equity_is_ineligible():
    fundamentals = FundamentalsCoverage(
        resolved_field_count=10, total_field_count=12, ambiguous_field_count=0, facts_fetched=True,
        earliest_fact_age_days=1500, negative_equity=True, material_dilution=False,
    )
    result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, fundamentals)
    assert result.status == CandidateStatus.INELIGIBLE
    assert EligibilityReason.DISTRESS_FLAG in result.reasons


def test_confirmed_material_dilution_is_ineligible():
    fundamentals = FundamentalsCoverage(
        resolved_field_count=10, total_field_count=12, ambiguous_field_count=0, facts_fetched=True,
        earliest_fact_age_days=1500, negative_equity=False, material_dilution=True,
    )
    result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, fundamentals)
    assert result.status == CandidateStatus.INELIGIBLE
    assert EligibilityReason.EXTREME_DILUTION_RISK in result.reasons


def test_multiple_reason_codes_can_coexist():
    row = UniverseRow(**{**GOOD_ROW.__dict__, "cik": None, "exchange_supported": False})
    result = evaluate_eligibility(row, GOOD_MARKET, GOOD_FUNDAMENTALS)
    assert {EligibilityReason.MISSING_CIK, EligibilityReason.UNSUPPORTED_EXCHANGE} <= set(result.reasons)


# --- DATA_NOT_READY: local cache/history gaps, not a company fact ------------------------------
# This is the D1.1 fix: `MISSING LOCAL DATA != COMPANY INELIGIBLE`.

def test_missing_local_fundamental_is_not_ineligible():
    """The core D1.1 regression test. A security whose local SEC store has nothing at all must
    never come back INELIGIBLE - only DATA_NOT_READY, because we have proven nothing bad about the
    company, only that our cache does not (yet) cover it."""
    fundamentals = FundamentalsCoverage(
        resolved_field_count=0, total_field_count=12, ambiguous_field_count=0, facts_fetched=False,
        earliest_fact_age_days=None, negative_equity=None, material_dilution=None,
    )
    result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, fundamentals)
    assert result.status == CandidateStatus.DATA_NOT_READY
    assert result.status != CandidateStatus.INELIGIBLE
    assert EligibilityReason.FUNDAMENTALS_NOT_FETCHED in result.reasons


def test_recent_ipo_is_data_not_ready_with_its_own_reason_not_fundamentals_not_fetched():
    """Companyfacts WERE fetched (facts_fetched=True); the company simply has not been reporting
    long enough. This must not be confused with "we never tried" (D1.1 brief §14)."""
    fundamentals = FundamentalsCoverage(
        resolved_field_count=2, total_field_count=12, ambiguous_field_count=0, facts_fetched=True,
        earliest_fact_age_days=200, negative_equity=None, material_dilution=None,
    )
    result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, fundamentals)
    assert result.status == CandidateStatus.DATA_NOT_READY
    assert EligibilityReason.INSUFFICIENT_REPORTING_HISTORY in result.reasons
    assert EligibilityReason.FUNDAMENTALS_NOT_FETCHED not in result.reasons


def test_older_company_with_thin_resolution_is_insufficient_comparable_periods():
    fundamentals = FundamentalsCoverage(
        resolved_field_count=2, total_field_count=12, ambiguous_field_count=0, facts_fetched=True,
        earliest_fact_age_days=2000, negative_equity=None, material_dilution=None,
    )
    result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, fundamentals)
    assert result.status == CandidateStatus.DATA_NOT_READY
    assert EligibilityReason.INSUFFICIENT_COMPARABLE_PERIODS in result.reasons


def test_no_price_data_is_data_not_ready():
    result = evaluate_eligibility(GOOD_ROW, None, GOOD_FUNDAMENTALS)
    assert result.status == CandidateStatus.DATA_NOT_READY
    assert EligibilityReason.REQUIRED_MARKET_DATA_NOT_AVAILABLE in result.reasons


def test_insufficient_daily_history_is_data_not_ready_not_ineligible():
    market = MarketSnapshot(latest_close=50.0, trailing_sessions=10, trailing_avg_dollar_volume=10_000_000.0)
    result = evaluate_eligibility(GOOD_ROW, market, GOOD_FUNDAMENTALS)
    assert result.status == CandidateStatus.DATA_NOT_READY
    assert EligibilityReason.REQUIRED_MARKET_DATA_NOT_AVAILABLE in result.reasons


def test_missing_security_id_is_reference_data_not_ready():
    row = UniverseRow(**{**GOOD_ROW.__dict__, "security_id": None})
    result = evaluate_eligibility(row, GOOD_MARKET, GOOD_FUNDAMENTALS)
    assert result.status == CandidateStatus.DATA_NOT_READY
    assert EligibilityReason.REFERENCE_DATA_NOT_READY in result.reasons


def test_unknown_security_type_is_reference_data_not_ready():
    row = UniverseRow(**{**GOOD_ROW.__dict__, "security_type_status": SecurityTypeStatus.UNKNOWN})
    result = evaluate_eligibility(row, GOOD_MARKET, GOOD_FUNDAMENTALS)
    assert result.status == CandidateStatus.DATA_NOT_READY
    assert EligibilityReason.REFERENCE_DATA_NOT_READY in result.reasons


def test_insufficient_fundamentals_alone_never_reaches_ineligible():
    for facts_fetched, age in [(False, None), (True, 100), (True, 3000)]:
        fundamentals = FundamentalsCoverage(
            resolved_field_count=MIN_RESOLVED_CANONICAL_FIELDS - 1, total_field_count=12,
            ambiguous_field_count=0, facts_fetched=facts_fetched, earliest_fact_age_days=age,
            negative_equity=None, material_dilution=None,
        )
        result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, fundamentals)
        assert result.status == CandidateStatus.DATA_NOT_READY, (facts_fetched, age, result)


# --- UNKNOWN: data exists but cannot be stably interpreted --------------------------------------
#
# `UNKNOWN` is schema-complete (SECURITY_TYPE_AMBIGUOUS / CIK_CONFLICT / CORPORATE_ACTION_UNRESOLVED
# / FUNDAMENTAL_FACT_AMBIGUOUS are defined reasons) but no detector currently populates it. A first
# attempt at `FUNDAMENTAL_FACT_AMBIGUOUS` (>=3 fields resolving to `FactStatus.AMBIGUOUS`) was
# tested against the full acquired universe and removed: it fired on ordinary 10-Q reporting, where
# the same period-end legitimately carries both a discrete-quarter and a year-to-date duration, not
# on genuine conflicting data. The regression test below guards against reintroducing that bug.

def test_ambiguous_field_count_is_diagnostic_only_and_never_changes_status():
    high_ambiguous = FundamentalsCoverage(
        resolved_field_count=10, total_field_count=12, ambiguous_field_count=8, facts_fetched=True,
        earliest_fact_age_days=1500, negative_equity=False, material_dilution=False,
    )
    result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, high_ambiguous)
    assert result.status == CandidateStatus.ELIGIBLE
    assert EligibilityReason.FUNDAMENTAL_FACT_AMBIGUOUS not in result.reasons


def test_hard_ineligible_outranks_data_not_ready():
    row = UniverseRow(**{**GOOD_ROW.__dict__, "cik": None})
    fundamentals = FundamentalsCoverage(
        resolved_field_count=0, total_field_count=12, ambiguous_field_count=0, facts_fetched=False,
        earliest_fact_age_days=None, negative_equity=None, material_dilution=None,
    )
    result = evaluate_eligibility(row, GOOD_MARKET, fundamentals)
    assert result.status == CandidateStatus.INELIGIBLE


# --- soft unknown: secondary risk flags never veto an otherwise-eligible candidate --------------

def test_unresolved_distress_check_stays_eligible_with_a_caveat():
    fundamentals = FundamentalsCoverage(
        resolved_field_count=10, total_field_count=12, ambiguous_field_count=0, facts_fetched=True,
        earliest_fact_age_days=1500, negative_equity=None, material_dilution=False,
    )
    result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, fundamentals)
    assert result.status == CandidateStatus.ELIGIBLE
    assert EligibilityReason.DISTRESS_UNRESOLVED in result.reasons


def test_failed_download_remains_data_not_ready_not_promoted_or_demoted():
    """Whether a CIK's companyfacts were never requested, or a request was attempted and failed
    (a `SecFetchError` in the acquisition run), the candidate's `facts_fetched` is `False` either
    way and the resulting status is the same `DATA_NOT_READY` - a failed fetch is never silently
    treated as proof of ineligibility, and never silently promoted to ELIGIBLE either."""
    never_attempted = FundamentalsCoverage(
        resolved_field_count=0, total_field_count=12, ambiguous_field_count=0, facts_fetched=False,
        earliest_fact_age_days=None, negative_equity=None, material_dilution=None,
    )
    attempted_and_failed = FundamentalsCoverage(
        resolved_field_count=0, total_field_count=12, ambiguous_field_count=0, facts_fetched=False,
        earliest_fact_age_days=None, negative_equity=None, material_dilution=None,
    )
    result_a = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, never_attempted)
    result_b = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, attempted_and_failed)
    assert result_a.status == result_b.status == CandidateStatus.DATA_NOT_READY


def test_normalization_failure_handling_fetched_but_nothing_usable_resolved():
    """A companyfacts document CAN be fetched successfully and still normalize to almost nothing
    usable (a shell filer, or one whose tags do not match H0's canonical mapping). This is a
    normalization/coverage gap, not a download failure and not a company-quality judgment - it
    still routes to `DATA_NOT_READY`, with a reason that reflects fetched-but-thin rather than
    never-fetched."""
    fetched_but_empty = FundamentalsCoverage(
        resolved_field_count=0, total_field_count=12, ambiguous_field_count=0, facts_fetched=True,
        earliest_fact_age_days=None, negative_equity=None, material_dilution=None,
    )
    result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, fetched_but_empty)
    assert result.status == CandidateStatus.DATA_NOT_READY
    assert EligibilityReason.INSUFFICIENT_COMPARABLE_PERIODS in result.reasons
    assert EligibilityReason.FUNDAMENTALS_NOT_FETCHED not in result.reasons


def test_unresolved_dilution_check_stays_eligible_with_a_caveat():
    fundamentals = FundamentalsCoverage(
        resolved_field_count=10, total_field_count=12, ambiguous_field_count=0, facts_fetched=True,
        earliest_fact_age_days=1500, negative_equity=False, material_dilution=None,
    )
    result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, fundamentals)
    assert result.status == CandidateStatus.ELIGIBLE
    assert EligibilityReason.DILUTION_UNRESOLVED in result.reasons
