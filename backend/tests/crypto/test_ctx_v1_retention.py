"""Local retention of a context collector root (`RETENTION_CTX_V1_1.md`).

The deletion list is asserted by exclusion: every kind of file a collector root holds is built
here, aged on both clocks, and only the sealed persistent files that are old on both survive into
the list. A live store is pruned while it holds the writer lock and keeps writing.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from app.crypto.context_collector_v1 import retention as R
from app.crypto.context_collector_v1.__main__ import main as cli
from app.crypto.context_collector_v1.store import ContextStore
from app.crypto.market_structure_v0.envelope import Session

NOW_S = 1_791_600_000.0  # 2026-10-10T... UTC; a fixed clock so names and mtimes agree
DAY = 86_400
CURRENT = "c0ffee00"
OLD_SESSION = "0dd5e550"


def _date(seconds: float) -> str:
    return time.strftime("%Y%m%d", time.gmtime(seconds))


def make_root(tmp_path: Path, *, holder: str | None = CURRENT) -> Path:
    root = tmp_path / "ctx"
    (root / "context").mkdir(parents=True)
    payload = {"pid": 1, "session_id": f"{holder}-rest" if holder else None,
               "started_ms": 1, "lock_version": "ms-v0-lock.v1-2"}
    (root / ".writer.lock").write_text(json.dumps(payload) + "\n")
    return root


def put(root: Path, kind: str, name: str, *, age_days: float, name_age_days: float | None = None
        ) -> Path:
    """A file whose mtime is `age_days` old; its name's date defaults to the same age."""
    directory = root / kind
    directory.mkdir(parents=True, exist_ok=True)
    name_age = age_days if name_age_days is None else name_age_days
    path = directory / name.format(date=_date(NOW_S - name_age * DAY))
    path.write_bytes(b'{"x":1}\n')
    mtime = NOW_S - age_days * DAY
    os.utime(path, (mtime, mtime))
    return path


def listed(report: dict) -> set[str]:
    return {item["path"] for item in report["delete"]}


# --------------------------------------------------------------------------- what is deleted

def test_old_sealed_files_of_every_retained_kind_are_listed(tmp_path):
    root = make_root(tmp_path)
    expected = set()
    for kind in R.RETAINED_KINDS:
        suffix = ".jsonl.gz" if kind in ("context", "wall_v2", "wall_r0") else ".jsonl"
        session8 = OLD_SESSION if kind == "session" else CURRENT
        path = put(root, kind, f"{kind}-{{date}}-{session8}-00001{suffix}", age_days=9)
        expected.add(str(path.relative_to(root)))
    report = R.plan(root, now_s=NOW_S)
    assert listed(report) == expected
    assert report["days"] == R.RETENTION_DAYS == 7


def test_young_files_are_kept(tmp_path):
    root = make_root(tmp_path)
    put(root, "context", "context-{date}-" + CURRENT + "-00001.jsonl.gz", age_days=6.9)
    put(root, "telemetry", "telemetry-{date}-" + CURRENT + "-00001.jsonl", age_days=1)
    report = R.plan(root, now_s=NOW_S)
    assert report["delete"] == []
    assert report["kept"]["young"] == 2


def test_both_clocks_must_be_old(tmp_path):
    root = make_root(tmp_path)
    # Sealed long ago, but the name says it was opened recently: a clock disagrees, keep it.
    put(root, "context", "context-{date}-" + CURRENT + "-00001.jsonl.gz",
        age_days=30, name_age_days=1)
    # Named long ago, but written recently (touched, restored, clock jump): keep it.
    put(root, "context", "context-{date}-" + CURRENT + "-00002.jsonl.gz",
        age_days=1, name_age_days=30)
    assert R.plan(root, now_s=NOW_S)["delete"] == []


def test_current_session_header_is_kept_at_any_age(tmp_path):
    root = make_root(tmp_path)
    mine = put(root, "session", "session-{date}-" + CURRENT + "-00001.jsonl", age_days=40)
    theirs = put(root, "session", "session-{date}-" + OLD_SESSION + "-00001.jsonl", age_days=40)
    # Only the header is protected: the running session's old context hours still go.
    ctx = put(root, "context", "context-{date}-" + CURRENT + "-00001.jsonl.gz", age_days=40)
    report = R.plan(root, now_s=NOW_S)
    assert listed(report) == {str(theirs.relative_to(root)), str(ctx.relative_to(root))}
    assert mine.exists()
    assert report["protected_session8"] == CURRENT
    assert report["kept"]["current_session_header"] == 1


# --------------------------------------------------------------------------- what is never touched

def test_active_cache_lock_and_foreign_files_are_never_listed(tmp_path):
    root = make_root(tmp_path)
    old = 30
    put(root, "context", "context-{date}-" + CURRENT + "-00009.jsonl.open", age_days=old)
    put(root, "context", "context-{date}-" + CURRENT + "-00008.jsonl.gz.tmp", age_days=old)
    put(root, "context", "notes-{date}.jsonl", age_days=old)
    put(root, "context", "context-{date}-NOTHEX00-00001.jsonl.gz", age_days=old)
    # Volatile cache: a link to a directory full of old files, and a real directory moved aside.
    cache = tmp_path / "run" / "ctx-state-x"
    cache.mkdir(parents=True)
    (cache / "collector_state.json").write_text("{}")
    (root / "state").symlink_to(cache, target_is_directory=True)
    aside = root / "state.disk-1"
    aside.mkdir()
    (aside / "collector_state.json").write_text("{}")
    # A symbolic link that looks exactly like a sealed file: never followed, never removed.
    target = tmp_path / "elsewhere.jsonl.gz"
    target.write_bytes(b"x")
    os.utime(target, (NOW_S - old * DAY,) * 2)
    link = root / "context" / f"context-{_date(NOW_S - old * DAY)}-{CURRENT}-00003.jsonl.gz"
    link.symlink_to(target)
    # A kind this collector does not persist (research material) is not looked at.
    put(root, "raw_depth", "raw_depth-{date}-" + CURRENT + "-00001.jsonl", age_days=old)
    os.utime(root / ".writer.lock", (NOW_S - old * DAY,) * 2)

    report = R.prune(root, now_s=NOW_S, apply=True)
    assert report["delete"] == [] and report["deleted_files"] == 0
    assert report["kept"]["not_sealed"] == 2
    assert report["kept"]["unrecognised_name"] == 2
    assert report["kept"]["not_regular_file"] == 1
    for survivor in (root / ".writer.lock", cache / "collector_state.json",
                     aside / "collector_state.json", target, link,
                     root / "raw_depth"):
        assert survivor.exists() or survivor.is_symlink()


def test_dry_run_is_the_default_and_deletes_nothing(tmp_path, capsys):
    root = make_root(tmp_path)
    path = put(root, "wall_r0", "wall_r0-{date}-" + CURRENT + "-00001.jsonl.gz", age_days=10)
    report = R.prune(root, now_s=NOW_S)
    assert report["applied"] is False and report["delete_files"] == 1
    assert path.exists()
    assert cli(["prune", "--root", str(root)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is False and out["deleted_files"] == 0
    assert path.exists()


def test_apply_deletes_exactly_the_listed_files(tmp_path):
    root = make_root(tmp_path)
    old = put(root, "wall_v2", "wall_v2-{date}-" + CURRENT + "-00001.jsonl.gz", age_days=10)
    young = put(root, "wall_v2", "wall_v2-{date}-" + CURRENT + "-00002.jsonl.gz", age_days=2)
    report = R.prune(root, now_s=NOW_S, apply=True)
    assert report["deleted_files"] == 1 and report["failed"] == []
    assert not old.exists() and young.exists()


# --------------------------------------------------------------------------- refusal

@pytest.mark.parametrize("damage", ["no_lock", "no_context", "missing"])
def test_a_root_that_is_not_a_context_root_is_refused(tmp_path, damage, capsys):
    root = make_root(tmp_path)
    old = put(root, "telemetry", "telemetry-{date}-" + CURRENT + "-00001.jsonl", age_days=30)
    if damage == "no_lock":
        (root / ".writer.lock").unlink()
    elif damage == "no_context":
        (root / "context").rmdir()
    else:
        root = tmp_path / "nowhere"
    with pytest.raises(R.RootRefused):
        R.prune(root, now_s=NOW_S, apply=True)
    assert cli(["prune", "--root", str(root), "--apply"]) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert old.exists()


def test_retention_below_one_day_is_refused(tmp_path):
    root = make_root(tmp_path)
    with pytest.raises(ValueError):
        R.plan(root, days=0, now_s=NOW_S)


def test_unreadable_lock_protects_nothing_extra_but_still_deletes_only_old(tmp_path):
    root = make_root(tmp_path)
    (root / ".writer.lock").write_text("12345\n")  # pre-V1.2 bare pid
    old = put(root, "session", "session-{date}-" + OLD_SESSION + "-00001.jsonl", age_days=30)
    young = put(root, "session", "session-{date}-" + CURRENT + "-00001.jsonl", age_days=1)
    report = R.plan(root, now_s=NOW_S)
    assert report["protected_session8"] is None
    assert listed(report) == {str(old.relative_to(root))}
    assert young.exists()


# --------------------------------------------------------------------------- live writer

def test_prune_beside_a_live_writer_leaves_it_writing(tmp_path):
    root = tmp_path / "ctx"
    session = Session()
    store = ContextStore.open(root, session.session_id, started_ns=0)
    try:
        record = session.record("context", {"n": 1}, receive_ms=int(NOW_S * 1000), mono_ns=1)
        store.write(record)
        store.write(session.record("session", {"event": "START"}, receive_ms=int(NOW_S * 1000),
                                   mono_ns=2))
        store.tick()
        old = put(root, "context", "context-{date}-" + OLD_SESSION + "-00001.jsonl.gz",
                  age_days=30)
        # Age everything the live writer has, on both clocks, as if it had run for a month.
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                os.utime(path, (NOW_S - 30 * DAY,) * 2)
        report = R.prune(root, now_s=NOW_S, apply=True)
        assert report["protected_session8"] == session.session_id[:8]
        assert listed(report) == {str(old.relative_to(root))}
        assert not old.exists()
        assert store.holds_authority()
        store.write(session.record("context", {"n": 2}, receive_ms=int(NOW_S * 1000) + 1000,
                                   mono_ns=3))
        store.tick()
    finally:
        store.close()
    assert list((root / "context").glob(f"*-{session.session_id[:8]}-*"))
