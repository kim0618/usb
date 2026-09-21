"""E-MAX-M1 replay mechanics on synthetic sessions (no historical tape)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import numpy as np

from app.backtest.strategy_e0_overnight.minute import SymbolTape, ordinal
from app.backtest.strategy_e1_premarket import premarket as P
from app.backtest.strategy_e_max import m1_replay as MR
from app.backtest.strategy_e_r3 import build as B
from app.market.calendar import MarketCalendar
from app.strategy_e_v1_1 import decision as D

CAL = MarketCalendar("America/New_York")
SESSION = date(2026, 9, 15)


def _tape(symbol, *, volume_d=5_000.0, d_open=True, slope=0.01, open_price=10.0):
    days, day = [], SESSION
    for _ in range(8):
        day = CAL.previous_trading_day(day)
        days.append(day)
    rows = ([], [], [], [])
    for d, volume in [(d, 1_000.0) for d in sorted(days)] + [(SESSION, volume_d)]:
        pre = [4 * 60 + 5, 7 * 60, 8 * 60 + 30] + list(range(9 * 60, 9 * 60 + 25, 4))
        regular = [] if (d == SESSION and not d_open) else list(range(P.OPEN_MIN, P.OPEN_MIN + 5))
        for k, minute in enumerate(pre + regular):
            price = 10.2 + slope * k if minute < P.OPEN_MIN else open_price * (1 + 0.002 * (minute - P.OPEN_MIN))
            for bucket, value in zip(rows, (ordinal(d), minute, price, volume)):
                bucket.append(value)
    p = np.array(rows[2])
    return SymbolTape(symbol=symbol, et_day=np.array(rows[0], dtype=np.int64),
                      minute=np.array(rows[1], dtype=np.int64), open=p, high=p * 1.0001,
                      low=p * 0.9999, close=p, volume=np.array(rows[3]), vwap=p, sources={},
                      overlap_sessions=0)


def _prepared(tapes):
    rows = {s: B.symbol_rows(t, {SESSION}) for s, t in tapes.items()}
    columns = {s: j for j, s in enumerate(sorted(tapes))}
    n = len(columns)
    daily = B.DailySession(np.ones(n, bool), np.ones(n, bool), np.full(n, 10.0), np.full(n, 5e7))
    frame = B.frame_for(SESSION, rows, daily, columns, spy_close_previous=499.0)
    origins = {SESSION: {s: B.origin(True, rows[s][SESSION].has_0930_open) for s in frame.symbols}}
    seals = {SESSION: D.seal(frame, source_digest="s")}
    return SimpleNamespace(frames={SESSION: frame}, rows=rows, origins=origins, seals=seals,
                           calendar=CAL, timeline=[SESSION])


TAPES = {"AAA": _tape("AAA"), "BBB": _tape("BBB", d_open=False), "CCC": _tape("CCC"),
         "DDD": _tape("DDD", volume_d=40_000.0, slope=0.02), "EEE": _tape("EEE", volume_d=20_000.0)}


def test_subset_method_with_r0_reproduces_the_e_r3_path() -> None:
    p = _prepared(TAPES)
    assert MR.equivalent(MR.replay_base(p), MR.replay_variant("R0", p))


def test_r1_changes_selection_and_keeps_every_candidate_row() -> None:
    p = _prepared(TAPES)
    base, r1 = MR.replay_base(p), MR.replay_variant("R1", p)
    assert base[0]["selected_order"] == ["AAA", "BBB", "CCC"]
    assert r1[0]["selected_order"][:2] == ["DDD", "EEE"]
    assert {r["symbol"] for r in r1[0]["records"]} == set(TAPES)
    delta = MR.selection_delta(base, r1)
    assert delta["selected_set_changed"] == 1 and delta["eligible_to_change"] == 1
    inv = MR.invariants(p, {"R0": base, "R1": r1})
    assert inv["pass"]


def test_not_selected_rows_carry_zero_weight_and_no_return() -> None:
    p = _prepared(TAPES)
    r1 = MR.replay_variant("R1", p)
    skipped = [r for r in r1[0]["records"] if not r["selected"]]
    assert skipped and all(r["weight"] == "0/1" and "COST_10BP" not in r for r in skipped)
    assert sorted(r["selection_rank"] for r in r1[0]["records"]) == [1, 2, 3, 4, 5]
