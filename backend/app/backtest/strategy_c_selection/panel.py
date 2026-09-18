"""Session x ticker panels built from the cached raw inputs, plus point-in-time truncation.

A ``Panel`` holds raw (never back-adjusted) daily OHLCV on the XNYS session grid, the ticker's
split records, and the dated CS reference snapshots. Split information enters the arrays in
exactly one form: ``F(t)``, the cumulative price factor of the ticker's splits executed on or
before session ``t``. ``F(t)`` needs nothing later than ``t``, so every ratio of ``x(t)/F(t)``
values is point-in-time; the label code may read ``F`` beyond D because a label is the future.

``truncate(panel, D)`` removes every session after D, every split executed after D, and every
snapshot dated after D. The PIT audit recomputes features on the truncated panel and requires
them to equal the full computation bit for bit.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
import gzip
import json
from pathlib import Path

import numpy as np

from app.backtest.strategy_c_selection.raw_fetch import grouped_path, splits_path, tickers_path

BENCHMARK = "SPY"


@dataclass(frozen=True)
class SplitEvent:
    ticker: str
    execution_date: date
    split_from: float
    split_to: float

    @property
    def price_factor(self) -> float:
        """Multiply a pre-split raw price by this to put it on the post-split basis."""
        return self.split_from / self.split_to

    @property
    def is_reverse(self) -> bool:
        return self.split_to < self.split_from


@dataclass(frozen=True)
class Panel:
    sessions: tuple[date, ...]
    tickers: tuple[str, ...]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    splits: tuple[SplitEvent, ...]
    snapshots: Mapping[date, frozenset[str]]  # as_of -> allowed CS tickers
    missing_sessions: tuple[date, ...] = ()
    _cache: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def shape(self) -> tuple[int, int]:
        return self.close.shape

    def column(self, ticker: str) -> int:
        return self.tickers.index(ticker)

    def index_of(self, session: date) -> int:
        return self.sessions.index(session)

    def split_arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(F, split count, reverse split count), cumulative over executions <= session."""
        if "splits" not in self._cache:
            t, n = self.shape
            factor = np.ones((t, n))
            count = np.zeros((t, n))
            reverse = np.zeros((t, n))
            column = {ticker: i for i, ticker in enumerate(self.tickers)}
            session_array = np.array([s.toordinal() for s in self.sessions])
            for event in self.splits:
                j = column.get(event.ticker)
                if j is None:
                    continue
                rows = session_array >= event.execution_date.toordinal()
                factor[rows, j] *= event.price_factor
                count[rows, j] += 1
                if event.is_reverse:
                    reverse[rows, j] += 1
            self._cache["splits"] = (factor, count, reverse)
        return self._cache["splits"]

    def membership(self) -> np.ndarray:
        """(T, N) bool: ticker is in the latest CS snapshot dated on or before the session."""
        if "membership" not in self._cache:
            t, n = self.shape
            out = np.zeros((t, n), dtype=bool)
            column = {ticker: i for i, ticker in enumerate(self.tickers)}
            ordered = sorted(self.snapshots)
            for i, session in enumerate(self.sessions):
                usable = [d for d in ordered if d <= session]
                if not usable:
                    continue
                cols = [column[x] for x in self.snapshots[usable[-1]] if x in column]
                out[i, cols] = True
            self._cache["membership"] = out
        return self._cache["membership"]


def truncate(panel: Panel, as_of: date) -> Panel:
    """Everything the panel knew at the close of ``as_of`` and nothing later."""
    keep = sum(1 for s in panel.sessions if s <= as_of)
    return Panel(
        sessions=panel.sessions[:keep], tickers=panel.tickers,
        open=panel.open[:keep].copy(), high=panel.high[:keep].copy(), low=panel.low[:keep].copy(),
        close=panel.close[:keep].copy(), volume=panel.volume[:keep].copy(),
        splits=tuple(e for e in panel.splits if e.execution_date <= as_of),
        snapshots={d: v for d, v in panel.snapshots.items() if d <= as_of},
        missing_sessions=tuple(s for s in panel.missing_sessions if s <= as_of))


def with_changes(panel: Panel, **changes: object) -> Panel:
    """A copy with fresh caches (tests and mutation audits)."""
    return replace(panel, _cache={}, **changes)


def _read_gz(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def load_snapshots(root: Path, dates: Iterable[date],
                   allowed_exchanges: frozenset[str]) -> dict[date, frozenset[str]]:
    out: dict[date, frozenset[str]] = {}
    for as_of in dates:
        payload = _read_gz(tickers_path(root, as_of))
        out[as_of] = frozenset(
            row["ticker"] for row in payload["results"]
            if row.get("type") == "CS" and row.get("market") == "stocks"
            and row.get("primary_exchange") in allowed_exchanges)
    return out


def load_splits(root: Path, start: date, end: date) -> tuple[SplitEvent, ...]:
    payload = _read_gz(splits_path(root, start, end))
    events = []
    seen: set[tuple[str, date]] = set()
    for row in payload["results"]:
        try:
            event = SplitEvent(str(row["ticker"]), date.fromisoformat(row["execution_date"]),
                               float(row["split_from"]), float(row["split_to"]))
        except (KeyError, TypeError, ValueError):
            continue
        if event.split_from <= 0 or event.split_to <= 0 or event.split_from == event.split_to:
            continue
        key = (event.ticker, event.execution_date)
        if key in seen:  # one ticker cannot split twice on one date; keep the first record
            continue
        seen.add(key)
        events.append(event)
    return tuple(sorted(events, key=lambda e: (e.execution_date, e.ticker)))


def load_panel(root: Path, sessions: Sequence[date], snapshot_dates: Sequence[date],
               allowed_exchanges: frozenset[str], split_range: tuple[date, date]) -> Panel:
    """Grouped daily rows for tickers that appear in any snapshot, plus the benchmark."""
    snapshots = load_snapshots(root, snapshot_dates, allowed_exchanges)
    universe = set().union(*snapshots.values()) | {BENCHMARK}
    tickers = tuple(sorted(universe))
    column = {ticker: i for i, ticker in enumerate(tickers)}
    t, n = len(sessions), len(tickers)
    arrays = {name: np.full((t, n), np.nan) for name in ("o", "h", "l", "c", "v")}
    missing: list[date] = []
    for i, session in enumerate(sessions):
        payload = _read_gz(grouped_path(root, session))
        body = payload.get("body")
        if body is None:
            missing.append(session)
            continue
        for row in body.get("results") or ():
            j = column.get(row.get("T"))
            if j is None:
                continue
            values = [row.get(k) for k in ("o", "h", "l", "c", "v")]
            if any(v is None for v in values):
                continue
            for name, value in zip(("o", "h", "l", "c", "v"), values):
                arrays[name][i, j] = float(value)
    return Panel(tuple(sessions), tickers, arrays["o"], arrays["h"], arrays["l"], arrays["c"],
                 arrays["v"], load_splits(root, *split_range), snapshots, tuple(missing))
