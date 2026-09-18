"""Dataset authority: what replay is allowed to read, and the proof that it may.

A Parquet file existing is not permission to replay it. Before a single bar is served
this module requires a manifest entry that is COMPLETE, was written by the collector
version and dataset schema this code understands, and whose every file still hashes to
the checksum the manifest recorded. The Parquet footers are then checked against the
collector's own Arrow schema and its ``dataset_schema_version`` metadata, so a file
swapped underneath a matching manifest row is caught by the file check and a file from
a different schema generation is caught by the footer.

The manifest is opened read-only (``create=False``): replay never migrates, never
writes, and never takes the workspace writer lock. Google Drive holds the only copy.

Rows are indexed by ET trading date. The dataset knows the whole range - that is what
makes an exact previous-session close possible - and the provider on top of it is what
clamps every answer to the replay clock.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import sqlite3

import pyarrow.parquet as pq

from app.backtest.collector.collector import COLLECTOR_VERSION, PROVIDER
from app.backtest.collector.dataset import DATA_KIND, DATASET_SCHEMA_VERSION, SCHEMA, TIMEFRAME
from app.backtest.replay.errors import (
    DatasetAmbiguous, DatasetChecksumMismatch, DatasetIncomplete, DatasetNotFound,
    DatasetSchemaMismatch, DateOutsideDataset, SessionDataMissing, SymbolMismatch,
)
from app.backtest.workspace.errors import WorkspaceError
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.manifest import (
    MANIFEST_SCHEMA_VERSION, MANIFEST_TABLE, EntryFile, entry_files, files_match,
    manifest_connection,
)
from app.market.symbols import normalize_symbol

#: The row columns replay reads. ``transactions`` and ``vwap`` are deliberately absent:
#: the collector recorded that the provider VWAP is not a price authority.
ROW_COLUMNS = ("timestamp_utc", "trading_date", "session", "open", "high", "low", "close",
               "volume", "symbol")


@dataclass(frozen=True)
class DatasetIdentity:
    """Everything a replay result needs to name the bytes it was produced from."""

    workspace_root: Path
    provider: str
    symbol: str
    entry_id: int
    collector_version: str
    dataset_schema_version: int
    manifest_schema_version: int
    combined_checksum: str
    start_date: date
    end_date: date
    files: tuple[EntryFile, ...]
    verification_detail: str

    @property
    def relative_paths(self) -> tuple[str, ...]:
        return tuple(item.relative_path for item in self.files)

    @property
    def row_count(self) -> int:
        return sum(item.row_count for item in self.files)

    def lines(self) -> tuple[str, ...]:
        return (
            f"workspace_root={self.workspace_root}",
            f"provider={self.provider} symbol={self.symbol} timeframe={TIMEFRAME}",
            f"manifest_entry_id={self.entry_id} manifest_status=COMPLETE",
            f"manifest_schema_version={self.manifest_schema_version}",
            f"collector_version={self.collector_version}",
            f"dataset_schema_version={self.dataset_schema_version}",
            f"effective_range={self.start_date}..{self.end_date}",
            f"combined_checksum={self.combined_checksum}",
            f"files={len(self.files)} rows={self.row_count}",
            *(f"file path={item.relative_path} rows={item.row_count} bytes={item.byte_size} "
              f"sha256={item.checksum}" for item in self.files),
            f"dataset_verification={self.verification_detail}",
        )


@dataclass(frozen=True)
class RawBar:
    """One stored row, untouched. Conversion to a market-domain bar is the provider's."""

    __slots__ = ("timestamp", "session", "open", "high", "low", "close", "volume")

    timestamp: object  # tz-aware datetime in the market timezone
    session: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def _complete_entry(connection: sqlite3.Connection, provider: str, symbol: str) -> sqlite3.Row:
    rows = connection.execute(
        f"SELECT * FROM {MANIFEST_TABLE} WHERE provider = ? AND symbol = ? AND data_kind = ?"
        " AND timeframe = ? ORDER BY start_date, end_date, id",
        (provider, symbol, DATA_KIND, TIMEFRAME)).fetchall()
    if not rows:
        raise DatasetNotFound(
            f"no manifest entry for {provider}/{symbol} {DATA_KIND}/{TIMEFRAME}; collect it first")
    complete = [row for row in rows if str(row["status"]) == "COMPLETE"]
    if not complete:
        statuses = ", ".join(f"{row['start_date']}..{row['end_date']}={row['status']}" for row in rows)
        raise DatasetIncomplete(f"no COMPLETE entry for {provider}/{symbol} ({statuses})")
    if len(complete) > 1:
        listed = ", ".join(f"{row['id']}:{row['start_date']}..{row['end_date']}" for row in complete)
        raise DatasetAmbiguous(
            f"{len(complete)} COMPLETE entries claim {provider}/{symbol} ({listed}); replay does "
            "not choose between datasets")
    return complete[0]


def _verify_entry(connection: sqlite3.Connection, row: sqlite3.Row,
                  workspace: Workspace) -> DatasetIdentity:
    entry_id = int(row["id"])
    if str(row["collector_version"]) != COLLECTOR_VERSION:
        raise DatasetIncomplete(
            f"entry {entry_id} was written by {row['collector_version']}, this replay understands "
            f"{COLLECTOR_VERSION}")
    if row["schema_version"] is None or int(row["schema_version"]) != DATASET_SCHEMA_VERSION:
        raise DatasetSchemaMismatch(
            f"entry {entry_id} records dataset schema {row['schema_version']}, this replay "
            f"understands {DATASET_SCHEMA_VERSION}")
    matched, detail = files_match(connection, entry_id, workspace=workspace)
    if not matched:
        raise DatasetChecksumMismatch(f"entry {entry_id}: {detail}")
    return DatasetIdentity(
        workspace_root=workspace.root, provider=str(row["provider"]), symbol=str(row["symbol"]),
        entry_id=entry_id, collector_version=str(row["collector_version"]),
        dataset_schema_version=int(row["schema_version"]),
        manifest_schema_version=MANIFEST_SCHEMA_VERSION, combined_checksum=str(row["checksum"]),
        start_date=date.fromisoformat(str(row["start_date"])),
        end_date=date.fromisoformat(str(row["end_date"])),
        files=entry_files(connection, entry_id), verification_detail=detail)


def _check_footer(path: Path) -> None:
    file = pq.ParquetFile(path)
    if file.schema_arrow != SCHEMA:
        raise DatasetSchemaMismatch(f"{path.name} does not carry the collector's Arrow schema")
    metadata = file.schema_arrow.metadata or {}
    stored = metadata.get(b"dataset_schema_version")
    if stored is None or int(stored) != DATASET_SCHEMA_VERSION:
        raise DatasetSchemaMismatch(
            f"{path.name} carries dataset_schema_version={stored!r}, expected {DATASET_SCHEMA_VERSION}")
    if metadata.get(b"timestamp_authority") != b"bar_start":
        raise DatasetSchemaMismatch(
            f"{path.name} does not declare bar_start timestamps; replay availability depends on it")


class ReplayDataset:
    """A verified, read-only view of one symbol's collected minute history.

    It holds every session in the manifest range. That is deliberate: an exact
    previous-session regular close is only knowable from the session before the one
    being replayed. Nothing here filters by time - the provider above it owns the
    point-in-time contract, and this object has no notion of ``as_of`` at all.
    """

    def __init__(self, identity: DatasetIdentity, rows: dict[date, tuple[RawBar, ...]]) -> None:
        self.identity = identity
        self._rows = rows
        self._dates = tuple(sorted(rows))

    @classmethod
    def load(cls, workspace: Workspace, symbol: str, *,
             provider: str = PROVIDER) -> "ReplayDataset":
        """Verify the manifest and the files, then index every stored row by ET date."""
        symbol = normalize_symbol(symbol)
        with manifest_connection(workspace, create=False) as connection:
            row = _complete_entry(connection, provider, symbol)
            identity = _verify_entry(connection, row, workspace)
        rows: dict[date, list[RawBar]] = {}
        for item in identity.files:
            path = workspace.root / item.relative_path
            _check_footer(path)
            table = pq.read_table(path, columns=list(ROW_COLUMNS))
            columns = {name: table.column(name).to_pylist() for name in ROW_COLUMNS}
            for index in range(table.num_rows):
                if columns["symbol"][index] != symbol:
                    raise SymbolMismatch(
                        f"{item.relative_path} row {index} holds symbol "
                        f"{columns['symbol'][index]!r}, not {symbol}")
                day = columns["trading_date"][index]
                rows.setdefault(day, []).append(RawBar(
                    columns["timestamp_utc"][index], columns["session"][index],
                    columns["open"][index], columns["high"][index], columns["low"][index],
                    columns["close"][index], columns["volume"][index]))
        indexed = {day: tuple(sorted(items, key=lambda bar: bar.timestamp))
                   for day, items in rows.items()}
        return cls(identity, indexed)

    @property
    def symbol(self) -> str:
        return self.identity.symbol

    @property
    def trading_dates(self) -> tuple[date, ...]:
        return self._dates

    def covers(self, day: date) -> bool:
        return self.identity.start_date <= day <= self.identity.end_date

    def assert_covers(self, day: date) -> None:
        if not self.covers(day):
            raise DateOutsideDataset(
                f"{day} is outside the collected range "
                f"{self.identity.start_date}..{self.identity.end_date}")

    def assert_symbol(self, symbol: str) -> None:
        if normalize_symbol(symbol) != self.symbol:
            raise SymbolMismatch(f"this dataset holds {self.symbol}, not {normalize_symbol(symbol)}")

    def session_rows(self, day: date) -> tuple[RawBar, ...]:
        """Every stored row of one ET trading date, oldest first."""
        self.assert_covers(day)
        rows = self._rows.get(day)
        if rows is None:
            raise SessionDataMissing(f"{self.symbol} has no stored row for {day}")
        return rows

    def rows_or_empty(self, day: date) -> tuple[RawBar, ...]:
        """Rows for a date that may legitimately hold none (a date before the range)."""
        return self._rows.get(day, ())


def load_dataset(workspace_root: Path, symbol: str, *,
                 provider: str = PROVIDER) -> ReplayDataset:
    return ReplayDataset.load(Workspace(Path(workspace_root)), symbol, provider=provider)


def dataset_errors() -> tuple[type[Exception], ...]:
    """The typed failures a CLI reports rather than raising as a traceback."""
    return (WorkspaceError,)


def describe_dates(days: Sequence[date]) -> str:
    return "none" if not days else f"{days[0]}..{days[-1]} ({len(days)})"
