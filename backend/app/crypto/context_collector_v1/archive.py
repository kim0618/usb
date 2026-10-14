"""COLD archive of a context collector root: manifests, receipts, disk health and the local pull.

`RETENTION_CTX_V1_2.md` fixes the contract; this module is the archive half of it and
`retention.py` is the prune half. Standard library only, and nothing imported from the rest of the
package, so the same file runs on the server (manifests, receipt checks) and on the operator's PC
(the pull), with any Python 3.10+.

**The flow.** Nothing is copied on the server. The sealed files are immutable and the own kinds
are already gzip, so the archive is a *description* of journal files, not a second copy of them:

1. `retention retain` writes `manifests/<id>.json`: the files that left HOT, their size and
   sha256. A manifest is immutable; files that leave HOT later go into a new part.
2. The operator's PC runs `pull`: it fetches the manifests, pulls the listed files straight from
   the journal with rsync, hashes them locally and writes a receipt. The receipt is uploaded to
   `receipts/<id>.json`.
3. `retention retain --apply` (with pruning enabled) deletes a file only when a valid receipt
   lists it with the sha256 the manifest has and the file on disk still has.

The server holds no credential for the external store; the only transfer is an ssh pull that the
operator's PC starts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MANIFEST_VERSION = "ctx-archive-manifest.v1"
RECEIPT_VERSION = "ctx-archive-receipt.v1"
MANIFEST_DIRNAME = "manifests"
RECEIPT_DIRNAME = "receipts"

RESULT_OK = "OK"
RESULT_PARTIAL = "PARTIAL"
RESULT_FAILED = "FAILED"

#: Disk health of the filesystem holding the journal. Display only: no threshold ever loosens a
#: prune gate or deletes anything (`RETENTION_CTX_V1_2.md` section 9).
DISK_WARNING_BYTES = 1_000_000_000
DISK_CRITICAL_BYTES = 500_000_000
DISK_OK = "OK"
DISK_WARNING = "WARNING"
DISK_CRITICAL = "CRITICAL"
DISK_UNKNOWN = "UNKNOWN"

_CHUNK = 1 << 20


# --------------------------------------------------------------------------- primitives

def canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(_CHUNK)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def utc_iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seal(body: dict[str, Any], field: str) -> dict[str, Any]:
    """`body` with `field` set to the sha256 of the canonical body without it."""
    out = {k: v for k, v in body.items() if k != field}
    out[field] = hashlib.sha256(canonical(out).encode()).hexdigest()
    return out


def sealed_ok(body: Any, field: str) -> bool:
    if not isinstance(body, dict) or not isinstance(body.get(field), str):
        return False
    rest = {k: v for k, v in body.items() if k != field}
    return hashlib.sha256(canonical(rest).encode()).hexdigest() == body[field]


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def aggregate_digest(entries: Iterable[dict[str, Any]]) -> str:
    """One digest over `sha256  path` lines in path order: what a receipt attests to."""
    lines = sorted(f'{e["sha256"]}  {e["path"]}' for e in entries)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


# --------------------------------------------------------------------------- disk health

def disk_health(path: Path | str, *, usage: Any = None) -> dict[str, Any]:
    """Free space of the filesystem holding `path`, classified. Never raises."""
    try:
        reading = usage if usage is not None else shutil.disk_usage(path)
    except OSError as exc:
        return {"state": DISK_UNKNOWN, "path": str(path), "error": f"{type(exc).__name__}: {exc}",
                "warning_below_bytes": DISK_WARNING_BYTES,
                "critical_below_bytes": DISK_CRITICAL_BYTES}
    free = int(reading.free)
    state = (DISK_CRITICAL if free < DISK_CRITICAL_BYTES
             else DISK_WARNING if free < DISK_WARNING_BYTES else DISK_OK)
    return {"state": state, "path": str(path), "free_bytes": free,
            "total_bytes": int(reading.total), "used_bytes": int(reading.used),
            "warning_below_bytes": DISK_WARNING_BYTES, "critical_below_bytes": DISK_CRITICAL_BYTES,
            "display_only": True}


# --------------------------------------------------------------------------- manifests

def manifest_dir(archive_dir: Path) -> Path:
    return Path(archive_dir) / MANIFEST_DIRNAME


def receipt_dir(archive_dir: Path) -> Path:
    return Path(archive_dir) / RECEIPT_DIRNAME


def load_manifests(archive_dir: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """`({manifest_id: manifest}, invalid)`. A manifest whose seal does not verify is not used."""
    found: dict[str, dict[str, Any]] = {}
    invalid: list[dict[str, str]] = []
    directory = manifest_dir(archive_dir)
    if not directory.is_dir():
        return found, invalid
    for path in sorted(directory.glob("*.json")):
        try:
            body = json.loads(path.read_bytes())
        except (OSError, ValueError):
            invalid.append({"file": path.name, "reason": "UNREADABLE"})
            continue
        if not sealed_ok(body, "manifest_sha256") or body.get("version") != MANIFEST_VERSION \
                or body.get("manifest_id") != path.stem:
            invalid.append({"file": path.name, "reason": "SEAL_OR_VERSION"})
            continue
        found[body["manifest_id"]] = body
    return found, invalid


def manifest_index(manifests: dict[str, dict[str, Any]]) -> dict[str, tuple[str, dict[str, Any]]]:
    """`{journal relative path: (manifest_id, entry)}`. The first manifest naming a path wins."""
    index: dict[str, tuple[str, dict[str, Any]]] = {}
    for manifest_id in sorted(manifests):
        for entry in manifests[manifest_id]["files"]:
            index.setdefault(entry["path"], (manifest_id, entry))
    return index


def next_part(manifests: dict[str, dict[str, Any]], session8: str, utc_date: str) -> int:
    prefix = f"{session8}-{utc_date}-"
    parts = [int(m[len(prefix):]) for m in manifests if m.startswith(prefix)
             and m[len(prefix):].isdigit()]
    return (max(parts) + 1) if parts else 1


def build_manifest(*, session8: str, utc_date: str, part: int, files: list[dict[str, Any]],
                   session: dict[str, Any], q123: dict[str, Any], root: Path,
                   retention_version: str, now_s: float) -> dict[str, Any]:
    entries = sorted(files, key=lambda e: e["path"])
    body = {
        "version": MANIFEST_VERSION,
        "manifest_id": f"{session8}-{utc_date}-{part:03d}",
        "session8": session8,
        "session_id": session.get("session_id"),
        "session_state": session.get("state"),
        "journal_schema": session.get("journal_schema"),
        "utc_date": utc_date,
        "part": part,
        "root": str(root),
        "retention_version": retention_version,
        "created_at": utc_iso(now_s),
        "q123": q123,
        "files": [{"path": e["path"], "kind": e["kind"], "segment": e["segment"],
                   "bytes": e["bytes"], "sha256": e["sha256"], "sealed_utc": e["sealed_utc"]}
                  for e in entries],
        "file_count": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries),
        "aggregate_digest": aggregate_digest(entries),
    }
    return seal(body, "manifest_sha256")


def write_manifest(archive_dir: Path, manifest: dict[str, Any]) -> Path:
    """Write once. An existing manifest id is never overwritten."""
    path = manifest_dir(archive_dir) / f'{manifest["manifest_id"]}.json'
    if path.exists():
        raise FileExistsError(f"{path} exists; manifests are immutable")
    write_atomic(path, (canonical(manifest) + "\n").encode())
    return path


# --------------------------------------------------------------------------- receipts

def build_receipt(manifest: dict[str, Any], *, verified: list[dict[str, Any]],
                  failed: list[dict[str, Any]], destination: str, pulled_at_s: float,
                  verified_at_s: float) -> dict[str, Any]:
    result = (RESULT_OK if not failed and len(verified) == manifest["file_count"]
              else RESULT_PARTIAL if verified else RESULT_FAILED)
    body = {
        "version": RECEIPT_VERSION,
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "pulled_at": utc_iso(pulled_at_s),
        "verified_at": utc_iso(verified_at_s),
        "destination": destination,
        "result": result,
        "file_count": len(verified),
        "byte_count": sum(e["bytes"] for e in verified),
        "verified": sorted(verified, key=lambda e: e["path"]),
        "failed": sorted(failed, key=lambda e: e["path"]),
        "aggregate_digest": aggregate_digest(verified),
    }
    return seal(body, "receipt_sha256")


def check_receipt(receipt: Any, manifest: dict[str, Any] | None) -> tuple[dict[str, str], str | None]:
    """`({path: sha256 verified externally}, None)`, or `({}, reason)` when it cannot be used."""
    if manifest is None:
        return {}, "RECEIPT_WITHOUT_MANIFEST"
    if not sealed_ok(receipt, "receipt_sha256") or receipt.get("version") != RECEIPT_VERSION:
        return {}, "RECEIPT_SEAL_OR_VERSION"
    if receipt.get("manifest_id") != manifest["manifest_id"] or \
            receipt.get("manifest_sha256") != manifest["manifest_sha256"]:
        return {}, "RECEIPT_MANIFEST_MISMATCH"
    verified = receipt.get("verified")
    if not isinstance(verified, list) or receipt.get("result") not in (RESULT_OK, RESULT_PARTIAL):
        return {}, "RECEIPT_NOT_VERIFIED"
    if aggregate_digest(verified) != receipt.get("aggregate_digest") or \
            receipt.get("file_count") != len(verified) or \
            receipt.get("byte_count") != sum(int(e.get("bytes", -1)) for e in verified):
        return {}, "RECEIPT_DIGEST_MISMATCH"
    listed = {e["path"]: e for e in manifest["files"]}
    out: dict[str, str] = {}
    for entry in verified:
        want = listed.get(entry.get("path"))
        if want is None or want["sha256"] != entry.get("sha256") or \
                want["bytes"] != entry.get("bytes"):
            return {}, "RECEIPT_FILE_MISMATCH"
        out[entry["path"]] = entry["sha256"]
    return out, None


def load_receipts(archive_dir: Path, manifests: dict[str, dict[str, Any]]
                  ) -> tuple[dict[str, str], dict[str, str]]:
    """`({path: externally verified sha256}, {receipt file: reason not used})`."""
    verified: dict[str, str] = {}
    rejected: dict[str, str] = {}
    directory = receipt_dir(archive_dir)
    if not directory.is_dir():
        return verified, rejected
    for path in sorted(directory.glob("*.json")):
        try:
            body = json.loads(path.read_bytes())
        except (OSError, ValueError):
            rejected[path.name] = "RECEIPT_UNREADABLE"
            continue
        files, reason = check_receipt(body, manifests.get(path.stem))
        if reason is not None:
            rejected[path.name] = reason
            continue
        verified.update(files)
    return verified, rejected


# --------------------------------------------------------------------------- local pull

def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=False, capture_output=True, text=True)


def verify_pulled(manifest: dict[str, Any], journal_mirror: Path
                  ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    verified, failed = [], []
    for entry in manifest["files"]:
        local = journal_mirror / entry["path"]
        if not local.is_file():
            failed.append({"path": entry["path"], "reason": "NOT_PULLED"})
            continue
        size = local.stat().st_size
        if size != entry["bytes"]:
            failed.append({"path": entry["path"], "reason": "SIZE_MISMATCH"})
            continue
        digest = sha256_file(local)
        if digest != entry["sha256"]:
            failed.append({"path": entry["path"], "reason": "SHA256_MISMATCH"})
            continue
        verified.append({"path": entry["path"], "sha256": digest, "bytes": size})
    return verified, failed


def pull(*, host: str, remote_root: str, remote_archive: str, dest: Path,
         rsync: str = "rsync", upload: bool = True) -> dict[str, Any]:
    """Fetch manifests, pull their files, verify, write and upload receipts. Idempotent."""
    dest = Path(dest).expanduser()
    mirror = dest / "journal"
    local_manifests = dest / MANIFEST_DIRNAME
    local_receipts = dest / RECEIPT_DIRNAME
    for directory in (mirror, local_manifests, local_receipts):
        directory.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"event": "PULL", "host": host, "dest": str(dest), "manifests": [],
                              "errors": []}
    got = _run([rsync, "-a", f"{host}:{remote_archive.rstrip('/')}/{MANIFEST_DIRNAME}/",
                f"{local_manifests}/"])
    if got.returncode != 0:
        report["errors"].append({"step": "fetch_manifests", "rc": got.returncode,
                                 "stderr": got.stderr.strip()[-400:]})
        return report
    manifests, invalid = load_manifests(dest)
    report["invalid_manifests"] = invalid
    for manifest_id in sorted(manifests):
        manifest = manifests[manifest_id]
        receipt_path = local_receipts / f"{manifest_id}.json"
        entry: dict[str, Any] = {"manifest_id": manifest_id, "files": manifest["file_count"],
                                 "bytes": manifest["total_bytes"]}
        existing = None
        if receipt_path.exists():
            try:
                existing = json.loads(receipt_path.read_bytes())
            except (OSError, ValueError):
                existing = None
        if existing is not None and check_receipt(existing, manifest)[1] is None and \
                existing.get("result") == RESULT_OK:
            entry["result"] = "ALREADY_VERIFIED"
        else:
            pulled_at = time.time()
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".list") as listing:
                listing.write("".join(f'{f["path"]}\n' for f in manifest["files"]))
                list_path = listing.name
            try:
                got = _run([rsync, "-a", f"--files-from={list_path}",
                            f"{host}:{remote_root.rstrip('/')}/", f"{mirror}/"])
            finally:
                os.unlink(list_path)
            if got.returncode not in (0, 23, 24):          # 23/24: some files vanished
                entry["rsync_rc"] = got.returncode
                entry["rsync_stderr"] = got.stderr.strip()[-400:]
            verified, failed = verify_pulled(manifest, mirror)
            receipt = build_receipt(manifest, verified=verified, failed=failed,
                                    destination=str(mirror), pulled_at_s=pulled_at,
                                    verified_at_s=time.time())
            write_atomic(receipt_path, (canonical(receipt) + "\n").encode())
            entry["result"] = receipt["result"]
            entry["verified_files"] = receipt["file_count"]
            entry["failed"] = receipt["failed"][:5]
        if upload and entry.get("result") in (RESULT_OK, RESULT_PARTIAL, "ALREADY_VERIFIED"):
            sent = _run([rsync, "-a", str(receipt_path),
                         f"{host}:{remote_archive.rstrip('/')}/{RECEIPT_DIRNAME}/"])
            entry["uploaded"] = sent.returncode == 0
            if sent.returncode != 0:
                entry["upload_stderr"] = sent.stderr.strip()[-400:]
        report["manifests"].append(entry)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.crypto.context_collector_v1.archive",
                                     description="COLD archive tools (RETENTION_CTX_V1_2).")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pull", help="run on the operator's PC: pull, verify, upload receipts")
    p.add_argument("--host", required=True, help="ssh host alias of the server")
    p.add_argument("--remote-root", required=True, help="journal root on the server")
    p.add_argument("--remote-archive", required=True, help="archive dir on the server")
    p.add_argument("--dest", required=True, help="local COLD directory")
    p.add_argument("--no-upload", action="store_true", help="verify only; do not send receipts")
    d = sub.add_parser("disk", help="print the disk health of a path")
    d.add_argument("path")
    args = parser.parse_args(argv)
    if args.command == "disk":
        print(json.dumps(disk_health(args.path), sort_keys=True))
        return 0
    report = pull(host=args.host, remote_root=args.remote_root,
                  remote_archive=args.remote_archive, dest=Path(args.dest),
                  upload=not args.no_upload)
    print(json.dumps(report, sort_keys=True, indent=1))
    bad = report["errors"] or any(m.get("result") not in (RESULT_OK, "ALREADY_VERIFIED")
                                  or m.get("uploaded") is False for m in report["manifests"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["MANIFEST_VERSION", "RECEIPT_VERSION", "DISK_WARNING_BYTES", "DISK_CRITICAL_BYTES",
           "canonical", "sha256_file", "seal", "sealed_ok", "aggregate_digest", "disk_health",
           "load_manifests", "manifest_index", "next_part", "build_manifest", "write_manifest",
           "build_receipt", "check_receipt", "load_receipts", "verify_pulled", "pull", "main"]
