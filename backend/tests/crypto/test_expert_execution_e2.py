"""E2 position management: order classification, inverse accounting, path metrics, counterfactuals,
equity alignment and no-lookahead. Synthetic data only."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.crypto.research.expert_execution import e2 as E
from app.crypto.research.expert_execution import grid as G

MIN = E.MIN_NS
T0 = int(pd.Timestamp("2021-01-01", tz="UTC").value)


def _grid(prices, hi=None, lo=None, start=T0 - 120 * MIN):
    n = len(prices)
    return E.Grid(pd.DataFrame({"b": start + (np.arange(n) + 1) * MIN, "mid_close": prices,
                                "mid_hi": hi if hi is not None else prices, "mid_lo": lo if lo is not None else prices,
                                "n_trades": np.ones(n, np.int64)}))


def _stream(rows):
    """rows: (minutes from T0, orderid, q, px, kind[, is_maker, funding])"""
    out = []
    for i, r in enumerate(rows):
        mins, oid, q, px, kind = r[:5]
        mk = r[5] if len(r) > 5 else True
        fund = r[6] if len(r) > 6 else 0.0
        ec = E.exec_cost(q, px) if kind != "FUND" else 0.0
        fee = abs(ec) * (-0.00025 if mk else 0.00075) if kind != "FUND" else 0.0
        out.append((T0 + int(mins * MIN), i, oid, float(q), ec, fee, float(px), kind, mk, float(fund)))
    return pd.DataFrame(out, columns=["t", "seq", "orderid", "q", "ec", "fee", "px", "kind", "is_maker", "funding"])


LONG = _stream([
    (0, "e", 1000, 10000, "INC"), (0.5, "e", 1000, 10000, "INC"),      # entry order, 2 fills
    (10, "a1", 2000, 9500, "INC"),                                    # ADD at a lower price
    (20, "r1", -1500, 10200, "DEC"),                                  # partial close
    (25, "", 0, 10100, "FUND", None, 30.0),                           # funding paid while long 2500
    (40, "c", -2500, 10500, "DEC", False),                            # full close
])


def test_order_classification_entry_add_reduce_close():
    o = E.classify_orders(LONG)
    assert o.set_index("orderid").type.to_dict() == {"e": "ENTRY", "a1": "ADD", "r1": "REDUCE", "c": "FULL_CLOSE"}
    assert o.set_index("orderid").qty["e"] == 2000
    assert o.set_index("orderid").vwap["a1"] == pytest.approx(9500)


def test_actual_replay_average_entry_unrealized_and_attribution():
    b = E.replay_actual(LONG)
    assert b.pos == 0 and b.cost == 0
    avg_after_add = 4000 / (2000 / 10000 + 2000 / 9500)            # inverse: harmonic average
    snap = E.slice_book(b, T0 + 15 * MIN)
    assert snap.pos == 4000
    assert snap.pos * E.MULT / snap.cost == pytest.approx(avg_after_add)
    assert snap.unreal(10000) == pytest.approx(E.exec_cost(4000, 10000) - snap.cost)
    assert snap.unreal(10000) > 0                                    # long with avg below 10000
    gross = (E.exec_cost(1500, 10200) - E.exec_cost(1500, avg_after_add)) + (E.exec_cost(2500, 10500) - E.exec_cost(2500, avg_after_add))
    assert b.realized == pytest.approx(gross, rel=1e-9) and gross > 0
    assert b.funding == 30.0 and b.fees == pytest.approx(LONG.fee.sum())
    assert b.net == pytest.approx(b.realized - b.fees - 30.0)


def test_no_add_counterfactual_keeps_exit_timing_and_scales_reduces():
    o = E.classify_orders(LONG)
    b = E.cf_book(LONG, o, "NO_ADD")
    # initial 2000; actual reduce took 1500 of 4000 (37.5%) -> CF reduces 750; close 1250 at the close VWAP
    avg = 10000.0
    exp = (E.exec_cost(750, 10200) - E.exec_cost(750, avg)) + (E.exec_cost(1250, 10500) - E.exec_cost(1250, avg))
    assert b.realized == pytest.approx(exp, rel=1e-9)
    assert b.pos == 0
    assert b.funding == pytest.approx(30.0 * 1250 / 2500)            # funding scaled by CF/actual position
    assert b.ev_t[-1] == T0 + 40 * MIN                                # same final exit time


def test_no_partial_counterfactual_holds_everything_to_the_close():
    o = E.classify_orders(LONG)
    b = E.cf_book(LONG, o, "NO_PARTIAL")
    avg = 4000 / (2000 / 10000 + 2000 / 9500)
    assert b.realized == pytest.approx(E.exec_cost(4000, 10500) - E.exec_cost(4000, avg), rel=1e-9)
    assert b.funding == pytest.approx(30.0 * 4000 / 2500)


def test_fixed_add_rule_triggers_on_adverse_move_and_caps():
    prices = np.full(400, 10000.0)
    lo = prices.copy()
    lo[125:] = 9940.0                                                # -0.6% from entry from minute 5 on
    g = _grid(prices, hi=prices, lo=lo)
    s = _stream([(0, "e", 1000, 10000, "INC"), (200, "c", -1000, 10000, "DEC")])
    o = E.classify_orders(s)
    b = E.cf_book(s, o, "RULE", g, 0.005, lambda t: -0.00025)
    adds = [p for p in b.ev_px if abs(p - 9950.0) < 1e-6]
    assert len(adds) == 1                                            # next trigger 9900.25 never reached
    b2 = E.cf_book(s, o, "RULE", g, 0.0001, lambda t: -0.00025)
    assert sum(1 for q in np.diff([0] + b2.ev_pos) if q > 0) - 1 == E.RULE_MAX_ADDS


def test_path_mae_mfe_underwater_and_leverage():
    prices = np.full(300, 10000.0)
    prices[125:130] = 9000.0                                         # dip while long, then recover
    prices[140:150] = 11000.0
    g = _grid(prices)
    s = _stream([(0, "e", 10000, 10000, "INC"), (60, "c", -10000, 10000, "DEC")])
    b = E.replay_actual(s)
    pm = E.path_metrics(b, int(s.t.iloc[0]), int(s.t.iloc[-1]), g, equity0=2.0)
    fee0 = abs(E.exec_cost(10000, 10000)) * -0.00025
    exp_mae = (E.exec_cost(10000, 9000) - E.exec_cost(10000, 10000)) - fee0
    assert pm["mae_xbt"] == pytest.approx(exp_mae / 1e8)
    assert pm["mae_roe"] == pytest.approx(exp_mae / 1e8 / 2.0)
    assert pm["mfe_xbt"] > 0 and pm["t_mfe_h"] > pm["t_mae_h"]
    assert pm["underwater_minutes"] == 5 and pm["valid_minutes"] == 60
    assert pm["recovery_h"] == pytest.approx(5 / 60, abs=1e-9)   # MAE at the dip's first minute, back at >= 0 five minutes later
    assert pm["peak_leverage"] == pytest.approx((10000 / 9000) / (2.0 + exp_mae / 1e8), rel=1e-6)


def test_stop_counterfactual_exits_at_threshold_minute_close():
    prices = np.full(300, 10000.0)
    prices[130:] = 9700.0                                            # -3% vs long entry
    g = _grid(prices)
    s = _stream([(0, "e", 10000, 10000, "INC"), (100, "c", -10000, 10500, "DEC")])
    b = E.replay_actual(s)
    eq0 = 1.0                                                        # 1 XBT; position ~1 XBT -> -3% move ~ -3% ROE
    roe_xbt, hit, tb = E.stop_cf(b, int(s.t.iloc[0]), int(s.t.iloc[-1]), g, eq0, lambda t: 0.00075)
    assert hit and tb == g.b[130]
    val = E.exec_cost(10000, 9700.0)
    exp = (b.ev_net[0] + val - b.ev_cost[0] - abs(val) * 0.00075) / 1e8
    assert roe_xbt == pytest.approx(exp)
    assert b.net > 0 > roe_xbt                                       # the stop would have locked in the loss


def test_reverse_split_and_liquidation_closed_episode():
    s = _stream([(0, "e", -1000, 10000, "INC"), (5, "LIQ", 1000, 10800, "DEC", False)])
    o = E.classify_orders(s)
    assert o.type.tolist() == ["ENTRY", "FULL_CLOSE"]
    b = E.cf_book(s, o, "NO_ADD")
    assert b.pos == 0 and b.ev_t[-1] == T0 + 5 * MIN                # CF not extended past the liquidation
    with pytest.raises(ValueError):
        E.Book().trade(0, 5, 1.0, 0.0, 1.0) or E.Book(pos=5, cost=1).trade(0, -8, 1.0, 0.0, 1.0)


def test_grid_minute_bounds_and_market_alignment():
    d = "20210101"
    day0 = int(pd.Timestamp(d, tz="UTC").value)
    q = pd.DataFrame({"ts": [day0 + 10 * 10**9, day0 + 50 * 10**9, day0 + 61 * 10**9, day0 + 90 * 10**9],
                      "bid": [100.0, 102.0, 99.0, 101.0], "ask": [101.0, 103.0, 99.0, 102.0]})   # 3rd is locked
    raw = G.day_minutes(d, q, pd.DataFrame({"ts": [day0 + 5 * 10**9], "size": [7]}))
    g = G.finalize(raw)
    assert g.b.iloc[0] == day0 + 60 * 10**9
    assert g.mid_close.iloc[0] == 102.5 and g.mid_hi.iloc[0] == 102.5 and g.mid_lo.iloc[0] == 100.5
    assert g.mid_close.iloc[1] == 101.5                              # locked quote ignored
    assert np.isnan(g.mid_close.iloc[3])                             # last quote 90 s old at b=240 s -> stale
    assert g.n_trades.iloc[0] == 1 and g.trade_qty.iloc[0] == 7
    assert list(E.minute_bounds(T0 + 30 * 10**9, T0 + 3 * MIN)) == [T0 + 2 * MIN, T0 + 3 * MIN]


def test_context_and_entry_reference_use_only_the_past():
    rng = np.random.default_rng(0)
    prices = 10000 * np.exp(np.cumsum(rng.normal(0, 1e-3, 400)))
    g1 = _grid(prices.copy())
    p2 = prices.copy()
    t = T0 + 30 * MIN + 17 * 10**9
    cut = int(g1.idx(E.Grid.floor(t))) + 1
    p2[cut:] *= 1.5
    g2 = _grid(p2)
    c1, c2 = g1.context([t]), g2.context([t])
    for k in c1:
        assert c1[k][0] == c2[k][0], k
    assert g1.M(E.Grid.floor(t)) == g2.M(E.Grid.floor(t))


def test_equity_alignment_wallet_and_pending():
    w = pd.DataFrame({"transactstatus": ["Completed", "Completed"], "transacttype": ["Deposit", "RealisedPNL"],
                      "date_utc": pd.to_datetime(["2021-01-01", "2021-01-01"]), "walletbalance_sat": [1e8, 1.5e8]})
    g = _grid(np.full(2000, 10000.0), start=T0 - 60 * MIN)
    pend = lambda tq: np.zeros(len(np.asarray(tq)))                  # noqa: E731
    before = E.equity_at([T0 + 11 * 60 * MIN], w, pend, g, [0], [0])[0]
    after = E.equity_at([T0 + 13 * 60 * MIN], w, pend, g, [0], [0])[0]
    assert before == pytest.approx(1.0) and after == pytest.approx(1.5)   # RealisedPNL posts at 12:00 UTC
    held = E.equity_at([T0 + 13 * 60 * MIN], w, pend, g, [10000], [E.exec_cost(10000, 9000)])[0]
    assert held == pytest.approx(1.5 + (E.exec_cost(10000, 10000) - E.exec_cost(10000, 9000)) / 1e8)


def test_outcome_classes():
    assert [E.outcome_class(x) for x in (0.01, 0.0, -0.01, -0.06)] == ["SUCCESS", "FLAT", "FAILED", "CATASTROPHIC"]
