"""Manual Context & Exit R0.1: the reproducibility checks of contract section 10."""
from __future__ import annotations

import json

import numpy as np
import pytest

from app.crypto.research.manual_context_exit_r0 import bars as B
from app.crypto.research.manual_context_exit_r0 import stats as ST
from app.crypto.research.manual_context_exit_r0 import structure as S
from app.crypto.research.manual_context_exit_r0 import study as Y
from app.crypto.research.manual_context_exit_r0 import walls as W

HAVE_K1 = B.K1_PATH.exists()


def _series(close, high=None, low=None, step=60_000, t0=1_700_000_000_000 // 3_600_000 * 3_600_000,
            tf="1m") -> B.Series:
    close = np.asarray(close, float)
    high = close + 0.5 if high is None else np.asarray(high, float)
    low = close - 0.5 if low is None else np.asarray(low, float)
    n = len(close)
    return B.Series(tf, step, t0 + np.arange(n, dtype=np.int64) * step, close.copy(), high, low,
                    close, np.ones(n), close.copy())


def _run(close, high, low, res, sup, atr=1.0) -> S.StructureRun:
    n = len(close)
    ts = 1_700_000_000_000 + np.arange(n, dtype=np.int64) * 60_000
    f = lambda x: np.asarray(x, float)
    return S.StructureRun("1m", ts, ts + 60_000, f(close), f(high), f(low), np.full(n, atr), f(res), f(sup),
                          np.where(np.isfinite(f(res)), 7, -1), np.where(np.isfinite(f(sup)), 9, -1),
                          np.ones(n, bool), [()] * n, 0, 0)


# --------------------------------------------------------------------------- aggregation

def test_aggregate_equals_direct_recomputation_and_drops_partial_groups():
    rng = np.random.default_rng(3)
    n = 10 * 37 + 4
    c = 100 + np.cumsum(rng.normal(0, 1, n))
    s = _series(c, c + rng.random(n), c - rng.random(n))
    s = B.Series("1m", s.step_ms, s.ts + 3 * 60_000, s.open, s.high, s.low, s.close, s.volume, s.turnover)
    a = B.aggregate(s, "10m")
    first = int(np.argmax(s.ts % 600_000 == 0))
    assert first == 7 and len(a) == (n - first) // 10
    for k in range(len(a)):
        sl = slice(first + 10 * k, first + 10 * k + 10)
        assert a.ts[k] == s.ts[sl][0] and a.open[k] == s.open[sl][0] and a.close[k] == s.close[sl][-1]
        assert a.high[k] == s.high[sl].max() and a.low[k] == s.low[sl].min()
        assert a.volume[k] == pytest.approx(s.volume[sl].sum())


def test_15s_bars_from_trades_and_flat_empty_buckets():
    t0 = 1_788_912_000_000
    t = np.array([t0 + 1, t0 + 5_000, t0 + 14_999, t0 + 15_000, t0 + 44_000])
    p = np.array([10.0, 12.0, 11.0, 13.0, 9.0])
    q = np.array([1.0, 2.0, 1.0, 1.0, 3.0])
    s = B.bars_from_trades(t, p, q, t0, t0 + 60_000, 15_000)
    assert list(s.open) == [10, 13, 9, 9] and list(s.close) == [11, 13, 9, 9]
    assert list(s.high) == [12, 13, 9, 9] and list(s.low) == [10, 13, 9, 9]
    assert list(s.volume) == [4, 1, 3, 0]          # bucket 3 had no trade: flat at 9, volume 0


# --------------------------------------------------------------------------- structure

@pytest.mark.skipif(not HAVE_K1, reason="D2 history not present")
def test_structure_parity_with_market_context_r0_rows():
    """Nearest R/S recorded here == R1 via MC R0's ContextEngine (an independent caller)."""
    from app.crypto.research.market_context_r0.structure import ContextEngine
    from app.crypto.research.price_structure_r1.engine import EngineConfig
    from app.crypto.market_context_v1 import contract as MC
    k1 = B.load_k1()
    start = int(np.searchsorted(k1.ts, B.K1_END_MS - 6 * B.DAY_MS))
    s = k1.slice(start, start + 4 * 1440)
    run = S.run(s)
    bars = S.engine_bars(s)
    eng = ContextEngine(bars, EngineConfig(MC.THETA_1H, MC.THETA_5M,
                                           int(bars["1m"].ts[1440]) + 60_000, int(bars["1m"].ts[-1]) + 60_000))
    eng.run()
    res = np.array([r["res_price"] if r["res_price"] is not None else np.nan for r in eng.rows])
    sup = np.array([r["sup_price"] if r["sup_price"] is not None else np.nan for r in eng.rows])
    mine = np.flatnonzero(run.published)
    assert len(mine) == len(eng.rows)
    np.testing.assert_array_equal(run.res[mine], res)
    np.testing.assert_array_equal(run.sup[mine], sup)
    assert run.violations == 0


@pytest.mark.skipif(not HAVE_K1, reason="D2 history not present")
@pytest.mark.parametrize("tf", ["1h", "1m"])
def test_prefix_invariance_of_structure_and_episodes(tf):
    k1 = B.load_k1()
    s = B.series(tf, k1)
    s = s.slice(0, min(len(s), 12_000))
    full = S.run(s)
    full_eps = Y.episodes(full)
    for cut in np.linspace(3_000, len(s) - 1, 6).astype(int):
        part = S.run(s.slice(0, cut))
        np.testing.assert_array_equal(part.res, full.res[:cut])
        np.testing.assert_array_equal(part.sup, full.sup[:cut])
        np.testing.assert_array_equal(part.published, full.published[:cut])
        a = [(e.side, e.kind, e.bar, e.level) for e in Y.episodes(part)]
        b = [(e.side, e.kind, e.bar, e.level) for e in full_eps if e.bar < cut]
        assert a == b


@pytest.mark.skipif(not HAVE_K1, reason="D2 history not present")
def test_no_future_leak_into_levels_entries_or_entry_features():
    k1 = B.load_k1()
    der = B.load_derivatives()
    s = B.series("1h", k1).slice(0, 8_000)
    j = 6_000
    bumped = B.Series(s.tf, s.step_ms, s.ts, s.open.copy(), s.high.copy(), s.low.copy(),
                      s.close.copy(), s.volume, s.turnover)
    for arr in (bumped.open, bumped.high, bumped.low, bumped.close):
        arr[j:] *= 1.07
    a, b = S.run(s), S.run(bumped)
    np.testing.assert_array_equal(a.res[:j], b.res[:j])
    np.testing.assert_array_equal(a.sup[:j], b.sup[:j])
    ta = [t for t in Y.cohort_entries(a, Y.episodes(a)) if t.entry < j]
    tb = [t for t in Y.cohort_entries(b, Y.episodes(b)) if t.entry < j]
    assert [(t.cohort, t.side, t.entry, t.price, t.target, t.inval) for t in ta] == \
        [(t.cohort, t.side, t.entry, t.price, t.target, t.inval) for t in tb]
    assert [Y.entry_features(a, t, der) for t in ta] == [Y.entry_features(b, t, der) for t in tb]


def test_time_scaled_bars_use_the_synthetic_clock():
    s = _series(np.linspace(100, 110, 200), step=600_000, tf="10m")
    bars = S.engine_bars(s)
    assert bars["1m"].ts[0] == S.SYN_T0_MS and np.all(np.diff(bars["1m"].ts) == 60_000)
    assert bars["5m"].step_ms == 300_000 and bars["1h"].step_ms == 3_600_000


# --------------------------------------------------------------------------- touches

def test_touch_episode_needs_prev_close_below_and_rearms_after_one_atr():
    close = [98, 99.5, 98.7, 97.5, 99.0, 99.5, 98.0]
    high = [98.5, 100.2, 99.0, 98.0, 100.1, 100.5, 98.4]
    low = [97.5, 99.0, 98.0, 97.0, 98.5, 99.0, 97.5]
    run = _run(close, high, low, [100.0] * 7, [np.nan] * 7, atr=1.0)
    eps = [e for e in Y.episodes(run) if e.kind == "REAL"]
    # bar 1 touches; bar 3 closes 97.5 < 100 - 1 ATR -> re-armed; bar 4 touches again; bar 5 does
    # not count (disarmed, no re-arm close in between).
    assert [e.bar for e in eps] == [1, 4]


def test_classify_hold_break_latency_and_retest():
    close = [99, 99.5, 100.5, 101, 99.8, 99]
    high = [99.5, 100.2, 100.8, 101.5, 100.5, 99.5]
    low = [98.5, 99.0, 99.8, 100.2, 99.5, 98.5]
    run = _run(close, high, low, [100.0] * 6, [np.nan] * 6)
    ep = Y.Episode("RES", "REAL", 1, 100.0, 7, 1.0)
    c = Y.classify(run, ep, 4)
    assert c["break"] and c["latency"] == 1 and c["retest"] is True
    assert c["mfe_bp"] == pytest.approx((99.5 - 98.5) / 99.5 * 1e4)
    assert Y.classify(run, ep, 5) is None


# --------------------------------------------------------------------------- exits and costs

def _flat_trade_run(n=70, base=100.0):
    close = np.full(n, base)
    return close, close + 0.1, close - 0.1


def test_cost_accounting_target_exit_with_funding_crossing():
    close, high, low = _flat_trade_run()
    high[3] = 102.5                       # target 102 touched at bar 3
    run = _run(close, high, low, np.full(70, 102.0), np.full(70, 98.0))
    t = Y.Trade("FADE", "LONG", 0, 100.0, 102.0, 98.0, 7, 1.0, "SUP")
    der = B.Derivatives(np.array([int(run.known_ms[1]) + 1]), np.array([0.0001]),
                        np.array([0]), np.array([100.0]), np.array([0]), np.array([1.0]))
    x = Y.simulate(run, t, der)
    assert x["A"]["why"] == "TARGET" and x["A"]["exit_bar"] == 3
    assert x["A"]["gross_bp"] == pytest.approx(200.0)
    assert x["A"]["funding_bp"] == pytest.approx(1.0)          # long pays positive funding
    assert x["A"]["net_bp"] == pytest.approx(200.0 - 11.25 - 1.0)
    t2 = Y.Trade("FADE", "SHORT", 0, 100.0, 98.0, 102.0, 9, 1.0, "RES")
    assert Y.simulate(run, t2, der)["H60"]["funding_bp"] == pytest.approx(-1.0)


def test_same_bar_target_and_invalidation_resolves_to_invalidation():
    close, high, low = _flat_trade_run()
    high[2], close[2] = 102.5, 97.5       # touches the target and closes below the invalidation
    run = _run(close, high, low, np.full(70, 102.0), np.full(70, 98.0))
    t = Y.Trade("FADE", "LONG", 0, 100.0, 102.0, 98.0, 7, 1.0, "SUP")
    x = Y.simulate(run, t, None)
    assert x["S3"]["why"] == "INVALID" and x["S3"]["exit_bar"] == 2
    assert x["A"]["why"] == "TARGET"


def test_breakeven_moves_stop_from_the_next_bar_and_fixed_percent_is_loss_first():
    close, high, low = _flat_trade_run()
    high[2] = 101.2                       # midpoint 101 reached
    close[4], low[4] = 99.5, 99.4          # closes below entry -> breakeven exit
    run = _run(close, high, low, np.full(70, 102.0), np.full(70, 98.0))
    t = Y.Trade("FADE", "LONG", 0, 100.0, 102.0, 98.0, 7, 1.0, "SUP")
    x = Y.simulate(run, t, None)
    assert x["F"]["why"] == "STOP_MOVED" and x["F"]["exit_bar"] == 4
    assert x["S3"]["why"] == "TIMEOUT"
    close2, high2, low2 = _flat_trade_run()
    high2[1], low2[1] = 101.5, 98.5       # both 1 ATR sides in one bar
    run2 = _run(close2, high2, low2, np.full(70, 102.0), np.full(70, 98.0))
    assert Y.simulate(run2, t, None)["I"]["why"] == "SL"


def test_one_position_per_cohort_side_and_hmax():
    n = 300
    close = np.full(n, 99.0)
    high = close + 0.2
    high[[10, 40, 100]] = 100.1           # touches at 10, 40 (inside 10's hold), 100
    close[[39, 99]] = 97.0                # re-arm closes
    low = close - 0.2
    run = _run(close, high, low, np.full(n, 100.0), np.full(n, 95.0))
    fades = [t for t in Y.cohort_entries(run, Y.episodes(run)) if t.cohort == "FADE"]
    assert [t.entry for t in fades] == [10, 100]


# --------------------------------------------------------------------------- walls

def _ctx(n=200, mid=100.0):
    t = 1_791_636_444_098 + np.arange(n, dtype=np.int64) * 1_000
    return W.Context(t, np.full(n, mid), np.ones(n, bool), np.ones(n, np.int64), np.zeros(n, np.int64),
                     np.full(n, 0.6), [])


def test_end_states_are_the_collectors_own_and_unmatched_closes_are_not_published():
    ctx = _ctx()
    ctx.vanished = [
        {"side": "ASK", "bin_low": "100.5", "state": "CONSUMED_CANDIDATE", "observed_at_ms": int(ctx.t[50])},
        {"side": "BID", "bin_low": "99", "state": "UNKNOWN", "reason": "RESNAPSHOT_GENERATION_CHANGED",
         "observed_at_ms": int(ctx.t[60])},
    ]
    walls = [W.Wall("ASK", 100.5, 101.0, 100.6, int(ctx.t[10]), int(ctx.t[50]), 1e6, 9e5, 1e6, "NOT_SELECTED"),
             W.Wall("BID", 99.0, 99.5, 99.2, int(ctx.t[10]), int(ctx.t[60]), 1e6, 1e6, 1e6, "NOT_SELECTED"),
             W.Wall("ASK", 102.0, 102.5, 102.1, int(ctx.t[10]), int(ctx.t[70]), 1e6, 1e6, 1e6, "NOT_SELECTED"),
             W.Wall("ASK", 103.0, 103.5, 103.1, int(ctx.t[10]), None, 1e6, 1e6, 1e6, None)]
    W.attach_end_states(walls, ctx)
    assert [w.end_state for w in walls] == ["CONSUMED_CANDIDATE", "UNKNOWN", "NOT_PUBLISHED", "OPEN_AT_END"]


def test_touch_approach_and_interaction_classes():
    ctx = _ctx(400)
    ctx.mid[100:110] = 100.5              # enters the ASK bin [100.5, 101]
    w = W.Wall("ASK", 100.5, 101.0, 100.6, int(ctx.t[0]), int(ctx.t[300]), 1e6, 1e6, 1e6, "NOT_SELECTED",
               end_state="CANCEL_LIKE")
    W.attach_touches([w], ctx)
    assert w.first_touch_ms == int(ctx.t[100]) and w.approaches == 1
    out = W.interaction_classes([w], ctx)
    assert out["I-A|ASK"]["events"] == 1   # buy flow (0.6) into it, persisted >= 60 s, never beyond
    assert out["I-C|ASK"]["events"] == 0   # touched walls are never I-C
    assert out["I-A|ASK"]["gate"] == "INSUFFICIENT"


def test_flow_into_wall_is_none_without_a_side():
    ctx = _ctx()
    ctx.buy60[:] = 0.5
    w = W.Wall("BID", 99, 99.5, 99.2, int(ctx.t[0]), None, 1, 1, 1, None)
    assert W.flow_into_wall(ctx, w, int(ctx.t[5])) is None
    ctx.buy60[:] = 0.3
    assert W.flow_into_wall(ctx, w, int(ctx.t[5])) is True


# --------------------------------------------------------------------------- stats, alignment

def test_gates_and_holm():
    assert [ST.gate(n) for n in (0, 19, 20, 49, 50, 99, 100)] == \
        ["INSUFFICIENT", "INSUFFICIENT", "EARLY", "EARLY", "REVIEW", "REVIEW", "STRONGER"]
    assert ST.holm([0.01, 0.04, None, 0.03]) == [0.03, 0.06, None, 0.06]


def test_bootstrap_is_deterministic_per_label():
    v = np.arange(100, dtype=float)
    blocks = np.arange(100) // 7
    assert ST.mean_ci(v, blocks, "x") == ST.mean_ci(v, blocks, "x")
    assert ST.mean_ci(v, blocks, "x") != ST.mean_ci(v, blocks, "y")


def test_manual_trade_alignment_labels(tmp_path, monkeypatch):
    from app.crypto.research.manual_context_exit_r0 import run as R
    ledger = tmp_path / "l.jsonl"
    ledger.write_text("\n".join(json.dumps({"event_type": "POSITION_OPEN", "ts_ms": ms}) for ms in (
        B.K1_END_MS - 1, B.K1_END_MS, 1_791_700_000_000)) + "\n")
    fills = tmp_path / "f.json"
    fills.write_text(json.dumps({"fills": []}))
    monkeypatch.setattr(R, "LEDGER_COPY", ledger)
    monkeypatch.setattr(R, "LIVE_FILLS_COPY", fills)
    monkeypatch.setattr(R, "C1J_ROOT", tmp_path / "absent")
    out = R.backfill()
    assert [r["context"] for r in out["rows"]] == ["PARTIAL", "NOT_AVAILABLE", "NOT_AVAILABLE"]


@pytest.mark.skipif(not HAVE_K1, reason="D2 history not present")
def test_deterministic_replay_of_a_timeframe_study():
    from app.crypto.research.manual_context_exit_r0 import run as R
    k1 = B.load_k1()
    der = B.load_derivatives()
    a = json.dumps(R.tf_study("1d", k1, der), sort_keys=True, default=R._json)
    b = json.dumps(R.tf_study("1d", k1, der), sort_keys=True, default=R._json)
    assert a == b
