"""The E0 row table: one row per (session D, ticker) that passes the declared PIT universe.

Everything the study later summarises is built here exactly once, in array form. The daily
panel comes from Strategy D's loader, which verifies every frozen file against the sha256 the
freeze recorded and refuses a grid with a hole; E0 imports it and changes nothing in it.

Two properties are structural rather than checked after the fact:

* a feature array at row ``t`` is built only from panel columns ``<= t``, so a feature cannot
  see D+1 even if a later edit asked it to;
* the labels are the only arrays that read ``t + 1``, and they are never inputs to a filter.

``SPY`` is not a CS security, so Strategy D's panel does not carry it. E0 reads the benchmark's
close out of the same frozen grouped files, through the same freeze rows and the same sha256
verification, in its own pass.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.strategy_d_analog.source import (
    FREEZE_FILE, GROUPED, OK, DailyHistory, _resolve, _snapshot_dir, load_daily_history,
)
from app.backtest.strategy_e0_overnight.config import E0HardFail, E0Rules

BENCHMARK = "SPY"


@dataclass(frozen=True)
class DailyRows:
    """Flat arrays, one entry per surviving (session index, ticker column) pair."""

    session_idx: np.ndarray          # int32, index into the grid
    ticker_idx: np.ndarray           # int32, index into panel.tickers
    sessions: tuple[date, ...]
    tickers: tuple[str, ...]
    overnight: np.ndarray            # float64, open(D+1)/close(D) - 1
    features: Mapping[str, np.ndarray]
    close: np.ndarray                # raw close(D), for the price buckets
    dollar_volume: np.ndarray
    counters: Mapping[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return int(self.session_idx.size)

    def session_dates(self) -> np.ndarray:
        return np.array([self.sessions[i] for i in self.session_idx], dtype=object)

    def ticker_names(self) -> np.ndarray:
        return np.array([self.tickers[j] for j in self.ticker_idx], dtype=object)


def _rolling_median(values: np.ndarray, window: int) -> np.ndarray:
    """``out[t]`` = median over ``[t - window, t - 1]``; rows before the window are NaN.

    The window ends strictly before ``t`` so that a liquidity filter built on it cannot select
    on the same session's volume, which is also the RVOL numerator.
    """
    t_len, n_len = values.shape
    out = np.full((t_len, n_len), np.nan)
    for t in range(window, t_len):
        out[t] = np.nanmedian(values[t - window:t], axis=0)
    return out


def _rolling_count(values: np.ndarray, window: int) -> np.ndarray:
    present = np.isfinite(values).astype(np.int32)
    out = np.zeros(values.shape, dtype=np.int32)
    for t in range(window, values.shape[0]):
        out[t] = present[t - window:t].sum(axis=0)
    return out


def load_benchmark_close(workspace_root: Path, snapshot_id: str, sessions: Sequence[date],
                         *, c_raw_root: Path | None = None) -> np.ndarray:
    """SPY close on the study grid, read from the frozen grouped files with sha256 verified."""
    freeze_path = _snapshot_dir(workspace_root, snapshot_id) / FREEZE_FILE
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    by_date = {date.fromisoformat(str(r["session_date"])): r
               for r in freeze["files"] if r["file_type"] == GROUPED and r["status"] == OK}
    out = np.full(len(sessions), np.nan)
    for i, session in enumerate(sessions):
        row = by_date.get(session)
        if row is None:
            raise E0HardFail(f"benchmark: the freeze has no usable grouped file for {session}")
        path, _ = _resolve(row, workspace_root, c_raw_root)
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != str(row["sha256"]):
            raise E0HardFail(f"benchmark: sha256 of {path} disagrees with the freeze")
        payload = json.loads(gzip.decompress(body))
        for item in (payload.get("body") or {}).get("results") or ():
            if item.get("T") == BENCHMARK:
                close = item.get("c")
                if close is not None:
                    out[i] = float(close)
                break
    missing = int(np.isnan(out).sum())
    if missing:
        raise E0HardFail(f"benchmark: SPY close missing on {missing} of {len(sessions)} sessions")
    return out


def build(history: DailyHistory, benchmark_close: np.ndarray, rules: E0Rules) -> DailyRows:
    """Apply the declared universe, features and labels to the frozen panel."""
    panel = history.panel
    sessions = panel.sessions
    t_len, n_len = panel.shape
    if benchmark_close.shape != (t_len,):
        raise E0HardFail("benchmark series does not match the grid length")

    factor, _, _ = panel.split_arrays()
    price = panel.close / factor                     # split-normalized close
    prev_price = np.vstack([np.full((1, n_len), np.nan), price[:-1]])
    volume_adj = panel.volume / factor
    dollar_volume = panel.close * panel.volume       # factor cancels
    window = rules.warmup

    median_dv = _rolling_median(dollar_volume, window)
    median_vol = _rolling_median(volume_adj, window)
    present = _rolling_count(dollar_volume, window)

    high, low, close = panel.high, panel.low, panel.close
    span = high - low
    with np.errstate(invalid="ignore", divide="ignore"):
        clv = np.where(span > 0, (close - low) / span, np.nan)
        day_return = price / prev_price - 1.0
        rvol = np.where(median_vol > 0, volume_adj / median_vol, np.nan)
        mom3 = np.full((t_len, n_len), np.nan)
        mom3[3:] = price[3:] / price[:-3] - 1.0
        mom5 = np.full((t_len, n_len), np.nan)
        mom5[5:] = price[5:] / price[:-5] - 1.0
        bench5 = np.full(t_len, np.nan)
        bench5[5:] = benchmark_close[5:] / benchmark_close[:-5] - 1.0
        rel5 = mom5 - bench5[:, None]

    membership = panel.membership()
    first, last = rules.first_index, rules.last_index
    if last >= t_len - 1:
        raise E0HardFail(f"last eligible index {last} leaves no D+1 on a grid of {t_len}")

    rows_t: list[np.ndarray] = []
    rows_j: list[np.ndarray] = []
    counters = {"ca_excluded": 0, "no_next_open": 0, "candidate_cells": 0,
                "failed_membership": 0, "failed_price": 0, "failed_liquidity": 0,
                "clv_undefined": 0, "rvol_undefined": 0}

    for t in range(first, last + 1):
        member = membership[t]
        counters["candidate_cells"] += int(member.size)
        counters["failed_membership"] += int((~member).sum())
        ok = member & np.isfinite(close[t]) & (close[t] >= rules.min_close)
        counters["failed_price"] += int((member & ~ok).sum())
        liquid = (np.isfinite(median_dv[t]) & (median_dv[t] >= rules.min_dollar_volume)
                  & (present[t] >= rules.min_present_sessions))
        counters["failed_liquidity"] += int((ok & ~liquid).sum())
        ok &= liquid
        ok &= np.isfinite(prev_price[t]) & (prev_price[t] > 0)
        next_open = panel.open[t + 1]
        has_open = np.isfinite(next_open) & (next_open > 0)
        counters["no_next_open"] += int((ok & ~has_open).sum())
        ok &= has_open
        # A split executing in (D, D+1] moves the basis between the two prices of the label.
        # F(t + 1) != F(t) is exactly that event, so the test needs no separate date scan.
        unchanged = factor[t + 1] == factor[t]
        counters["ca_excluded"] += int((ok & ~unchanged).sum())
        ok &= unchanged
        cols = np.flatnonzero(ok)
        if cols.size:
            rows_t.append(np.full(cols.size, t, dtype=np.int32))
            rows_j.append(cols.astype(np.int32))

    if not rows_t:
        raise E0HardFail("the declared universe selected no rows")
    idx_t = np.concatenate(rows_t)
    idx_j = np.concatenate(rows_j)

    overnight = panel.open[idx_t + 1, idx_j] / panel.close[idx_t, idx_j] - 1.0
    features = {
        "day_return": day_return[idx_t, idx_j],
        "clv": clv[idx_t, idx_j],
        "rvol": rvol[idx_t, idx_j],
        "dollar_volume": dollar_volume[idx_t, idx_j],
        "momentum_3d": mom3[idx_t, idx_j],
        "momentum_5d": mom5[idx_t, idx_j],
        "relative_strength_5d": rel5[idx_t, idx_j],
        "close_price": panel.close[idx_t, idx_j],
    }
    counters["clv_undefined"] = int((~np.isfinite(features["clv"])).sum())
    counters["rvol_undefined"] = int((~np.isfinite(features["rvol"])).sum())
    counters["rows"] = int(idx_t.size)
    counters["sessions_used"] = int(np.unique(idx_t).size)
    counters["tickers_used"] = int(np.unique(idx_j).size)

    return DailyRows(
        session_idx=idx_t, ticker_idx=idx_j, sessions=sessions, tickers=panel.tickers,
        overnight=overnight, features=features, close=features["close_price"],
        dollar_volume=features["dollar_volume"], counters=counters)


def load_daily_rows(workspace_root: Path, rules: E0Rules, *, c_raw_root: Path | None = None,
                    duplicate_rows_allowed: bool = False) -> tuple[DailyRows, DailyHistory]:
    history = load_daily_history(workspace_root, rules.snapshot_id,
                                 allowed_exchanges=rules.allowed_exchanges, c_raw_root=c_raw_root,
                                 duplicate_rows_allowed=duplicate_rows_allowed)
    benchmark = load_benchmark_close(workspace_root, rules.snapshot_id, history.panel.sessions,
                                     c_raw_root=c_raw_root)
    return build(history, benchmark, rules), history
