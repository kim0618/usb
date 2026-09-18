"""Strategy B's minute dataset: ``sparse_minute_bars``, HYBRID-S, raw prices, every provider field.

Why a separate data kind
------------------------
Strategy A's ``minute_bars`` entry means "every regular minute of every session exists"
(STRICT): the collector refuses a range with one missing regular minute, and ``ReplayDataset``
reads that promise. Small caps never keep it (0 of 251 sessions in the feasibility audit), and
B does not need it. A ``sparse_minute_bars`` entry promises something else:

* rows are exactly the bars the provider returned for 04:00-20:00 ET on XNYS sessions inside
  the entry range - a silent minute is absent, never filled, and a session may be empty;
* prices are raw (``adjusted=false``); splits are applied point-in-time by the research layer;
* ``vwap`` (Massive ``vw``) and ``transactions`` (Massive ``n``) are kept per bar, and volume
  keeps its fractional shares (A's replay quantizes to integers; B does not);
* completeness is judged after the fact (``session_audit``), never at load time.

A distinct ``data_kind`` keeps the two promises from ever being confused: A's loader filters on
``minute_bars`` and cannot pick a B entry (so B entries never make an A symbol ambiguous), and
this loader accepts only ``sparse_minute_bars``.

Sources
-------
An entry is materialized from rows that came either from a verified A ``minute_bars`` entry
(the same Massive rows; A's STRICT set is a valid sparse set) or from a direct read-only range
request. The Parquet footer names the source, so the provenance is covered by the file checksum.
The manifest schema is unchanged: ``data_kind``, ``collector_version`` and ``schema_version`` are
existing columns, and ``regular_complete_sessions`` stays NULL because this kind makes no claim.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3

import pyarrow as pa
import pyarrow.parquet as pq

from app.backtest.collector.collector import COLLECTOR_VERSION as A_COLLECTOR_VERSION
from app.backtest.collector.dataset import DATA_KIND as A_DATA_KIND, TIMEFRAME as A_TIMEFRAME
from app.backtest.replay.dataset import _check_footer as check_a_footer
from app.backtest.replay.dataset import _complete_entry as a_complete_entry
from app.backtest.replay.dataset import _verify_entry as verify_a_entry
from app.backtest.workspace.errors import WorkspaceError
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.lock import writer_lock
from app.backtest.workspace.manifest import (
    MANIFEST_TABLE, EntryFile, EntryMetadata, ManifestKey, complete_entry, entry_files,
    files_match, manifest_connection, mark_collecting, mark_failed, plan_entry,
)
from app.backtest.workspace.safe_write import safe_write
from app.integrations.massive.minute_bars import ET, SessionPart, classify
from app.market.calendar import MarketCalendar
from app.market.symbols import normalize_symbol
from app.strategy_b.models import MomentumBar, Session
from app.strategy_b.session import SessionBoundaries

PROVIDER = "massive"
DATA_KIND = "sparse_minute_bars"
TIMEFRAME = "1minute"
DATASET_SCHEMA_VERSION = 1
DATASET_VERSION = "strategy-b-sparse-minute-v1"
COMPLETENESS_POLICY = "HYBRID_S"
MINUTE_ROOT = "market_data/normalized/minute_sparse/massive"
LOCK_PURPOSE = "strategy_b_sparse_minute_materialize"

FOOTER = {
    b"dataset_schema_version": str(DATASET_SCHEMA_VERSION).encode(),
    b"data_kind": DATA_KIND.encode(),
    b"timestamp_authority": b"bar_start",
    b"bar_interval": b"PT1M",
    b"adjusted": b"false",
    b"completeness_policy": COMPLETENESS_POLICY.encode(),
    b"vwap_authority": b"provider vw per bar; observation input, parity with a realtime "
                       b"trade-accumulated VWAP unverified",
    b"transactions_semantics": b"provider n per bar; parity with Kiwoom FE event count unverified",
    b"volume_semantics": b"provider value as sent, fractional shares preserved",
}
COLUMNS = (
    pa.field("provider", pa.string(), nullable=False),
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("timestamp_utc", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("timestamp_et", pa.timestamp("us", tz="America/New_York"), nullable=False),
    pa.field("trading_date", pa.date32(), nullable=False),
    pa.field("session", pa.string(), nullable=False),
    pa.field("open", pa.float64(), nullable=False),
    pa.field("high", pa.float64(), nullable=False),
    pa.field("low", pa.float64(), nullable=False),
    pa.field("close", pa.float64(), nullable=False),
    pa.field("volume", pa.float64(), nullable=False),
    pa.field("transactions", pa.float64()),
    pa.field("vwap", pa.float64()),
)
_SESSION = {SessionPart.PREMARKET.value: Session.PREMARKET, SessionPart.REGULAR.value: Session.REGULAR,
            SessionPart.POSTMARKET.value: Session.AFTER}


class SparseDatasetError(WorkspaceError):
    code = "SPARSE_DATASET_ERROR"


class SparseRowsInvalid(SparseDatasetError):
    code = "SPARSE_ROWS_INVALID"


class SparseDatasetNotFound(SparseDatasetError):
    code = "SPARSE_DATASET_NOT_FOUND"


class SparseDatasetAmbiguous(SparseDatasetError):
    code = "SPARSE_DATASET_AMBIGUOUS"


class SparseDatasetCorrupt(SparseDatasetError):
    code = "SPARSE_DATASET_CORRUPT"


@dataclass(frozen=True, slots=True)
class SourceRow:
    """One provider minute aggregate as sent. ``timestamp`` is the bar start, tz-aware."""

    timestamp: datetime
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    vwap: float | None
    transactions: float | None


@dataclass(frozen=True)
class SparseDatasetIdentity:
    symbol: str
    entry_id: int
    data_kind: str
    dataset_version: str
    dataset_schema_version: int
    combined_checksum: str
    start_date: date
    end_date: date
    row_count: int
    source: str
    files: tuple[EntryFile, ...]

    def lines(self) -> tuple[str, ...]:
        return (f"dataset={self.symbol}:{self.data_kind}:{self.dataset_version}:"
                f"schema{self.dataset_schema_version}:{self.start_date}..{self.end_date}:"
                f"rows{self.row_count}:{self.combined_checksum}",)

    def as_dict(self) -> dict[str, object]:
        return {"symbol": self.symbol, "manifest_entry_id": self.entry_id,
                "data_kind": self.data_kind, "dataset_version": self.dataset_version,
                "dataset_schema_version": self.dataset_schema_version,
                "combined_checksum": self.combined_checksum,
                "start_date": self.start_date.isoformat(), "end_date": self.end_date.isoformat(),
                "row_count": self.row_count, "source": self.source,
                "files": [{"path": item.relative_path, "sha256": item.checksum,
                           "rows": item.row_count, "bytes": item.byte_size} for item in self.files]}


@dataclass(frozen=True)
class MaterializeReport:
    identity: SparseDatasetIdentity
    expected_sessions: int
    sessions_with_rows: int
    premarket_sessions: int
    dropped_rows: int


def boundaries_for(calendar: MarketCalendar, day: date) -> SessionBoundaries:
    """B's session boundaries from the exchange calendar, early closes included."""
    window = calendar.session(day)
    if window is None:
        raise SparseRowsInvalid(f"{day} is not an XNYS session")
    base = SessionBoundaries.standard(day)
    return SessionBoundaries(day, base.premarket_start, window.market_open.astimezone(ET),
                             window.market_close.astimezone(ET), base.after_close)


def _sessions_between(calendar: MarketCalendar, start: date, end: date) -> tuple[date, ...]:
    days, day = [], start if calendar.is_trading_day(start) else calendar.next_trading_day(start)
    while day <= end:
        days.append(day)
        day = calendar.next_trading_day(day)
    return tuple(days)


def _validated_table(symbol: str, rows: Sequence[SourceRow], *, start: date, end: date,
                     calendar: MarketCalendar) -> tuple[pa.Table, int, set[date], set[date]]:
    """Structural checks only. No completeness rule: a silent minute is not an error."""
    columns: dict[str, list[object]] = {field.name: [] for field in COLUMNS}
    previous: datetime | None = None
    days: set[date] = set()
    premarket: set[date] = set()
    dropped = 0
    windows: dict[date, object] = {}
    for row in rows:
        if row.timestamp.tzinfo is None:
            raise SparseRowsInvalid(f"{symbol} row timestamp is not timezone-aware")
        if row.timestamp.second or row.timestamp.microsecond:
            raise SparseRowsInvalid(f"{symbol} {row.timestamp.isoformat()} is not a minute start")
        if previous is not None and row.timestamp <= previous:
            raise SparseRowsInvalid(f"{symbol} rows are not strictly increasing at {row.timestamp.isoformat()}")
        previous = row.timestamp
        local = row.timestamp.astimezone(ET)
        day = local.date()
        if not start <= day <= end:
            dropped += 1  # a range request can return an edge row outside the entry's dates
            continue
        if day not in windows:
            windows[day] = calendar.session(day)
        window = windows[day]
        if window is None:
            raise SparseRowsInvalid(f"{symbol} has a row on {day}, which is not an XNYS session")
        part = classify(local, window)  # type: ignore[arg-type]
        if part is SessionPart.OUTSIDE:
            raise SparseRowsInvalid(f"{symbol} row {local.isoformat()} is outside 04:00-20:00 ET")
        values = (row.open, row.high, row.low, row.close, row.volume)
        if any(value is None for value in values):
            raise SparseRowsInvalid(f"{symbol} row {local.isoformat()} has null OHLCV")
        try:
            MomentumBar(local, row.open, row.high, row.low, row.close, row.volume,  # type: ignore[arg-type]
                        _SESSION[part.value], vwap=row.vwap, transactions=row.transactions)
        except ValueError as error:
            raise SparseRowsInvalid(f"{symbol} row {local.isoformat()} is not a valid bar: {error}") from None
        days.add(day)
        if part is SessionPart.PREMARKET:
            premarket.add(day)
        for name, value in (("provider", PROVIDER), ("symbol", symbol),
                            ("timestamp_utc", row.timestamp.astimezone(timezone.utc)),
                            ("timestamp_et", local), ("trading_date", day), ("session", part.value),
                            ("open", row.open), ("high", row.high), ("low", row.low),
                            ("close", row.close), ("volume", row.volume),
                            ("transactions", row.transactions), ("vwap", row.vwap)):
            columns[name].append(value)
    return pa.table({field.name: pa.array(columns[field.name], type=field.type) for field in COLUMNS},
                    schema=pa.schema(list(COLUMNS))), dropped, days, premarket


def materialize(workspace: Workspace, symbol: str, rows: Sequence[SourceRow], *, start: date,
                end: date, source: str, calendar: MarketCalendar,
                source_request_count: int | None = None) -> MaterializeReport:
    """Write one ``sparse_minute_bars`` entry for ``[start, end]`` under the writer lock."""
    symbol = normalize_symbol(symbol)
    if "\n" in source or not source.strip():
        raise SparseRowsInvalid("source must be one non-empty line")
    table, dropped, days, premarket = _validated_table(symbol, rows, start=start, end=end,
                                                       calendar=calendar)
    expected = _sessions_between(calendar, start, end)
    table = table.replace_schema_metadata({**FOOTER, b"source": source.encode()})
    relative = f"{MINUTE_ROOT}/{symbol}/{start.isoformat()}_{end.isoformat()}.parquet"
    key = ManifestKey(PROVIDER, symbol, DATA_KIND, TIMEFRAME, start.isoformat(), end.isoformat())
    with writer_lock(workspace, purpose=f"{LOCK_PURPOSE}:{symbol}"):
        with manifest_connection(workspace, create=True) as connection:
            entry_id = plan_entry(connection, key, collector_version=DATASET_VERSION)
            overlapping = [row for row in _complete_rows(connection, symbol)
                           if str(row["start_date"]) <= end.isoformat()
                           and start.isoformat() <= str(row["end_date"])]
            if overlapping:
                # Checked before any byte is written: two COMPLETE entries over the same dates
                # would make every later load of that range a refusal.
                listed = ", ".join(f"{row['id']}:{row['start_date']}..{row['end_date']}" for row in overlapping)
                raise SparseDatasetAmbiguous(
                    f"{symbol} {start}..{end} overlaps COMPLETE {DATA_KIND} entries ({listed}); "
                    "an entry is never rewritten or duplicated")
            mark_collecting(connection, entry_id, collector_version=DATASET_VERSION)
            try:
                with safe_write(workspace.root / relative) as handle:
                    pq.write_table(table, handle.partial_path, compression="zstd",
                                   write_statistics=False)
                files = (EntryFile(relative, str(handle.checksum), table.num_rows, int(handle.size)),)
                complete_entry(connection, entry_id, workspace=workspace, files=files,
                               relative_path=relative, metadata=EntryMetadata(
                                   expected_sessions=len(expected), actual_sessions=len(days),
                                   regular_complete_sessions=None,
                                   premarket_sessions=len(premarket),
                                   source_request_count=source_request_count,
                                   schema_version=DATASET_SCHEMA_VERSION))
            except BaseException:
                mark_failed(connection, entry_id)
                raise
    dataset = SparseMinuteDataset.load_entry(workspace, entry_id, calendar=calendar)
    return MaterializeReport(dataset.identity, len(expected), len(days), len(premarket), dropped)


def rows_from_a_entry(workspace: Workspace, symbol: str) -> tuple[tuple[SourceRow, ...], str, date, date]:
    """Every row of the one verified A ``minute_bars`` entry, including ``vw`` and ``n``.

    Uses A's own manifest verification (COMPLETE, collector version, schema, checksums, footer)
    read-only; A's ``ReplayDataset`` is not used because it deliberately drops vwap/transactions.
    """
    symbol = normalize_symbol(symbol)
    with manifest_connection(workspace, create=False) as connection:
        row = a_complete_entry(connection, PROVIDER, symbol)
        identity = verify_a_entry(connection, row, workspace)
    rows: list[SourceRow] = []
    names = ("timestamp_utc", "open", "high", "low", "close", "volume", "vwap", "transactions",
             "symbol")
    for item in identity.files:
        path = workspace.root / item.relative_path
        check_a_footer(path)
        table = pq.read_table(path, columns=list(names))
        data = {name: table.column(name).to_pylist() for name in names}
        for index in range(table.num_rows):
            if data["symbol"][index] != symbol:
                raise SparseRowsInvalid(f"{item.relative_path} row {index} is not {symbol}")
            rows.append(SourceRow(data["timestamp_utc"][index], data["open"][index],
                                  data["high"][index], data["low"][index], data["close"][index],
                                  data["volume"][index], data["vwap"][index],
                                  data["transactions"][index]))
    rows.sort(key=lambda item: item.timestamp)
    source = (f"workspace:{A_DATA_KIND}/{A_TIMEFRAME}:entry={identity.entry_id}:"
              f"collector={A_COLLECTOR_VERSION}:checksum={identity.combined_checksum}")
    return tuple(rows), source, identity.start_date, identity.end_date


class SparseMinuteDataset:
    """A verified, read-only view of one symbol's ``sparse_minute_bars`` entry.

    Like A's ``ReplayDataset`` it holds the whole range and knows nothing about ``as_of``:
    ``SessionTape`` cuts every read at the replay moment.
    """

    def __init__(self, identity: SparseDatasetIdentity, rows: dict[date, list[tuple]],
                 calendar: MarketCalendar) -> None:
        self.identity = identity
        self.calendar = calendar
        self._rows = rows
        self._bars: dict[date, tuple[MomentumBar, ...]] = {}

    @classmethod
    def load(cls, workspace: Workspace, symbol: str, *, calendar: MarketCalendar,
             start: date, end: date) -> "SparseMinuteDataset":
        """The one COMPLETE entry that covers ``[start, end]``; zero or several is a refusal."""
        symbol = normalize_symbol(symbol)
        with manifest_connection(workspace, create=False) as connection:
            row = _covering_entry(connection, symbol, start, end)
            identity = _verify(connection, row, workspace)
        return cls._read(workspace, identity, calendar)

    @classmethod
    def load_entry(cls, workspace: Workspace, entry_id: int, *,
                   calendar: MarketCalendar) -> "SparseMinuteDataset":
        with manifest_connection(workspace, create=False) as connection:
            row = connection.execute(f"SELECT * FROM {MANIFEST_TABLE} WHERE id = ?", (entry_id,)).fetchone()
            if row is None or str(row["data_kind"]) != DATA_KIND or str(row["status"]) != "COMPLETE":
                raise SparseDatasetNotFound(f"entry {entry_id} is not a COMPLETE {DATA_KIND} entry")
            identity = _verify(connection, row, workspace)
        return cls._read(workspace, identity, calendar)

    @classmethod
    def _read(cls, workspace: Workspace, identity: SparseDatasetIdentity,
              calendar: MarketCalendar) -> "SparseMinuteDataset":
        symbol = identity.symbol
        rows: dict[date, list[tuple]] = {}
        for item in identity.files:
            path = workspace.root / item.relative_path
            file = pq.ParquetFile(path)
            _check_footer(file.schema_arrow, path)
            table = file.read()
            if table.num_rows != item.row_count:
                raise SparseDatasetCorrupt(f"{item.relative_path} holds {table.num_rows} rows, "
                                           f"manifest says {item.row_count}")
            data = {field.name: table.column(field.name).to_pylist() for field in COLUMNS}
            for index in range(table.num_rows):
                if data["symbol"][index] != symbol or data["provider"][index] != PROVIDER:
                    raise SparseDatasetCorrupt(f"{item.relative_path} row {index} is not {PROVIDER}/{symbol}")
                rows.setdefault(data["trading_date"][index], []).append(tuple(
                    data[name][index] for name in ("timestamp_et", "session", "open", "high", "low",
                                                   "close", "volume", "vwap", "transactions")))
        return cls(identity, rows, calendar)

    @property
    def symbol(self) -> str:
        return self.identity.symbol

    def covers(self, day: date) -> bool:
        return self.identity.start_date <= day <= self.identity.end_date

    def bars(self, day: date) -> tuple[MomentumBar, ...]:
        """Actual bars of one covered session, oldest first. An empty session is ``()``."""
        if not self.covers(day):
            raise SparseDatasetNotFound(f"{self.symbol} dataset does not cover {day}")
        cached = self._bars.get(day)
        if cached is None:
            boundaries = boundaries_for(self.calendar, day)
            built = []
            for stamp, session, open_, high, low, close, volume, vwap, transactions in sorted(
                    self._rows.get(day, ()), key=lambda item: item[0]):
                local = stamp.astimezone(ET)
                label = _SESSION.get(session)
                if label is None or label is not boundaries.classify(local):
                    raise SparseDatasetCorrupt(
                        f"{self.symbol} {local.isoformat()} is stored as {session}, calendar says "
                        f"{boundaries.classify(local)}")
                built.append(MomentumBar(local, open_, high, low, close, volume, label,
                                         vwap=vwap, transactions=transactions))
            cached = self._bars[day] = tuple(built)
        return cached


def _complete_rows(connection: sqlite3.Connection, symbol: str) -> list[sqlite3.Row]:
    return connection.execute(
        f"SELECT * FROM {MANIFEST_TABLE} WHERE provider = ? AND symbol = ? AND data_kind = ?"
        " AND timeframe = ? AND status = 'COMPLETE' ORDER BY id", (PROVIDER, symbol, DATA_KIND,
                                                                  TIMEFRAME)).fetchall()


def _covering_entry(connection: sqlite3.Connection, symbol: str, start: date, end: date) -> sqlite3.Row:
    rows = _complete_rows(connection, symbol)
    covering = [row for row in rows if date.fromisoformat(str(row["start_date"])) <= start
                and end <= date.fromisoformat(str(row["end_date"]))]
    if not covering:
        raise SparseDatasetNotFound(f"no COMPLETE {DATA_KIND} entry covers {symbol} {start}..{end}")
    if len(covering) > 1:
        raise SparseDatasetAmbiguous(
            f"{len(covering)} COMPLETE {DATA_KIND} entries cover {symbol} {start}..{end}; "
            "a replay does not choose between datasets")
    return covering[0]


def _verify(connection: sqlite3.Connection, row: sqlite3.Row, workspace: Workspace) -> SparseDatasetIdentity:
    entry_id = int(row["id"])
    if str(row["collector_version"]) != DATASET_VERSION:
        raise SparseDatasetCorrupt(f"entry {entry_id} was written by {row['collector_version']}")
    if row["schema_version"] is None or int(row["schema_version"]) != DATASET_SCHEMA_VERSION:
        raise SparseDatasetCorrupt(f"entry {entry_id} records schema {row['schema_version']}")
    matched, detail = files_match(connection, entry_id, workspace=workspace)
    if not matched:
        raise SparseDatasetCorrupt(f"entry {entry_id}: {detail}")
    files = entry_files(connection, entry_id)
    sources = {pq.ParquetFile(workspace.root / item.relative_path).schema_arrow.metadata.get(b"source", b"")
               for item in files}
    return SparseDatasetIdentity(
        symbol=str(row["symbol"]), entry_id=entry_id, data_kind=DATA_KIND,
        dataset_version=DATASET_VERSION, dataset_schema_version=DATASET_SCHEMA_VERSION,
        combined_checksum=str(row["checksum"]), start_date=date.fromisoformat(str(row["start_date"])),
        end_date=date.fromisoformat(str(row["end_date"])), row_count=int(row["row_count"]),
        source=";".join(sorted(item.decode() for item in sources)), files=files)


def _check_footer(schema: pa.Schema, path: Path) -> None:
    if [(field.name, field.type) for field in schema] != [(field.name, field.type) for field in COLUMNS]:
        raise SparseDatasetCorrupt(f"{path.name} does not carry the {DATA_KIND} columns")
    metadata = schema.metadata or {}
    for key, value in FOOTER.items():
        if metadata.get(key) != value:
            raise SparseDatasetCorrupt(f"{path.name} footer {key.decode()} is not {value.decode()!r}")
    if not metadata.get(b"source"):
        raise SparseDatasetCorrupt(f"{path.name} does not name its source")
