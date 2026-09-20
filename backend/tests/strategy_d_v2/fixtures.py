"""Synthetic panels for the V2-A coordinate tests. No Drive, no network, no real prices.

Every series here is short and hand-checkable: a test that says "return_20 is this number" can be
verified with a calculator, and the reference implementations in the test modules recompute the
declared formulas with plain Python loops so the numpy version is never checked against itself.
"""

from datetime import date, timedelta

import numpy as np

from app.backtest.strategy_c_selection.panel import Panel, SplitEvent
from app.market.calendar import MarketCalendar

TICKERS = ("AAA", "BBB", "CCC", "DDD", "EEE")


def sessions(count: int, start: date = date(2024, 9, 17)) -> tuple[date, ...]:
    calendar = MarketCalendar()
    out: list[date] = []
    cursor = start
    while len(out) < count:
        if calendar.is_trading_day(cursor):
            out.append(cursor)
        cursor += timedelta(days=1)
    return tuple(out)


def series(days: int, seed: int = 7) -> dict[str, np.ndarray]:
    """Distinct, strictly positive paths - no two tickers tie on any coordinate by accident."""
    rng = np.random.default_rng(seed)
    out: dict[str, np.ndarray] = {}
    for i, ticker in enumerate(TICKERS):
        steps = rng.normal(0.0005 * (i + 1), 0.012 + 0.002 * i, days)
        out[ticker] = 40.0 * (1.0 + i) * np.exp(np.cumsum(steps))
    return out


def make_panel(days: int = 120, *, seed: int = 7, tickers: tuple[str, ...] = TICKERS,
               splits: tuple[SplitEvent, ...] = (), listed_from: dict[str, int] | None = None,
               ) -> Panel:
    """An OHLCV panel where high/low/open are deterministic functions of the close path."""
    grid = sessions(days)
    close_by_ticker = series(days, seed)
    width = len(tickers)
    close = np.full((days, width), np.nan)
    high = np.full((days, width), np.nan)
    low = np.full((days, width), np.nan)
    open_ = np.full((days, width), np.nan)
    volume = np.full((days, width), np.nan)
    for j, ticker in enumerate(tickers):
        path = close_by_ticker.get(ticker)
        if path is None:
            rng = np.random.default_rng(99 + j)
            path = 25.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, days)))
        start = 0 if listed_from is None else listed_from.get(ticker, 0)
        close[start:, j] = path[start:]
        high[start:, j] = path[start:] * (1.0 + 0.004 * (j + 1))
        low[start:, j] = path[start:] * (1.0 - 0.003 * (j + 1))
        open_[start:, j] = path[start:] * (1.0 + 0.001 * (j + 1))
        volume[start:, j] = 900_000.0 + 1_000.0 * np.arange(days - start) + 5_000.0 * j
    snapshots = {grid[0]: frozenset(tickers)}
    return Panel(grid, tickers, open_, high, low, close, volume, splits, snapshots)


def eligible_all(panel: Panel, from_index: int = 60) -> np.ndarray:
    """Every ticker eligible from ``from_index`` on: the universe rule is tested separately."""
    mask = np.zeros(panel.close.shape, dtype=bool)
    mask[from_index:] = np.isfinite(panel.close[from_index:])
    return mask


def make_history(panel, *, freeze_id: str = "TEST-FREEZE", figi: dict | None = None):
    """A minimal ``DailyHistory`` over a synthetic panel: enough for library and search tests.

    The freeze identity is fake on purpose - these tests never bind to the declaration, they
    exercise the engine - and ``figi`` defaults to "no snapshot carries a composite FIGI", which
    is the case the null rule has to handle.
    """
    from app.backtest.strategy_d_analog.source import DailyHistory, grid_digest
    from app.backtest.strategy_d_v2.models import DAILY_AUTHORITY, FreezeIdentity, SessionGrid

    dates = panel.sessions
    grid = SessionGrid(tuple(dates), grid_digest(dates))
    identity = FreezeIdentity(
        snapshot_id="TEST-SNAP", snapshot_sha256="0" * 64, freeze_id=freeze_id,
        freeze_digest="1" * 64, source_digest="2" * 64, d_read_digest="3" * 64,
        daily_authority=DAILY_AUTHORITY, first_session=dates[0].isoformat(),
        last_session=dates[-1].isoformat(), session_count=len(dates), grid_digest=grid.digest)
    snapshots = tuple(sorted(figi or {}))
    return DailyHistory(grid, panel, identity, dict(figi or {}), snapshots, {})


WIDE_TICKERS = tuple(f"T{i:03d}" for i in range(60))
