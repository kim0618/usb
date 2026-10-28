"""Strategy I paper read API: state separation (not launched / no run / input blocked / error / delayed /
running), figures only from the store, overlay kept apart, and isolation from the registry routes."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.strategy_i import router
from app.strategies.i_paper import store as ST
from app.strategies.i_paper import views as IV

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=NY)          # expected latest stored session = 2026-11-03


def sample_events():
    ev = [{"type": "SIGNAL", "session": "2026-10-30", "k": 0, "signal": "2026-10-30", "exec": "2026-11-02"},
          {"type": "TRADE", "session": "2026-11-02", "k": 0, "seq": 0, "side": "BUY", "ticker": "AAA", "shares": 10.0, "notional": 500.0, "cost": 2.0, "stale_last_close": False},
          {"type": "TRADE", "session": "2026-11-02", "k": 0, "seq": 1, "side": "BUY", "ticker": "BBB", "shares": 4.0, "notional": 480.0, "cost": 1.92, "stale_last_close": False},
          {"type": "EXEC", "session": "2026-11-02", "k": 0, "signal": "2026-10-30", "holdings": 2, "holdings_list": ["AAA", "BBB"], "trade_count": 2,
           "cash_pct": 90.0, "nav_post": 9996.08, "costs": 3.92, "turnover_one_way": None},
          {"type": "OVERLAY_OBS", "session": "2026-11-02", "k": 0, "signal": "2026-10-30", "affects_book": False, "status": "NOT_EVALUATED",
           "reason": "NO_FORWARD_PIT_SIC_SNAPSHOT"},
          {"type": "NAV", "session": "2026-11-02", "nav": 9996.08},
          {"type": "NAV", "session": "2026-11-03", "nav": 10016.08}]
    latest = {"session": "2026-11-03", "cash_from_logged_trades": 9016.08, "nav": 10016.08,
              "positions": [{"ticker": "AAA", "shares": 10.0, "close": 52.0, "value": 520.0, "stale": False},
                            {"ticker": "BBB", "shares": 4.0, "close": 120.0, "value": 480.0, "stale": False}]}
    return ev, latest


def make_store(tmp: Path, as_of=date(2026, 11, 3)) -> Path:
    s = tmp / "store"; ST.launch(s, date(2026, 10, 30))
    ev, latest = sample_events()
    ST.run(s, as_of, lambda l, a, w: {"events": [e for e in ev if e["session"] <= a.isoformat()], "pending": [], "latest": latest})
    return s


def test_not_launched_shows_no_figures(tmp_path):
    v = IV.build(tmp_path / "nope", NOW)
    assert v["status"] == "NOT_LAUNCHED" and v["launched"] is False
    assert "account" not in v and "positions" not in v and "nav_series" not in v


def test_launched_without_run_and_input_blocked(tmp_path):
    s = tmp_path / "s"; ST.launch(s, date(2026, 10, 30))
    assert IV.build(s, NOW)["status"] == "LAUNCHED_NO_RUN"
    from app.strategies.i_paper import view as V
    def bad(l, a, w): raise V.ViewError("grouped store not calendar-complete: missing ['2026-11-02']")
    try: ST.run(s, date(2026, 11, 3), bad)
    except V.ViewError: pass
    v = IV.build(s, NOW)
    assert v["status"] == "INPUT_BLOCKED" and "calendar-complete" in v["reason"] and "account" not in v


def test_running_book_figures_come_from_the_store(tmp_path):
    v = IV.build(make_store(tmp_path), NOW)
    assert v["status"] == "RUNNING" and v["next_signal"] == "2026-11-30"
    a = v["account"]
    assert a["nav"] == 10016.08 and round(a["total_pnl"], 2) == 16.08 and round(a["day_pnl"], 2) == 20.0 and a["holdings"] == 2
    aaa = next(p for p in v["positions"] if p["ticker"] == "AAA")
    assert round(aaa["avg_cost"], 4) == 50.2 and aaa["value"] == 520.0 and round(aaa["weight"], 6) == round(520 / 10016.08, 6)
    assert v["overlay"]["virtual"] is True and v["overlay"]["affects_book"] is False
    assert all("OVERLAY" not in json.dumps(p) for p in v["positions"]) and v["orders"] == "NONE"


def test_delayed_and_error_states(tmp_path):
    s = make_store(tmp_path, as_of=date(2026, 11, 2))
    assert IV.build(s, NOW)["status"] == "DATA_DELAYED"
    def boom(l, a, w): raise ST.IntegrityError("stored event 3 is not reproduced by the inputs")
    try: ST.run(s, date(2026, 11, 3), boom)
    except ST.IntegrityError: pass
    v = IV.build(s, NOW)
    assert v["status"] == "ERROR" and "not reproduced" in v["reason"] and v["account"]["as_of"] == "2026-11-02"   # last good book kept
    ev, latest = sample_events()
    ST.run(s, date(2026, 11, 3), lambda l, a, w: {"events": ev, "pending": [], "latest": latest})
    assert IV.build(s, NOW)["status"] == "RUNNING"                                                                # error cleared by a good run


def test_route_reads_the_configured_store_only(tmp_path, monkeypatch):
    s = make_store(tmp_path)
    monkeypatch.setenv("USB_STRATEGY_I_PAPER_STORE", str(s))
    app = FastAPI(); app.include_router(router, prefix="/api/v1")
    r = TestClient(app).get("/api/v1/strategy-i/paper")
    assert r.status_code == 200 and r.json()["strategy_id"] == "STRATEGY_I_MOM12_2" and r.json()["launched"] is True
    monkeypatch.setenv("USB_STRATEGY_I_PAPER_STORE", str(tmp_path / "missing"))
    assert TestClient(app).get("/api/v1/strategy-i/paper").json()["status"] == "NOT_LAUNCHED"


def test_registry_routes_untouched():
    from app.api.router import router as api
    app = FastAPI(); app.include_router(api)
    paths = set(app.openapi()["paths"])
    assert "/api/v1/strategy-i/paper" in paths and "/api/v1/strategies" in paths and "/api/v1/strategies/{strategy_id}/paper" in paths
    from app.strategies import registry as REG
    assert "STRATEGY_I" not in json.dumps([getattr(x, "strategy_id", str(x)) for x in getattr(REG, "REGISTRY", [])])


def _client(store: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("USB_STRATEGY_I_PAPER_STORE", str(store))
    app = FastAPI(); app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def _tree(root: Path) -> dict[str, tuple[int, str]]:
    import hashlib
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(root.rglob("*")) if p.is_file()}


def pre_launch_store(tmp: Path, as_of=date(2026, 10, 20)) -> Path:
    """The production shape before the launch signal: one completed run, zero events, no book."""
    s = tmp / "store"; ST.launch(s, date(2026, 10, 30))
    ST.run(s, as_of, lambda l, a, w: {"events": [], "pending": [], "latest": None})
    return s


GUARD_SKIP = {"at": "2026-10-28T06:15:04+00:00", "decision": "SKIP", "exit_code": 10, "guard": "V3",
              "reasons": ["MEMORY_PSI_full_avg10:6.92", "IO_PSI_full_avg10:25.52"], "mem_available_mib": 411.9}
GUARD_RUN = {"at": "2026-10-28T06:52:58+00:00", "decision": "RUN", "exit_code": 0, "guard": "V3.1", "reasons": []}


def test_pre_launch_zero_events_is_not_an_error(tmp_path):
    s = pre_launch_store(tmp_path, as_of=date(2026, 10, 27))
    v = IV.build(s, datetime(2026, 10, 28, 9, 0, tzinfo=NY))
    assert v["status"] == "RUNNING" and v["reason"] is None and v["last_run"]["events"] == 0
    assert v["account"] is None and v["positions"] == [] and v["trades"] == [] and v["next_signal"] == "2026-10-30"
    v = IV.build(pre_launch_store(tmp_path / "late"), datetime(2026, 10, 28, 9, 0, tzinfo=NY))
    assert v["status"] == "DATA_DELAYED" and v["reason"] is None and v["flags"]["data_delay"]["expected"] == "2026-10-27"


def test_guard_skip_is_read_back_behind_a_later_run(tmp_path, monkeypatch):
    s = pre_launch_store(tmp_path)
    c = _client(s, monkeypatch)
    assert c.get("/api/v1/strategy-i/paper").json()["guard"] is None                     # no guard file yet
    (s / "guard.jsonl").write_text(json.dumps(GUARD_SKIP) + "\n" + json.dumps(GUARD_RUN) + "\n", encoding="utf-8")
    g = c.get("/api/v1/strategy-i/paper").json()["guard"]
    assert g["latest"]["decision"] == "RUN" and g["last_skip"]["decision"] == "SKIP"
    assert g["last_skip"]["reasons"] == GUARD_SKIP["reasons"] and "mem_available_mib" not in g["last_skip"]
    (s / "guard.jsonl").write_text("{not json\n", encoding="utf-8")
    r = c.get("/api/v1/strategy-i/paper")
    assert r.status_code == 200 and r.json()["guard"]["latest"]["decision"] == "UNREADABLE"


def test_malformed_store_reports_error_in_the_body(tmp_path, monkeypatch):
    s = pre_launch_store(tmp_path)
    (s / "state.json").write_text("{broken", encoding="utf-8")
    r = _client(s, monkeypatch).get("/api/v1/strategy-i/paper")
    assert r.status_code == 200 and r.json()["status"] == "ERROR" and "read model failed" in r.json()["reason"]
    assert r.json()["orders"] == "NONE"


def test_reads_leave_the_store_untouched_and_are_deterministic(tmp_path, monkeypatch):
    s = make_store(tmp_path)
    (s / "guard.jsonl").write_text(json.dumps(GUARD_SKIP) + "\n", encoding="utf-8")
    before = _tree(s)
    c = _client(s, monkeypatch)
    bodies = [c.get("/api/v1/strategy-i/paper").json() for _ in range(3)]
    assert _tree(s) == before and bodies[0] == bodies[1] == bodies[2]
    assert c.post("/api/v1/strategy-i/paper").status_code == 405                         # GET only


def test_route_does_not_load_the_engine_runtime():
    import subprocess, sys
    code = ("import sys, app.api.strategy_i as A; from fastapi.testclient import TestClient; from fastapi import FastAPI\n"
            "app = FastAPI(); app.include_router(A.router); TestClient(app).get('/strategy-i/paper')\n"
            "bad = [m for m in ('app.strategies.i_paper.store', 'app.strategies.i_paper.run', 'app.strategies.i_paper.lean',\n"
            "       'app.strategies.i_paper.view', 'app.strategies.i_paper.guard', 'pandas', 'numpy') if m in sys.modules]\n"
            "print(bad); sys.exit(1 if bad else 0)")
    env = dict(__import__("os").environ, USB_STRATEGY_I_PAPER_STORE="/nonexistent/i-store")
    r = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
