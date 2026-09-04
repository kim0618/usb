"""Pure-ish Quant Scanner V0 calculation pipeline."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite

from app.core.exceptions import DataError
from app.market.domain import DailyBar
from app.market.provider import MarketDataProvider
from app.market.reference import SymbolMetadata, SymbolMetadataProvider
from app.market.symbols import normalize_symbol
from app.scanner.config import ScannerConfig
from app.scanner.domain import (
    ExcludedSymbol,
    ExclusionReason,
    RankedCandidate,
    RawMetrics,
    ScannerResult,
)
from app.scanner.metrics import (
    average_dollar_volume,
    latest_dollar_volume,
    relative_strength,
    return_over_lookback,
    rvol,
)
from app.scanner.normalization import normalize_metrics


@dataclass(frozen=True)
class _EligibleCandidate:
    symbol: str
    metadata: SymbolMetadata
    bars: tuple[DailyBar, ...]
    raw: RawMetrics
    observed_at: datetime
    available_at: datetime


class QuantScanner:
    def __init__(
        self,
        market_data: MarketDataProvider,
        metadata: SymbolMetadataProvider,
        config: ScannerConfig | None = None,
    ) -> None:
        self.market_data = market_data
        self.metadata = metadata
        self.config = config or ScannerConfig()

    def scan(
        self,
        universe: Sequence[str],
        *,
        trading_date: date,
        scan_as_of: datetime,
    ) -> ScannerResult:
        if scan_as_of.tzinfo is None or scan_as_of.utcoffset() is None:
            raise DataError("scan_as_of must be timezone-aware")
        try:
            symbols = sorted({normalize_symbol(symbol) for symbol in universe})
        except ValueError as exc:
            raise DataError("Universe contains an invalid symbol") from exc
        metadata = self.metadata.get_metadata(symbols, scan_as_of)
        query_symbols = sorted(set(symbols) | {self.config.benchmark_symbol})
        all_bars = self.market_data.get_daily_bars(query_symbols, end=trading_date)
        bars_by_symbol = self._point_in_time_bars(all_bars, trading_date, scan_as_of)
        benchmark = self._benchmark_bars(bars_by_symbol, trading_date)

        eligible: list[_EligibleCandidate] = []
        excluded: list[ExcludedSymbol] = []
        for symbol in symbols:
            candidate, reason = self._evaluate_symbol(
                symbol, metadata.get(symbol), bars_by_symbol.get(symbol, ()), benchmark, trading_date
            )
            if candidate is not None:
                eligible.append(candidate)
            else:
                if reason is None:
                    raise AssertionError("Excluded scanner symbol requires a reason")
                excluded.append(ExcludedSymbol(symbol=symbol, reason=reason))

        ranked = self._normalize_and_rank(eligible)
        return ScannerResult(
            trading_date=trading_date,
            scan_as_of=scan_as_of,
            score_version=self.config.score_version,
            universe_count=len(symbols),
            candidates=tuple(ranked),
            excluded=tuple(excluded),
        )

    @staticmethod
    def _point_in_time_bars(
        bars: Sequence[DailyBar], trading_date: date, scan_as_of: datetime
    ) -> dict[str, tuple[DailyBar, ...]]:
        grouped: dict[str, dict[date, DailyBar]] = defaultdict(dict)
        for bar in bars:
            if bar.trading_date <= trading_date and bar.available_at <= scan_as_of:
                existing = grouped[bar.symbol].get(bar.trading_date)
                if existing is None or QuantScanner._duplicate_priority(bar) > QuantScanner._duplicate_priority(existing):
                    grouped[bar.symbol][bar.trading_date] = bar
        return {
            symbol: tuple(sorted(items.values(), key=lambda bar: bar.trading_date))
            for symbol, items in grouped.items()
        }

    @staticmethod
    def _duplicate_priority(bar: DailyBar) -> tuple[object, ...]:
        return (
            bar.available_at, bar.observed_at, bar.open, bar.high, bar.low, bar.close, bar.volume
        )

    def _benchmark_bars(
        self, bars_by_symbol: dict[str, tuple[DailyBar, ...]], trading_date: date
    ) -> tuple[DailyBar, ...]:
        bars = bars_by_symbol.get(self.config.benchmark_symbol, ())
        required = self.config.required_history
        if len(bars) < required or not bars or bars[-1].trading_date != trading_date:
            raise DataError(
                f"Benchmark {self.config.benchmark_symbol} lacks point-in-time history"
            )
        return bars

    def _evaluate_symbol(
        self,
        symbol: str,
        metadata: SymbolMetadata | None,
        bars: tuple[DailyBar, ...],
        benchmark: tuple[DailyBar, ...],
        trading_date: date,
    ) -> tuple[_EligibleCandidate | None, ExclusionReason | None]:
        if metadata is None:
            return None, ExclusionReason.MISSING_METADATA
        if symbol == self.config.benchmark_symbol:
            return None, ExclusionReason.BENCHMARK_SYMBOL
        if not metadata.active:
            return None, ExclusionReason.INACTIVE
        if metadata.market_cap is None:
            return None, ExclusionReason.MISSING_MARKET_CAP
        if metadata.market_cap < self.config.minimum_market_cap:
            return None, ExclusionReason.MARKET_CAP_TOO_LOW
        if len(bars) < self.config.required_history or bars[-1].trading_date != trading_date:
            return None, ExclusionReason.INSUFFICIENT_HISTORY
        required_bars = bars[-self.config.required_history:]
        benchmark_grid = benchmark[-self.config.required_history:]
        if tuple(bar.trading_date for bar in required_bars) != tuple(
            bar.trading_date for bar in benchmark_grid
        ):
            return None, ExclusionReason.MISALIGNED_HISTORY
        latest = bars[-1]
        if latest.close < self.config.minimum_price:
            return None, ExclusionReason.PRICE_TOO_LOW
        try:
            average_dv = average_dollar_volume(bars, self.config.dollar_volume_lookback)
            if average_dv < self.config.minimum_average_dollar_volume:
                return None, ExclusionReason.LOW_LIQUIDITY
            raw = RawMetrics(
                rvol=rvol(bars, self.config.volume_lookback),
                relative_strength=relative_strength(
                    required_bars, benchmark_grid, self.config.relative_strength_lookback
                ),
                dollar_volume=latest_dollar_volume(bars),
                momentum=return_over_lookback(required_bars, self.config.momentum_lookback),
                average_dollar_volume=average_dv,
            )
            if not all(isfinite(value) for value in raw.__dict__.values()):
                raise DataError("Metric is not finite")
        except DataError:
            return None, ExclusionReason.INVALID_MARKET_DATA

        dependencies = (*required_bars, *benchmark_grid)
        return _EligibleCandidate(
            symbol=symbol,
            metadata=metadata,
            bars=bars,
            raw=raw,
            observed_at=max(metadata.observed_at, *(bar.observed_at for bar in dependencies)),
            available_at=max(metadata.available_at, *(bar.available_at for bar in dependencies)),
        ), None

    def _normalize_and_rank(
        self, eligible: Sequence[_EligibleCandidate]
    ) -> list[RankedCandidate]:
        raw_by_symbol = {
            item.symbol: {
                "rvol": item.raw.rvol,
                "relative_strength": item.raw.relative_strength,
                "dollar_volume": item.raw.dollar_volume,
                "momentum": item.raw.momentum,
            }
            for item in eligible
        }
        normalized = normalize_metrics(
            raw_by_symbol,
            self.config.winsor_lower_percentile,
            self.config.winsor_upper_percentile,
        )
        scored: list[tuple[_EligibleCandidate, dict[str, float], dict[str, float], float]] = []
        for item in eligible:
            normalized_metrics = normalized[item.symbol]
            contributions = {
                metric: normalized_metrics[metric] * weight
                for metric, weight in self.config.weights.items()
            }
            score = sum(contributions.values())
            scored.append((item, normalized_metrics, contributions, score))
        scored.sort(key=lambda row: (-row[3], -row[1]["rvol"], row[0].symbol))

        results: list[RankedCandidate] = []
        for index, (item, normalized_metrics, contributions, score) in enumerate(scored, start=1):
            latest = item.bars[-1]
            prior = item.bars[-2] if len(item.bars) >= 2 else None
            previous_return_pct = (
                None
                if prior is None or prior.close == 0
                else (latest.close - prior.close) / prior.close
            )
            results.append(
                RankedCandidate(
                    rank=index,
                    symbol=item.symbol,
                    final_score=score,
                    raw_metrics=item.raw,
                    normalized_metrics=dict(normalized_metrics),
                    weighted_contributions=contributions,
                    market_cap=item.metadata.market_cap,
                    latest_close=latest.close,
                    latest_volume=latest.volume,
                    company_name=item.metadata.company_name,
                    exchange=item.metadata.exchange,
                    previous_open=latest.open,
                    previous_high=latest.high,
                    previous_low=latest.low,
                    previous_return_pct=previous_return_pct,
                    average_dollar_volume=item.raw.average_dollar_volume,
                    observed_at=item.observed_at,
                    available_at=item.available_at,
                    is_top8=index <= self.config.top_count,
                )
            )
        return results
