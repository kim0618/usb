"""One session of the mover scanner: eligibility, features, score, pool, output.

The order is fixed and every step is recorded. A symbol that leaves the universe leaves with a
named reason, and the only reasons are data quality or executability: no tape, a split
executing that morning, no premarket print, no previous close, a sub-dollar price, no volume
baseline. Nothing is removed for being small, for being volatile, or for having a large gap.

Strategy A's premarket gate is also evaluated here, on its own window and with the deployed
``StrategyConfig`` thresholds read rather than copied. It changes no score and admits nothing;
it exists so this study can count how many of the symbols handed to GPT the live gate could
actually have traded. That count is the starvation measurement, and it is the reason the
scanner was rewritten.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.backtest.mover_scanner_v1 import score as S
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.backtest.mover_scanner_v1.daily import DailyPanel
from app.backtest.mover_scanner_v1.premarket import PremarketPanel
from app.strategy.config import GapDirection, StrategyConfig


class Rejection(StrEnum):
    """Why a universe member produced no candidate. Data and executability only."""

    NO_TAPE_COVERAGE = "NO_TAPE_COVERAGE"
    SPLIT_EXECUTES_THIS_SESSION = "SPLIT_EXECUTES_THIS_SESSION"
    NO_PREMARKET_PRINT = "NO_PREMARKET_PRINT"
    TOO_FEW_PREMARKET_PRINTS = "TOO_FEW_PREMARKET_PRINTS"
    PREMARKET_DOLLAR_VOLUME_TOO_LOW = "PREMARKET_DOLLAR_VOLUME_TOO_LOW"
    NO_PREVIOUS_REGULAR_CLOSE = "NO_PREVIOUS_REGULAR_CLOSE"
    PRICE_TOO_LOW = "PRICE_TOO_LOW"
    NO_DAILY_VOLUME_BASELINE = "NO_DAILY_VOLUME_BASELINE"
    NO_PREMARKET_RVOL_BASELINE = "NO_PREMARKET_RVOL_BASELINE"
    INVALID_FEATURE = "INVALID_FEATURE"


@dataclass(frozen=True)
class GateReading:
    """What Strategy A's deployed premarket gate would see at the open. Never scored."""

    gap_pct: float | None
    volume_ratio: float | None
    gap_pass: bool
    volume_pass: bool

    @property
    def both_pass(self) -> bool:
        return self.gap_pass and self.volume_pass


@dataclass(frozen=True)
class MoverCandidate:
    """One eligible symbol on one session, with everything the output contract names."""

    session_date: date
    symbol: str
    gap_pct: float
    pm_bars: int
    pm_volume: float
    pm_dollar_volume: float
    pm_rvol: float
    pm_momentum: float
    pm_range: float
    last_price: float
    previous_close: float
    adv20_shares: float
    addv20_dollar: float
    rvol_baseline_median: float
    rvol_baseline_sessions: int
    gap_quality: float
    tradability_score: float
    momentum_detail: Mapping[str, float]
    tradability_detail: Mapping[str, float]
    gate: GateReading
    raw_components: Mapping[str, float]
    normalized_components: Mapping[str, float] = field(default_factory=dict)
    component_scores: Mapping[str, float] = field(default_factory=dict)
    total_score: float = 0.0
    pool_score: float = 0.0
    candidate_pool_rank: int | None = None
    rank: int | None = None

    def row(self, scan_time: str) -> dict[str, Any]:
        """The declared output record (section L), as plain JSON data."""
        return {
            "session_date": self.session_date.isoformat(),
            "scan_time": scan_time,
            "rank": self.rank,
            "symbol": self.symbol,
            "gap_pct": self.gap_pct,
            "pm_volume": self.pm_volume,
            "pm_dollar_volume": self.pm_dollar_volume,
            "pm_rvol": self.pm_rvol,
            "pm_momentum": self.pm_momentum,
            "pm_range": self.pm_range,
            "tradability_score": self.tradability_score,
            "component_scores": dict(self.component_scores),
            "total_score": self.total_score,
            "candidate_pool_rank": self.candidate_pool_rank,
            "pm_bars": self.pm_bars,
            "last_price": self.last_price,
            "previous_close": self.previous_close,
            "gap_quality": self.gap_quality,
            "adv20_shares": self.adv20_shares,
            "addv20_dollar": self.addv20_dollar,
            "pm_rvol_baseline_median": self.rvol_baseline_median,
            "pm_rvol_baseline_sessions": self.rvol_baseline_sessions,
            "raw_components": dict(self.raw_components),
            "normalized_components": dict(self.normalized_components),
            "momentum_detail": dict(self.momentum_detail),
            "tradability_detail": dict(self.tradability_detail),
            "entry_gate": {
                "gap_pct": self.gate.gap_pct, "volume_ratio": self.gate.volume_ratio,
                "gap_pass": self.gate.gap_pass, "volume_pass": self.gate.volume_pass,
                "both_pass": self.gate.both_pass,
            },
        }


@dataclass(frozen=True)
class SessionScan:
    session_date: date
    evaluated: int
    eligible: int
    rejections: Mapping[str, int]
    pool: tuple[MoverCandidate, ...]
    top: tuple[MoverCandidate, ...]
    dominance: Mapping[str, float]


def gate_reading(gate_last_price: float | None, gate_volume: float | None,
                 previous_close: float, adv20_shares: float,
                 strategy: StrategyConfig) -> GateReading:
    """``PremarketContext``'s own arithmetic, on the gate's own window.

    The gate reads the last premarket print before the open and the whole premarket volume,
    divided by the mean of the last 20 daily volumes. Thresholds are read from the deployed
    config so this measurement cannot drift away from the gate it is measuring.
    """
    if gate_last_price is None or gate_volume is None or previous_close <= 0 or adv20_shares <= 0:
        return GateReading(None, None, False, False)
    gap = Decimal(repr(gate_last_price)) / Decimal(repr(previous_close)) - Decimal("1")
    ratio = Decimal(repr(gate_volume)) / Decimal(repr(adv20_shares))
    if strategy.premarket_gap_direction is not GapDirection.UP:
        raise ValueError("the measured gate is the paper UP rule; a changed direction needs review")
    gap_pass = strategy.premarket_gap_min_pct <= gap <= strategy.premarket_gap_max_pct
    volume_pass = ratio >= strategy.premarket_volume_ratio_min
    return GateReading(float(gap), float(ratio), bool(gap_pass), bool(volume_pass))


def _candidate(symbol: str, session: date, premarket: PremarketPanel, symbol_row: int,
               session_column: int, daily: DailyPanel, daily_position: int,
               config: MoverScannerConfig, strategy: StrategyConfig,
               ) -> tuple[MoverCandidate | None, Rejection | None]:
    read = lambda name: premarket.field(name, symbol_row, session_column)  # noqa: E731
    bars = read("pm_bars")
    if not S.finite(bars) or bars <= 0:
        return None, Rejection.NO_PREMARKET_PRINT
    if bars < config.minimum_premarket_bars:
        return None, Rejection.TOO_FEW_PREMARKET_PRINTS
    dollar_volume = read("pm_dollar_volume")
    if not S.finite(dollar_volume) or dollar_volume < config.minimum_premarket_dollar_volume:
        return None, Rejection.PREMARKET_DOLLAR_VOLUME_TOO_LOW
    previous_close = daily.previous_close(symbol, daily_position)
    if previous_close is None:
        return None, Rejection.NO_PREVIOUS_REGULAR_CLOSE
    last_price = read("pm_last_price")
    if not S.finite(last_price) or last_price < config.minimum_price:
        return None, Rejection.PRICE_TOO_LOW
    adv20, addv20, baseline_sessions = daily.baselines(symbol, daily_position,
                                                       config.daily_baseline_sessions)
    if not adv20 or not addv20 or adv20 <= 0 or addv20 <= 0:
        return None, Rejection.NO_DAILY_VOLUME_BASELINE
    median, used = premarket.rvol_baseline(symbol_row, session_column,
                                           config.premarket_rvol_baseline_sessions)
    if median is None:
        return None, Rejection.NO_PREMARKET_RVOL_BASELINE

    volume = read("pm_volume")
    high, low = read("pm_high"), read("pm_low")
    reference = read("pm_late_reference_price")
    if not S.finite(volume, high, low, reference):
        return None, Rejection.INVALID_FEATURE
    gap = last_price / previous_close - 1.0
    rvol = volume / max(median, config.premarket_rvol_baseline_floor_shares)
    momentum = S.momentum_parts(last_price, reference, high, low, read("pm_up_bar_share"), config)
    trade = S.tradability(last_price, addv20, bars, config)
    quality = S.gap_quality(gap, config)
    raw = {"pm_dollar_volume": dollar_volume, "pm_rvol": rvol, "gap_quality": quality,
           "pm_momentum": momentum["pm_momentum"], "tradability": trade["tradability_score"]}
    if not S.finite(*raw.values(), gap):
        return None, Rejection.INVALID_FEATURE
    gate_price = read("gate_last_price")
    gate_volume = read("gate_volume")
    return MoverCandidate(
        session_date=session, symbol=symbol, gap_pct=gap, pm_bars=int(bars), pm_volume=volume,
        pm_dollar_volume=dollar_volume, pm_rvol=rvol, pm_momentum=momentum["pm_momentum"],
        pm_range=(high - low) / previous_close, last_price=last_price,
        previous_close=previous_close, adv20_shares=adv20, addv20_dollar=addv20,
        rvol_baseline_median=median, rvol_baseline_sessions=used, gap_quality=quality,
        tradability_score=trade["tradability_score"], momentum_detail=momentum,
        tradability_detail=trade,
        gate=gate_reading(gate_price if S.finite(gate_price) else None,
                          gate_volume if S.finite(gate_volume) else None,
                          previous_close, adv20, strategy),
        raw_components=raw), None


def scan_session(session: date, symbols: Sequence[str], premarket: PremarketPanel,
                 daily: DailyPanel, config: MoverScannerConfig,
                 strategy: StrategyConfig | None = None) -> SessionScan:
    """Score one session's universe and cut the pool, then the output."""
    strategy = strategy or StrategyConfig()
    symbol_rows = premarket.symbol_index
    session_column = premarket.session_index[session]
    daily_position = daily.index[session]
    rejections: dict[str, int] = {}
    candidates: list[MoverCandidate] = []
    for symbol in symbols:
        row = symbol_rows.get(symbol)
        if row is None or not bool(premarket.covered[row, session_column]):
            rejections[Rejection.NO_TAPE_COVERAGE] = \
                rejections.get(Rejection.NO_TAPE_COVERAGE, 0) + 1
            continue
        candidate, reason = _candidate(symbol, session, premarket, row, session_column,
                                       daily, daily_position, config, strategy)
        if candidate is None:
            rejections[str(reason)] = rejections.get(str(reason), 0) + 1
        else:
            candidates.append(candidate)

    if not candidates:
        return SessionScan(session, len(symbols), 0, rejections, (), (), {})
    raw_by_symbol = {item.symbol: item.raw_components for item in candidates}
    normalized = S.normalize_components(raw_by_symbol, config)
    scored: list[MoverCandidate] = []
    from dataclasses import replace
    for item in candidates:
        row = normalized[item.symbol]
        parts = S.contributions(row, config)
        scored.append(replace(item, normalized_components=row, component_scores=parts,
                              total_score=sum(parts.values()),
                              pool_score=S.pool_score(row, config)))

    # Stage 1: the mover pool, on participation evidence; ties break on the full score then
    # the symbol, so the cut is deterministic.
    scored.sort(key=lambda item: (-item.pool_score, -item.total_score, item.symbol))
    pool = [replace(item, candidate_pool_rank=position)
            for position, item in enumerate(scored[:config.pool_size], start=1)]
    # Stage 2: the output, on the full opportunity score within the pool.
    pool.sort(key=lambda item: (-item.total_score, -item.pool_score, item.symbol))
    top = tuple(replace(item, rank=position)
                for position, item in enumerate(pool[:config.top_count], start=1))
    pool_by_pool_rank = tuple(sorted(pool, key=lambda item: item.candidate_pool_rank or 0))
    return SessionScan(session, len(symbols), len(candidates), rejections,
                       pool_by_pool_rank, top,
                       S.dominance([item.component_scores for item in scored]))
