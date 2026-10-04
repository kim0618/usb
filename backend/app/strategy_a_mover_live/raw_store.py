"""The raw premarket bar persistence contract. Keep the bars, not only the aggregates.

E's collection walk read minute bars and stored only ``pm_dollar_volume``, ``pm_bars``,
``has_open_0930`` and a staging flag over one undivided ``[04:00, 09:24]`` window. That choice
is why A's 20-session share-volume baseline cannot be reconstructed from 159 stored sessions:
the share volume, the prices, the highs and the lows were read and then dropped, and the window
cannot be split at 09:15. E's own contract note records it in one line - "status rows only;
bars were not persisted".

This module is the correction, and it is deliberately dull. Every minute A's pass reads is
written out with its identity and its provenance, so a feature contract that changes later is
recomputed from stored bars instead of recollected from the network:

    symbol, session_date, bar_timestamp, open, high, low, close, volume,
    provider, collector_version, observed_at

**Duplicate timestamps are a correctness question, not a nuisance.** The rolling cache and the
finalization pass both read the same minute, and the tick lane re-derives minutes the minute
lane already had. Two readings of one minute that agree are one fact, so the second is dropped.
Two readings that disagree mean one of them is wrong, and the pair is quarantined with both
values rather than silently resolved: picking the later one would let a late partial page
overwrite a complete minute. ``strict`` raises instead, for a caller that would rather fail.

Layout follows the existing runtime convention - one gzipped JSON file per session under
``data/runtime/strategy_a_mover_live/`` - so no new repository structure is introduced.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.strategy_a_mover_live import contract as LC

RAW_ROOT = "data/runtime/strategy_a_mover_live/raw"
#: The persisted record's own format name, so a reader can refuse an unknown shape.
RAW_FORMAT = "a-mover-live-raw-minute-v1"
FIELDS = ("symbol", "session_date", "bar_timestamp", "open", "high", "low", "close", "volume",
          "provider", "collector_version", "observed_at")


class RawConflict(RuntimeError):
    """One minute was observed twice with different values."""


@dataclass(frozen=True)
class RawBar:
    """One minute of one symbol, with the provenance that makes it re-readable."""

    symbol: str
    session_date: date
    bar_timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    provider: str = LC.LIVE_PROVIDER
    collector_version: str = LC.COLLECTOR_VERSION
    observed_at: datetime | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.symbol, self.bar_timestamp.isoformat()

    @property
    def values(self) -> tuple[float, float, float, float, float]:
        """The five numbers a duplicate has to agree on. Provenance is not part of identity."""
        return (self.open, self.high, self.low, self.close, self.volume)

    def row(self) -> dict[str, Any]:
        body = asdict(self)
        body["session_date"] = self.session_date.isoformat()
        body["bar_timestamp"] = self.bar_timestamp.isoformat()
        body["observed_at"] = self.observed_at.isoformat() if self.observed_at else None
        return {name: body[name] for name in FIELDS}


@dataclass(frozen=True)
class RawQuarantine:
    """A disagreement, kept with both readings so it can be reviewed rather than guessed."""

    symbol: str
    bar_timestamp: datetime
    stored: tuple[float, float, float, float, float]
    offered: tuple[float, float, float, float, float]
    stored_observed_at: datetime | None
    offered_observed_at: datetime | None

    def row(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "bar_timestamp": self.bar_timestamp.isoformat(),
                "stored": list(self.stored), "offered": list(self.offered),
                "stored_observed_at": (self.stored_observed_at.isoformat()
                                       if self.stored_observed_at else None),
                "offered_observed_at": (self.offered_observed_at.isoformat()
                                        if self.offered_observed_at else None)}


class RawSession:
    """One session's accumulated bars, deduped on agreement and quarantined on disagreement."""

    def __init__(self, session: date, *, strict: bool = False) -> None:
        self.session = session
        self.strict = strict
        self._bars: dict[tuple[str, str], RawBar] = {}
        self._quarantine: list[RawQuarantine] = []
        self.accepted = 0
        self.deduped = 0

    def __len__(self) -> int:
        return len(self._bars)

    @property
    def quarantine(self) -> tuple[RawQuarantine, ...]:
        return tuple(self._quarantine)

    def add(self, bar: RawBar) -> str:
        """``ACCEPTED``, ``DEDUPED`` or ``QUARANTINED``."""
        if bar.session_date != self.session:
            raise ValueError(f"{bar.symbol} {bar.bar_timestamp} is not in session {self.session}")
        existing = self._bars.get(bar.key)
        if existing is None:
            self._bars[bar.key] = bar
            self.accepted += 1
            return "ACCEPTED"
        if existing.values == bar.values:
            self.deduped += 1
            return "DEDUPED"
        conflict = RawQuarantine(bar.symbol, bar.bar_timestamp, existing.values, bar.values,
                                 existing.observed_at, bar.observed_at)
        if self.strict:
            raise RawConflict(f"{bar.symbol} {bar.bar_timestamp.isoformat()}: "
                              f"{existing.values} != {bar.values}")
        self._quarantine.append(conflict)
        return "QUARANTINED"

    def extend(self, bars: Iterable[RawBar]) -> dict[str, int]:
        counts = {"ACCEPTED": 0, "DEDUPED": 0, "QUARANTINED": 0}
        for bar in bars:
            counts[self.add(bar)] += 1
        return counts

    def bars(self) -> Iterator[RawBar]:
        for key in sorted(self._bars):
            yield self._bars[key]

    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted({bar.symbol for bar in self._bars.values()}))

    def payload(self) -> dict[str, Any]:
        rows = [bar.row() for bar in self.bars()]
        body = {"format": RAW_FORMAT, "session": self.session.isoformat(),
                "provider": LC.LIVE_PROVIDER, "collector_version": LC.COLLECTOR_VERSION,
                "fields": list(FIELDS), "symbols": len(self.symbols()),
                "bars": len(rows), "accepted": self.accepted, "deduped": self.deduped,
                "quarantined": len(self._quarantine),
                "quarantine": [item.row() for item in self._quarantine], "rows": rows}
        body["digest"] = hashlib.sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return body


def session_path(repo: Path, session: date, root: str = RAW_ROOT) -> Path:
    return Path(repo) / root / f"{session.isoformat()}.json.gz"


def write_session(repo: Path, raw: RawSession, root: str = RAW_ROOT) -> Path:
    """Atomic gzipped write of one session's bars, in the existing runtime convention."""
    path = session_path(repo, raw.session, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_bytes(gzip.compress(
        (json.dumps(raw.payload(), sort_keys=True, separators=(",", ":")) + "\n").encode()))
    os.replace(temporary, path)
    return path


def read_session(repo: Path, session: date, root: str = RAW_ROOT) -> Mapping[str, Any]:
    path = session_path(repo, session, root)
    body = json.loads(gzip.decompress(path.read_bytes()))
    if body.get("format") != RAW_FORMAT:
        raise ValueError(f"{path} is not {RAW_FORMAT}")
    return body


def bars_from_cache(symbol: str, session: date, minutes: Mapping[int, Sequence[float]],
                    observed_at: datetime, *, cut_minute: int | None = None,
                    tzinfo=None) -> list[RawBar]:
    """Turn one ``SymbolCache.bars`` mapping into raw rows, newest-first order irrelevant.

    ``minutes`` is the collector's ``minute of day -> [o, h, l, c, v]``. ``cut_minute`` keeps
    the point-in-time rule explicit at the persistence boundary too: a minute at or after the
    cut is not written into A's session file, so a replay of the file cannot see past the cut.
    """
    from datetime import datetime as _datetime, time as _time
    from app.strategy_a_mover_live.schedule import ET
    zone = tzinfo or ET
    out: list[RawBar] = []
    for minute, values in sorted(minutes.items()):
        if cut_minute is not None and minute >= cut_minute:
            continue
        stamp = _datetime.combine(session, _time(minute // 60, minute % 60), tzinfo=zone)
        open_, high, low, close, volume = (float(values[index]) for index in range(5))
        out.append(RawBar(symbol=symbol, session_date=session, bar_timestamp=stamp, open=open_,
                          high=high, low=low, close=close, volume=volume,
                          observed_at=observed_at))
    return out
