from datetime import datetime

import pytest

from app.core.exceptions import DataError
from app.market.fake import FakeMarketDataProvider
from app.scanner.domain import ExclusionReason
from app.scanner.scanner import QuantScanner
from app.scanner.metrics import average_dollar_volume, relative_strength, return_over_lookback
from tests.scanner_fixtures import (
    ET,
    SCAN_AS_OF,
    TRADING_DATE,
    history,
    scanner_providers,
    symbol_metadata,
)


def test_filters_and_exclusion_reasons() -> None:
    symbols = ["INACTIVE", "LOWCAP", "SHORT", "LOWPRICE", "ILLIQUID", "GOOD"]
    bars = {
        "SHORT": history("SHORT", count=20),
        "LOWPRICE": history("LOWPRICE", base_close=4.0),
        "ILLIQUID": history("ILLIQUID", base_close=10.0, historical_volume=100_000),
    }
    metadata = {
        "INACTIVE": symbol_metadata("INACTIVE", active=False),
        "LOWCAP": symbol_metadata("LOWCAP", market_cap=299_999_999),
    }
    market, reference = scanner_providers(
        symbols, bars_by_symbol=bars, metadata_by_symbol=metadata
    )
    result = QuantScanner(market, reference).scan(
        symbols, trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF
    )
    reasons = {item.symbol: item.reason for item in result.excluded}
    assert [item.symbol for item in result.candidates] == ["GOOD"]
    assert reasons == {
        "ILLIQUID": ExclusionReason.LOW_LIQUIDITY,
        "INACTIVE": ExclusionReason.INACTIVE,
        "LOWCAP": ExclusionReason.MARKET_CAP_TOO_LOW,
        "LOWPRICE": ExclusionReason.PRICE_TOO_LOW,
        "SHORT": ExclusionReason.INSUFFICIENT_HISTORY,
    }


def test_stock_point_in_time_bar_is_not_used_before_available() -> None:
    delayed = datetime(2024, 7, 1, 9, 0, tzinfo=ET)
    market, reference = scanner_providers(
        ["AAA"], bars_by_symbol={"AAA": history("AAA", latest_available_at=delayed)}
    )
    scanner = QuantScanner(market, reference)
    before = scanner.scan(
        ["AAA"],
        trading_date=TRADING_DATE,
        scan_as_of=datetime(2024, 7, 1, 8, 30, tzinfo=ET),
    )
    after = scanner.scan(
        ["AAA"],
        trading_date=TRADING_DATE,
        scan_as_of=datetime(2024, 7, 1, 9, 1, tzinfo=ET),
    )
    assert before.excluded[0].reason is ExclusionReason.INSUFFICIENT_HISTORY
    assert after.candidate_count == 1


def test_benchmark_point_in_time_is_enforced() -> None:
    delayed = datetime(2024, 7, 1, 9, 0, tzinfo=ET)
    spy = history("SPY", latest_available_at=delayed)
    market, reference = scanner_providers(["AAA"])
    market = FakeMarketDataProvider(daily_bars=[*history("AAA"), *spy])
    scanner = QuantScanner(market, reference)
    with pytest.raises(DataError, match="Benchmark SPY"):
        scanner.scan(
            ["AAA"],
            trading_date=TRADING_DATE,
            scan_as_of=datetime(2024, 7, 1, 8, 30, tzinfo=ET),
        )
    assert scanner.scan(
        ["AAA"],
        trading_date=TRADING_DATE,
        scan_as_of=datetime(2024, 7, 1, 9, 1, tzinfo=ET),
    ).candidate_count == 1


def test_ranking_tie_is_symbol_ascending_and_deterministic() -> None:
    symbols = ["ZZZ", "AAA", "MMM"]
    market, reference = scanner_providers(symbols)
    scanner = QuantScanner(market, reference)
    first = scanner.scan(symbols, trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF)
    second = scanner.scan(reversed(symbols), trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF)
    assert first == second
    assert [(item.rank, item.symbol) for item in first.candidates] == [
        (1, "AAA"), (2, "MMM"), (3, "ZZZ")
    ]


def test_market_context_metadata_and_daily_snapshot_are_preserved() -> None:
    bars = history("AAA", base_close=100.0, latest_close=105.0, latest_volume=456_789)
    market, reference = scanner_providers(["AAA"], bars_by_symbol={"AAA": bars})
    result = QuantScanner(market, reference).scan(
        ["AAA"], trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF
    )
    candidate = result.candidates[0]
    parts = candidate.score_components()
    assert parts["company_name"] == "AAA Corp"
    assert parts["exchange"] == "NASDAQ"
    assert parts["previous_open"] == 105.0
    assert parts["previous_high"] == 106.0
    assert parts["previous_low"] == 104.0
    assert parts["latest_close"] == 105.0
    assert parts["latest_volume"] == 456_789
    assert parts["previous_return_pct"] == pytest.approx(0.05)
    assert result.trading_date == TRADING_DATE


@pytest.mark.parametrize("size, expected_top", [(7, 7), (8, 8), (9, 8)])
def test_top8_cardinality(size: int, expected_top: int) -> None:
    symbols = [f"S{index:03d}" for index in range(size)]
    market, reference = scanner_providers(symbols)
    result = QuantScanner(market, reference).scan(
        symbols, trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF
    )
    assert len(result.top8) == expected_top
    assert len(result.candidates) == size


def test_deterministic_120_symbol_universe_uses_one_market_query() -> None:
    class CountingProvider(FakeMarketDataProvider):
        calls = 0

        def get_daily_bars(self, symbols, start=None, end=None):  # type: ignore[no-untyped-def]
            self.calls += 1
            return super().get_daily_bars(symbols, start, end)

    symbols = [f"S{index:03d}" for index in range(120)]
    market, reference = scanner_providers(
        symbols,
        bars_by_symbol={
            symbol: history(
                symbol,
                daily_step=(index % 7) * 0.03,
                latest_volume=300_000 + index * 1_000,
            )
            for index, symbol in enumerate(symbols)
        },
    )
    counting = CountingProvider(daily_bars=market.get_daily_bars([*symbols, "SPY"]))
    scanner = QuantScanner(counting, reference)
    first = scanner.scan(symbols, trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF)
    second = scanner.scan(symbols, trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF)
    assert first == second
    assert first.candidate_count == 120
    assert len(first.top8) == 8
    assert counting.calls == 2


def test_middle_missing_trading_day_is_excluded_even_with_21_bars() -> None:
    halted = history("HALTED", count=22)
    del halted[-6]
    market, reference = scanner_providers(
        ["GOOD", "HALTED"], bars_by_symbol={"HALTED": halted}
    )
    result = QuantScanner(market, reference).scan(
        ["GOOD", "HALTED"], trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF
    )
    assert [candidate.symbol for candidate in result.candidates] == ["GOOD"]
    assert result.excluded[0].symbol == "HALTED"
    assert result.excluded[0].reason is ExclusionReason.MISALIGNED_HISTORY


def test_duplicate_daily_bar_uses_latest_pit_correction_without_shifting_windows() -> None:
    original = history("AAA")
    duplicate = original[-6]
    correction = duplicate.model_copy(update={
        "close": duplicate.close + 10.0,
        "high": duplicate.high + 10.0,
        "observed_at": duplicate.observed_at.replace(minute=2),
        "available_at": duplicate.available_at.replace(minute=3),
    })
    market, reference = scanner_providers(
        ["AAA"], bars_by_symbol={"AAA": [*original, correction]}
    )
    scanner = QuantScanner(market, reference)
    first = scanner.scan(["AAA"], trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF)
    second = scanner.scan(["AAA"], trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF)
    assert first == second
    assert first.candidate_count == 1
    metrics = first.candidates[0].raw_metrics
    deduplicated = [bar if bar.trading_date != correction.trading_date else correction for bar in original]
    spy = history("SPY", daily_step=0.1)
    assert metrics.rvol == 1.0
    assert metrics.relative_strength == relative_strength(deduplicated, spy, 5)
    assert metrics.momentum == return_over_lookback(deduplicated, 20)
    assert metrics.average_dollar_volume == average_dollar_volume(deduplicated, 20)


def test_benchmark_is_used_but_never_ranked_as_candidate() -> None:
    market, reference = scanner_providers(["SPY", "AAA"])
    result = QuantScanner(market, reference).scan(
        ["SPY", "AAA"], trading_date=TRADING_DATE, scan_as_of=SCAN_AS_OF
    )
    assert [candidate.symbol for candidate in result.candidates] == ["AAA"]
    assert result.excluded[0].symbol == "SPY"
    assert result.excluded[0].reason is ExclusionReason.BENCHMARK_SYMBOL
