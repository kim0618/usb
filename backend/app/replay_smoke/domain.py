"""Small result domain for replay smoke validation (not performance research)."""

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ScannerRunSummary:
    trading_date: date
    scan_trading_date: date
    universe_count: int
    candidate_count: int
    top8_count: int
    top8_symbols: tuple[str, ...]
    ranks: tuple[int, ...]
    scores: tuple[float, ...]


@dataclass(frozen=True)
class PathResult:
    trading_date: date
    symbol: str
    variant: str
    rank: int
    status: str
    gross_pnl: Decimal = Decimal("0")
    net_pnl: Decimal = Decimal("0")
    gross_r: Decimal = Decimal("0")
    net_r: Decimal = Decimal("0")
    spread_cost: Decimal = Decimal("0")
    slippage_cost: Decimal = Decimal("0")
    commission: Decimal = Decimal("0")
    fx_cost: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")
    ambiguous_bar_count: int = 0
    holding_minutes: int = 0
    overnight: bool = False
    pyramid: bool = False
    exit_reason: str | None = None


@dataclass(frozen=True)
class DailyResult:
    trading_date: date
    candidate_count: int
    top8_count: int
    shadow_paths: int
    trades: int
    no_trade: int
    unfilled: int
    rejected: int
    overnight_carried: int
    day2_exits: int
    ambiguous_count: int


@dataclass(frozen=True)
class VariantSummary:
    variant: str
    candidate_paths: int
    trades: int
    no_trade: int
    unfilled: int
    rejected: int
    wins: int
    losses: int
    breakeven: int
    gross_pnl: Decimal
    net_pnl: Decimal
    gross_r_sum: Decimal
    net_r_sum: Decimal
    average_net_r: Decimal | None
    median_net_r: Decimal | None
    positive_r_rate: Decimal | None
    spread_cost: Decimal
    slippage_cost: Decimal
    commission: Decimal
    fx_cost: Decimal
    total_cost: Decimal
    ambiguous_bar_count: int
    ambiguous_trade_count: int
    overnight_count: int
    pyramid_count: int
    average_holding_minutes: Decimal | None


@dataclass(frozen=True)
class ReplaySmokeResult:
    run_id: str
    start_date: date
    end_date: date
    trading_days: tuple[date, ...]
    universe: tuple[str, ...]
    scanner_runs: tuple[ScannerRunSummary, ...]
    shadow_results: tuple[PathResult, ...]
    daily_results: tuple[DailyResult, ...]
    variant_summaries: tuple[VariantSummary, ...]
    starting_research_capital: Decimal
    account_policy: str
    execution_version: str
    strategy_version: str
    risk_version: str
    shadow_variant_version: str
    invariant_results: dict[str, bool]
    determinism_result: bool | None = None
    order_independence_result: bool | None = None
    warnings: tuple[str, ...] = ()

    @property
    def canonical(self) -> dict[str, Any]:
        payload = asdict(self)
        for ignored in ("run_id", "determinism_result", "order_independence_result"):
            payload.pop(ignored, None)
        return _jsonable(payload)

    def to_report(self) -> dict[str, Any]:
        payload = _jsonable(asdict(self))
        payload["statement"] = "Synthetic Smoke results are implementation validation only."
        payload["totals"] = {
            "trading_days": len(self.trading_days), "scanner_runs": len(self.scanner_runs),
            "top8_candidates": sum(item.top8_count for item in self.scanner_runs),
            "shadow_paths": len(self.shadow_results),
            "trades": sum(item.trades for item in self.daily_results),
            "no_trade": sum(item.no_trade for item in self.daily_results),
            "unfilled": sum(item.unfilled for item in self.daily_results),
            "rejected": sum(item.rejected for item in self.daily_results),
            "orphan_states": 0 if self.invariant_results.get("no_orphan") else 1,
            "invariant_violations": sum(not value for value in self.invariant_results.values()),
        }
        return payload


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
