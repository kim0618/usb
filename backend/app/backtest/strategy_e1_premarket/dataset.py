"""Joining the premarket table to the daily point-in-time universe.

The join is what makes the point-in-time claim structural. An E1 decision is taken at 09:25 of
session D, so every daily input must be known at the **close of D-1**. E0's row for session D-1
is exactly that: its features are built from sessions <= D-1 and its universe filter already
applied CS membership, price, liquidity and the corporate-action test at D-1. E1 therefore looks
up the previous grid session rather than the same one, and a premarket row whose previous session
has no E0 row is dropped rather than filled.

Walking the XNYS grid rather than the tape matters for the same reason it did in E0: after a
holiday the previous *calendar* day is not the previous session, and a gap measured against the
wrong close is not a gap.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight.dataset import DailyRows
from app.backtest.strategy_e1_premarket.config import E1HardFail, E1Rules

BENCHMARK = "SPY"


@dataclass(frozen=True)
class PremarketRows:
    """One row per (session D, ticker) surviving the declared E1 universe."""

    sessions: np.ndarray          # object array of date, session D
    tickers: np.ndarray           # object array of str
    features: dict[str, np.ndarray]
    labels: dict[str, np.ndarray]
    counters: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return int(self.sessions.size)


def _previous_session_map(sessions: Sequence[date]) -> dict[str, str]:
    return {sessions[i].isoformat(): sessions[i - 1].isoformat() for i in range(1, len(sessions))}


def _benchmark_premarket(cohort: Mapping[str, np.ndarray]) -> dict[str, float]:
    """SPY's last premarket price per session, from the same tape every other symbol uses."""
    is_spy = cohort["symbols"].astype(str) == BENCHMARK
    return {str(s): float(p) for s, p in
            zip(cohort["sessions"][is_spy].astype(str), cohort["pm_last_price"][is_spy])}


def build(cohort: Mapping[str, np.ndarray], daily: DailyRows, benchmark_close: np.ndarray,
          rules: E1Rules) -> PremarketRows:
    """Apply the declared universe, then assemble the declared features and labels."""
    grid = list(daily.sessions)
    previous = _previous_session_map(grid)
    grid_index = {s.isoformat(): i for i, s in enumerate(grid)}

    session_text = np.array([s.isoformat() for s in grid])
    ticker_text = np.array(list(daily.tickers))
    daily_key = np.char.add(np.char.add(session_text[daily.session_idx], "|"),
                            ticker_text[daily.ticker_idx])
    order = np.argsort(daily_key)
    sorted_key = daily_key[order]

    sessions = cohort["sessions"].astype(str)
    symbols = cohort["symbols"].astype(str)
    counters: dict[str, Any] = {"premarket_rows": int(sessions.size)}

    # -- declared premarket universe ---------------------------------------------------------
    enough_bars = cohort["pm_bars"] >= rules.min_premarket_bars
    enough_value = cohort["pm_dollar_volume"] >= rules.min_premarket_dollar_volume
    counters["rejected_premarket_bars"] = int((~enough_bars).sum())
    counters["rejected_premarket_dollar_volume"] = int((enough_bars & ~enough_value).sum())
    keep = enough_bars & enough_value
    keep &= np.isfinite(cohort[f"open_{570}"]) & (cohort[f"open_{570}"] > 0)
    counters["rejected_no_open_price"] = int((enough_bars & enough_value & ~keep).sum())

    # -- the daily row of the previous session ------------------------------------------------
    prev = np.array([previous.get(s, "") for s in sessions])
    on_grid = np.array([s in grid_index for s in sessions]) & (prev != "")
    counters["rejected_session_not_on_grid"] = int((keep & ~on_grid).sum())
    keep &= on_grid

    minute_key = np.char.add(np.char.add(prev, "|"), symbols)
    position = np.clip(np.searchsorted(sorted_key, minute_key), 0, max(sorted_key.size - 1, 0))
    matched = sorted_key[position] == minute_key
    counters["rejected_no_daily_row"] = int((keep & ~matched).sum())
    keep &= matched
    if not keep.any():
        raise E1HardFail("the declared E1 universe selected no rows")

    index = order[position][keep]
    prev_close = daily.features["close_price"][index]
    last = cohort["pm_last_price"][keep]

    spy = _benchmark_premarket(cohort)
    spy_close = {s.isoformat(): float(benchmark_close[i]) for s, i in
                 ((grid[i], i) for i in range(len(grid)))}
    kept_sessions = sessions[keep]
    spy_gap = np.array([
        (spy[s] / spy_close[previous[s]] - 1.0)
        if s in spy and previous.get(s) in spy_close and spy_close[previous[s]] > 0 else np.nan
        for s in kept_sessions])

    gap = last / prev_close - 1.0
    features = {
        "premarket_gap": gap,
        "premarket_return": cohort["pm_return"][keep],
        "premarket_high_return": cohort["pm_high"][keep] / prev_close - 1.0,
        "premarket_low_return": cohort["pm_low"][keep] / prev_close - 1.0,
        "premarket_volume": cohort["pm_volume"][keep],
        "premarket_dollar_volume": cohort["pm_dollar_volume"][keep],
        "premarket_rvol": cohort["pm_rvol"][keep],
        "return_0800_0925": cohort["return_0800_0925"][keep],
        "return_0900_0925": cohort["return_0900_0925"][keep],
        "return_0915_0925": cohort["return_0915_0925"][keep],
        "return_last30m": cohort["return_0855_0925"][keep],
        "distance_to_premarket_high": cohort["distance_to_premarket_high"][keep],
        "position_in_premarket_range": cohort["position_in_premarket_range"][keep],
        "premarket_volume_last30m_ratio": cohort["premarket_volume_last30m_ratio"][keep],
        "previous_day_return": daily.features["day_return"][index],
        "previous_day_clv": daily.features["clv"][index],
        "previous_day_dollar_volume": daily.features["dollar_volume"][index],
        "close_price": prev_close,
        "spy_premarket_return": spy_gap,
        "relative_strength_vs_spy": gap - spy_gap,
        "pm_bars": cohort["pm_bars"][keep],
    }
    labels = {name: cohort[name][keep] for name in cohort
              if name.startswith(("R_", "MFE_", "MAE_"))}

    counters["rows"] = int(keep.sum())
    counters["sessions"] = int(np.unique(kept_sessions).size)
    counters["tickers"] = int(np.unique(symbols[keep]).size)
    for name in ("return_0900_0925", "return_0915_0925", "return_last30m",
                 "premarket_rvol", "position_in_premarket_range", "relative_strength_vs_spy"):
        counters[f"undefined_{name}"] = int((~np.isfinite(features[name])).sum())
    counters["undefined_R_5m_strict"] = int((~np.isfinite(labels["R_5m_strict"])).sum())

    return PremarketRows(
        sessions=np.array([date.fromisoformat(s) for s in kept_sessions], dtype=object),
        tickers=np.array(symbols[keep], dtype=object),
        features=features, labels=labels, counters=counters)
