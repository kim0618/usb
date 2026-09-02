"""Versioned Quant Scanner V0 parameters."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ScannerConfig:
    score_version: str = "quant_v0"
    benchmark_symbol: str = "SPY"
    minimum_price: float = 5.0
    minimum_market_cap: float = 300_000_000.0
    minimum_average_dollar_volume: float = 20_000_000.0
    volume_lookback: int = 20
    relative_strength_lookback: int = 5
    dollar_volume_lookback: int = 20
    momentum_lookback: int = 20
    winsor_lower_percentile: float = 5.0
    winsor_upper_percentile: float = 95.0
    rvol_weight: float = 0.35
    relative_strength_weight: float = 0.30
    dollar_volume_weight: float = 0.20
    momentum_weight: float = 0.15
    top_count: int = 8

    def __post_init__(self) -> None:
        if not self.score_version.strip():
            raise ValueError("score_version must not be empty")
        object.__setattr__(self, "benchmark_symbol", self.benchmark_symbol.strip().upper())
        if not self.benchmark_symbol:
            raise ValueError("benchmark_symbol must not be empty")
        if not 0 <= self.winsor_lower_percentile < self.winsor_upper_percentile <= 100:
            raise ValueError("winsor percentiles are invalid")
        if any(value <= 0 for value in (
            self.volume_lookback,
            self.relative_strength_lookback,
            self.dollar_volume_lookback,
            self.momentum_lookback,
            self.top_count,
        )):
            raise ValueError("lookbacks and top_count must be positive")
        if abs(sum(self.weights.values()) - 1.0) > 1e-12:
            raise ValueError("scanner weights must sum to 1.0")

    @property
    def weights(self) -> dict[str, float]:
        return {
            "rvol": self.rvol_weight,
            "relative_strength": self.relative_strength_weight,
            "dollar_volume": self.dollar_volume_weight,
            "momentum": self.momentum_weight,
        }

    @property
    def required_history(self) -> int:
        return max(
            self.volume_lookback + 1,
            self.relative_strength_lookback + 1,
            self.dollar_volume_lookback + 1,
            self.momentum_lookback + 1,
        )

