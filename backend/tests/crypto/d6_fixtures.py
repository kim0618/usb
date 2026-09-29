"""Synthetic inputs for the D6-B tests.

Everything here is made up on purpose. No fixture reads a forward return, a PnL or a price path
outcome, so no expected value in these tests can encode performance. Prices are built from a
fixed seed and shaped only to put a feature in a chosen bucket.
"""
from __future__ import annotations

import numpy as np

from app.crypto.research.d6.model import BarWindow, MarketContext, StrategyState

MINUTE_MS = 60_000
DAY_BARS = 1440
#: 30 bucket days + the decision day + enough rows before them for the 1440-return volatility.
FULL_DAYS = 32
BASE_TS = 1_612_137_600_000  # 2021-02-01T00:00:00Z, a UTC day boundary like the research grid


def synthetic_grid(days: int = FULL_DAYS, *, seed: int = 20260928,
                   price: float = 100_000.0, drift: float = 0.0,
                   basis: float = 0.0, oi_slope: float = 0.0,
                   vol: float = 8.0e-4) -> dict[str, np.ndarray]:
    """A gap-free 1m grid whose volatility, basis and OI trend are dialled in, not discovered.

    `vol` is the per-minute log-return standard deviation, so the 24 h realised volatility lands
    near it and the regime label can be chosen by the caller.
    """
    n = days * DAY_BARS
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift, vol, n)
    close = price * np.exp(np.cumsum(steps))
    ts = BASE_TS + np.arange(n, dtype=np.int64) * MINUTE_MS
    index_close = close * np.exp(-basis)          # basis = ln(close / index)
    oi = 1.0e9 * np.exp(np.arange(n) * oi_slope)
    return {"ts": ts, "close": close, "index_close": index_close, "oi": oi,
            "open": close, "mark_close": close}


def window(grid: dict[str, np.ndarray], end_index: int | None = None) -> BarWindow:
    end = len(grid["ts"]) - 1 if end_index is None else end_index
    hi = end + 1
    return BarWindow(ts_ms=grid["ts"][:hi], close=grid["close"][:hi],
                     index_close=grid["index_close"][:hi], oi=grid["oi"][:hi])


def clean_market(win: BarWindow, **overrides) -> MarketContext:
    """A context in which no filter fails for a reason the test did not ask for."""
    defaults = dict(
        now_ms=win.bar_close_ms,
        oi_record_ts_ms=win.bar_close_ms - 6 * MINUTE_MS,
        next_funding_ts_ms=win.bar_close_ms + 60 * MINUTE_MS,
        mark=100_000.0,
        entry_reference_price=100_000.0,
        safe_max_qty=None,
    )
    defaults.update(overrides)
    return MarketContext(**defaults)


def clean_state(**overrides) -> StrategyState:
    defaults = dict(position_open=False, last_exit_ts_ms=None, day_realized_pnl_pct=0.0,
                    consecutive_losses=0, last_loss_ts_ms=None, emergency_stop=None,
                    equity=7440.48)
    defaults.update(overrides)
    return StrategyState(**defaults)


def force_last_bar(grid: dict[str, np.ndarray], *, basis: float | None = None,
                   drop_1h: float | None = None, oi_change_1h: float | None = None) -> None:
    """Bend the final rows so the decision bar lands where the test wants it.

    Only rows at or before the decision bar are touched, so the window stays PIT-valid.
    """
    last = len(grid["ts"]) - 1
    if drop_1h is not None:
        grid["close"][last] = grid["close"][last - 60] * np.exp(drop_1h)
    if basis is not None:
        grid["index_close"][last] = grid["close"][last] * np.exp(-basis)
    if oi_change_1h is not None:
        grid["oi"][last] = grid["oi"][last - 60] * np.exp(oi_change_1h)
