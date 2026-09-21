"""The E-D6 development input: E1's bound minute tape, exposed read-only through a file view.

E1 bound itself to 2,152 minute files over 1,902 symbols. The shared store has grown since then,
including new files inside development symbol folders, and the E1 loader reads every page in a
folder. Rather than editing that loader, E-D6 builds a directory of symlinks that exposes exactly
the bound files (and the legacy parquet E1 also read, each checked against the frozen USB-HIST-V1
manifest) and hands that directory to the unchanged loader as its workspace root. E1's own
``tape_digest`` over the view must reproduce the bound digest, before and after the run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import gzip
import hashlib
import json
from pathlib import Path

from app.backtest.strategy_e0_overnight import minute as M
from app.backtest.strategy_e1_premarket import cohort as C


REPO_ROOT = Path(__file__).resolve().parents[4]
MANIFEST_PATH = (REPO_ROOT
                 / "docs/backtest/strategy_e_candidate/strategy_e_d6_development_tape_v1.json")
SNAPSHOT_FILES = "market_data/metadata/historical_snapshot/USB-HIST-V1/files.jsonl.gz"
DEVELOPMENT_TAPE_DIGEST = "d12ff28a98cf9cc64cad86f595985fd5ae2308b555635501fa5729d4439222a4"
DEVELOPMENT_SYMBOLS = 1902
DEVELOPMENT_FILES = 2152


class DatasetIntegrityError(RuntimeError):
    """The development input cannot be shown to be the bound E1 tape."""


@dataclass(frozen=True)
class TapeManifest:
    digest: str
    files: tuple[tuple[str, str, int], ...]      # (symbol, file name, size), sorted

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted({symbol for symbol, _, _ in self.files}))


def manifest_digest(files: Sequence[tuple[str, str, int]]) -> str:
    """The ``cohort.tape_digest`` recipe applied to a file list instead of a directory."""
    digest = hashlib.sha256()
    for symbol, name, size in sorted(files):
        digest.update(f"{symbol}/{name}\t{size}\n".encode())
    return digest.hexdigest()


def load_manifest(path: Path = MANIFEST_PATH) -> TapeManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    files = tuple(sorted((row["symbol"], row["file"], int(row["size"]))
                         for row in payload["files"]))
    declared = payload["declaration"]["tape_digest"]
    found = manifest_digest(files)
    if declared != DEVELOPMENT_TAPE_DIGEST or found != DEVELOPMENT_TAPE_DIGEST:
        raise DatasetIntegrityError(
            f"development manifest digest {found} does not equal the bound {DEVELOPMENT_TAPE_DIGEST}")
    manifest = TapeManifest(found, files)
    if len(files) != DEVELOPMENT_FILES or len(manifest.symbols) != DEVELOPMENT_SYMBOLS:
        raise DatasetIntegrityError("development manifest size differs from the E1 binding")
    return manifest


def legacy_minute_hashes(workspace_root: Path) -> dict[str, tuple[str, int]]:
    """relative path -> (sha256, size) of every legacy minute parquet in USB-HIST-V1."""
    out: dict[str, tuple[str, int]] = {}
    with gzip.open(workspace_root / SNAPSHOT_FILES, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row["path"].startswith(M.LEGACY_DIR + "/"):
                out[row["path"]] = (row["sha256"], int(row["size"]))
    return out


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _link(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        if link.is_symlink() and Path(link.readlink()) == target:
            return
        raise DatasetIntegrityError(f"view path already holds something else: {link}")
    link.symlink_to(target)


def build_view(workspace_root: Path, manifest: TapeManifest, view_root: Path,
               legacy: Mapping[str, tuple[str, int]]) -> dict[str, object]:
    """Symlink exactly the bound pages and the manifest-verified legacy parquet into a view."""
    missing = []
    for symbol, name, size in manifest.files:
        source = workspace_root / M.RAW_DIR / symbol / name
        if not source.is_file():
            missing.append(f"{symbol}/{name}")
            continue
        if source.stat().st_size != size:
            raise DatasetIntegrityError(f"{symbol}/{name} changed size since the E1 binding")
        _link(source, view_root / M.RAW_DIR / symbol / name)
    if missing:
        raise DatasetIntegrityError(f"{len(missing)} bound files are gone, e.g. {missing[:3]}")

    wanted = set(manifest.symbols)
    legacy_used = []
    for relative, (sha, size) in sorted(legacy.items()):
        symbol = relative.split("/")[-2]
        if symbol not in wanted:
            continue
        source = workspace_root / relative
        if source.stat().st_size != size or _sha256(source) != sha:
            raise DatasetIntegrityError(f"legacy minute file differs from USB-HIST-V1: {relative}")
        _link(source, view_root / relative)
        legacy_used.append(relative)
    unlisted = sorted(p.relative_to(workspace_root).as_posix()
                      for symbol in wanted
                      for p in (workspace_root / M.LEGACY_DIR / symbol).glob("*.parquet")
                      if p.relative_to(workspace_root).as_posix() not in legacy)
    if unlisted:
        raise DatasetIntegrityError(f"legacy minute files outside USB-HIST-V1: {unlisted[:3]}")

    digest, files = C.tape_digest(view_root, manifest.symbols)
    if digest != manifest.digest or files != len(manifest.files):
        raise DatasetIntegrityError(f"view tape digest {digest} != bound {manifest.digest}")
    legacy_digest = hashlib.sha256("".join(
        f"{rel}\t{legacy[rel][0]}\n" for rel in legacy_used).encode()).hexdigest()
    return {"tape_digest": digest, "tape_files": files, "tape_symbols": len(manifest.symbols),
            "legacy_minute_files": len(legacy_used), "legacy_minute_digest": legacy_digest}


def verify_view(view_root: Path, manifest: TapeManifest) -> bool:
    """Recompute E1's digest over the view (the targets' current sizes) after the run."""
    digest, files = C.tape_digest(view_root, manifest.symbols)
    return digest == manifest.digest and files == len(manifest.files)
