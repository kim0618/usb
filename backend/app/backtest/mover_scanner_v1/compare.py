"""Scanner quality, measured the same way for both scanners. No returns, no replay, no PnL.

The question is whether the symbols a scanner hands to GPT are symbols Strategy A could trade,
so both arms are described by one premarket feature engine and judged by the deployed entry
gate's own arithmetic. The current arm is the frozen ``historical-daily-top8-v1``
reconstruction: the real ``QuantScanner`` over the real trade-value TOP10, already checksummed,
so nothing about it is recomputed here.

Describing the current arm needs no eligibility rule at all - the point is to report what its
picks look like, including when a pick has no premarket print to speak of - so
``describe_features`` applies no floor and returns None for anything undefined.

"Mega-cap share" is reported on two authorities, because the store has dated market caps for a
few hundred symbols only. The primary measure is a liquidity one that exists for every symbol:
where the pick sits in the cross-sectional distribution of 20-session average dollar volume.
That is also the property that produced the problem, since the current universe is a dollar
volume ranking. The market-cap reading is reported beside it with its own coverage.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any

import numpy as np

from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.backtest.mover_scanner_v1.daily import DailyPanel
from app.backtest.mover_scanner_v1.premarket import PremarketPanel
from app.backtest.mover_scanner_v1.scan import GateReading, MoverCandidate, gate_reading
from app.strategy.config import StrategyConfig

#: Declared for this study: a market capitalisation at or above this is called mega cap.
MEGA_CAP_THRESHOLD = 200_000_000_000.0
#: Declared for this study: the cross-sectional ADDV percentile above which a pick is called
#: a top-liquidity name.
TOP_LIQUIDITY_PERCENTILE = 99.0


@dataclass(frozen=True)
class Described:
    """One (symbol, session) as the premarket engine sees it, with no eligibility applied."""

    symbol: str
    session_date: date
    covered: bool
    gap_pct: float | None
    pm_bars: int | None
    pm_volume: float | None
    pm_dollar_volume: float | None
    pm_rvol: float | None
    addv20_dollar: float | None
    gate: GateReading


def describe_features(symbol: str, session: date, premarket: PremarketPanel, daily: DailyPanel,
                      config: MoverScannerConfig, strategy: StrategyConfig) -> Described:
    """The premarket reading for any symbol, eligible or not."""
    row = premarket.symbol_index.get(symbol)
    column = premarket.session_index[session]
    position = daily.index[session]
    empty = GateReading(None, None, False, False)
    if row is None or not bool(premarket.covered[row, column]):
        return Described(symbol, session, False, None, None, None, None, None, None, empty)
    read = lambda name: premarket.field(name, row, column)  # noqa: E731
    bars = read("pm_bars")
    previous_close = daily.previous_close(symbol, position)
    adv20, addv20, _ = daily.baselines(symbol, position, config.daily_baseline_sessions)
    median_volume, used = premarket.rvol_baseline(row, column,
                                                  config.premarket_rvol_baseline_sessions)
    last_price = read("pm_last_price")
    gap = (None if previous_close is None or not np.isfinite(last_price)
           else last_price / previous_close - 1.0)
    rvol = (None if median_volume is None or used < config.premarket_rvol_baseline_sessions
            or not np.isfinite(read("pm_volume"))
            else read("pm_volume") / max(median_volume,
                                         config.premarket_rvol_baseline_floor_shares))
    gate_price, gate_volume = read("gate_last_price"), read("gate_volume")
    gate = (empty if previous_close is None or adv20 is None else gate_reading(
        gate_price if np.isfinite(gate_price) else None,
        gate_volume if np.isfinite(gate_volume) else None, previous_close, adv20, strategy))
    return Described(
        symbol, session, True,
        gap, int(bars) if np.isfinite(bars) else None,
        read("pm_volume") if np.isfinite(read("pm_volume")) else None,
        read("pm_dollar_volume") if np.isfinite(read("pm_dollar_volume")) else None,
        rvol, addv20, gate)


def addv_percentiles(symbols: Sequence[str], session: date, daily: DailyPanel,
                     config: MoverScannerConfig) -> Mapping[str, float]:
    """Each symbol's percentile of 20-session average dollar volume within ``symbols``."""
    position = daily.index[session]
    values: dict[str, float] = {}
    for symbol in symbols:
        _, addv, _ = daily.baselines(symbol, position, config.daily_baseline_sessions)
        if addv and addv > 0:
            values[symbol] = addv
    if not values:
        return {}
    ordered = np.sort(np.asarray(list(values.values())))
    return {symbol: float(np.searchsorted(ordered, value, side="right") / ordered.size * 100.0)
            for symbol, value in values.items()}


def turnover(selections: Sequence[tuple[date, tuple[str, ...]]]) -> float | None:
    """Mean share of a session's output that was not in the previous session's output."""
    changes = []
    for (_, previous), (_, current) in zip(selections, selections[1:]):
        if not current:
            continue
        changes.append(len(set(current) - set(previous)) / len(current))
    return None if not changes else float(np.mean(changes))


@dataclass(frozen=True)
class ArmSummary:
    name: str
    sessions: int
    slots: int
    unique_symbols: int
    repeat_ratio: float | None
    median_gap_pct: float | None
    median_pm_rvol: float | None
    median_pm_dollar_volume: float | None
    median_addv_percentile: float | None
    top_liquidity_share: float | None
    mega_cap_share_known: float | None
    market_cap_known_share: float | None
    gap_pass_per_session: float
    volume_pass_per_session: float
    both_pass_per_session: float
    sessions_with_any_both_pass: int
    sessions_with_any_both_pass_share: float
    no_premarket_print_share: float
    gap_down_share: float
    turnover: float | None
    details: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        payload = {key: value for key, value in self.__dict__.items() if key != "details"}
        payload["details"] = dict(self.details)
        return payload


def _optional_median(values: Sequence[float]) -> float | None:
    usable = [value for value in values if value is not None and np.isfinite(value)]
    return None if not usable else float(median(usable))


def summarize_arm(name: str, selections: Sequence[tuple[date, tuple[str, ...]]],
                  described: Mapping[tuple[date, str], Described],
                  percentiles: Mapping[date, Mapping[str, float]],
                  market_caps: Mapping[str, float] | None = None) -> ArmSummary:
    """One arm's scanner-quality summary. Every rate is per selected slot unless named otherwise."""
    rows = [described[(session, symbol)] for session, symbols in selections
            for symbol in symbols if (session, symbol) in described]
    slots = sum(len(symbols) for _, symbols in selections)
    picked = [symbol for _, symbols in selections for symbol in symbols]
    unique = len(set(picked))
    gate_rows = [row.gate for row in rows]
    sessions_with = 0
    for session, symbols in selections:
        if any(described[(session, symbol)].gate.both_pass for symbol in symbols
               if (session, symbol) in described):
            sessions_with += 1
    caps = market_caps or {}
    known = [caps[symbol] for symbol in picked if symbol in caps]
    percentile_values = [percentiles.get(session, {}).get(symbol)
                         for session, symbols in selections for symbol in symbols]
    percentile_values = [value for value in percentile_values if value is not None]
    sessions_count = len(selections)
    return ArmSummary(
        name=name, sessions=sessions_count, slots=slots, unique_symbols=unique,
        repeat_ratio=None if slots == 0 else 1.0 - unique / slots,
        median_gap_pct=_optional_median([row.gap_pct for row in rows]),
        median_pm_rvol=_optional_median([row.pm_rvol for row in rows]),
        median_pm_dollar_volume=_optional_median([row.pm_dollar_volume for row in rows]),
        median_addv_percentile=_optional_median(percentile_values),
        top_liquidity_share=(None if not percentile_values else
                             sum(1 for value in percentile_values
                                 if value >= TOP_LIQUIDITY_PERCENTILE) / len(percentile_values)),
        mega_cap_share_known=(None if not known else
                              sum(1 for value in known if value >= MEGA_CAP_THRESHOLD) / len(known)),
        market_cap_known_share=None if slots == 0 else len(known) / slots,
        gap_pass_per_session=0.0 if not sessions_count else
        sum(1 for gate in gate_rows if gate.gap_pass) / sessions_count,
        volume_pass_per_session=0.0 if not sessions_count else
        sum(1 for gate in gate_rows if gate.volume_pass) / sessions_count,
        both_pass_per_session=0.0 if not sessions_count else
        sum(1 for gate in gate_rows if gate.both_pass) / sessions_count,
        sessions_with_any_both_pass=sessions_with,
        sessions_with_any_both_pass_share=0.0 if not sessions_count else
        sessions_with / sessions_count,
        no_premarket_print_share=0.0 if not rows else
        sum(1 for row in rows if not row.pm_bars) / len(rows),
        gap_down_share=0.0 if not rows else
        sum(1 for row in rows if row.gap_pct is not None and row.gap_pct <= 0) / len(rows),
        turnover=turnover(selections),
        details={
            "described_rows": len(rows),
            "slots_without_description": slots - len(rows),
            "mega_cap_threshold": MEGA_CAP_THRESHOLD,
            "top_liquidity_percentile": TOP_LIQUIDITY_PERCENTILE,
        })


def pool_summary(scans: Sequence[Any]) -> dict[str, Any]:
    """Pool and output sizes across the study (section S)."""
    pool_sizes = [len(scan.pool) for scan in scans]
    top_sizes = [len(scan.top) for scan in scans]
    return {
        "pool_average_size": None if not pool_sizes else float(np.mean(pool_sizes)),
        "pool_median_size": None if not pool_sizes else float(median(pool_sizes)),
        "pool_minimum_size": None if not pool_sizes else int(min(pool_sizes)),
        "top_average_size": None if not top_sizes else float(np.mean(top_sizes)),
        "sessions_below_top_count": sum(1 for size in top_sizes if size < 8),
        "eligible_average": None if not scans else float(np.mean([s.eligible for s in scans])),
        "eligible_minimum": None if not scans else int(min(s.eligible for s in scans)),
    }


def new_selections(scans: Sequence[Any]) -> list[tuple[date, tuple[str, ...]]]:
    return [(scan.session_date, tuple(item.symbol for item in scan.top)) for scan in scans]


def current_selections(rows: Sequence[Mapping[str, Any]], sessions: Sequence[date],
                       ) -> list[tuple[date, tuple[str, ...]]]:
    """The frozen arm's TOP8 per session, in its own stored rank order."""
    wanted = {session.isoformat() for session in sessions}
    by_session: dict[str, list[tuple[int, str]]] = {}
    for row in rows:
        day = str(row["session_date"])
        if day in wanted:
            by_session.setdefault(day, []).append((int(row["rank"]), str(row["symbol"])))
    return [(session, tuple(symbol for _, symbol in sorted(by_session.get(session.isoformat(), []))))
            for session in sessions]
