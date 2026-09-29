"""E0 expert-execution study: parsing, contract accounting, position/episode reconstruction and
PIT alignment. Synthetic data only; the real ledger is never required to run these."""
from __future__ import annotations

import ast
import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.crypto.research.expert_execution import alignment as A
from app.crypto.research.expert_execution import contracts as K
from app.crypto.research.expert_execution import ingest as I
from app.crypto.research.expert_execution import positions as P

INV = K.ContractSpec("XBTUSD", "inverse", -1e8, "USD", "XBt", 0.0, 0, True)


def _fills(rows, symbol="XBTUSD", spec=INV):
    """rows: (ts, signed_qty, price[, exectype, text]) -> frame shaped like normalized executions."""
    recs = []
    for i, r in enumerate(rows):
        ts, q, px = r[:3]
        et = r[3] if len(r) > 3 else "Trade"
        text = r[4] if len(r) > 4 else ""
        cost = round(K.exec_cost_sat(spec.kind, spec.multiplier, q, px)) if q else 0
        recs.append({"seq": i, "transact_ts": pd.Timestamp(ts, tz="UTC"), "symbol": symbol,
                     "signed_qty": q, "lastqty": abs(q), "lastpx": px, "execcost": cost,
                     "execcomm": 0, "exectype": et, "text": text,
                     "homenotional": float(np.sign(q)) if et != "Funding" else 0.0})
    return pd.DataFrame(recs)


# ---------------------------------------------------------------- parsing / normalization
def test_timestamp_parse_variable_fraction_and_malformed():
    s = pd.Series(["2018-03-05 09:16:57.937933", "2021-01-01 04:00:00.0", "2019-04-01 11:59:59.9", "57:26.3", ""])
    t = I.parse_ts(s)
    assert t.iloc[0] == pd.Timestamp("2018-03-05 09:16:57.937933", tz="UTC")
    assert t.iloc[1] == pd.Timestamp("2021-01-01 04:00:00", tz="UTC")
    assert t.iloc[2].microsecond == 900000
    assert t.iloc[3] is pd.NaT and t.iloc[4] is pd.NaT


def test_side_normalization_and_unknown_rejected():
    assert I.normalize_side(pd.Series(["Buy", "Sell", ""])).tolist() == [1, -1, 0]
    with pytest.raises(ValueError):
        I.normalize_side(pd.Series(["buy"]))


def test_raw_read_keeps_multiline_text_and_empty_strings(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text('﻿a,text,b\n1,"Triggered: x\nSubmission",\n2,,5\n', encoding="utf-8")
    d = I.read_raw_strings(p)
    assert len(d) == 2 and d.text.iloc[0] == "Triggered: x\nSubmission"
    assert d.b.iloc[0] == "" and d.a.iloc[1] == "2"


def test_qty_price_conversion_and_duplicates(tmp_path):
    raw = pd.DataFrame({
        "src_file": ["f"] * 3, "src_row": [0, 1, 2],
        "execid": ["a", "b", "b"], "side": ["Buy", "Sell", "Sell"],
        "lastqty": ["2000", "5", "5"], "lastpx": ["11441.5", "0.2276", "0.2276"],
        "transacttime": ["2018-03-05 09:16:57.937933"] * 3, "timestamp": ["2018-03-05 09:16:57.937933"] * 3,
        "lastliquidityind": ["AddedLiquidity", "RemovedLiquidity", "RemovedLiquidity"],
        **{c: ["1"] * 3 for c in ["orderqty", "leavesqty", "cumqty", "execcost", "execcomm"]},
        **{c: [""] * 3 for c in ["price", "avgpx", "commission", "stoppx", "displayqty",
                                 "homenotional", "foreignnotional", "pegoffsetvalue"]},
    })
    df = I.normalize_executions(raw)
    assert df.lastqty.dtype == "Int64" and df.lastpx.iloc[0] == 11441.5
    assert df.signed_qty.tolist() == [2000, -5, -5]
    assert df.is_maker.tolist() == [True, False, False]
    assert df.price.isna().all()                      # empty -> NaN, not 0
    assert df.execid.duplicated().sum() == 1          # duplicates are detectable, not silently dropped


# ---------------------------------------------------------------- zip safety
def _zip(tmp_path, members):
    p = tmp_path / "z.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in members:
            z.writestr(n, data)
    return p


def test_zip_check_flags_traversal_and_bomb(tmp_path):
    assert I.check_zip(_zip(tmp_path, [("a.csv", "x,y\n1,2\n")]))["safe"]
    assert "path_traversal" in I.check_zip(_zip(tmp_path, [("../evil.csv", "x")]))["unsafe_reasons"]
    assert "compression_ratio" in I.check_zip(_zip(tmp_path, [("big.csv", "0" * 5_000_000)]))["unsafe_reasons"]
    assert "executable_member" in I.check_zip(_zip(tmp_path, [("run.exe", "MZ")]))["unsafe_reasons"]


# ---------------------------------------------------------------- contract model
def test_inverse_accounting_matches_closed_form():
    # long 100 USD contracts 10000 -> 11000: 100 * (1/10000 - 1/11000) XBT
    pnl = K.round_trip_pnl_sat("inverse", -1e8, 100, 10000, 11000)
    assert pnl == pytest.approx(100 * (1 / 10000 - 1 / 11000) * 1e8)
    # short profits when price falls, and inverse PnL is asymmetric in price
    up = K.round_trip_pnl_sat("inverse", -1e8, -100, 10000, 11000)
    down = K.round_trip_pnl_sat("inverse", -1e8, -100, 10000, 9000)
    assert up < 0 < down and abs(down) > abs(up)
    # quanto ETHUSD (100 XBt per $1 per contract): linear in price
    assert K.round_trip_pnl_sat("quanto", 100, 10, 2000, 2100) == pytest.approx(10 * 100 * 100)


def test_ledger_sign_convention_first_rows():
    # From the real file: Sell 2000 XBTUSD @ 11441.5 -> execcost +17,480,000; Buy 1051 ETHUSD @ 2115 -> +222,286,500
    assert K.exec_cost_sat("inverse", -1e8, -2000, 11441.5) == pytest.approx(17_480_138, rel=1e-5)
    assert K.exec_cost_sat("quanto", 100, 1051, 2115.0) == 222_286_500


def test_infer_specs_identifies_inverse_and_quanto():
    rng = np.random.default_rng(0)
    q = rng.integers(1, 5000, 200) * rng.choice([-1, 1], 200)
    px = rng.uniform(3000, 60000, 200)
    inv = pd.DataFrame({"symbol": "XBTUSD", "signed_qty": q, "lastpx": px,
                        "execcost": np.round(q * -1e8 / px), "currency": "USD", "settlcurrency": "XBt"})
    eth_px = np.round(px / 20, 2)
    qu = pd.DataFrame({"symbol": "ETHUSD", "signed_qty": q, "lastpx": eth_px,
                       "execcost": q * 100 * eth_px, "currency": "USD", "settlcurrency": "XBt"})
    specs = K.infer_specs(pd.concat([inv, qu]))
    assert specs["XBTUSD"].kind == "inverse" and specs["XBTUSD"].multiplier == -1e8
    assert specs["ETHUSD"].kind == "quanto" and specs["ETHUSD"].multiplier == 100
    assert specs["XBTUSD"].perpetual and not K._is_perpetual("XBTU21") and not K._is_perpetual("ETHUSDM21")


# ---------------------------------------------------------------- positions / episodes
def test_classify_sign_transitions():
    assert P.classify(0, 5) == "OPEN_LONG" and P.classify(0, -5) == "OPEN_SHORT"
    assert P.classify(5, 3) == "ADD_LONG" and P.classify(-5, -3) == "ADD_SHORT"
    assert P.classify(5, -3) == "REDUCE_LONG" and P.classify(-5, 3) == "REDUCE_SHORT"
    assert P.classify(5, -5) == "CLOSE_LONG" and P.classify(-5, 5) == "CLOSE_SHORT"
    assert P.classify(5, -8) == "REVERSE_TO_SHORT" and P.classify(-5, 8) == "REVERSE_TO_LONG"


def test_flat_and_reverse_reconstruction_and_realized_pnl():
    g = _fills([
        ("2021-01-01 00:00", 100, 10000),
        ("2021-01-01 00:10", 100, 12000),      # add: avg entry is harmonic for inverse
        ("2021-01-01 01:00", -300, 11000),     # reverse to short 100
        ("2021-01-01 02:00", 100, 10000),      # close short -> flat
    ])
    rows, ep, end = P.reconstruct_symbol(g, INV)
    assert rows.action.tolist() == ["OPEN_LONG", "ADD_LONG", "REVERSE_TO_SHORT", "CLOSE_SHORT"]
    assert rows.pos_after.tolist() == [100, 200, -100, 0] and end[0] == 0 and end[1] == 0
    exp_avg = 200 / (100 / 10000 + 100 / 12000)
    assert rows.avg_entry_before.iloc[2] == pytest.approx(exp_avg, rel=1e-4)
    long_pnl = sum(K.round_trip_pnl_sat("inverse", -1e8, 100, p, 11000) for p in (10000, 12000))
    short_pnl = K.round_trip_pnl_sat("inverse", -1e8, -100, 11000, 10000)
    assert rows.realized_gross_sat.iloc[2] == pytest.approx(long_pnl, abs=2)
    assert rows.realized_gross_sat.iloc[3] == pytest.approx(short_pnl, abs=2)
    assert len(ep) == 2 and ep.closed_by.tolist() == ["REVERSE", "CLOSE"]
    assert ep.direction.tolist() == [1, -1] and bool(ep.opened_by_reverse.iloc[1])
    assert rows.closes_episode_id.iloc[2] == 0 and rows.episode_id.iloc[2] == 1


def test_partial_reduce_keeps_average_cost():
    g = _fills([("2021-01-01", -1000, 20000), ("2021-01-02", 400, 18000), ("2021-01-03", 600, 22000)])
    rows, ep, end = P.reconstruct_symbol(g, INV)
    assert rows.action.tolist() == ["OPEN_SHORT", "REDUCE_SHORT", "CLOSE_SHORT"]
    assert rows.avg_entry_before.iloc[2] == pytest.approx(20000, rel=1e-4)
    total = K.round_trip_pnl_sat("inverse", -1e8, -400, 20000, 18000) + K.round_trip_pnl_sat("inverse", -1e8, -600, 20000, 22000)
    assert rows.realized_gross_sat.sum() == pytest.approx(total, abs=3)
    assert len(ep) == 1 and ep.fills.iloc[0] == 3


def test_funding_rows_do_not_move_position_and_snapshot_check():
    g = _fills([("2021-01-01 03:00", -500, 30000), ("2021-01-01 04:00", 0, 30100, "Funding"),
                ("2021-01-01 05:00", 500, 29000)])
    g.loc[1, ["lastqty", "homenotional", "execcomm"]] = [500, -0.0166, 12]
    rows, ep, _ = P.reconstruct_symbol(g, INV)
    assert rows.action.iloc[1] == "FUNDING" and rows.pos_after.iloc[1] == -500
    assert ep.funding_sat.iloc[0] == 12
    chk = P.funding_snapshot_check(g, rows)
    assert chk["match"] == 1 and chk["mismatch"] == 0
    g.loc[1, "lastqty"] = 499
    assert P.funding_snapshot_check(g, rows)["mismatch"] == 1


def test_liquidation_closes_episode():
    g = _fills([("2021-01-01", 100, 10000), ("2021-01-02", -100, 8000, "Trade", "Liquidation")])
    _, ep, _ = P.reconstruct_symbol(g, INV)
    assert ep.closed_by.iloc[0] == "LIQUIDATION" and ep.liquidation_fills.iloc[0] == 1


# ---------------------------------------------------------------- PIT alignment for later market data
def test_alignment_never_uses_the_bar_containing_the_event():
    minute = 60 * 10**9
    bars = np.arange(0, 10 * minute, minute, dtype=np.int64)
    ev = np.array([5 * minute + 1, 5 * minute, 0], dtype=np.int64)
    last = A.last_closed_bar_index(bars, minute, ev)
    # event at 5:00.000000001 sits in bar 5 -> last closed bar is 4; exactly 5:00 -> bar 4 closed at 5:00
    assert last.tolist() == [4, 4, -1]
    fwd = A.first_forward_bar_index(bars, ev)
    assert fwd.tolist() == [6, 5, 0]
    assert all(bars[i] + minute <= e for i, e in zip(last, ev) if i >= 0)
    assert all(bars[j] >= e for j, e in zip(fwd, ev) if j < len(bars))


# ---------------------------------------------------------------- isolation
def test_engine_does_not_import_expert_execution_and_vice_versa():
    root = Path(__file__).resolve().parents[2] / "app" / "crypto"
    pkg = root / "research" / "expert_execution"
    for p in root.rglob("*.py"):
        if pkg in p.parents:
            continue
        assert "expert_execution" not in p.read_text(encoding="utf-8"), p
    for p in pkg.glob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                assert not node.module.startswith("app."), (p, node.module)
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith("app.") for a in node.names), p
