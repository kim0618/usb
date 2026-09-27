from datetime import date, datetime, timezone

import pytest

from app.backtest.strategy_h0.facts import (
    CanonicalFact, FactStatus, canonical_coverage, extract_companyfacts, resolve_fact,
)
from app.backtest.strategy_h0.pit import MappingRecord, market_cap_at, ticker_to_cik_at

UTC = timezone.utc


def t(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def fact(**changes) -> CanonicalFact:
    values = dict(
        field="revenue", taxonomy="us-gaap", tag="Revenues", unit="USD", value=100.0,
        start=date(2023, 1, 1), end=date(2023, 3, 31), filed=date(2023, 5, 1),
        accepted_at=t("2023-05-01T20:00:00"), accession="0001", form="10-Q",
        fiscal_year=2023, fiscal_period="Q1", frame="CY2023Q1",
    )
    values.update(changes)
    return CanonicalFact(**values)


def test_future_filing_is_rejected_by_decision_time() -> None:
    result = resolve_fact([fact()], "revenue", t("2023-05-01T19:59:59"))
    assert result.status == FactStatus.MISSING


def test_amendment_is_versioned_and_only_visible_after_acceptance() -> None:
    original = fact(value=100, accession="0001", accepted_at=t("2023-05-01T20:00:00"))
    amended = fact(value=90, accession="0002", form="10-Q/A", accepted_at=t("2023-06-01T20:00:00"))
    assert resolve_fact([original, amended], "revenue", t("2023-05-15T00:00:00")).fact == original
    assert resolve_fact([original, amended], "revenue", t("2023-06-02T00:00:00")).fact == amended


def test_primary_tag_precedes_fallback_at_same_period() -> None:
    fallback = fact(tag="Revenues", value=100)
    primary = fact(tag="RevenueFromContractWithCustomerExcludingAssessedTax", value=101)
    assert resolve_fact([fallback, primary], "revenue", t("2023-05-02T00:00:00")).fact == primary


def test_conflicting_duplicate_is_ambiguous() -> None:
    one = fact(value=100)
    two = fact(value=101)
    result = resolve_fact([one, two], "revenue", t("2023-05-02T00:00:00"))
    assert result.status == FactStatus.AMBIGUOUS


def test_companyfacts_requires_acceptance_join_and_correct_unit_and_period() -> None:
    document = {"facts": {"us-gaap": {"Revenues": {"units": {
        "USD": [
            {"val": 12, "start": "2023-01-01", "end": "2023-03-31", "filed": "2023-05-01",
             "accn": "known", "form": "10-Q", "fy": 2023, "fp": "Q1"},
            {"val": 13, "start": "2023-01-01", "end": "2023-03-31", "filed": "2023-05-01",
             "accn": "no_acceptance", "form": "10-Q"},
        ],
        "shares": [{"val": 99, "start": "2023-01-01", "end": "2023-03-31",
                    "filed": "2023-05-01", "accn": "known", "form": "10-Q"}],
    }}}}}
    rows = extract_companyfacts(document, {"known": t("2023-05-01T20:00:00")})
    assert [(row.accession, row.unit, row.period_type) for row in rows] == [("known", "USD", "duration")]


def test_ytd_cash_flow_remains_duration_not_single_quarter() -> None:
    row = fact(field="operating_cash_flow", tag="NetCashProvidedByUsedInOperatingActivities",
               start=date(2023, 1, 1), end=date(2023, 9, 30), fiscal_period="Q3")
    assert row.period_type == "duration"
    assert (row.end - row.start).days > 180


def test_missing_fields_are_explicit() -> None:
    coverage = canonical_coverage([fact()], t("2023-05-02T00:00:00"))
    assert coverage["revenue"] == "OK"
    assert coverage["assets"] == "MISSING"


def test_ticker_cik_mapping_is_pit_and_ambiguous_mapping_fails() -> None:
    records = [MappingRecord("OLD", "1", t("2020-01-01T00:00:00"), t("2022-01-01T00:00:00"),
                             t("2020-01-01T00:00:00")),
               MappingRecord("NEW", "1", t("2022-01-01T00:00:00"), None,
                             t("2022-01-01T00:00:00"))]
    assert ticker_to_cik_at(records, "OLD", t("2021-01-01T00:00:00")) == "1"
    assert ticker_to_cik_at(records, "OLD", t("2023-01-01T00:00:00")) is None
    duplicate = records + [MappingRecord("NEW", "2", t("2022-01-01T00:00:00"), None,
                                         t("2022-01-01T00:00:00"))]
    with pytest.raises(ValueError, match="ambiguous"):
        ticker_to_cik_at(duplicate, "NEW", t("2023-01-01T00:00:00"))


def test_market_cap_rejects_future_shares() -> None:
    assert market_cap_at(10, 1_000_000, close_known_at=t("2023-01-02T21:00:00"),
                         shares_known_at=t("2023-02-01T21:00:00"),
                         decision_time=t("2023-01-03T00:00:00")) is None
    assert market_cap_at(10, 1_000_000, close_known_at=t("2023-01-02T21:00:00"),
                         shares_known_at=t("2023-01-02T20:00:00"),
                         decision_time=t("2023-01-03T00:00:00")) == 10_000_000
