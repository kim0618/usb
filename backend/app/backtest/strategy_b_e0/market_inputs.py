"""The two market-wide inputs B-E0 reads beside its minute bars: grouped daily and the splits dump.

Both are read from the local mirror, never from Drive at replay time, and both are decided by the
contract's data_adapter block rather than by this module:

* **Grouped daily is B-E0's daily authority (D2).** Strategy A deliberately does not use it
  (USB-DAILY-AUTHORITY-V1); B already computes its scope from it, so using it for the D-1 audit and
  the first-appearance count keeps B consistent with itself. Only universe members are held, as a
  dense (session x member) array, so memory stays bounded however many tickers the market lists.
* **The splits dump is the only split source.** Absence of a record means no split, so "split
  records present for every symbol" is the wrong question; "the dump covers every ticker" is the
  right one. The dump carries conflicting duplicates (the same ticker and date with two different
  ratios). A conflicting key is dropped from every symbol and reported, never resolved by picking
  one: there is no rule that would make the pick defensible.

Nothing here adjusts a price. Splits leave as records; the pure layer applies them point in time.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
import gzip
import json
from math import isfinite
from pathlib import Path

import numpy as np

from app.backtest.historical_store.b_universe import GROUPED_DIR
from app.strategy_b.models import OfficialDailyBar
from app.strategy_b.split_adjustment import SplitRecord

SPLITS_DIR = "market_data/raw/massive/splits"
DAILY_AUTHORITY = "MASSIVE_GROUPED_DAILY"


class MarketInputError(ValueError):
    """A market input could not be read the way the contract says it must be."""


# ---- grouped daily ----------------------------------------------------------------------------


def grouped_path(root: Path, day: date) -> Path:
    return root / GROUPED_DIR / f"{day.year:04d}" / f"{day.isoformat()}.json.gz"


@dataclass(slots=True)
class GroupedDaily:
    """Grouped daily OHLCV for universe members over the grid, as dense arrays.

    ``present[i, j]`` is whether member j had a grouped row on ``sessions[i]``. Values are NaN
    where absent. Rows outside the member set are never held.
    """

    sessions: tuple[date, ...]
    members: tuple[str, ...]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    present: np.ndarray
    missing_sessions: tuple[date, ...]
    _session_index: dict[date, int] = field(default_factory=dict)
    _member_index: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._session_index = {day: i for i, day in enumerate(self.sessions)}
        self._member_index = {symbol: j for j, symbol in enumerate(self.members)}

    def _cell(self, symbol: str, day: date) -> tuple[int, int] | None:
        i, j = self._session_index.get(day), self._member_index.get(symbol)
        return None if i is None or j is None else (i, j)

    def volume_on(self, symbol: str, day: date) -> float:
        cell = self._cell(symbol, day)
        return float("nan") if cell is None else float(self.volume[cell])

    def official(self, symbol: str, day: date) -> OfficialDailyBar | None:
        """D's grouped bar as the audit's official daily, or None when it cannot stand as one."""
        cell = self._cell(symbol, day)
        if cell is None or not self.present[cell]:
            return None
        values = [float(array[cell]) for array in (self.open, self.high, self.low, self.close)]
        volume = float(self.volume[cell])
        if not all(isfinite(v) and v > 0 for v in values) or not isfinite(volume) or volume < 0:
            return None
        return OfficialDailyBar(day, *values, volume)

    def first_appearance(self, symbol: str) -> date | None:
        j = self._member_index.get(symbol)
        if j is None:
            return None
        hits = np.flatnonzero(self.present[:, j])
        return None if hits.size == 0 else self.sessions[int(hits[0])]

    def sessions_since_first_appearance(self, symbol: str, day: date) -> int | None:
        """Grid sessions strictly before ``day`` since the ticker first had a grouped row.

        Point in time: the first appearance is the minimum date the ticker is present, and a
        minimum that lies before ``day`` is the same whether computed over the whole grid or over
        dates before ``day`` only. A first appearance on or after ``day`` counts as zero.
        """
        first = self.first_appearance(symbol)
        i = self._session_index.get(day)
        if first is None or i is None:
            return None
        return max(0, i - self._session_index[first])


def load_grouped_daily(root: Path, sessions: Sequence[date], members: Iterable[str]) -> GroupedDaily:
    ordered = tuple(sorted(set(members)))
    column = {symbol: j for j, symbol in enumerate(ordered)}
    shape = (len(sessions), len(ordered))
    arrays = {name: np.full(shape, np.nan) for name in ("o", "h", "l", "c", "v")}
    present = np.zeros(shape, dtype=bool)
    missing: list[date] = []
    for i, day in enumerate(sessions):
        path = grouped_path(root, day)
        if not path.is_file():
            missing.append(day)
            continue
        body = json.loads(gzip.decompress(path.read_bytes()))
        results = (body.get("body") or {}).get("results")
        if results is None:
            missing.append(day)
            continue
        for row in results:
            j = column.get(row.get("T"))
            if j is None:
                continue
            present[i, j] = True
            for key in ("o", "h", "l", "c", "v"):
                value = row.get(key)
                if value is not None:
                    arrays[key][i, j] = float(value)
    return GroupedDaily(tuple(sessions), ordered, arrays["o"], arrays["h"], arrays["l"],
                        arrays["c"], arrays["v"], present, tuple(missing))


# ---- splits -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SplitSource:
    by_symbol: Mapping[str, tuple[SplitRecord, ...]]
    conflicts: Mapping[tuple[str, date], tuple[tuple[float, float], ...]]
    invalid: tuple[tuple[str, str, str], ...]
    identical_duplicates: int
    records_read: int
    path: Path

    def records(self, symbol: str) -> tuple[SplitRecord, ...]:
        return self.by_symbol.get(symbol, ())

    def conflicts_for(self, symbols: Iterable[str]) -> dict[tuple[str, date], tuple]:
        wanted = set(symbols)
        return {key: value for key, value in self.conflicts.items() if key[0] in wanted}


def splits_path(root: Path) -> Path:
    found = sorted((root / SPLITS_DIR).glob("splits_*.json.gz"))
    if len(found) != 1:
        raise MarketInputError(f"expected exactly one splits dump under {root / SPLITS_DIR}, "
                               f"found {[p.name for p in found]}")
    return found[0]


def load_splits(root: Path) -> SplitSource:
    path = splits_path(root)
    body = json.loads(gzip.decompress(path.read_bytes()))
    grouped: dict[tuple[str, date], set[tuple[float, float]]] = {}
    invalid: list[tuple[str, str, str]] = []
    for row in body.get("results") or ():
        ticker, day = row.get("ticker"), row.get("execution_date")
        try:
            pair = (float(row["split_from"]), float(row["split_to"]))
            key = (str(ticker), date.fromisoformat(str(day)))
        except (KeyError, TypeError, ValueError) as error:
            invalid.append((str(ticker), str(day), f"unparseable: {error}"))
            continue
        if not all(isfinite(v) and v > 0 for v in pair) or pair[0] == pair[1]:
            invalid.append((key[0], key[1].isoformat(), "SplitRecord would refuse this ratio"))
            continue
        grouped.setdefault(key, set()).add(pair)

    by_symbol: dict[str, list[SplitRecord]] = {}
    conflicts: dict[tuple[str, date], tuple[tuple[float, float], ...]] = {}
    duplicates = 0
    records_read = len(body.get("results") or ())
    for key in sorted(grouped):
        pairs = grouped[key]
        if len(pairs) > 1:
            conflicts[key] = tuple(sorted(pairs))
            continue
        (split_from, split_to), = pairs
        by_symbol.setdefault(key[0], []).append(SplitRecord(key[1], split_from, split_to))
    seen: dict[tuple[str, date], int] = {}
    for row in body.get("results") or ():
        try:
            key = (str(row["ticker"]), date.fromisoformat(str(row["execution_date"])))
        except (KeyError, ValueError):
            continue
        seen[key] = seen.get(key, 0) + 1
    duplicates = sum(1 for key, count in seen.items() if count > 1 and key not in conflicts)
    return SplitSource({s: tuple(r) for s, r in by_symbol.items()}, conflicts, tuple(invalid),
                       duplicates, records_read, path)
