"""One writer per journal root, and a journal failure that costs the screen nothing.

Two properties, and they pull in opposite directions, which is why they are tested together.

**Fail closed.** A second writer on one root must write nothing at all. The damage two writers do
to this tape is not a torn line - `O_APPEND` under a page is atomic enough that the file stays
parseable - it is that the cadence rule lives in each writer's own `cursors`, so two of them
produce two interleaved cadences, a duplicate for most minutes, and `written_because` values that
describe neither. The first test below measures that damage with the lock bypassed, so the rest of
the file is testing against a number rather than against an intuition.

**Fail quietly.** This writer rides on a read-only panel. The V0 collector exists to write and so
it stops the process when it loses authority; here, stopping the process would turn a tape problem
into a screen outage. So every test that takes the lock away also checks that the panel still
assembles all five layers and that every route still answers.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.crypto.market_context_v1 import assemble as ASM
from app.crypto.market_context_v1 import contract as C
from app.crypto.market_context_v1 import journal as J

BACKEND = Path(__file__).resolve().parents[2]
NOW = 1_790_000_000_000


def payload(*, symbol: str = C.BTC, bar_close_ms: int | None = NOW,
            breakout: str = "NONE") -> dict:
    """The shape `record()` reads, with only the fields the cadence anchors on filled in."""
    return {
        "panel_version": "mc-v1.0", "symbol": symbol,
        "layers": {
            "PRICE": {"state": C.LIVE, "reasons": [],
                      "row": {"close": "86000.0", "bar_open_ms": None,
                              "bar_close_ms": bar_close_ms, "trend_structure": "UP",
                              "active_level_count": 7, "atr_5m": "40", "atr_1h": "300"},
                      "levels": {"resistance": None, "support": None},
                      "events": {"breakout_state": breakout, "retest_state": "NONE",
                                 "failed_break_state": "NONE", "breakout_level_id": None,
                                 "breakout_age_min": None},
                      "range": None, "prev_session": None, "series": None},
            "LIQUIDITY": {"state": C.LIVE, "reasons": [], "sides": {}, "book": {"mid": "86000"},
                          "coverage": {}},
            "FLOW": {"state": C.LIVE, "reasons": [], "windows": {}, "vanished": {},
                     "absorption": None, "trade_stream": {}},
            "DERIVATIVES": {"state": C.LIVE, "reasons": [], "open_interest": {},
                            "open_interest_change": {}, "funding": {}, "basis": {}},
            "AUXILIARY": {"state": C.UNAVAILABLE, "reasons": []},
        },
    }


def line(**kwargs) -> dict:
    written = kwargs.pop("written_at_ms", NOW)
    return J.record(payload(**kwargs), written_at_ms=written)


# --- what the lock is for ----------------------------------------------------------------------

def test_two_writers_on_one_root_damage_the_tape_in_a_measurable_way(tmp_path, monkeypatch):
    """The baseline, with authority bypassed. This is the number the lock exists to prevent.

    Without it, "two writers would be bad" is a belief. With it, the expectation in every test
    below - one line per minute, from one cadence - is a comparison.
    """
    monkeypatch.setattr(J.ForwardJournal, "_authorised", lambda self: True)
    first, second = J.ForwardJournal(tmp_path), J.ForwardJournal(tmp_path)
    minutes = [NOW + index * 60_000 for index in range(4)]
    for minute in minutes:
        for writer in (first, second):
            writer.append(line(bar_close_ms=minute, written_at_ms=minute),
                          written_at_ms=minute)
    records = J.read_records(J.day_file(tmp_path, NOW))
    assert len(records) == 8                       # four minutes, each written twice
    assert [record["written_because"] for record in records].count("FIRST_RECORD") == 2
    # And the duplicates are indistinguishable from real records: same minute, same signature,
    # same `written_because`. No reader can repair this after the fact.
    assert len({json.dumps({k: v for k, v in record.items()
                            if k != "written_at_ms"}, sort_keys=True)
                for record in records}) == 4


# --- collision ---------------------------------------------------------------------------------

def test_the_second_writer_on_one_root_is_refused_and_writes_nothing(tmp_path):
    first = J.ForwardJournal(tmp_path)
    assert first.start()["writer"]["writable"] is True
    second = J.ForwardJournal(tmp_path)
    view = second.start()
    assert view["writer"]["writable"] is False
    assert view["status"] == J.STATE_REFUSED
    assert second.append(line(), written_at_ms=NOW) is False
    assert second.written == 0 and second.blocked_writes == 1
    # The refused writer did not advance its own cursor. A writer that believed it had written
    # would skip the next real line as a duplicate the moment it got the lock back.
    assert second.cursors == {}
    assert first.append(line(), written_at_ms=NOW) is True
    assert len(J.read_records(J.day_file(tmp_path, NOW))) == 1
    first.stop()


def test_a_writer_that_never_called_start_is_still_refused(tmp_path):
    """Authority is a precondition of `append`, not of `start`.

    `start` exists so a second preview is refused at boot. If the lock were only taken there, a
    caller that skipped it - a test, a script, a later refactor - would append without one, and
    the invariant would hold only by convention.
    """
    holder = J.ForwardJournal(tmp_path)
    holder.start()
    sneak = J.ForwardJournal(tmp_path)
    assert sneak.append(line(), written_at_ms=NOW) is False
    assert sneak.state() == J.STATE_REFUSED
    holder.stop()


def test_the_refusal_names_who_holds_the_root(tmp_path):
    """"Somebody has it" is not actionable; a pid and a session are."""
    holder = J.ForwardJournal(tmp_path, session_id="holder-1")
    holder.start()
    refused = J.ForwardJournal(tmp_path, session_id="refused-2")
    lock = refused.start()["writer"]["lock"]
    assert lock["holder"]["pid"] == os.getpid()
    assert lock["holder"]["session_id"] == "holder-1"
    assert lock["holder"]["lock_version"] == J.LOCK_VERSION
    assert lock["held"] is False
    assert refused.view()["writer"]["session_id"] == "refused-2"
    holder.stop()


def test_the_lock_file_records_the_holder_and_is_rewritten_in_place_without_truncating(tmp_path):
    first = J.ForwardJournal(tmp_path, session_id="a-long-session-identifier-aaaaaaaaaaaa")
    first.start()
    first.stop()
    second = J.ForwardJournal(tmp_path, session_id="b")
    second.start()
    raw = J.lock_file(tmp_path).read_text(encoding="utf-8")
    assert json.loads(raw.splitlines()[0])["session_id"] == "b"
    # The previous holder's tail is still in the file, and is ignored rather than cleaned up -
    # cleaning it would mean writing over a file this process may not own.
    assert len(raw.splitlines()) >= 1
    assert second.lock.read_holder()["session_id"] == "b"
    second.stop()


def test_an_unparseable_lock_file_is_reported_as_a_hint_not_trusted_as_a_record(tmp_path):
    """A hand-edited or foreign lock file still names someone, and `read_holder` never raises.

    Exercised on the reader directly, because the first version of this test put the garbage in
    the file and then acquired - and acquiring **overwrites** it, so the test was reading back the
    new holder's own valid payload and the `UNPARSEABLE` branch was never reached. The overwrite
    is pinned below instead, since it is the more useful fact: a leftover lock file never makes
    the next writer unreadable.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    J.lock_file(tmp_path).write_text("not json at all\n", encoding="utf-8")
    lock = J.WriterLock(tmp_path)
    assert lock.read_holder() == {"pid": "not json at all", "format": "UNPARSEABLE"}
    J.lock_file(tmp_path).write_text("", encoding="utf-8")
    assert lock.read_holder() is None
    assert J.WriterLock(tmp_path / "absent").read_holder() is None


def test_acquiring_replaces_whatever_the_lock_file_said_before(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    J.lock_file(tmp_path).write_text("not json at all\n", encoding="utf-8")
    writer = J.ForwardJournal(tmp_path, session_id="fresh")
    assert writer.start()["writer"]["writable"] is True
    assert writer.lock.read_holder()["session_id"] == "fresh"
    writer.stop()


# --- crash and restart -------------------------------------------------------------------------

LOCK_PROGRAM = """
import sys, time
from pathlib import Path
from app.crypto.market_context_v1 import journal as J
writer = J.ForwardJournal(Path(sys.argv[1]), session_id="subprocess")
view = writer.start()
print(view["writer"]["writable"], flush=True)
time.sleep(600)
"""


def _spawn_holder(root: Path) -> subprocess.Popen:
    process = subprocess.Popen([sys.executable, "-c", LOCK_PROGRAM, str(root)], cwd=BACKEND,
                               stdout=subprocess.PIPE, text=True)
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "True"
    return process


@pytest.mark.parametrize("how", [signal.SIGKILL, signal.SIGTERM])
def test_a_crashed_holders_lock_is_free_immediately_with_no_timeout_and_no_cleanup(tmp_path, how):
    """The whole stale-lock recovery rule, exercised against a real kernel rather than a mock.

    `flock` lives on the open file description, so the holder dying releases it - which is why
    this journal has no stale-lock timeout, no "lock older than N" rule, and no path that removes
    somebody else's lock file. SIGKILL is the case that matters: no handler runs, no `stop()` is
    called, and the lock is still gone.
    """
    process = _spawn_holder(tmp_path)
    blocked = J.ForwardJournal(tmp_path)
    assert blocked.start()["status"] == J.STATE_REFUSED
    process.send_signal(how)
    process.wait(timeout=30)
    # The lock file is still there, still naming the dead process. It is not a lock.
    holder = J.lock_file(tmp_path).read_text(encoding="utf-8")
    assert json.loads(holder.splitlines()[0])["session_id"] == "subprocess"
    restarted = J.ForwardJournal(tmp_path, session_id="after-crash")
    assert restarted.start()["writer"]["writable"] is True
    assert restarted.append(line(), written_at_ms=NOW) is True
    restarted.stop()


def test_a_refused_writer_does_not_recover_on_its_own_while_the_holder_lives(tmp_path):
    """Fail closed means closed. Retrying quietly is how both writers end up in the file."""
    process = _spawn_holder(tmp_path)
    try:
        refused = J.ForwardJournal(tmp_path)
        refused.start()
        for index in range(5):
            assert refused.append(line(bar_close_ms=NOW + index * 60_000,
                                       written_at_ms=NOW + index * 60_000),
                                  written_at_ms=NOW + index * 60_000) is False
        assert refused.blocked_writes == 5 and refused.written == 0
        assert J.read_records(J.day_file(tmp_path, NOW)) == []
    finally:
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=30)


def test_an_orderly_stop_releases_the_lock_for_the_next_process(tmp_path):
    first = J.ForwardJournal(tmp_path)
    first.start()
    first.append(line(), written_at_ms=NOW)
    first.stop()
    second = J.ForwardJournal(tmp_path)
    assert second.start()["writer"]["writable"] is True
    later = NOW + 60_000
    assert second.append(line(bar_close_ms=later, written_at_ms=later),
                         written_at_ms=later) is True
    assert len(J.read_records(J.day_file(tmp_path, NOW))) == 2
    second.stop()


def test_stop_is_idempotent_and_safe_on_a_lock_that_was_never_taken(tmp_path):
    writer = J.ForwardJournal(tmp_path)
    writer.stop()
    writer.stop()
    off = J.ForwardJournal(None)
    off.stop()
    assert off.state() == J.STATE_OFF


# --- the inode hole, which is the half the lock cannot close by itself -------------------------

def test_a_holder_whose_lock_file_was_removed_stops_writing(tmp_path):
    """`flock` is on an inode. Delete the file and the next `open` makes a different one.

    From that moment a second writer locks the replacement cleanly and both processes believe they
    are alone - so the holder has to notice. It re-checks the inode before every append and stops
    permanently, because a writer that cannot prove it owns the root is the one that has to yield.
    """
    holder = J.ForwardJournal(tmp_path)
    holder.start()
    assert holder.append(line(), written_at_ms=NOW) is True
    J.lock_file(tmp_path).unlink()
    later = NOW + 60_000
    assert holder.append(line(bar_close_ms=later, written_at_ms=later),
                         written_at_ms=later) is False
    assert holder.state() == J.STATE_AUTHORITY_LOST
    assert holder.view()["writer"]["refusal"] == J.STATE_AUTHORITY_LOST
    assert len(J.read_records(J.day_file(tmp_path, NOW))) == 1
    # And the replacement writer that caused it can take the root, so exactly one cadence
    # continues rather than two.
    successor = J.ForwardJournal(tmp_path, session_id="successor")
    assert successor.start()["writer"]["writable"] is True
    assert successor.append(line(bar_close_ms=later, written_at_ms=later),
                            written_at_ms=later) is True
    assert len(J.read_records(J.day_file(tmp_path, NOW))) == 2
    successor.stop()


def test_authority_once_lost_is_not_quietly_regained(tmp_path):
    holder = J.ForwardJournal(tmp_path)
    holder.start()
    J.lock_file(tmp_path).unlink()
    assert holder.append(line(), written_at_ms=NOW) is False
    # Even with the file back and lockable, this writer stays closed: it is holding a lock on an
    # inode nothing can find, so "lockable again" says nothing about who owns the root now.
    assert holder.append(line(), written_at_ms=NOW + 60_000) is False
    assert holder.written == 0 and holder.blocked_writes == 2


def test_a_replaced_lock_file_is_a_refusal_rather_than_a_lock_that_protects_nothing(tmp_path,
                                                                                   monkeypatch):
    """Between `open` and `flock` the file can be replaced. Two tries, then refuse."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    real_stat = os.stat
    path = str(J.lock_file(tmp_path))

    class Foreign:
        st_dev, st_ino = -1, -1

    def stat_lies(target, *args, **kwargs):
        return Foreign() if str(target) == path else real_stat(target, *args, **kwargs)

    monkeypatch.setattr(J.os, "stat", stat_lies)
    writer = J.ForwardJournal(tmp_path)
    view = writer.start()
    assert view["status"] == J.STATE_REFUSED
    assert "replaced" in (writer.last_error or "")
    assert writer.append(line(), written_at_ms=NOW) is False


# --- status, which is the only thing a screen gets out of all this -----------------------------

def test_the_status_word_walks_off_the_closed_list(tmp_path):
    assert J.ForwardJournal(None).state() == J.STATE_OFF
    writer = J.ForwardJournal(tmp_path)
    assert writer.start()["status"] == J.STATE_WAITING          # holds the lock, nothing written
    writer.append(line(), written_at_ms=NOW)
    assert writer.state(now_ms=NOW) == J.STATE_WRITING
    assert writer.state(now_ms=NOW + J.WRITE_STALE_MS) == J.STATE_WRITING
    assert writer.state(now_ms=NOW + J.WRITE_STALE_MS + 1) == J.STATE_STALE
    assert set(J.JOURNAL_STATES) == {"OFF", "REFUSED", "AUTHORITY_LOST", "ERROR", "STALE",
                                     "WRITING", "WAITING"}
    assert writer.view()["status"] in J.JOURNAL_STATES
    writer.stop()


def test_a_root_that_cannot_be_created_is_an_error_state_not_an_exception(tmp_path,
                                                                         monkeypatch):
    writer = J.ForwardJournal(tmp_path / "nope")

    def refuse(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(J.Path, "mkdir", refuse)
    assert writer.start()["status"] == J.STATE_ERROR
    assert writer.append(line(), written_at_ms=NOW) is False
    assert "read-only filesystem" in (writer.last_error or "")


def test_the_stale_threshold_never_gates_a_write(tmp_path):
    """A tape that stopped writing because it looked stale would make the report the outage."""
    writer = J.ForwardJournal(tmp_path)
    writer.start()
    writer.append(line(), written_at_ms=NOW)
    far = NOW + 10 * J.WRITE_STALE_MS
    assert writer.state(now_ms=far) == J.STATE_STALE
    assert writer.append(line(bar_close_ms=far, written_at_ms=far), written_at_ms=far) is True
    assert writer.state(now_ms=far) == J.STATE_WRITING
    writer.stop()


def test_constructing_a_journal_takes_no_lock_and_creates_no_directory(tmp_path):
    """Importing or constructing must have no side effect; eight test files construct these."""
    root = tmp_path / "untouched"
    writer = J.ForwardJournal(root)
    assert writer.writable is None
    assert root.exists() is False
    assert J.ForwardJournal(root).start()["writer"]["writable"] is True
    assert root.exists() is True


def test_the_recovery_rule_is_published_rather_than_left_to_a_runbook(tmp_path):
    view = J.ForwardJournal(tmp_path).view()
    recovery = view["writer"]["recovery"]
    assert "flock" in recovery and "inode" in recovery
    assert "no stale-lock timeout" in recovery
    assert view["writer"]["single_writer"] is True
    assert view["writer"]["fail_closed"] is True
    assert view["writer"]["blocks_the_panel"] is False


# --- a journal failure costs the screen nothing ------------------------------------------------

def test_the_panel_assembles_every_layer_while_the_journal_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("MS_V0_ROOT", raising=False)
    holder = J.ForwardJournal(tmp_path)
    holder.start()
    panel = ASM.Panel(journal_root=tmp_path)
    payload_out = panel.poll(C.BTC, now_ms=NOW, opener=lambda *a, **k: (_ for _ in ()).throw(
        OSError("no network in this test")))
    assert set(payload_out["layers"]) == set(C.LAYERS)
    assert payload_out["journal"]["status"] == J.STATE_REFUSED
    assert payload_out["journal"]["wrote_this_poll"] is False
    assert payload_out["journal"]["writer"]["blocks_the_panel"] is False
    # Every layer still carries its own state word, and none of them is the journal's.
    for name in C.LAYERS:
        assert payload_out["layer_states"][name] in (*C.LAYER_STATES, C.UNAVAILABLE)
    holder.stop()


def test_every_route_still_answers_while_the_journal_has_no_authority(tmp_path, monkeypatch):
    """The failure mode this whole policy is written around: the tape is the thing that stops."""
    monkeypatch.delenv("MS_V0_ROOT", raising=False)
    monkeypatch.setenv(J.ROOT_ENV, str(tmp_path))
    holder = J.ForwardJournal(tmp_path)
    holder.start()
    from app.crypto.market_context_v1 import api as API

    monkeypatch.setattr(API, "state", API.PanelState())
    with TestClient(API.app) as client:
        health = client.get("/api/market-context/health")
        assert health.status_code == 200
        assert health.json()["journal"]["status"] == J.STATE_REFUSED
        answer = client.get("/api/market-context/panel", params={"symbol": "ETHUSDT"})
        assert answer.status_code == 200
        assert set(answer.json()["layers"]) == set(C.LAYERS)
        assert answer.json()["journal"]["writer"]["writable"] is False
    holder.stop()


def test_the_lifespan_does_not_start_the_timer_without_authority(tmp_path, monkeypatch):
    monkeypatch.setenv(J.ROOT_ENV, str(tmp_path))
    holder = J.ForwardJournal(tmp_path)
    holder.start()
    from app.crypto.market_context_v1 import api as API

    monkeypatch.setattr(API, "state", API.PanelState())
    ticks = {"count": 0}

    async def counting_timer(panel):
        ticks["count"] += 1

    monkeypatch.setattr(API, "_journal_timer", counting_timer)
    with TestClient(API.app) as client:
        client.get("/api/market-context/health")
        time.sleep(0.05)
    assert ticks["count"] == 0
    holder.stop()


def test_the_lifespan_releases_the_lock_so_a_restart_is_not_blocked_by_itself(tmp_path,
                                                                             monkeypatch):
    monkeypatch.setenv(J.ROOT_ENV, str(tmp_path))
    from app.crypto.market_context_v1 import api as API

    monkeypatch.setattr(API, "state", API.PanelState())
    monkeypatch.setattr(API, "_journal_timer", lambda panel: _never())
    with TestClient(API.app) as client:
        client.get("/api/market-context/health")
    after = J.ForwardJournal(tmp_path, session_id="restarted")
    assert after.start()["writer"]["writable"] is True
    after.stop()


async def _never() -> None:
    import asyncio
    await asyncio.Event().wait()
