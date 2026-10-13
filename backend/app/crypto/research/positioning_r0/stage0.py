"""Positioning R0 Stage 0: states only, no outcome (contract sections 4 and 5).

Run from backend/: python -m app.crypto.research.positioning_r0.stage0 [out.json]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from ..ptm_r1 import data as PD
from ..ptm_r1.states import NONE, run_lengths
from . import data as MD
from . import states as S

CONTRACT = PD.ROOT / "docs/crypto/positioning_r0/CONTRACT_POSITIONING_R0_V1.md"
OUT = PD.ROOT / "data/research/crypto/positioning_r0/stage0_v1.json"
FORBIDDEN = ("return", "pnl", "net", "gross", "profit", "mfe", "mae", "outcome")


def pct(x: np.ndarray) -> dict | None:
    a = x[~np.isnan(x)]
    if len(a) == 0:
        return None
    q = np.percentile(a, [1, 5, 25, 50, 75, 95, 99])
    return {"n": int(len(a)), "min": float(a.min()), "p1": float(q[0]), "p5": float(q[1]), "p25": float(q[2]),
            "median": float(q[3]), "p75": float(q[4]), "p95": float(q[5]), "p99": float(q[6]),
            "max": float(a.max())}


def state_freq(s: np.ndarray, labels=((1, "LONG"), (-1, "SHORT"), (0, "EVEN"), (NONE, "NONE"))) -> dict:
    return {name: float((s == v).mean()) for v, name in labels}


def runs_hours(s: np.ndarray, values, per_hour: int) -> dict | None:
    r = run_lengths(s, values=values)
    if len(r) == 0:
        return None
    r = r / per_hour
    return {"runs": int(len(r)), "median_h": float(np.median(r)), "mean_h": float(r.mean()),
            "p90_h": float(np.percentile(r, 90))}


def flips_per_day(s: np.ndarray, per_day: int) -> float:
    d = s[(s == 1) | (s == -1)]
    return float((d[1:] != d[:-1]).sum() / (len(s) / per_day))


def transition(codes: np.ndarray, k: int) -> list[list[float]]:
    m = np.zeros((k, k))
    a, b = codes[:-1], codes[1:]
    ok = (a >= 0) & (b >= 0)
    np.add.at(m, (a[ok], b[ok]), 1)
    rows = m.sum(axis=1, keepdims=True)
    with np.errstate(invalid="ignore"):
        return np.round(np.where(rows > 0, m / rows, 0.0), 4).tolist()


def code8(hr: S.Hourly) -> np.ndarray:
    ok = np.isin(hr.G, (1, -1)) & np.isin(hr.TA, (1, -1)) & np.isin(hr.TP, (1, -1))
    c = ((hr.G == 1) * 4 + (hr.TA == 1) * 2 + (hr.TP == 1)).astype(np.int8)
    return np.where(ok, c, -1).astype(np.int8)


def label8(c: int) -> str:
    return f"G{'L' if c & 4 else 'S'}_TA{'L' if c & 2 else 'S'}_TP{'L' if c & 1 else 'S'}"


def viability(n48: int) -> str:
    return "TOO_RARE" if n48 < 100 else "LIMITED" if n48 < 500 else "RESEARCHABLE"


def candidate_counts(hr: S.Hourly) -> dict:
    out = {}
    for c in S.CANDIDATES:
        lo, sh = S.conditions(hr, c)
        ev = S.onsets(hr, c)
        dd = S.dedup_4h(ev)
        n48 = S.overlap_48h(dd)
        out[c] = {"name": S.NAMES[c], "hours_condition_long": int(lo.sum()), "hours_condition_short": int(sh.sum()),
                  "raw_onsets": len(ev), "raw_long": sum(1 for _, d in ev if d == 1),
                  "dedup_4h_episodes": len(dd), "dedup_long": sum(1 for _, d in dd if d == 1),
                  "dedup_short": sum(1 for _, d in dd if d == -1),
                  "overlap_48h_n": len(n48), "overlap_48h_long": sum(1 for _, d in n48 if d == 1),
                  "viability": viability(len(n48))}
    return out


def select(counts: dict) -> list[str]:
    return [c for c in S.CANDIDATES if counts[c]["viability"] != "TOO_RARE"][:3]


def run(out: Path | None = OUT) -> dict:
    sha = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    log = PD.ReadLog()
    m = MD.load_train(log)
    vol, cov = PD.load_train(log)
    px = PD.load_train_prices(log)
    hr = S.build_hourly(m, px.close, vol)
    rep: dict = {"contract_sha256": sha, "venue": "Binance USD-M BTCUSDT (metrics, klines)",
                 "metrics_quality": m.quality, "kline_coverage": cov, "hours": hr.n}
    a = m.arr
    rep["distributions_5m"] = {
        "oi_btc": pct(a["oi"]), "oi_value_usdt": pct(a["oi_value"]), "G": pct(a["g"]), "TA": pct(a["ta"]),
        "TP": pct(a["tp"]), "taker_vol_ratio_redundant": pct(a["taker_ratio"]),
        **{f"{k}_pct": pct(S.oi_change(a["oi"], w) * 100) for k, w in S.OI_WINDOWS.items()}}
    rep["distributions_hourly"] = {f"{k}_pct": pct(v * 100) for k, v in hr.O.items()}
    st5 = {k: S.ratio_state(a[f]) for k, f in (("G", "g"), ("TA", "ta"), ("TP", "tp"))}
    rep["ratio_states_5m"] = {k: {"freq": state_freq(v), "runs": runs_hours(v, (1, -1), 12),
                                  "flips_per_day": flips_per_day(v, 288)} for k, v in st5.items()}
    rep["ratio_states_hourly"] = {k: {"freq": state_freq(v), "runs": runs_hours(v, (1, -1), 1),
                                      "flips_per_day": flips_per_day(v, 24)}
                                  for k, v in (("G", hr.G), ("TA", hr.TA), ("TP", hr.TP))}
    rep["price_dir_hourly"] = state_freq(hr.price, ((1, "UP"), (-1, "DOWN"), (0, "FLAT"), (NONE, "NONE")))
    rep["oi_dir_hourly"] = state_freq(hr.oi_dir, ((1, "UP"), (-1, "DOWN"), (0, "FLAT"), (NONE, "NONE")))
    rep["flow_hourly"] = state_freq(hr.flow, ((1, "BUY"), (-1, "SELL"), (0, "EVEN"), (NONE, "NONE")))
    reg = hr.regime
    rep["price_oi_regimes"] = {
        "freq": {r: float((reg == i).mean()) for i, r in enumerate(S.REGIMES)},
        "undefined": float((reg == -1).mean()),
        "runs": {r: runs_hours(reg, (i,), 1) for i, r in enumerate(S.REGIMES)},
        "onsets_per_day": {r: float(((reg == i) & np.concatenate(([False], reg[:-1] != i))).sum() / (hr.n / 24))
                           for i, r in enumerate(S.REGIMES)},
        "transition_A_B_C_D": transition(reg, 4),
        "symmetry_note": "A (up+OI up) vs C (down+OI up) and B vs D are the LONG/SHORT mirror pairs"}
    c8 = code8(hr)
    rep["g_ta_tp_8state"] = {
        "freq": {label8(c): float((c8 == c).mean()) for c in range(8)}, "undefined": float((c8 == -1).mean()),
        "runs": {label8(c): runs_hours(c8, (c,), 1) for c in range(8)},
        "transition": transition(c8, 8), "labels": [label8(c) for c in range(8)],
        "overlap_with_price_oi": {label8(c): {r: int(((c8 == c) & (reg == i)).sum()) for i, r in enumerate(S.REGIMES)}
                                  for c in range(8)}}
    both = lambda x, y: np.isin(x, (1, -1)) & np.isin(y, (1, -1))      # noqa: E731
    d1 = both(hr.G, hr.TP)
    d2 = both(hr.TA, hr.TP)
    d4 = np.isin(hr.flow, (1, -1)) & np.isin(hr.oi_dir, (1, -1))
    rep["divergences_hourly"] = {
        "D1_G_vs_TP": {"defined": int(d1.sum()), "disagree": int((d1 & (hr.G != hr.TP)).sum()),
                       "G_long_TP_short": int(((hr.G == 1) & (hr.TP == -1)).sum()),
                       "G_short_TP_long": int(((hr.G == -1) & (hr.TP == 1)).sum())},
        "D2_TA_vs_TP": {"defined": int(d2.sum()), "disagree": int((d2 & (hr.TA != hr.TP)).sum()),
                        "TA_long_TP_short": int(((hr.TA == 1) & (hr.TP == -1)).sum()),
                        "TA_short_TP_long": int(((hr.TA == -1) & (hr.TP == 1)).sum())},
        "D3_price_oi": {r: int((reg == i).sum()) for i, r in enumerate(S.REGIMES)},
        "D4_flow_vs_oi": {"defined": int(d4.sum()), "aligned_buy_up": int(((hr.flow == 1) & (hr.oi_dir == 1)).sum()),
                          "buy_oi_down": int(((hr.flow == 1) & (hr.oi_dir == -1)).sum()),
                          "sell_oi_up": int(((hr.flow == -1) & (hr.oi_dir == 1)).sum()),
                          "sell_oi_down": int(((hr.flow == -1) & (hr.oi_dir == -1)).sum())},
        "price_vs_flow_agree_share": float((hr.price[d4] == hr.flow[d4]).mean())}
    counts = candidate_counts(hr)
    rep["candidates"] = counts
    rep["selected"] = select(counts)
    rep["selection_rule"] = "menu order P1..P4, first three with 48 h overlap-adjusted N >= 100"
    rep["read_log"] = log.entries
    rep["contract_sha256_after"] = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    bad = forbidden_keys(rep)
    if bad:
        raise RuntimeError(f"outcome-like keys in Stage 0 output: {bad}")
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=1, sort_keys=True))
    return rep


def forbidden_keys(obj, path="") -> list[str]:
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k != "read_log" and any(tok in k.lower() for tok in FORBIDDEN):
                bad.append(path + k)
            if k != "read_log":
                bad += forbidden_keys(v, path + k + ".")
    elif isinstance(obj, list):
        for v in obj:
            bad += forbidden_keys(v, path)
    return bad


if __name__ == "__main__":
    r = run(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
    print(json.dumps({"quality": r["metrics_quality"], "candidates": r["candidates"], "selected": r["selected"]},
                     indent=1))
