"""USB-HIST-V1: one frozen, content-addressed description of the Common Historical Store.

The snapshot lists every member file with its sha256 (Common Raw files, and the legacy A files
it references read-only), the coverage of each data type over the snapshot window, the minute
session audit, and the authority contract. It is written once into
``market_data/metadata/historical_snapshot/<snapshot_id>/`` and refused if that directory
already holds a snapshot: a change is a new snapshot id (USB-HIST-V2, ...), never an edit.
"""

from collections.abc import Sequence
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from app.backtest.historical_store.c_freeze import sha256_file
from app.backtest.historical_store.coverage import legacy_entries
from app.backtest.historical_store.raw_fetch import DAILY_DIR, MINUTE_DIR, read_ledger

SNAPSHOT_DIR = "market_data/metadata/historical_snapshot"
POINTER = "state/historical/CURRENT_HISTORICAL_SNAPSHOT.json"

AUTHORITY_CONTRACT = {
    "contract_id": "USB-DAILY-AUTHORITY-V1",
    "authorities": {
        "MASSIVE_GROUPED_DAILY": {
            "endpoint": "/v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=false",
            "stored_at": "market_data/raw/massive/grouped_daily/<YYYY>/<date>.json.gz",
            "payload_form": "wrapped-json-sort-keys ({format, session, body}; body = provider JSON)"},
        "MASSIVE_TICKER_AGGREGATE": {
            "endpoint": "/v2/aggs/ticker/{symbol}/range/1/day/{from}/{to}?adjusted=false",
            "stored_at": ["market_data/raw/massive/per_symbol_daily/<SYMBOL>/ (provider bytes)",
                          "market_data/normalized/daily/massive/<SYMBOL>.parquet (legacy A, manifest daily_bars)"]},
    },
    "rules": [
        "GROUPED_DAILY != PER_SYMBOL_DAILY: never merged, never used to fill each other.",
        "A run identity names the daily authority it read; a view reads exactly one daily authority.",
        "adjusted=false everywhere; split handling happens in views from the splits records.",
    ],
    "evidence": "overlap 2025-08-11..2026-09-15, 101 symbols, 27,876 symbol-days: volume differs on 3,853, "
                "transactions 3,703, vwap 4,294, high 22, low 26, open 1, close 0.",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def common_raw_members(root: Path, directory: str, kind: str) -> tuple[list[dict], list[dict]]:
    files, ledgers = [], []
    base = root / directory
    for ledger_path in sorted(base.rglob("*.request.json")) if base.is_dir() else ():
        ledger = read_ledger(ledger_path)
        ledgers.append(ledger)
        files.append({"path": str(ledger_path.relative_to(root)), "kind": f"{kind}_ledger",
                      "sha256": sha256_file(ledger_path), "size": ledger_path.stat().st_size,
                      "role": "COMMON_RAW"})
        for page in ledger.get("pages", ()):
            path = ledger_path.parent / page["file"]
            found = sha256_file(path)
            if found != page["file_sha256"]:
                raise RuntimeError(f"{path} sha256 {found} != ledger {page['file_sha256']}")
            files.append({"path": str(path.relative_to(root)), "kind": kind, "sha256": found,
                          "size": path.stat().st_size, "role": "COMMON_RAW",
                          "authority": "MASSIVE_TICKER_AGGREGATE", "rows": page["results"]})
    return files, ledgers


def reference_daily_members(root: Path, directory: str) -> list[dict]:
    """Daily reference snapshot pages and ledgers (provider bytes), verified against their ledger."""
    files = []
    base = root / directory
    for ledger_path in sorted(base.rglob("*.request.json")) if base.is_dir() else ():
        ledger = read_ledger(ledger_path)
        files.append({"path": str(ledger_path.relative_to(root)), "kind": "REFERENCE_DAILY_LEDGER",
                      "sha256": sha256_file(ledger_path), "size": ledger_path.stat().st_size, "role": "COMMON_RAW"})
        for page in ledger.get("pages", ()):
            path = ledger_path.parent / page["file"]
            found = sha256_file(path)
            if found != page["file_sha256"]:
                raise RuntimeError(f"{path} sha256 {found} != ledger {page['file_sha256']}")
            files.append({"path": str(path.relative_to(root)), "kind": "REFERENCE_DAILY", "sha256": found,
                          "size": path.stat().st_size, "role": "COMMON_RAW",
                          "authority": "MASSIVE_REFERENCE_TICKERS", "rows": page["results"]})
    return files


def build(root: Path, *, snapshot_id: str, sessions: Sequence[date], freeze: dict, universe: dict,
          audit_rows: list[dict], coverage: dict, reuse_matrix: list[dict],
          extra_files: Sequence[dict] = (), extra_documents: dict | None = None,
          extra_fields: dict | None = None) -> dict:
    folder = root / SNAPSHOT_DIR / snapshot_id
    if (folder / "snapshot.json").exists():
        raise FileExistsError(f"{snapshot_id} already exists; a change needs a new snapshot id")
    files = []
    for row in freeze["files"]:
        path = root / row["common_path"]
        found = sha256_file(path)
        if found != row["sha256"]:
            raise RuntimeError(f"{path} differs from {freeze['freeze_id']}")
        files.append({"path": row["common_path"], "kind": row["file_type"], "sha256": found,
                      "size": row["size"], "role": "COMMON_RAW", "status": row["status"],
                      "authority": row.get("authority"), "seed": freeze["freeze_id"]})
    minute_files, minute_ledgers = common_raw_members(root, MINUTE_DIR, "MINUTE_RAW")
    daily_files, daily_ledgers = common_raw_members(root, DAILY_DIR, "PER_SYMBOL_DAILY_RAW")
    files += minute_files + daily_files + list(extra_files)
    for kind, data_kind in (("minute", "LEGACY_MINUTE_STRICT"), ("per_symbol_daily", "LEGACY_DAILY")):
        for symbol, start, end, relative, rows in legacy_entries(root, kind):
            target = root / relative
            paths = sorted(target.glob("*.parquet")) if target.is_dir() else [target]
            for path in paths:
                files.append({"path": str(path.relative_to(root)), "kind": data_kind, "sha256": sha256_file(path),
                              "size": path.stat().st_size, "role": "LEGACY_REFERENCE",
                              "entry": f"{symbol} {start}..{end}",
                              "authority": "MASSIVE_TICKER_AGGREGATE"})
    files.sort(key=lambda f: f["path"])
    folder.mkdir(parents=True, exist_ok=False)
    audit_table = pa.Table.from_pylist(audit_rows)
    audit_path = folder / "minute_session_audit.parquet"
    pq.write_table(audit_table, audit_path, compression="zstd")
    documents = {"authority_contract.json": AUTHORITY_CONTRACT, "minute_universe.json": universe,
                 "coverage.json": coverage, "reuse_matrix.json": reuse_matrix,
                 "c_raw_freeze.json": freeze, **(extra_documents or {})}
    for name, payload in documents.items():
        (folder / name).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    with gzip.open(folder / "files.jsonl.gz", "wt", encoding="utf-8") as handle:
        for row in files:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    manifest_digest = hashlib.sha256(b"".join(
        f"{f['path']}\t{f['sha256']}\t{f['size']}\n".encode() for f in files)).hexdigest()
    minute_rows = sum(l.get("rows", 0) for l in minute_ledgers if l.get("status") == "COMPLETE") \
        + coverage["minute"]["legacy_rows_in_window"]
    snapshot = {
        "snapshot_id": snapshot_id, "provider": "massive", "plan": "Stocks Basic (rolling 2 years, T-1)",
        "created_at": _utc_now(), "start_date": sessions[0].isoformat(), "end_date": sessions[-1].isoformat(),
        "sessions": len(sessions), "price_adjustment": "adjusted=false",
        "daily_authorities": sorted(AUTHORITY_CONTRACT["authorities"]),
        "grouped_sessions": coverage["grouped_daily"]["existing"],
        "reference_snapshots": coverage["reference_tickers"]["existing"],
        "split_records": coverage["splits"]["records"],
        "minute_universe_id": universe["universe_id"],
        "minute_universe_symbols": len(universe["symbols"]),
        "minute_sessions": coverage["minute"]["symbol_sessions_with_bars"],
        "minute_rows": minute_rows,
        "files": len(files), "disk_bytes": sum(f["size"] for f in files),
        "member_roles": {r: sum(f["role"] == r for f in files) for r in ("COMMON_RAW", "LEGACY_REFERENCE")},
        "c_raw_freeze": {"freeze_id": freeze["freeze_id"], "freeze_digest": freeze["freeze_digest"],
                         "c_raw_digest": freeze["c_raw_digest"]},
        "manifest_digest": manifest_digest,
        "coverage_digest": hashlib.sha256(canonical(coverage)).hexdigest(),
        "session_audit_sha256": sha256_file(audit_path),
        "documents": {name: sha256_file(folder / name) for name in sorted(documents)},
        "files_list_sha256": sha256_file(folder / "files.jsonl.gz"),
        **(extra_fields or {}),
        "status": "FROZEN",
    }
    body = json.dumps(snapshot, indent=1, sort_keys=True) + "\n"
    descriptor = os.open(folder / "snapshot.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(body)
    pointer = root / POINTER
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({"snapshot_id": snapshot_id, "path": f"{SNAPSHOT_DIR}/{snapshot_id}/snapshot.json",
                                   "snapshot_sha256": hashlib.sha256(body.encode()).hexdigest(),
                                   "status": "FROZEN", "updated_at": _utc_now()}, indent=1) + "\n")
    return snapshot
