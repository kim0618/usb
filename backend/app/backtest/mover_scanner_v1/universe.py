"""The point-in-time base universe: active US common stocks, as wide as the store allows.

The authority is the dated reference cache in force on the session - the newest cache dated on
or before it - exactly as the historical TOP8 reconstruction uses it. Those files hold security
type CS only, so ETFs, funds and the provider's test tickers never enter.

They do, however, hold preferred shares and baby bonds. The provider labels AGNCL, FULTP and
BHFAO type CS, and the obvious discriminator does not work: ``share_class_figi`` and
``composite_figi`` are both absent for 1,106 of 5,305 rows, among them ordinary commons such as
ACN, ACGL and AER. So the exclusion requires three signals to agree: the ticker has no
composite FIGI, *and* it extends a shorter ticker of the same CIK, *and* that shorter ticker is
itself in the universe. On 2026-07-01 that removes 83 names and keeps GOOG, GOOGL, BRK.A,
BRK.B, AGNC, FULT, ACN and ACGL.

Its known cost is named rather than hidden: a genuine share class whose own row has no FIGI is
removed too (LILAK is the measured instance). Every removal is returned, so a report can show
the list and how many of them would have reached the candidate pool.

Nothing here reads a cache dated after the session it describes.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path

#: Searched in this order; a later directory's cache for the same date wins, which only
#: matters if two collectors ever stored the same as_of.
CACHE_DIRS = ("research_universe_u1/reference", "research_universe_v2/reference", "strategy_c/raw")
NON_COMMON_RULE = "NON_COMMON_BY_CIK_PREFIX_NO_FIGI"


@dataclass(frozen=True)
class ReferenceRow:
    ticker: str
    cik: str | None
    composite_figi: str | None
    share_class_figi: str | None
    active: bool


@dataclass(frozen=True)
class BaseUniverse:
    """One dated reference cache, already reduced to a tradable common-stock universe."""

    as_of: date
    symbols: frozenset[str]
    excluded_inactive: frozenset[str]
    excluded_non_common: frozenset[str]
    checksum: str

    @property
    def source_row_count(self) -> int:
        return len(self.symbols) + len(self.excluded_inactive) + len(self.excluded_non_common)


def _rows(path: Path) -> list[ReferenceRow]:
    body = json.loads(gzip.decompress(path.read_bytes()))
    pages = body.get("pages")
    raw = ([row for page in pages for row in (page.get("results") or [])]
           if isinstance(pages, list) else (body.get("results") or []))
    out = []
    for row in raw:
        ticker = row.get("ticker")
        if not isinstance(ticker, str) or not ticker:
            continue
        out.append(ReferenceRow(
            ticker=ticker, cik=row.get("cik"), composite_figi=row.get("composite_figi"),
            share_class_figi=row.get("share_class_figi"),
            active=bool(row.get("active", True)) and row.get("delisted_utc") in (None, "")))
    return out


def non_common_tickers(rows: Sequence[ReferenceRow]) -> frozenset[str]:
    """Tickers the declared three-signal rule calls preferred shares or baby bonds."""
    present = {row.ticker for row in rows}
    by_cik: dict[str, list[str]] = {}
    for row in rows:
        if row.cik:
            by_cik.setdefault(row.cik, []).append(row.ticker)
    found = set()
    for row in rows:
        if row.composite_figi:
            continue
        siblings = by_cik.get(row.cik or "", ())
        if any(other != row.ticker and len(other) < len(row.ticker)
               and row.ticker.startswith(other) and other in present for other in siblings):
            found.add(row.ticker)
    return frozenset(found)


def load_universes(repo: Path, *, exclude_non_common: bool = True) -> list[BaseUniverse]:
    """Every dated CS cache on disk, oldest first."""
    found: dict[date, BaseUniverse] = {}
    for base in CACHE_DIRS:
        for path in sorted((repo / "data/runtime" / base / "tickers").glob("CS_*.json.gz")):
            as_of = date.fromisoformat(path.stem.replace(".json", "").split("_")[1])
            rows = _rows(path)
            non_common = non_common_tickers(rows) if exclude_non_common else frozenset()
            inactive = frozenset(row.ticker for row in rows if not row.active)
            symbols = frozenset(row.ticker for row in rows
                                if row.active and row.ticker not in non_common)
            digest = hashlib.sha256(
                json.dumps(sorted(symbols), separators=(",", ":")).encode()).hexdigest()
            found[as_of] = BaseUniverse(as_of, symbols, inactive, non_common, digest)
    return [found[key] for key in sorted(found)]


def universe_for(universes: Sequence[BaseUniverse], day: date) -> BaseUniverse | None:
    """The cache in force on ``day``: the newest dated on or before it, never a later one."""
    prior = [item for item in universes if item.as_of <= day]
    return prior[-1] if prior else None


def split_sessions(repo: Path) -> Mapping[str, frozenset[date]]:
    """Split execution dates by ticker, from the frozen splits store.

    A split executes before the open, so the previous session's stored close is on the old
    basis while the premarket prints are on the new one. Both the grouped daily store and the
    minute tape are unadjusted, so that mismatch would read as a large false gap.
    """
    out: dict[str, set[date]] = {}
    for path in sorted((repo / "data/runtime/strategy_c/raw/splits").glob("splits_*.json.gz")):
        body = json.loads(gzip.decompress(path.read_bytes()))
        pages = body.get("pages")
        rows = ([row for page in pages for row in (page.get("results") or [])]
                if isinstance(pages, list) else (body.get("results") or []))
        for row in rows:
            ticker, executed = row.get("ticker"), row.get("execution_date")
            if isinstance(ticker, str) and isinstance(executed, str):
                out.setdefault(ticker, set()).add(date.fromisoformat(executed))
    return {symbol: frozenset(days) for symbol, days in out.items()}


def eligible_symbols(universe: BaseUniverse, available: Iterable[str],
                     splits: Mapping[str, frozenset[date]], session: date,
                     *, exclude_split_sessions: bool = True) -> tuple[str, ...]:
    """Universe members that have a tape, with a split-executing session removed."""
    return tuple(sorted(
        symbol for symbol in available
        if symbol in universe.symbols
        and not (exclude_split_sessions and session in splits.get(symbol, frozenset()))))
