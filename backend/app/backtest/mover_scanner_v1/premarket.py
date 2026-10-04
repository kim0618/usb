"""Premarket features for the whole tape, at a declared cut inside the premarket.

The cut is point-in-time by construction: a bar is read only if its start minute is strictly
below the cut, so every bar used had already closed when the scan was taken. The 09:15 ET
default therefore sees 04:00 through 09:14 inclusive and nothing of the session it will trade.

Two windows are measured, and they answer different questions. The **scan window** [04:00, cut)
is what the scanner may know. The **gate window** [04:00, 09:30) is what Production's premarket
gate sees when it evaluates at the open; it exists here only so this study can count how many
candidates the deployed gate would have admitted, and no score reads it.

Minute boundaries come from the exchange calendar's real ET instants per session, so no bar is
shifted by an hour across a DST transition. Premarket minute bars exist only where a trade
printed, so a quiet session is a session with few bars, not a missing one: coverage is decided
by the mirror's ledgers, and a covered session with no print contributes a zero to the
relative-volume baseline rather than dropping out of it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np

from app.backtest.strategy_b_e0.session_cache import SessionCache
from app.backtest.mover_scanner_v1.config import (
    PREMARKET_START_MINUTE, REGULAR_OPEN_MINUTE, MoverScannerConfig,
)
from app.strategy_b.session import ET

MS_PER_MINUTE = 60_000
#: Open, high, low, close, volume column order of the cache's value matrix.
OPEN, HIGH, LOW, CLOSE, VOLUME = 0, 1, 2, 3, 4

SCAN_FIELDS = ("pm_bars", "pm_volume", "pm_dollar_volume", "pm_first_price", "pm_last_price",
               "pm_high", "pm_low", "pm_up_bar_share", "pm_late_reference_price")
GATE_FIELDS = ("gate_bars", "gate_volume", "gate_last_price")
FIELDS = SCAN_FIELDS + GATE_FIELDS


@dataclass(frozen=True)
class PremarketPanel:
    """``field -> (symbol, session)`` matrices. NaN means the pair has no covered premarket."""

    symbols: tuple[str, ...]
    sessions: tuple[date, ...]
    values: Mapping[str, np.ndarray]
    covered: np.ndarray
    cut_minute: int
    cache_digest: str

    @property
    def symbol_index(self) -> Mapping[str, int]:
        return {symbol: row for row, symbol in enumerate(self.symbols)}

    @property
    def session_index(self) -> Mapping[date, int]:
        return {day: column for column, day in enumerate(self.sessions)}

    def field(self, name: str, symbol_row: int, session_column: int) -> float:
        return float(self.values[name][symbol_row, session_column])

    def rvol_baseline(self, symbol_row: int, session_column: int, sessions: int,
                      ) -> tuple[float | None, int]:
        """Median scan-window volume over the covered sessions before this one.

        The median, not the mean, because premarket volume is dominated by occasional
        event days; this is the same normalisation the audited V2 premarket history uses.
        """
        if session_column == 0:
            return None, 0
        window = self.values["pm_volume"][symbol_row, :session_column]
        covered = self.covered[symbol_row, :session_column]
        usable = window[covered][-sessions:]
        if usable.size < sessions:
            return None, int(usable.size)
        value = float(np.median(usable))
        return (value if np.isfinite(value) else None), int(usable.size)


def session_premarket_start_ms(day: date) -> int:
    """04:00 ET of the session, as epoch milliseconds, over the zone's real offset."""
    return int(datetime.combine(day, time(PREMARKET_START_MINUTE // 60,
                                          PREMARKET_START_MINUTE % 60), tzinfo=ET).timestamp()
               * 1000)


def _aggregate(offsets: np.ndarray, values: np.ndarray, limit: int, late_start: int,
               ) -> dict[str, float]:
    """Scan-window aggregates for one (symbol, session), from already-sliced arrays."""
    inside = np.flatnonzero((offsets >= 0) & (offsets < limit))
    if inside.size == 0:
        return {name: 0.0 if name in ("pm_bars", "pm_volume", "pm_dollar_volume") else np.nan
                for name in SCAN_FIELDS}
    rows = values[inside]
    close, volume = rows[:, CLOSE], rows[:, VOLUME]
    before_late = np.flatnonzero(offsets[inside] < late_start)
    reference = float(close[before_late[-1]]) if before_late.size else float(rows[0, OPEN])
    return {
        "pm_bars": float(inside.size),
        "pm_volume": float(volume.sum()),
        "pm_dollar_volume": float((close * volume).sum()),
        "pm_first_price": float(rows[0, OPEN]),
        "pm_last_price": float(close[-1]),
        "pm_high": float(rows[:, HIGH].max()),
        "pm_low": float(rows[:, LOW].min()),
        "pm_up_bar_share": float(np.count_nonzero(close > rows[:, OPEN]) / inside.size),
        "pm_late_reference_price": reference,
    }


def _gate_aggregate(offsets: np.ndarray, values: np.ndarray, limit: int) -> dict[str, float]:
    inside = np.flatnonzero((offsets >= 0) & (offsets < limit))
    if inside.size == 0:
        return {"gate_bars": 0.0, "gate_volume": 0.0, "gate_last_price": np.nan}
    rows = values[inside]
    return {"gate_bars": float(inside.size), "gate_volume": float(rows[:, VOLUME].sum()),
            "gate_last_price": float(rows[-1, CLOSE])}


def build_panel(cache: SessionCache, config: MoverScannerConfig,
                sessions: Sequence[date] | None = None,
                symbols: Sequence[str] | None = None,
                progress=None) -> PremarketPanel:
    """One pass over the tape, producing every premarket field for every covered pair."""
    grid = tuple(sessions if sessions is not None else cache.sessions)
    members = tuple(symbols if symbols is not None else cache.symbols)
    span = (len(members), len(grid))
    values = {name: np.full(span, np.nan) for name in FIELDS}
    covered = np.zeros(span, dtype=bool)
    scan_limit = config.scan_cut_minute - PREMARKET_START_MINUTE
    gate_limit = REGULAR_OPEN_MINUTE - PREMARKET_START_MINUTE
    late_start = max(0, scan_limit - config.momentum_late_window_minutes)
    starts = {day: session_premarket_start_ms(day) for day in grid}

    for column, day in enumerate(grid):
        origin = starts[day]
        for row, symbol in enumerate(members):
            coverage = cache.coverage(symbol, day)
            if coverage is None:
                continue
            covered[row, column] = True
            if coverage.rows == 0:
                for name in ("pm_bars", "pm_volume", "pm_dollar_volume"):
                    values[name][row, column] = 0.0
                values["gate_bars"][row, column] = 0.0
                values["gate_volume"][row, column] = 0.0
                continue
            stamps = np.asarray(cache._t[coverage.start:coverage.end])
            if stamps.size > 1 and not bool(np.all(np.diff(stamps) > 0)):
                raise ValueError(f"{symbol} {day} cache rows are not strictly time ordered")
            # Only the premarket is ever read, so narrow the value read to it before touching
            # the memory map: the stored day runs to 20:00 ET and is three times as wide.
            low = int(np.searchsorted(stamps, origin, side="left"))
            high = int(np.searchsorted(stamps, origin + gate_limit * MS_PER_MINUTE, side="left"))
            if high <= low:
                for name in ("pm_bars", "pm_volume", "pm_dollar_volume",
                             "gate_bars", "gate_volume"):
                    values[name][row, column] = 0.0
                continue
            matrix = np.asarray(cache._v[coverage.start + low:coverage.start + high,
                                         :VOLUME + 1])
            offsets = (stamps[low:high] - origin) // MS_PER_MINUTE
            for name, value in _aggregate(offsets, matrix, scan_limit, late_start).items():
                values[name][row, column] = value
            for name, value in _gate_aggregate(offsets, matrix, gate_limit).items():
                values[name][row, column] = value
        if progress is not None:
            progress(column + 1, len(grid))
    return PremarketPanel(members, grid, values, covered, config.scan_cut_minute, cache.digest)
