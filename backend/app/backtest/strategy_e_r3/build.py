"""The V1.1 decision inputs on the bound development tape, and the facts kept away from them.

Per symbol, one pass over its bound tape produces, for every timeline session with a premarket
bar:

* the V1.1 09:25 row, from ``universe.premarket_row`` called directly (no batch shortcut);
* separately, the post-09:25 facts the replay needs *after* the seal: whether the 09:30 bar
  exists with a positive open (correction B attribution), the exact 09:30 / 09:34 bars (entry and
  exit layers only), and Research's R_5m labels (trade-table descriptors only).

The decision path (``frames``) reads only the first group. ``origin`` labels are attached to rows
after sealing, for the structural delta and the trade table; nothing downstream of H5 reads them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import minute as M
from app.backtest.strategy_e1_premarket import premarket as P
from app.backtest.strategy_e_d6.replay import exact_bars
from app.strategy_e_v1_1 import universe as U

ORIGINS = ("V1", "A", "B", "AB")
POISON_SEED = 20260921


@dataclass(frozen=True)
class SymbolRow:
    """One (symbol, session): the 09:25 row plus the post-cutoff facts, kept in separate fields."""

    row: U.PremarketRow
    has_0930_open: bool                 # post-09:25 fact; attribution only
    labels: dict[str, float]            # post-09:25; trade-table descriptors only
    bars: dict[str, list]               # post-09:25; entry/exit layers only


_CTX: dict[str, Any] = {}


def _init(view_root: str, sessions: Sequence[str]) -> None:
    _CTX["root"] = Path(view_root)
    _CTX["sessions"] = {date.fromisoformat(s) for s in sessions}
    _CTX["edges"], _CTX["offsets"] = M.et_offsets(
        datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc))


def _day(key: int) -> date:
    return date.fromordinal(key + date(1970, 1, 1).toordinal())


def _opening(tape: M.SymbolTape, session: date) -> dict[str, float] | None:
    sel = np.flatnonzero(tape.et_day == M.ordinal(session))
    order = sel[np.argsort(tape.minute[sel], kind="stable")]
    return P.opening_block(tape.minute[order], tape.open[order], tape.high[order],
                           tape.low[order], tape.close[order])


def premarket_sessions(tape: M.SymbolTape, wanted: set[date]) -> list[date]:
    has = (tape.minute >= P.PREMARKET_START) & (tape.minute <= P.DECISION_LAST_BAR)
    return sorted(d for d in (_day(int(k)) for k in np.unique(tape.et_day[has])) if d in wanted)


def symbol_rows(tape: M.SymbolTape, wanted: set[date]) -> dict[date, SymbolRow]:
    out: dict[date, SymbolRow] = {}
    for session in premarket_sessions(tape, wanted):
        row = U.premarket_row(tape, session)              # 09:25 only; truncates first
        if row is None:
            continue
        opening = _opening(tape, session)                 # post-cutoff, after the row exists
        anchor = opening[f"open_{P.OPEN_MIN}"] if opening is not None else float("nan")
        has_open = opening is not None and bool(np.isfinite(anchor) and anchor > 0)
        labels = P.labels(opening) if opening is not None else {"R_5m": float("nan"),
                                                                  "R_5m_strict": float("nan")}
        out[session] = SymbolRow(row, has_open,
                                 {"R_5m": float(labels["R_5m"]),
                                  "R_5m_strict": float(labels["R_5m_strict"])},
                                 exact_bars(tape, [session])[session])
    return out


def _job(symbol: str) -> tuple[str, dict[date, SymbolRow]]:
    tape = M.load_symbol_tape(symbol, _CTX["root"], _CTX["edges"], _CTX["offsets"])
    if tape is None:
        return symbol, {}
    return symbol, symbol_rows(tape, _CTX["sessions"])


def build_rows(view_root: Path, symbols: Sequence[str], sessions: Sequence[str], *,
               workers: int, progress=None) -> dict[str, dict[date, SymbolRow]]:
    out: dict[str, dict[date, SymbolRow]] = {}
    with ProcessPoolExecutor(max_workers=workers, initializer=_init,
                             initargs=(str(view_root), tuple(sessions))) as pool:
        for i, (symbol, rows) in enumerate(pool.map(_job, sorted(symbols), chunksize=4)):
            out[symbol] = rows
            if progress is not None and i % 300 == 0:
                progress(f"V1.1 rows {i}/{len(symbols)}")
    return out


# -- PIT poison ----------------------------------------------------------------------------------

def poisoned(tape: M.SymbolTape, session: date, mode: str) -> M.SymbolTape:
    """Every bar after 09:24 ET of D and every later day: replaced by noise, or deleted."""
    day = M.ordinal(session)
    after = (tape.et_day > day) | ((tape.et_day == day) & (tape.minute > P.DECISION_LAST_BAR))
    if mode == "delete":
        keep = ~after
        return M.SymbolTape(symbol=tape.symbol, et_day=tape.et_day[keep], minute=tape.minute[keep],
                            open=tape.open[keep], high=tape.high[keep], low=tape.low[keep],
                            close=tape.close[keep], volume=tape.volume[keep],
                            vwap=tape.vwap[keep], sources=tape.sources,
                            overlap_sessions=tape.overlap_sessions)
    rng = np.random.default_rng(POISON_SEED)
    columns = {}
    for name in ("open", "high", "low", "close", "volume", "vwap"):
        values = getattr(tape, name).copy()
        noise = rng.uniform(1.0, 1000.0, size=int(after.sum())) * 19.0
        values[after] = np.where(np.isfinite(values[after]), noise, values[after])
        columns[name] = values
    return M.SymbolTape(symbol=tape.symbol, et_day=tape.et_day, minute=tape.minute,
                        sources=tape.sources, overlap_sessions=tape.overlap_sessions, **columns)


def _poison_job(symbol: str) -> tuple[str, dict[str, dict[date, U.PremarketRow | None]]]:
    tape = M.load_symbol_tape(symbol, _CTX["root"], _CTX["edges"], _CTX["offsets"])
    out: dict[str, dict[date, U.PremarketRow | None]] = {"noise": {}, "delete": {}}
    if tape is None:
        return symbol, out
    for session in premarket_sessions(tape, _CTX["sessions"]):
        for mode in out:
            out[mode][session] = U.premarket_row(poisoned(tape, session, mode), session)
    return symbol, out


def poison_rows(view_root: Path, symbols: Sequence[str], sessions: Sequence[str], *,
                workers: int) -> dict[str, dict[str, dict[date, U.PremarketRow | None]]]:
    with ProcessPoolExecutor(max_workers=workers, initializer=_init,
                             initargs=(str(view_root), tuple(sessions))) as pool:
        return dict(pool.map(_poison_job, sorted(symbols), chunksize=1))


def pit_sample(symbols: Sequence[str], sample: int = 40) -> list[str]:
    """E-D6's deterministic sampling rule (``run.pit_sample``)."""
    return sorted(set(symbols))[::max(1, len(symbols) // sample)]


# -- daily (D-1) --------------------------------------------------------------------------------

@dataclass(frozen=True)
class DailySession:
    eligible: np.ndarray          # V1.1, (N,)
    has_open_d: np.ndarray        # post-09:25 fact; attribution only
    close_prev: np.ndarray
    dollar_volume_prev: np.ndarray


def daily_sessions(panel, grid: Sequence[date], indices: Sequence[int], calendar, *,
                   poison: bool = False) -> dict[date, DailySession]:
    """``universe.daily_eligibility`` for each decision session index ``d`` (row D-1 = d-1).

    With ``poison``, every row from D onward (prices, volume, membership) is overwritten before
    slicing, which must change nothing.
    """
    factor = panel.split_arrays()[0]
    membership = panel.membership()
    out = {}
    rng = np.random.default_rng(POISON_SEED)
    for d in indices:
        close, volume, member = panel.close, panel.volume, membership
        if poison:
            close, volume, member = close.copy(), volume.copy(), member.copy()
            close[d:] = rng.uniform(1, 1000, close[d:].shape)
            volume[d:] = rng.uniform(1, 1e9, volume[d:].shape)
            member[d:] = ~member[d:]
        eligible = U.daily_eligibility(
            grid[d], list(grid[:d]), close=close[:d], volume=volume[:d], factor=factor[:d],
            membership=member[d - 1], split_in_window=factor[d] != factor[d - 1],
            calendar=calendar)
        opens = panel.open[d]
        out[grid[d]] = DailySession(eligible, np.isfinite(opens) & (opens > 0),
                                    panel.close[d - 1], panel.close[d - 1] * panel.volume[d - 1])
    return out


# -- frames --------------------------------------------------------------------------------------

def frame_for(session: date, rows: Mapping[str, Mapping[date, SymbolRow]],
              daily: DailySession, columns: Mapping[str, int], *,
              spy_close_previous: float, spy_symbol: str = "SPY") -> U.DecisionFrame:
    """The 09:25 frame: V1.1 rows and D-1 context only. No post-cutoff field is passed."""
    premarket, contexts = {}, {}
    for symbol, by_session in rows.items():
        entry = by_session.get(session)
        j = columns.get(symbol)
        if entry is None or j is None:
            continue
        premarket[symbol] = entry.row
        contexts[symbol] = U.DailyContext(bool(daily.eligible[j]), float(daily.close_prev[j]),
                                          float(daily.dollar_volume_prev[j]))
    spy = rows.get(spy_symbol, {}).get(session)
    spy_last = spy.row.premarket["pm_last_price"] if spy is not None else float("nan")
    return U.build_frame(session, premarket, contexts, spy_premarket_last=spy_last,
                         spy_close_previous=spy_close_previous)


def origin(has_open_d: bool, has_0930: bool) -> str:
    """Which removed V1 test(s) a V1.1 row needed: V1 = neither, A = daily open, B = 09:30."""
    if has_open_d and has_0930:
        return "V1"
    if has_0930:
        return "A"
    if has_open_d:
        return "B"
    return "AB"


VARIANTS = {"V1": ("V1",), "A_only": ("V1", "A"), "B_only": ("V1", "B"),
            "V1_1": ("V1", "A", "B", "AB")}
