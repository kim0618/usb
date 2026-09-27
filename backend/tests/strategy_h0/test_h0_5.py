from datetime import date, datetime, timedelta, timezone

import pytest

from app.backtest.strategy_h0.facts import CanonicalFact
from app.backtest.strategy_h0.h0_5 import (
    Lane, SecurityInterval, SharesResolution, first_available_session, historical_market_cap,
    is_eligible_common_stock, market_cap_lane, resolve_pit_shares, security_at,
)

UTC = timezone.utc


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def state(**changes) -> SecurityInterval:
    values = dict(security_id="FIGI-1", company_id="0000000001", ticker="OLD",
                  effective_from=date(2020, 1, 1), effective_to=date(2023, 1, 1),
                  exchange="XNAS", security_type="CS", market="stocks", locale="us",
                  known_at=dt("2019-12-01T00:00:00"))
    values.update(changes)
    return SecurityInterval(**values)


def shares(**changes) -> CanonicalFact:
    values = dict(field="shares_outstanding", taxonomy="dei",
                  tag="EntityCommonStockSharesOutstanding", unit="shares", value=100_000_000,
                  start=None, end=date(2021, 5, 1), filed=date(2021, 5, 3),
                  accepted_at=dt("2021-05-03T21:30:00"), accession="one", form="10-Q",
                  fiscal_year=2021, fiscal_period="Q1", frame=None)
    values.update(changes)
    return CanonicalFact(**values)


OPENS = [dt("2021-05-03T13:30:00"), dt("2021-05-04T13:30:00"),
         dt("2021-06-30T13:30:00")]


def test_future_ticker_state_rejected_and_delisted_history_preserved() -> None:
    old = state()
    future = state(ticker="NEW", effective_from=date(2023, 1, 1), effective_to=None,
                   known_at=dt("2023-01-01T00:00:00"))
    assert security_at([old, future], "FIGI-1", date(2021, 6, 30), dt("2021-06-30T21:00:00")) == old
    assert security_at([old, future], "FIGI-1", date(2023, 2, 1), dt("2022-12-31T23:00:00")) is None


def test_post_delisting_excluded() -> None:
    old = state()
    assert is_eligible_common_stock(security_at([old], "FIGI-1", date(2022, 12, 30),
                                                dt("2022-12-30T21:00:00")))
    assert security_at([old], "FIGI-1", date(2023, 1, 1), dt("2023-01-02T00:00:00")) is None


def test_ticker_change_preserves_security_identity() -> None:
    rows = [state(), state(ticker="NEW", effective_from=date(2023, 1, 1), effective_to=None,
                           known_at=dt("2022-12-01T00:00:00"))]
    assert security_at(rows, "FIGI-1", date(2022, 1, 1), dt("2022-01-01T00:00:00")).ticker == "OLD"
    assert security_at(rows, "FIGI-1", date(2023, 1, 2), dt("2023-01-02T21:00:00")).ticker == "NEW"


def test_after_hours_filing_available_next_session_and_future_shares_rejected() -> None:
    assert first_available_session(dt("2021-05-03T21:30:00"), OPENS) == date(2021, 5, 4)
    assert resolve_pit_shares([shares()], date(2021, 5, 3), OPENS).fact is None
    assert resolve_pit_shares([shares()], date(2021, 5, 4), OPENS).fact is not None


def test_stale_shares_and_missing_shares_are_unknown() -> None:
    old = shares(end=date(2021, 1, 1), accepted_at=dt("2021-01-04T13:00:00"))
    assert resolve_pit_shares([old], date(2021, 6, 30), OPENS).fact is None
    assert historical_market_cap(10, SharesResolution(None, "missing")) is None


def test_split_inconsistency_rejected() -> None:
    result = resolve_pit_shares([shares()], date(2021, 6, 30), OPENS,
                                split_dates=[date(2021, 6, 1)])
    assert result.fact is None


def test_market_cap_and_current_fallback_impossible() -> None:
    resolved = SharesResolution(shares(value=200_000_000), "ok")
    assert historical_market_cap(10, resolved) == 2_000_000_000
    assert historical_market_cap(None, resolved) is None
    with pytest.raises(TypeError):
        historical_market_cap(10, resolved, current_market_cap=3_000_000_000)  # type: ignore[call-arg]


@pytest.mark.parametrize(("value", "lane"), [
    (499_999_999, Lane.MICRO), (500_000_000, Lane.H_SMALL),
    (1_999_999_999, Lane.H_SMALL), (2_000_000_000, Lane.H_MID),
    (9_999_999_999, Lane.H_MID), (10_000_000_000, Lane.H_UPPER_MID),
    (49_999_999_999, Lane.H_UPPER_MID), (50_000_000_000, Lane.H_LARGE),
    (None, Lane.UNKNOWN),
])
def test_lane_boundaries(value, lane) -> None:
    assert market_cap_lane(value) is lane


def test_weighted_average_and_multiclass_are_rejected() -> None:
    weighted = shares(tag="WeightedAverageNumberOfSharesOutstanding")
    assert resolve_pit_shares([weighted], date(2021, 5, 4), OPENS).fact is None
    assert resolve_pit_shares([shares()], date(2021, 5, 4), OPENS,
                              multiple_share_classes=True).fact is None
