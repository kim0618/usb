"""D5.2 historical parity: the frozen research C1 against the operational C1 engine.

    PYTHONPATH=backend .venv/bin/python scripts/c1_parity.py [start] [end] [segment_days] [out.json]

This is the gate the signal layer had to pass before anything was put on a screen. It needs the
research data (`data/runtime/crypto/`), so it runs on a development machine, not on the server.

The research arrays are the authority. The engine is fed the same raw 1m/5m series the study read
and then has to reproduce, bar by bar: the spot basis, its bucket, the open-interest change, the
volatility regime, the event mask, and the 4 h LONG outcome after the contract's cost.

Run in day-aligned segments with 35 days of warm-up each, because a bar's verdict depends on at
most 30 whole days (the bucket window) plus 1,440 bars (the volatility window): the segments are
independent, and 2.9 M rows of Python floats will not fit in memory at once.
"""
import sys, json, math, pathlib
import numpy as np
from app.crypto.research import dataset, features as F, long_horizon as LH
from app.crypto.research import derivatives_flow as DF
from app.crypto.c1 import contract as K
from app.crypto.c1.engine import C1Engine
from app.crypto.c1.grid import Bar, build_grid
from app.crypto.c1 import shadow as SH

DAY = 86_400_000
WARMUP_DAYS = 35

print("loading research grid ...", flush=True)
g = dataset.load()
ts = g["ts"]; n = len(ts)
bounds = np.array([LH.ms(LH.SAMPLE_START)] + [LH.ms(d) for d in LH.FOLD_STARTS] + [LH.ms(LH.END)])
f1 = (ts >= bounds[0]) & (ts < bounds[1])
reg = F.regime_labels(g, f1)
x = DF.load_inputs(g); f5 = DF.features5(x)
s1_ref = DF.to_1m(f5["spot_basis_bybit"], x["B"], ts)
bucket_ref = F.bucketize(s1_ref, n // F.DAY_BARS)
with np.errstate(invalid="ignore", divide="ignore"):
    oi_ref = np.log(g["oi"] / F._lag(g["oi"], 60))
c1_ref = (bucket_ref == 0) & (oi_ref < 0) & (reg["vol"] == 0)
_lnC = np.log(g["close"])
rv_ref = F._rstd(_lnC - F._lag(_lnC, 1), F.DAY_BARS)
VOL_LABEL = {0: "HIGH", 1: "MID", 2: "LOW", -1: "UNKNOWN"}

print("loading raw venue series ...", flush=True)
spot_ts, spot_close = DF.binance_spot()
oi_raw = dataset._read("open_interest_5m", ["open_interest"])
spot_all = list(zip(spot_ts.tolist(), spot_close.tolist()))
oi_all = list(zip(oi_raw["timestamp_ms"].tolist(), oi_raw["open_interest"].tolist()))

targets = F.targets(g, K.OFFICIAL_HORIZON_MIN)
micro = {"BASE": K.MICRO_BASE_FRAC, "STRESS": K.MICRO_STRESS_FRAC}
net_ref = DF.net_values(targets, "LONG", K.TAKER_RATE, micro)
funding_schedule = list(zip(g["funding_ts"].tolist(), g["funding_rate"].tolist()))

SEG_START = int(np.datetime64(sys.argv[1] if len(sys.argv) > 1 else "2021-03-04", "ms").astype(np.int64))
SEG_END = int(np.datetime64(sys.argv[2] if len(sys.argv) > 2 else "2026-09-23", "ms").astype(np.int64))
SEG_DAYS = int(sys.argv[3]) if len(sys.argv) > 3 else 180
OUT = pathlib.Path(sys.argv[4]) if len(sys.argv) > 4 else None

report = {"segments": [], "bars_compared": 0, "mask_mismatch": 0, "bucket_mismatch": 0,
          "vol_mismatch": 0, "s1_max_abs_diff": 0.0, "oi_max_abs_diff": 0.0,
          "rv_max_abs_diff": 0.0, "events": 0, "net_max_abs_diff": 0.0, "gross_max_abs_diff": 0.0,
          "entry_mismatch": 0, "exit_mismatch": 0, "shadow_compared": 0, "nets": [], "event_rows": []}

segment_start = SEG_START
while segment_start < SEG_END:
    segment_stop = min(segment_start + SEG_DAYS * DAY, SEG_END)
    load_from = segment_start - WARMUP_DAYS * DAY
    lo = int(np.searchsorted(ts, load_from, "left"))
    hi = int(np.searchsorted(ts, segment_stop, "left"))
    # the official 4 h exit of a signal in this segment lands after it
    hi_pad = min(hi + K.OFFICIAL_HORIZON_MIN + 2, n)
    sub_ts = ts[lo:hi_pad]
    bars = [Bar(ts_ms=int(t), open=float(o), high=float(h), low=float(l), close=float(c))
            for t, o, h, l, c in zip(sub_ts, g["open"][lo:hi_pad], g["high"][lo:hi_pad],
                                     g["low"][lo:hi_pad], g["close"][lo:hi_pad])
            if np.isfinite(c)]
    s_lo = int(np.searchsorted(spot_ts, load_from - 10 * 60_000, "left"))
    s_hi = int(np.searchsorted(spot_ts, segment_stop, "left"))
    o_lo = int(np.searchsorted(oi_raw["timestamp_ms"], load_from - 2 * 3_600_000, "left"))
    o_hi = int(np.searchsorted(oi_raw["timestamp_ms"], segment_stop, "left"))
    grid = build_grid(bars, spot_all[s_lo:s_hi], oi_all[o_lo:o_hi],
                      start_ms=int(sub_ts[0]), end_ms=int(sub_ts[-1]))
    engine = C1Engine()
    begin = grid.index_of(segment_start)
    stop = grid.index_of(segment_stop)
    compared = 0
    for index, ev, signal in engine.walk(grid, start_index=begin, stop_index=stop):
        ref = lo + (grid.ts(index) - int(sub_ts[0])) // 60_000
        assert ts[ref] == grid.ts(index)
        compared += 1
        if bool(ev.on) != bool(c1_ref[ref]):
            report["mask_mismatch"] += 1
        if ev.features.s1_bucket != int(bucket_ref[ref]):
            report["bucket_mismatch"] += 1
        if ev.features.vol_regime != VOL_LABEL[int(reg["vol"][ref])]:
            report["vol_mismatch"] += 1
        for key, mine, theirs in (("s1_max_abs_diff", ev.features.s1, s1_ref[ref]),
                                  ("oi_max_abs_diff", ev.features.oi_change_1h, oi_ref[ref]),
                                  ("rv_max_abs_diff", ev.features.rv_24h, rv_ref[ref])):
            if theirs is None:
                continue
            if math.isnan(mine) and not np.isfinite(theirs):
                continue
            if math.isnan(mine) or not np.isfinite(theirs):
                report[key] = float("inf")
                continue
            report[key] = max(report[key], abs(mine - theirs))
        if signal is not None:
            report["events"] += 1
            trade = SH.open_shadow(signal, grid, funding_schedule)
            SH.settle(trade, signal, grid, funding_schedule)
            if trade.status == "SETTLED":
                report["shadow_compared"] += 1
                e_ref = float(targets["ratio"][ref])
                if abs(trade.entry_price - float(g["open"][ref + 1])) > 0:
                    report["entry_mismatch"] += 1
                if abs(trade.exit_price - float(g["open"][ref + 1 + K.OFFICIAL_HORIZON_MIN])) > 0:
                    report["exit_mismatch"] += 1
                report["gross_max_abs_diff"] = max(report["gross_max_abs_diff"],
                                                   abs(trade.gross_return - float(targets["long"][ref])))
                report["net_max_abs_diff"] = max(report["net_max_abs_diff"],
                                                 abs(trade.net_return - float(net_ref["VIP0_BASE"][ref])))
                report["nets"].append(trade.net_return)
                report["event_rows"].append({"signal_id": signal.signal_id, "ref": int(ref),
                                             "net": trade.net_return,
                                             "mfe": trade.mfe, "mae": trade.mae})
    report["bars_compared"] += compared
    report["segments"].append({"from": int(segment_start), "to": int(segment_stop), "bars": compared})
    print(f"  {np.datetime64(segment_start,'ms')} .. {np.datetime64(segment_stop,'ms')} "
          f"bars {compared} events {report['events']} mask_mismatch {report['mask_mismatch']}", flush=True)
    segment_start = segment_stop

out = {k: v for k, v in report.items() if k not in ("nets", "event_rows")}
print(json.dumps(out, indent=1))
verdict = "PASS" if (report["mask_mismatch"] == 0 and report["bucket_mismatch"] == 0
                     and report["vol_mismatch"] == 0 and report["entry_mismatch"] == 0
                     and report["exit_mismatch"] == 0 and report["s1_max_abs_diff"] == 0.0
                     and report["oi_max_abs_diff"] == 0.0
                     and report["gross_max_abs_diff"] == 0.0) else "FAIL"
print("PARITY", verdict)
if OUT is not None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=1))
raise SystemExit(0 if verdict == "PASS" else 1)
