"""MethodJudgement IO P1: the shared share-class reference cache answers exactly what the frozen loader answers,
reads each snapshot file once, and refuses lookups it was not built for."""

from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path

import pytest

from app.backtest.strategy_h_v2.valuation import share_class as SC
from app.strategies.h_auto import inputs as IN


def _row(ticker, cik, figi="F"):
    return {"ticker": ticker, "cik": cik, "type": "CS", "market": "stocks", "locale": "us", "active": True,
            "composite_figi": f"{figi}{ticker}", "share_class_figi": f"S{ticker}", "primary_exchange": "XNYS"}


def _write(directory: Path, as_of: str, rows):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"CS_{as_of}.json.gz").write_bytes(gzip.compress(json.dumps({"results": rows}).encode()))


@pytest.fixture
def dirs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _write(a, "2026-01-02", [_row("X", "0000000001"), _row("Y", "0000000002")])
    _write(a, "2026-04-01", [_row("X", "0000000001"), _row("XB", "0000000001", "G")])
    _write(b, "2026-04-01", [_row("Z", "0000000003")])            # same as_of in a later dir: first wins
    _write(b, "2026-07-01", [_row("X", "0000000001")])
    (a / "CS_bad-name.json.gz").write_bytes(b"x")
    return (a, b)


@pytest.mark.parametrize("day", [date(2025, 12, 31), date(2026, 1, 2), date(2026, 3, 31), date(2026, 4, 1),
                                 date(2026, 6, 30), date(2026, 7, 1), date(2027, 1, 1)])
def test_same_choice_and_same_answer_as_the_frozen_loader(dirs, day):
    cache = IN.ReferenceSnapshotCache(dirs)
    old, new = SC.load_reference_snapshot(day, dirs), cache.get(day)
    assert (old is None) == (new is None)
    if old is not None:
        assert (new.as_of, new.path, new.rows) == (old.as_of, old.path, old.rows)
        for cik in ("0000000001", "0000000002", "0000000003", "0000000009"):
            assert new.securities(cik) == old.securities(cik)


def test_each_file_is_parsed_once_no_hidden_reread(dirs, monkeypatch):
    calls = []
    real = SC.load_reference_snapshot
    monkeypatch.setattr(SC, "load_reference_snapshot", lambda d, ds: calls.append(d) or real(d, ds))
    cache = IN.ReferenceSnapshotCache(dirs)
    for day in [date(2026, 1, 5)] * 50 + [date(2026, 5, 1)] * 50 + [date(2026, 8, 1)] * 50:
        cache.get(day)
    assert len(calls) == 3 and cache.loads == 3


def test_narrowed_cache_refuses_an_unexpected_cik_instead_of_answering_absent(dirs):
    cache = IN.ReferenceSnapshotCache(dirs, ciks=["1"])
    snap = cache.get(date(2026, 4, 2))
    full = SC.load_reference_snapshot(date(2026, 4, 2), dirs)
    assert snap.securities("0000000001") == full.securities("0000000001")
    assert len(snap.securities("0000000001")) == 2
    with pytest.raises(IN.ReferenceCacheMiss):
        snap.securities("0000000002")
    gone = IN.ReferenceSnapshotCache(dirs, ciks=["0000000002"]).get(date(2026, 7, 2))
    assert gone.securities("0000000002") == ()                    # wanted but absent: the loader's own answer


def test_cached_snapshot_is_immutable(dirs):
    snap = IN.ReferenceSnapshotCache(dirs, ciks=["0000000001"]).get(date(2026, 4, 2))
    with pytest.raises(Exception):
        snap.as_of = date(2020, 1, 1)
    with pytest.raises(TypeError):
        snap.by_cik["0000000001"] = ()


def test_binding_uses_the_shared_cache_and_restores_the_loader(dirs, monkeypatch, tmp_path):
    from app.dev import run_strategy_h_v2_d5_d2 as D52
    monkeypatch.setattr(IN, "d5_reference_dirs", lambda: dirs)
    before = D52.load_reference_snapshot
    cache = IN.ReferenceSnapshotCache(dirs, ciks=["0000000001"])
    panel = IN.Panel(bars={"SPY": {date(2026, 4, 1): 1.0, date(2026, 4, 2): 1.0}}, until=date(2026, 4, 2))
    with IN.d5_binding(packages_dir=tmp_path, panel=panel, facts_roots=[tmp_path], submission_roots=[tmp_path],
                       reference_cache=cache):
        assert D52.load_reference_snapshot == cache.get
        assert D52.load_reference_snapshot(date(2026, 4, 2)) is D52.load_reference_snapshot(date(2026, 4, 3))
    assert D52.load_reference_snapshot is before
    other = IN.ReferenceSnapshotCache([tmp_path])
    with pytest.raises(RuntimeError):
        with IN.d5_binding(packages_dir=tmp_path, panel=panel, facts_roots=[tmp_path], submission_roots=[tmp_path],
                           reference_cache=other):
            pass


def test_no_snapshot_on_or_before_is_none(dirs):
    assert IN.ReferenceSnapshotCache(dirs).get(date(2020, 1, 1)) is None
    assert IN.ReferenceSnapshotCache([Path("/nonexistent")]).get(date(2026, 1, 1)) is None
