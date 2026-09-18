"""Durable Parquet storage for collected minute bars, one file per symbol and year.

Partition choice, measured rather than assumed: one AAPL year is 192,309 rows and about
7 MiB of Parquet, and the readers this store exists for - replay and the backtester -
open one symbol over a contiguous date range, so a whole year in one file is a single
open instead of 251. Google Drive syncs a handful of medium files far better than
thousands of small ones, and a re-collection rewrites one file rather than a directory.
A finer grain only pays off when a single session has to be updated in isolation, which
is not how a range collection behaves.

Files are written through the workspace safe-write contract: rows stream into a sibling
``.partial``, and the rename into place happens only after the whole collection has
validated. A failed run therefore leaves an existing COMPLETE file exactly as it was.
"""

from collections.abc import Iterator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import pyarrow as pa
import pyarrow.parquet as pq

from app.backtest.collector.dataset import SCHEMA
from app.backtest.collector.errors import StorageError
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.safe_write import PartialWrite, safe_write


NORMALIZED_ROOT = "market_data/normalized/minute"
RAW_ROOT = "market_data/raw"
# Rows buffered before a row group is written. Large enough to keep row groups useful for
# a range scan, small enough that a year never sits in memory at once.
FLUSH_ROWS = 50_000
COMPRESSION = "snappy"


@dataclass(frozen=True)
class WrittenPartition:
    year: int
    relative_path: str
    checksum: str
    row_count: int
    byte_size: int


def relative_partition_path(provider: str, symbol: str, year: int) -> str:
    return f"{NORMALIZED_ROOT}/{provider}/{symbol}/{year:04d}.parquet"


def partition_path(workspace: Workspace, provider: str, symbol: str, year: int) -> Path:
    return workspace.root / relative_partition_path(provider, symbol, year)


def symbol_directory(workspace: Workspace, provider: str, symbol: str) -> Path:
    return workspace.root / NORMALIZED_ROOT / provider / symbol


def relative_symbol_directory(provider: str, symbol: str) -> str:
    return f"{NORMALIZED_ROOT}/{provider}/{symbol}"


def relative_raw_page_path(provider: str, symbol: str, start: str, end: str, page: int) -> str:
    return f"{RAW_ROOT}/{provider}/{symbol}/{symbol}_{start}_{end}_page{page:02d}.json"


def existing_partitions(workspace: Workspace, provider: str, symbol: str) -> tuple[Path, ...]:
    directory = symbol_directory(workspace, provider, symbol)
    return tuple(sorted(directory.glob("*.parquet"))) if directory.is_dir() else ()


def read_partition(path: Path) -> pa.Table:
    try:
        return pq.read_table(path)
    except (OSError, pa.ArrowInvalid) as error:
        raise StorageError(f"cannot read Parquet partition {path}: {error}") from error


def partition_row_count(path: Path) -> int:
    try:
        return int(pq.ParquetFile(path).metadata.num_rows)
    except (OSError, pa.ArrowInvalid) as error:
        raise StorageError(f"cannot read Parquet footer of {path}: {error}") from error


def iter_partition_batches(path: Path, batch_size: int = FLUSH_ROWS) -> Iterator[pa.RecordBatch]:
    return pq.ParquetFile(path).iter_batches(batch_size=batch_size)


class PartitionWriter:
    """Streams row batches into one ``.partial`` per year; commits them together.

    Nothing reaches a final path until ``commit`` is called, and ``commit`` is called
    only after validation has passed, so the store never holds a dataset the collector
    would have refused.
    """

    def __init__(self, workspace: Workspace, *, provider: str, symbol: str,
                 flush_rows: int | None = None) -> None:
        self._workspace = workspace
        self._provider = provider
        self._symbol = symbol
        self._flush_rows = max(1, FLUSH_ROWS if flush_rows is None else flush_rows)
        self._stack = ExitStack()
        self._handles: dict[int, PartialWrite] = {}
        self._writers: dict[int, pq.ParquetWriter] = {}
        self._rows: dict[int, int] = {}
        self._pending: dict[int, list[pa.Table]] = {}
        self._pending_rows: dict[int, int] = {}
        self._committed = False

    def __enter__(self) -> "PartitionWriter":
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> bool:
        if self._committed:
            return False
        self._close_writers()
        if exc is None:
            exc_type, exc = StorageError, StorageError("partitions were never committed")
        self._stack.__exit__(exc_type, exc, traceback)
        return False

    @property
    def years(self) -> tuple[int, ...]:
        return tuple(sorted(set(self._rows) | set(self._pending)))

    def target_paths(self) -> tuple[Path, ...]:
        return tuple(partition_path(self._workspace, self._provider, self._symbol, year)
                     for year in self.years)

    def write_table(self, year: int, table: pa.Table) -> None:
        if self._committed:
            raise StorageError("partitions are already committed")
        if table.num_rows == 0:
            return
        if table.schema != SCHEMA:
            raise StorageError("row batch does not match the dataset schema")
        self._pending.setdefault(year, []).append(table)
        self._pending_rows[year] = self._pending_rows.get(year, 0) + table.num_rows
        if self._pending_rows[year] >= self._flush_rows:
            self._flush(year)

    def flush_all(self) -> None:
        for year in sorted(self._pending):
            self._flush(year)

    def _flush(self, year: int) -> None:
        tables = self._pending.pop(year, [])
        self._pending_rows.pop(year, None)
        if not tables:
            return
        batch = pa.concat_tables(tables)
        self._writer(year).write_table(batch)
        self._rows[year] = self._rows.get(year, 0) + batch.num_rows

    def _writer(self, year: int) -> pq.ParquetWriter:
        writer = self._writers.get(year)
        if writer is not None:
            return writer
        destination = partition_path(self._workspace, self._provider, self._symbol, year)
        handle = self._stack.enter_context(safe_write(destination))
        writer = pq.ParquetWriter(handle.partial_path, SCHEMA, compression=COMPRESSION)
        self._handles[year] = handle
        self._writers[year] = writer
        return writer

    def _close_writers(self) -> None:
        for writer in self._writers.values():
            try:
                writer.close()
            except (OSError, pa.ArrowException):  # pragma: no cover - best effort on abort
                pass
        self._writers.clear()

    def commit(self) -> tuple[WrittenPartition, ...]:
        """Close the files, rename every partial into place, and report what was written."""
        if self._committed:
            raise StorageError("partitions are already committed")
        self.flush_all()
        if not self._rows:
            raise StorageError("no rows were written; nothing to commit")
        self._close_writers()
        self._stack.close()  # safe_write: checksum each partial, then atomic replace
        self._committed = True
        written = []
        for year, rows in sorted(self._rows.items()):
            handle = self._handles[year]
            if handle.checksum is None or handle.size is None:  # pragma: no cover - safe_write sets both
                raise StorageError(f"partition {year} was not checksummed")
            written.append(WrittenPartition(
                year=year,
                relative_path=relative_partition_path(self._provider, self._symbol, year),
                checksum=handle.checksum, row_count=rows, byte_size=handle.size))
        return tuple(written)


def verify_partitions(workspace: Workspace, partitions: Sequence[WrittenPartition]) -> tuple[str, ...]:
    """Re-open each committed file and confirm its row count and schema from the footer."""
    notes = []
    for partition in partitions:
        path = workspace.root / partition.relative_path
        file = pq.ParquetFile(path)
        if file.metadata.num_rows != partition.row_count:
            raise StorageError(
                f"{partition.relative_path} holds {file.metadata.num_rows} rows, "
                f"{partition.row_count} were written")
        if file.schema_arrow != SCHEMA:
            raise StorageError(f"{partition.relative_path} does not match the dataset schema")
        notes.append(f"{partition.relative_path} rows={partition.row_count} "
                     f"row_groups={file.metadata.num_row_groups} bytes={partition.byte_size}")
    return tuple(notes)
