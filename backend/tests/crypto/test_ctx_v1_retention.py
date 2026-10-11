"""Local retention of a context collector root (`RETENTION_CTX_V1_1.md`, amended by
`RETENTION_ACTIVE_SESSION_FIX_V1.md`: the active session's rebuild sources are never pruned).

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
        path = put(root, kind, f"{kind}-{{date}}-{OLD_SESSION}-00001{suffix}", age_days=9)
        expected.add(str(path.relative_to(root)))
    report = R.plan(root, now_s=NOW_S)
    assert listed(report) == expected
    assert report["days"] == R.RETENTION_DAYS == 7


def test_young_files_are_kept(tmp_path):
    root = make_root(tmp_path)
    put(root, "context", "context-{date}-" + OLD_SESSION + "-00001.jsonl.gz", age_days=6.9)
    put(root, "telemetry", "telemetry-{date}-" + CURRENT + "-00001.jsonl", age_days=1)
    report = R.plan(root, now_s=NOW_S)
    assert report["delete"] == []
    assert report["kept"]["young"] == 2


def test_both_clocks_must_be_old(tmp_path):
    root = make_root(tmp_path)
    # Sealed long ago, but the name says it was opened recently: a clock disagrees, keep it.
    put(root, "context", "context-{date}-" + OLD_SESSION + "-00001.jsonl.gz",
        age_days=30, name_age_days=1)
    # Named long ago, but written recently (touched, restored, clock jump): keep it.
    put(root, "context", "context-{date}-" + OLD_SESSION + "-00002.jsonl.gz",
        age_days=1, name_age_days=30)
    assert R.plan(root, now_s=NOW_S)["delete"] == []


def test_active_session_rebuild_sources_are_kept_at_any_age(tmp_path):
    root = make_root(tmp_path)
    mine = {put(root, kind, f"{kind}-{{date}}-{CURRENT}-00001{suffix}", age_days=40)
            for kind, suffix in (("session", ".jsonl"), ("context", ".jsonl.gz"),
                                 ("wall_v2", ".jsonl.gz"), ("wall_r0", ".jsonl.gz"))}
    theirs = put(root, "session", "session-{date}-" + OLD_SESSION + "-00001.jsonl", age_days=40)
    # Operating measurements are not rebuilt from: the running session's still age out.
    telemetry = put(root, "telemetry", "telemetry-{date}-" + CURRENT + "-00001.jsonl",
                    age_days=40)
    report = R.plan(root, now_s=NOW_S)
    assert listed(report) == {str(theirs.relative_to(root)), str(telemetry.relative_to(root))}
    assert report["protected_session8"] == CURRENT and report["identity_failure"] is None
    assert report["kept"]["active_session"] == 4
    assert all(path.exists() for path in mine)


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
    path = put(root, "wall_r0", "wall_r0-{date}-" + OLD_SESSION + "-00001.jsonl.gz",
               age_days=10)
    report = R.prune(root, now_s=NOW_S)
    assert report["applied"] is False and report["delete_files"] == 1
    assert path.exists()
    assert cli(["prune", "--root", str(root)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is False and out["deleted_files"] == 0
    assert path.exists()


def test_apply_deletes_exactly_the_listed_files(tmp_path):
    root = make_root(tmp_path)
    old = put(root, "wall_v2", "wall_v2-{date}-" + OLD_SESSION + "-00001.jsonl.gz", age_days=10)
    young = put(root, "wall_v2", "wall_v2-{date}-" + OLD_SESSION + "-00002.jsonl.gz", age_days=2)
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


def test_unreadable_lock_fails_closed_and_deletes_nothing(tmp_path):
    root = make_root(tmp_path)
    (root / ".writer.lock").write_text("12345\n")  # pre-V1.2 bare pid
    old = put(root, "session", "session-{date}-" + OLD_SESSION + "-00001.jsonl", age_days=30)
    young = put(root, "session", "session-{date}-" + CURRENT + "-00001.jsonl", age_days=1)
    report = R.prune(root, now_s=NOW_S, apply=True)
    assert report["protected_session8"] is None
    assert report["identity_failure"] == "LOCK_NAMES_NO_SESSION"
    assert report["delete"] == [] and report["deleted_files"] == 0
    assert report["kept"]["identity_unknown"] == 1
    assert old.exists() and young.exists()


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


# --------------------------------------------------------------------------- active session
# RETENTION_ACTIVE_SESSION_FIX_V1.md section 7: the required matrix.

def _state(root: Path, session_id: str) -> None:
    (root / "state").mkdir(exist_ok=True)
    (root / "state" / "collector_state.json").write_text(
        json.dumps({"written_ms": 1, "session": {"session_id": session_id},
                    "collector": {"pid": 1}}))


def _active_hours(root: Path, *, first_age_days: float, hours: int = 3) -> set[Path]:
    """The running session's first hours of every rebuild kind, opened `first_age_days` ago."""
    made = set()
    for n in range(1, hours + 1):
        age = first_age_days - (n - 1) / 24
        made.add(put(root, "session", f"session-{{date}}-{CURRENT}-{n:05d}.jsonl", age_days=age))
        for kind in ("context", "wall_v2", "wall_r0"):
            made.add(put(root, kind, f"{kind}-{{date}}-{CURRENT}-{n:05d}.jsonl.gz",
                         age_days=age))
    return made


@pytest.mark.parametrize("age_days", [8, 30])
def test_active_session_at_8_and_30_days_deletes_nothing(tmp_path, age_days):
    root = make_root(tmp_path)
    _state(root, f"{CURRENT}-rest")
    mine = _active_hours(root, first_age_days=age_days)
    # The hour being written right now and its compression temporary.
    put(root, "wall_r0", "wall_r0-{date}-" + CURRENT + "-00099.jsonl.open", age_days=0)
    put(root, "context", "context-{date}-" + CURRENT + "-00098.jsonl.gz.tmp", age_days=0)
    report = R.prune(root, now_s=NOW_S, apply=True)
    assert report["delete"] == [] and report["deleted_files"] == 0
    assert report["kept"]["active_session"] == len(mine)
    assert all(path.exists() for path in mine)


@pytest.mark.parametrize("age_days,deleted", [(8, True), (6, False)])
def test_closed_session_keeps_the_seven_day_rule(tmp_path, age_days, deleted):
    root = make_root(tmp_path)
    paths = {put(root, kind, f"{kind}-{{date}}-{OLD_SESSION}-00001{suffix}", age_days=age_days)
             for kind, suffix in (("session", ".jsonl"), ("telemetry", ".jsonl"),
                                  ("storage_stats", ".jsonl"), ("context", ".jsonl.gz"),
                                  ("wall_v2", ".jsonl.gz"), ("wall_r0", ".jsonl.gz"))}
    report = R.prune(root, now_s=NOW_S, apply=True)
    assert report["deleted_files"] == (len(paths) if deleted else 0)
    assert all(path.exists() != deleted for path in paths)


def test_closed_old_wall_r0_goes_while_active_wall_r0_stays(tmp_path):
    root = make_root(tmp_path)
    closed = put(root, "wall_r0", "wall_r0-{date}-" + OLD_SESSION + "-00001.jsonl.gz", age_days=9)
    active = put(root, "wall_r0", "wall_r0-{date}-" + CURRENT + "-00001.jsonl.gz", age_days=9)
    report = R.prune(root, now_s=NOW_S, apply=True)
    assert listed(report) == {str(closed.relative_to(root))}
    assert not closed.exists() and active.exists()


@pytest.mark.parametrize("lock", [
    {"pid": 1, "session_id": None},                 # names no session
    {"pid": 1},                                     # pre-session lock shape
    {"pid": 1, "session_id": "NOT-HEX-session"},    # not a session8
    "garbage",                                      # not JSON
])
def test_unknown_identity_fails_closed(tmp_path, lock):
    root = make_root(tmp_path)
    text = lock if isinstance(lock, str) else json.dumps(lock)
    (root / ".writer.lock").write_text(text)
    old = {put(root, "wall_r0", "wall_r0-{date}-" + s8 + "-00001.jsonl.gz", age_days=30)
           for s8 in (CURRENT, OLD_SESSION)}
    report = R.prune(root, now_s=NOW_S, apply=True)
    assert report["identity_failure"] is not None
    assert report["delete"] == [] and report["kept"]["identity_unknown"] == 2
    assert all(path.exists() for path in old)


def test_state_naming_another_session_fails_closed(tmp_path):
    root = make_root(tmp_path)
    _state(root, f"{OLD_SESSION}-rest")
    old = put(root, "context", "context-{date}-" + OLD_SESSION + "-00001.jsonl.gz", age_days=30)
    report = R.prune(root, now_s=NOW_S, apply=True)
    assert report["identity_failure"] == "STATE_NAMES_OTHER_SESSION"
    assert report["delete"] == [] and old.exists()


def test_a_live_process_alone_does_not_make_every_file_active(tmp_path):
    root = make_root(tmp_path)
    # A lock held by this very (alive) process: closed sessions still age out.
    lock = json.loads((root / ".writer.lock").read_text())
    lock["pid"] = os.getpid()
    (root / ".writer.lock").write_text(json.dumps(lock))
    _state(root, f"{CURRENT}-rest")
    closed = put(root, "context", "context-{date}-" + OLD_SESSION + "-00001.jsonl.gz", age_days=9)
    assert listed(R.plan(root, now_s=NOW_S)) == {str(closed.relative_to(root))}


@pytest.mark.parametrize("shape", ["crypto_paper_run", "liqfwd", "other_runtime"])
def test_other_runtime_roots_are_refused(tmp_path, shape, capsys):
    """Paths shaped like the other production runtimes. None has `context/` + `.writer.lock`."""
    root = tmp_path / shape
    root.mkdir()
    if shape == "crypto_paper_run":
        (root / "ledger.jsonl").write_text("{}\n")
        (root / "tape").mkdir()
        victim = root / "tape" / f"tape-{_date(NOW_S - 30 * DAY)}-{OLD_SESSION}-00001.jsonl"
    elif shape == "liqfwd":
        # A V0-style store root with its own writer lock but no context kind.
        (root / ".writer.lock").write_text(json.dumps({"pid": 1, "session_id": "0dd5e550-x"}))
        (root / "session").mkdir()
        victim = root / "session" / f"session-{_date(NOW_S - 30 * DAY)}-{OLD_SESSION}-00001.jsonl"
    else:
        (root / "context").mkdir()  # context/ but no lock: still not a collector root
        victim = root / "context" / f"context-{_date(NOW_S - 30 * DAY)}-{OLD_SESSION}-00001.jsonl.gz"
    victim.write_bytes(b"x")
    os.utime(victim, (NOW_S - 30 * DAY,) * 2)
    with pytest.raises(R.RootRefused):
        R.plan(root, now_s=NOW_S)
    assert cli(["prune", "--root", str(root), "--apply"]) == 2
    assert "REFUSED" in capsys.readouterr().err
    assert victim.exists()


def _mixed_root(tmp_path: Path) -> Path:
    root = make_root(tmp_path)
    _state(root, f"{CURRENT}-rest")
    _active_hours(root, first_age_days=12)
    for n, age in enumerate((9, 8.5, 6, 1), start=1):
        for kind, suffix in (("context", ".jsonl.gz"), ("wall_r0", ".jsonl.gz"),
                             ("telemetry", ".jsonl")):
            put(root, kind, f"{kind}-{{date}}-{OLD_SESSION}-{n:05d}{suffix}", age_days=age)
    return root


def test_dry_run_list_equals_apply_list_and_rerun_is_idempotent(tmp_path):
    root = _mixed_root(tmp_path)
    dry = R.prune(root, now_s=NOW_S)
    applied = R.prune(root, now_s=NOW_S, apply=True)
    assert dry["delete"] == applied["delete"] and dry["delete_files"] == 6
    assert applied["deleted_files"] == 6 and applied["failed"] == []
    again = R.prune(root, now_s=NOW_S, apply=True)
    assert again["delete"] == [] and again["deleted_files"] == 0
    assert again["kept"] == applied["kept"]
