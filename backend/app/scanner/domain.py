"""Quant Scanner result domain objects."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class ExclusionReason(StrEnum):
    MISSING_METADATA = "MISSING_METADATA"
    INACTIVE = "INACTIVE"
    PRICE_TOO_LOW = "PRICE_TOO_LOW"
    MARKET_CAP_TOO_LOW = "MARKET_CAP_TOO_LOW"
    MISSING_MARKET_CAP = "MISSING_MARKET_CAP"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    INVALID_MARKET_DATA = "INVALID_MARKET_DATA"
    MISALIGNED_HISTORY = "MISALIGNED_HISTORY"
    BENCHMARK_SYMBOL = "BENCHMARK_SYMBOL"


class ScannerRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class RawMetrics:
    rvol: float
    relative_strength: float
    dollar_volume: float
    momentum: float
    average_dollar_volume: float


@dataclass(frozen=True)
class RankedCandidate:
    rank: int
    symbol: str
    final_score: float
    raw_metrics: RawMetrics
    normalized_metrics: dict[str, float]
    weighted_contributions: dict[str, float]
    market_cap: float
    latest_close: float
    latest_volume: int
    company_name: str | None
    exchange: str | None
    previous_open: float | None
    previous_high: float | None
    previous_low: float | None
    previous_return_pct: float | None
    average_dollar_volume: float
    observed_at: datetime
    available_at: datetime
    is_top8: bool

    def score_components(self) -> dict[str, Any]:
        return {
            "raw": {
                "rvol": self.raw_metrics.rvol,
                "relative_strength": self.raw_metrics.relative_strength,
                "dollar_volume": self.raw_metrics.dollar_volume,
                "momentum": self.raw_metrics.momentum,
                "average_dollar_volume": self.raw_metrics.average_dollar_volume,
            },
            "normalized": dict(self.normalized_metrics),
            "weighted_contributions": dict(self.weighted_contributions),
            "final_score": self.final_score,
            "market_cap": self.market_cap,
            "latest_close": self.latest_close,
            "latest_volume": self.latest_volume,
            "company_name": self.company_name,
            "exchange": self.exchange,
            "previous_open": self.previous_open,
            "previous_high": self.previous_high,
            "previous_low": self.previous_low,
            "previous_return_pct": self.previous_return_pct,
        }


@dataclass(frozen=True)
class ExcludedSymbol:
    symbol: str
    reason: ExclusionReason


@dataclass(frozen=True)
class ScannerResult:
    trading_date: date
    scan_as_of: datetime
    score_version: str
    universe_count: int
    candidates: tuple[RankedCandidate, ...]
    excluded: tuple[ExcludedSymbol, ...]

    @property
    def top8(self) -> tuple[RankedCandidate, ...]:
        return tuple(candidate for candidate in self.candidates if candidate.is_top8)

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded)
