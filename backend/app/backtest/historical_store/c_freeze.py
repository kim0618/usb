"""STRATEGY_C_RAW_FREEZE_V1 and its byte-identical promotion into the Common Raw store.

The C raw cache (``data/runtime/strategy_c/raw``) is the seed of grouped daily, CS reference
snapshots and market-wide splits. The freeze lists every file with its sha256; the promotion
copies bytes only (no move, no re-gzip, no JSON re-serialization) and checks local == Drive.

C grouped files are *wrapped* payloads (``{"format", "session", "body"}``, written with
``sort_keys``), not the provider's response bytes. They are kept exactly as they are, and the
payload form is recorded so a reader never assumes provider bytes.
"""

from collections.abc import Callable
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any

FREEZE_ID = "STRATEGY_C_RAW_FREEZE_V1"
SEEDED_FROM = "data/runtime/strategy_c/raw (app.dev.fetch_strategy_c_selection_raw, strategy-c-selection-raw-v1)"
COMMON_DIR = {"grouped": "market_data/raw/massive/grouped_daily",
              "tickers": "market_data/raw/massive/reference_tickers",
              "splits": "market_data/raw/massive/splits"}
FILE_TYPE = {"grouped": "GROUPED_DAILY", "tickers": "REFERENCE_TICKERS_CS", "splits": "SPLITS"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def common_relative(kind: str, name: str) -> str:
    if kind == "grouped":
        return f"{COMMON_DIR[kind]}/{name[:4]}/{name}"
    return f"{COMMON_DIR[kind]}/{name}"


def _record(raw_root: Path, path: Path) -> dict[str, Any]:
    kind = path.parent.name
    payload = json.loads(gzip.decompress(path.read_bytes()))
    row: dict[str, Any] = {"relative_path": str(path.relative_to(raw_root)), "file_type": FILE_TYPE[kind],
                           "size": path.stat().st_size, "sha256": sha256_file(path), "source": "massive",
                           "seeded_from": SEEDED_FROM, "payload_form": "wrapped-json-sort-keys",
                           "format": payload.get("format"), "common_path": common_relative(kind, path.name)}
    if kind == "grouped":
        body = payload.get("body")
        row.update(session_date=payload["session"],
                   adjusted=None if body is None else body.get("adjusted"),
                   rows=None if body is None else len(body.get("results") or ()),
                   authority="MASSIVE_GROUPED_DAILY",
                   status="OK" if body is not None else f"NOT_AVAILABLE:{payload.get('error')}")
    elif kind == "tickers":
        row.update(as_of=payload["as_of"], adjusted=None, rows=len(payload["results"]),
                   pages=payload["pages"], status="OK")
    else:
        row.update(as_of=f"{payload['start']}..{payload['end']}", adjusted=None,
                   rows=len(payload["results"]), pages=payload["pages"], status="OK")
    return row


def freeze_digest(files: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in sorted(files, key=lambda r: r["relative_path"]):
        digest.update(f"{row['relative_path']}\t{row['sha256']}\t{row['size']}\n".encode())
    return digest.hexdigest()


def build_freeze(raw_root: Path, *, c_raw_digest: str) -> dict[str, Any]:
    paths = sorted(p for kind in COMMON_DIR for p in (raw_root / kind).glob("*") if p.is_file())
    stray = sorted(str(p.relative_to(raw_root)) for p in raw_root.rglob("*")
                   if p.is_file() and p.parent.name not in COMMON_DIR)
    if stray or any(p.name.endswith(".partial") for p in paths):
        raise RuntimeError(f"unexpected files in C raw: {stray}")
    files = [_record(raw_root, p) for p in paths]
    usable = sorted(r["session_date"] for r in files if r["file_type"] == "GROUPED_DAILY" and r["status"] == "OK")
    return {"freeze_id": FREEZE_ID, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "raw_root": "data/runtime/strategy_c/raw", "provider": "massive", "price_adjustment": "adjusted=false",
            "file_count": len(files), "total_bytes": sum(r["size"] for r in files),
            "grouped_files": sum(r["file_type"] == "GROUPED_DAILY" for r in files),
            "grouped_usable_sessions": len(usable), "usable_range": [usable[0], usable[-1]],
            "not_available": [r["relative_path"] for r in files if r["status"] != "OK"],
            "c_raw_digest": c_raw_digest, "freeze_digest": freeze_digest(files), "files": files}


def _copy_verified(source: Path, target: Path, expected: str) -> str:
    """Byte copy through a ``.partial`` name; an existing target must already match."""
    if target.exists():
        found = sha256_file(target)
        return "present" if found == expected else f"CONFLICT {found}"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    shutil.copyfile(source, partial)
    os.replace(partial, target)
    found = sha256_file(target)
    return "copied" if found == expected else f"MISMATCH {found}"


def promote(freeze: dict[str, Any], raw_root: Path, workspace_root: Path,
            log: Callable[[str], None] = print) -> dict[str, Any]:
    outcomes: dict[str, int] = {}
    bad = []
    for row in freeze["files"]:
        result = _copy_verified(raw_root / row["relative_path"], workspace_root / row["common_path"], row["sha256"])
        key = result.split()[0]
        outcomes[key] = outcomes.get(key, 0) + 1
        if key not in {"copied", "present"}:
            bad.append((row["relative_path"], result))
    log(f"promotion {outcomes} bad={len(bad)}")
    return {"outcomes": outcomes, "failures": bad, "verified": not bad}
