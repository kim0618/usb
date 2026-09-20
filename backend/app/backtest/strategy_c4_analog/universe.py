"""Session x ticker inputs for C-4: the C panel plus the two fields C never read (vw) and FIGI.

The grouped daily cache already carries ``vw`` (the session VWAP) and the CS reference snapshots
already carry ``composite_figi``; neither is used by any existing strategy, so C-4 reads them
from the same frozen bytes without a single new request. Everything else - the session grid, the
split factor, membership, eligibility, the matching buckets - comes from
``app.backtest.strategy_c_selection`` unchanged.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
import gzip
import json
from pathlib import Path

import numpy as np

from app.backtest.strategy_c_selection.panel import BENCHMARK, Panel, load_splits
from app.backtest.strategy_c_selection.raw_fetch import grouped_path, tickers_path
from app.backtest.strategy_c_selection.rules import SelectionRules

C_RAW_ROOT = Path("data/runtime/strategy_c/raw")
SNAPSHOT_DATES = (date(2024, 10, 1), date(2025, 1, 2), date(2025, 4, 1), date(2025, 7, 1),
                  date(2025, 10, 1), date(2026, 1, 2), date(2026, 4, 1), date(2026, 7, 1))
SPLIT_RANGE = (date(2024, 9, 16), date(2026, 9, 16))


@dataclass(frozen=True)
class SnapshotRow:
    as_of: date
    cik_by_ticker: dict[str, str]
    figi_by_ticker: dict[str, str]
    tickers: frozenset[str]


def _read_gz(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def load_reference(root: Path, dates: Sequence[date],
                   allowed_exchanges: frozenset[str]) -> tuple[SnapshotRow, ...]:
    """CS membership, CIK and composite FIGI of one snapshot, read from the frozen bytes."""
    out: list[SnapshotRow] = []
    for as_of in dates:
        payload = _read_gz(tickers_path(root, as_of))
        members, cik, figi = set(), {}, {}
        for row in payload["results"]:
            ticker = row.get("ticker")
            if not ticker:
                continue
            if (row.get("type") == "CS" and row.get("market") == "stocks"
                    and row.get("primary_exchange") in allowed_exchanges):
                members.add(str(ticker))
            raw_cik = (row.get("cik") or "").strip()
            if raw_cik:
                cik[str(ticker)] = f"{int(raw_cik):010d}"
            composite = (row.get("composite_figi") or "").strip()
            if composite:
                figi[str(ticker)] = composite
        out.append(SnapshotRow(date.fromisoformat(payload["as_of"]), cik, figi, frozenset(members)))
    return tuple(sorted(out, key=lambda s: s.as_of))


def load_panel_and_vw(root: Path, sessions: Sequence[date], snapshots: Sequence[SnapshotRow],
                      split_range: tuple[date, date]) -> tuple[Panel, np.ndarray]:
    """One pass over the grouped cache: the C panel plus the session VWAP array it discards."""
    membership = {s.as_of: s.tickers for s in snapshots}
    universe = set().union(*membership.values()) | {BENCHMARK}
    tickers = tuple(sorted(universe))
    column = {ticker: i for i, ticker in enumerate(tickers)}
    t, n = len(sessions), len(tickers)
    arrays = {name: np.full((t, n), np.nan) for name in ("o", "h", "l", "c", "v", "vw")}
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
            if any(value is None for value in values):
                continue
            for name, value in zip(("o", "h", "l", "c", "v"), values):
                arrays[name][i, j] = float(value)
            vw = row.get("vw")
            if vw is not None:
                arrays["vw"][i, j] = float(vw)
    panel = Panel(tuple(sessions), tickers, arrays["o"], arrays["h"], arrays["l"], arrays["c"],
                  arrays["v"], load_splits(root, *split_range), dict(membership), tuple(missing))
    return panel, arrays["vw"]


def entity_codes(panel: Panel, snapshots: Sequence[SnapshotRow]) -> np.ndarray:
    """(T, N) int32 FIGI code, -1 when unknown; the snapshot with the latest as_of <= session."""
    t, n = panel.shape
    codes = np.full((t, n), -1, dtype=np.int32)
    interned: dict[str, int] = {}
    ordered = sorted(snapshots, key=lambda s: s.as_of)
    for i, session in enumerate(panel.sessions):
        usable = [s for s in ordered if s.as_of <= session]
        if not usable:
            continue
        current = usable[-1]
        for ticker, figi in current.figi_by_ticker.items():
            j = _column(panel, ticker)
            if j is None:
                continue
            code = interned.get(figi)
            if code is None:
                code = len(interned)
                interned[figi] = code
            codes[i, j] = code
    return codes


def cik_codes(panel: Panel, snapshots: Sequence[SnapshotRow]) -> tuple[np.ndarray, tuple[str, ...]]:
    """(T, N) index into the returned CIK list, -1 for UNKNOWN_MAPPING.

    The C-E0 rule, unchanged: the CIK of the snapshot with the latest ``as_of <= D``, and a
    ticker whose CIK differs in the following snapshot is unmapped for the whole period.
    """
    t, n = panel.shape
    codes = np.full((t, n), -1, dtype=np.int32)
    ordered = sorted(snapshots, key=lambda s: s.as_of)
    order: dict[str, int] = {}
    names: list[str] = []
    for position, current in enumerate(ordered):
        following = ordered[position + 1] if position + 1 < len(ordered) else None
        rows = [i for i, session in enumerate(panel.sessions)
                if session >= current.as_of and (following is None or session < following.as_of)]
        if not rows:
            continue
        low, high = min(rows), max(rows)
        for ticker, cik in current.cik_by_ticker.items():
            j = _column(panel, ticker)
            if j is None:
                continue
            if following is not None:
                nxt = following.cik_by_ticker.get(ticker)
                if nxt is not None and nxt != cik:
                    continue  # CIK_CHANGED_ACROSS_D
            code = order.get(cik)
            if code is None:
                code = len(names)
                order[cik] = code
                names.append(cik)
            codes[low:high + 1, j] = code
    return codes, tuple(names)


_COLUMN_CACHE: dict[int, dict[str, int]] = {}


def _column(panel: Panel, ticker: str) -> int | None:
    key = id(panel)
    table = _COLUMN_CACHE.get(key)
    if table is None:
        table = {name: i for i, name in enumerate(panel.tickers)}
        _COLUMN_CACHE[key] = table
    return table.get(ticker)


def usable_sessions(root: Path, sessions: Sequence[date]) -> tuple[date, ...]:
    """The C rule: leading sessions outside the provider window are dropped, a gap is refused."""
    status = []
    for session in sessions:
        payload = _read_gz(grouped_path(root, session))
        status.append((session, "body" in payload))
    first = next(i for i, (_, ok) in enumerate(status) if ok)
    tail = status[first:]
    gaps = [s.isoformat() for s, ok in tail if not ok]
    if gaps:
        raise RuntimeError(f"grouped daily missing inside the grid: {gaps}")
    return tuple(s for s, _ in tail)


def all_sessions(root: Path) -> tuple[date, ...]:
    return tuple(sorted(date.fromisoformat(p.name.split(".")[0])
                        for p in (root / "grouped").glob("*.json.gz")))


def base_eligible(features, rules: SelectionRules) -> np.ndarray:
    """member and has_bar and base history and hard filter, before the CA exclusion."""
    del rules
    return features.member & features.has_bar & features.base_history & features.hard_filter
