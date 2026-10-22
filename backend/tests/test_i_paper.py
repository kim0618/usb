"""Strategy I order-less paper: schedule, point-in-time view, frozen-rule regression, integer shares,
cash/cost arithmetic, overlay isolation, duplicate-run prevention and restart recovery.

Uses the local production-layout stores under data/runtime (read through symlinked as-of views; the
stores themselves are never written). Skipped when those stores are absent.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from app.market.calendar import MarketCalendar
from app.strategies.i_paper import engine as EN
from app.strategies.i_paper import store as ST
from app.strategies.i_paper import view as V

REPO = Path(__file__).resolve().parents[2]
RT = REPO / "data/runtime"
pytestmark = pytest.mark.skipif(not (RT / "strategy_c/raw/grouped").exists(), reason="local stores absent")
BASE_TRADES_SHA = "af4712474a60961739facc30b3254bb3ebb88f928e4db3c4ae150c28aa20dea4"
BASE_NAV_SHA = "1b88912592b349a7d68c9eb8689166978faf3116a8c410fee6d5c322b1610fc0"
CAL = MarketCalendar()
AS_OF = date(2026, 10, 12)
LAUNCH = date(2025, 9, 30)


@pytest.fixture(scope="module")
def m21(tmp_path_factory):
    d = tmp_path_factory.mktemp("v")
    vw = V.build_view(V.Stores(RT), AS_OF, d / "view", CAL)
    (d / "s").mkdir()
    return V.load_v21(vw, d / "s")


def mirror(tmp: Path, drop: str | None = None) -> Path:
    """symlink copy of the stores (optionally without one grouped session)"""
    rt = tmp / "rt"
    for sub in ("strategy_c/raw/grouped", "strategy_c/raw/tickers", "strategy_c/raw/splits", "research_universe_u1/reference/tickers"):
        (rt / sub).mkdir(parents=True)
        for p in (RT / sub).glob("*.json.gz"):
            if drop and p.name.startswith(drop): continue
            os.symlink(p.resolve(), rt / sub / p.name)
    return rt


# ------------------------------------------------------------------ monthly signal schedule
def test_month_end_schedule_matches_frozen_window():
    sig = [d.isoformat() for d in EN.month_end_signals(CAL, LAUNCH, date(2026, 9, 30))]
    assert sig == ["2025-09-30", "2025-10-31", "2025-11-28", "2025-12-31", "2026-01-30", "2026-02-27", "2026-03-31",
                   "2026-04-30", "2026-05-29", "2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30"]


def test_pending_signal_until_execution_file_exists():
    sessions = [w.session_date.isoformat() for w in __import__("app.backtest.collector.range", fromlist=["x"]).sessions_between(CAL, date(2024, 9, 16), date(2026, 9, 30))]
    sigs, exs, pending, refused = EN.schedule(CAL, sessions, date(2026, 8, 31), date(2026, 9, 30))
    assert sigs == ["2026-08-31"] and exs == ["2026-09-01"] and pending == ["2026-09-30"] and refused == []


# ------------------------------------------------------------------ point-in-time view
def test_view_hides_future_files_and_refuses_gaps(tmp_path):
    rt = mirror(tmp_path)
    os.symlink((RT / "strategy_c/raw/grouped/2026-10-12.json.gz").resolve(), rt / "strategy_c/raw/grouped/2026-10-13.json.gz")
    os.symlink((RT / "strategy_c/raw/tickers/CS_2026-10-01.json.gz").resolve(), rt / "strategy_c/raw/tickers/CS_2027-01-04.json.gz")
    vw = V.build_view(V.Stores(rt), date(2026, 10, 2), tmp_path / "view", CAL)
    assert vw.sessions[-1] == "2026-10-02" and all(s <= "2026-10-02" for s in vw.sessions)
    assert not any(n > "CS_2026-10-02" for n in vw.manifest["tickers"])
    assert not (tmp_path / "view/strategy_c/raw/grouped/2026-10-13.json.gz").exists()
    rt2 = mirror(tmp_path / "b", drop="2026-06-15")
    with pytest.raises(V.ViewError, match="not calendar-complete"):
        V.build_view(V.Stores(rt2), AS_OF, tmp_path / "b/view", CAL)
    with pytest.raises(V.ViewError, match="no grouped file|not calendar-complete"):
        V.build_view(V.Stores(rt), date(2026, 10, 14), tmp_path / "c/view", CAL)


# ------------------------------------------------------------------ frozen rules
def test_frozen_rules_reproduce_baseline_with_research_continuity(m21, tmp_path):
    r = EN.compute(m21, CAL, LAUNCH, AS_OF, None, _research_keep_v2_continuity=True)
    run = m21.simulate21("MOM12_2", 40)
    pd.DataFrame(run["trades"], columns=["exec", "side", "ticker", "shares", "notional", "cost"]).to_csv(tmp_path / "t.csv", index=False)
    run["nav"].rename("nav").to_csv(tmp_path / "n.csv")
    assert hashlib.sha256((tmp_path / "t.csv").read_bytes()).hexdigest() == BASE_TRADES_SHA
    assert hashlib.sha256((tmp_path / "n.csv").read_bytes()).hexdigest() == BASE_NAV_SHA
    assert len(r["signals"]) == 13 and [e for e in r["events"] if e["type"] == "CONTINUITY_LINKED"][0]["ticker"] == "BREA"


def test_production_mode_has_no_v2_continuity_and_reports_stops(m21):
    r = EN.compute(m21, CAL, LAUNCH, AS_OF, None)
    assert "BREA" not in m21.TRANS and m21.AMBIG == {}
    stops = [(e["ticker"], e["session"]) for e in r["events"] if e["type"] == "PRICE_STOP_HELD"]
    assert ("BREA", "2025-10-03") in stops
    brea = [e for e in r["events"] if e["type"] == "TRADE" and e["ticker"] == "BREA" and e["side"] == "SELL"]
    assert brea and brea[0]["stale_last_close"] and brea[0]["session"] == "2025-11-03"


def test_integer_shares_cash_and_costs(m21):
    r = EN.compute(m21, CAL, LAUNCH, AS_OF, None)
    tr = [e for e in r["events"] if e["type"] == "TRADE"]
    assert all(float(e["shares"]).is_integer() and e["shares"] >= 1 for e in tr if e["side"].startswith("BUY"))
    cash = 10000.0
    for e in tr:
        n = e["notional"]
        fee = n * 0.004 + ((n * 0.206 / 1e4 + min(e["shares"] * 0.000195, 9.79)) if e["side"] == "SELL" else 0.0)
        assert abs(fee - e["cost"]) <= 1e-6
        cash += (n - e["cost"]) if e["side"] == "SELL" else -(n + e["cost"])
        assert cash >= -1e-6
    ex = [e for e in r["events"] if e["type"] == "EXEC"]
    nav = {e["session"]: e["nav"] for e in r["events"] if e["type"] == "NAV"}
    assert all(abs(e["nav_post"] - nav[e["session"]]) < 1e-4 and 0 <= e["cash_pct"] <= 100 and e["holdings"] <= 20 for e in ex)
    assert abs(ex[-1]["cash_from_logged_trades"] - cash) < 1e-6


# ------------------------------------------------------------------ overlay observation only
def test_overlay_observation_never_changes_the_book(m21, tmp_path):
    base = EN.compute(m21, CAL, LAUNCH, AS_OF, None)
    R = m21.m1.get_rank("2026-09-30", "MOM12_2")
    recs = []
    for tk in json.loads(json.dumps([e for e in base["events"] if e["type"] == "EXEC"][-1]["holdings_list"])):
        cik = (R["snap"].get(tk) or {}).get("cik")
        recs.append({"cik": cik, "sic": "2834" if tk < "M" else "3674", "http_status": 200, "fetched_at": "2026-09-30T21:00:00+00:00"})
    store = tmp_path / "sic"; store.mkdir()
    (store / "2026-09-30.json").write_text(json.dumps({"records": recs}))
    obs = EN.compute(m21, CAL, LAUNCH, AS_OF, store)
    strip = lambda ev: [e for e in ev if e["type"] != "OVERLAY_OBS"]
    assert strip(obs["events"]) == strip(base["events"])
    o12 = [e for e in obs["events"] if e["type"] == "OVERLAY_OBS"][-1]
    assert o12["status"] == "EVALUATED" and o12["affects_book"] is False and o12["over_cap"] and o12["virtual_forced_exits"]
    assert all(e["status"] == "NOT_EVALUATED" for e in obs["events"] if e["type"] == "OVERLAY_OBS" and e["k"] < 12)
    for r in recs: r["fetched_at"] = "2026-10-01T15:00:00+00:00"        # after the execution open: not point-in-time
    (store / "2026-09-30.json").write_text(json.dumps({"records": recs}))
    late = EN.compute(m21, CAL, LAUNCH, AS_OF, store)
    o = [e for e in late["events"] if e["type"] == "OVERLAY_OBS"][-1]
    assert o["status"] == "NOT_EVALUATED" and o["reason"] == "SNAPSHOT_OUTSIDE_PIT_WINDOW_OR_FAILED"


# ------------------------------------------------------------------ store: duplicate runs and restart recovery
def fake_compute(events):
    def _c(launch, as_of, work):
        return {"events": [e for e in events if e["session"] <= as_of.isoformat()], "pending": [], "manifest": {}}
    return _c


EVS = [{"type": "NAV", "session": "2026-11-0%d" % d, "nav": 10000 + d} for d in range(2, 7)]


def test_launch_once_idempotent_runs_and_lock(tmp_path):
    s = tmp_path / "store"
    ST.launch(s, date(2026, 10, 30))
    with pytest.raises(ST.IntegrityError): ST.launch(s, date(2026, 10, 30))
    a = ST.run(s, date(2026, 11, 3), fake_compute(EVS)); assert a["appended"] == 2
    b = ST.run(s, date(2026, 11, 3), fake_compute(EVS)); assert b["appended"] == 0
    c = ST.run(s, date(2026, 11, 6), fake_compute(EVS)); assert c["appended"] == 3 and c["events"] == 5
    with ST.locked(s):
        with pytest.raises(ST.Busy): ST.run(s, date(2026, 11, 6), fake_compute(EVS))
    with pytest.raises(ST.IntegrityError, match="behind"): ST.run(s, date(2026, 11, 4), fake_compute(EVS))


def test_restart_recovery_and_tamper_detection(tmp_path):
    s = tmp_path / "store"; ST.launch(s, date(2026, 10, 30))
    ST.run(s, date(2026, 11, 4), fake_compute(EVS))
    (s / "events.jsonl.tmp.999").write_text("half")                          # crash leftover
    (s / "state.json").unlink()                                              # crash between events and state
    r = ST.run(s, date(2026, 11, 4), fake_compute(EVS))
    assert r["appended"] == 0 and (s / "state.json").exists() and not list(s.glob("*.tmp.*"))
    lines = (s / "events.jsonl").read_text().splitlines()
    (s / "events.jsonl").write_text("\n".join(lines[:-1] + [lines[-1].replace('"nav":10004', '"nav":10099')]) + "\n")
    with pytest.raises(ST.IntegrityError, match="not reproduced"): ST.run(s, date(2026, 11, 5), fake_compute(EVS))
    (s / "events.jsonl").write_text("\n".join(lines)[:-5])                    # partial last line
    with pytest.raises(ST.IntegrityError, match="partial"): ST.run(s, date(2026, 11, 5), fake_compute(EVS))


def seed_on_time(store: Path, stores: V.Stores):
    """registry as if every existing snapshot had been seen on its own as-of date (historical test only)"""
    reg = {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "first_seen": p.name[3:13]} for p in stores.snapshot_files()}
    store.mkdir(parents=True, exist_ok=True); (store / "reference_seen.json").write_text(json.dumps(reg))


def test_frozen_compute_two_as_ofs_extend_as_a_prefix(tmp_path):
    s = tmp_path / "store"; ST.launch(s, LAUNCH)
    seed_on_time(s, V.Stores(RT))
    comp = ST.frozen_compute(V.Stores(RT), store=s, today="2026-10-21")
    a = ST.run(s, date(2026, 10, 2), comp)
    b = ST.run(s, AS_OF, comp)
    assert a["appended"] > 0 and b["appended"] == 6 and b["events"] == a["events"] + 6     # 6 NAV sessions 10-05..10-12
    c = ST.run(s, AS_OF, comp)
    assert c["appended"] == 0


# ------------------------------------------------------------------ P1: point-in-time continuity
from app.strategies.i_paper import continuity as CT


def ranking_files():
    return [p for d in V.Stores(RT).tickers for p in sorted(d.glob("CS_*.json.gz"))]


def evidence_copy(tmp: Path, name: str, src: str, edit) -> Path:
    import gzip
    j = json.loads(gzip.open(RT / "strategy_c/raw/tickers" / src).read())
    j["results"] = edit(j["results"]); j["as_of"] = name[3:13]
    d = tmp / "evidence"; d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / name, "wt") as fh: json.dump(j, fh)
    return d / name


def brea_cik():
    import gzip
    j = json.loads(gzip.open(RT / "strategy_c/raw/tickers/CS_2025-10-01.json.gz").read())
    return next(r["cik"] for r in j["results"] if r["ticker"] == "BREA")


def test_brea_slmt_future_snapshot_is_blocked(m21):
    # on-time availability: SLMT's CIK first appears in CS_2026-01-02, after BREA left the book (2025-11-03)
    r = EN.compute(m21, CAL, LAUNCH, AS_OF, None, registry={}, ranking_files=ranking_files(), evidence_files=[])
    u = [e for e in r["events"] if e["type"] == "CONTINUITY_UNRESOLVED" and e["ticker"] == "BREA"]
    assert u and u[0]["session"] == "2025-11-03" and u[0]["reason"] == "NO_PIT_EVIDENCE"
    assert not [e for e in r["events"] if e["type"] == "CONTINUITY_LINKED"]
    assert any(d["ticker"] == "BREA" and d["v21_link"][0] == "SLMT" for d in r["v21_continuity_differences"])
    # back-filled availability (registered 2026-10-02): still blocked, and ranking refuses signals before it
    reg = {p.name: {"sha256": "x", "first_seen": "2026-10-02"} for p in ranking_files()}
    r2 = EN.compute(m21, CAL, LAUNCH, AS_OF, None, registry=reg, ranking_files=ranking_files(), evidence_files=[])
    assert r2["signals"] == [] and "2025-09-30" in r2["refused"]


def test_pit_evidence_links_and_reproduces_v21(m21, tmp_path):
    cik = brea_cik()
    def edit(rows):
        rows = [r for r in rows if r["ticker"] != "BREA"]
        return rows + [{"ticker": "SLMT", "cik": cik, "active": True, "type": "CS", "primary_exchange": "XNAS"}]
    e = evidence_copy(tmp_path, "CS_2025-10-03.json.gz", "CS_2025-10-01.json.gz", edit)
    r = EN.compute(m21, CAL, LAUNCH, AS_OF, None, registry={}, ranking_files=ranking_files(), evidence_files=[e])
    link = [x for x in r["events"] if x["type"] == "CONTINUITY_LINKED"]
    assert link and link[0]["ticker"] == "BREA" and link[0]["new"] == "SLMT" and link[0]["session"] == "2025-10-03"
    run = m21.simulate21("MOM12_2", 40)
    pd.DataFrame(run["trades"], columns=["exec", "side", "ticker", "shares", "notional", "cost"]).to_csv(tmp_path / "t.csv", index=False)
    run["nav"].rename("nav").to_csv(tmp_path / "n.csv")
    assert hashlib.sha256((tmp_path / "t.csv").read_bytes()).hexdigest() == BASE_TRADES_SHA      # monthly signals, costs, cash, NAV preserved
    assert hashlib.sha256((tmp_path / "n.csv").read_bytes()).hexdigest() == BASE_NAV_SHA
    # the same evidence registered only on 2025-11-04 (after the exit) does not link the past
    late = EN.compute(m21, CAL, LAUNCH, AS_OF, None, registry={e.name: {"sha256": "x", "first_seen": "2025-11-04"}},
                      ranking_files=ranking_files(), evidence_files=[e])
    assert not [x for x in late["events"] if x["type"] == "CONTINUITY_LINKED" and x["ticker"] == "BREA"]


def test_insufficient_evidence_stays_unresolved(m21, tmp_path):
    cik = brea_cik()
    other = next(t for t in ["BNBX", "CCHH", "CHAI", "HSPO", "TLNC", "TTRX", "WSBCO", "XELLL"]
                 if t in m21.C.columns and m21.C[t].first_valid_index() in ("2025-10-03", "2025-10-06", "2025-10-07", "2025-10-08"))
    amb = evidence_copy(tmp_path, "CS_2025-10-03.json.gz", "CS_2025-10-01.json.gz",
                        lambda rows: [r for r in rows if r["ticker"] not in ("BREA", other)] + [
                            {"ticker": "SLMT", "cik": cik}, {"ticker": other, "cik": cik}])
    r = EN.compute(m21, CAL, LAUNCH, AS_OF, None, registry={}, ranking_files=ranking_files(), evidence_files=[amb])
    u = [x for x in r["events"] if x["type"] == "CONTINUITY_UNRESOLVED" and x["ticker"] == "BREA"]
    assert u and u[0]["reason"] == "AMBIGUOUS"
    still = evidence_copy(tmp_path / "b", "CS_2025-10-03.json.gz", "CS_2025-10-01.json.gz",
                          lambda rows: rows + [{"ticker": "SLMT", "cik": cik}])          # old ticker still listed: no change event
    r2 = EN.compute(m21, CAL, LAUNCH, AS_OF, None, registry={}, ranking_files=ranking_files(), evidence_files=[still])
    assert [x for x in r2["events"] if x["type"] == "CONTINUITY_UNRESOLVED" and x["ticker"] == "BREA"][0]["reason"] == "NO_PIT_EVIDENCE"


def test_cdtx_vtyx_tern_price_stops(m21):
    # V1 price-stop rule: stop reported the first session without a close, valued and sold at the last close
    # when the name leaves the hold zone, unresolved (no successor ticker). Rows equal to the baseline only when
    # the paths are equal (PIT link case, checked by the trade-hash test above).
    r = EN.compute(m21, CAL, LAUNCH, AS_OF, None, registry={}, ranking_files=ranking_files(), evidence_files=[])
    C = m21.C
    for tk, last, ex in (("CDTX", "2026-01-06", "2026-02-02"), ("VTYX", "2026-03-03", "2026-04-01"), ("TERN", "2026-05-04", "2026-06-01")):
        stop = [e for e in r["events"] if e["type"] == "PRICE_STOP_HELD" and e["ticker"] == tk]
        u = [e for e in r["events"] if e["type"] == "CONTINUITY_UNRESOLVED" and e["ticker"] == tk]
        t = [e for e in r["events"] if e["type"] == "TRADE" and e["ticker"] == tk and e["session"] == ex][0]
        assert stop[0]["last_close_session"] == last and stop[0]["session"] == CAL.next_trading_day(date.fromisoformat(last)).isoformat()
        assert u[0]["session"] == ex and u[0]["reason"] == "NO_PIT_EVIDENCE" and t["stale_last_close"] and t["side"] == "SELL"
        assert abs(t["notional"] - t["shares"] * float(C[tk].loc[last])) < 1e-6


def test_registry_is_append_only(tmp_path):
    f = tmp_path / "CS_2026-10-01.json.gz"; shutil.copy(RT / "strategy_c/raw/tickers/CS_2026-10-01.json.gz", f)
    reg = CT.update_registry(tmp_path / "reg.json", [f], "2026-10-21")
    assert reg[f.name]["first_seen"] == "2026-10-21"
    assert CT.update_registry(tmp_path / "reg.json", [f], "2026-11-30")[f.name]["first_seen"] == "2026-10-21"
    f.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="changed after registration"):
        CT.update_registry(tmp_path / "reg.json", [f], "2026-11-30")


# ------------------------------------------------------------------ P4: memory-lean loader, same numbers
import numpy as np
from app.strategies.i_paper import lean as LN


@pytest.fixture(scope="module")
def lean_pair(tmp_path_factory):
    d = tmp_path_factory.mktemp("lean")
    vw = V.build_view(V.Stores(RT), AS_OF, d / "view", CAL)
    (d / "s").mkdir()
    return vw, V.load_v21(vw, d / "s"), LN.load(vw)


def test_lean_matrices_and_tables_equal_frozen_import(lean_pair):
    vw, om, lm = lean_pair
    assert isinstance(lm.m1.adj, LN.SplitFactors) and lm.adj is lm.m1.adj        # V2.1 reads the same object
    for nm in ("C", "DV", "adj", "CA", "CAf", "R1"):
        x, y = getattr(om.m1, nm), getattr(lm.m1, nm)
        if nm == "adj": y = y.dense()
        if nm == "DV": x, y = x.iloc[y.offset:], y.frame                                 # rows a ranking can read
        assert x.index.equals(y.index) and x.columns.equals(y.columns) and x.dtypes.equals(y.dtypes)
        assert np.array_equal(x.to_numpy(), y.to_numpy(), equal_nan=True), nm
    assert list(om.S) == list(lm.S) and om.pos == lm.pos and om.m1.snap_dates == lm.m1.snap_dates and om.m1.sic == lm.m1.sic
    assert om.TRANS == lm.TRANS and om.AMBIG == lm.AMBIG and om.last_valid == lm.last_valid
    assert not hasattr(lm.m1, "rows") and not hasattr(lm.m1, "px")                 # the 1 GB objects are never built
    rr = json.loads((REPO / "backend/tests/fixtures/i_paper_rank_baseline.json").read_text())
    assert all(hashlib.sha256("\n".join(lm.m1.get_rank(m["signal"], "MOM12_2")["order"]).encode()).hexdigest() == m["order_sha256"]
               for m in rr["months"])


def test_lean_trades_nav_and_events_equal(lean_pair, tmp_path):
    vw, om, lm = lean_pair
    kw = dict(registry={}, ranking_files=vw.ranking_files, evidence_files=[])
    for research in (True, False):
        a = EN.compute(om, CAL, LAUNCH, AS_OF, None, _research_keep_v2_continuity=research, **kw)
        b = EN.compute(lm, CAL, LAUNCH, AS_OF, None, _research_keep_v2_continuity=research, **kw)
        assert json.dumps(a["events"]) == json.dumps(b["events"]) and a["latest"] == b["latest"]
        assert a["unresolved"] == b["unresolved"] and a["v21_continuity_differences"] == b["v21_continuity_differences"]
        if not research:
            payload = ''.join(ST.canon(e) + '\n' for e in b['events'])
            assert len(b['events']) == 462
            assert hashlib.sha256(payload.encode()).hexdigest() == 'c579744396e33ed891e7ebd2190b325a9eded2ec04e70c78995fb8dcccbb8059'
    EN.compute(lm, CAL, LAUNCH, AS_OF, None, _research_keep_v2_continuity=True, **kw)
    run = lm.simulate21("MOM12_2", 40)
    pd.DataFrame(run["trades"], columns=["exec", "side", "ticker", "shares", "notional", "cost"]).to_csv(tmp_path / "t.csv", index=False)
    run["nav"].rename("nav").to_csv(tmp_path / "n.csv")
    assert hashlib.sha256((tmp_path / "t.csv").read_bytes()).hexdigest() == BASE_TRADES_SHA
    assert hashlib.sha256((tmp_path / "n.csv").read_bytes()).hexdigest() == BASE_NAV_SHA


def test_lean_lookback_window_keeps_every_ticker_and_decision(lean_pair):
    vw, om, _ = lean_pair
    kw = dict(registry={}, ranking_files=vw.ranking_files, evidence_files=[])
    for launch in (date(2026, 3, 31), date(2026, 6, 30)):
        lm = LN.load(vw, LN.start_for(vw, launch.isoformat()))
        assert lm.m1.C.shape[1] == om.m1.C.shape[1] and lm.m1.C.shape[0] < om.m1.C.shape[0]
        a = EN.compute(om, CAL, launch, AS_OF, None, **kw); b = EN.compute(lm, CAL, launch, AS_OF, None, **kw)
        assert json.dumps(a["events"]) == json.dumps(b["events"]) and a["latest"] == b["latest"]
