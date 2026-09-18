"""Strategy A's view of a frozen Common Historical Store snapshot (A STRICT, USB-HIST-V1).

A reads its minute and per-symbol daily tape from the snapshot's **member list**, never from
a directory listing and never from the legacy manifest. The snapshot is opened by explicit
id; ``snapshot.json``, the member list and the session audit are verified against the digests
the snapshot recorded, and every member file a symbol reads is re-hashed before a row of it is
used. Files written into the same folders after the freeze (a later collector) are invisible.

Minute authority is ``MASSIVE_TICKER_AGGREGATE`` from two storage forms of one snapshot:

* ``LEGACY_MINUTE_STRICT`` Parquet (the collector's own rows, read exactly as
  ``ReplayDataset.load`` reads them);
* ``MINUTE_RAW`` provider pages, turned into the same Arrow rows by the collector's own
  ``parse_bar`` and ``RowBuffer`` (session label from the same ``classify``).

The A STRICT view keeps a session only when the frozen session audit says its regular session
is complete, it has bars and no bar lies outside 04:00-20:00 ET. Nothing is filled or merged:
an excluded session has no rows at all, and the exclusion is part of the dataset identity. A
session present in both storage forms is refused, not chosen.

Daily authority is the same ticker aggregate (legacy daily Parquet + ``PER_SYMBOL_DAILY_RAW``
pages). ``MASSIVE_GROUPED_DAILY`` is never read here (USB-DAILY-AUTHORITY-V1).
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from app.backtest.baseline.coverage import MinuteCoverageStatus, SymbolMinuteCoverage
from app.backtest.collector.daily_dataset import DailyRow, to_row
from app.backtest.collector.daily_storage import read_daily_table, table_rows
from app.backtest.collector.dataset import DATASET_SCHEMA_VERSION, RowBuffer
from app.backtest.collector.range import sessions_between
from app.backtest.historical_store.c_freeze import sha256_file
from app.backtest.historical_store.raw_fetch import DAILY_DIR, MINUTE_DIR
from app.backtest.historical_store.snapshot import POINTER, SNAPSHOT_DIR
from app.backtest.replay.dataset import (
    ROW_COLUMNS, DatasetIdentity, RawBar, ReplayDataset, _check_footer,
)
from app.backtest.replay.errors import (
    DatasetAmbiguous, DatasetChecksumMismatch, DatasetIncomplete, DatasetNotFound,
    SymbolMismatch,
)
from app.backtest.research.contract import DAILY_DATA_VERSION, tape_end_at
from app.backtest.research.daily import DailyDatasetIdentity, SymbolDataset, daily_window, row_digest
from app.backtest.research.errors import DailyDataMissing
from app.backtest.research.workspace_store import WorkspaceDailyBarStore
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.manifest import MANIFEST_SCHEMA_VERSION, EntryFile
from app.integrations.massive.daily_bars import parse_daily_bar
from app.integrations.massive.minute_bars import SessionPart, classify, parse_bar
from app.market.calendar import MarketCalendar, TradingSessionWindow
from app.market.domain import DailyBar
from app.market.symbols import normalize_symbol

A_STRICT_VIEW = "A_STRICT_V1"
MINUTE_PROVIDER = "massive"
LEGACY_MINUTE_DIR = "market_data/normalized/minute/massive"
LEGACY_DAILY_DIR = "market_data/normalized/daily/massive"
EXCLUDE_EMPTY = "EMPTY_SESSION"
EXCLUDE_INCOMPLETE = "REGULAR_INCOMPLETE"
EXCLUDE_OUTSIDE = "OUTSIDE_EXTENDED_HOURS_ROWS"


class SnapshotInvalid(DatasetIncomplete):
    code = "SNAPSHOT_INVALID"


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


@dataclass(frozen=True)
class AuditRow:
    symbol: str
    session_date: date
    source: str
    regular_rows: int
    outside_rows: int
    expected_regular_rows: int
    regular_complete: bool
    empty_session: bool

    @property
    def strict_exclusion(self) -> str | None:
        if self.empty_session:
            return EXCLUDE_EMPTY
        if not self.regular_complete:
            return EXCLUDE_INCOMPLETE
        if self.outside_rows:
            return EXCLUDE_OUTSIDE
        return None


@dataclass
class CommonSnapshot:
    """A frozen snapshot, verified, with its members and session audit indexed."""

    root: Path
    snapshot_id: str
    snapshot: Mapping[str, Any]
    snapshot_sha256: str
    members: tuple[Mapping[str, Any], ...]
    audit: Mapping[str, Mapping[date, AuditRow]]
    _verified: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def open(cls, root: Path, snapshot_id: str) -> "CommonSnapshot":
        folder = root / SNAPSHOT_DIR / snapshot_id
        path = folder / "snapshot.json"
        if not path.is_file():
            raise DatasetNotFound(f"{snapshot_id} has no snapshot.json under {SNAPSHOT_DIR}")
        body = path.read_bytes()
        snapshot = json.loads(body)
        digest = hashlib.sha256(body).hexdigest()
        if snapshot.get("status") != "FROZEN" or snapshot.get("snapshot_id") != snapshot_id:
            raise SnapshotInvalid(f"{snapshot_id} is not a FROZEN snapshot of that id")
        pointer_path = root / POINTER
        if pointer_path.is_file():
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            if pointer.get("snapshot_id") == snapshot_id and pointer.get("snapshot_sha256") != digest:
                raise SnapshotInvalid(f"{snapshot_id} snapshot.json does not hash to the pointer's digest")
        for name, expected in snapshot["documents"].items():
            if sha256_file(folder / name) != expected:
                raise SnapshotInvalid(f"{snapshot_id}/{name} differs from the snapshot digest")
        if sha256_file(folder / "files.jsonl.gz") != snapshot["files_list_sha256"]:
            raise SnapshotInvalid(f"{snapshot_id} member list differs from the snapshot digest")
        audit_path = folder / "minute_session_audit.parquet"
        if sha256_file(audit_path) != snapshot["session_audit_sha256"]:
            raise SnapshotInvalid(f"{snapshot_id} session audit differs from the snapshot digest")
        with gzip.open(folder / "files.jsonl.gz", "rt", encoding="utf-8") as handle:
            members = tuple(json.loads(line) for line in handle)
        manifest = hashlib.sha256(b"".join(
            f"{m['path']}\t{m['sha256']}\t{m['size']}\n".encode() for m in members)).hexdigest()
        if manifest != snapshot["manifest_digest"]:
            raise SnapshotInvalid(f"{snapshot_id} member list does not reproduce manifest_digest")
        table = pq.read_table(audit_path)
        audit: dict[str, dict[date, AuditRow]] = defaultdict(dict)
        for row in table.to_pylist():
            audit[row["symbol"]][row["session_date"]] = AuditRow(
                row["symbol"], row["session_date"], row["source"], int(row["regular_rows"]),
                int(row["outside_rows"]), int(row["expected_regular_rows"]),
                bool(row["regular_complete"]), bool(row["empty_session"]))
        return cls(root, snapshot_id, snapshot, digest, members, dict(audit))

    @property
    def start_date(self) -> date:
        return date.fromisoformat(self.snapshot["start_date"])

    @property
    def end_date(self) -> date:
        return date.fromisoformat(self.snapshot["end_date"])

    def members_under(self, prefix: str, kinds: Sequence[str]) -> list[Mapping[str, Any]]:
        return [m for m in self.members if m["path"].startswith(prefix + "/") and m["kind"] in kinds]

    def verified_path(self, member: Mapping[str, Any]) -> Path:
        """The member's file, re-hashed once per snapshot object before it is read."""
        path = self.root / member["path"]
        if member["path"] not in self._verified:
            found = sha256_file(path) if path.is_file() else "MISSING"
            if found != member["sha256"]:
                raise DatasetChecksumMismatch(f"{member['path']} sha256 {found} != snapshot {member['sha256']}")
            self._verified[member["path"]] = found
        return path

    def identity_lines(self, view: str) -> tuple[str, ...]:
        return (f"historical_snapshot={self.snapshot_id}",
                f"historical_snapshot_sha256={self.snapshot_sha256}",
                f"historical_snapshot_manifest_digest={self.snapshot['manifest_digest']}",
                f"historical_view={view}")

    # --- A STRICT minute --------------------------------------------------------------------

    def strict_sessions(self, symbol: str, sessions: Sequence[date]) -> tuple[list[date], dict[date, str]]:
        rows = self.audit.get(symbol, {})
        kept, excluded = [], {}
        for day in sessions:
            row = rows.get(day)
            if row is None:
                excluded[day] = "NOT_IN_SNAPSHOT"
                continue
            reason = row.strict_exclusion
            if reason is None:
                kept.append(day)
            else:
                excluded[day] = reason
        return kept, excluded


def _page_rows(path: Path) -> list[Any]:
    body = json.loads(gzip.decompress(path.read_bytes()))
    return list(body.get("results") or ())


def _index_table(table: pa.Table, symbol: str, label: str,
                 rows: dict[date, list[RawBar]]) -> None:
    """``ReplayDataset.load``'s row loop, unchanged, for one Arrow table."""
    columns = {name: table.column(name).to_pylist() for name in ROW_COLUMNS}
    for index in range(table.num_rows):
        if columns["symbol"][index] != symbol:
            raise SymbolMismatch(f"{label} row {index} holds symbol {columns['symbol'][index]!r}, not {symbol}")
        day = columns["trading_date"][index]
        rows.setdefault(day, []).append(RawBar(
            columns["timestamp_utc"][index], columns["session"][index],
            columns["open"][index], columns["high"][index], columns["low"][index],
            columns["close"][index], columns["volume"][index]))


@dataclass(frozen=True)
class StrictMinuteView:
    dataset: ReplayDataset
    excluded: Mapping[date, str]


def load_a_strict_minute(snapshot: CommonSnapshot, symbol: str, *,
                         calendar: MarketCalendar) -> StrictMinuteView:
    """One symbol's A STRICT minute tape over the whole snapshot window."""
    symbol = normalize_symbol(symbol)
    grid = [window.session_date for window in sessions_between(calendar, snapshot.start_date, snapshot.end_date)]
    kept, excluded = snapshot.strict_sessions(symbol, grid)
    keep = set(kept)
    windows: dict[date, TradingSessionWindow] = {
        w.session_date: w for w in sessions_between(calendar, snapshot.start_date, snapshot.end_date)}
    legacy = snapshot.members_under(f"{LEGACY_MINUTE_DIR}/{symbol}", ("LEGACY_MINUTE_STRICT",))
    raw = snapshot.members_under(f"{MINUTE_DIR}/{symbol}", ("MINUTE_RAW",))
    if not legacy and not raw:
        raise DatasetNotFound(f"{snapshot.snapshot_id} holds no minute member for {symbol}")
    rows: dict[date, list[RawBar]] = {}
    sources: dict[date, str] = {}
    used: list[Mapping[str, Any]] = []
    for member in legacy:
        path = snapshot.verified_path(member)
        _check_footer(path)
        table = pq.read_table(path, columns=list(ROW_COLUMNS))
        days = pa.compute.is_in(table.column("trading_date"), value_set=pa.array(sorted(keep), pa.date32()))
        table = table.filter(days)
        before = set(rows)
        _index_table(table, symbol, member["path"], rows)
        for day in set(rows) - before:
            sources[day] = member["path"]
        used.append(member)
    for member in raw:
        path = snapshot.verified_path(member)
        buffer = RowBuffer(MINUTE_PROVIDER, symbol)
        page_days: set[date] = set()
        for item in _page_rows(path):
            bar = parse_bar(item)
            day = bar.bar_start_et.date()
            if day not in keep:
                continue
            if bar.has_null_ohlcv:
                raise DatasetIncomplete(f"{member['path']} {bar.bar_start.isoformat()} has null OHLCV")
            window = windows[day]
            if classify(bar.bar_start, window) is SessionPart.OUTSIDE:
                raise DatasetIncomplete(f"{member['path']} {bar.bar_start.isoformat()} is outside "
                                        "extended hours in a session the audit calls clean")
            buffer.add(bar, window)
            page_days.add(day)
        clash = sorted(day for day in page_days if day in sources)
        if clash:
            raise DatasetAmbiguous(f"{symbol} {clash[0]} is stored in both {sources[clash[0]]} and "
                                   f"{member['path']}; the view does not choose")
        if len(buffer):
            _index_table(buffer.to_table(), symbol, member["path"], rows)
        for day in page_days:
            sources[day] = member["path"]
        used.append(member)
    indexed = {day: tuple(sorted(items, key=lambda bar: bar.timestamp)) for day, items in rows.items()}
    audit = snapshot.audit.get(symbol, {})
    for day, bars in indexed.items():
        stamps = [bar.timestamp for bar in bars]
        if len(set(stamps)) != len(stamps):
            raise DatasetIncomplete(f"{symbol} {day} repeats a timestamp")
        regular = sum(1 for bar in bars if bar.session == SessionPart.REGULAR.value)
        if regular != audit[day].expected_regular_rows or regular != audit[day].regular_rows:
            raise DatasetIncomplete(f"{symbol} {day} has {regular} regular rows, the audit says "
                                    f"{audit[day].regular_rows} of {audit[day].expected_regular_rows}")
    missing = sorted(keep - set(indexed))
    if missing:
        raise DatasetIncomplete(f"{symbol} {missing[0]} is a clean audit session with no stored row")
    used.sort(key=lambda m: m["path"])
    checksum = hashlib.sha256(_canonical({
        "view": A_STRICT_VIEW, "snapshot_id": snapshot.snapshot_id,
        "snapshot_sha256": snapshot.snapshot_sha256, "symbol": symbol,
        "members": [[m["path"], m["sha256"]] for m in used],
        "excluded": {day.isoformat(): reason for day, reason in sorted(excluded.items())},
    })).hexdigest()
    files = tuple(EntryFile(m["path"], m["sha256"], int(m.get("rows") or 0), int(m["size"])) for m in used)
    identity = DatasetIdentity(
        workspace_root=snapshot.root, provider=MINUTE_PROVIDER, symbol=symbol, entry_id=0,
        collector_version=f"{snapshot.snapshot_id}/{A_STRICT_VIEW}",
        dataset_schema_version=DATASET_SCHEMA_VERSION, manifest_schema_version=MANIFEST_SCHEMA_VERSION,
        combined_checksum=checksum, start_date=snapshot.start_date, end_date=snapshot.end_date,
        files=files, verification_detail=f"{len(files)} snapshot members sha256 verified; "
                                         f"{len(excluded)} sessions excluded by {A_STRICT_VIEW}")
    return StrictMinuteView(ReplayDataset(identity, indexed), dict(sorted(excluded.items())))


# --- per-symbol daily (MASSIVE_TICKER_AGGREGATE) --------------------------------------------------


def load_ticker_daily(snapshot: CommonSnapshot, symbol: str) -> tuple[tuple[DailyRow, ...], tuple[Mapping[str, Any], ...]]:
    symbol = normalize_symbol(symbol)
    legacy = [m for m in snapshot.members if m["path"] == f"{LEGACY_DAILY_DIR}/{symbol}.parquet"
              and m["kind"] == "LEGACY_DAILY"]
    raw = snapshot.members_under(f"{DAILY_DIR}/{symbol}", ("PER_SYMBOL_DAILY_RAW",))
    rows: dict[date, DailyRow] = {}
    origin: dict[date, str] = {}
    for member in legacy:
        for row in table_rows(read_daily_table(snapshot.verified_path(member))):
            rows[row.session_date] = row
            origin[row.session_date] = member["path"]
    for member in raw:
        for item in _page_rows(snapshot.verified_path(member)):
            row = to_row(parse_daily_bar(item))
            if row.session_date in origin:
                raise DatasetAmbiguous(f"{symbol} daily {row.session_date} is stored in both "
                                       f"{origin[row.session_date]} and {member['path']}")
            if any(v is None for v in (row.open, row.high, row.low, row.close, row.volume)):
                raise DatasetIncomplete(f"{member['path']} {row.session_date} has null OHLCV")
            rows[row.session_date] = row
            origin[row.session_date] = member["path"]
    window = [day for day in sorted(rows) if snapshot.start_date <= day <= snapshot.end_date]
    return tuple(rows[day] for day in window), tuple(sorted(legacy + raw, key=lambda m: m["path"]))


SNAPSHOT_DAILY_DATA_VERSION_PREFIX = f"{DAILY_DATA_VERSION};store=historical_snapshot"


class SnapshotDailyBarStore(WorkspaceDailyBarStore):
    """``WorkspaceDailyBarStore`` over a snapshot's ticker-aggregate daily members."""

    def __init__(self, snapshot: CommonSnapshot, *, calendar: MarketCalendar | None = None) -> None:
        super().__init__(Workspace(snapshot.root), calendar=calendar)
        self._snapshot = snapshot
        self.members: dict[str, tuple[Mapping[str, Any], ...]] = {}
        self.data_version = f"{SNAPSHOT_DAILY_DATA_VERSION_PREFIX}/{snapshot.snapshot_id}"

    def relative_path(self, symbol: str) -> str:
        return f"{self._snapshot.snapshot_id}:daily:{normalize_symbol(symbol)}"

    def _load_symbol(self, symbol: str) -> tuple[DailyBar, ...]:
        cached = self._bars.get(symbol)
        if cached is not None:
            return cached
        rows, members = load_ticker_daily(self._snapshot, symbol)
        if not members:
            raise DailyDataMissing(f"{self._snapshot.snapshot_id} holds no daily member for {symbol}")
        self.members[symbol] = members
        bars = tuple(
            DailyBar(symbol=symbol, trading_date=row.session_date, open=row.open, high=row.high,
                     low=row.low, close=row.close, volume=int(round(row.volume)),
                     observed_at=tape_end_at(row.session_date),
                     available_at=tape_end_at(row.session_date))
            for row in rows)
        self._fractional[symbol] = frozenset(
            row.session_date for row in rows if float(row.volume) != float(int(round(row.volume))))
        self._bars[symbol] = bars
        return bars

    def load(self, symbols: Sequence[str], *, trading_date: date,
             required_sessions: int) -> DailyDatasetIdentity:
        window = daily_window(trading_date, required_sessions, self._calendar)
        datasets: list[SymbolDataset] = []
        for symbol in sorted({normalize_symbol(item) for item in symbols}):
            stored = self._load_symbol(symbol)
            bars = tuple(bar for bar in stored if window.start <= bar.trading_date <= trading_date)
            fractional = self._fractional.get(symbol, frozenset())
            datasets.append(SymbolDataset(
                symbol=symbol, bars=bars, checksum=row_digest(bars), row_count=len(bars),
                fractional_volume_rows=sum(1 for bar in bars if bar.trading_date in fractional),
                null_rows=0, cache_key=self.relative_path(symbol)))
        return DailyDatasetIdentity(daily_data_version=self.data_version, window=window,
                                    datasets=tuple(datasets))


# --- coverage (from the audit, before any row is read) -------------------------------------------


def strict_coverage(snapshot: CommonSnapshot, symbols: Sequence[str], *, calendar: MarketCalendar,
                    required_start: date, required_end: date) -> tuple[SymbolMinuteCoverage, ...]:
    expected = [w.session_date for w in sessions_between(calendar, required_start, required_end)]
    reports = []
    for symbol in symbols:
        kept, excluded = snapshot.strict_sessions(symbol, expected)
        missing = tuple(day for day in expected if day in excluded)
        reasons = sorted({excluded[day] for day in missing})
        base = dict(symbol=symbol, required_start=required_start, required_end=required_end,
                    expected_sessions=len(expected), available_sessions=len(kept),
                    missing_sessions=missing, manifest_status=f"SNAPSHOT:{snapshot.snapshot_id}",
                    dataset_start=snapshot.start_date, dataset_end=snapshot.end_date,
                    collector_version=f"{snapshot.snapshot_id}/{A_STRICT_VIEW}", complete_entries=1)
        if not snapshot.audit.get(symbol):
            reports.append(SymbolMinuteCoverage(status=MinuteCoverageStatus.NO_DATASET,
                                                detail="symbol is not in the snapshot audit", **base))
        elif missing:
            reports.append(SymbolMinuteCoverage(
                status=MinuteCoverageStatus.PARTIAL_RANGE,
                detail=f"{A_STRICT_VIEW} excludes {len(missing)} required sessions ({','.join(reasons)})", **base))
        else:
            reports.append(SymbolMinuteCoverage(status=MinuteCoverageStatus.COVERED,
                                                detail=f"{A_STRICT_VIEW} covers the run", **base))
    return tuple(reports)
