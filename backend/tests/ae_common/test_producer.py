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
    assert result.e_shadow["status"] == "SHADOW_DECIDED"
    assert result.e_shadow["parity"]["exact"] is True
    assert result.e_shadow["orders"] == 0 and result.e_shadow["book_written"] is False
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
    assert result.e_shadow["status"] == "SHADOW_DECIDED" and result.e_shadow["parity"]["exact"]
    assert not SS.exists(tmp_path / "snap", SS.SNAPSHOT_0915)


def test_an_e_failure_leaves_a_untouched(tmp_path):
    result, _, _, _, _ = morning(tmp_path, store=False)            # E's RVOL store is missing
    assert result.e_shadow["status"] == "NO_DECISION" and result.e_shadow["reason"] == "E_SHADOW_FAILED"
    assert result.seal_0915["status"] == "SEALED" and result.a_parity["comparison"]["exact"]


def test_without_an_e_universe_e_records_no_decision_and_a_still_gets_its_snapshot(tmp_path):
    result, _, _, phases, _ = morning(tmp_path, e_art=False)
    assert result.e_shadow["status"] == "NO_DECISION" and result.e_shadow["reason"] == "UNIVERSE_NOT_AVAILABLE"
    assert "e_finalization" not in phases
    assert result.seal_0915["status"] == "SEALED"


def test_the_e_side_never_imports_an_execution_path():
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
