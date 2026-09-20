"""The only D module that knows where bytes live (D1 Pre-flight Contract §8).

It turns the common Historical Store - a frozen snapshot plus the C raw freeze document it
embeds - into the logical API the rest of Strategy D speaks: a canonical session grid, an
as-of view of the daily panel, membership, split factors and stable identities. Every other D
module works in grid indices and ticker strings and can be tested on a synthetic panel.

Two properties are load-time contracts, not later checks: every file read is verified against
the sha256 the freeze recorded (G6, R2), and the grid passes G1~G8 before any array is built.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.strategy_c_selection.panel import Panel, SplitEvent
from app.backtest.strategy_d_analog.models import (
    DAILY_AUTHORITY, FreezeIdentity, HardFail, PointInTimeViolation, SessionGrid, SymbolIdentity,
)
from app.market.calendar import MarketCalendar

SNAPSHOT_DIR = "market_data/metadata/historical_snapshot"
SNAPSHOT_FILE = "snapshot.json"
FREEZE_FILE = "c_raw_freeze.json"
GROUPED = "GROUPED_DAILY"
TICKERS = "REFERENCE_TICKERS_CS"
SPLITS = "SPLITS"
OK = "OK"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class AsOfView:
    """Rows ``0..as_of_idx`` of the panel and nothing later; the arrays simply end at ``as_of_idx``.

    A read of a later session is impossible rather than merely forbidden, which is why the PIT
    audit can plant future bars and see no difference in the result (D0 PIT #2).
    """

    as_of_idx: int
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    factor: np.ndarray
    split_count: np.ndarray

    @property
    def price(self) -> np.ndarray:
        """``P(t) = raw close(t) / F(t)``, the split-normalized path D encodes."""
        return self.close / self.factor

    def row(self, index: int) -> int:
        if index < 0 or index > self.as_of_idx:
            raise PointInTimeViolation(f"row {index} is outside the as-of view [0, {self.as_of_idx}]")
        return index


@dataclass
class DailyHistory:
    """A frozen daily dataset bound to one ``FreezeIdentity``, served point-in-time."""

    grid: SessionGrid
    panel: Panel
    freeze: FreezeIdentity
    figi: Mapping[date, Mapping[str, str | None]]
    snapshot_dates: tuple[date, ...]
    checks: dict[str, Any]
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def tickers(self) -> tuple[str, ...]:
        return self.panel.tickers

    def _factor(self) -> np.ndarray:
        if "factor" not in self._cache:
            self._cache["factor"] = self.panel.split_arrays()[0]
        return self._cache["factor"]

    def split_counts(self) -> np.ndarray:
        """Cumulative count of splits executed on or before each session, ``(T, N)``."""
        if "counts" not in self._cache:
            self._cache["counts"] = self.panel.split_arrays()[1]
        return self._cache["counts"]

    def panel_view(self, as_of_idx: int) -> AsOfView:
        if as_of_idx < 0 or as_of_idx >= len(self.grid):
            raise HardFail("R4", f"as-of index {as_of_idx} is off the grid of {len(self.grid)}")
        stop = as_of_idx + 1
        return AsOfView(as_of_idx, self.panel.open[:stop], self.panel.high[:stop],
                        self.panel.low[:stop], self.panel.close[:stop], self.panel.volume[:stop],
                        self._factor()[:stop], self.split_counts()[:stop])

    def membership(self, as_of_idx: int) -> np.ndarray:
        """``(N,)`` bool: in the latest CS snapshot dated on or before ``session(as_of_idx)``."""
        if as_of_idx < 0 or as_of_idx >= len(self.grid):
            raise HardFail("R4", f"as-of index {as_of_idx} is off the grid of {len(self.grid)}")
        return self.panel.membership()[as_of_idx]

    def snapshot_as_of(self, session_idx: int) -> date | None:
        usable = [d for d in self.snapshot_dates if d <= self.grid.session(session_idx)]
        return usable[-1] if usable else None

    def split_factor(self, ticker: str, session_idx: int, as_of_idx: int) -> float:
        if session_idx > as_of_idx:
            raise PointInTimeViolation(
                f"split factor of {ticker} at {session_idx} requested as of {as_of_idx}")
        return float(self._factor()[session_idx, self.panel.column(ticker)])

    def stable_identity(self, ticker: str, session_idx: int) -> SymbolIdentity:
        as_of = self.snapshot_as_of(session_idx)
        figi = None if as_of is None else self.figi.get(as_of, {}).get(ticker)
        return SymbolIdentity(ticker, figi, as_of)


def _snapshot_dir(workspace_root: Path, snapshot_id: str) -> Path:
    return workspace_root / SNAPSHOT_DIR / snapshot_id


def _read_verified(path: Path, expected_sha256: str, *, where: str) -> dict[str, Any]:
    """Read one frozen gzip payload and verify it against the freeze row (G6 / R2)."""
    if not path.exists():
        raise HardFail("R2", f"{where}: {path} is missing")
    body = path.read_bytes()
    found = _sha256(body)
    if found != expected_sha256:
        raise HardFail("R2", f"{where}: sha256 {found} != freeze {expected_sha256} ({path})")
    return json.loads(gzip.decompress(body))


def _resolve(row: Mapping[str, Any], workspace_root: Path, c_raw_root: Path | None) -> tuple[Path, str]:
    """Common store first, C's local cache as the fallback; the bytes are the same either way."""
    common = workspace_root / str(row["common_path"])
    if common.exists():
        return common, "common"
    if c_raw_root is not None:
        local = c_raw_root / str(row["relative_path"])
        if local.exists():
            return local, "local"
    raise HardFail("R2", f"neither the common nor the local copy of {row['relative_path']} exists")


def _freeze_digest(files: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in sorted(files, key=lambda r: str(r["relative_path"])):
        digest.update(f"{row['relative_path']}\t{row['sha256']}\t{row['size']}\n".encode())
    return digest.hexdigest()


def grid_digest(dates: Iterable[date]) -> str:
    return hashlib.sha256("".join(d.isoformat() + "\n" for d in dates).encode("utf-8")).hexdigest()


def _check_grid(dates: Sequence[date], not_ok: Sequence[date], freeze: Mapping[str, Any],
                snapshot: Mapping[str, Any], calendar: MarketCalendar) -> dict[str, Any]:
    """G1~G8 of D1 Pre-flight Contract §2.3. Any failure is R3 and stops the load."""
    if not dates:
        raise HardFail("R3", "the freeze lists no usable GROUPED_DAILY session")
    for day in dates:
        if not calendar.is_trading_day(day):
            raise HardFail("R3", f"G1: {day.isoformat()} is not an XNYS session")
    for earlier, later in zip(dates, dates[1:]):
        if later <= earlier:
            raise HardFail("R3", f"G2: sessions out of order or duplicated at {later.isoformat()}")
        if calendar.next_trading_day(earlier) != later:
            raise HardFail("R3", f"G3: XNYS session missing between {earlier} and {later}")
    late = [d.isoformat() for d in not_ok if d >= dates[0]]
    if late:
        raise HardFail("R3", f"G4: unusable grouped sessions inside the grid: {late}")
    start, end = date.fromisoformat(snapshot["start_date"]), date.fromisoformat(snapshot["end_date"])
    if dates[0] < start or dates[-1] > end:
        raise HardFail("R3", f"G7: grid {dates[0]}..{dates[-1]} outside snapshot {start}..{end}")
    usable = [date.fromisoformat(d) for d in freeze["usable_range"]]
    if len(dates) != int(freeze["grouped_usable_sessions"]) or [dates[0], dates[-1]] != usable:
        raise HardFail("R3", f"G8: grid {len(dates)} {dates[0]}..{dates[-1]} disagrees with the freeze"
                             f" {freeze['grouped_usable_sessions']} {freeze['usable_range']}")
    return {"G1_all_xnys_sessions": True, "G2_strictly_ascending": True, "G3_no_internal_gap": True,
            "G4_unusable_only_before_grid": True, "G5_session_labels_agree": "checked while loading",
            "G6_file_sha256": "checked while loading", "G7_inside_snapshot_range": True,
            "G8_matches_freeze_counters": True}


def _load_snapshots(rows: Sequence[Mapping[str, Any]], workspace_root: Path, c_raw_root: Path | None,
                    allowed_exchanges: frozenset[str], read_log: list[tuple[str, str]],
                    ) -> tuple[dict[date, frozenset[str]], dict[date, dict[str, str | None]], dict[str, int]]:
    """CS reference snapshots: the membership set and the composite FIGI of each member."""
    members: dict[date, frozenset[str]] = {}
    figi: dict[date, dict[str, str | None]] = {}
    stats = {"rows": 0, "members": 0, "figi_null": 0}
    for row in rows:
        path, _ = _resolve(row, workspace_root, c_raw_root)
        payload = _read_verified(path, str(row["sha256"]), where=f"tickers {row['as_of']}")
        as_of = date.fromisoformat(str(payload["as_of"]))
        if as_of != date.fromisoformat(str(row["as_of"])):
            raise HardFail("R3", f"G5: ticker snapshot {path.name} says as_of {payload['as_of']}")
        keep: dict[str, str | None] = {}
        for item in payload["results"]:
            stats["rows"] += 1
            # The declaration asks for type=CS, active=true rows on the allowed exchanges. The
            # active flag is a no-op on the V1 dataset (every row carries it) and is checked
            # anyway, so a later snapshot that lists a dead name cannot slip into the universe.
            if (item.get("type") != "CS" or item.get("market") != "stocks"
                    or item.get("active") is not True
                    or item.get("primary_exchange") not in allowed_exchanges):
                continue
            ticker = str(item["ticker"])
            code = item.get("composite_figi")
            keep[ticker] = str(code) if code else None
        members[as_of] = frozenset(keep)
        figi[as_of] = keep
        stats["members"] += len(keep)
        stats["figi_null"] += sum(1 for v in keep.values() if v is None)
        read_log.append((str(row["relative_path"]), str(row["sha256"])))
    return members, figi, stats


def _load_splits(rows: Sequence[Mapping[str, Any]], workspace_root: Path, c_raw_root: Path | None,
                 read_log: list[tuple[str, str]]) -> tuple[SplitEvent, ...]:
    events: list[SplitEvent] = []
    seen: set[tuple[str, date]] = set()
    for row in rows:
        path, _ = _resolve(row, workspace_root, c_raw_root)
        payload = _read_verified(path, str(row["sha256"]), where=f"splits {row['as_of']}")
        for item in payload["results"]:
            try:
                event = SplitEvent(str(item["ticker"]), date.fromisoformat(item["execution_date"]),
                                   float(item["split_from"]), float(item["split_to"]))
            except (KeyError, TypeError, ValueError):
                continue
            if event.split_from <= 0 or event.split_to <= 0 or event.split_from == event.split_to:
                continue
            key = (event.ticker, event.execution_date)
            if key in seen:  # one ticker cannot split twice on one date; keep the first record
                continue
            seen.add(key)
            events.append(event)
        read_log.append((str(row["relative_path"]), str(row["sha256"])))
    return tuple(sorted(events, key=lambda e: (e.execution_date, e.ticker)))


def _load_grouped(rows: Sequence[Mapping[str, Any]], dates: Sequence[date], tickers: Sequence[str],
                  workspace_root: Path, c_raw_root: Path | None, read_log: list[tuple[str, str]],
                  ) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Grouped daily rows into ``(T, N)`` arrays. A repeated ticker in one session is F3, not a merge."""
    column = {ticker: i for i, ticker in enumerate(tickers)}
    arrays = {name: np.full((len(dates), len(tickers)), np.nan) for name in ("o", "h", "l", "c", "v")}
    by_date = {date.fromisoformat(str(r["session_date"])): r for r in rows}
    stats = {"rows_total": 0, "rows_kept": 0, "duplicate_ticker_rows": 0,
             "duplicate_examples": [], "read_locations": {"common": 0, "local": 0}}
    for i, session in enumerate(dates):
        row = by_date[session]
        path, where = _resolve(row, workspace_root, c_raw_root)
        stats["read_locations"][where] += 1
        payload = _read_verified(path, str(row["sha256"]), where=f"grouped {session.isoformat()}")
        if str(payload.get("session")) != session.isoformat() or path.stem.split(".")[0] != session.isoformat():
            raise HardFail("R3", f"G5: {path.name} carries session {payload.get('session')!r}")
        seen: set[str] = set()
        for item in (payload.get("body") or {}).get("results") or ():
            stats["rows_total"] += 1
            ticker = item.get("T")
            if ticker in seen:
                stats["duplicate_ticker_rows"] += 1
                if len(stats["duplicate_examples"]) < 20:
                    stats["duplicate_examples"].append([session.isoformat(), str(ticker)])
                continue
            if isinstance(ticker, str):
                seen.add(ticker)
            j = column.get(ticker)
            if j is None:
                continue
            values = [item.get(k) for k in ("o", "h", "l", "c", "v")]
            if any(v is None for v in values):
                continue
            for name, value in zip(("o", "h", "l", "c", "v"), values):
                arrays[name][i, j] = float(value)
            stats["rows_kept"] += 1
        read_log.append((str(row["relative_path"]), str(row["sha256"])))
    return arrays, stats


def load_daily_history(workspace_root: Path, snapshot_id: str, *, allowed_exchanges: frozenset[str],
                       expected: FreezeIdentity | None = None, c_raw_root: Path | None = None,
                       calendar: MarketCalendar | None = None,
                       duplicate_rows_allowed: bool = False) -> DailyHistory:
    """Load one frozen dataset. ``snapshot_id`` is explicit; the CURRENT pointer is never followed."""
    snapshot_path = _snapshot_dir(workspace_root, snapshot_id) / SNAPSHOT_FILE
    if not snapshot_path.exists():
        raise HardFail("R2", f"snapshot {snapshot_id} not found at {snapshot_path}")
    snapshot_bytes = snapshot_path.read_bytes()
    snapshot = json.loads(snapshot_bytes)
    if snapshot.get("status") != "FROZEN":
        raise HardFail("R2", f"snapshot {snapshot_id} status is {snapshot.get('status')!r}, not FROZEN")
    if snapshot.get("snapshot_id") != snapshot_id:
        raise HardFail("R2", f"snapshot file declares id {snapshot.get('snapshot_id')!r}")

    freeze_path = _snapshot_dir(workspace_root, snapshot_id) / FREEZE_FILE
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if _freeze_digest(freeze["files"]) != freeze["freeze_digest"]:
        raise HardFail("R2", "c_raw_freeze.json rows do not hash to its own freeze_digest")
    if snapshot.get("c_raw_freeze", {}).get("freeze_digest") != freeze["freeze_digest"]:
        raise HardFail("R2", "the snapshot and its freeze document disagree on freeze_digest")

    grouped_rows = [r for r in freeze["files"] if r["file_type"] == GROUPED]
    for row in grouped_rows:
        if row["status"] == OK and row.get("authority") != DAILY_AUTHORITY:
            raise HardFail("R2", f"{row['relative_path']} authority {row.get('authority')!r}")
    usable = sorted(date.fromisoformat(str(r["session_date"])) for r in grouped_rows if r["status"] == OK)
    unusable = sorted(date.fromisoformat(str(r["session_date"])) for r in grouped_rows if r["status"] != OK)
    calendar = calendar or MarketCalendar()
    checks = _check_grid(usable, unusable, freeze, snapshot, calendar)
    grid = SessionGrid(tuple(usable), grid_digest(usable))

    read_log: list[tuple[str, str]] = []
    members, figi, member_stats = _load_snapshots(
        [r for r in freeze["files"] if r["file_type"] == TICKERS], workspace_root, c_raw_root,
        allowed_exchanges, read_log)
    splits = _load_splits([r for r in freeze["files"] if r["file_type"] == SPLITS],
                          workspace_root, c_raw_root, read_log)
    tickers = tuple(sorted(set().union(*members.values()))) if members else ()
    arrays, grouped_stats = _load_grouped([r for r in grouped_rows if r["status"] == OK], usable,
                                          tickers, workspace_root, c_raw_root, read_log)
    if grouped_stats["duplicate_ticker_rows"] and not duplicate_rows_allowed:
        raise HardFail("F3", f"{grouped_stats['duplicate_ticker_rows']} repeated ticker rows in grouped"
                             f" sessions, e.g. {grouped_stats['duplicate_examples'][:5]}")

    panel = Panel(tuple(usable), tickers, arrays["o"], arrays["h"], arrays["l"], arrays["c"],
                  arrays["v"], splits, members)
    identity = FreezeIdentity(
        snapshot_id=snapshot_id, snapshot_sha256=_sha256(snapshot_bytes),
        freeze_id=str(freeze["freeze_id"]), freeze_digest=str(freeze["freeze_digest"]),
        source_digest=str(freeze["c_raw_digest"]),
        d_read_digest=hashlib.sha256(
            "".join(f"{p}\t{s}\n" for p, s in sorted(read_log)).encode("utf-8")).hexdigest(),
        daily_authority=DAILY_AUTHORITY, first_session=usable[0].isoformat(),
        last_session=usable[-1].isoformat(), session_count=len(usable), grid_digest=grid.digest)
    if expected is not None:
        identity.assert_matches(expected)
    checks.update(snapshot_sessions=int(snapshot.get("sessions", 0)),
                  snapshot_range=[snapshot["start_date"], snapshot["end_date"]],
                  reference_snapshots=len(members), split_records=len(splits),
                  member_stats=member_stats, grouped=grouped_stats)
    return DailyHistory(grid, panel, identity, figi, tuple(sorted(members)), checks)


def read_set_digest(workspace_root: Path, snapshot_id: str, *,
                    c_raw_root: Path | None = None) -> str:
    """Re-hash every file a D run reads and fold the result into the same digest the load built.

    The load-time ``d_read_digest`` says "these bytes are what the freeze recorded". Running this
    again when the run is over says "and they still are". The two together are the D2 input
    immutability check (F1): another writer may add files to the workspace - the minute collector
    does, continuously - but if anything under D's own read set moved, the artifacts are void.
    """
    freeze_path = _snapshot_dir(workspace_root, snapshot_id) / FREEZE_FILE
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    lines: list[tuple[str, str]] = []
    for row in freeze["files"]:
        if row["file_type"] == GROUPED and row["status"] != OK:
            continue
        path, _ = _resolve(row, workspace_root, c_raw_root)
        lines.append((str(row["relative_path"]), _sha256(path.read_bytes())))
    return hashlib.sha256(
        "".join(f"{p}\t{s}\n" for p, s in sorted(lines)).encode("utf-8")).hexdigest()
