"""BTC context strip STRUCTURE outside the API process: incremental R1 parity, the snapshot file,
the oneshot worker, and the API's reading of it.

Parity is measured against R1's own `run()` over the whole series, minute by minute. The series is
synthetic (seeded) because the frozen BTC file is not in the repository; the same comparison on
that 53-day file is recorded in `docs/crypto/btc_context_ui_v1/MEMORY_LIGHT_V1_REPORT.md`.
"""
from __future__ import annotations

import ast
import json
import os
import pickle
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.crypto.btc_context_ui_v1 import api as A
from app.crypto.btc_context_ui_v1 import structure_state as S
from app.crypto.btc_context_ui_v1 import structure_worker as W
from app.crypto.btc_context_ui_v1 import view as V
from app.crypto.market_context_v1 import contract as C
from app.crypto.market_context_v1 import structure as STR
from app.crypto.research.market_context_r0.structure import ContextEngine
from app.crypto.research.price_structure_r1.engine import EngineConfig

BACKEND = Path(__file__).resolve().parents[2]
PACKAGE = BACKEND / "app/crypto/btc_context_ui_v1"
DAY = 1440
T0 = 1_786_492_800_000                       # 2026-08-12T00:00:00Z, a UTC day start


def synthetic_rows(days: int, seed: int = 11) -> list[dict]:
    """A seeded BTC-like 1m walk with calm and violent stretches, in the venue's row shape."""
    rng = np.random.default_rng(seed)
    n = days * DAY
    vol = np.where((np.arange(n) // 600) % 5 == 3, 0.0030, 0.0008)
    drift = np.repeat(rng.normal(0, 0.0002, n // 240 + 1), 240)[:n]
    r = rng.normal(drift, vol)
    close = 80_000 * np.exp(np.cumsum(r))
    opn = np.concatenate([[80_000.0], close[:-1]])
    wick = np.abs(rng.normal(0, vol * 0.6, n))
    high = np.maximum(opn, close) * (1 + wick)
    low = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, vol * 0.6, n)))
    volume = rng.gamma(2.0, 5.0, n)
    rows = []
    for i in range(n):
        rows.append({"timestamp_ms": T0 + i * 60_000, "open": f"{opn[i]:.1f}",
                     "high": f"{high[i]:.1f}", "low": f"{low[i]:.1f}", "close": f"{close[i]:.1f}",
                     "volume": f"{volume[i]:.3f}", "turnover": f"{volume[i] * close[i]:.2f}"})
    return rows


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return synthetic_rows(7)


@pytest.fixture(scope="module")
def arrays(rows) -> dict:
    return S.rows_to_arrays(rows)


def _sub(arrays: dict, k: int) -> dict:
    return {key: value[:k] for key, value in arrays.items()}


@pytest.fixture(scope="module")
def reference(arrays):
    """R1's own run over the whole series, publishing every minute after day 5."""
    bars = S.bars_from_arrays(arrays)
    base = 5 * DAY
    engine = ContextEngine(bars, EngineConfig(C.THETA_1H, C.THETA_5M,
                                              int(arrays["ts"][base]) + 60_000,
                                              int(arrays["ts"][-1]) + 60_000))
    engine.run()
    return base, {row["info_ts"]: row for row in engine.rows}, engine


# --------------------------------------------------------------------------- parity


def test_bars_from_arrays_is_build_bars(rows, arrays):
    a, b = S.bars_from_arrays(arrays), STR.build_bars(rows)
    for tf in ("1m", "5m", "1h"):
        for field in ("ts", "open", "high", "low", "close", "volume", "turnover", "atr"):
            assert np.array_equal(getattr(a[tf], field), getattr(b[tf], field), equal_nan=True)


def test_incremental_equals_full_r1_at_every_minute(arrays, reference):
    base, ref_rows, ref = reference
    engine = S.IncrementalEngine.build(S.bars_from_arrays(_sub(arrays, base)), anchor_ms=T0)
    n = len(arrays["ts"])
    steps = [1] * 400 + [2, 3, 5, 17, 60, 241] + [1] * 200
    k, compared, restarts = base, 0, 0
    pattern = iter(steps * 10)
    while k < n:
        k2 = min(n, k + next(pattern))
        engine.step_to(S.bars_from_arrays(_sub(arrays, k2)))
        for row in engine.rows[-min(k2 - k, S.KEEP_ROWS):]:
            assert row == ref_rows[row["info_ts"]], row["info_ts"]
            compared += 1
        k = k2
        if compared % 500 == 0:                       # restart from a checkpoint now and then
            engine = pickle.loads(pickle.dumps(engine, protocol=pickle.HIGHEST_PROTOCOL))
            restarts += 1
    assert compared >= 1800 and restarts >= 2          # every step endpoint, 2 days stepped
    assert engine.processed == n
    assert len(engine.audit.violations) == 0 and len(engine.audit.vanished) == 0
    # Not vacuous: the compared span created, confirmed and expired levels, moved the nearest
    # level ids, crossed UTC days and had violent hours.
    lo = int(arrays["ts"][base]) + 60_000
    went = {t["to"] for t in ref.book.transitions if t["at"] >= lo}
    assert {"CONFIRMED", "EXPIRED"} <= went
    ids = [(r["res_id"], r["sup_id"]) for _, r in sorted(ref_rows.items())]
    assert sum(a != b for a, b in zip(ids, ids[1:])) >= 10
    assert len({ts // C.DAY_MS for ts in ref_rows}) >= 2


def test_original_series_run_matches_reference(rows, reference):
    """The 31b6f2d path (dict rows, `Series.run`) publishes the same latest row."""
    base, ref_rows, _ = reference
    for cut in (base + 1, base + 777, len(rows)):
        series = STR.Series("BTCUSDT", anchor_ms=T0)
        series.rows = rows[:cut]
        latest = series.run(publish_window_ms=10 * 60_000).latest
        assert latest == ref_rows[latest["info_ts"]]


def test_step_refuses_a_rewritten_history_and_a_gap(arrays):
    engine = S.IncrementalEngine.build(S.bars_from_arrays(_sub(arrays, 2 * DAY)), anchor_ms=T0)
    changed = {key: value[: 2 * DAY + 5].copy() for key, value in arrays.items()}
    changed["close"][100] += 1.0
    with pytest.raises(S.StructureStateError):
        engine.step_to(S.bars_from_arrays(changed))
    gap = {key: np.delete(value[: 2 * DAY + 5], 2 * DAY + 1) for key, value in arrays.items()}
    with pytest.raises(Exception):
        engine.step_to(S.bars_from_arrays(gap))
    assert engine.step_to(S.bars_from_arrays(_sub(arrays, 2 * DAY))) == 0


# --------------------------------------------------------------------------- checkpoint


def test_checkpoint_round_trip_and_refusals(tmp_path, arrays, monkeypatch):
    engine = S.IncrementalEngine.build(S.bars_from_arrays(_sub(arrays, 2 * DAY)), anchor_ms=T0)
    path = tmp_path / S.CHECKPOINT_NAME
    S.save_checkpoint(path, engine)
    back = S.load_checkpoint(path)
    assert back is not None and back.processed == engine.processed and back.rows == engine.rows
    assert S.load_checkpoint(path, symbol="ETHUSDT") is None
    monkeypatch.setattr(S, "code_fingerprint", lambda: "other code")
    assert S.load_checkpoint(path) is None
    monkeypatch.undo()
    path.write_bytes(b"not a pickle")
    assert S.load_checkpoint(path) is None
    assert S.load_checkpoint(tmp_path / "missing") is None


# --------------------------------------------------------------------------- worker


class _Venue:
    """Serves `/fapi/v1/klines` from the synthetic rows, as Binance would."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls = 0
        self.fail = False

    def __call__(self, url: str, timeout: float = 0):
        from urllib.parse import parse_qs, urlparse
        self.calls += 1
        if self.fail:
            raise OSError("venue down")
        q = {k: v[0] for k, v in parse_qs(urlparse(url if isinstance(url, str)
                                                   else url.full_url).query).items()}
        lo, hi = int(q["startTime"]), int(q["endTime"])
        page = [[r["timestamp_ms"], r["open"], r["high"], r["low"], r["close"], r["volume"], 0,
                 r["turnover"], 1, "0", "0", "0"]
                for r in self.rows if lo <= r["timestamp_ms"] <= hi][: int(q["limit"])]
        body = json.dumps(page).encode()

        class _Resp:
            status = 200

            def read(self_inner):
                return body

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

        return _Resp()


def test_worker_rebuilds_then_steps_and_matches_full_r1(tmp_path, rows, reference, monkeypatch):
    base, ref_rows, _ = reference
    venue = _Venue(rows)
    now = T0 + (base + 1) * 60_000 + 5_000          # minute `base` (index base-1 .. ) closed
    first = W.run_once(tmp_path, now_ms=now, anchor_days=5, opener=venue)
    assert first["result"] == "REBUILT"
    for minutes in (1, 1, 3, 1):
        now += minutes * 60_000
        out = W.run_once(tmp_path, now_ms=now, anchor_days=5, opener=venue)
        assert out["result"] == "STEPPED" and out["stepped"] == minutes
        snap = json.loads((tmp_path / S.SNAPSHOT_NAME).read_text())
        ref = ref_rows[snap["bar_close_ms"]]
        assert snap["resistance"] == {"price": ref["res_price"], "distance_pct": ref["res_distance_pct"],
                                      "level_id": ref["res_id"]}
        assert snap["support"] == {"price": ref["sup_price"], "distance_pct": ref["sup_distance_pct"],
                                   "level_id": ref["sup_id"]}
        assert snap["bar_close_ms"] == STR.last_closed_open_ms(now) + 60_000
    assert W.run_once(tmp_path, now_ms=now, anchor_days=5, opener=venue)["result"] == "UP_TO_DATE"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [S.SNAPSHOT_NAME, S.CHECKPOINT_NAME, W.LOCK_NAME])     # no temp file left behind


def test_worker_venue_failure_writes_nothing(tmp_path, rows, reference, monkeypatch):
    base, _, _ = reference
    venue = _Venue(rows)
    now = T0 + (base + 1) * 60_000 + 5_000
    assert W.run_once(tmp_path, now_ms=now, anchor_days=5, opener=venue)["result"] == "REBUILT"
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    venue.fail = True
    assert W.run_once(tmp_path, now_ms=now + 120_000, anchor_days=5, opener=venue)["result"] == "VENUE_ERROR"
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_worker_skips_while_another_run_holds_the_lock(tmp_path):
    import fcntl
    fd = os.open(tmp_path / W.LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert W.run_once(tmp_path, now_ms=T0)["result"] == "LOCKED"
    finally:
        os.close(fd)
    assert not (tmp_path / S.SNAPSHOT_NAME).exists()


def test_worker_rebuilds_over_a_corrupt_checkpoint(tmp_path, rows, reference, monkeypatch):
    base, _, _ = reference
    venue = _Venue(rows)
    (tmp_path / S.CHECKPOINT_NAME).write_bytes(b"\x80\x05garbage")
    assert W.run_once(tmp_path, now_ms=T0 + (base + 1) * 60_000, anchor_days=5, opener=venue)["result"] == "REBUILT"


# --------------------------------------------------------------------------- snapshot file


def test_atomic_replacement_never_shows_a_partial_file(tmp_path):
    path = tmp_path / S.SNAPSHOT_NAME
    big = {"symbol": "BTCUSDT", "bar_close_ms": 1, "pad": "x" * 40_000}
    S.write_snapshot(path, big)
    stop = threading.Event()
    bad: list[str] = []

    def reader():
        while not stop.is_set():
            raw = path.read_bytes()
            try:
                json.loads(raw)
            except ValueError:
                bad.append(raw[:20].decode(errors="replace"))

    t = threading.Thread(target=reader)
    t.start()
    for i in range(300):
        S.write_snapshot(path, dict(big, bar_close_ms=i))
    stop.set()
    t.join()
    assert bad == []


# --------------------------------------------------------------------------- API reading


def _snap(close_ms: int, **kw) -> dict:
    base = {"symbol": "BTCUSDT", "bar_open_ms": close_ms - 60_000, "bar_close_ms": close_ms,
            "close": 85000.0, "resistance": {"price": 85100.0, "distance_pct": 0.1176, "level_id": 7},
            "support": {"price": 84900.0, "distance_pct": -0.1176, "level_id": 9}}
    base.update(kw)
    return base


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    path = tmp_path / S.SNAPSHOT_NAME
    monkeypatch.setenv(A.STRUCTURE_SNAPSHOT_ENV, str(path))
    monkeypatch.delenv(A.ROOT_ENV, raising=False)

    class _Off:
        def __call__(self, *a, **k):
            raise OSError("no venue in tests")

    monkeypatch.setattr(A.state, "sources", A.Sources(opener=_Off()))
    return path, TestClient(A.app)


def test_fresh_snapshot_is_shown(api_client):
    path, client = api_client
    now = int(time.time() * 1000)
    S.write_snapshot(path, _snap(now - now % 60_000))
    assert client.get("/snapshot").json()["structure"] == {
        "live": True, "resistance_pct": "0.12", "support_pct": "0.12",
        "resistance_price": "85100.0", "support_price": "84900.0"}


@pytest.mark.parametrize("case", ["missing", "stale", "corrupt", "partial", "foreign", "huge",
                                  "no_close"])
def test_bad_snapshot_is_dashes(api_client, case):
    path, client = api_client
    now = int(time.time() * 1000)
    if case == "stale":
        S.write_snapshot(path, _snap(now - C.MINUTE_MS - C.STRUCTURE_GRACE_MS - 1))
    elif case == "corrupt":
        path.write_bytes(b"\x00\xff not json")
    elif case == "partial":
        path.write_bytes(json.dumps(_snap(now)).encode()[:40])
    elif case == "foreign":
        S.write_snapshot(path, _snap(now, symbol="ETHUSDT"))
    elif case == "huge":
        S.write_snapshot(path, _snap(now, pad="x" * (A.STRUCTURE_SNAPSHOT_MAX_BYTES + 1)))
    elif case == "no_close":
        S.write_snapshot(path, _snap(now, bar_close_ms="soon"))
    body = client.get("/snapshot").json()
    assert body["structure"] == {"live": False, "resistance_pct": None, "support_pct": None,
                                 "resistance_price": None, "support_price": None}
    assert set(body) == {"symbol", "server_time_ms", "structure", "liquidity", "flow",
                         "derivatives", "walls", "entry"}


def test_snapshot_layer_reads_the_same_as_the_in_process_view(arrays):
    """API contract parity: for the same R1 row, the snapshot path and the 31b6f2d
    `structure_view` path reduce to the same STRUCTURE section, LIVE and STALE."""
    engine = S.IncrementalEngine.build(S.bars_from_arrays(_sub(arrays, 3 * DAY)), anchor_ms=T0)
    run = STR.StructureRun(anchor_ms=T0, series_first_ms=T0, series_last_open_ms=engine.last_open_ms,
                           series_rows=engine.processed, publish_from_ms=0, publish_to_ms=0,
                           rows=engine.rows, computed_at_ms=0, recompute_ms=0,
                           repaint_violations=0, vanished_levels=0)
    snap = json.loads(json.dumps(S.snapshot_payload(engine, written_ms=0)))
    close = snap["bar_close_ms"]
    for now in (close, close + 60_000 + C.STRUCTURE_GRACE_MS, close + 60_000 + C.STRUCTURE_GRACE_MS + 1):
        old = V.structure_section(STR.structure_view(run, symbol="BTCUSDT", now_ms=now))
        new = V.structure_section(A.structure_layer(snap, now))
        assert old == new


# --------------------------------------------------------------------------- isolation


def test_api_process_never_loads_the_series_or_numpy():
    code = ("import sys; import app.crypto.btc_context_ui_v1.api as A; "
            "bad = [m for m in ('numpy', 'app.crypto.market_context_v1.structure', "
            "'app.crypto.btc_context_ui_v1.structure_state', "
            "'app.crypto.btc_context_ui_v1.structure_worker', "
            "'app.crypto.research.price_structure_r1.engine') if m in sys.modules]; "
            "s = A.Sources(); "
            "bad += [a for a in ('series', 'run', 'structure_layer') if hasattr(s, a)]; "
            "print(bad)")
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True,
                         check=True)
    assert out.stdout.strip() == "[]"


def test_worker_writes_only_inside_its_state_dir():
    """The worker's writes all go through `atomic_write_bytes`, the lock file and the checkpoint
    unlink - every path built from `state_dir`. No other write call exists in either module."""
    allowed = {"structure_state.py": {"mkdir", "replace", "unlink", "fsync", "fdopen", "write"},
               "structure_worker.py": {"mkdir", "unlink"}}
    for name, ok in allowed.items():
        tree = ast.parse((PACKAGE / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if fn in ("write_text", "write_bytes", "mkdir", "unlink", "rename", "replace",
                          "rmtree", "remove", "fdopen", "write"):
                    assert fn in ok, (name, fn)
                if fn == "open":
                    modes = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
                    assert not any(set(str(m)) & set("wax+") for m in modes), name
