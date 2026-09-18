"""``state/collector_manifest.sqlite3``: the registry the historical collector writes to.

Schema 1 built the table. Schema 2 adds what a range collection has to record - how many
XNYS sessions were expected and seen, how many requests it cost, which dataset schema
produced the rows - and a child table, because one range can land in more than one
Parquet partition and a single ``relative_path`` column cannot describe two files.
Schema 3 adds ``listing_state``, because a daily range that stops short has two entirely
different meanings - the symbol was not listed yet, or sessions are missing - and
``status`` cannot carry that: a PRE_LISTING dataset is COMPLETE over the range it covers.
The daily collector reuses this manifest rather than standing a second registry up beside
it, so both collectors' entries stay comparable and countable in one place.

The rule the schema itself enforces has not changed: a row cannot claim COMPLETE without
finished files behind it. ``complete_entry`` goes further and re-reads every file it is
given, so a checksum in the manifest is one this process computed from bytes on disk.

Journal mode stays on the rollback journal rather than WAL, because a WAL sidecar file
synced independently by Google Drive is a corrupt database on the other PC. A migration
runs only when the caller opened the manifest for writing (``create=True``); a reader
never rewrites a file the other PC may be syncing.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import datetime
import hashlib
from pathlib import Path
import sqlite3

from app.backtest.workspace.errors import ManifestError, ManifestIncomplete
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.safe_write import PARTIAL_SUFFIX, sha256_file, utc_now_iso


MANIFEST_SCHEMA_VERSION = 3
MANIFEST_TABLE = "collector_entries"
ENTRY_FILES_TABLE = "collector_entry_files"
STATUSES = ("PLANNED", "COLLECTING", "COMPLETE", "FAILED")
# Columns added in schema 2. Every one is nullable: SQLite cannot add a NOT NULL column
# to a populated table, and a schema-1 row has no answer for any of them.
METADATA_COLUMNS = ("expected_sessions", "actual_sessions", "regular_complete_sessions",
                    "premarket_sessions", "source_request_count", "schema_version",
                    "file_count", "byte_size")
#: Why a collected range covers what it covers. NULL on every minute-bar entry and on
#: every schema-2 row, which is what "this collector never answered the question" means.
LISTING_STATES = ("COMPLETE", "PRE_LISTING", "MISSING_DATA", "PROVIDER_FAILURE")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS manifest_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS {MANIFEST_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    collector_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    data_kind TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL,
    row_count INTEGER,
    checksum TEXT,
    relative_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expected_sessions INTEGER,
    actual_sessions INTEGER,
    regular_complete_sessions INTEGER,
    premarket_sessions INTEGER,
    source_request_count INTEGER,
    schema_version INTEGER,
    file_count INTEGER,
    byte_size INTEGER,
    listing_state TEXT,
    CHECK (status IN ('PLANNED', 'COLLECTING', 'COMPLETE', 'FAILED')),
    CHECK (start_date <= end_date),
    CHECK (relative_path IS NULL OR relative_path NOT LIKE '%{PARTIAL_SUFFIX}'),
    CHECK (status <> 'COMPLETE' OR (relative_path IS NOT NULL
                                    AND checksum IS NOT NULL
                                    AND row_count IS NOT NULL)),
    UNIQUE (provider, symbol, data_kind, timeframe, start_date, end_date)
);
CREATE TABLE IF NOT EXISTS {ENTRY_FILES_TABLE} (
    entry_id INTEGER NOT NULL REFERENCES {MANIFEST_TABLE}(id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    checksum TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    byte_size INTEGER NOT NULL,
    PRIMARY KEY (entry_id, relative_path),
    CHECK (relative_path NOT LIKE '%{PARTIAL_SUFFIX}'),
    CHECK (row_count >= 0),
    CHECK (byte_size > 0)
);
"""


@dataclass(frozen=True)
class ManifestKey:
    provider: str
    symbol: str
    data_kind: str
    timeframe: str
    start_date: str
    end_date: str


@dataclass(frozen=True)
class EntryFile:
    """One finished file of an entry, as a path relative to the workspace root."""

    relative_path: str
    checksum: str
    row_count: int
    byte_size: int


@dataclass(frozen=True)
class EntryMetadata:
    """Collection bookkeeping for one entry. Every field is optional by schema."""

    expected_sessions: int | None = None
    actual_sessions: int | None = None
    regular_complete_sessions: int | None = None
    premarket_sessions: int | None = None
    source_request_count: int | None = None
    schema_version: int | None = None

    def to_columns(self) -> dict[str, int | None]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


def _apply_pragmas(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")


def open_manifest(path: Path, *, create: bool = True) -> sqlite3.Connection:
    path = Path(path)
    if not create and not path.is_file():
        raise ManifestError(f"manifest {path} does not exist")
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    _apply_pragmas(connection)
    if create:
        connection.executescript(SCHEMA)
    _check_schema_version(connection, migrate=create)
    return connection


def _stored_version(connection: sqlite3.Connection) -> int | None:
    row = connection.execute(
        "SELECT value FROM manifest_meta WHERE key = 'manifest_schema_version'").fetchone()
    if row is None:
        return None
    try:
        return int(row["value"])
    except (TypeError, ValueError) as error:
        raise ManifestError(f"manifest schema version {row['value']!r} is not an integer") from error


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _migrate_to_2(connection: sqlite3.Connection) -> None:
    """Schema 1 to 2: the metadata columns and the per-file child table.

    ``executescript(SCHEMA)`` already created the child table; only the columns that
    SQLite cannot add through ``CREATE TABLE IF NOT EXISTS`` are added here.
    """
    existing = _table_columns(connection, MANIFEST_TABLE)
    for column in METADATA_COLUMNS:
        if column not in existing:
            connection.execute(f"ALTER TABLE {MANIFEST_TABLE} ADD COLUMN {column} INTEGER")


def _migrate_to_3(connection: sqlite3.Connection) -> None:
    """Schema 2 to 3: the listing-state column.

    Added without a CHECK constraint on purpose - SQLite cannot add one to a populated
    table - so the allowed values are enforced in ``set_listing_state`` instead.
    """
    if "listing_state" not in _table_columns(connection, MANIFEST_TABLE):
        connection.execute(f"ALTER TABLE {MANIFEST_TABLE} ADD COLUMN listing_state TEXT")


MIGRATIONS = {2: _migrate_to_2, 3: _migrate_to_3}


def _check_schema_version(connection: sqlite3.Connection, *, migrate: bool) -> None:
    stored = _stored_version(connection)
    if stored is None:
        connection.execute(
            "INSERT INTO manifest_meta (key, value) VALUES ('manifest_schema_version', ?)",
            (str(MANIFEST_SCHEMA_VERSION),))
        return
    if stored == MANIFEST_SCHEMA_VERSION:
        return
    if stored > MANIFEST_SCHEMA_VERSION or not migrate:
        raise ManifestError(
            f"manifest schema {stored} is not supported (expected {MANIFEST_SCHEMA_VERSION})")
    for version in range(stored + 1, MANIFEST_SCHEMA_VERSION + 1):
        step = MIGRATIONS.get(version)
        if step is None:
            raise ManifestError(f"no migration to manifest schema {version}")
        step(connection)
    connection.execute(
        "UPDATE manifest_meta SET value = ? WHERE key = 'manifest_schema_version'",
        (str(MANIFEST_SCHEMA_VERSION),))


@contextmanager
def manifest_connection(workspace: Workspace, *, create: bool = True) -> Iterator[sqlite3.Connection]:
    connection = open_manifest(workspace.manifest_path, create=create)
    try:
        yield connection
    finally:
        connection.close()


def quick_check(connection: sqlite3.Connection) -> str:
    return str(connection.execute("PRAGMA quick_check").fetchone()[0])


def plan_entry(connection: sqlite3.Connection, key: ManifestKey, *, collector_version: str,
               now: datetime | None = None) -> int:
    """Register an intended collection. Re-planning an existing key leaves the row alone."""
    stamp = utc_now_iso(now)
    existing = find_entry(connection, key)
    if existing is not None:
        return int(existing["id"])
    cursor = connection.execute(
        f"INSERT INTO {MANIFEST_TABLE} (provider, collector_version, symbol, data_kind, timeframe,"
        " start_date, end_date, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'PLANNED', ?, ?)",
        (key.provider, collector_version, key.symbol, key.data_kind, key.timeframe,
         key.start_date, key.end_date, stamp, stamp))
    return int(cursor.lastrowid)


def find_entry(connection: sqlite3.Connection, key: ManifestKey) -> sqlite3.Row | None:
    return connection.execute(
        f"SELECT * FROM {MANIFEST_TABLE} WHERE provider = ? AND symbol = ? AND data_kind = ?"
        " AND timeframe = ? AND start_date = ? AND end_date = ?",
        (key.provider, key.symbol, key.data_kind, key.timeframe,
         key.start_date, key.end_date)).fetchone()


def get_entry(connection: sqlite3.Connection, entry_id: int) -> sqlite3.Row:
    row = connection.execute(
        f"SELECT * FROM {MANIFEST_TABLE} WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise ManifestError(f"manifest entry {entry_id} does not exist")
    return row


def entry_files(connection: sqlite3.Connection, entry_id: int) -> tuple[EntryFile, ...]:
    rows = connection.execute(
        f"SELECT relative_path, checksum, row_count, byte_size FROM {ENTRY_FILES_TABLE}"
        " WHERE entry_id = ? ORDER BY relative_path", (entry_id,)).fetchall()
    return tuple(EntryFile(str(row["relative_path"]), str(row["checksum"]),
                           int(row["row_count"]), int(row["byte_size"])) for row in rows)


def mark_collecting(connection: sqlite3.Connection, entry_id: int, *, collector_version: str | None = None,
                    now: datetime | None = None) -> None:
    get_entry(connection, entry_id)
    if collector_version is None:
        connection.execute(
            f"UPDATE {MANIFEST_TABLE} SET status = 'COLLECTING', updated_at = ? WHERE id = ?",
            (utc_now_iso(now), entry_id))
        return
    connection.execute(
        f"UPDATE {MANIFEST_TABLE} SET status = 'COLLECTING', collector_version = ?,"
        " updated_at = ? WHERE id = ?", (collector_version, utc_now_iso(now), entry_id))


def mark_status(connection: sqlite3.Connection, entry_id: int, *, status: str,
                now: datetime | None = None) -> None:
    """Set an entry's status directly. Used to restore COMPLETE after a failed refresh."""
    if status not in STATUSES:
        raise ManifestError(f"{status} is not a manifest status")
    get_entry(connection, entry_id)
    connection.execute(
        f"UPDATE {MANIFEST_TABLE} SET status = ?, updated_at = ? WHERE id = ?",
        (status, utc_now_iso(now), entry_id))


def set_listing_state(connection: sqlite3.Connection, entry_id: int, *, state: str | None,
                      now: datetime | None = None) -> None:
    """Record why a range covers what it covers, beside the status that says whether it ran."""
    if state is not None and state not in LISTING_STATES:
        raise ManifestError(f"{state} is not a listing state")
    get_entry(connection, entry_id)
    connection.execute(
        f"UPDATE {MANIFEST_TABLE} SET listing_state = ?, updated_at = ? WHERE id = ?",
        (state, utc_now_iso(now), entry_id))


def mark_failed(connection: sqlite3.Connection, entry_id: int, *,
                now: datetime | None = None) -> None:
    get_entry(connection, entry_id)
    connection.execute(
        f"UPDATE {MANIFEST_TABLE} SET status = 'FAILED', updated_at = ? WHERE id = ?",
        (utc_now_iso(now), entry_id))


def mark_complete(connection: sqlite3.Connection, entry_id: int, *, workspace: Workspace,
                  relative_path: str, checksum: str, row_count: int,
                  now: datetime | None = None) -> None:
    """COMPLETE for a single-file entry: a partial file is never a finished entry."""
    get_entry(connection, entry_id)
    final = _validated_final(workspace, relative_path)
    connection.execute(
        f"UPDATE {MANIFEST_TABLE} SET status = 'COMPLETE', relative_path = ?, checksum = ?,"
        " row_count = ?, file_count = 1, byte_size = ?, updated_at = ? WHERE id = ?",
        (relative_path, checksum, row_count, final.stat().st_size, utc_now_iso(now), entry_id))


def _validated_final(workspace: Workspace, relative_path: str) -> Path:
    if relative_path.endswith(PARTIAL_SUFFIX):
        raise ManifestIncomplete(f"{relative_path} is a partial file")
    final = workspace.root / relative_path
    if not final.is_file():
        raise ManifestIncomplete(f"{relative_path} does not exist under the workspace root")
    if final.stat().st_size == 0:
        raise ManifestIncomplete(f"{relative_path} is empty")
    return final


def combined_checksum(files: Sequence[EntryFile]) -> str:
    """One deterministic digest over an entry's files: path, checksum, and row count.

    Defined over the manifest's own record of the files rather than over concatenated
    bytes, so it can be recomputed without re-reading gigabytes and stays stable however
    the files are ordered on disk.
    """
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda entry: entry.relative_path):
        digest.update(f"{item.relative_path}\t{item.checksum}\t{item.row_count}\n".encode("utf-8"))
    return digest.hexdigest()


def complete_entry(connection: sqlite3.Connection, entry_id: int, *, workspace: Workspace,
                   files: Sequence[EntryFile], relative_path: str,
                   metadata: EntryMetadata | None = None,
                   now: datetime | None = None) -> str:
    """COMPLETE for an entry whose rows landed in one or more Parquet partitions.

    Every file is re-read here: the checksum stored in the manifest is the one this
    process computed from the bytes now on disk, not the one the caller believed. The
    returned value is the entry's combined checksum.
    """
    get_entry(connection, entry_id)
    if not files:
        raise ManifestIncomplete("a COMPLETE entry needs at least one file")
    verified: list[EntryFile] = []
    for item in files:
        final = _validated_final(workspace, item.relative_path)
        actual = sha256_file(final)
        if actual != item.checksum:
            raise ManifestIncomplete(
                f"{item.relative_path} checksum on disk does not match the collected checksum")
        verified.append(EntryFile(item.relative_path, actual, item.row_count,
                                  final.stat().st_size))
    if relative_path.endswith(PARTIAL_SUFFIX):
        raise ManifestIncomplete(f"{relative_path} is a partial path")
    if not (workspace.root / relative_path).exists():
        raise ManifestIncomplete(f"{relative_path} does not exist under the workspace root")
    checksum = combined_checksum(verified)
    columns = (metadata or EntryMetadata()).to_columns()
    connection.execute(f"DELETE FROM {ENTRY_FILES_TABLE} WHERE entry_id = ?", (entry_id,))
    connection.executemany(
        f"INSERT INTO {ENTRY_FILES_TABLE} (entry_id, relative_path, checksum, row_count, byte_size)"
        " VALUES (?, ?, ?, ?, ?)",
        [(entry_id, item.relative_path, item.checksum, item.row_count, item.byte_size)
         for item in verified])
    assignments = ", ".join(f"{name} = ?" for name in columns)
    connection.execute(
        f"UPDATE {MANIFEST_TABLE} SET status = 'COMPLETE', relative_path = ?, checksum = ?,"
        f" row_count = ?, file_count = ?, byte_size = ?, updated_at = ?, {assignments}"
        " WHERE id = ?",
        (relative_path, checksum, sum(item.row_count for item in verified), len(verified),
         sum(item.byte_size for item in verified), utc_now_iso(now), *columns.values(), entry_id))
    return checksum


def verify_entry(connection: sqlite3.Connection, entry_id: int, *,
                 workspace: Workspace) -> tuple[bool, str]:
    """Does a COMPLETE entry still match the files on disk? (verdict, reason)."""
    row = get_entry(connection, entry_id)
    if str(row["status"]) != "COMPLETE":
        return False, f"status is {row['status']}"
    return files_match(connection, entry_id, workspace=workspace)


def files_match(connection: sqlite3.Connection, entry_id: int, *,
                workspace: Workspace) -> tuple[bool, str]:
    """Do the files an entry names still exist unchanged? Status is not consulted."""
    row = get_entry(connection, entry_id)
    files = entry_files(connection, entry_id)
    if not files:
        return False, "no file rows recorded for the entry"
    for item in files:
        path = workspace.root / item.relative_path
        if not path.is_file():
            return False, f"{item.relative_path} is missing"
        if path.stat().st_size != item.byte_size:
            return False, f"{item.relative_path} changed size"
        if sha256_file(path) != item.checksum:
            return False, f"{item.relative_path} checksum differs"
    if combined_checksum(files) != str(row["checksum"]):
        return False, "combined checksum differs from the recorded files"
    return True, "files match the manifest"


def path_owners(connection: sqlite3.Connection, relative_path: str) -> tuple[int, ...]:
    """Which COMPLETE entries claim a file at this path? (empty when nobody does)."""
    rows = connection.execute(
        f"SELECT f.entry_id AS entry_id FROM {ENTRY_FILES_TABLE} f"
        f" JOIN {MANIFEST_TABLE} e ON e.id = f.entry_id"
        " WHERE f.relative_path = ? AND e.status = 'COMPLETE' ORDER BY f.entry_id",
        (relative_path,)).fetchall()
    return tuple(int(row["entry_id"]) for row in rows)


def entries_of_kind(connection: sqlite3.Connection, *, provider: str, data_kind: str,
                    timeframe: str) -> tuple[sqlite3.Row, ...]:
    """Every entry of one collector's kind, newest range last. Status is not filtered."""
    rows = connection.execute(
        f"SELECT * FROM {MANIFEST_TABLE} WHERE provider = ? AND data_kind = ? AND timeframe = ?"
        " ORDER BY symbol, start_date, end_date",
        (provider, data_kind, timeframe)).fetchall()
    return tuple(rows)


def counts_by_status(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        f"SELECT status, COUNT(*) AS total FROM {MANIFEST_TABLE} GROUP BY status").fetchall()
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[str(row["status"])] = int(row["total"])
    return counts
