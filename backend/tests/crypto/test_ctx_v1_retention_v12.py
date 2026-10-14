"""Retention V1.2 (`RETENTION_CTX_V1_2.md`): HOT / ARCHIVE_CANDIDATE / PRUNE_ELIGIBLE, the Q123
checkpoint guard, manifests and receipts, display-only disk health, and dry runs that delete
nothing.

Every gate is asserted by the keep reason it produces, so a test that passes for the wrong reason
fails. The last section drives the real Q123 recorder over a journal V1.2 has pruned.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import time
from collections import namedtuple
from pathlib import Path

import pytest

from app.crypto.context_collector_v1 import archive as A
from app.crypto.context_collector_v1 import retention as R
from app.crypto.context_collector_v1.__main__ import main as cli

NOW_S = 1_791_900_000.0           # 2026-10-13T... UTC
HOUR = 3600
DAY = 86_400
ACTIVE = "c0ffee00"
CLOSED = "0dd5e550"
SCHEMA = R.Q123_CHECKPOINT_SCHEMA
Usage = namedtuple("Usage", "total used free")
PLENTY = Usage(10_000_000_000, 1_000_000_000, 9_000_000_000)


def _date(seconds: float) -> str:
    return time.strftime("%Y%m%d", time.gmtime(seconds))


# --------------------------------------------------------------------------- builders

def make_root(tmp_path: Path, *, holder: str | None = ACTIVE, state_session: str | None = None
              ) -> Path:
    root = tmp_path / "journal"
    (root / "context").mkdir(parents=True)
    (root / ".writer.lock").write_text(json.dumps(
        {"pid": 1, "session_id": f"{holder}-aaaa-bbbb" if holder else None, "started_ms": 1,
         "lock_version": "ms-v0-lock.v1-2"}) + "\n")
    if state_session:
        (root / "state").mkdir()
        (root / "state" / R.STATE_FILENAME).write_text(json.dumps(
            {"session": {"session_id": f"{state_session}-aaaa"}}))
    return root


def put(root: Path, kind: str, s8: str, n: int, *, age_h: float, data: bytes | None = None,
        name_age_h: float | None = None, suffix: str | None = None) -> Path:
    directory = root / kind
    directory.mkdir(parents=True, exist_ok=True)
    if suffix is None:
        suffix = ".jsonl.gz" if kind in ("context", "wall_r0", "wall_v2") else ".jsonl"
    name_age = age_h if name_age_h is None else name_age_h
    path = directory / f"{kind}-{_date(NOW_S - name_age * HOUR)}-{s8}-{n:05d}{suffix}"
    payload = data if data is not None else f'{{"kind":"{kind}","n":{n}}}\n'.encode()
    path.write_bytes(gzip.compress(payload, mtime=0) if suffix.endswith(".gz") else payload)
    mtime = NOW_S - age_h * HOUR
    os.utime(path, (mtime, mtime))
    return path


def session_file(root: Path, s8: str, *, end: bool, age_h: float = 200) -> Path:
    lines = [{"kind": "session", "payload": {"event": "START", "session_id": f"{s8}-aaaa",
                                             "collector_version": "ctx-collector.code.1.1",
                                             "contract": {"id": "x"}}}]
    if end:
        lines.append({"kind": "session", "payload": {"event": "END", "reason": "stop"}})
    data = "".join(json.dumps(x) + "\n" for x in lines).encode()
    return put(root, "session", s8, 1, age_h=age_h, data=data)


def cursor(s8: str, n_ctx: int, n_r0: int | None = None, *, done: bool = False) -> dict:
    return {"s8": s8, "pos": {"context": [n_ctx, 10], "wall_r0": [n_r0 or n_ctx, 10]},
            "resting": {"ASK|100050.0": {"side": "ASK"}}, "pending": [],
            "last_ctx_seq": 1, "session_id": f"{s8}-aaaa", "first_receive_ms": 1,
            "samples": 1, "last_sample_ms": 1, "done": done}


def checkpoint(runtime: Path, sample_ms: int, cursors: dict, *, schema: str = SCHEMA,
               corrupt: bool = False, unrecoverable: dict | None = None) -> Path:
    body = {"schema": schema, "sample_ms": sample_ms, "cursors": cursors,
            "unrecoverable": unrecoverable or {}, "quality": {}}
    body["sha256"] = hashlib.sha256(A.canonical(body).encode()).hexdigest()
    path = runtime / "checkpoints" / f"ckpt-{sample_ms:015d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = A.canonical(body)
    path.write_text(text[:-5] + "XXXX}" if corrupt else text)
    return path


def q123_store(store: Path, *, anchor_ms: int | None) -> Path:
    store.mkdir(parents=True, exist_ok=True)
    (store / "meta.json").write_text("{}\n")
    rows = [] if anchor_ms is None else [{"key": "a1", "anchor_ts": anchor_ms}]
    (store / "approaches.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return store


def q123(tmp_path: Path, cursors: dict, *, sample_ms: int = 1_000_000, **kw) -> tuple[Path, Path]:
    """A runtime with one checkpoint and a store whose need lies after it."""
    runtime = tmp_path / "rt"
    checkpoint(runtime, sample_ms, cursors, **kw)
    store = q123_store(tmp_path / "store",
                       anchor_ms=sample_ms + R.Q123_ROW_MARGIN_MS + R.Q123_SAMPLE_MARGIN_MS + 1)
    return runtime, store


def run(root: Path, tmp_path: Path, *, runtime: Path | None = None, store: Path | None = None,
        **kw) -> dict:
    kw.setdefault("usage", PLENTY)
    return R.retain(root, tmp_path / "archive", q123_runtime=runtime, q123_store=store,
                    now_s=NOW_S, **kw)


def by_path(report: dict) -> dict[str, dict]:
    return {f["path"]: f for f in report["files"]}


def reason(report: dict, path: Path) -> str:
    return by_path(report)[str(path.relative_to(path.parents[1]))]["reason"]


def klass(report: dict, path: Path) -> str:
    return by_path(report)[str(path.relative_to(path.parents[1]))]["class"]


def receipt_all(tmp_path: Path, root: Path, *, tamper: str | None = None) -> None:
    """What a successful pull leaves behind: a receipt per manifest, verified against `root`."""
    manifests, _ = A.load_manifests(tmp_path / "archive")
    for manifest in manifests.values():
        verified, failed = A.verify_pulled(manifest, root)
        if tamper == "sha" and verified:
            verified[0] = dict(verified[0], sha256="0" * 64)
        receipt = A.build_receipt(manifest, verified=verified, failed=failed,
                                  destination="/local", pulled_at_s=NOW_S, verified_at_s=NOW_S)
        if tamper == "seal":
            receipt["file_count"] += 1
        A.write_atomic(tmp_path / "archive" / "receipts" / f'{manifest["manifest_id"]}.json',
                       (A.canonical(receipt) + "\n").encode())


def eligible_after_receipt(root: Path, tmp_path: Path, **kw) -> dict:
    run(root, tmp_path, **kw)                   # writes manifests
    receipt_all(tmp_path, root)
    return run(root, tmp_path, **kw)


# --------------------------------------------------------------------------- 1, 2, 3: Q123 guard

def test_1_active_session_with_valid_checkpoint_is_eligible_below_the_margin(tmp_path):
    root = make_root(tmp_path)
    old = put(root, "context", ACTIVE, 3, age_h=100)
    r0 = put(root, "wall_r0", ACTIVE, 3, age_h=100)
    runtime, store = q123(tmp_path, {ACTIVE: cursor(ACTIVE, 20)})
    report = eligible_after_receipt(root, tmp_path, runtime=runtime, store=store)
    assert report["q123"]["state"] == "OK"
    assert report["q123"]["sessions"][ACTIVE]["watermark"] == {"context": 20, "wall_r0": 20}
    assert klass(report, old) == klass(report, r0) == "PRUNE_ELIGIBLE"
    assert report["sessions"][ACTIVE]["state"] == "ACTIVE"


@pytest.mark.parametrize("setup,expected", [
    ("no_runtime", "Q123_NO_RUNTIME"),
    ("empty_checkpoints", "Q123_NO_CHECKPOINT"),
    ("only_corrupt", "Q123_NO_CHECKPOINT"),
])
def test_2_active_session_without_a_checkpoint_keeps_context_and_wall_r0(tmp_path, setup,
                                                                          expected):
    root = make_root(tmp_path)
    old = put(root, "context", ACTIVE, 1, age_h=500)
    r0 = put(root, "wall_r0", ACTIVE, 1, age_h=500)
    runtime = None
    store = q123_store(tmp_path / "store", anchor_ms=10**13)
    if setup == "empty_checkpoints":
        runtime = tmp_path / "rt"
        (runtime / "checkpoints").mkdir(parents=True)
    elif setup == "only_corrupt":
        runtime = tmp_path / "rt"
        checkpoint(runtime, 1000, {ACTIVE: cursor(ACTIVE, 50)}, corrupt=True)
    report = eligible_after_receipt(root, tmp_path, runtime=runtime, store=store)
    assert reason(report, old) == reason(report, r0) == expected
    assert report["prune_eligible"]["files"] == 0


@pytest.mark.parametrize("damage", ["schema", "structure"])
def test_3_checkpoint_schema_or_shape_mismatch_fails_the_guard_closed(tmp_path, damage):
    root = make_root(tmp_path)
    old = put(root, "context", ACTIVE, 1, age_h=500)
    v2 = put(root, "wall_v2", ACTIVE, 1, age_h=500)
    runtime, store = q123(tmp_path, {ACTIVE: cursor(ACTIVE, 50)})
    if damage == "schema":
        checkpoint(runtime, 2000, {ACTIVE: cursor(ACTIVE, 60)}, schema="q123-forward-recorder.v2")
    else:
        bad = cursor(ACTIVE, 60)
        del bad["resting"]
        checkpoint(runtime, 2000, {ACTIVE: bad})
    report = eligible_after_receipt(root, tmp_path, runtime=runtime, store=store)
    want = "SCHEMA_MISMATCH" if damage == "schema" else "CHECKPOINT_STRUCTURE"
    assert report["q123"]["state"] == want
    assert reason(report, old) == f"Q123_{want}"
    assert klass(report, v2) == "PRUNE_ELIGIBLE"        # no Q123 dependency: unaffected


def test_3b_a_corrupt_checkpoint_is_ignored_like_the_recorder_ignores_it(tmp_path):
    root = make_root(tmp_path)
    old = put(root, "context", ACTIVE, 1, age_h=500)
    runtime, store = q123(tmp_path, {ACTIVE: cursor(ACTIVE, 50)})
    checkpoint(runtime, 500, {ACTIVE: cursor(ACTIVE, 2)}, corrupt=True)   # would lower W
    report = eligible_after_receipt(root, tmp_path, runtime=runtime, store=store)
    assert report["q123"]["ignored_checksum"] == 1
    assert report["q123"]["sessions"][ACTIVE]["watermark"]["context"] == 50
    assert klass(report, old) == "PRUNE_ELIGIBLE"


def test_3c_no_checkpoint_at_or_before_need_means_the_recorder_would_start_over(tmp_path):
    root = make_root(tmp_path)
    old = put(root, "context", ACTIVE, 1, age_h=500)
    runtime = tmp_path / "rt"
    checkpoint(runtime, 5_000_000, {ACTIVE: cursor(ACTIVE, 50)})
    store = q123_store(tmp_path / "store", anchor_ms=1_000_000)    # need far before it
    report = eligible_after_receipt(root, tmp_path, runtime=runtime, store=store)
    assert reason(report, old) == "Q123_NO_CHECKPOINT_AT_OR_BEFORE_NEED"


def test_3d_an_empty_q123_store_rebuilds_from_session_starts_so_nothing_goes(tmp_path):
    root = make_root(tmp_path)
    old = put(root, "context", ACTIVE, 1, age_h=500)
    runtime = tmp_path / "rt"
    checkpoint(runtime, 1000, {ACTIVE: cursor(ACTIVE, 50)})
    store = q123_store(tmp_path / "store", anchor_ms=None)
    report = eligible_after_receipt(root, tmp_path, runtime=runtime, store=store)
    assert reason(report, old) == "Q123_NEED_NONE"


def test_3e_a_session_missing_from_any_checkpoint_is_kept(tmp_path):
    root = make_root(tmp_path)
    old = put(root, "context", ACTIVE, 1, age_h=500)
    runtime, store = q123(tmp_path, {ACTIVE: cursor(ACTIVE, 50)}, sample_ms=2000)
    checkpoint(runtime, 1000, {"0dd5e550": cursor("0dd5e550", 5, done=True)})
    report = eligible_after_receipt(root, tmp_path, runtime=runtime, store=store)
    assert reason(report, old) == "Q123_SESSION_NOT_IN_EVERY_CHECKPOINT"


def test_3f_an_unrecoverable_session_is_kept(tmp_path):
    root = make_root(tmp_path)
    old = put(root, "context", ACTIVE, 1, age_h=500)
    runtime, store = q123(tmp_path, {ACTIVE: cursor(ACTIVE, 50)},
                          unrecoverable={ACTIVE: "UnrecoverableStart"})
    report = eligible_after_receipt(root, tmp_path, runtime=runtime, store=store)
    assert reason(report, old) == "Q123_UNRECOVERABLE"


# --------------------------------------------------------------------------- 4, 5: margin and HOT

def test_4_the_twelve_segment_margin_below_the_oldest_checkpoint_position(tmp_path):
    root = make_root(tmp_path)
    files = {n: put(root, "context", ACTIVE, n, age_h=500) for n in (7, 8, 9, 19, 20, 21)}
    runtime, store = q123(tmp_path, {ACTIVE: cursor(ACTIVE, 30)}, sample_ms=2000)
    checkpoint(runtime, 1000, {ACTIVE: cursor(ACTIVE, 20)})     # the oldest one sets W = 20
    report = eligible_after_receipt(root, tmp_path, runtime=runtime, store=store)
    assert R.MARGIN_SEGMENTS == 12
    assert {n: klass(report, p) for n, p in files.items()} == {
        7: "PRUNE_ELIGIBLE", 8: "PRUNE_ELIGIBLE", 9: "ARCHIVE_CANDIDATE",
        19: "ARCHIVE_CANDIDATE", 20: "ARCHIVE_CANDIDATE", 21: "ARCHIVE_CANDIDATE"}
    assert reason(report, files[9]) == "Q123_MARGIN"


def test_5_hot_48h_protects_on_either_clock(tmp_path):
    root = make_root(tmp_path)
    young = put(root, "wall_v2", ACTIVE, 1, age_h=47)
    old_mtime_young_name = put(root, "wall_v2", ACTIVE, 2, age_h=100, name_age_h=1)
    old = put(root, "wall_v2", ACTIVE, 3, age_h=100)
    report = eligible_after_receipt(root, tmp_path)
    assert R.HOT_HOURS == 48
    assert klass(report, young) == klass(report, old_mtime_young_name) == "HOT"
    assert klass(report, old) == "PRUNE_ELIGIBLE"
    assert report["hot"]["files"] == 2


def test_5b_open_files_and_session_files_are_never_candidates(tmp_path):
    root = make_root(tmp_path)
    live = put(root, "context", ACTIVE, 9, age_h=900, suffix=".jsonl.open")
    header = session_file(root, ACTIVE, end=False, age_h=900)
    report = eligible_after_receipt(root, tmp_path)
    assert (klass(report, live), reason(report, live)) == ("HOT", "NOT_SEALED")
    assert (klass(report, header), reason(report, header)) == ("PINNED", "ALWAYS_KEPT")
    manifests, _ = A.load_manifests(tmp_path / "archive")
    assert not any(f["kind"] == "session" for m in manifests.values() for f in m["files"])


# --------------------------------------------------------------------------- 6-9: manifests, receipts

def test_6_manifest_contents_and_immutability(tmp_path):
    root = make_root(tmp_path)
    session_file(root, ACTIVE, end=False)
    a = put(root, "context", ACTIVE, 1, age_h=100)
    runtime, store = q123(tmp_path, {ACTIVE: cursor(ACTIVE, 40)})
    first = run(root, tmp_path, runtime=runtime, store=store)
    assert len(first["manifests"]["new"]) == 1
    manifest_id = first["manifests"]["new"][0]
    path = tmp_path / "archive" / "manifests" / f"{manifest_id}.json"
    body = json.loads(path.read_text())
    assert A.sealed_ok(body, "manifest_sha256")
    assert body["session8"] == ACTIVE and body["session_id"] == f"{ACTIVE}-aaaa"
    assert body["session_state"] == "ACTIVE"
    assert body["journal_schema"]["collector_version"] == "ctx-collector.code.1.1"
    assert body["retention_version"] == "ctx-retention.v1.2"
    assert body["q123"]["watermark"] == {"context": 40, "wall_r0": 40}
    assert body["q123"]["done"] is False
    entry = body["files"][0]
    assert entry["path"] == str(a.relative_to(root)) and entry["segment"] == 1
    assert entry["sha256"] == A.sha256_file(a) and entry["bytes"] == a.stat().st_size
    assert body["created_at"] and body["file_count"] == 1
    # A second run adds nothing; a file that leaves HOT later goes into a new part.
    again = run(root, tmp_path, runtime=runtime, store=store)
    assert again["manifests"]["new"] == []
    put(root, "context", ACTIVE, 2, age_h=100)
    third = run(root, tmp_path, runtime=runtime, store=store)
    assert third["manifests"]["new"] == [manifest_id[:-3] + "002"]
    with pytest.raises(FileExistsError):
        A.write_manifest(tmp_path / "archive", body)
    # Gzip files are listed as they are, never recompressed.
    assert a.read_bytes()[:2] == b"\x1f\x8b"


def test_7_no_receipt_no_prune(tmp_path):
    root = make_root(tmp_path)
    old = put(root, "wall_v2", ACTIVE, 1, age_h=100)
    report = run(root, tmp_path)
    assert report["manifests"]["new"]
    assert (klass(report, old), reason(report, old)) == ("ARCHIVE_CANDIDATE", "NO_RECEIPT")


@pytest.mark.parametrize("tamper", ["sha", "seal"])
def test_8_a_receipt_that_does_not_match_prunes_nothing(tmp_path, tamper):
    root = make_root(tmp_path)
    old = put(root, "wall_v2", ACTIVE, 1, age_h=100)
    run(root, tmp_path)
    receipt_all(tmp_path, root, tamper=tamper)
    report = run(root, tmp_path, apply=True, prune_enabled=True)
    assert reason(report, old) == "NO_RECEIPT"
    assert list(report["receipts"]["rejected"].values()) == [
        "RECEIPT_FILE_MISMATCH" if tamper == "sha" else "RECEIPT_SEAL_OR_VERSION"]
    assert old.exists() and report["deleted"]["files"] == 0


def test_8b_a_file_changed_after_its_manifest_is_kept(tmp_path):
    root = make_root(tmp_path)
    old = put(root, "wall_v2", ACTIVE, 1, age_h=100)
    run(root, tmp_path)
    receipt_all(tmp_path, root)
    old.write_bytes(gzip.compress(b"different", mtime=0))
    mtime = NOW_S - 100 * HOUR
    os.utime(old, (mtime, mtime))
    report = run(root, tmp_path, apply=True, prune_enabled=True)
    assert reason(report, old) in ("RECEIPT_SHA_MISMATCH", "CURRENT_SHA_MISMATCH")
    assert old.exists()


def test_9_a_valid_receipt_makes_a_file_eligible_and_apply_deletes_it(tmp_path):
    root = make_root(tmp_path)
    old = put(root, "wall_v2", ACTIVE, 1, age_h=100)
    hot = put(root, "wall_v2", ACTIVE, 2, age_h=1)
    run(root, tmp_path)
    receipt_all(tmp_path, root)
    report = run(root, tmp_path, apply=True, prune_enabled=True)
    assert report["applied"] is True
    assert report["deleted"] == {"files": 1, "bytes": by_path(report)[
        str(old.relative_to(root))]["bytes"]}
    assert not old.exists() and hot.exists()
    assert report["receipt_verified"]["files"] == 1


# --------------------------------------------------------------------------- 10, 11: identity, closed

@pytest.mark.parametrize("holder,state_session", [(ACTIVE, "deadbeef"), (None, None)])
def test_10_lock_state_mismatch_or_no_lock_session_prunes_nothing(tmp_path, holder,
                                                                 state_session):
    root = make_root(tmp_path, holder=holder, state_session=state_session)
    old = put(root, "wall_v2", ACTIVE, 1, age_h=100)
    report = eligible_after_receipt(root, tmp_path, apply=True, prune_enabled=True)
    assert reason(report, old) == "IDENTITY_UNKNOWN"
    assert report["sessions"][ACTIVE]["state"] == "UNKNOWN"
    assert old.exists()


def test_11_closed_sessions_clean_and_crashed(tmp_path):
    root = make_root(tmp_path)
    clean, crashed, finalizing = CLOSED, "0dd5e551", "0dd5e552"
    session_file(root, clean, end=True)
    session_file(root, crashed, end=False)
    files = {s: put(root, "context", s, 1, age_h=500) for s in (clean, crashed, finalizing)}
    put(root, "wall_r0", finalizing, 2, age_h=1, suffix=".jsonl.open")
    runtime, store = q123(tmp_path, {ACTIVE: cursor(ACTIVE, 50),
                                     clean: cursor(clean, 3, done=True),
                                     crashed: cursor(crashed, 3, done=False),
                                     finalizing: cursor(finalizing, 3, done=True)})
    report = eligible_after_receipt(root, tmp_path, runtime=runtime, store=store)
    states = {s: report["sessions"][s]["state"] for s in (clean, crashed, finalizing)}
    assert states == {clean: "CLOSED_CLEAN", crashed: "CLOSED_CRASHED", finalizing: "UNKNOWN"}
    assert klass(report, files[clean]) == "PRUNE_ELIGIBLE"          # done: margin not needed
    assert reason(report, files[crashed]) == "Q123_NOT_DONE"
    assert reason(report, files[finalizing]) == "SESSION_STATE_UNKNOWN"


# --------------------------------------------------------------------------- 12, 13: kinds

def test_12_wall_v2_has_no_q123_dependency(tmp_path):
    root = make_root(tmp_path)
    v2 = put(root, "wall_v2", ACTIVE, 1, age_h=100)
    ctx = put(root, "context", ACTIVE, 1, age_h=100)
    report = eligible_after_receipt(root, tmp_path)       # no Q123 runtime at all
    assert klass(report, v2) == "PRUNE_ELIGIBLE"
    assert reason(report, ctx) == "Q123_NO_RUNTIME"
    assert "wall_v2" not in R.Q123_KINDS


def test_13_telemetry_keeps_the_v11_seven_day_contract_apart_from_the_raw_policy(tmp_path):
    root = make_root(tmp_path)
    tel_young = put(root, "telemetry", ACTIVE, 1, age_h=100)
    tel_old = put(root, "telemetry", ACTIVE, 2, age_h=8 * 24)
    stats_old = put(root, "storage_stats", ACTIVE, 1, age_h=8 * 24)
    stats_young = put(root, "storage_stats", ACTIVE, 2, age_h=6 * 24)
    assert R.TELEMETRY_HOT_HOURS == R.RETENTION_DAYS * 24 == 168
    dry = run(root, tmp_path)                                    # no --apply: nothing goes
    assert klass(dry, tel_young) == klass(dry, stats_young) == "HOT"
    assert {klass(dry, tel_old), reason(dry, tel_old)} == {"EXPIRED", "EXPIRED_V1_1_RETENTION"}
    assert dry["expired"]["files"] == 2 and dry["deleted_expired"]["files"] == 0
    manifests, _ = A.load_manifests(tmp_path / "archive")      # never archived, no receipt
    assert not any(f["kind"] in R.TELEMETRY_KINDS for m in manifests.values() for f in m["files"])
    # --apply with the prune switch OFF still expires them: independent of the raw policy.
    applied = run(root, tmp_path, apply=True, prune_enabled=False)
    assert applied["deleted_expired"]["files"] == 2 and applied["deleted"]["files"] == 0
    assert not tel_old.exists() and not stats_old.exists()
    assert tel_young.exists() and stats_young.exists()


def test_13b_telemetry_expiry_matches_v11_and_needs_an_identity(tmp_path):
    root = make_root(tmp_path)
    for n, age in enumerate((6.9 * 24, 7.1 * 24, 30 * 24, 8 * 24), 1):
        put(root, "telemetry", ACTIVE if n % 2 else CLOSED, n, age_h=age)
    put(root, "storage_stats", ACTIVE, 1, age_h=8 * 24, name_age_h=1)   # young name: kept
    v11 = {d["path"] for d in R.plan(root, now_s=NOW_S)["delete"]
           if d["path"].split("/")[0] in R.TELEMETRY_KINDS}
    v12 = {f["path"] for f in run(root, tmp_path)["files"]
           if f["class"] == "EXPIRED" and f["reason"] == "EXPIRED_V1_1_RETENTION"}
    # 7.1 days: its name carries the cut-off date itself, so both versions keep it.
    assert v12 == v11 and sorted(p.rsplit("-", 1)[1] for p in v12) == ["00003.jsonl", "00004.jsonl"]
    unknown = make_root(tmp_path / "u", state_session="deadbeef")
    old = put(unknown, "telemetry", ACTIVE, 1, age_h=30 * 24)
    report = run(unknown, tmp_path / "u", apply=True, prune_enabled=True)
    assert reason(report, old) == "IDENTITY_UNKNOWN" and old.exists()


def test_13c_hot_periods_below_a_day_are_refused(tmp_path):
    root = make_root(tmp_path)
    with pytest.raises(ValueError):
        run(root, tmp_path, hot_hours=12)
    with pytest.raises(ValueError):
        run(root, tmp_path, margin_segments=0)


# --------------------------------------------------------------------------- 14-16: disk health

def test_14_15_disk_states():
    assert A.disk_health("/", usage=Usage(10, 0, 1_000_000_000))["state"] == "OK"
    assert A.disk_health("/", usage=Usage(10, 0, 999_999_999))["state"] == "WARNING"
    assert A.disk_health("/", usage=Usage(10, 0, 500_000_000))["state"] == "WARNING"
    assert A.disk_health("/", usage=Usage(10, 0, 499_999_999))["state"] == "CRITICAL"
    assert A.disk_health("/nonexistent/path/xyz")["state"] == "UNKNOWN"


def test_14_15_disk_state_is_in_every_run_report(tmp_path):
    root = make_root(tmp_path)
    report = run(root, tmp_path, usage=Usage(10, 0, 700_000_000))
    assert report["disk_before"]["state"] == report["health"] == "WARNING"
    assert R.summary(report)["disk_before"]["free_bytes"] == 700_000_000


def test_16_critical_disk_keeps_every_gate(tmp_path):
    root = make_root(tmp_path)
    no_receipt = put(root, "wall_v2", ACTIVE, 1, age_h=100)
    q_guarded = put(root, "context", ACTIVE, 1, age_h=100)
    young = put(root, "wall_v2", ACTIVE, 2, age_h=1)
    critical = Usage(10, 0, 1)
    report = run(root, tmp_path, usage=critical, apply=True, prune_enabled=True)
    assert report["health"] == "CRITICAL"
    assert reason(report, no_receipt) == "NO_RECEIPT"
    assert reason(report, q_guarded) == "Q123_NO_RUNTIME"
    assert klass(report, young) == "HOT"
    assert report["deleted"]["files"] == 0
    assert all(p.exists() for p in (no_receipt, q_guarded, young))


# --------------------------------------------------------------------------- 17: dry runs

@pytest.mark.parametrize("apply,enabled", [(False, False), (True, False), (False, True)])
def test_17_dry_runs_delete_nothing(tmp_path, apply, enabled):
    root = make_root(tmp_path)
    old = put(root, "wall_v2", ACTIVE, 1, age_h=100)
    run(root, tmp_path)
    receipt_all(tmp_path, root)
    before = sorted(p.name for p in root.rglob("*"))
    report = run(root, tmp_path, apply=apply, prune_enabled=enabled)
    assert klass(report, old) == "PRUNE_ELIGIBLE"
    assert report["applied"] is False and report["deleted"]["files"] == 0
    assert sorted(p.name for p in root.rglob("*")) == before


def test_17b_cli_defaults_to_a_dry_run_even_with_apply(tmp_path, monkeypatch, capsys):
    root = make_root(tmp_path)
    old = put(root, "wall_v2", ACTIVE, 1, age_h=100)
    run(root, tmp_path)
    receipt_all(tmp_path, root)
    monkeypatch.delenv(R.PRUNE_ENABLED_ENV, raising=False)
    os.utime(old, (time.time() - 100 * HOUR,) * 2)   # the CLI uses the real clock
    assert cli(["retain", "--root", str(root), "--archive-dir", str(tmp_path / "archive"),
                "--apply"]) == 0
    line = json.loads(capsys.readouterr().out)
    assert line["event"] == "RETAIN" and line["applied"] is False and line["prune_enabled"] is False
    assert "files" not in line and old.exists()
    assert cli(["retain", "--root", str(root)]) == 2                   # no archive dir
    assert cli(["retain", "--root", str(tmp_path), "--archive-dir", str(tmp_path / "a")]) == 2


# --------------------------------------------------------------------------- the pull

def test_pull_round_trip_with_local_rsync(tmp_path, monkeypatch):
    if shutil.which("rsync") is None:
        pytest.skip("rsync not installed")
    root = make_root(tmp_path)
    old = put(root, "wall_v2", ACTIVE, 1, age_h=100)
    tel = put(root, "telemetry", ACTIVE, 1, age_h=9 * 24)
    run(root, tmp_path)

    def local_rsync(argv):                 # "host:/path" -> "/path", everything else unchanged
        return subprocess.run([a.split(":", 1)[1] if a.startswith("srv:") else a for a in argv],
                              check=False, capture_output=True, text=True)

    monkeypatch.setattr(A, "_run", local_rsync)
    dest = tmp_path / "cold"
    report = A.pull(host="srv", remote_root=str(root), remote_archive=str(tmp_path / "archive"),
                    dest=dest)
    assert report["errors"] == []
    assert [m["result"] for m in report["manifests"]] == ["OK"]      # telemetry: not archived
    assert all(m["uploaded"] for m in report["manifests"])
    assert (dest / "journal" / old.relative_to(root)).read_bytes() == old.read_bytes()
    receipt = json.loads(next((tmp_path / "archive" / "receipts").glob("*.json")).read_text())
    assert {"manifest_id", "manifest_sha256", "pulled_at", "verified_at", "result", "file_count",
            "byte_count", "aggregate_digest", "receipt_sha256"} <= set(receipt)
    again = A.pull(host="srv", remote_root=str(root), remote_archive=str(tmp_path / "archive"),
                   dest=dest)
    assert [m["result"] for m in again["manifests"]] == ["ALREADY_VERIFIED"]
    final = run(root, tmp_path, apply=True, prune_enabled=True)
    assert final["deleted"]["files"] == 1 and final["deleted_expired"]["files"] == 1
    assert not old.exists() and not tel.exists()


def test_pull_marks_a_missing_file_partial(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    a = put(root, "wall_v2", ACTIVE, 1, age_h=100)
    b = put(root, "wall_v2", ACTIVE, 2, age_h=100)
    run(root, tmp_path)
    b.unlink()
    manifest = next(iter(A.load_manifests(tmp_path / "archive")[0].values()))
    verified, failed = A.verify_pulled(manifest, root)
    receipt = A.build_receipt(manifest, verified=verified, failed=failed, destination="/x",
                              pulled_at_s=NOW_S, verified_at_s=NOW_S)
    assert receipt["result"] == "PARTIAL" and failed[0]["reason"] == "NOT_PULLED"
    files, why = A.check_receipt(receipt, manifest)
    assert why is None and list(files) == [str(a.relative_to(root))]


# --------------------------------------------------------------------------- parity with Q123

def test_q123_constants_match_the_recorder():
    RC = pytest.importorskip("app.crypto.research.q123_forward_v1.recorder")
    K = pytest.importorskip("app.crypto.research.q123_forward_v1.contract")
    ST = pytest.importorskip("app.crypto.research.q123_forward_v1.stream")
    STO = pytest.importorskip("app.crypto.research.q123_forward_v1.store")
    assert R.Q123_CHECKPOINT_SCHEMA == RC.RECORDER_VERSION
    assert R.Q123_HORIZONS_MIN == tuple(K.HORIZONS_MIN)
    assert R.Q123_SAMPLE_MARGIN_MS == RC.SAMPLE_MARGIN_MS
    assert R.Q123_ROW_MARGIN_MS == RC.ROW_MARGIN_MS
    assert R.Q123_KINDS == tuple(ST.KINDS)
    assert A.canonical({"b": 1, "a": "é"}) == STO.canonical({"b": 1, "a": "é"})


def test_q123_need_matches_the_recorder(tmp_path):
    STO = pytest.importorskip("app.crypto.research.q123_forward_v1.store")
    RC = pytest.importorskip("app.crypto.research.q123_forward_v1.recorder")
    store = tmp_path / "store"
    s = STO.Store.open(store, create_meta={"schema": "x"})
    s.append(STO.APPROACHES, [{"key": "a1", "anchor_ts": 50_000_000},
                              {"key": "a2", "anchor_ts": 90_000_000}])
    s.append(STO.EVENTS, [{"key": "e1", "event_id": "e1", "validity": "VALID",
                           "decision_ts": 40_000_000},
                          {"key": "e2", "event_id": "e2", "validity": "INVALID",
                           "decision_ts": 1}])
    s.append(STO.OUTCOMES, [{"key": f"e1:{h}"} for h in (5, 15, 30)])
    fake = type("F", (), {"store": STO.Store.open(store, read_only=True)})()
    assert R.q123_need_ms(store) == (RC.Recorder._needed_from(fake), None)
    s.append(STO.OUTCOMES, [{"key": "e1:60"}])
    fake.store = STO.Store.open(store, read_only=True)
    assert R.q123_need_ms(store) == (RC.Recorder._needed_from(fake), None)


# --------------------------------------------------------------------------- the real recorder

def test_recorder_resumes_unchanged_after_a_v12_prune(tmp_path):
    """Drive Q123, prune the active session's early segments with V1.2, drive again: the store is
    the one an unpruned run produces, and the active session's first segment is really gone."""
    FX = pytest.importorskip("tests.crypto.q123_recorder_fixtures")
    QR = pytest.importorskip("tests.crypto.test_q123_forward_recorder")
    # A late approach puts Q123's need two hours behind the tail, past the first checkpoints:
    # the steady state of a recorder that has run for days. With every checkpoint after the
    # need (a young recorder) V1.2 refuses, as test_3c shows.
    approaches = QR.APPROACH + [(FX.T0 + 140 * 60_000, "RES", 8, FX.LEVEL)]

    def build(root):                       # one long active session, as in production
        b = FX.session(root, "b", FX.T0 + 60_000 - 30_000, 9000, walls=QR.ASK_WALL)
        (root / ".writer.lock").write_text(json.dumps(
            {"pid": 1, "session_id": FX.sid("b"), "started_ms": 1}))
        return b

    def grow(b):
        for k in range(9000, 10200):
            b.sample(FX.T0 + 60_000 - 30_000 + k * 1000, FX.LEVEL * 0.999)
        b.flush()

    clean_root = tmp_path / "clean" / "root"
    grow(build(clean_root))
    assert QR.drive(clean_root, tmp_path / "clean", approaches=approaches) == 0
    clean = QR.store_lines(tmp_path / "clean" / "store")
    assert clean["events.jsonl"] and clean["outcomes.jsonl"], "the run must produce facts"

    root = tmp_path / "root"
    b = build(root)
    assert QR.drive(root, tmp_path, approaches=approaches) == 0      # checkpoints written
    grow(b)
    sealed = NOW_S - 30 * DAY
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (sealed, sealed))
    kw = dict(q123_runtime=tmp_path / "rt", q123_store=tmp_path / "store", now_s=NOW_S,
              margin_segments=1, usage=PLENTY)
    R.retain(root, tmp_path / "archive", **kw)
    receipt_all(tmp_path, root)
    report = R.retain(root, tmp_path / "archive", apply=True, prune_enabled=True, **kw)
    assert report["q123"]["state"] == "OK", report["q123"]
    s8b = FX.sid("b")[:8]
    gone = [f["path"] for f in report["files"] if f.get("deleted") and f.get("s8") == s8b]
    # context rotates every 400 records, so its first segments fall below the watermark; the
    # fixture writes few wall rows, so wall_r0 stays inside the margin and is kept.
    assert any(p.startswith("context/") and "-00001.jsonl" in p for p in gone), \
        report["keep_reasons"]
    assert QR.drive(root, tmp_path, approaches=approaches) == 0
    assert QR.store_lines(tmp_path / "store") == clean


# --------------------------------------------------------------------------- BTC context /health

def test_btc_context_health_reports_disk_and_stays_ok(monkeypatch):
    api = pytest.importorskip("app.crypto.btc_context_ui_v1.api")
    import asyncio
    monkeypatch.setattr(api.CA, "disk_health", lambda path: {"state": "CRITICAL", "path": str(path)})
    body = asyncio.run(api.health())
    assert body["ok"] is True and body["disk"]["state"] == "CRITICAL"
