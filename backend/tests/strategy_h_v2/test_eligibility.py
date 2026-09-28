from __future__ import annotations

from conftest import utc

from app.backtest.strategy_h_v2.eligibility import (
    EligibilityReason,
    EligibilityStatus,
    FundamentalsCoverage,
    MarketSnapshot,
    MIN_RESOLVED_CANONICAL_FIELDS,
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
    resolved_field_count=10, total_field_count=12, negative_equity=False, material_dilution=False,
)


def test_eligible_when_everything_clears():
    result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, GOOD_FUNDAMENTALS)
    assert result.status == EligibilityStatus.ELIGIBLE
    assert result.reasons == ()


def test_not_common_stock_reason():
    row = UniverseRow(**{**GOOD_ROW.__dict__, "security_type_status": SecurityTypeStatus.NOT_COMMON_STOCK})
    result = evaluate_eligibility(row, GOOD_MARKET, GOOD_FUNDAMENTALS)
    assert result.status == EligibilityStatus.INELIGIBLE
    assert EligibilityReason.NOT_COMMON_STOCK in result.reasons


def test_unsupported_exchange_reason():
    row = UniverseRow(**{**GOOD_ROW.__dict__, "exchange_supported": False})
    result = evaluate_eligibility(row, GOOD_MARKET, GOOD_FUNDAMENTALS)
    assert EligibilityReason.UNSUPPORTED_EXCHANGE in result.reasons


def test_missing_cik_reason():
    row = UniverseRow(**{**GOOD_ROW.__dict__, "cik": None})
    result = evaluate_eligibility(row, GOOD_MARKET, GOOD_FUNDAMENTALS)
    assert EligibilityReason.MISSING_CIK in result.reasons


def test_multiple_reason_codes_can_coexist():
    row = UniverseRow(**{**GOOD_ROW.__dict__, "cik": None, "exchange_supported": False})
    result = evaluate_eligibility(row, GOOD_MARKET, GOOD_FUNDAMENTALS)
    assert {EligibilityReason.MISSING_CIK, EligibilityReason.UNSUPPORTED_EXCHANGE} <= set(result.reasons)


def test_insufficient_daily_history():
    market = MarketSnapshot(latest_close=50.0, trailing_sessions=10, trailing_avg_dollar_volume=10_000_000.0)
    result = evaluate_eligibility(GOOD_ROW, market, GOOD_FUNDAMENTALS)
    assert EligibilityReason.INSUFFICIENT_DAILY_HISTORY in result.reasons


def test_extreme_low_price():
    market = MarketSnapshot(latest_close=0.25, trailing_sessions=200, trailing_avg_dollar_volume=10_000_000.0)
    result = evaluate_eligibility(GOOD_ROW, market, GOOD_FUNDAMENTALS)
    assert EligibilityReason.EXTREME_LOW_PRICE in result.reasons


def test_low_liquidity():
    market = MarketSnapshot(latest_close=50.0, trailing_sessions=200, trailing_avg_dollar_volume=1_000.0)
    result = evaluate_eligibility(GOOD_ROW, market, GOOD_FUNDAMENTALS)
    assert EligibilityReason.LOW_LIQUIDITY in result.reasons


def test_insufficient_fundamentals_below_minimum():
    fundamentals = FundamentalsCoverage(
        resolved_field_count=MIN_RESOLVED_CANONICAL_FIELDS - 1, total_field_count=12,
        negative_equity=None, material_dilution=None,
    )
    result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, fundamentals)
    assert EligibilityReason.INSUFFICIENT_FUNDAMENTALS in result.reasons


def test_distress_flag_on_negative_equity():
    fundamentals = FundamentalsCoverage(
        resolved_field_count=10, total_field_count=12, negative_equity=True, material_dilution=False,
    )
    result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, fundamentals)
    assert EligibilityReason.DISTRESS_FLAG in result.reasons


def test_no_price_data_is_unknown_not_ineligible_by_itself():
    fundamentals = FundamentalsCoverage(
        resolved_field_count=10, total_field_count=12, negative_equity=False, material_dilution=False,
    )
    result = evaluate_eligibility(GOOD_ROW, None, fundamentals)
    assert result.status == EligibilityStatus.UNKNOWN
    assert EligibilityReason.NO_PRICE_DATA in result.reasons


def test_unknown_equity_recorded_but_does_not_block_an_otherwise_eligible_candidate():
    """An unresolved secondary risk flag (distress) is not silently treated as healthy - it is
    recorded in `reasons` for the evidence bundle - but it must not by itself veto ELIGIBLE when
    every primary input (price, liquidity, core fundamentals coverage) is fine."""
    fundamentals = FundamentalsCoverage(
        resolved_field_count=10, total_field_count=12, negative_equity=None, material_dilution=False,
    )
    result = evaluate_eligibility(GOOD_ROW, GOOD_MARKET, fundamentals)
    assert result.status == EligibilityStatus.ELIGIBLE
    assert EligibilityReason.DISTRESS_FLAG in result.reasons


def test_ineligible_reasons_take_priority_over_unknown():
    fundamentals = FundamentalsCoverage(
        resolved_field_count=0, total_field_count=12, negative_equity=None, material_dilution=None,
    )
    row = UniverseRow(**{**GOOD_ROW.__dict__, "cik": None})
    result = evaluate_eligibility(row, GOOD_MARKET, fundamentals)
    assert result.status == EligibilityStatus.INELIGIBLE
