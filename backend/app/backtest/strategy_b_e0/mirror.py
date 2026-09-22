"""Copy exactly the files B-E0 needs from the Drive store to a local mirror, and prove the copy.

B_ENGINE_DESIGN_V1 section 8 already decided that B replays from a local copy, not from the Drive
mount: a Drive cold read streams at a couple of hundred MB a minute and a replay touches every
member on every session. This module is that copy. It reads Drive and never writes it.

Three things make the mirror trustworthy rather than merely present:

* **Every page is checked against the hash its ledger recorded when it was fetched.** Grouped
  daily and splits are checked against the frozen C raw manifest. A mismatch stops the mirror;
  nothing is silently accepted.
* **Only what the run needs is copied**: for each universe member, the COMPLETE ledgers that
  overlap its fetch range, plus every grouped daily file and the splits dump. Members with no such
  ledger are reported, which the preflight turns into DATASET_NOT_READY.
* **The dataset digest is a function of content, not of when or where the copy ran**: sha256 over
  the sorted (relative path, file sha256) pairs. The same inputs give the same digest.

The mirror is resumable. A local file whose hash already matches is not copied again.
"""

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path

from app.backtest.historical_store.raw_fetch import MINUTE_DIR
from app.backtest.strategy_b_e0.universe import RunUniverse

MIRROR_VERSION = "b-e0-mirror-v1"
MANIFEST_NAME = "mirror_manifest.json"
CHUNK = 1 << 20


class MirrorFailed(RuntimeError):
    """A file could not be copied, or the copy does not hash to what its source recorded."""


@dataclass(frozen=True, slots=True)
class PlannedFile:
    path: str
    """Path relative to both the Drive root and the mirror root."""
    kind: str
    expected_sha256: str
    """What the source recorded: the ledger's page hash for a page, the freeze hash for grouped
    daily and splits, and for a ledger the hash of the bytes read at planning time, so a ledger
    rewritten on Drive after an earlier mirror is copied again rather than trusted stale."""


@dataclass(frozen=True, slots=True)
class MirrorPlan:
    files: tuple[PlannedFile, ...]
    members_without_ledgers: tuple[str, ...]
    ledgers_by_member: Mapping[str, tuple[str, ...]]
    ledger_schema_problems: tuple[str, ...] = ()


EXPECTED_FORMAT = "usb-common-raw-v1"


def ledger_problems(rel: str, ledger: Mapping) -> list[str]:
    """What would make a ledger's bars unusable as B's raw input. Prices must be unadjusted."""
    problems = []
    if ledger.get("adjusted") is not False:
        problems.append(f"{rel}: adjusted={ledger.get('adjusted')!r}, B requires raw (false)")
    if ledger.get("format") != EXPECTED_FORMAT:
        problems.append(f"{rel}: format {ledger.get('format')!r} != {EXPECTED_FORMAT}")
    if ledger.get("timespan") not in (None, "minute"):
        problems.append(f"{rel}: timespan {ledger.get('timespan')!r} is not minute")
    return problems


def _overlaps(ledger: Mapping, start: date, end: date) -> bool:
    if ledger.get("status") != "COMPLETE":
        return False
    return date.fromisoformat(ledger["start"]) <= end and date.fromisoformat(ledger["end"]) >= start


def plan(drive_root: Path, universe: RunUniverse, freeze: Mapping, *, workers: int = 8) -> MirrorPlan:
    """Which files to copy. Reads ledgers (small), never pages."""

    def member_files(symbol: str) -> tuple[str, list[PlannedFile], list[str], list[str]]:
        member = universe.members[symbol]
        folder = drive_root / MINUTE_DIR / symbol
        if not folder.is_dir():
            return symbol, [], [], []
        files: list[PlannedFile] = []
        ledgers: list[str] = []
        problems: list[str] = []
        for ledger_path in sorted(folder.glob("*.request.json")):
            raw = ledger_path.read_bytes()
            ledger = json.loads(raw)
            if not _overlaps(ledger, member.fetch_start, member.fetch_end):
                continue
            rel = f"{MINUTE_DIR}/{symbol}/{ledger_path.name}"
            problems.extend(ledger_problems(rel, ledger))
            ledgers.append(rel)
            files.append(PlannedFile(rel, "minute_ledger", hashlib.sha256(raw).hexdigest()))
            for page in ledger.get("pages") or ():
                files.append(PlannedFile(f"{MINUTE_DIR}/{symbol}/{page['file']}", "minute_page",
                                         page["file_sha256"]))
        return symbol, files, ledgers, problems

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(member_files, universe.symbols))

    files: list[PlannedFile] = []
    by_member: dict[str, tuple[str, ...]] = {}
    without: list[str] = []
    schema: list[str] = []
    for symbol, member_plan, ledgers, problems in results:
        if not ledgers:
            without.append(symbol)
        by_member[symbol] = tuple(ledgers)
        files.extend(member_plan)
        schema.extend(problems)
    kinds = {"GROUPED_DAILY": "grouped_daily", "SPLITS": "splits"}
    for entry in freeze["files"]:
        kind = kinds.get(entry["file_type"])
        if kind is not None:
            files.append(PlannedFile(entry["common_path"], kind, entry["sha256"]))
    return MirrorPlan(tuple(sorted(files, key=lambda f: f.path)), tuple(sorted(without)), by_member,
                      tuple(sorted(schema)))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _copy_one(drive_root: Path, mirror_root: Path, item: PlannedFile) -> dict:
    source, target = drive_root / item.path, mirror_root / item.path
    if target.is_file():
        existing = file_sha256(target)
        if existing == item.expected_sha256:
            return {"path": item.path, "kind": item.kind, "size": target.stat().st_size,
                    "sha256": existing, "expected_sha256": item.expected_sha256, "copied": False}
    if not source.is_file():
        raise MirrorFailed(f"{item.path} is missing at the source")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".partial")
    digest = hashlib.sha256()
    with source.open("rb") as read, partial.open("wb") as write:
        while block := read.read(CHUNK):
            digest.update(block)
            write.write(block)
    actual = digest.hexdigest()
    if actual != item.expected_sha256:
        partial.unlink(missing_ok=True)
        raise MirrorFailed(f"{item.path}: sha256 {actual} != recorded {item.expected_sha256}")
    partial.replace(target)
    return {"path": item.path, "kind": item.kind, "size": target.stat().st_size, "sha256": actual,
            "expected_sha256": item.expected_sha256, "copied": True}


def dataset_digest(entries: Sequence[Mapping]) -> str:
    """sha256 over sorted (path, sha256): content identity, independent of time and machine."""
    body = "\n".join(f"{e['path']}\t{e['sha256']}" for e in sorted(entries, key=lambda e: e["path"]))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def run(drive_root: Path, mirror_root: Path, mirror_plan: MirrorPlan, *, universe: RunUniverse,
        freeze_digest: str, workers: int = 8,
        progress: Callable[[int, int], None] | None = None) -> dict:
    """Copy and verify every planned file, then write the manifest. Raises on any mismatch."""
    entries: list[dict] = []
    failures: list[str] = []
    total = len(mirror_plan.files)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_copy_one, drive_root, mirror_root, item)
                   for item in mirror_plan.files]
        for done, future in enumerate(futures, start=1):
            try:
                entries.append(future.result())
            except MirrorFailed as error:
                failures.append(str(error))
            if progress is not None and (done % 500 == 0 or done == total):
                progress(done, total)
    if failures:
        raise MirrorFailed(f"{len(failures)} files failed: {failures[:5]}")

    entries.sort(key=lambda e: e["path"])
    manifest = {
        "mirror_version": MIRROR_VERSION,
        "universe_sha256": universe.sha256,
        "c_raw_freeze_digest": freeze_digest,
        "files": entries,
        "file_count": len(entries),
        "bytes": sum(e["size"] for e in entries),
        "copied_this_run": sum(1 for e in entries if e["copied"]),
        "members_without_ledgers": list(mirror_plan.members_without_ledgers),
        "ledger_schema_problems": list(mirror_plan.ledger_schema_problems),
        "ledgers_by_member": {k: list(v) for k, v in sorted(mirror_plan.ledgers_by_member.items())},
        "dataset_digest": dataset_digest(entries),
    }
    # the manifest itself is not part of the digest; "copied" is run history, not content
    mirror_root.mkdir(parents=True, exist_ok=True)
    (mirror_root / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                                             encoding="utf-8")
    return manifest


def load_manifest(mirror_root: Path) -> dict:
    path = mirror_root / MANIFEST_NAME
    if not path.is_file():
        raise MirrorFailed(f"no mirror manifest at {path}; run the mirror first")
    return json.loads(path.read_text(encoding="utf-8"))


def verify(mirror_root: Path, manifest: Mapping) -> list[str]:
    """Re-hash every mirrored file against the manifest. Local and cheap compared with Drive."""
    problems = []
    for entry in manifest["files"]:
        path = mirror_root / entry["path"]
        if not path.is_file():
            problems.append(f"{entry['path']}: missing")
        elif file_sha256(path) != entry["sha256"]:
            problems.append(f"{entry['path']}: sha256 changed")
    if dataset_digest(manifest["files"]) != manifest["dataset_digest"]:
        problems.append("dataset_digest does not match the manifest's own files")
    return problems


def compare_manifests(before: Mapping, after: Mapping) -> dict[str, list[str]]:
    """Which mirrored files a repair added, changed or removed. A repair may only add.

    ``changed`` or ``removed`` being non-empty means something other than new data moved, which is
    exactly what a Common Raw repair must never do.
    """
    old = {e["path"]: e["sha256"] for e in before["files"]}
    new = {e["path"]: e["sha256"] for e in after["files"]}
    return {"added": sorted(set(new) - set(old)),
            "removed": sorted(set(old) - set(new)),
            "changed": sorted(p for p in set(old) & set(new) if old[p] != new[p]),
            "unchanged": sorted(p for p in set(old) & set(new) if old[p] == new[p])}
