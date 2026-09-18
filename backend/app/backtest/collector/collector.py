"""One symbol, one range, one durable dataset: the collector's whole flow.

Order matters more than anything else here. The environment is checked before a client
exists; the range is clamped to the last session Stocks Basic publishes before a request
is made; rows stream into ``.partial`` files while they are validated; and the manifest
only ever learns the word COMPLETE after the final Parquet files are in place and their
checksums have been read back from disk. A failed run leaves the previous COMPLETE
dataset exactly as it was.

Re-running the same symbol and range costs zero API calls: the manifest entry is checked
against the collector version, the dataset schema version, and the checksum of every file
it names, and a match returns the recorded outcome instead of fetching it again.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import sqlite3
import time

from app.backtest.collector.dataset import (
    DATA_KIND, DATASET_SCHEMA_VERSION, TIMEFRAME, PrecisionAudit, RowBuffer, audit_precision,
)
from app.backtest.collector.environment import EnvironmentReport, assert_local_environment
from app.backtest.collector.errors import (
    CollectorError, DataQualityFailed, IncompleteRange, PartitionConflict, RangeTooLarge,
    RegularMinutesMissing, SessionsMissing,
)
from app.backtest.collector.range import CollectionRange, assert_publishable
from app.backtest.collector.storage import (
    PartitionWriter, WrittenPartition, relative_raw_page_path, relative_symbol_directory,
    verify_partitions,
)
from app.backtest.collector.validation import CollectionValidation, StreamingValidator
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.lock import DEFAULT_LEASE_SECONDS, writer_lock
from app.backtest.workspace.manifest import (
    EntryFile, EntryMetadata, ManifestKey, complete_entry, entry_files, files_match, find_entry,
    get_entry, manifest_connection, mark_collecting, mark_failed, mark_status, path_owners,
    plan_entry, verify_entry,
)
from app.backtest.workspace.safe_write import safe_write
from app.core.config import Settings
from app.integrations.massive.client import (
    MassiveAggregatesClient, RequestAccounting, build_massive_client, long_range_page_cap,
)


COLLECTOR_VERSION = "massive-historical-collector-v1"
PROVIDER = "massive"
LOCK_PURPOSE = "collect_historical_massive"
# A key can only reach a saved page through a ``next_url`` query parameter; the bytes are
# kept otherwise untouched so a raw file stays the provider's own answer.
RAW_SECRET_QUERY = re.compile(rb"(?i)([?&]api[_-]?key=)[^\"&\s]+")


ClientFactory = Callable[..., MassiveAggregatesClient]


@dataclass(frozen=True)
class CollectionOutcome:
    symbol: str
    collection_range: CollectionRange
    entry_id: int
    cache_hit: bool
    manifest_status: str
    checksum: str
    partitions: tuple[WrittenPartition, ...]
    validation: CollectionValidation | None
    precision: PrecisionAudit
    http_requests: int
    pages: int
    retries: int
    response_bytes: int
    elapsed_seconds: float
    raw_pages_saved: int
    environment: EnvironmentReport
    cache_detail: str = ""
    #: Manifest entries this run's overwrite displaced: another entry's COMPLETE file was
    #: replaced, so that entry no longer describes the bytes on disk and was demoted.
    displaced_entries: tuple[int, ...] = ()

    @property
    def row_count(self) -> int:
        return sum(partition.row_count for partition in self.partitions)

    @property
    def byte_size(self) -> int:
        return sum(partition.byte_size for partition in self.partitions)

    @property
    def relative_paths(self) -> tuple[str, ...]:
        return tuple(partition.relative_path for partition in self.partitions)


def manifest_key(symbol: str, collection_range: CollectionRange) -> ManifestKey:
    return ManifestKey(provider=PROVIDER, symbol=symbol, data_kind=DATA_KIND, timeframe=TIMEFRAME,
                       start_date=collection_range.effective_start.isoformat(),
                       end_date=collection_range.effective_end.isoformat())


def _page_cap(collection_range: CollectionRange) -> int:
    """The bounded page cap for this range, or a typed refusal to collect it in one go."""
    try:
        return long_range_page_cap(collection_range.request_start, collection_range.request_end)
    except ValueError as error:
        raise RangeTooLarge(
            f"{collection_range.effective_start}..{collection_range.effective_end} is too long for "
            f"one bounded fetch ({error}); collect it in shorter ranges") from None


def _failure(validation: CollectionValidation) -> CollectorError:
    reason = validation.failures()[0]
    if reason.startswith("INCOMPLETE_RANGE"):
        return IncompleteRange(reason)
    if reason.startswith("SESSIONS_MISSING"):
        return SessionsMissing(reason)
    if reason.startswith("REGULAR_MINUTES_MISSING"):
        return RegularMinutesMissing(reason)
    return DataQualityFailed(reason)


def collect(workspace: Workspace, *, symbol: str, collection_range: CollectionRange,
            settings: Settings, now: datetime, client_factory: ClientFactory = build_massive_client,
            save_raw: bool = False, refresh: bool = False, overwrite_partitions: bool = False,
            lease_seconds: int = DEFAULT_LEASE_SECONDS,
            timer: Callable[[], float] = time.monotonic) -> CollectionOutcome:
    """Collect one symbol over one range into the workspace, or refuse with a typed code."""
    environment = assert_local_environment(settings=settings)
    assert_publishable(collection_range, now)
    page_cap = _page_cap(collection_range)
    key = manifest_key(symbol, collection_range)
    with writer_lock(workspace, purpose=f"{LOCK_PURPOSE}:{symbol}", lease_seconds=lease_seconds):
        with manifest_connection(workspace) as connection:
            # Everything that can refuse this run is checked before the manifest is
            # touched, so a refusal leaves no half-meaningful PLANNED row behind.
            existing = find_entry(connection, key)
            existing_id = int(existing["id"]) if existing is not None else None
            if existing_id is not None and not refresh:
                cached = _cache_hit(connection, existing_id, workspace=workspace,
                                    collection_range=collection_range, symbol=symbol,
                                    environment=environment)
                if cached is not None:
                    return cached
            _check_partition_conflicts(connection, workspace=workspace, symbol=symbol,
                                       collection_range=collection_range, entry_id=existing_id,
                                       overwrite=overwrite_partitions)
            entry_id = plan_entry(connection, key, collector_version=COLLECTOR_VERSION, now=now)
            was_complete = str(get_entry(connection, entry_id)["status"]) == "COMPLETE"
            mark_collecting(connection, entry_id, collector_version=COLLECTOR_VERSION, now=now)
            try:
                return _run(connection, workspace, symbol=symbol, collection_range=collection_range,
                            settings=settings, entry_id=entry_id, client_factory=client_factory,
                            save_raw=save_raw, environment=environment, now=now, timer=timer,
                            page_cap=page_cap)
            except BaseException:
                # A refresh that fails must not demote the dataset it failed to replace:
                # those files were never touched, so the old entry is still true.
                if was_complete and files_match(connection, entry_id, workspace=workspace)[0]:
                    mark_status(connection, entry_id, status="COMPLETE", now=now)
                else:
                    mark_failed(connection, entry_id, now=now)
                raise


def _cache_hit(connection: sqlite3.Connection, entry_id: int, *, workspace: Workspace, symbol: str,
               collection_range: CollectionRange,
               environment: EnvironmentReport) -> CollectionOutcome | None:
    """A COMPLETE entry of this collector and schema whose files still match: no request."""
    row = get_entry(connection, entry_id)
    if str(row["status"]) != "COMPLETE":
        return None
    if str(row["collector_version"]) != COLLECTOR_VERSION:
        return None
    if row["schema_version"] is None or int(row["schema_version"]) != DATASET_SCHEMA_VERSION:
        return None
    verified, detail = verify_entry(connection, entry_id, workspace=workspace)
    if not verified:
        return None
    files = entry_files(connection, entry_id)
    partitions = tuple(
        WrittenPartition(year=int(Path(item.relative_path).stem), relative_path=item.relative_path,
                         checksum=item.checksum, row_count=item.row_count, byte_size=item.byte_size)
        for item in files)
    return CollectionOutcome(
        symbol=symbol, collection_range=collection_range, entry_id=entry_id, cache_hit=True,
        manifest_status="COMPLETE", checksum=str(row["checksum"]), partitions=partitions,
        validation=None, precision=PrecisionAudit(), http_requests=0, pages=0, retries=0,
        response_bytes=0, elapsed_seconds=0.0, raw_pages_saved=0, environment=environment,
        cache_detail=detail)


def _check_partition_conflicts(connection: sqlite3.Connection, *, workspace: Workspace, symbol: str,
                               collection_range: CollectionRange, entry_id: int | None,
                               overwrite: bool) -> None:
    """Refuse to overwrite a finished file that a different manifest entry owns."""
    if overwrite:
        return
    for year in collection_range.years:
        relative = f"{relative_symbol_directory(PROVIDER, symbol)}/{year:04d}.parquet"
        if not (workspace.root / relative).is_file():
            continue
        owners = [owner for owner in path_owners(connection, relative) if owner != entry_id]
        if owners:
            raise PartitionConflict(
                f"{relative} is already a COMPLETE file of manifest entry {owners[0]}; pass "
                "--overwrite-partitions to replace it")


def _raw_saver(workspace: Workspace, symbol: str, collection_range: CollectionRange,
               counter: list[int]) -> Callable[[bytes], None]:
    start = collection_range.effective_start.isoformat()
    end = collection_range.effective_end.isoformat()

    def save(body: bytes) -> None:
        counter[0] += 1
        relative = relative_raw_page_path(PROVIDER, symbol, start, end, counter[0])
        with safe_write(workspace.root / relative) as handle:
            handle.partial_path.write_bytes(RAW_SECRET_QUERY.sub(rb"\1REDACTED", body))

    return save


def _run(connection: sqlite3.Connection, workspace: Workspace, *, symbol: str, collection_range: CollectionRange,
         settings: Settings, entry_id: int, client_factory: ClientFactory, save_raw: bool,
         environment: EnvironmentReport, now: datetime, timer: Callable[[], float],
         page_cap: int) -> CollectionOutcome:
    precision = PrecisionAudit()
    raw_counter = [0]
    save_raw_page = _raw_saver(workspace, symbol, collection_range, raw_counter) if save_raw else None

    def observe(body: bytes) -> None:
        audit_precision(body, precision)
        if save_raw_page is not None:
            save_raw_page(body)

    client = client_factory(settings, page_observer=observe)
    client.accounting = RequestAccounting()
    buffer = RowBuffer(PROVIDER, symbol)
    started = timer()
    with PartitionWriter(workspace, provider=PROVIDER, symbol=symbol) as writer:
        def sink(window, bars: Sequence) -> None:
            for bar in bars:
                buffer.add(bar, window)
            writer.write_table(window.session_date.year, buffer.to_table())
            buffer.clear()

        validator = StreamingValidator(collection_range, sink)
        for page in client.iter_minute_aggregate_pages(
                symbol, collection_range.request_start, collection_range.request_end,
                max_pages=page_cap, keep_results=False):
            for bar in page.bars:
                validator.add(bar)
        validation = validator.finish()
        elapsed = timer() - started
        if not validation.complete:
            raise _failure(validation)
        partitions = writer.commit()
    verify_partitions(workspace, partitions)
    metadata = EntryMetadata(
        expected_sessions=collection_range.expected_sessions,
        actual_sessions=validation.coverage.sessions_present,
        regular_complete_sessions=validation.coverage.sessions_with_complete_regular_minutes,
        premarket_sessions=validation.coverage.sessions_with_premarket,
        source_request_count=client.accounting.http_requests,
        schema_version=DATASET_SCHEMA_VERSION)
    checksum = complete_entry(
        connection, entry_id, workspace=workspace,
        files=[EntryFile(item.relative_path, item.checksum, item.row_count, item.byte_size)
               for item in partitions],
        relative_path=relative_symbol_directory(PROVIDER, symbol), metadata=metadata, now=now)
    displaced = _demote_displaced(connection, workspace, entry_id=entry_id,
                                  partitions=partitions, now=now)
    accounting = client.accounting
    return CollectionOutcome(
        symbol=symbol, collection_range=collection_range, entry_id=entry_id, cache_hit=False,
        manifest_status="COMPLETE", checksum=checksum, partitions=partitions,
        validation=validation, precision=precision, http_requests=accounting.http_requests,
        pages=accounting.pages, retries=accounting.retries,
        response_bytes=accounting.response_bytes, elapsed_seconds=elapsed,
        raw_pages_saved=raw_counter[0], environment=environment, displaced_entries=displaced)


def _demote_displaced(connection: sqlite3.Connection, workspace: Workspace, *, entry_id: int,
                      partitions: Sequence[WrittenPartition], now: datetime) -> tuple[int, ...]:
    """Demote every other COMPLETE entry whose file this run replaced.

    ``--overwrite-partitions`` is the only way a finished file owned by another entry is
    replaced. That entry then names bytes that no longer exist, and leaving it COMPLETE
    would give a reader two COMPLETE entries for one symbol - one of them false. An entry
    whose files still verify is left alone; nothing is demoted on a guess.
    """
    displaced: set[int] = set()
    for partition in partitions:
        for owner in path_owners(connection, partition.relative_path):
            if owner != entry_id and not files_match(connection, owner, workspace=workspace)[0]:
                displaced.add(owner)
    for owner in sorted(displaced):
        mark_failed(connection, owner, now=now)
    return tuple(sorted(displaced))
