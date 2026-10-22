"""Strategy I oneshot overlap guard and the I-owned grouped history merge (no market data needed)."""
from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timezone

import pytest

from app.strategies.i_paper import guard as G
from app.strategies.i_paper import view as V

ROOMY = {"MemAvailable": 400, "SwapFree": 1400}


def run_at(utc: str, active=(), mem=ROOMY, psi=0.0):
    return G.decide(datetime.fromisoformat(utc).replace(tzinfo=timezone.utc), is_active=lambda u: u in active,
                    meminfo=lambda: mem, psi=lambda: psi)


def test_runs_at_0215_et_in_both_dst_regimes():
    assert run_at("2026-10-21T06:15:00")["decision"] == "RUN"      # EDT: 02:15 ET = 06:15 UTC
    assert run_at("2026-11-04T07:15:00")["decision"] == "RUN"      # EST: 02:15 ET = 07:15 UTC


@pytest.mark.parametrize("unit", G.HEAVY_UNITS + G.E_UNITS)
def test_any_heavy_or_e_unit_active_skips(unit):
    r = run_at("2026-10-21T06:15:00", active=(unit,))
    assert r["decision"] == "SKIP" and unit in r["reasons"][0]


def test_e_window_skips_on_weekdays_only():
    assert run_at("2026-10-21T08:00:00")["reasons"] == ["E_ACTIVE_WINDOW:Wed 04:00 ET"]
    assert run_at("2026-10-24T08:00:00")["decision"] == "RUN"      # Saturday


def test_memory_pressure_and_headroom_skip():
    assert run_at("2026-10-21T06:15:00", psi=25.0)["reasons"] == ["MEMORY_PSI:25.00"]
    assert run_at("2026-10-21T06:15:00", mem={"MemAvailable": 100, "SwapFree": 300})["reasons"] == ["MEMORY_HEADROOM_MIB:400"]


def test_main_exit_codes_and_log(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "decide", lambda now: {"decision": "SKIP", "reasons": ["X"]})
    assert G.main(["--store", str(tmp_path)]) == 1
    monkeypatch.setattr(G, "decide", lambda now: {"decision": "RUN", "reasons": []})
    assert G.main(["--store", str(tmp_path)]) == 0
    assert [json.loads(l)["decision"] for l in (tmp_path / "guard.jsonl").read_text().splitlines()] == ["SKIP", "RUN"]


# ------------------------------------------------------------------ I-owned history merged into the view
def _g(d, session, body=b"x"):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{session}.json.gz").write_bytes(gzip.compress(body, mtime=0))


def _runtime(tmp_path):
    rt = tmp_path / "rt"
    for sub in ("strategy_c/raw/tickers", "strategy_c/raw/splits", "research_universe_u1/reference/tickers"):
        (rt / sub).mkdir(parents=True)
    return rt


def test_history_fills_older_sessions_and_conflicts_refuse(tmp_path):
    rt = _runtime(tmp_path); hist = tmp_path / "hist"
    for s in ("2026-10-12", "2026-10-13"): _g(hist, s)
    for s in ("2026-10-13", "2026-10-14"): _g(rt / "strategy_c/raw/grouped", s)
    vw = V.build_view(V.Stores(rt, evidence=(), history=(hist,)), date(2026, 10, 14), tmp_path / "v")
    assert vw.sessions == ["2026-10-12", "2026-10-13", "2026-10-14"] and vw.manifest["grouped"]["from_history"] == 1
    _g(hist, "2026-10-14", b"different")
    with pytest.raises(V.ViewError, match="differs"):
        V.build_view(V.Stores(rt, evidence=(), history=(hist,)), date(2026, 10, 14), tmp_path / "v2")
