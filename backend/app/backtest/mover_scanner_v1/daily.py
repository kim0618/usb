"""Regular-session daily baselines for the whole market, from the grouped daily store.

Two of the scanner's inputs are daily, not intraday: the previous regular close every gap is
measured against, and the 20-session volume baselines. Both come from the market-wide grouped
daily files, which are the only full-market daily source in the store and are unadjusted
(``adjusted: false``), the same basis as the minute tape.

The averages are built the way ``build_premarket_context`` builds Production's denominator: the
daily bars that exist in the 45 calendar days before the session, then the last 20 of them,
then their mean. That is mirrored rather than improved, because the gate-pass counts this study
reports have to be the gate's own arithmetic.

Known difference from Production, documented not corrected: paper's denominator is Kiwoom's
daily volume, and grouped daily volume is a different tally of the same session (the store's
own note records grouped and per-symbol daily disagreeing on volume). It is a proxy for the
denominator, and the gap numerator - previous close - is unaffected by the choice.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
import gzip
import json
from pathlib import Path

import numpy as np

GROUPED_DIRS = ("strategy_c/raw/grouped",
                "strategy_b_e0/mirror/market_data/raw/massive/grouped_daily")
#: Production's ``build_premarket_context`` window for the volume denominator.
DAILY_LOOKBACK_DAYS = 45


def grouped_path(repo: Path, day: date) -> Path | None:
    for base in GROUPED_DIRS:
        for candidate in (repo / "data/runtime" / base / f"{day.isoformat()}.json.gz",
                          repo / "data/runtime" / base / str(day.year) / f"{day.isoformat()}.json.gz"):
            if candidate.is_file():
                return candidate
    return None


def grouped_rows(path: Path) -> list[dict]:
    body = json.loads(gzip.decompress(path.read_bytes()))
    inner = body.get("body")
    return (inner or body).get("results") or []


@dataclass(frozen=True)
class DailyPanel:
    """One close and one volume per (symbol, grid session); NaN where the session has no bar."""

    sessions: tuple[date, ...]
    close: Mapping[str, np.ndarray]
    volume: Mapping[str, np.ndarray]

    @property
    def index(self) -> Mapping[date, int]:
        return {day: position for position, day in enumerate(self.sessions)}

    def previous_close(self, symbol: str, position: int) -> float | None:
        """The exact previous grid session's close, or None when it is absent or not positive."""
        if position <= 0 or symbol not in self.close:
            return None
        value = float(self.close[symbol][position - 1])
        return value if np.isfinite(value) and value > 0 else None

    def _window(self, symbol: str, position: int, baseline_sessions: int,
                ) -> tuple[np.ndarray, np.ndarray]:
        oldest = self.sessions[position] - timedelta(days=DAILY_LOOKBACK_DAYS)
        first = next((k for k in range(position) if self.sessions[k] >= oldest), position)
        close = self.close[symbol][first:position]
        volume = self.volume[symbol][first:position]
        present = np.flatnonzero(np.isfinite(close) & np.isfinite(volume))[-baseline_sessions:]
        return close[present], volume[present]

    def baselines(self, symbol: str, position: int, baseline_sessions: int,
                  ) -> tuple[float | None, float | None, int]:
        """``(average daily volume, average daily dollar volume, sessions used)``."""
        if symbol not in self.close:
            return None, None, 0
        close, volume = self._window(symbol, position, baseline_sessions)
        if close.size == 0:
            return None, None, 0
        return float(volume.mean()), float((close * volume).mean()), int(close.size)


def load_panel(repo: Path, sessions: Sequence[date], symbols: frozenset[str] | None = None,
               ) -> DailyPanel:
    """Read the grouped daily files for ``sessions``; a missing file is a missing grid session."""
    close: dict[str, np.ndarray] = {}
    volume: dict[str, np.ndarray] = {}
    span = len(sessions)
    for position, day in enumerate(sessions):
        path = grouped_path(repo, day)
        if path is None:
            raise FileNotFoundError(f"grouped daily is missing for {day.isoformat()}")
        for row in grouped_rows(path):
            symbol, bar_close, bar_volume = row.get("T"), row.get("c"), row.get("v")
            if not isinstance(symbol, str) or symbol not in (symbols or {symbol}):
                continue
            if not isinstance(bar_close, (int, float)) or not isinstance(bar_volume, (int, float)):
                continue
            if symbol not in close:
                close[symbol] = np.full(span, np.nan)
                volume[symbol] = np.full(span, np.nan)
            close[symbol][position] = float(bar_close)
            volume[symbol][position] = float(bar_volume)
    return DailyPanel(tuple(sessions), close, volume)
