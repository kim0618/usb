"""The producer's morning on a virtual clock: both seals, the clock-driven E start, the deadlines,
parity, and failure isolation between the A and E sides."""

from datetime import time

import pytest

from app.ae_common import producer as P
from app.ae_common import snapshot as SS
from app.strategy_e_max_rt.rvol_store import RvolStore
from tests.ae_common.helpers import SESSION, Clock, at, lanes, minute_rows

A_SYMBOLS = tuple(f"P{i:02d}" for i in range(30))
E_SYMBOLS = A_SYMBOLS[:12] + ("EONLY1", "EONLY2")
NOT_LISTED = "P29"                                       # in A's union, absent from Kiwoom's listing


def art():
    return {"symbols": list(E_SYMBOLS), "digest": "e-digest",
            "close_d_minus_1": {s: 9.5 for s in E_SYMBOLS},
            "dollar_volume_d_minus_1": {s: 2.0e7 for s in E_SYMBOLS}, "spy_close_d_minus_1": 500.0}


def morning(tmp_path, *, start=time(9, 10), e_art=True, store=True):
    clock = Clock(at(start))
    symbols = sorted(set(A_SYMBOLS) | set(E_SYMBOLS) | {"SPY"})
    pages = {s: minute_rows(SESSION, 9 * 60 + 40, price=10.0 + i * 0.1) for i, s in enumerate(symbols)}
    lane_minute, lane_tick, clients, limiters = lanes(pages, clock)
    rvol = tmp_path / "rvol.sqlite3"
    if store:
        RvolStore(rvol).connection.close()
    inputs = P.Inputs(session=SESSION, exchanges={s: "ND" for s in symbols if s != NOT_LISTED},
                      a_symbols=A_SYMBOLS, a_union_checksum="u", e_art=art() if e_art else None,
                      e_refusal=None if e_art else "UNIVERSE_NOT_AVAILABLE: test")
    phases = []
    result = P.run_session(inputs, lane_minute=lane_minute, lane_tick=lane_tick, limiters=limiters,
                           clock=P.Clock(now=clock, sleep=clock.tick, session=SESSION),
                           snapshot_dir=tmp_path / "snap", rvol_store=rvol, log=lambda text: None,
                           on_phase=phases.append)
    return result, clients, limiters, phases, clock


def test_the_morning_seals_both_snapshots_and_e_starts_on_the_clock(tmp_path):
    result, clients, limiters, phases, _ = morning(tmp_path)
    assert phases == ["rolling", "a_sweep", "e_finalization", "closed"]
    assert result.seal_0915["status"] == "SEALED"
    summary = result.seal_0915["manifest"]["summary"]
    assert summary["complete_for_a"] is True and summary["a_status_counts"].get(SS.UNMAPPED) == 1
    fin = result.report["e_finalization"]
    assert fin["T0"] == at(time(9, 25)).isoformat() and fin["T0_delay_s"] == 0
    assert fin["future_cutoff_violation_count"] == 0
    assert result.e_shadow["status"] == "DECIDED"
    assert result.e_shadow["parity"]["exact"] is True
    assert result.e_shadow["real_orders"] == 0 and result.e_shadow["book_written"] is False
    assert result.e_shadow["paper_runtime"]["reason"] == "E_PAPER_NOT_ENABLED"
    assert result.a_parity["comparison"]["exact"] is True
    assert result.a_parity["bars_at_or_after_cut_in_file"] == 0
    assert result.report["rolling_order_unique"] is True


def test_no_request_leaves_after_its_phase_deadline(tmp_path):
    result, clients, limiters, _, _ = morning(tmp_path)
    for limiter in limiters:
        assert all(p["last_sent_before_deadline"] for p in limiter.declaration()["phases"])
    closed = at(time(9, 29, 45))
    assert all(sent < closed for c in clients for sent, _, _ in c.sent)
    sweep = [sent for c in clients for sent, _, _ in c.sent if at(time(9, 15)) <= sent < at(time(9, 25))]
    assert sweep and max(sweep) < at(time(9, 25))


def test_one_app_key_two_lanes_and_no_symbol_read_twice_in_the_sweep(tmp_path):
    result, clients, _, _, _ = morning(tmp_path)
    sweep_reads = [symbol for c in clients for sent, _, symbol in c.sent if at(time(9, 15)) <= sent < at(time(9, 25))]
    assert len(sweep_reads) == len(set(sweep_reads))
    assert NOT_LISTED not in {symbol for c in clients for _, _, symbol in c.sent}


def test_a_seal_failure_leaves_e_untouched(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(SS, "body_0915", broken)
    result, _, _, _, _ = morning(tmp_path)
    assert result.seal_0915["status"] == "FAILED"
    assert result.report["e_finalization"]["T0_delay_s"] == 0
    assert result.e_shadow["status"] == "DECIDED" and result.e_shadow["parity"]["exact"]
    assert not SS.exists(tmp_path / "snap", SS.SNAPSHOT_0915)


def test_an_e_failure_leaves_a_untouched(tmp_path):
    result, _, _, _, _ = morning(tmp_path, store=False)            # E's RVOL store is missing
    assert result.e_shadow["status"] == "NO_DECISION" and result.e_shadow["reason"] == "E_DECISION_FAILED"
    assert result.seal_0915["status"] == "SEALED" and result.a_parity["comparison"]["exact"]


def test_without_an_e_universe_e_records_no_decision_and_a_still_gets_its_snapshot(tmp_path):
    result, _, _, phases, _ = morning(tmp_path, e_art=False)
    assert result.e_shadow["status"] == "NO_DECISION" and result.e_shadow["reason"] == "UNIVERSE_NOT_AVAILABLE"
    assert "e_finalization" not in phases
    assert result.seal_0915["status"] == "SEALED"


def test_the_e_decision_module_never_imports_an_execution_path():
    import ast
    from pathlib import Path
    source = (Path(P.__file__).parent / "e_shadow.py").read_text(encoding="utf-8")
    modules = {n.module for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ImportFrom) and n.module}
    assert not modules & {"app.strategy_e_max_rt.paper", "app.strategy_e_max_rt.engine", "app.broker.sim_broker",
                          "app.market.factory"}


def test_a_symbol_the_source_refuses_all_morning_leaves_a_complete_and_a_documented_one_too(tmp_path):
    from tests.ae_common import helpers as H
    clock = Clock(at(time(9, 10)))
    symbols = sorted(set(A_SYMBOLS) | set(E_SYMBOLS) | {"SPY"})
    pages = {s: minute_rows(SESSION, 9 * 60 + 40) for s in symbols}
    lane_minute, lane_tick, _, limiters = H.lanes(pages, clock, fail=("P20",))      # A-only, refused
    rvol = tmp_path / "rvol.sqlite3"
    RvolStore(rvol).connection.close()
    inputs = P.Inputs(session=SESSION, exchanges={s: "ND" for s in symbols}, a_symbols=A_SYMBOLS,
                      a_union_checksum="u", e_art=art(), documented_refusals=("P21",))
    result = P.run_session(inputs, lane_minute=lane_minute, lane_tick=lane_tick, limiters=limiters,
                           clock=P.Clock(now=clock, sleep=clock.tick, session=SESSION),
                           snapshot_dir=tmp_path / "snap", rvol_store=rvol, log=lambda text: None)
    summary = result.seal_0915["manifest"]["summary"]
    body = SS.load(tmp_path / "snap", SS.SNAPSHOT_0915).body
    assert body["symbols"]["P20"]["status"] == SS.MARKET_DATA_UNAVAILABLE
    assert body["symbols"]["P21"]["status"] == SS.USABLE        # documented, but served today: used
    assert summary["complete_for_a"] is True and summary["permanent_exceptions"] == 1


def test_documented_refusals_are_read_from_e_store_read_only(tmp_path):
    rvol = tmp_path / "rvol.sqlite3"
    store = RvolStore(rvol)
    store.connection.execute("INSERT INTO kiwoom_collection_state VALUES ('PS', 0, NULL, 0, '2026-10-17', "
                             "'MARKET_DATA_UNAVAILABLE')")
    store.connection.execute("INSERT INTO kiwoom_collection_state VALUES ('KHC', 3, NULL, 0, '2026-10-17', "
                             "'STORE_CONFLICT')")
    store.connection.commit()
    store.connection.close()
    before = rvol.read_bytes()
    assert P.documented_refusals(rvol) == (("PS",), None)
    assert rvol.read_bytes() == before
    assert P.documented_refusals(tmp_path / "missing.sqlite3")[0] == ()


# -- E official paper from snapshot_0924 -------------------------------------------------------------

WINNERS = ("P00", "P01")


def trend_rows(last_minute, *, start, slope):
    stamp = SESSION.strftime("%Y%m%d")
    rows = []
    for minute in range(last_minute, 239, -1):
        p = start + slope * (minute - 240)
        rows.append({"bus_dt": stamp, "cntr_tm": f"{stamp}{minute // 60:02d}{minute % 60:02d}00",
                     "open_pric": f"{p:.4f}", "high_pric": f"{p + 0.2:.4f}", "low_pric": f"{p - 0.2:.4f}",
                     "cur_prc": f"{p + 0.1:.4f}", "trde_qty": "5000"})
    return rows


def seeded_store(path, symbols, *, sessions=20, value=1000.0):
    import sqlite3
    from datetime import timedelta
    RvolStore(path).connection.close()
    connection = sqlite3.connect(path)
    day = SESSION
    filled = 0
    while filled < sessions:
        day -= timedelta(days=1)
        if day.weekday() >= 5:
            continue
        filled += 1
        for s in symbols:
            connection.execute("INSERT INTO kiwoom_premarket_sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (s, day.isoformat(), value, 50, 1, 1, "KIWOOM", 1, "x", f"{day.isoformat()}T15:00:00+00:00"))
    connection.commit()
    connection.close()


def paper_morning(tmp_path, monkeypatch, *, seeded=True, skip_0934=()):
    from app.dev import run_e_rt2_dryrun as RT2
    from app.market.calendar import MarketCalendar
    from tests.strategy_e_max.test_e_rt0_runtime import FakeProvider, bar
    clock = Clock(at(time(9, 10)))
    symbols = sorted(set(A_SYMBOLS) | set(E_SYMBOLS) | {"SPY"})
    pages = {s: trend_rows(9 * 60 + 40, start=10.0, slope=0.01 if s in WINNERS or s == "SPY" else -0.002)
             for s in symbols}
    lane_minute, lane_tick, _, limiters = lanes(pages, clock)
    rvol = tmp_path / "rvol.sqlite3"
    if seeded:
        seeded_store(rvol, E_SYMBOLS)
    else:
        RvolStore(rvol).connection.close()
    day = []
    for s in E_SYMBOLS:
        day.append(bar(s, 9, 30, 20.0, 20.1, day=SESSION))
        for m in (31, 32, 33):
            day.append(bar(s, 9, m, 20.1, 20.1, day=SESSION))
        if s not in skip_0934:
            day.append(bar(s, 9, 34, 20.1, 21.0, day=SESSION))
        day.append(bar(s, 9, 35, 22.0, 22.0, day=SESSION))     # never to be used
    monkeypatch.setattr(RT2.time, "sleep", clock.tick)
    e_paper = P.EPaper(book_root=tmp_path / "erun", provider_factory=lambda: FakeProvider(day, clock),
                       calendar=MarketCalendar("America/New_York"), equity="10000", interval=10.0)
    inputs = P.Inputs(session=SESSION, exchanges={s: "ND" for s in symbols}, a_symbols=A_SYMBOLS,
                      a_union_checksum="u", e_art=art())
    result = P.run_session(inputs, lane_minute=lane_minute, lane_tick=lane_tick, limiters=limiters,
                           clock=P.Clock(now=clock, sleep=clock.tick, session=SESSION),
                           snapshot_dir=tmp_path / "snap", rvol_store=rvol, log=lambda text: None, e_paper=e_paper)
    book_dir = tmp_path / "erun" / "paper_state_v2" / "OFFICIAL_KIWOOM_PAPER" / "STRATEGY_E_MAX_V1"
    return result, book_dir


def test_an_authorized_snapshot_decision_enters_at_the_0930_open_and_exits_at_the_0934_close(tmp_path, monkeypatch):
    import json
    from decimal import Decimal
    result, book_dir = paper_morning(tmp_path, monkeypatch)
    e = result.e_shadow
    assert e["from_snapshot"]["gate"]["authorized"] is True
    assert tuple(e["from_snapshot"]["decision"]["selected"]) == WINNERS and e["parity"]["exact"]
    paper = e["paper_runtime"]
    # R1's _paper_stage reports the ledger dict under "book" (its name is reused); book_dir names the book
    assert paper["ran"] is True and paper["book_dir"].endswith("paper_state_v2/OFFICIAL_KIWOOM_PAPER")
    assert paper["real_orders"] == 0
    assert paper["execution_version"] == "e_cost_v2_exact_0934_close"
    state = json.loads((book_dir / "sessions" / f"{SESSION.isoformat()}.json").read_text())
    for symbol in WINNERS:
        entry, exit_ = state["entries"][symbol], state["exits"][symbol]
        assert entry["fill_bar"]["timestamp"].startswith(f"{SESSION.isoformat()}T09:30")
        assert entry["raw_market_price"] == "20.0"                       # the 09:30 open
        assert exit_["fill_bar"]["timestamp"].startswith(f"{SESSION.isoformat()}T09:34")
        assert exit_["raw_market_price"] == "21.0" and exit_["fill_price_field"] == "close"
        assert exit_["exit_flag"] == "EXACT_0934_CLOSE"                  # never the 09:35 open (22.0)
    book = json.loads((book_dir / "book.json").read_text())
    assert book["initial_equity"] == "10000"
    session = book["sessions"][SESSION.isoformat()]
    assert Decimal(session["equity_after"]) == Decimal("10000") + Decimal(session["sim_realized_pnl"])
    assert Decimal(session["sim_realized_pnl"]) > 0 and book["execution_version"] == "e_cost_v2_exact_0934_close"


def test_a_missing_0934_bar_is_unresolved_in_the_official_book(tmp_path, monkeypatch):
    import json
    result, book_dir = paper_morning(tmp_path, monkeypatch, skip_0934=("P00",))
    state = json.loads((book_dir / "sessions" / f"{SESSION.isoformat()}.json").read_text())
    assert state["exits"]["P00"]["status"] == "UNRESOLVED_EXIT"
    assert state["exits"]["P00"]["reason"] == "NO_EXACT_0934_BAR"
    assert state["exits"]["P01"]["exit_flag"] == "EXACT_0934_CLOSE"


def test_an_unauthorized_gate_is_recorded_in_the_book_and_never_traded(tmp_path, monkeypatch):
    import json
    result, book_dir = paper_morning(tmp_path, monkeypatch, seeded=False)     # no RVOL history
    e = result.e_shadow
    assert e["from_snapshot"]["gate"]["authorized"] is False
    assert e["paper_runtime"]["ran"] is False and e["paper_runtime"]["entries"] == 0
    state = json.loads((book_dir / "sessions" / f"{SESSION.isoformat()}.json").read_text())
    assert state["phase"] == "NO_DECISION" and not state["entries"]
    assert result.seal_0915["status"] == "SEALED"                              # A untouched
