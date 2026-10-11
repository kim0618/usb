"""Retention vs. the Q123 forward recorder's recovery contract (`RETENTION_ACTIVE_SESSION_FIX_V1.md`).

The recorder promises that without its checkpoint or cursor it can rebuild an active collector
session from that session's first segment. Retention must therefore never take the first segments
of the active session, however old the session is. Here the active session is aged past 8 and 30
days, pruned with `--apply`, and the recorder is run incrementally, without a checkpoint, and
with a corrupt cursor. The Q123 package lives on its own research branch; where it is absent
this module is skipped.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

pytest.importorskip("app.crypto.research.q123_forward_v1.recorder")
FX = pytest.importorskip("tests.crypto.q123_recorder_fixtures")
QR = pytest.importorskip("tests.crypto.test_q123_forward_recorder")

from app.crypto.context_collector_v1 import retention as R  # noqa: E402

MIN = 60_000
DAY = 86_400
APPROACHES = QR.APPROACH + [(FX.T0 + 120 * MIN, "RES", 8, FX.LEVEL)]
SEALED_S = (FX.T0 + 4 * 3_600_000) / 1000      # every fixture file sealed by then


def _lock(root: Path, session_id: str) -> None:
    (root / ".writer.lock").write_text(json.dumps(
        {"pid": 1, "session_id": session_id, "started_ms": 1, "lock_version": "ms-v0-lock.v1-2"}))


def _age_everything(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            os.utime(path, (SEALED_S, SEALED_S))


def build(root: Path, *, active_seconds: int):
    """Closed session `a`, then active session `b` still writing (`.open` tail, lock names it)."""
    FX.session(root, "a", FX.T0 + MIN - 30_000, 2400, walls=QR.ASK_WALL, close=True)
    b = FX.session(root, "b", FX.T0 + 50 * MIN, active_seconds, walls=QR.ASK_WALL)
    _lock(root, FX.sid("b"))
    return b


def grow(b, *, from_s: int, to_s: int) -> None:
    for k in range(from_s, to_s):
        b.sample(FX.T0 + 50 * MIN + k * 1000, FX.LEVEL * 0.999)
    b.flush()


def files_of(root: Path, s8: str) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob(f"*-{s8}-*") if p.is_file()}


def prune(root: Path, age_days: float) -> dict:
    _age_everything(root)
    report = R.prune(root, now_s=SEALED_S + age_days * DAY, apply=True)
    assert report["identity_failure"] is None and report["failed"] == []
    return report


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    """The recorder's store on the unpruned, fully grown journal, from scratch."""
    base = tmp_path_factory.mktemp("clean")
    b = build(base / "root", active_seconds=4000)
    grow(b, from_s=4000, to_s=5200)
    assert QR.drive(base / "root", base, approaches=APPROACHES) == 0
    return QR.store_lines(base / "store")


@pytest.mark.parametrize("age_days", [8.5, 30])
def test_retention_keeps_every_active_source_and_drops_the_closed_session(tmp_path, age_days):
    root = tmp_path / "root"
    build(root, active_seconds=5200)
    active = files_of(root, FX.sid("b")[:8])
    closed = files_of(root, FX.sid("a")[:8])
    assert any("-00001.jsonl" in name for name in active)
    report = prune(root, age_days)
    assert files_of(root, FX.sid("b")[:8]) == active                 # D: nothing active lost
    assert {item["path"] for item in report["delete"]} == closed     # closed + old: by policy
    assert report["kept"]["active_session"] == len([n for n in active if ".open" not in n])


@pytest.mark.parametrize("age_days", [8.5, 30])
def test_a_incremental_from_checkpoint_after_prune(tmp_path, clean, age_days):
    root = tmp_path / "root"
    b = build(root, active_seconds=4000)
    assert QR.drive(root, tmp_path, approaches=APPROACHES) == 0      # checkpoint written
    grow(b, from_s=4000, to_s=5200)
    prune(root, age_days)
    assert QR.drive(root, tmp_path, approaches=APPROACHES) == 0
    assert QR.store_lines(tmp_path / "store") == clean


@pytest.mark.parametrize("damage", ["delete_checkpoint", "corrupt_cursor"])
def test_b_c_rebuild_without_a_usable_checkpoint_after_prune(tmp_path, clean, damage):
    root = tmp_path / "root"
    b = build(root, active_seconds=4000)
    assert QR.drive(root, tmp_path, approaches=APPROACHES) == 0
    grow(b, from_s=4000, to_s=5200)
    prune(root, 30)
    if damage == "delete_checkpoint":
        shutil.rmtree(tmp_path / "rt")
    else:
        for path in (tmp_path / "rt" / "checkpoints").glob("*.json"):
            path.write_text(path.read_text()[:40] + "garbage")
    assert QR.drive(root, tmp_path, approaches=APPROACHES) == 0
    assert QR.store_lines(tmp_path / "store") == clean


def test_full_reconstruction_of_the_active_session_from_its_first_segment(tmp_path):
    """Store and runtime both lost after a 30-day prune: the active session is rebuilt whole,
    exactly as from the same session never pruned. Without its first segment it cannot be."""
    pruned = tmp_path / "pruned"
    build(pruned, active_seconds=5200)
    prune(pruned, 30)
    untouched = tmp_path / "untouched"
    build(untouched, active_seconds=5200)
    for path in untouched.rglob(f"*-{FX.sid('a')[:8]}-*"):
        path.unlink()                                  # the same closed loss, nothing else
    assert QR.drive(pruned, tmp_path / "p", approaches=APPROACHES) == 0
    assert QR.drive(untouched, tmp_path / "u", approaches=APPROACHES) == 0
    rebuilt = QR.store_lines(tmp_path / "p" / "store")
    assert rebuilt == QR.store_lines(tmp_path / "u" / "store")
    assert rebuilt["events.jsonl"], "the active session produced events to rebuild"

    # What ctx-retention.v1 would have done: drop the active session's first hour.
    for kind in ("context", "wall_r0"):
        for path in (pruned / kind).glob(f"{kind}-*-{FX.sid('b')[:8]}-00001.jsonl*"):
            path.unlink()
    assert QR.drive(pruned, tmp_path / "lost", approaches=APPROACHES) == 0
    assert QR.store_lines(tmp_path / "lost" / "store") != rebuilt
