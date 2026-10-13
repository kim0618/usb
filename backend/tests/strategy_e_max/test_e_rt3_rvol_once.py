"""E-RT3 ``once``: the timer form of ``serve`` makes the same passes and writes the same files.

No Kiwoom call is made: ``_prepare`` and ``_pass`` are replaced with recorders, and the queue is
scripted cycle by cycle the way the store would answer it.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.dev import run_e_rt3_rvol as R

NOW = datetime(2026, 10, 13, 0, 35, 7, tzinfo=R.ET)


class _Now(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


class _Store:
    def coverage(self, symbols, before):
        return {"fully_ready": 3, "symbols": 3, "partially_ready": 0, "zero_history": 0}

    def exhausted_symbols(self):
        return set()

    def close(self):
        pass


class _Stop(Exception):
    pass


def _harness(monkeypatch, queues):
    """``queues`` is one (work, fresh) pair per cycle; returns the list _pass is called with."""
    calls, cycle = [], iter(queues)
    monkeypatch.setattr(R, "datetime", _Now)
    monkeypatch.setattr(R, "in_guard", lambda now=None: False)
    monkeypatch.setattr(R, "_prepare", lambda args: (_Store(), "lane", ["A", "B", "C"], ["A", "B", "C"],
                                                     {}, {}, {}, "d" * 16))
    monkeypatch.setattr(R, "_queue", lambda *a, **k: next(cycle))

    def fake_pass(store, lane, work, *, before, target, workers, deadline, label):
        calls.append((label, tuple(work), before.isoformat(), target, workers))
        return {"label": label, "symbols": len(work), "queued": len(work), "pages": 1,
                "staged_written": 1, "requests_per_second": 1.0, "errors": {}}

    monkeypatch.setattr(R, "_pass", fake_pass)

    def no_sleep(seconds):
        raise _Stop

    monkeypatch.setattr(R.time, "sleep", no_sleep)
    return calls


def _args(out: Path) -> SimpleNamespace:
    return SimpleNamespace(out=out, target=20, workers=3, rate=4.6, store=Path("s.sqlite3"))


def _files(out: Path) -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(out.iterdir())}


QUEUES = [([("A", "A", "NY")], [("B", "B", "NY"), ("C", "C", "ND")]),   # append, then bootstrap A
          ([("A", "A", "NY")], [("B", "B", "NY"), ("C", "C", "ND")]),   # A still short: bootstrap again
          ([], [("A", "A", "NY"), ("B", "B", "NY"), ("C", "C", "ND")])]  # nothing left: serve idles


def test_once_matches_serve_until_serve_would_sleep(monkeypatch, tmp_path):
    serve_out, once_out = tmp_path / "serve", tmp_path / "once"
    serve_calls = _harness(monkeypatch, QUEUES)
    with pytest.raises(_Stop):
        R.serve(_args(serve_out))
    once_calls = _harness(monkeypatch, QUEUES)
    assert R.once(_args(once_out)) == 0

    assert once_calls == serve_calls
    assert [c[0] for c in serve_calls] == ["append", "bootstrap", "bootstrap"]
    assert serve_calls[0][2] == "2026-10-14" and serve_calls[0][3] == 1      # today's session included
    assert _files(once_out) == _files(serve_out)
    assert "append_20261013T003507.json" in _files(once_out)


def test_second_run_on_the_same_et_day_does_not_append_again(monkeypatch, tmp_path):
    out = tmp_path / "passes"
    out.mkdir()
    (out / "append_20261013T003507.json").write_text("{}\n")
    (out / "append_20261012T002101.json").write_text("{}\n")
    calls = _harness(monkeypatch, [([], [("A", "A", "NY")])])
    assert R.once(_args(out)) == 0
    assert calls == []


def test_previous_day_report_does_not_block_todays_append(monkeypatch, tmp_path):
    out = tmp_path / "passes"
    out.mkdir()
    (out / "append_20261012T002101.json").write_text("{}\n")
    calls = _harness(monkeypatch, [([], [("A", "A", "NY")])])
    assert R.once(_args(out)) == 0
    assert [c[0] for c in calls] == ["append"]


def test_once_in_the_guard_window_exits_without_touching_the_api(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "in_guard", lambda now=None: True)

    def boom(args):
        raise AssertionError("no API call in the guard window")

    monkeypatch.setattr(R, "_prepare", boom)
    assert R.once(_args(tmp_path / "passes")) == 0


def test_appended_sessions_ignores_foreign_names(tmp_path):
    for name in ("append_20261013T003507.json", "append_bad.json", "bootstrap_20261013T010000.json",
                 "coverage_latest.json"):
        (tmp_path / name).write_text("{}")
    assert R._appended_sessions(tmp_path) == {"2026-10-13"}
