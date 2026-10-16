"""E-RUNTIME-R1: E's runtime is independent of A, its decision is executed only when it is whole and
on time, and its exit is the frozen exact 09:34 close. Strategy rules are untouched."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.dev import run_a_mover_live as AW
from app.dev import run_e_rt2_dryrun as RUN
from app.integrations.kiwoom import lane_share as SHARE
from app.strategy_e_max_rt import availability as AV, config as CFG, cost as COST, engine as ENG

from tests.strategy_e_max.test_e_rt0_runtime import (
    CAL, Clock, CountingSource, D, FakeProvider, at, bar, frame, run_day,
)

V2 = COST.official_execution(COST.EXECUTION_VERSION_V2)


def make_v2(tmp_path, bars, fr=None, equity="10000"):
    clock = Clock(at(9, 0))
    fr = fr or frame()
    provider = FakeProvider(bars, clock)
    cfg = CFG.RuntimeConfig(enabled=True, state_dir=tmp_path / "rt", initial_equity=Decimal(equity))
    eng = ENG.Engine(cfg, lambda: provider, CountingSource(fr), CAL, ENG.Store(cfg.state_dir, cfg.strategy_id),
                     execution=V2)
    return eng, clock, provider, fr


def day_bars(symbols, *, close_0934=21.0, open_0935=22.0, skip_0934=()):
    out = []
    for s in symbols:
        out.append(bar(s, 9, 30, 20.0, 20.1))
        for m in (31, 32, 33):
            out.append(bar(s, 9, m, 20.1, 20.1))
        if s not in skip_0934:
            out.append(bar(s, 9, 34, 20.1, close_0934))
        out.append(bar(s, 9, 35, open_0935, open_0935))
    return out


# -- 1. A is not in E's process -----------------------------------------------------------------

def test_e_runner_has_no_a_call_left() -> None:
    source = inspect.getsource(RUN)
    for needle in ("strategy_a_mover_live", "attach_isolated", "run_cut", "rolling_order", "rolling_until"):
        assert needle not in source, needle


def test_importing_and_starting_the_e_runner_never_loads_a() -> None:
    code = ("import sys, app.dev.run_e_rt2_dryrun as R; import inspect; inspect.signature(R.run); "
            "print(any(m.startswith('app.strategy_a_mover_live') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parents[2], check=True)
    assert out.stdout.strip() == "False"


def test_e_t0_is_fixed_by_the_clock_even_with_the_a_flag_on(monkeypatch) -> None:
    """The A flag is not read by E: the only waits before T0 are clock waits on E's own windows."""
    monkeypatch.setenv("A_MOVER_LIVE_ENABLED", "true")
    source = inspect.getsource(RUN.run)
    assert "A_MOVER_LIVE" not in source and "environ.get(\"A_" not in source
    assert "rolling_deadline = at(SHARE.A_CUT)" in source
    assert "while now() < at(FZ.FINALIZE_AT)" in source


# -- 2. the key is shared by clock windows ------------------------------------------------------

def _moments():
    t = datetime(2026, 10, 19, 3, 59, 0)
    while t.time() < time(9, 31):
        yield t.time()
        t += timedelta(seconds=5)


def test_lane_share_never_oversubscribes_either_api() -> None:
    for moment in _moments():
        assert SHARE.combined_rate(moment, "minute") <= SHARE.FULL_RATE + 1e-9, moment
        assert SHARE.combined_rate(moment, "tick") <= SHARE.FULL_RATE + 1e-9, moment


def test_e_owns_both_apis_alone_for_its_finalization() -> None:
    session = date(2026, 10, 19)
    e_min, e_tick = SHARE.ScheduledRateLimiter(session, SHARE.E_MINUTE), SHARE.ScheduledRateLimiter(session, SHARE.E_TICK)
    a_min, a_tick = SHARE.ScheduledRateLimiter(session, SHARE.A_MINUTE), SHARE.ScheduledRateLimiter(session, SHARE.A_TICK)
    for moment in (time(9, 25), time(9, 27), time(9, 29, 44)):
        when = datetime.combine(session, moment, tzinfo=SHARE.ET)
        assert e_min.window(when)[0] == e_tick.window(when)[0] == SHARE.FULL_RATE
        assert a_min.window(when) == (0.0, None) and a_tick.window(when) == (0.0, None)
    # E's tick-shard refresh keeps its pre-10-05 budget: the minute API, alone, from 09:20:40
    when = datetime.combine(session, time(9, 21), tzinfo=SHARE.ET)
    assert e_min.window(when)[0] == SHARE.FULL_RATE and a_min.window(when)[0] == 0.0


def test_limiter_waits_for_the_next_window_and_closes_after_the_last() -> None:
    session = date(2026, 10, 19)
    moments = [datetime.combine(session, time(9, 16), tzinfo=SHARE.ET)]
    slept = []

    def sleeper(seconds):
        slept.append(seconds)
        moments[0] += timedelta(seconds=seconds)

    e_min = SHARE.ScheduledRateLimiter(session, SHARE.E_MINUTE, clock=lambda: moments[0], sleeper=sleeper)
    e_min.acquire()                                  # 09:16: E waits for its 09:20:40 refresh window
    assert moments[0].time() >= time(9, 20, 40) and slept
    moments[0] = datetime.combine(session, time(9, 30), tzinfo=SHARE.ET)
    with pytest.raises(SHARE.KiwoomWindowClosed):
        e_min.acquire()


def test_a_minute_lane_blocks_until_the_hard_stop_then_refuses() -> None:
    session = date(2026, 10, 19)
    moments = [datetime.combine(session, time(9, 22), tzinfo=SHARE.ET)]

    def sleeper(seconds):
        moments[0] += timedelta(seconds=seconds)

    a_min = SHARE.ScheduledRateLimiter(session, SHARE.A_MINUTE, clock=lambda: moments[0], sleeper=sleeper,
                                       hold_until=SHARE.E_FINALIZE)
    with pytest.raises(SHARE.KiwoomWindowClosed):
        a_min.acquire()
    assert moments[0].time() >= time(9, 25)          # it held its claimed symbol, it did not race


# -- 3. the execution gate ----------------------------------------------------------------------

OPEN = datetime(2026, 10, 19, 9, 30, tzinfo=SHARE.ET)


def _gate(**kw):
    base = dict(decided_at=OPEN - timedelta(seconds=20), open_et=OPEN, statuses={"A": AV.FEATURE_COMPLETE},
                documented=set(), rvol_unknown=[], unreachable=set(), pending=[])
    return RUN.execution_gate(**(base | kw))


def test_gate_authorizes_a_whole_on_time_decision() -> None:
    gate = _gate()
    assert gate["authorized"] and gate["status"] == "AUTHORIZED" and gate["seal_margin_s"] == 20.0


def test_seal_at_or_after_0930_is_never_executed() -> None:
    assert _gate(decided_at=OPEN)["reasons"] == ["DECISION_AFTER_0930"]
    assert not _gate(decided_at=OPEN + timedelta(seconds=5))["authorized"]


def test_partial_universe_blocks_execution() -> None:
    statuses = {f"S{i}": AV.FEATURE_COMPLETE for i in range(1428)} | {f"T{i}": AV.STALE for i in range(1127)}
    gate = _gate(statuses=statuses)
    assert not gate["authorized"] and gate["status"] == "DECISION_INCOMPLETE_UNIVERSE"
    assert gate["unexpected_stale"] == 1127


def test_a_documented_source_refusal_is_allowed() -> None:
    gate = _gate(statuses={"A": AV.FEATURE_COMPLETE, "PS": AV.STALE}, documented={"PS"})
    assert gate["authorized"] and gate["documented_exceptions"] == ["PS"]
    assert not _gate(statuses={"A": AV.FEATURE_COMPLETE, "PS": AV.STALE, "X": AV.STALE},
                     documented={"PS"})["authorized"]


def test_unexpected_rvol_gap_and_incomplete_bootstrap_block() -> None:
    assert _gate(rvol_unknown=["NEW"])["reasons"] == ["RVOL_UNEXPECTED_MISSING"]
    assert _gate(rvol_unknown=["OLD"], unreachable={"OLD"})["authorized"]
    assert _gate(pending=["KHC"])["reasons"] == ["RVOL_BOOTSTRAP_INCOMPLETE"]


# -- 4. the exact 09:34 close ---------------------------------------------------------------------

def test_v2_exit_fills_at_the_0934_close_not_the_0935_open(tmp_path) -> None:
    fr = frame()
    eng, clock, provider, fr = make_v2(tmp_path, day_bars(fr.symbols, close_0934=21.0, open_0935=22.0), fr)
    state = run_day(eng, clock)
    assert state["phase"] == "COMPLETE"
    for symbol in state["decision"]["selected"]:
        exit_ = state["exits"][symbol]
        assert exit_["fill_bar"]["timestamp"].startswith(f"{D.isoformat()}T09:34")
        assert exit_["raw_market_price"] == "21.0" and exit_["fill_price_field"] == "close"
        assert exit_["exit_flag"] == "EXACT_0934_CLOSE"
        assert exit_["intent"]["reason"] == "E_MAX_V1_EXIT_EXACT_0934_CLOSE"


def test_v2_never_reaches_the_0935_open_path(tmp_path, monkeypatch) -> None:
    fr = frame()
    eng, clock, provider, fr = make_v2(tmp_path, day_bars(fr.symbols), fr)
    original = ENG.Engine._exit_exact_close
    calls = []
    monkeypatch.setattr(ENG.Engine, "_exit_exact_close", lambda self, *a: (calls.append(1), original(self, *a)))
    state = run_day(eng, clock)
    assert calls and all(e["exit_flag"] != "ON_TIME_0935_OPEN" for e in state["exits"].values())


def test_missing_0934_bar_is_unresolved_without_fallback(tmp_path) -> None:
    fr = frame()
    selected_first = "S03"                                  # highest RVOL in the synthetic frame
    eng, clock, provider, fr = make_v2(tmp_path, day_bars(fr.symbols, skip_0934=(selected_first,)), fr)
    state = run_day(eng, clock)
    exit_ = state["exits"][selected_first]
    assert exit_["status"] == "UNRESOLVED_EXIT" and exit_["reason"] == "NO_EXACT_0934_BAR"
    assert "raw_market_price" not in exit_ and exit_["fill_price_field"] is None
    # excluded from the book's PnL, entry cost included (E-D3: no synthetic return)
    others = [s for s in state["decision"]["selected"] if s != selected_first]
    book = json.loads((tmp_path / "rt" / CFG.STRATEGY_ID / "book.json").read_text())
    expected = sum(Decimal(state["entries"][s]["intent"]["quantity"]) * (Decimal("21.0") - Decimal("20.0"))
                   - Decimal(state["entries"][s]["fill_cost"]) for s in others)
    assert Decimal(book["realized_pnl"]) == expected


def test_a_late_0934_bar_is_waited_for_until_it_or_a_later_bar_exists(tmp_path) -> None:
    fr = frame()
    bars = [b for b in day_bars(fr.symbols) if b.timestamp.minute < 34]   # nothing after 09:33 yet
    eng, clock, provider, fr = make_v2(tmp_path, bars, fr)
    state = run_day(eng, clock, times=((9, 25), (9, 31), (9, 36)))
    assert state["phase"] == "POSITION_OPEN" and not state["exits"]


def test_v2_cost_is_10bp_of_entry_notional_once(tmp_path) -> None:
    fr = frame()
    eng, clock, provider, fr = make_v2(tmp_path, day_bars(fr.symbols), fr)
    state = run_day(eng, clock)
    for symbol in state["decision"]["selected"]:
        entry, exit_ = state["entries"][symbol], state["exits"][symbol]
        notional = Decimal(entry["intent"]["quantity"]) * Decimal(entry["raw_market_price"])
        assert Decimal(entry["fill_cost"]) == notional * Decimal("0.001")
        assert Decimal(exit_["fill_cost"]) == 0
    assert state["accounting_version"] == "V2" and state["execution_version"] == "e_cost_v2_exact_0934_close"


def test_exposure_target_actual_and_gap_are_recorded(tmp_path) -> None:
    fr = frame()
    eng, clock, provider, fr = make_v2(tmp_path, day_bars(fr.symbols), fr)
    summary = run_day(eng, clock)["summary"]
    target, actual = Decimal(summary["target_exposure"]), Decimal(summary["actual_exposure"])
    assert target in (Decimal(2), Decimal(3)) and 0 < actual <= target * Decimal("1.05")
    assert Decimal(summary["exposure_gap"]) == actual - target


def test_v2_engine_refuses_to_continue_a_v1_book(tmp_path) -> None:
    fr = frame()
    eng_v1 = ENG.Engine(CFG.RuntimeConfig(enabled=True, state_dir=tmp_path / "rt", initial_equity=Decimal("10000")),
                        lambda: FakeProvider(day_bars(fr.symbols), Clock(at(9, 0))), CountingSource(fr), CAL,
                        ENG.Store(tmp_path / "rt", CFG.STRATEGY_ID))
    eng_v1.store.save_book(eng_v1.store.load_book(Decimal("10000"), eng_v1.execution))
    eng, clock, provider, fr = make_v2(tmp_path, day_bars(fr.symbols), fr)
    clock.t = at(9, 25)
    assert eng.tick(clock.t)["error"] == "BOOK_EXECUTION_VERSION_MISMATCH"


# -- 5. the paper stage: V2 books only, gate respected ------------------------------------------

class _Decision:
    selected = ("S03",)

    def to_json(self):
        return {"selected": list(self.selected), "final_exposure": "2/1"}


def test_unauthorized_session_is_recorded_not_traded_and_old_books_untouched(tmp_path) -> None:
    v1 = tmp_path / "paper_state_v1" / "OFFICIAL_KIWOOM_PAPER" / CFG.STRATEGY_ID
    v1.mkdir(parents=True)
    (v1 / "book.json").write_text('{"equity": "10190.38592"}')
    before = (v1 / "book.json").read_bytes()
    gate = _gate(statuses={"X": AV.STALE})
    clock = Clock(at(9, 30))
    out = RUN._paper_stage(_Decision(), D, {"paper_evidence_status": "OFFICIAL_KIWOOM_PAPER"}, tmp_path, CAL,
                           clock, lambda t: datetime.combine(D, t, tzinfo=SHARE.ET), lambda m: None, "10000",
                           provider_factory=lambda: (_ for _ in ()).throw(AssertionError("no market data call")),
                           book=RUN.BOOK_OFFICIAL, gate=gate)
    assert out["ran"] is False and out["reason"] == "DECISION_INCOMPLETE_UNIVERSE"
    book = json.loads((tmp_path / "paper_state_v2" / "OFFICIAL_KIWOOM_PAPER" / CFG.STRATEGY_ID / "book.json").read_text())
    assert book["equity"] == "10000" and book["execution_version"] == "e_cost_v2_exact_0934_close"
    assert book["sessions"][D.isoformat()]["phase"] == "NO_DECISION"
    assert (v1 / "book.json").read_bytes() == before


def test_shadow_and_official_v2_books_are_separate(tmp_path) -> None:
    with pytest.raises(ValueError):
        RUN._paper_stage(_Decision(), D, {"paper_evidence_status": "X"}, tmp_path, CAL, Clock(at(9, 30)),
                         None, lambda m: None, "10000", book="PROVISIONAL_RVOL_BOOTSTRAP", gate=_gate())
    assert RUN.BOOK_SHADOW != RUN.BOOK_OFFICIAL and RUN.PAPER_STATE_V2 == "paper_state_v2"


# -- 6. A's own worker ---------------------------------------------------------------------------

def test_a_worker_with_the_flag_off_touches_nothing(tmp_path) -> None:
    assert AW.run(tmp_path / "a", environ={"A_MOVER_LIVE_ENABLED": "false"}) == 0
    assert not (tmp_path / "a").exists()


def test_a_worker_owns_its_union_and_splits_its_own_tick_shard() -> None:
    from app.strategy_e_max_rt import finalizer as FZ
    caches = {s: FZ.SymbolCache(s, "NA", s) for s in ("AAA", "BBB", "CCC", "DDD", "EEE")}
    handle = SimpleNamespace(caches={}, extra_caches=dict(caches), shard_minute=(), shard_tick=())
    AW.standalone_handle(handle, FZ.hash_lanes)
    assert handle.caches == caches and handle.extra_caches == {}
    assert sorted(handle.shard_minute + handle.shard_tick) == sorted(caches)
    assert not set(handle.shard_minute) & set(handle.shard_tick)


def test_an_a_failure_cannot_reach_e() -> None:
    """E's worker has no reference to A at all, so an A exception has nowhere to propagate."""
    source = inspect.getsource(RUN)
    assert "app.strategy_a_mover_live" not in source
    assert "app.strategy_e_max_rt" not in inspect.getsource(AW).replace("app.strategy_e_max_rt import finalizer", "")
