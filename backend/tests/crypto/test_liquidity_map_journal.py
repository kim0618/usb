"""Reading a journal that is still being written to.

Every test here is one way a reader of this journal fails quietly: trusting a torn trailing line,
losing a cursor across a rotation, picking the wrong session, or reading a whole file to get its
last row.
"""
from __future__ import annotations

import json

import pytest

from app.crypto.liquidity_map import journal as J

from tests.crypto.liquidity_map_fixtures import Handwriter, written_journal


def test_the_file_name_pattern_reads_every_name_the_store_writes(tmp_path):
    for name, expect_open, expect_rev in (("derived-20261004-5750e43b-00001.jsonl", False, 0),
                                          ("derived-20261004-5750e43b-00002.jsonl.open", True, 0),
                                          ("raw_depth-20261004-5750e43b-00003-r1.jsonl", False, 1)):
        parsed = J.parse_name(tmp_path / name)
        assert parsed is not None, name
        assert parsed.is_open is expect_open and parsed.rev == expect_rev

    assert J.parse_name(tmp_path / ".writer.lock") is None
    assert J.parse_name(tmp_path / "derived-20261004-5750e43b-00001.parquet") is None


def test_a_torn_trailing_line_is_not_a_record(tmp_path):
    """A buffered writer leaves a fragment after the last newline. It is not short data."""
    path = tmp_path / "wall-20261004-aaaaaaaa-00001.jsonl.open"
    path.write_bytes(b'{"seq":1}\n{"seq":2}\n{"seq":3,"half"')
    lines = [line for line, _ in J.reverse_lines(path)]
    assert [json.loads(line)["seq"] for line in lines] == [2, 1]


def test_a_file_with_no_newline_at_all_yields_nothing(tmp_path):
    path = tmp_path / "wall-20261004-aaaaaaaa-00001.jsonl.open"
    path.write_bytes(b'{"seq":1,"unfin')
    assert list(J.reverse_lines(path)) == []


def test_reverse_lines_crosses_chunk_boundaries_without_splitting_a_record(tmp_path):
    path = tmp_path / "wall-20261004-aaaaaaaa-00001.jsonl"
    rows = [json.dumps({"seq": index, "pad": "x" * 200}) for index in range(400)]
    path.write_bytes(("\n".join(rows) + "\n").encode())
    seen = [json.loads(line)["seq"] for line, _ in J.reverse_lines(path, chunk=512)]
    assert seen == list(reversed(range(400)))


def test_the_last_record_is_found_without_reading_the_whole_file(tmp_path):
    path = tmp_path / "derived"
    path.mkdir()
    rows = [json.dumps({"seq": index, "kind": "derived", "pad": "y" * 500})
            for index in range(5_000)]
    (path / "derived-20261004-aaaaaaaa-00001.jsonl").write_bytes(
        ("\n".join(rows) + "\n").encode())
    writer = Handwriter(tmp_path)
    writer.session_start()
    session = J.latest_session(tmp_path)
    record, touched = J.last_record(tmp_path, "derived", session)
    assert record is not None and record["seq"] == 4_999
    # The file is about 2.5 MB; finding its last line must not be a function of its size.
    assert touched < 4_096


def test_files_are_ordered_the_way_the_collector_wrote_them(tmp_path):
    directory = tmp_path / "wall"
    directory.mkdir()
    for name in ("wall-20261004-aaaaaaaa-00002.jsonl",
                 "wall-20261004-aaaaaaaa-00010.jsonl",
                 "wall-20261004-aaaaaaaa-00001.jsonl",
                 "wall-20261005-aaaaaaaa-00001.jsonl.open"):
        (directory / name).write_bytes(b"")
    order = [(item.date, item.seq) for item in J.stream_files(tmp_path, "wall")]
    assert order == [("20261004", 1), ("20261004", 2), ("20261004", 10), ("20261005", 1)]


def test_only_the_asked_for_session_is_returned(tmp_path):
    directory = tmp_path / "wall"
    directory.mkdir()
    (directory / "wall-20261004-aaaaaaaa-00001.jsonl").write_bytes(b"")
    (directory / "wall-20261004-bbbbbbbb-00001.jsonl").write_bytes(b"")
    assert len(J.stream_files(tmp_path, "wall", "aaaaaaaa")) == 1


def test_the_newest_session_is_chosen_by_its_recorded_start_not_by_file_age(tmp_path):
    """A previous session's files are sealed after the next one starts, so mtime lies."""
    old = Handwriter(tmp_path, session_id="11111111-0000-0000-0000-000000000000")
    old.session_start(started_ms=1_000)
    new = Handwriter(tmp_path, session_id="22222222-0000-0000-0000-000000000000")
    new.session_start(started_ms=9_000)
    # Touch the older session's file last, the way a late seal would.
    (tmp_path / "session" / "session-20261004-11111111-00001.jsonl").touch()
    assert J.latest_session(tmp_path).session8 == "22222222"


def test_an_ended_session_reports_that_it_ended(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    assert J.latest_session(tmp_path).ended is False
    writer.session_end()
    assert J.latest_session(tmp_path).ended is True


def test_an_empty_root_is_an_explicit_refusal_not_an_empty_answer(tmp_path):
    with pytest.raises(J.JournalEmpty):
        J.latest_session(tmp_path)


def test_a_cursor_resumes_where_it_stopped_and_sees_each_record_once(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    session = J.latest_session(tmp_path)
    writer.wall(side="BID", price="84700", event="OPENED", status="ACTIVE", receive_ms=1_100,
                open_file=True)
    cursor = J.Cursor()
    first, _ = cursor.advance(tmp_path, "wall", session)
    assert [record["seq"] for record in first] == [2]
    assert cursor.advance(tmp_path, "wall", session)[0] == []
    writer.wall(side="ASK", price="84800", event="OPENED", status="ACTIVE", receive_ms=1_200,
                open_file=True)
    second, _ = cursor.advance(tmp_path, "wall", session)
    assert [record["seq"] for record in second] == [3]


def test_a_cursor_carries_on_into_the_next_file_after_a_rotation(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    session = J.latest_session(tmp_path)
    writer.wall(side="BID", price="84700", event="OPENED", status="ACTIVE", receive_ms=1_100,
                path_seq=1)
    cursor = J.Cursor()
    assert len(cursor.advance(tmp_path, "wall", session)[0]) == 1
    writer.wall(side="ASK", price="84800", event="OPENED", status="ACTIVE", receive_ms=1_200,
                path_seq=2, open_file=True)
    records, _ = cursor.advance(tmp_path, "wall", session)
    assert [record["payload"]["side"] for record in records] == ["ASK"]
    assert cursor.advance(tmp_path, "wall", session)[0] == []


def test_a_cursor_placed_at_the_end_reads_nothing_already_written(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start()
    session = J.latest_session(tmp_path)
    for index in range(5):
        writer.wall(side="BID", price=f"8470{index}", event="OPENED", status="ACTIVE",
                    receive_ms=1_100 + index, open_file=True)
    cursor = J.Cursor().at_end(tmp_path, "wall", session)
    assert cursor.advance(tmp_path, "wall", session)[0] == []
    writer.wall(side="ASK", price="84800", event="OPENED", status="ACTIVE", receive_ms=2_000,
                open_file=True)
    assert len(cursor.advance(tmp_path, "wall", session)[0]) == 1


def test_a_cursor_placed_at_the_end_of_a_torn_file_re_reads_the_completed_line(tmp_path):
    directory = tmp_path / "wall"
    directory.mkdir()
    path = directory / "wall-20261004-aaaaaaaa-00001.jsonl.open"
    path.write_bytes(b'{"seq":1,"kind":"wall","payload":{}}\n{"seq":2,"kind":"wa')
    writer = Handwriter(tmp_path)
    writer.session_start()
    session = J.latest_session(tmp_path)
    cursor = J.Cursor().at_end(tmp_path, "wall", session)
    with open(path, "ab") as handle:
        handle.write(b'll","payload":{}}\n')
    records, _ = cursor.advance(tmp_path, "wall", session)
    assert [record["seq"] for record in records] == [2]


def test_the_real_writer_produces_a_journal_this_reader_can_follow(tmp_path):
    info = written_journal(tmp_path / "journal", samples=3, end_session=False, seal=False)
    session = J.latest_session(tmp_path / "journal")
    assert session.session_id == info["session_id"] and session.ended is False
    derived, _ = J.last_record(tmp_path / "journal", "derived", session)
    assert derived is not None
    assert derived["payload"]["sample_index"] == 3
    assert derived["payload"]["book"]["state"] == "SYNCED"
    telemetry, _ = J.recent_records(tmp_path / "journal", "telemetry", session, limit=5)
    assert telemetry and telemetry[0]["kind"] == "telemetry"


def test_recent_records_returns_newest_first(tmp_path):
    written_journal(tmp_path / "journal", samples=2, end_session=False, seal=False)
    session = J.latest_session(tmp_path / "journal")
    records, _ = J.recent_records(tmp_path / "journal", "telemetry", session, limit=4)
    seqs = [record["seq"] for record in records]
    assert seqs == sorted(seqs, reverse=True)
