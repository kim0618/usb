"""E-RT2.1 market-data-unavailable contract and E-RT3 Kiwoom RVOL store."""

from __future__ import annotations

from datetime import date, timedelta
import inspect
import json
from pathlib import Path

import numpy as np
import pytest

from app.backtest.strategy_e0_overnight.minute import SymbolTape
from app.backtest.strategy_e1_premarket.premarket import session_rows
from app.strategy_e_max_rt import availability as AV, rvol_store as RS

D = date(2026, 9, 22)
PRIOR = [D - timedelta(days=n) for n in range(1, 40)]


def outcome(symbol="X", *, finalized=True, contiguous=True, on_time=True, bars=5, errors=None):
    return AV.SymbolOutcome(symbol, finalized, contiguous, on_time, bars,
                            errors if errors is not None else {"A": None})


# -- RT2.1 -------------------------------------------------------------------------------------

def test_unavailable_is_not_sparse_and_not_stale() -> None:
    ps = outcome("PS", finalized=False, contiguous=False, bars=0,
                 errors={"A": "MARKET_DATA_UNAVAILABLE", "B": "MARKET_DATA_UNAVAILABLE"})
    assert AV.classify(ps) == AV.MARKET_DATA_UNAVAILABLE
    assert AV.classify(outcome(bars=0)) == AV.SPARSE_NO_PREMARKET
    assert AV.classify(outcome(bars=12)) == AV.FEATURE_COMPLETE
    assert AV.classify(outcome(errors={"A": "PROVIDER_TIMEOUT"})) == AV.STALE      # transport, not refusal
    assert AV.classify(outcome(on_time=False)) == AV.STALE


def test_unavailable_h5_is_unknown_never_false() -> None:
    assert AV.h5_status(AV.MARKET_DATA_UNAVAILABLE, None) == AV.H5_UNKNOWN
    assert AV.h5_status(AV.STALE, True) == AV.H5_UNKNOWN
    assert AV.h5_status(AV.SPARSE_NO_PREMARKET, False) == AV.H5_FALSE
    assert AV.h5_status(AV.FEATURE_COMPLETE, True) == AV.H5_TRUE


def test_unavailable_cannot_be_selected_and_nothing_backfills() -> None:
    assert not AV.executable(AV.MARKET_DATA_UNAVAILABLE) and not AV.executable(AV.STALE)
    assert AV.executable(AV.FEATURE_COMPLETE) and AV.executable(AV.SPARSE_NO_PREMARKET)
    text = inspect.getsource(AV)
    assert "NO BACKFILL" in text and "never replaced by the next candidate" in AV.diagnostics(
        {}, {}, 0, [])["no_backfill"]


def test_canonical_denominator_is_the_frozen_seal_count() -> None:
    states = {"AAA": AV.FEATURE_COMPLETE, "BBB": AV.SPARSE_NO_PREMARKET, "PS": AV.MARKET_DATA_UNAVAILABLE}
    d = AV.diagnostics(states, {"AAA": AV.H5_TRUE, "BBB": AV.H5_FALSE, "PS": AV.H5_UNKNOWN},
                       eligible_rows=854, candidates=["AAA"])
    assert d["breadth_denominator_eligible_rows"] == 854          # unchanged by the contract
    assert d["canonical_universe"] == 3 and d["market_data_unavailable"] == 1
    assert d["h5_true"] == 1 and d["h5_false"] == 1 and d["h5_unknown"] == 1
    assert d["market_data_unavailable_symbols"] == ["PS"] and 0 < d["market_data_unavailable_share"] < 1


def test_ps_fixture_and_a_normal_symbol_side_by_side() -> None:
    states = {s: AV.classify(o) for s, o in {
        "PS": outcome("PS", finalized=False, contiguous=False, bars=0,
                      errors={"A": "MARKET_DATA_UNAVAILABLE", "B": "MARKET_DATA_UNAVAILABLE"}),
        "AAPL": outcome("AAPL", bars=300)}.items()}
    assert states == {"PS": AV.MARKET_DATA_UNAVAILABLE, "AAPL": AV.FEATURE_COMPLETE}
    d = AV.diagnostics(states, {"PS": AV.H5_UNKNOWN, "AAPL": AV.H5_FALSE}, 854, [])
    assert d["stale"] == 0 and d["feature_complete"] == 1


def test_session_is_not_blocked_by_unavailable_symbols() -> None:
    states = {f"S{i}": AV.FEATURE_COMPLETE for i in range(100)} | {"PS": AV.MARKET_DATA_UNAVAILABLE}
    d = AV.diagnostics(states, {}, 90, [])
    assert d["market_data_unavailable"] == 1 and d["market_data_unavailable_share"] == pytest.approx(1 / 101)
    assert "threshold" not in json.dumps(d)                       # no tolerated share invented here


# -- RT3 store ---------------------------------------------------------------------------------

def _tape(sessions, *, dollars_per_session, bars=3, open_bar=True):
    """A synthetic Kiwoom-like tape: premarket bars plus (optionally) the 09:30 bar."""
    days, minutes, price, volume = [], [], [], []
    for i, value in enumerate(dollars_per_session):
        day = sessions[i].toordinal() - date(1970, 1, 1).toordinal()
        for k in range(bars):
            days.append(day)
            minutes.append(4 * 60 + k)
            price.append(10.0)
            volume.append(value / bars / 10.0)
        if open_bar:
            days.append(day)
            minutes.append(9 * 60 + 30)
            price.append(10.0)
            volume.append(1000.0)
    n = len(days)
    arr = lambda x: np.array(x, dtype=float)  # noqa: E731
    return SymbolTape("X", np.array(days), np.array(minutes), arr(price), arr(price), arr(price),
                      arr(price), arr(volume), np.full(n, np.nan), {}, 0)


def test_store_denominator_matches_the_frozen_rvol(tmp_path) -> None:
    sessions = [D - timedelta(days=30 - n) for n in range(25)]
    dollars = [1000.0 * (n + 1) for n in range(25)]
    frozen = session_rows(_tape(sessions, dollars_per_session=dollars))
    store = RS.RvolStore(tmp_path / "rvol.sqlite3")
    for day, value in zip(sessions, dollars):
        store.put(RS.SessionRecord("X", day, value, 3, True))
    for i, day in enumerate(sessions):
        row = frozen[day.toordinal() - date(1970, 1, 1).toordinal()]
        mine = store.rvol("X", day, dollars[i])
        if np.isnan(row.pm_rvol):
            assert mine is None
        else:
            assert mine == pytest.approx(row.pm_rvol, rel=0, abs=1e-12)
    assert (RS.RVOL_WINDOW, RS.RVOL_MINIMUM) == (20, 5)


def test_staging_rule_and_zero_volume_sessions(tmp_path) -> None:
    store = RS.RvolStore(tmp_path / "s.sqlite3")
    day = D - timedelta(days=10)
    assert RS.SessionRecord("X", day, 100.0, 3, True).staged
    assert not RS.SessionRecord("X", day, 100.0, 3, False).staged          # no 09:30 bar
    assert not RS.SessionRecord("X", day, 0.0, 0, True).staged             # no premarket bar
    for n in range(6):
        store.put(RS.SessionRecord("X", D - timedelta(days=10 + n), 0.0 if n < 3 else 500.0, 3, True))
    assert store.staged_count("X", D) == 3 and store.denominator("X", D) is None   # zeros excluded


def test_from_minute_rows_uses_close_times_volume_in_the_frozen_window() -> None:
    def row(h, m, close, vol):
        return {"cntr_tm": f"20260922{h:02d}{m:02d}00", "bus_dt": "20260922", "open_pric": str(close),
                "high_pric": str(close), "low_pric": str(close), "cur_prc": str(close), "trde_qty": str(vol)}
    rows = [row(3, 59, 10, 100), row(4, 0, 10, 100), row(9, 24, 20, 50), row(9, 25, 30, 999), row(9, 30, 11, 5)]
    record = RS.from_minute_rows("X", D, rows)
    assert record.pm_bars == 2 and record.pm_dollar_volume == pytest.approx(10 * 100 + 20 * 50)
    assert record.has_open_0930 and record.staged


def test_append_today_is_idempotent_and_conflicts_are_visible(tmp_path) -> None:
    store = RS.RvolStore(tmp_path / "s.sqlite3")
    record = RS.SessionRecord("X", D, 1234.0, 5, True)
    assert store.append_today(record) == "WRITTEN"
    assert store.append_today(record) == "KEPT"
    assert store.append_today(RS.SessionRecord("X", D, 9999.0, 5, True)) == "CONFLICT"
    assert store.staged_history("X", D + timedelta(days=1)) == [1234.0]


def test_missing_only_planner_and_coverage(tmp_path) -> None:
    store = RS.RvolStore(tmp_path / "s.sqlite3")
    for n in range(22):
        store.put(RS.SessionRecord("FULL", D - timedelta(days=n + 1), 100.0, 3, True))
    for n in range(7):
        store.put(RS.SessionRecord("PART", D - timedelta(days=n + 1), 100.0, 3, True))
    plan = store.bootstrap_plan(["FULL", "PART", "NEW"], D)
    assert plan["already_complete"] == 1 and plan["per_symbol"] == {"PART": 13, "NEW": 20}
    assert plan["sessions_needed_total"] == 33
    coverage = store.coverage(["FULL", "PART", "NEW"], D)
    assert coverage["fully_ready"] == 1 and coverage["zero_history"] == 1
    assert coverage["distribution"]["5-19"] == 1 and coverage["window"] == 20


def test_preload_matches_per_symbol_denominator(tmp_path) -> None:
    store = RS.RvolStore(tmp_path / "s.sqlite3")
    for symbol, base in (("A", 100.0), ("B", 250.0)):
        for n in range(10):
            store.put(RS.SessionRecord(symbol, D - timedelta(days=n + 1), base * (n + 1), 3, True))
    loaded = store.preload(["A", "B", "C"], D)
    assert loaded["A"] == store.denominator("A", D) and loaded["B"] == store.denominator("B", D)
    assert loaded["C"] is None


def test_rolling_window_eviction_keeps_the_frozen_window(tmp_path) -> None:
    store = RS.RvolStore(tmp_path / "s.sqlite3")
    days = [D - timedelta(days=n + 1) for n in range(40)]
    for day in days:
        store.put(RS.SessionRecord("X", day, 100.0, 3, True))
    before = store.denominator("X", D)
    assert store.evict_before("X", D - timedelta(days=RS.RVOL_WINDOW)) == 20
    assert store.denominator("X", D) == before and store.staged_count("X", D) == 20


def test_insufficient_history_versus_missing_data(tmp_path) -> None:
    store = RS.RvolStore(tmp_path / "s.sqlite3")
    window = PRIOR[:RS.RVOL_WINDOW]
    for day in window:                                  # every session collected, few staged
        store.put(RS.SessionRecord("NEWLY", day, 0.0, 0, False))
    assert store.readiness("NEWLY", D, window) == RS.NAN_BY_RULE
    assert store.readiness("NEVER", D, window) == RS.DATA_MISSING
    for day in window:
        store.put(RS.SessionRecord("GOOD", day, 500.0, 3, True))
    assert store.readiness("GOOD", D, window) == RS.READY


def test_source_label_and_no_massive(tmp_path) -> None:
    assert (RS.SOURCE, RS.EVIDENCE_CLASS) == ("KIWOOM", "KIWOOM_NATIVE")
    text = " ".join(inspect.getsource(RS).split())
    assert "Massive volume is never written here" in text
    store = RS.RvolStore(tmp_path / "s.sqlite3")
    store.put(RS.SessionRecord("X", D, 1.0, 1, True))
    assert store.connection.execute("select distinct source from kiwoom_premarket_sessions").fetchall() == [("KIWOOM",)]


def test_e_stays_disabled_and_a_is_untouched_by_preparation() -> None:
    from app.strategy_e_max_rt import config as CFG
    assert CFG.from_env({}).enabled is False and CFG.LIVE_MARGIN_APPROVED is False
    text = inspect.getsource(RS) + inspect.getsource(AV)
    for banned in ("SimBroker", "submit_order", "usb-backend", "strategy_states"):
        assert banned not in text


def test_rt2_status_rows_are_reread_under_the_contract() -> None:
    assert AV.reclassify("STALE", "MarketDataError") == AV.MARKET_DATA_UNAVAILABLE       # RT2's PS row
    assert AV.reclassify("SPARSE_NO_PREMARKET", "") == AV.SPARSE_NO_PREMARKET            # RT2's AAMI/BH.A rows
    assert AV.reclassify("FEATURE_COMPLETE", None) == AV.FEATURE_COMPLETE
    assert AV.reclassify("STALE", "PROVIDER_TIMEOUT") == AV.STALE


def test_outcome_from_a_finalizer_cache() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.strategy_e_max_rt.finalizer import SymbolCache
    et = ZoneInfo("America/New_York")
    deadline = datetime(2026, 9, 22, 9, 29, 45, tzinfo=et)
    ok = SymbolCache("AAPL", "ND")
    ok.bars = {m: [1.0] * 5 for m in range(4 * 60, 9 * 60 + 25)}
    ok.finalized_at, ok.contiguous, ok.data_source = datetime(2026, 9, 22, 9, 26, tzinfo=et), True, "KIWOOM_usa06011"
    assert AV.classify(AV.outcome_from_cache(ok, deadline=deadline)) == AV.FEATURE_COMPLETE
    dead = SymbolCache("PS", "ND")
    dead.error = "MARKET_DATA_UNAVAILABLE"
    assert AV.classify(AV.outcome_from_cache(dead, deadline=deadline,
                                             lane_errors={"usa06011": "MARKET_DATA_UNAVAILABLE",
                                                          "usa06010": "MARKET_DATA_UNAVAILABLE"})) == AV.MARKET_DATA_UNAVAILABLE


def test_decision_reads_the_store_and_unavailable_rows_stay_unknown(tmp_path) -> None:
    """The wiring the runtime uses: store -> denominator -> rvol -> frozen H5/R1/B2, with the
    unavailable and the denominator-less rows fail-closed and the B2 denominator untouched."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
    from app.strategy_e_max_rt import decision as DEC
    from app.strategy_e_v1_1.universe import DecisionFrame

    store = RS.RvolStore(tmp_path / "s.sqlite3")
    for n in range(RS.RVOL_WINDOW):
        store.put(RS.SessionRecord("HAS", D - timedelta(days=n + 1), 1_000_000.0, 30, True))
    denominators = store.preload(["HAS", "NONE"], D)
    assert denominators["HAS"] == 1_000_000.0 and denominators["NONE"] is None

    today = {"HAS": 8_000_000.0, "NONE": 8_000_000.0}
    rows, unknown = {}, []
    for symbol in ("HAS", "NONE"):
        denominator = denominators[symbol]
        if denominator is None:
            unknown.append(symbol)
        rows[symbol] = dict.fromkeys(SEALED_FEATURES, 0.0) | {
            "premarket_gap": 0.06, "premarket_rvol": (today[symbol] / denominator) if denominator else float("nan"),
            "position_in_premarket_range": 0.95, "return_0900_0925": 0.01, "close_price": 10.0,
            "premarket_dollar_volume": today[symbol], "previous_day_dollar_volume": 5e7, "pm_bars": 30}
    symbols = tuple(sorted(rows))
    frame = DecisionFrame(D, symbols, {n: np.array([rows[s][n] for s in symbols], dtype=float) for n in SEALED_FEATURES}, {})
    decided = DEC.decide_from_frame(frame, source_digest="x", source="KIWOOM_NATIVE_TEST",
                                    decided_at=datetime(2026, 9, 22, 9, 25, tzinfo=ZoneInfo("America/New_York")))
    assert "HAS" in decided.candidates and "NONE" not in decided.candidates        # NaN rvol cannot pass H5
    assert decided.universe_rows == 2                                              # both rows still count
    h5 = {s: (AV.H5_UNKNOWN if s in unknown else (AV.H5_TRUE if s in decided.candidates else AV.H5_FALSE))
          for s in symbols}
    diagnostics = AV.diagnostics({s: AV.FEATURE_COMPLETE for s in symbols}, h5,
                                 decided.universe_rows, decided.candidates)
    assert diagnostics["h5_unknown"] == 1 and diagnostics["breadth_denominator_eligible_rows"] == 2
