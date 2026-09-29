"""D5.2 derivatives flow: timestamp normalization, venue mapping, liquidation sides, basis math,
term-structure roll, PIT alignment, duplicates, missing fields, fold split and cost application."""
from __future__ import annotations

import gzip
import json

import numpy as np
import pytest

from app.crypto.derivatives import normalize as N
from app.crypto.research import derivatives_flow as D
from app.crypto.research import long_horizon as LH

MIN, FIVE, DAY = D.MIN, D.FIVE, D.DAY_MS
T0 = 1_700_000_100_000 - (1_700_000_100_000 % FIVE)


def test_timestamp_normalization_microseconds_and_merge_duplicates():
    t = np.array([1_735_689_600_000_000, 1_704_067_200_000], dtype=np.int64)     # 2025 spot us, 2024 ms
    assert D.norm_ms(t).tolist() == [1_735_689_600_000, 1_704_067_200_000]
    ts, cl = D._merge([np.array([3, 1, 3])], [np.array([30.0, 10.0, 31.0])])
    assert ts.tolist() == [1, 3] and cl.tolist() == [10.0, 31.0]                   # duplicate: last wins


def test_close5_uses_last_minute_of_bin_and_missing_is_nan():
    B = np.array([T0 + FIVE, T0 + 2 * FIVE, T0 + 3 * FIVE])
    t1 = np.array([T0, T0 + 3 * MIN, T0 + FIVE + MIN])                          # bin 1: minutes 0,3; bin 2: minute 1
    c1 = np.array([1.0, 2.0, 3.0])
    out = D.close5_from_1m(t1, c1, B)
    assert out[0] == 2.0 and out[1] == 3.0 and np.isnan(out[2])
    carried = D.carry_one(np.array([1.0, np.nan, np.nan, 4.0]))
    assert carried[1] == 1.0 and np.isnan(carried[2]) and carried[3] == 4.0     # one bar only


def test_okx_start_stamp_becomes_bar_end(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "ARCH", tmp_path)
    (tmp_path / "okx").mkdir()
    with gzip.open(tmp_path / "okx" / "okx_swap_last_5m.jsonl.gz", "wt") as f:
        f.write(json.dumps([str(T0), "1", "1", "1", "100.5", "0"]) + "\n")
    t, c = D.okx_series("okx_swap_last")
    assert t.tolist() == [T0 + FIVE] and c.tolist() == [100.5]
    B = np.array([T0 + FIVE, T0 + 2 * FIVE])
    assert D.on5(t, c, B)[0] == 100.5 and np.isnan(D.on5(t, c, B)[1])


def test_asof_respects_known_at_delay():
    B = np.array([T0 + FIVE, T0 + 2 * FIVE])
    src_ts = np.array([T0 + FIVE])
    v = D.asof(src_ts, np.array([7.0]), src_ts + FIVE, B)                      # OI known 5 min after stamp
    assert np.isnan(v[0]) and v[1] == 7.0


def test_decision_bar_sees_only_closed_5m_bar():
    B = D.grid5()
    f5 = np.arange(len(B), dtype=float)
    ts1 = np.array([B[3] - MIN, B[3], B[3] + 3 * MIN])                          # bars closing at B3, B3+1m, B3+4m
    out = D.to_1m(f5, B, ts1)
    assert out.tolist() == [3.0, 3.0, 3.0]
    assert D.to_1m(f5, B, np.array([B[3] + 4 * MIN]))[0] == 4.0                 # closes at B4 -> sees bar 4


def _x(n=3, **over):
    x = {"B": np.arange(n) * FIVE + T0 + FIVE}
    for v in ("bybit", "binance", "okx"):
        for k in ("last", "mark", "index"):
            x[f"{v}_{k}"] = np.full(n, 100.0)
    x.update({"spot": np.full(n, 100.0), "bybit_funding": np.full(n, 1e-4), "binance_funding": np.full(n, 5e-5),
              "bybit_oi": np.full(n, 10.0), "binance_oi": np.full(n, 20.0), "fut_near": np.full(n, 101.0),
              "fut_far": np.full(n, 103.0)})
    x["cm_index"] = np.full(n, 100.0)
    x["fut_near_exp"] = x["B"] + 30 * DAY
    x["fut_far_exp"] = x["B"] + 120 * DAY
    x.update(over)
    return x


def test_basis_calculations():
    f = D.features5(_x(bybit_last=np.full(3, 99.0)))
    assert f["xbasis_bybit_vs_peers"][0] == pytest.approx(np.log(99 / 100))
    assert f["spot_basis_bybit"][0] == pytest.approx(np.log(99 / 100))
    assert f["xfunding_spread"][0] == pytest.approx(5e-5)
    assert f["ts_near_ann"][0] == pytest.approx(np.log(101 / 100) * 365 / 30)
    assert f["ts_far_ann"][0] == pytest.approx(np.log(103 / 100) * 365 / 120)
    assert f["ts_slope"][0] == pytest.approx(f["ts_far_ann"][0] - f["ts_near_ann"][0])
    assert f["ts_inverted"][0] == 1.0                                            # 3%/120d < 1%/30d annualized
    assert f["xlast_dispersion"][0] == pytest.approx(np.std([np.log(99), np.log(100), np.log(100)]))


def test_missing_venue_gives_nan_not_zero():
    f = D.features5(_x(okx_last=np.array([np.nan, 100.0, 100.0])))
    assert np.isnan(f["xbasis_bybit_vs_peers"][0]) and np.isnan(f["xlast_dispersion"][0])
    assert np.isfinite(f["xbasis_bybit_vs_peers"][1])


def test_contract_roll_seven_days():
    B = np.array([0, 10, 20, 30], dtype=np.int64) * DAY
    closes = {"A": (int(25 * DAY), np.array([1.0, 1.1, 1.2, np.nan])),
              "B": (int(60 * DAY), np.array([2.0, 2.1, 2.2, 2.3])),
              "C": (int(90 * DAY), np.array([np.nan, np.nan, 3.2, 3.3]))}
    near, far, ne, fe = D.roll_near_far(B, closes)
    # day 0,10: A has >7d -> near A, far B. day 20: A has 5d -> near B, far C. day 30: near B, far C
    assert near.tolist() == [1.0, 1.1, 2.2, 2.3]
    assert far.tolist() == [2.0, 2.1, 3.2, 3.3]
    assert ne.tolist() == [25 * DAY, 25 * DAY, 60 * DAY, 60 * DAY]


def test_liquidation_side_mapping_and_venues():
    by = {"topic": "allLiquidation.BTCUSDT", "data": [{"T": 1, "s": "BTCUSDT", "S": "Buy", "v": "0.5", "p": "100"}]}
    bn = {"stream": "btcusdt@forceOrder", "data": {"e": "forceOrder", "o": {"s": "BTCUSDT", "S": "SELL", "q": "0.2", "z": "0.2",
                                                                            "p": "99", "ap": "98", "T": 2}}}
    ok = {"arg": {"channel": "liquidation-orders"}, "data": [{"instId": "BTC-USDT-SWAP", "details": [
        {"posSide": "short", "side": "buy", "sz": "3", "bkPx": "101", "ts": "3"}]}]}
    r = list(N.bybit(by)) + list(N.binance(bn)) + list(N.okx(ok))
    assert [x["liquidated_side"] for x in r] == ["LONG", "LONG", "SHORT"]
    assert r[1]["price"] == 98.0 and r[1]["is_aggregated"] is True
    assert r[2]["qty_base"] == pytest.approx(0.03) and r[2]["notional_quote"] == pytest.approx(3.03)
    assert N.VENUE_SYMBOL == {"bybit": "BTCUSDT", "binance": "BTCUSDT", "okx": "BTC-USDT-SWAP"}
    unknown = list(N.okx({"arg": {"channel": "liquidation-orders"}, "data": [{"instId": "ETH-USDT-SWAP", "details": [
        {"posSide": "net", "sz": "1", "bkPx": "1", "ts": "4"}]}]}))
    assert unknown[0]["liquidated_side"] is None and unknown[0]["qty_base"] is None     # never guessed
    assert len(N.dedupe(r + r)) == 3


def test_walk_forward_split_matches_d5_1():
    bounds = [LH.ms(LH.SAMPLE_START)] + [LH.ms(d) for d in LH.FOLD_STARTS] + [LH.ms(LH.END)]
    assert bounds == sorted(bounds) and len(bounds) == 11
    t = np.array([LH.ms("2021-06-01"), LH.ms("2022-03-01"), LH.ms("2026-02-01")])
    assert (np.searchsorted(bounds, t, side="right") - 1).tolist() == [0, 1, 9]   # train-only prefix, F1, F9


def test_cost_application():
    T = {"long": np.array([0.01]), "short": np.array([-0.01]), "ratio": np.array([1.01]), "fund_long": np.array([1e-4])}
    v = D.net_values(T, "LONG", 0.00055, {"BASE": 1e-6, "STRESS": 2e-4})
    assert v["ZERO"][0] == 0.01
    assert v["VIP0_BASE"][0] == pytest.approx(0.01 - 0.00055 * 2.01 - 1e-6 - 1e-4)
    s = D.net_values(T, "SHORT", 0.00055, {"BASE": 1e-6, "STRESS": 2e-4})
    assert s["VIP0_FEE"][0] == pytest.approx(-0.01 - 0.00055 * 2.01 + 1e-4)      # short receives funding
    assert set(v) == {"ZERO", "VIP0_FEE", "VIP0_BASE", "VIP0_STRESS"}             # no maker scenario


def test_isolation_research_modules_not_imported_by_engine():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "app" / "crypto"
    for p in list((root / "paper").rglob("*.py")) + list((root / "terminal").rglob("*.py")):
        t = p.read_text(encoding="utf-8")
        assert "derivatives_flow" not in t and "crypto.derivatives" not in t, p
