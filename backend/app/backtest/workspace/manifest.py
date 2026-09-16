"""``state/collector_manifest.sqlite3``: schema foundation for the historical collector.

This stage builds the table and the safe helpers only. No historical data is registered
here, and no collector exists yet. The one rule the schema itself enforces is that a row
cannot claim COMPLETE without a finished file behind it: checksum, row count, and a
relative path that is not a ``.partial`` leftover.

Journal mode stays on the rollback journal rather than WAL, because a WAL sidecar file
synced independently by Google Drive is a corrupt database on the other PC.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3

from app.backtest.workspace.errors import ManifestError, ManifestIncomplete
from app.backtest.workspace.layout import Workspace
from app.backtest.workspace.safe_write import PARTIAL_SUFFIX, utc_now_iso


MANIFEST_SCHEMA_VERSION = 1
MANIFEST_TABLE = "collector_entries"
STATUSES = ("PLANNED", "COLLECTING", "COMPLETE", "FAILED")

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
    CHECK (status IN ('PLANNED', 'COLLECTING', 'COMPLETE', 'FAILED')),
    CHECK (start_date <= end_date),
    CHECK (relative_path IS NULL OR relative_path NOT LIKE '%{PARTIAL_SUFFIX}'),
    CHECK (status <> 'COMPLETE' OR (relative_path IS NOT NULL
                                    AND checksum IS NOT NULL
                                    AND row_count IS NOT NULL)),
    UNIQUE (provider, symbol, data_kind, timeframe, start_date, end_date)
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
    _check_schema_version(connection)
    return connection


def _check_schema_version(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT value FROM manifest_meta WHERE key = 'manifest_schema_version'").fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO manifest_meta (key, value) VALUES ('manifest_schema_version', ?)",
            (str(MANIFEST_SCHEMA_VERSION),))
        return
    if int(row["value"]) != MANIFEST_SCHEMA_VERSION:
        raise ManifestError(
            f"manifest schema {row['value']} is not supported "
            f"(expected {MANIFEST_SCHEMA_VERSION})")


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


def mark_collecting(connection: sqlite3.Connection, entry_id: int, *,
                    now: datetime | None = None) -> None:
    get_entry(connection, entry_id)
    connection.execute(
        f"UPDATE {MANIFEST_TABLE} SET status = 'COLLECTING', updated_at = ? WHERE id = ?",
        (utc_now_iso(now), entry_id))


def mark_failed(connection: sqlite3.Connection, entry_id: int, *,
                now: datetime | None = None) -> None:
    get_entry(connection, entry_id)
    connection.execute(
        f"UPDATE {MANIFEST_TABLE} SET status = 'FAILED', updated_at = ? WHERE id = ?",
        (utc_now_iso(now), entry_id))


def mark_complete(connection: sqlite3.Connection, entry_id: int, *, workspace: Workspace,
                  relative_path: str, checksum: str, row_count: int,
                  now: datetime | None = None) -> None:
    """COMPLETE only after the final file exists: a partial file is never a finished entry."""
    get_entry(connection, entry_id)
    if relative_path.endswith(PARTIAL_SUFFIX):
        raise ManifestIncomplete(f"{relative_path} is a partial file")
    final = workspace.root / relative_path
    if not final.is_file():
        raise ManifestIncomplete(f"{relative_path} does not exist under the workspace root")
    if final.stat().st_size == 0:
        raise ManifestIncomplete(f"{relative_path} is empty")
    connection.execute(
        f"UPDATE {MANIFEST_TABLE} SET status = 'COMPLETE', relative_path = ?, checksum = ?,"
        " row_count = ?, updated_at = ? WHERE id = ?",
        (relative_path, checksum, row_count, utc_now_iso(now), entry_id))


def counts_by_status(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        f"SELECT status, COUNT(*) AS total FROM {MANIFEST_TABLE} GROUP BY status").fetchall()
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[str(row["status"])] = int(row["total"])
    return counts
