"""Q1/Q2/Q3 Forward Recorder (`q123-forward-recorder.v1`).

What is pinned here: the incremental reader yields exactly the seconds the collector's batch
reader yields; the recorder stores exactly what the batch detector computes, once; nothing it
stores is ever rewritten; a crash at any write point, a deleted or corrupted cursor, or a replayed
input loses nothing and duplicates nothing; a session boundary is never bridged.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.crypto.context_collector_v1 import reader as CR
from app.crypto.research.q123_forward_v1 import analyze as AN
from app.crypto.research.q123_forward_v1 import contract as K
from app.crypto.research.q123_forward_v1 import detect as D
from app.crypto.research.q123_forward_v1 import frames as F
from app.crypto.research.q123_forward_v1 import outcome as O
from app.crypto.research.q123_forward_v1 import recorder as RC
from app.crypto.research.q123_forward_v1 import store as STO
from app.crypto.research.q123_forward_v1 import stream as ST
from tests.crypto import q123_recorder_fixtures as FX

PKG = Path(__file__).resolve().parents[2] / "app/crypto/research/q123_forward_v1"
BACKEND = Path(__file__).resolve().parents[2]
MIN = 60_000
ASK_WALL = ((99_950.0, "ASK"),)
APPROACH = [(FX.T0, "RES", 7, FX.LEVEL)]


# ----------------------------------------------------------------------------- helpers
def read_all(root: Path, limit: int) -> list[dict]:
    cursors: dict[str, ST.SessionCursor] = {}
    out = []
    order = ST.sessions_in_order(root, cursors)
    for idx, s8 in enumerate(order):
        c = cursors.setdefault(s8, ST.SessionCursor(s8=s8))
        reader = ST.SessionReader(root, c)
        while True:
            got, progressed = reader.step(session_over=idx < len(order) - 1, limit=limit)
            out.extend(dataclasses.asdict(x) for x in got)
            if not progressed:
                break
    return out


def reference(root: Path) -> list[dict]:
    return [dataclasses.asdict(x) for x in ST.iterate_reference(root)]


def two_sessions(root: Path, *, torn=False):
    a = FX.session(root, "a", FX.T0 + MIN - 30_000, 1500, walls=ASK_WALL, rotate=300)
    a.wall(FX.T0 + 400_000, side="ASK", price=99_950.0, event="ENDED", status="ENDED")
    a.sample(FX.T0 + MIN - 30_000 + 1500 * 1000, FX.LEVEL * 0.999)
    a.flush(close=True)
    b = FX.session(root, "b", FX.T0 + 40 * MIN, 900, walls=ASK_WALL, rotate=250, torn=torn)
    return a, b


@pytest.fixture
def stub(monkeypatch):
    def install(approaches=APPROACH):
        FX.install_stub(RC, approaches, setter=monkeypatch.setattr)
    return install


def run_recorder(root, tmp, *, cycles=200):
    rec = RC.Recorder(root, tmp / "store", tmp / "rt", fault=RC.Faults(None), now=lambda: 0)
    for _ in range(cycles):
        rep = rec.cycle(poll_klines=False)
        if not (rep["samples"] or rep["approaches"] or rep["events"] or rep["outcomes"]):
            rep = rec.cycle(poll_klines=False)
            if not (rep["samples"] or rep["approaches"] or rep["events"] or rep["outcomes"]):
                break
    rec._checkpoint()
    return rec


def store_lines(path: Path) -> dict[str, dict[str, str]]:
    out = {}
    for name in STO.FILES:
        p = path / name
        out[name] = {json.loads(l)["key"]: l for l in p.read_text().splitlines()} \
            if p.exists() else {}
    return out


# ----------------------------------------------------------------------------- incremental reader
@pytest.mark.parametrize("limit", [1, 7, 333, 100_000])
def test_stream_yields_exactly_the_batch_readers_seconds(tmp_path, limit):
    two_sessions(tmp_path)
    ref = reference(tmp_path)
    got = read_all(tmp_path, limit)
    assert got == ref[:len(got)]
    # the live session keeps back the last HOLDBACK_MS of samples, nothing else
    assert 0 < len(ref) - len(got) <= ST.HOLDBACK_MS // 1000 + 1


def test_torn_line_of_a_live_file_is_never_consumed(tmp_path):
    two_sessions(tmp_path, torn=True)
    got = read_all(tmp_path, 50)
    assert got == reference(tmp_path)[:len(got)]


def test_cursor_round_trip_resumes_byte_for_byte(tmp_path):
    two_sessions(tmp_path)
    whole = read_all(tmp_path, 10_000)
    order = ST.sessions_in_order(tmp_path, {})
    c = ST.SessionCursor(s8=order[0])
    first, _ = ST.SessionReader(tmp_path, c).step(session_over=True, limit=400)
    c2 = ST.SessionCursor.from_json(json.loads(json.dumps(c.to_json())))
    rest = []
    reader = ST.SessionReader(tmp_path, c2)
    while True:
        got, progressed = reader.step(session_over=True, limit=97)
        rest.extend(got)
        if not progressed:
            break
    seq = [dataclasses.asdict(x) for x in first + rest]
    assert seq == whole[:len(seq)] and len(seq) == 1501


def test_a_session_without_its_first_segment_is_refused_not_guessed(tmp_path):
    FX.session(tmp_path, "a", FX.T0, 1200, walls=ASK_WALL, rotate=200, close=True)
    for p in (tmp_path / "wall_r0").glob("*-00001.*"):
        p.unlink()
    for p in (tmp_path / "context").glob("*-00001.*"):
        p.unlink()
    c = ST.SessionCursor(s8="aaaaaaaa")
    with pytest.raises(ST.UnrecoverableStart):
        ST.SessionReader(tmp_path, c).step(session_over=True)


def test_collector_root_is_never_written(tmp_path, stub):
    stub()
    two_sessions(tmp_path / "root")
    before = {p: p.stat().st_mtime_ns for p in (tmp_path / "root").rglob("*")}
    run_recorder(tmp_path / "root", tmp_path)
    after = {p: p.stat().st_mtime_ns for p in (tmp_path / "root").rglob("*")}
    assert before == after


# ----------------------------------------------------------------------------- recorder = detector
def test_recorder_stores_what_the_batch_detector_computes(tmp_path, stub):
    stub()
    FX.session(tmp_path / "root", "a", FX.T0 + MIN - 30_000, 4500, walls=ASK_WALL, flow=0.4,
               close=True)
    rec = run_recorder(tmp_path / "root", tmp_path)
    ev = list(rec.store.rows[STO.EVENTS].values())
    assert [(e["question"], e["fires"]) for e in ev] == [("Q1", True), ("Q2", False)]
    # the same detector on the whole frame
    sx = F.second_frame([tmp_path / "root"])
    mf = RC.window_minute_frame(None, K.PUBLISH_FROM_MS, FX.T0 + 600 * MIN, [])
    recs, _ = D.detect(mf, sx)
    assert sorted(STO.canonical(e["detector"]) for e in ev) == \
        sorted(STO.canonical(r) for r in recs)
    idx = {int(m): k for k, m in enumerate(sx.frame.ms)}
    for e in ev:
        for h in K.HORIZONS_MIN:
            o = rec.store.rows[STO.OUTCOMES][f'{e["event_id"]}:{h}']
            want = O.outcome(sx.frame, e["detector"]["lean"], e["structure_price"],
                             e["structure_side"], h, decision_index=idx[e["decision_ts"]])
            want.update(event_id=e["event_id"], question=e["question"], fires=e["fires"])
            assert o["detector"] == want and o["status"] == "MEASURED"
            assert o["net_bp"] == pytest.approx(o["direction_normalized_bp"] - 11.25, abs=1e-3)
            assert o["cost_bp"] == 11.25
            assert o["role"] == ("DIAGNOSTIC" if h == 30 else "PRIMARY_GRID")


def test_event_schema_and_no_state_verdict(tmp_path, stub):
    stub()
    FX.session(tmp_path / "root", "a", FX.T0 + MIN - 30_000, 4000, walls=ASK_WALL, close=True)
    rec = run_recorder(tmp_path / "root", tmp_path)
    for e in rec.store.rows[STO.EVENTS].values():
        for k in ("event_id", "question", "direction", "event_ts", "anchor_ts", "decision_ts",
                  "collector_session_id", "structure_level_id", "structure_price",
                  "structure_side", "wall_bin", "wall_state", "wall_persistence_ms_at_anchor",
                  "flow_state", "coverage_state", "generation", "reference_price", "validity",
                  "invalid_reason", "diagnostics"):
            assert k in e
        assert e["direction"] in ("UP", "DOWN")
        assert e["diagnostics"]["used_in_trigger"] is False
        top = {k: v for k, v in e.items() if k != "detector"}
        assert not any(v in ("LONG", "SHORT", "WAIT") for v in top.values() if isinstance(v, str))
    for o in rec.store.rows[STO.OUTCOMES].values():
        for k in ("status", "reachable", "target_ts", "observed_ts", "reference_price",
                  "exit_price", "raw_move_bp", "direction_normalized_bp", "gross_bp", "cost_bp",
                  "net_bp", "MFE_bp", "MAE_bp", "high_excursion_bp", "low_excursion_bp",
                  "missing_reason"):
            assert k in o


def test_rerun_on_the_same_input_appends_nothing(tmp_path, stub):
    stub()
    FX.session(tmp_path / "root", "a", FX.T0 + MIN - 30_000, 4500, walls=ASK_WALL, close=True)
    run_recorder(tmp_path / "root", tmp_path)
    before = {n: (tmp_path / "store" / n).read_bytes() for n in STO.FILES}
    shutil.rmtree(tmp_path / "rt")                        # cursor deleted: full replay
    run_recorder(tmp_path / "root", tmp_path)
    run_recorder(tmp_path / "root", tmp_path)             # and with the cursor
    assert {n: (tmp_path / "store" / n).read_bytes() for n in STO.FILES} == before
    assert not (tmp_path / "store" / STO.CONFLICTS).exists()


def test_growing_journal_fills_only_the_missing_horizons(tmp_path, stub):
    stub()
    root = tmp_path / "root"
    j = FX.Journal(root, FX.sid("a"))
    j.wall(FX.T0, side="ASK", price=99_950.0, first_seen_ms=FX.T0 - 300_000)
    start = FX.T0 + MIN - 30_000
    for k in range(1300):                                # enough for 5m and 15m, not 30m
        j.sample(start + k * 1000, FX.LEVEL * 0.999)
    j.flush()
    run_recorder(root, tmp_path)
    first = (tmp_path / "store" / STO.OUTCOMES).read_bytes()
    hs = sorted({json.loads(l)["horizon_min"] for l in first.decode().splitlines()})
    assert hs == [5, 15]
    for k in range(1300, 4400):
        j.sample(start + k * 1000, FX.LEVEL * 0.999)
    j.flush()
    run_recorder(root, tmp_path)
    after = (tmp_path / "store" / STO.OUTCOMES).read_bytes()
    assert after.startswith(first)                      # nothing earlier rewritten
    hs = sorted(json.loads(l)["horizon_min"] for l in after[len(first):].decode().splitlines())
    assert set(hs) == {30, 60}


# ----------------------------------------------------------------------------- session boundary
def test_outcome_crossing_a_session_end_is_missing_never_bridged(tmp_path, stub):
    stub()
    root = tmp_path / "root"
    FX.session(root, "a", FX.T0 + MIN - 30_000, 1200, walls=ASK_WALL, close=True)  # 20 minutes
    FX.session(root, "b", FX.T0 + 25 * MIN, 4000, walls=ASK_WALL, close=True)
    rec = run_recorder(root, tmp_path)
    q1 = next(e for e in rec.store.rows[STO.EVENTS].values() if e["question"] == "Q1")
    st = {h: rec.store.rows[STO.OUTCOMES][f'{q1["event_id"]}:{h}'] for h in K.HORIZONS_MIN}
    assert st[5]["status"] == "MEASURED" and st[15]["status"] == "MEASURED"
    for h in (30, 60):
        assert st[h]["status"] == "MISSING" and st[h]["missing_reason"] == "SESSION_BOUNDARY"
        assert st[h]["net_bp"] is None and st[h]["exit_price"] is None


def test_trigger_window_in_a_gap_between_sessions_is_excluded(tmp_path, stub):
    stub([(FX.T0 + 30 * MIN, "RES", 7, FX.LEVEL)])
    root = tmp_path / "root"
    FX.session(root, "a", FX.T0, 20 * 60, walls=ASK_WALL, close=True)
    FX.session(root, "b", FX.T0 + 45 * MIN, 3000, walls=ASK_WALL, close=True)
    rec = run_recorder(root, tmp_path)
    st = sorted(a["status"] for a in rec.store.rows[STO.APPROACHES].values())
    assert st == ["NO_SAMPLE_AT_MINUTE", "NO_SAMPLE_AT_MINUTE"]
    assert not rec.store.rows[STO.EVENTS]


def test_decision_across_a_session_end_is_excluded(tmp_path, stub):
    stub()
    root = tmp_path / "root"
    FX.session(root, "a", FX.T0 + MIN - 30_000, 60, walls=ASK_WALL, close=True)   # ends at +30s
    FX.session(root, "b", FX.T0 + MIN + 31_000, 4000, walls=ASK_WALL, close=True)
    rec = run_recorder(root, tmp_path)
    st = sorted(a["status"] for a in rec.store.rows[STO.APPROACHES].values())
    assert st == ["NO_DECISION_SAMPLE", "NO_DECISION_SAMPLE"]


def test_generation_change_inside_w_is_invalid_with_reason(tmp_path, stub):
    stub()
    FX.session(tmp_path / "root", "a", FX.T0 + MIN - 30_000, 4000, walls=ASK_WALL,
               gen=lambda k: 1 if k < 60 else 2, close=True)
    rec = run_recorder(tmp_path / "root", tmp_path)
    for e in rec.store.rows[STO.EVENTS].values():
        assert e["validity"] == "INVALID" and e["invalid_reason"] == K.GENERATION_CHANGED
        assert not e["fires"]
    assert not rec.store.rows[STO.OUTCOMES]


@pytest.mark.parametrize("event,status", [("ENDED", "ENDED"),                  # CANCEL_LIKE
                                          ("INTERRUPTED", "INTERRUPTED")])     # OUT_OF_COVERAGE
def test_vanished_wall_without_trade_through_is_not_consumption(tmp_path, stub, event, status):
    # a wall gone at W with mid never reaching its bin is never a consumption, whatever ended it
    stub()
    root = tmp_path / "root"
    j = FX.Journal(root, FX.sid("a"))
    j.wall(FX.T0, side="ASK", price=99_950.0, first_seen_ms=FX.T0 - 300_000)
    start = FX.T0 + MIN - 30_000
    for k in range(4000):
        if k == 60:
            j.wall(start + k * 1000 - 1, side="ASK", price=99_950.0, event=event,
                   status=status)
        j.sample(start + k * 1000, FX.LEVEL * 0.999, flow=0.4)
    j.flush(close=True)
    rec = run_recorder(root, tmp_path)
    q2 = next(e for e in rec.store.rows[STO.EVENTS].values() if e["question"] == "Q2")
    assert q2["wall_state"] == "GONE_AT_W_NOT_TRADED_THROUGH" and not q2["fires"]


# ----------------------------------------------------------------------------- store safety
def test_store_cuts_only_a_torn_tail_and_refuses_corruption(tmp_path):
    s = STO.Store.open(tmp_path, create_meta={"schema": "x"})
    s.append(STO.EVENTS, [{"key": "a", "v": 1}, {"key": "b", "v": 2}])
    with open(tmp_path / STO.EVENTS, "ab") as h:
        h.write(b'{"key":"c","v"')
    s2 = STO.Store.open(tmp_path)
    assert s2.repaired == {STO.EVENTS: len(b'{"key":"c","v"')}
    assert list(s2.keys[STO.EVENTS]) == ["a", "b"]
    with open(tmp_path / STO.EVENTS, "ab") as h:
        h.write(b'not json\n')
    with pytest.raises(STO.StoreCorrupt):
        STO.Store.open(tmp_path)


def test_store_never_replaces_a_fact(tmp_path):
    s = STO.Store.open(tmp_path, create_meta={"schema": "x"})
    s.append(STO.OUTCOMES, [{"key": "e:5", "net_bp": 1.0}])
    before = (tmp_path / STO.OUTCOMES).read_bytes()
    rep = s.append(STO.OUTCOMES, [{"key": "e:5", "net_bp": 2.0}, {"key": "e:5", "net_bp": 1.0}])
    assert rep["conflict"] == 1 and rep["new"] == 0
    assert (tmp_path / STO.OUTCOMES).read_bytes() == before
    assert (tmp_path / STO.CONFLICTS).exists()


def test_no_retention_in_the_store_or_recorder():
    for name in ("store.py", "recorder.py", "analyze.py"):
        tree = ast.parse((PKG / name).read_text())
        calls = {getattr(n.func, "attr", getattr(n.func, "id", None))
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert not calls & {"rmtree", "remove", "rmdir"}, name
    # the only unlink is of the recorder's own rebuildable checkpoints
    src = (PKG / "recorder.py").read_text()
    assert src.count(".unlink(") == 1 and "checkpoints" in src


# ----------------------------------------------------------------------------- crash recovery
DRIVER = """
import sys
sys.path.insert(0, {backend!r})
from tests.crypto import q123_recorder_fixtures as FX
from app.crypto.research.q123_forward_v1 import recorder as RC
FX.install_stub(RC, {approaches!r})
sys.exit(RC.main(["record", "--root", {root!r}, "--store", {store!r}, "--runtime", {rt!r},
                  "--klines-file", "unused", "--until-idle"]))
"""


def drive(root, out, *, fault=None, approaches=APPROACH):
    code = DRIVER.format(backend=str(BACKEND), approaches=approaches, root=str(root),
                         store=str(out / "store"), rt=str(out / "rt"))
    env = dict(os.environ)
    env.pop(RC.FAULT_ENV, None)
    if fault:
        env[RC.FAULT_ENV] = fault
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                          text=True, cwd=str(BACKEND)).returncode


@pytest.fixture(scope="module")
def crash_root(tmp_path_factory):
    base = tmp_path_factory.mktemp("crash")
    root = base / "root"
    FX.session(root, "a", FX.T0 + MIN - 30_000, 2400, walls=ASK_WALL, close=True)
    FX.session(root, "b", FX.T0 + 50 * MIN, 5200, walls=ASK_WALL, close=True)
    approaches = APPROACH + [(FX.T0 + 120 * MIN, "RES", 8, FX.LEVEL)]
    clean = base / "clean"
    assert drive(root, clean, approaches=approaches) == 0
    return root, store_lines(clean / "store"), approaches


@pytest.mark.parametrize("fault", [
    "before_append:events.jsonl", "after_append:events.jsonl", "torn_append:events.jsonl",
    "before_append:approaches.jsonl", "after_append:approaches.jsonl",
    "after_append:outcomes.jsonl@1", "after_append:outcomes.jsonl@2",
    "torn_append:outcomes.jsonl@2", "after_append:quality.jsonl@1",
])
def test_crash_at_each_write_point_loses_and_duplicates_nothing(tmp_path, crash_root, fault):
    root, clean, approaches = crash_root
    assert drive(root, tmp_path, fault=fault, approaches=approaches) == 137
    crashed = {n: (tmp_path / "store" / n).read_bytes() if (tmp_path / "store" / n).exists()
               else b"" for n in STO.FILES}
    assert drive(root, tmp_path, approaches=approaches) == 0
    got = store_lines(tmp_path / "store")
    assert got == clean                                     # no loss, no duplicate, same bytes
    for n in STO.FILES:                                     # completed lines were kept as written
        whole = crashed[n][:crashed[n].rfind(b"\n") + 1]
        assert (tmp_path / "store" / n).read_bytes().startswith(whole)


@pytest.mark.parametrize("damage", ["delete_runtime", "corrupt_checkpoints", "duplicate_replay"])
def test_cursor_loss_or_corruption_or_replay_is_harmless(tmp_path, crash_root, damage):
    root, clean, approaches = crash_root
    assert drive(root, tmp_path, fault="after_append:outcomes.jsonl@2",
                 approaches=approaches) == 137
    assert drive(root, tmp_path, approaches=approaches) == 0
    if damage == "delete_runtime":
        shutil.rmtree(tmp_path / "rt")
    elif damage == "corrupt_checkpoints":
        for p in (tmp_path / "rt" / "checkpoints").glob("*.json"):
            p.write_text(p.read_text()[:40] + "garbage")
    assert drive(root, tmp_path, approaches=approaches) == 0
    assert store_lines(tmp_path / "store") == clean


# ----------------------------------------------------------------------------- analyzer
def test_analyzer_is_the_contract_statistics_on_the_store(tmp_path, stub):
    stub()
    FX.session(tmp_path / "root", "a", FX.T0 + MIN - 30_000, 4500, walls=ASK_WALL, close=True)
    run_recorder(tmp_path / "root", tmp_path)
    out = AN.analyze(tmp_path / "store")
    assert {(g["question"], g["horizon_min"]) for g in out["grid_holm"]} == \
        {(q, h) for q in K.QUESTIONS for h in (5, 15, 60)}
    assert out["questions"]["Q1"]["horizons"]["30"]["role"] == "DIAGNOSTIC_ONLY"
    assert out["questions"]["Q1"]["stage"] == "INSUFFICIENT"
    assert all(r["automatic_promotion"] == "PROHIBITED" for r in out["signal_review_inputs"])
    win = AN.summary_window(tmp_path / "store", None, None)
    assert win["questions"]["Q1"]["fired"] == 1 and win["questions"]["Q1"]["valid"] == 1
    # only hours that are final (a later sample exists) are in the ledger: 30 s .. 3600 s
    assert win["quality"]["samples"] == 3570 and win["quality"]["hours"] == 1


def test_holm_adjusted_p_is_monotone_step_down():
    grid = [{"p_value": p} for p in (0.01, 0.04, 0.03, None)]
    AN.holm_adjusted(grid)
    assert [g["holm_adjusted_p"] for g in grid] == [0.03, 0.06, 0.06, None]


# ----------------------------------------------------------------------------- isolation
FORBIDDEN = ("terminal", "live", "paper", "c1", "order", "account", "leverage", "auto", "broker",
             "execution", "exit_guard", "exitguard")


def test_recorder_loads_no_order_account_or_trading_module():
    code = ("import sys, app.crypto.research.q123_forward_v1.recorder as r, "
            "app.crypto.research.q123_forward_v1.analyze as a; "
            "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('app'))))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=BACKEND, check=True).stdout.split()
    assert [m for m in out if any(p in FORBIDDEN for p in m.split("."))] == []
    assert not any(m.startswith(("app.crypto.paper", "app.crypto.binance", "app.services"))
                   for m in out)


def test_network_use_is_the_public_kline_endpoint_only():
    for name in ("stream.py", "store.py", "recorder.py", "analyze.py"):
        src = (PKG / name).read_text()
        assert "urllib" not in src and "http" not in src.replace("https://fapi", "")
    assert F.KLINES_URL == "https://fapi.binance.com/fapi/v1/klines"
