"""E-RT0: E-MAX V1 on the common virtual runtime. Fake provider, synthetic 09:25 frames, tmp state."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
from app.broker.sim import SimBroker
from app.execution.domain import IntentType, OrderIntent, OrderSide
from app.market.calendar import MarketCalendar
from app.market.domain import MarketSession, MinuteBar
from app.market.provider import MarketDataProvider
from app.strategy_e_max import v1
from app.strategy_e_max_forward import rules as FR
from app.strategy_e_max_rt import config as CFG, decision as DEC, engine as ENG, runtime as RT
from app.strategy_e_v1_1 import context
from app.strategy_e_v1_1.universe import DecisionFrame

ET = ZoneInfo("America/New_York")
D = date(2026, 9, 23)            # a Wednesday session
CAL = MarketCalendar("America/New_York")


def at(hh, mm, day=D):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET)


def frame(n_h5=4, n_other=1, rvols=None, day=D):
    symbols = [f"S{i:02d}" for i in range(n_h5 + n_other)]
    rvols = rvols or [3.0 + i for i in range(n_h5)]
    cols = {n: [] for n in SEALED_FEATURES}
    for i, s in enumerate(symbols):
        h5 = i < n_h5
        vals = {"premarket_gap": 0.03 if h5 else -0.01, "premarket_rvol": rvols[i] if h5 else 1.0,
                "position_in_premarket_range": 0.9, "return_0900_0925": 0.01, "return_last30m": 0.01,
                "relative_strength_vs_spy": 0.02, "premarket_dollar_volume": 1e6,
                "previous_day_dollar_volume": 5e7, "close_price": 20.0, "spy_premarket_return": 0.001, "pm_bars": 50.0}
        for n in SEALED_FEATURES:
            cols[n].append(vals[n])
    return DecisionFrame(day, tuple(symbols), {k: np.array(v, dtype=float) for k, v in cols.items()}, {})


class FakeProvider(MarketDataProvider):
    """Bars become visible only once complete (available_at = start + 1 min <= now)."""

    def __init__(self, bars, clock):
        self.bars, self.clock, self.calls = bars, clock, 0

    def get_daily_bars(self, symbols, start=None, end=None):
        return []

    def get_minute_bars(self, symbols, start=None, end=None, session=None):
        self.calls += 1
        now = self.clock()
        return [b for b in self.bars if b.symbol in symbols and b.available_at <= now
                and (start is None or b.timestamp >= start) and (end is None or b.timestamp <= end)]


def bar(symbol, hh, mm, o, c, day=D, volume=1000):
    ts = at(hh, mm, day)
    return MinuteBar(symbol=symbol, open=o, high=max(o, c), low=min(o, c), close=c, volume=volume,
                     observed_at=ts + timedelta(minutes=1), available_at=ts + timedelta(minutes=1),
                     timestamp=ts, session=MarketSession.REGULAR)


def bars_for(symbols, day=D, missing_open=(), exit_move=0.02):
    out = []
    for i, s in enumerate(symbols):
        if s not in missing_open:
            out.append(bar(s, 9, 30, 20.6, 20.7, day))
        for m in (31, 32, 33):
            out.append(bar(s, 9, m, 20.7, 20.7, day))
        out.append(bar(s, 9, 34, 20.7, 20.6 * (1 + exit_move), day))
        out.append(bar(s, 9, 35, 20.6 * (1 + exit_move), 21.0, day))
    return out


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class CountingSource:
    def __init__(self, fr):
        self.inner = DEC.FrameDecisionSource(lambda s: (fr, "synthetic"), source="SYNTHETIC")
        self.source, self.calls = "SYNTHETIC", 0

    def decide(self, session, decided_at):
        self.calls += 1
        return self.inner.decide(session, decided_at)


def make(tmp_path, fr=None, bars=None, *, enabled=True, source=None, clock=None, equity="10000"):
    clock = clock or Clock(at(9, 0))
    fr = fr or frame()
    provider = FakeProvider(bars if bars is not None else bars_for(fr.symbols), clock)
    cfg = CFG.RuntimeConfig(enabled=enabled, state_dir=tmp_path / "rt", initial_equity=Decimal(equity))
    eng = ENG.Engine(cfg, lambda: provider, source or CountingSource(fr), CAL, ENG.Store(cfg.state_dir, cfg.strategy_id))
    return eng, clock, provider


def run_day(eng, clock, times=((9, 24), (9, 25), (9, 30), (9, 31), (9, 33), (9, 35), (9, 36), (9, 40)), day=D):
    state = None
    for hh, mm in times:
        clock.t = at(hh, mm, day)
        state = eng.tick(clock.t)
    return state


# -- E logic ---------------------------------------------------------------------------------------

def test_full_lifecycle_normal_2x(tmp_path) -> None:
    eng, clock, _ = make(tmp_path)
    state = run_day(eng, clock)
    assert state["phase"] == "COMPLETE"
    d = state["decision"]
    assert d["selected"] == ["S03", "S02", "S01"] and d["final_exposure"] == "2/1" and not d["high_breadth"]
    assert {e["weight"] for e in state["entries"].values()} == {"2/3"}
    assert all(e["status"] == "FILLED" and e["fill_at"].startswith("2026-09-23T09:30") for e in state["entries"].values())
    assert all(x["fill_at"].startswith("2026-09-23T09:35") and x["exit_flag"] == "ON_TIME_0935_OPEN"
               for x in state["exits"].values())
    x = state["exits"]["S01"]
    assert x["development_proxy"]["status"] == "VALID" and x["fixed_bp_views"]["net_10bp"]
    book = json.loads((tmp_path / "rt" / v1.STRATEGY_ID / "book.json").read_text())
    assert D.isoformat() in book["sessions"] and book["live_margin_approved"] is False


def test_high_breadth_3x_and_equal_weight(tmp_path) -> None:
    eng, clock, _ = make(tmp_path, frame(n_h5=4, n_other=96))
    state = run_day(eng, clock)
    assert state["decision"]["high_breadth"] and state["decision"]["final_exposure"] == "3/1"
    assert {e["weight"] for e in state["entries"].values()} == {"1/1"}
    notional = sum(Decimal(e["intent"]["notional"]) for e in state["entries"].values())
    assert Decimal("29000") < notional <= Decimal("30000")          # 3.0x of 10,000, whole shares


def test_h5_r1_max3_and_breadth_identity() -> None:
    d = DEC.decide_from_frame(frame(n_h5=5, rvols=[3.5, 9.0, 4.0, 9.0, 6.0]), source_digest="x", source="T",
                              decided_at=at(9, 25))
    assert d.r1_order == ("S01", "S03", "S04", "S02", "S00") and d.selected == ("S01", "S03", "S04")
    assert d.h5_count == 5 and d.universe_rows == 6 and d.h5_rate is None and d.final_exposure == "2/1"
    DEC.verify(d)
    with pytest.raises(ValueError):
        DEC.verify(DEC.EDecision(**{**d.__dict__, "selected": ("S00",)}))


def test_no_backfill_equal_weight_among_executable(tmp_path) -> None:
    fr = frame()
    eng, clock, _ = make(tmp_path, fr, bars_for(fr.symbols, missing_open={"S03"}))
    state = run_day(eng, clock)
    assert state["entries"]["S03"]["status"] == "ENTRY_INVALID"
    assert "S00" not in state["entries"]                                 # candidate #4 never promoted
    assert {state["entries"][s]["weight"] for s in ("S02", "S01")} == {"1/1"}   # 2.0x over 2 executable


def test_decision_is_immutable_and_not_redecided(tmp_path) -> None:
    src = CountingSource(frame())
    eng, clock, _ = make(tmp_path, source=src)
    clock.t = at(9, 25)
    first = eng.tick(clock.t)["decision"]
    clock.t = at(9, 26)
    assert eng.tick(clock.t)["decision"] == first and src.calls == 1


def test_late_start_does_not_decide(tmp_path) -> None:
    src = CountingSource(frame())
    eng, clock, _ = make(tmp_path, source=src)
    clock.t = at(9, 31)
    state = eng.tick(clock.t)
    assert state["phase"] == "NO_DECISION" and src.calls == 0 and not state["entries"]


# -- realtime source gate ---------------------------------------------------------------------------

def test_kiwoom_capacity_blocks_and_fails_closed(tmp_path) -> None:
    cap = DEC.SourceCapacity("KIWOOM_USA06011_REST", 3.0, 1, 2550)
    assert cap.seconds_needed == 850 and len(cap.blockers()) == 2
    gate = DEC.RealtimeSourceGate(lambda s: cap, source="KIWOOM")
    eng, clock, _ = make(tmp_path, source=gate)
    clock.t = at(9, 25)
    state = eng.tick(clock.t)
    assert state["phase"] == "NO_DECISION" and state["no_decision"]["status"] == "FEATURE_CONTEXT_INCOMPLETE"
    assert not state["entries"]
    ok = DEC.SourceCapacity("FAST", 100.0, 1, 2550, rvol_history_same_source=True)
    assert ok.blockers() == []


def test_runtime_default_source_is_the_gate() -> None:
    eng = RT.build_engine(lambda: None, config=CFG.RuntimeConfig(True, Path("/tmp/x"), Decimal(1)))
    assert isinstance(eng.decision_source, DEC.RealtimeSourceGate)
    assert RT.kiwoom_capacity(D).blockers()


# -- runtime ------------------------------------------------------------------------------------

def test_restart_before_entry_keeps_selection(tmp_path) -> None:
    fr = frame()
    eng, clock, provider = make(tmp_path, fr)
    clock.t = at(9, 25)
    decided = eng.tick(clock.t)["decision"]
    src2 = CountingSource(frame(rvols=[9, 8, 7, 6]))                     # a different answer if asked again
    eng2 = ENG.Engine(eng.config, lambda: provider, src2, CAL, ENG.Store(eng.config.state_dir, eng.config.strategy_id))
    state = run_day(eng2, clock, times=((9, 29), (9, 31), (9, 36)))
    assert state["decision"] == decided and src2.calls == 0 and state["phase"] == "COMPLETE"


def test_restart_with_open_position_and_duplicate_ticks(tmp_path) -> None:
    eng, clock, provider = make(tmp_path)
    state = run_day(eng, clock, times=((9, 25), (9, 31), (9, 31), (9, 32)))
    assert state["phase"] == "POSITION_OPEN" and len(state["keys"]) == 3        # no double entry
    eng2 = ENG.Engine(eng.config, lambda: provider, CountingSource(frame()), CAL,
                      ENG.Store(eng.config.state_dir, eng.config.strategy_id))
    state = run_day(eng2, clock, times=((9, 36), (9, 36), (9, 37)))
    assert state["phase"] == "COMPLETE" and len(state["keys"]) == 6              # 3 entries + 3 exits, once
    book = json.loads((tmp_path / "rt" / v1.STRATEGY_ID / "book.json").read_text())
    assert list(book["sessions"]) == [D.isoformat()]


def test_session_rollover_carries_equity(tmp_path) -> None:
    fr1, fr2 = frame(), frame(day=D + timedelta(days=1))
    bars = bars_for(fr1.symbols) + bars_for(fr2.symbols, day=D + timedelta(days=1))
    clock = Clock(at(9, 0))
    provider = FakeProvider(bars, clock)
    cfg = CFG.RuntimeConfig(True, tmp_path / "rt", Decimal("10000"))
    frames = {D: fr1, D + timedelta(days=1): fr2}
    src = DEC.FrameDecisionSource(lambda s: (frames[s], "syn"), source="SYN")
    eng = ENG.Engine(cfg, lambda: provider, src, CAL, ENG.Store(cfg.state_dir, cfg.strategy_id))
    s1 = run_day(eng, clock)
    s2 = run_day(eng, clock, day=D + timedelta(days=1))
    assert s2["equity_at_open"] == s1["summary"]["equity_after"] and s2["phase"] == "COMPLETE"


def test_enable_disable_and_idle(tmp_path) -> None:
    eng, clock, _ = make(tmp_path, enabled=False)
    assert eng.tick(at(9, 25))["phase"] == "DISABLED"
    eng2, _, _ = make(tmp_path / "b")
    assert eng2.tick(at(9, 25, date(2026, 9, 26)))["phase"] == "IDLE"         # Saturday
    assert CFG.from_env({}).enabled is False and CFG.from_env({"STRATEGY_E_MAX_ENABLED": "true"}).enabled


def test_simulation_only_protection(tmp_path) -> None:
    assert CFG.LIVE_MARGIN_APPROVED is False and CFG.SimRiskProfile().live_margin_approved is False
    eng, clock, _ = make(tmp_path)
    state = run_day(eng, clock)
    assert state["mode"] == "SIMULATION_VIRTUAL_ONLY" and isinstance(eng._broker(state), SimBroker)
    import inspect
    src = inspect.getsource(ENG) + inspect.getsource(RT)
    assert "kiwoom_mode" not in src and "place_order" not in src and "KiwoomOrder" not in src


# -- A + E isolation -------------------------------------------------------------------------------

def _a_buy(broker, symbol, when):
    intent = OrderIntent(symbol=symbol, side=OrderSide.BUY, intent_type=IntentType.BASE_ENTRY, quantity=Decimal(10),
                         reference_price=Decimal("20.6"), notional=Decimal(206), account_notional=Decimal(7428.92),
                         account_currency="USD", instrument_currency="USD", strategy_version="STRATEGY_V0",
                         risk_amount=Decimal(5), initial_stop=Decimal("19"), market_as_of=when - timedelta(minutes=1),
                         created_at=when, reason="A")
    return broker.submit_order(intent, [bar(symbol, when.hour, when.minute, 20.6, 20.7)])


def test_a_and_e_trade_the_same_symbol_with_separate_books(tmp_path) -> None:
    a_broker = SimBroker(Decimal("7428.92"))
    a_order = _a_buy(a_broker, "S03", at(9, 30))
    a_before = (a_broker.cash, [(p.symbol, p.quantity) for p in a_broker.get_positions()])
    eng, clock, _ = make(tmp_path)
    state = run_day(eng, clock)
    assert a_order.status.value == "FILLED"
    assert (a_broker.cash, [(p.symbol, p.quantity) for p in a_broker.get_positions()]) == a_before
    assert state["entries"]["S03"]["status"] == "FILLED" and state["strategy_id"] == "STRATEGY_E_MAX_V1"
    assert all(k.startswith("STRATEGY_E_MAX_V1|") for k in state["keys"])
    assert not list(tmp_path.glob("**/*.sqlite3"))                       # E writes no A database


def test_e_failure_does_not_stop_a_and_a_failure_does_not_corrupt_e(tmp_path) -> None:
    class Boom:
        source = "BOOM"

        def decide(self, s, t):
            raise RuntimeError("decision crash")
    eng, clock, _ = make(tmp_path, source=Boom())
    clock.t = at(9, 25)
    assert eng.tick(clock.t)["phase"] == "ERROR"
    a_broker = SimBroker(Decimal("1000"))
    assert _a_buy(a_broker, "S01", at(9, 30)).status.value == "FILLED"   # A path unaffected
    good, clock2, _ = make(tmp_path / "g")
    clock2.t = at(9, 25)
    good.tick(clock2.t)
    with pytest.raises(Exception):
        _a_buy(SimBroker(Decimal("1000")), "S01", datetime(2026, 9, 23, 9, 30))    # naive time: A-side error
    assert good.store.load_session(D)["phase"] == "DECIDED"


def test_async_runtime_isolation_and_status(tmp_path) -> None:
    class Flaky:
        enabled = True

    async def scenario():
        eng, clock, _ = make(tmp_path)
        calls = {"n": 0}
        original = eng.tick

        def tick(now):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("one bad tick")
            return original(now)
        eng.tick = tick
        clock.t = at(9, 25)
        assert RT.start(eng, clock=clock, interval=0.01)
        await asyncio.sleep(0.1)
        status = RT.status()
        await RT.stop()
        return calls["n"], status
    n, status = asyncio.run(scenario())
    assert n >= 2 and status["phase"] == "DECIDED" and status["task_running"] and status["mode"] == "SIMULATION_VIRTUAL_ONLY"
    assert RT.status()["task_running"] is False


def test_start_from_env_disabled_is_a_noop(monkeypatch) -> None:
    monkeypatch.delenv("STRATEGY_E_MAX_ENABLED", raising=False)
    assert RT.start_from_env(lambda: None) is False and RT.status()["task_running"] is False


def test_main_lifespan_unchanged_for_a_when_e_disabled(monkeypatch) -> None:
    import app.main as main
    monkeypatch.delenv("STRATEGY_E_MAX_ENABLED", raising=False)
    started = []
    monkeypatch.setattr(main.strategy_e_max_runtime, "start", lambda *a, **k: started.append(1))
    from fastapi.testclient import TestClient
    with TestClient(main.create_app()) as client:
        assert client.get("/health").status_code == 200
    assert started == []


# -- frozen artifacts ------------------------------------------------------------------------------

def test_frozen_identity_unchanged() -> None:
    v1.load_rules()
    closure = FR.provenance_closure(FR.load_rules())
    assert closure["checks"]["pass"]
    assert not any((Path(v1.__file__).parent / n).exists() for n in ("rt.py", "runtime.py", "engine.py"))
