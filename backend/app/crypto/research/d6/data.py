"""Building PIT-safe inputs for the offline runner from the D2 canonical files.

Only the five D2 series the contract allows are read, through the D5 loader that already carries
the checksums and the OI delay rule. Nothing from D5.2 (Binance, OKX, COIN-M), nothing from the
liquidation collector, nothing from AOA.

Two facts the D5 grid does not carry are derived here, both from past rows only:

  `oi_record_ts_ms`     the stamp of the newest OI record in force at a bar close, needed by H2.
  `next_funding_ts_ms`  the next settlement, extrapolated from the interval between the last two
                        *observed* settlements. Reading the next settlement out of the funding
                        table would be reading a future row, so it is not done that way.
"""
from __future__ import annotations

import numpy as np

from ..dataset import MINUTE_MS, OI_DELAY_MS, _read, load as load_grid
from .model import BarWindow, MarketContext

ROOT_DATASETS = ("kline_1m", "mark_1m", "index_1m", "open_interest_5m", "funding")


def oi_record_timestamps(grid_ts: np.ndarray) -> np.ndarray:
    """Stamp of the OI record that was in force at each bar close. -1 where none was yet known.

    Mirrors `dataset._asof`, but keeps the source timestamp instead of the value.
    """
    oi = _read("open_interest_5m", ["open_interest"])
    src_ts = oi["timestamp_ms"]
    known_at = src_ts + OI_DELAY_MS
    order = np.argsort(known_at, kind="stable")
    known_at, src_ts = known_at[order], src_ts[order]
    pos = np.searchsorted(known_at, grid_ts + MINUTE_MS, side="right") - 1
    out = np.full(len(grid_ts), -1, dtype=np.int64)
    good = pos >= 0
    out[good] = src_ts[pos[good]]
    return out


def next_funding_timestamps(grid_ts: np.ndarray, funding_ts: np.ndarray) -> np.ndarray:
    """Next settlement after each bar close, extrapolated from observed past settlements.

    At bar close `c` the engine knows every settlement stamped at or before `c`. The interval is
    taken from the last two of those, and the next settlement is stepped forward from the most
    recent one until it lands after `c`. Needs two past settlements; -1 until then.
    """
    funding_ts = np.asarray(funding_ts, dtype=np.int64)
    close_ms = grid_ts + MINUTE_MS
    idx = np.searchsorted(funding_ts, close_ms, side="right") - 1  # last settlement known
    out = np.full(len(grid_ts), -1, dtype=np.int64)
    usable = idx >= 1
    if not usable.any():
        return out
    last = funding_ts[idx[usable]]
    prev = funding_ts[idx[usable] - 1]
    interval = last - prev
    interval = np.where(interval > 0, interval, 8 * 60 * 60 * 1000)
    steps = np.floor_divide(close_ms[usable] - last, interval) + 1
    out[usable] = last + steps * interval
    return out


def load_inputs(rebuild: bool = False) -> dict[str, np.ndarray]:
    """The D5 grid plus the two derived PIT columns."""
    grid = load_grid(rebuild=rebuild)
    grid = dict(grid)
    grid["oi_record_ts"] = oi_record_timestamps(grid["ts"])
    grid["next_funding_ts"] = next_funding_timestamps(grid["ts"], grid["funding_ts"])
    return grid


def window_at(grid: dict[str, np.ndarray], index: int, lookback_bars: int) -> BarWindow:
    """Rows [index - lookback_bars + 1 .. index]. The window ends at bar t by construction."""
    if index < 0 or index >= len(grid["ts"]):
        raise IndexError(f"index {index} outside grid of {len(grid['ts'])}")
    lo = max(0, index - lookback_bars + 1)
    hi = index + 1
    return BarWindow(ts_ms=grid["ts"][lo:hi], close=grid["close"][lo:hi],
                     index_close=grid["index_close"][lo:hi], oi=grid["oi"][lo:hi])


def market_at(grid: dict[str, np.ndarray], index: int, *, equity: float | None = None,
              safe_max_qty: float | None = None) -> MarketContext:
    """Decision instant = the close of bar t.

    `entry_reference_price` is `close[t]`, the last price known when the decision is made, and
    not `open[t+1]`. The contract puts the earliest legal *fill* at `open[t+1]`, but sizing
    happens at the decision instant and its result feeds back into the decision through the
    minimum quantity and minimum notional checks. Sizing on `open[t+1]` would therefore let a
    future bar flip a LONG into a HOLD, which the PIT clause forbids and the PIT tests catch.
    Live behaviour agrees: an operator sizes on the price on screen, then takes the fill.

    D6-C carries `open[t+1]` separately as the fill price. It is deliberately absent here.
    """
    close_ms = int(grid["ts"][index]) + MINUTE_MS
    oi_ts = int(grid["oi_record_ts"][index])
    funding_ts = int(grid["next_funding_ts"][index])
    return MarketContext(
        now_ms=close_ms,
        oi_record_ts_ms=None if oi_ts < 0 else oi_ts,
        next_funding_ts_ms=None if funding_ts < 0 else funding_ts,
        mark=float(grid["mark_close"][index]),
        entry_reference_price=float(grid["close"][index]),
        safe_max_qty=safe_max_qty,
    )


def index_of(grid: dict[str, np.ndarray], ts_ms: int) -> int:
    pos = int(np.searchsorted(grid["ts"], ts_ms, side="left"))
    if pos >= len(grid["ts"]) or int(grid["ts"][pos]) != ts_ms:
        raise KeyError(f"{ts_ms} is not a bar open time on the research grid")
    return pos
