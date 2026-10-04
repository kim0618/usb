"""One output root, one authority writer, and every way that was found to be untrue.

This file exists because of a measured incident rather than a worry. A measurement harness
launched the collector twice against one root; the second launch removed and recreated the root
while the first collector was still running. The result was two sessions writing one directory,
one session's `.open` files renamed out from under its file descriptors by the other's orphan
recovery, and a `FileNotFoundError` from the first process's seal at exit. The audit is below as
executable cases, including the ones that already behaved correctly, because a guarantee nobody
re-checks is a guarantee that quietly stops holding.

The mechanism, which is the whole point: **`flock` locks an inode, not a path.** While the lock
file exists, a second `Store.open` is refused and always was. Remove that file - on its own or
with the root around it - and the next `open(..., O_CREAT)` makes a *different* inode which locks
cleanly, so both processes hold a valid exclusive lock on two different files and neither can
tell from `flock` alone.

So the contract is enforced from two sides, and the tests are split the same way:

* **the incumbent fails closed.** A writer re-checks, once a second on the flush tick and before
  every seal and state write, that the lock file under its root is still the inode it locked. The
  moment it is not, the writer stops. This half is complete: it needs no cooperation from anybody.
* **the newcomer is refused** whenever the filesystem still carries evidence of a live writer -
  the lock file itself, or a compact state file younger than three seconds whose pid is alive.
  This half is a **heuristic and is documented as one**: a root that was removed outright takes
  that evidence with it, and the newcomer then has nothing left to read.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.crypto.market_structure_v0 import store as ST
from app.crypto.market_structure_v0.contract import OPEN_SUFFIX
from app.crypto.market_structure_v0.store import (Store, StoreAuthorityLost, StoreLocked,
                                                  live_writer_evidence)

BACKEND = Path(__file__).resolve().parents[2]
SESSION_A = "aaaaaaaa-1111-2222-3333-444444444444"
SESSION_B = "bbbbbbbb-1111-2222-3333-444444444444"


def record(kind: str = "storage_stats", seq: int = 1) -> dict:
    return {"kind": kind, "seq": seq, "receive_ms": int(time.time() * 1000),
            "mono_ns": time.monotonic_ns(), "payload": {"n": seq}}


def opened(root: Path, session: str = SESSION_A) -> Store:
    return Store.open(root, session, started_ns=time.monotonic_ns())


def state_payload(pid: int, *, session: str = SESSION_A, written_ms: int | None = None,
                  ended: bool = False) -> dict:
    return {"state_version": "ms-v0-state.v1-2", "is_authority": False,
            "written_ms": int(time.time() * 1000) if written_ms is None else written_ms,
            "session": {"session_id": session, "ended": ended, "seq": 1},
            "collector": {"pid": pid}}


def write_state_file(root: Path, payload: dict) -> None:
    directory = root / ST.STATE_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ST.STATE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def names(root: Path) -> list[str]:
    return sorted(path.name for path in root.rglob("*.jsonl*"))


def flush(store: Store) -> None:
    """Force the buffer out. `tick` flushes on an interval, so a tick at `now` writes nothing."""
    store.tick(time.monotonic_ns() + 10 ** 10, int(time.time() * 1000))


# --- A and B started together -----------------------------------------------------------------

def test_a_second_collector_on_the_same_root_is_refused(tmp_path):
    root = tmp_path / "data"
    first = opened(root, SESSION_A)
    with pytest.raises(StoreLocked):
        opened(root, SESSION_B)
    first.close()


def test_the_refusal_names_the_process_that_holds_the_root(tmp_path):
    """"Somebody has it" is not an operable message; a pid and a session are."""
    root = tmp_path / "data"
    first = opened(root, SESSION_A)
    with pytest.raises(StoreLocked) as refused:
        opened(root, SESSION_B)
    assert str(os.getpid()) in str(refused.value)
    assert SESSION_A in str(refused.value)
    first.close()


def test_two_different_roots_do_not_block_each_other(tmp_path):
    first = opened(tmp_path / "one", SESSION_A)
    second = opened(tmp_path / "two", SESSION_B)
    first.write(record())
    second.write(record())
    first.close()
    second.close()
    assert names(tmp_path / "one") and names(tmp_path / "two")


def test_the_lock_is_released_when_the_first_writer_closes(tmp_path):
    root = tmp_path / "data"
    first = opened(root, SESSION_A)
    first.close()
    second = opened(root, SESSION_B)
    second.close()


def test_two_processes_racing_for_one_root_produce_exactly_one_winner(tmp_path):
    """The same question asked of real processes rather than of one interpreter."""
    root = tmp_path / "data"
    program = (
        "import sys, time;"
        "sys.path.insert(0, %r);"
        "from app.crypto.market_structure_v0.store import Store, StoreLocked;"
        "\ntry:\n"
        "    store = Store.open(__import__('pathlib').Path(%r), 'ssssssss-0-0-0-0',"
        "                       started_ns=time.monotonic_ns())\n"
        "    print('WON'); time.sleep(1.5); store.close()\n"
        "except StoreLocked:\n"
        "    print('REFUSED')\n" % (str(BACKEND), str(root)))
    processes = [subprocess.Popen([sys.executable, "-c", program], cwd=BACKEND,
                                  stdout=subprocess.PIPE, text=True) for _ in range(4)]
    outcomes = [process.communicate()[0].strip() for process in processes]
    assert outcomes.count("WON") == 1, outcomes
    assert outcomes.count("REFUSED") == 3, outcomes


# --- the incumbent notices, and stops ----------------------------------------------------------

def test_a_writer_whose_lock_file_was_removed_stops_writing(tmp_path):
    """The half of the contract that can be enforced without anybody's cooperation."""
    root = tmp_path / "data"
    store = opened(root, SESSION_A)
    store.write(record())
    assert store.holds_authority() is True
    (root / ".writer.lock").unlink()
    assert store.holds_authority() is False
    with pytest.raises(StoreAuthorityLost):
        store.tick(time.monotonic_ns(), int(time.time() * 1000))
    store.close()


def test_a_writer_whose_lock_file_was_replaced_stops_writing(tmp_path):
    """Replaced, not removed: the path is there and is a different inode, which is the trap."""
    root = tmp_path / "data"
    store = opened(root, SESSION_A)
    (root / ".other").write_text("x", encoding="utf-8")
    os.replace(root / ".other", root / ".writer.lock")
    assert store.holds_authority() is False
    with pytest.raises(StoreAuthorityLost):
        store.tick(time.monotonic_ns(), int(time.time() * 1000))
    store.close()


def test_losing_the_lock_is_sticky(tmp_path):
    """Putting a file back where the lock was does not restore an authority nobody kept."""
    root = tmp_path / "data"
    store = opened(root, SESSION_A)
    (root / ".writer.lock").unlink()
    with pytest.raises(StoreAuthorityLost):
        store.tick()
    opened(root, SESSION_B).close()  # somebody else took the root and left again
    with pytest.raises(StoreAuthorityLost):
        store.tick()
    assert "no longer the file this process locked" in (store.authority_lost or "")
    store.close()


def test_a_writer_that_lost_the_root_stops_publishing_the_state_file(tmp_path):
    """The state file is the one artefact that claims to be the current state of the root."""
    root = tmp_path / "data"
    store = opened(root, SESSION_A)
    assert store.write_state({"state_version": "x"}) > 0
    (root / ".writer.lock").unlink()
    assert store.write_state({"state_version": "x"}) == 0
    assert store.state_writes_failed == 1
    assert "AuthorityLost" in (store.state_last_error or "")
    store.close()


def test_a_writer_that_lost_the_root_leaves_its_files_for_recovery_instead_of_crashing(tmp_path):
    """The observed symptom: a seal that cannot find its own path, thrown from shutdown."""
    root = tmp_path / "data"
    store = opened(root, SESSION_A)
    store.write(record())
    flush(store)
    (root / ".writer.lock").unlink()
    assert store.close() == []            # no exception, and nothing claimed as sealed
    assert "left for the next writer" in (store.authority_lost or "")
    leftover = [name for name in names(root) if name.endswith(OPEN_SUFFIX)]
    assert leftover, "the open file must still be there for orphan recovery to find"
    recovered = Store.open(root, SESSION_B, started_ns=time.monotonic_ns())
    assert recovered.recovery["files_recovered"] == len(leftover)
    recovered.close()


def test_the_abandoned_buffer_is_not_flushed_into_a_root_somebody_else_owns(tmp_path):
    root = tmp_path / "data"
    store = opened(root, SESSION_A)
    store.write(record(seq=1))
    flush(store)                 # seq 1 reaches the file
    store.write(record(seq=2))   # seq 2 is still buffered
    (root / ".writer.lock").unlink()
    store.close()
    recovered = Store.open(root, SESSION_B, started_ns=time.monotonic_ns())
    recovered.close()
    rows = [json.loads(line) for path in root.rglob("*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert [row["seq"] for row in rows] == [1]


# --- the newcomer, and the exact limit of what it can see --------------------------------------

def test_a_newcomer_is_refused_while_a_live_state_file_says_somebody_is_writing(tmp_path):
    """Lock file gone, writer still alive: the state file is the remaining evidence."""
    root = tmp_path / "data"
    store = opened(root, SESSION_A)
    write_state_file(root, state_payload(os.getpid()))
    (root / ".writer.lock").unlink()
    with pytest.raises(StoreLocked) as refused:
        opened(root, SESSION_B)
    assert "still writing here" in str(refused.value)
    store.close()


def test_the_refused_newcomer_leaves_the_lock_free_for_the_writer_that_owns_it(tmp_path):
    root = tmp_path / "data"
    store = opened(root, SESSION_A)
    write_state_file(root, state_payload(os.getpid()))
    (root / ".writer.lock").unlink()
    with pytest.raises(StoreLocked):
        opened(root, SESSION_B)
    store.close()
    # And once the owner is gone the root opens normally rather than staying poisoned.
    shutil.rmtree(root / ST.STATE_DIRNAME)
    opened(root, SESSION_B).close()


def test_a_stale_state_file_does_not_block_a_restart(tmp_path):
    """A killed collector must not lock its root forever. Three seconds and the evidence ages out."""
    root = tmp_path / "data"
    write_state_file(root, state_payload(os.getpid(),
                                         written_ms=int(time.time() * 1000)
                                         - ST.LIVE_STATE_WINDOW_MS - 1))
    assert live_writer_evidence(root) is None
    opened(root, SESSION_B).close()


def test_a_state_file_from_a_dead_process_does_not_block_a_restart(tmp_path):
    """Fresh file, dead pid: that is what `kill -9` a moment ago looks like."""
    root = tmp_path / "data"
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    write_state_file(root, state_payload(dead.pid))
    assert live_writer_evidence(root) is None
    opened(root, SESSION_B).close()


def test_a_session_that_recorded_its_own_end_is_not_evidence_of_a_live_writer(tmp_path):
    """A clean stop followed immediately by a restart is an ordinary thing to do."""
    root = tmp_path / "data"
    write_state_file(root, state_payload(os.getpid(), ended=True))
    assert live_writer_evidence(root) is None
    opened(root, SESSION_B).close()


def test_a_removed_root_is_the_case_the_newcomer_cannot_see(tmp_path):
    """Stated as a test so the limit is part of the contract rather than a footnote.

    Everything the newcomer could have read went with the directory. It opens the root, and it is
    right to: nothing distinguishes this from a fresh start. The incumbent is what stops.
    """
    root = tmp_path / "data"
    first = opened(root, SESSION_A)
    first.write(record())
    flush(first)
    shutil.rmtree(root)

    second = opened(root, SESSION_B)              # nothing left to refuse on
    second.write(record())
    flush(second)
    assert second.holds_authority() is True

    # And the first writer stops within one flush tick instead of writing into the new root.
    assert first.holds_authority() is False
    with pytest.raises(StoreAuthorityLost):
        first.tick(time.monotonic_ns(), int(time.time() * 1000))
    first.close()
    second.close()
    sessions = {name.split("-")[2] for name in names(root)}
    assert sessions == {SESSION_B[:8]}, "only the owner's rows may be in the root"


# --- forced termination and restart ------------------------------------------------------------

def test_a_killed_collector_releases_the_lock_and_the_next_one_recovers_its_files(tmp_path):
    """`kill -9` leaves no chance to release anything, so the kernel has to do it."""
    root = tmp_path / "data"
    program = (
        "import sys, time;"
        "sys.path.insert(0, %r);"
        "from pathlib import Path;"
        "from app.crypto.market_structure_v0.store import Store;"
        "store = Store.open(Path(%r), 'cccccccc-0-0-0-0', started_ns=time.monotonic_ns());"
        "store.write({'kind':'storage_stats','seq':1,'receive_ms':1,'mono_ns':1,'payload':{}});"
        "store.tick(time.monotonic_ns() + 10 ** 10, 1);"
        "print('READY', flush=True);"
        "time.sleep(30)" % (str(BACKEND), str(root)))
    process = subprocess.Popen([sys.executable, "-c", program], cwd=BACKEND,
                               stdout=subprocess.PIPE, text=True)
    assert process.stdout.readline().strip() == "READY"
    with pytest.raises(StoreLocked):
        opened(root, SESSION_B)
    process.kill()
    process.wait()
    # The state file the dead process left is no evidence, because its pid is gone.
    assert live_writer_evidence(root) is None
    restarted = Store.open(root, SESSION_B, started_ns=time.monotonic_ns())
    assert restarted.recovery["files_recovered"] >= 1
    assert restarted.recovery["rows_kept"] >= 1
    restarted.close()


def test_the_lock_is_not_inherited_by_a_child_process(tmp_path):
    """An inherited lock descriptor would keep a root locked after its owner exits."""
    root = tmp_path / "data"
    store = opened(root, SESSION_A)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"], cwd=BACKEND)
    store.close()
    # The owner has closed its descriptor; if the child had inherited it, this would be refused.
    second = opened(root, SESSION_B)
    second.close()
    child.wait()


# --- seal and recovery ------------------------------------------------------------------------

def test_recovery_never_overwrites_a_file_that_was_already_sealed(tmp_path):
    root = tmp_path / "data"
    first = opened(root, SESSION_A)
    first.write(record())
    first.close()
    sealed = names(root)[0]
    (root / "storage_stats" / (sealed + OPEN_SUFFIX[len(".jsonl"):])).write_text(
        '{"kind":"storage_stats"}\n', encoding="utf-8")
    recovered = Store.open(root, SESSION_B, started_ns=time.monotonic_ns())
    recovered.close()
    assert sealed in names(root)
    assert any("-r1.jsonl" in name for name in names(root)), names(root)


def test_orphan_recovery_only_ever_runs_while_the_lock_is_held(tmp_path):
    """Recovery renames other processes' files, so it is the one thing that must not race."""
    import inspect
    source = inspect.getsource(Store.open)
    assert source.index("lock.acquire") < source.index("recover_orphans")
    assert "lock.release()" in source
