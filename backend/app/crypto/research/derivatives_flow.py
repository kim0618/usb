"""D5.2 derivatives-flow edge study (cross-exchange, spot-perp, term structure) on Bybit BTCUSDT.

Frozen contract: docs/crypto/CRYPTO_D5_2_DERIVATIVES_FLOW_CONTRACT_V1.md (section numbers below refer to it).
Liquidations are FORWARD_ONLY and absent here by design.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.derivatives_flow qc     # data QC only (pre-freeze)
    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.derivatives_flow run    # study (post-freeze)

D5 / D5.1 modules are imported, never modified.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import dataset
from . import features as F
from . import long_horizon as LH
from .study import FEE_VERIFICATION, micro_costs
from app.crypto.paper.fees import load_scenarios

CONTRACT = Path("docs/crypto/CRYPTO_D5_2_DERIVATIVES_FLOW_CONTRACT_V1.md")
OUTDIR = Path("data/runtime/crypto/d5_2")
FREEZE = OUTDIR / "contract_freeze_v1.json"
ARCH = OUTDIR / "archive"
BIN = ARCH / "binance"

MIN = dataset.MINUTE_MS
FIVE = 5 * MIN
DAY_MS = 86_400_000
T0 = dataset.RESEARCH_START_MS          # 2021-02-01
T_END = dataset.RESEARCH_END_MS         # 2026-09-23 exclusive
HORIZONS = (15, 30, 60, 120, 240, 480)
PRIMARY = (15, 30, 60, 120, 240)
SIDES = ("LONG", "SHORT")
SCENARIOS = ("ZERO", "VIP0_FEE", "VIP0_BASE", "VIP0_STRESS")
ROLL_MIN_DAYS = 7
BOOT_SEED = 20260927

CONT = (("X1", "xbasis_bybit_vs_peers"), ("X2", "xlast_dispersion"), ("X3", "xmark_dispersion"),
        ("X4", "xbasis_dispersion"), ("X5", "xfunding_spread"), ("X6", "xoi_share_chg_1h"),
        ("S1", "spot_basis_bybit"), ("S2", "spot_basis_z24h"), ("S3", "spot_basis_chg_1h"),
        ("S4", "spot_basis_composite"), ("T1", "ts_near_ann"), ("T2", "ts_far_ann"), ("T3", "ts_slope"),
        ("T5", "ts_near_chg_24h"))
BINARY = (("T4", "ts_inverted"), ("C1", "dislocation_event"))


# ------------------------------------------------------------------ contract
def check_freeze() -> str:
    frozen = json.loads(FREEZE.read_text())["sha256"]
    now = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if now != frozen:
        raise SystemExit(f"contract hash mismatch: frozen {frozen} now {now}")
    return now


# ------------------------------------------------------------------ raw readers (section 2)
def norm_ms(t: np.ndarray) -> np.ndarray:
    """Binance spot switched to microsecond timestamps in 2025. Anything above 1e14 is us."""
    t = np.asarray(t, dtype=np.int64)
    return np.where(t > 100_000_000_000_000, t // 1000, t)


def _zip_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as z:
        name = z.namelist()[0]
        text = z.read(name).decode()
    rows = list(csv.reader(io.StringIO(text)))
    if rows and rows[0] and not re.match(r"^-?\d", rows[0][0].strip()):
        rows = rows[1:]                                   # newer files carry a header row
    return rows


def read_kline_dir(d: Path) -> tuple[np.ndarray, np.ndarray]:
    """All zips under d -> (open_time_ms, close), sorted, de-duplicated (last wins)."""
    ts, cl = [], []
    for p in sorted(d.glob("*.zip")):
        for r in _zip_rows(p):
            ts.append(int(r[0])); cl.append(float(r[4]))
    t = norm_ms(np.array(ts, dtype=np.int64))
    c = np.array(cl)
    o = np.argsort(t, kind="stable")
    t, c = t[o], c[o]
    keep = np.r_[t[1:] != t[:-1], True]
    return t[keep], c[keep]


def binance_series(kind: str) -> tuple[np.ndarray, np.ndarray]:
    sub = {"last": "klines/BTCUSDT", "mark": "markPriceKlines/BTCUSDT", "index": "indexPriceKlines/BTCUSDT"}[kind]
    ts, cl = [], []
    for freq in ("monthly", "daily"):
        d = BIN / f"futures/um/{freq}/{sub}/1m"
        if d.exists():
            t, c = read_kline_dir(d); ts.append(t); cl.append(c)
    return _merge(ts, cl)


def binance_spot() -> tuple[np.ndarray, np.ndarray]:
    ts, cl = [], []
    for freq in ("monthly", "daily"):
        t, c = read_kline_dir(BIN / f"spot/{freq}/klines/BTCUSDT/1m"); ts.append(t); cl.append(c)
    return _merge(ts, cl)


def _merge(ts, cl):
    t = np.concatenate(ts); c = np.concatenate(cl)
    o = np.argsort(t, kind="stable"); t, c = t[o], c[o]
    keep = np.r_[t[1:] != t[:-1], True]
    return t[keep], c[keep]


def binance_funding() -> tuple[np.ndarray, np.ndarray]:
    ts, fr = [], []
    for p in sorted((BIN / "futures/um/monthly/fundingRate/BTCUSDT").glob("*.zip")):
        for r in _zip_rows(p):
            ts.append(int(r[0])); fr.append(float(r[2]))
    return _merge([norm_ms(np.array(ts))], [np.array(fr)])


def binance_oi() -> tuple[np.ndarray, np.ndarray]:
    """metrics create_time 'YYYY-MM-DD HH:MM:SS' UTC, sum_open_interest (BTC)."""
    ts, oi = [], []
    for p in sorted((BIN / "futures/um/daily/metrics/BTCUSDT").glob("*.zip")):
        for r in _zip_rows(p):
            dt = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            ts.append(int(dt.timestamp() * 1000)); oi.append(float(r[2]) if r[2] else np.nan)
    return _merge([np.array(ts, dtype=np.int64)], [np.array(oi)])


def quarterly_contracts() -> dict[str, tuple[int, np.ndarray, np.ndarray]]:
    """COIN-M BTCUSD quarterlies (contract section 4). USDT-M quarterlies are SHORT_HISTORY for the far leg
    (both legs listed together only from 2023-H2), found in the pre-freeze QC."""
    out = {}
    for d in sorted((BIN / "futures/cm/monthly/klines").glob("BTCUSD_*")):
        code = d.name.split("_")[1]
        exp = int(datetime.strptime(code + "08", "%y%m%d%H").replace(tzinfo=timezone.utc).timestamp() * 1000)
        ts, cl = [], []
        for freq in ("monthly", "daily"):
            dd = BIN / f"futures/cm/{freq}/klines/{d.name}/1m"
            if dd.exists():
                t, c = read_kline_dir(dd); ts.append(t); cl.append(c)
        t, c = _merge(ts, cl)
        out[d.name] = (exp, t, c)
    return out


def cm_index() -> tuple[np.ndarray, np.ndarray]:
    ts, cl = [], []
    for freq in ("monthly", "daily"):
        t, c = read_kline_dir(BIN / f"futures/cm/{freq}/indexPriceKlines/BTCUSD/1m"); ts.append(t); cl.append(c)
    return _merge(ts, cl)


def okx_series(name: str) -> tuple[np.ndarray, np.ndarray]:
    """OKX 5m candles: ts = bar START ms. Returned keyed by bar END so they sit on the 5m boundary grid."""
    ts, cl = [], []
    with gzip.open(ARCH / "okx" / f"{name}_5m.jsonl.gz", "rt") as f:
        for line in f:
            r = json.loads(line)
            ts.append(int(r[0]) + FIVE); cl.append(float(r[4]))
    return _merge([np.array(ts, dtype=np.int64)], [np.array(cl)])


# ------------------------------------------------------------------ 5m grid (section 2)
def grid5() -> np.ndarray:
    return np.arange(T0 + FIVE, T_END + 1, FIVE, dtype=np.int64)          # boundaries T (bar END)


def close5_from_1m(t1: np.ndarray, c1: np.ndarray, B: np.ndarray) -> np.ndarray:
    """5m close at boundary T = close of the last 1m bar with open_time in [T-5m, T). NaN if none."""
    j = np.searchsorted(t1, B, side="left") - 1                          # last open_time < T
    ok = (j >= 0) & (t1[np.clip(j, 0, None)] >= B - FIVE)
    return np.where(ok, c1[np.clip(j, 0, None)], np.nan)


def on5(ts_end: np.ndarray, val: np.ndarray, B: np.ndarray) -> np.ndarray:
    idx = np.searchsorted(B, ts_end)
    out = np.full(len(B), np.nan)
    ok = (idx < len(B)) & (B[np.clip(idx, 0, len(B) - 1)] == ts_end)
    out[idx[ok]] = val[ok]
    return out


def carry_one(x: np.ndarray) -> np.ndarray:
    """Contract section 2: a missing 5m value may carry the previous bar once, never longer."""
    y = x.copy()
    miss = np.isnan(y)
    prev = np.r_[np.nan, x[:-1]]
    fill = miss & np.isfinite(prev)
    y[fill] = prev[fill]
    return y


def asof(src_ts: np.ndarray, src_val: np.ndarray, known_at: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Latest value whose known_at <= T."""
    o = np.argsort(known_at, kind="stable")
    k, v = known_at[o], src_val[o]
    j = np.searchsorted(k, B, side="right") - 1
    return np.where(j >= 0, v[np.clip(j, 0, None)], np.nan)


def load_inputs(g: dict) -> dict[str, np.ndarray]:
    """All venue series on the 5m boundary grid B (value known at T)."""
    B = grid5()
    x = {"B": B}
    t1 = g["ts"]
    for name, arr in (("bybit_last", g["close"]), ("bybit_mark", g["mark_close"]), ("bybit_index", g["index_close"])):
        x[name] = carry_one(close5_from_1m(t1, arr, B))
    for kind in ("last", "mark", "index"):
        t, c = binance_series(kind)
        x[f"binance_{kind}"] = carry_one(close5_from_1m(t, c, B))
    t, c = binance_spot()
    x["spot"] = carry_one(close5_from_1m(t, c, B))
    for name, key in (("okx_last", "okx_swap_last"), ("okx_mark", "okx_swap_mark"), ("okx_index", "okx_index")):
        t, c = okx_series(key)
        x[name] = carry_one(on5(t, c, B))
    # funding: known at settlement
    x["bybit_funding"] = asof(g["funding_ts"], g["funding_rate"], g["funding_ts"], B) if len(g["funding_ts"]) else np.full(len(B), np.nan)
    ft, fr = binance_funding()
    x["binance_funding"] = asof(ft, fr, ft, B)
    # OI: Bybit from the D5 grid (already PIT with +5m delay, sampled at the minute ending at T)
    j = np.searchsorted(t1, B - MIN)
    ok = (j < len(t1)) & (t1[np.clip(j, 0, len(t1) - 1)] == B - MIN)
    x["bybit_oi"] = np.where(ok, g["oi"][np.clip(j, 0, len(t1) - 1)], np.nan)
    ot, ov = binance_oi()
    x["binance_oi"] = asof(ot, ov, ot + FIVE, B)
    # term structure: near / far per contract section 4 roll rule
    q = quarterly_contracts()
    closes = {k: (exp, carry_one(close5_from_1m(t, c, B))) for k, (exp, t, c) in q.items()}
    near, far, near_exp, far_exp = roll_near_far(B, closes)
    t, c = cm_index()
    x["cm_index"] = carry_one(close5_from_1m(t, c, B))
    x.update({"fut_near": near, "fut_far": far, "fut_near_exp": near_exp, "fut_far_exp": far_exp})
    return x


def roll_near_far(B: np.ndarray, closes: dict[str, tuple[int, np.ndarray]]):
    """Section 4 roll: near = nearest expiry with expiry - T > 7 days, far = the next expiry.
    A contract without data at T gives NaN; the rule never skips to another contract."""
    exps = sorted((exp, k) for k, (exp, _) in closes.items())
    near = np.full(len(B), np.nan); far = np.full(len(B), np.nan)
    near_exp = np.full(len(B), np.nan); far_exp = np.full(len(B), np.nan)
    for i, (exp, k) in enumerate(exps):
        prev_exp = exps[i - 1][0] if i else -np.inf
        m = (exp - B > ROLL_MIN_DAYS * DAY_MS) & (prev_exp - B <= ROLL_MIN_DAYS * DAY_MS)
        near[m] = closes[k][1][m]; near_exp[m] = exp
        if i + 1 < len(exps):
            k2 = exps[i + 1][1]
            far[m] = closes[k2][1][m]; far_exp[m] = exps[i + 1][0]
    return near, far, near_exp, far_exp


def net_values(T: dict, side: str, taker: float, micro: dict) -> dict[str, np.ndarray]:
    """Section 6 cost scenarios (D5.1 formulas, no maker)."""
    gross = T["long"] if side == "LONG" else T["short"]
    fund = T["fund_long"] if side == "LONG" else -T["fund_long"]
    tk = taker * (1 + T["ratio"])
    return {"ZERO": gross, "VIP0_FEE": gross - tk - fund, "VIP0_BASE": gross - tk - micro["BASE"] - fund,
            "VIP0_STRESS": gross - tk - micro["STRESS"] - fund}


# ------------------------------------------------------------------ features (section 4)
def _d(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    out[k:] = x[k:] - x[:-k]
    return out


def _roll_z(x: np.ndarray, w: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    from numpy.lib.stride_tricks import sliding_window_view
    if len(x) < w:
        return out
    win = sliding_window_view(x, w)
    valid = np.isfinite(win).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        mu = np.nanmean(np.where(np.isfinite(win), win, np.nan), axis=1)
        sd = np.nanstd(np.where(np.isfinite(win), win, np.nan), axis=1)
        z = (x[w - 1:] - mu) / sd
    z[(valid < w * 0.5) | ~(sd > 0)] = np.nan
    out[w - 1:] = z
    return out


def features5(x: dict) -> dict[str, np.ndarray]:
    B = x["B"]
    lp = {v: np.log(x[f"{v}_last"]) for v in ("bybit", "binance", "okx")}
    lm = {v: np.log(x[f"{v}_mark"]) for v in ("bybit", "binance", "okx")}
    li = {v: np.log(x[f"{v}_index"]) for v in ("bybit", "binance", "okx")}
    f = {}
    f["xbasis_bybit_vs_peers"] = lp["bybit"] - (lp["binance"] + lp["okx"]) / 2
    f["xlast_dispersion"] = np.std(np.vstack([lp[v] for v in lp]), axis=0)
    f["xmark_dispersion"] = np.std(np.vstack([lm[v] for v in lm]), axis=0)
    f["xbasis_dispersion"] = np.std(np.vstack([lm[v] - li[v] for v in lm]), axis=0)
    f["xfunding_spread"] = x["bybit_funding"] - x["binance_funding"]
    with np.errstate(invalid="ignore", divide="ignore"):
        f["xoi_share_chg_1h"] = _d(np.log(x["bybit_oi"] / x["binance_oi"]), 12)
    ls = np.log(x["spot"])
    f["spot_basis_bybit"] = lp["bybit"] - ls
    f["spot_basis_z24h"] = _roll_z(f["spot_basis_bybit"], 288)
    f["spot_basis_chg_1h"] = _d(f["spot_basis_bybit"], 12)
    f["spot_basis_composite"] = np.mean(np.vstack([lp[v] - ls for v in lp]), axis=0)
    yr = 365 * DAY_MS
    with np.errstate(invalid="ignore", divide="ignore"):
        lci = np.log(x["cm_index"])
        f["ts_near_ann"] = (np.log(x["fut_near"]) - lci) * yr / (x["fut_near_exp"] - B)
        f["ts_far_ann"] = (np.log(x["fut_far"]) - lci) * yr / (x["fut_far_exp"] - B)
    f["ts_slope"] = f["ts_far_ann"] - f["ts_near_ann"]
    f["ts_near_chg_24h"] = _d(f["ts_near_ann"], 288)
    f["ts_inverted"] = np.where(np.isfinite(f["ts_slope"]), (f["ts_slope"] < 0).astype(float), np.nan)
    return f


def to_1m(f5: np.ndarray, B: np.ndarray, ts1: np.ndarray) -> np.ndarray:
    """Decision at the close of 1m bar t sees the 5m bar ending at floor((ts+60s)/5m)*5m."""
    T = ((ts1 + MIN) // FIVE) * FIVE
    i = (T - B[0]) // FIVE
    ok = (i >= 0) & (i < len(B))
    return np.where(ok, f5[np.clip(i, 0, len(B) - 1)], np.nan)


# ------------------------------------------------------------------ QC (pre-freeze, no features, no returns)
def qc() -> dict:
    g = dataset.load()
    x = load_inputs(g)
    B = x["B"]
    rep = {"grid_5m_bars": int(len(B)), "series": {}}
    for k, v in x.items():
        if k in ("B",) or k.endswith("_exp"):
            continue
        fin = np.isfinite(v)
        first = int(B[np.argmax(fin)]) if fin.any() else None
        rep["series"][k] = {"valid_share": float(fin.mean()), "first_valid": first,
                            "missing_after_first": int((~fin[np.argmax(fin):]).sum()) if fin.any() else None}
    # raw 1m gap/dup checks for Binance series (before carry)
    for kind in ("last", "mark", "index"):
        t, _ = binance_series(kind)
        w = t[(t >= T0) & (t < T_END)]
        rep[f"binance_{kind}_1m"] = {"rows": int(len(w)), "expected": int((T_END - T0) // MIN),
                                     "missing": int((T_END - T0) // MIN - len(w))}
    t, _ = binance_spot()
    w = t[(t >= T0) & (t < T_END)]
    rep["binance_spot_1m"] = {"rows": int(len(w)), "expected": int((T_END - T0) // MIN), "missing": int((T_END - T0) // MIN - len(w)),
                              "us_timestamp_files_normalized": True}
    q = quarterly_contracts()
    rep["quarterly"] = {k: {"expiry": v[0], "first": int(v[1][0]) if len(v[1]) else None, "last": int(v[1][-1]) if len(v[1]) else None,
                            "rows": int(len(v[1]))} for k, v in q.items()}
    for side in ("fut_near", "fut_far"):
        s = x[side]
        yrs = (np.datetime64("1970-01-01") + B.astype("timedelta64[ms]")).astype("datetime64[Y]").astype(int) + 1970
        rep[f"{side}_valid_by_year"] = {int(y): float(np.isfinite(s[yrs == y]).mean()) for y in np.unique(yrs)}
    (OUTDIR / "qc_v1.json").write_text(json.dumps(rep, indent=1))
    return rep


# ------------------------------------------------------------------ evaluation (sections 7-10), D5.1 logic
def summarize(cid, fcode, fname, k, h, side, S, WIN, MAE, MFE, cnt, med, regs, fold_days, oos_days, W_fold, W_oos, day_period):
    folds = {}
    for fo, days in fold_days.items():
        st = LH._stat(S, cnt, days, k, W_fold[fo])
        train_days = np.where((day_period >= 0) & (day_period < fo))[0]
        tn = cnt[train_days, k].sum()
        st["train_VIP0_BASE_mean"] = float(S["VIP0_BASE"][train_days, k].sum() / tn) if tn else None
        st["train_N"] = int(tn)
        st["selected"] = bool(st["train_VIP0_BASE_mean"] is not None and st["train_VIP0_BASE_mean"] > 0)
        folds[f"F{fo}"] = st
    oos = LH._stat(S, cnt, oos_days, k, W_oos)
    n_ = oos["N"]
    for sc in ("ZERO", "VIP0_BASE"):
        oos[sc]["median"] = med[sc][k]
        oos[sc]["win_rate"] = float(WIN[sc][oos_days, k].sum() / n_) if n_ else None
    oos["mae_mean"] = float(MAE[oos_days, k].sum() / n_) if n_ else None
    oos["mfe_mean"] = float(MFE[oos_days, k].sum() / n_) if n_ else None
    oos["N_eff"] = n_ / h
    gross = oos["ZERO"]["mean"]
    ratios = {}
    for sc in ("VIP0_FEE", "VIP0_BASE", "VIP0_STRESS"):
        net = oos[sc]["mean"]
        cost = None if gross is None or net is None else gross - net
        ratios[sc] = {"cost": cost, "ratio": None if cost is None else ("INF_COST_NONPOSITIVE" if cost <= 0 else gross / cost)}
    sel = [fo for fo in fold_days if folds[f"F{fo}"]["selected"]]
    wn = sum(cnt[fold_days[fo], k].sum() for fo in sel)
    wf = {"selected_folds": [f"F{fo}" for fo in sel], "N": int(wn),
          "VIP0_BASE_mean": float(sum(S["VIP0_BASE"][fold_days[fo], k].sum() for fo in sel) / wn) if wn else None,
          "ZERO_mean": float(sum(S["ZERO"][fold_days[fo], k].sum() for fo in sel) / wn) if wn else None}
    regimes = {}
    for axis, (labels, rc, rn, rg) in regs.items():
        regimes[axis] = {labels[j]: {"N": int(rc[k, j]), "net_sum": float(rn[k, j]),
                                     "net": float(rn[k, j] / rc[k, j]) if rc[k, j] else None,
                                     "gross": float(rg[k, j] / rc[k, j]) if rc[k, j] else None}
                         for j in range(len(labels))}
    return {"cell": cid, "feature": fcode, "feature_name": fname, "bucket": f"B{k+1}" if fname not in dict(BINARY).values() else "EVENT",
            "horizon_min": h, "side": side, "folds": folds, "oos": oos, "cost_ratio": ratios, "wf_selected": wf, "regimes": regimes}


def verdict(c):
    """D5.1 section 9 verbatim, with the primary horizon set of this contract."""
    folds = [f for f in c["folds"].values() if f["N"] >= 500 and f["days"] >= 20]
    nf = len(folds)
    o = c["oos"]
    g_ok = sum(f["ZERO"]["mean"] > 0 for f in folds)
    n_ok = sum(f["VIP0_BASE"]["mean"] > 0 for f in folds)
    s1 = nf > 0 and g_ok * 3 >= 2 * nf
    s2 = nf > 0 and n_ok * 2 >= nf
    ci = o["VIP0_BASE"].get("ci95")
    s3 = o["VIP0_BASE"]["mean"] is not None and o["VIP0_BASE"]["mean"] > 0 and ci is not None and ci[0] > 0
    wf = c["wf_selected"]
    s4 = len(wf["selected_folds"]) >= 3 and wf["VIP0_BASE_mean"] is not None and wf["VIP0_BASE_mean"] > 0
    r = c["cost_ratio"]["VIP0_BASE"]["ratio"]
    s5 = r == "INF_COST_NONPOSITIVE" or (r is not None and r >= 1.2)
    s6 = o["VIP0_STRESS"]["mean"] is not None and o["VIP0_STRESS"]["mean"] > 0
    s7 = o["N_eff"] >= 200 and nf >= 6
    s8, rr = LH._robust(c)
    fails = [nm for ok, nm in ((s1, f"S1_FOLD_DIR_{g_ok}/{nf}"), (s2, f"S2_FOLD_NET_{n_ok}/{nf}"), (s3, "S3_OOS_NET_CI"),
                               (s4, "S4_WF_SELECT"), (s5, "S5_RATIO"), (s6, "S6_STRESS"), (s7, "S7_SAMPLE"),
                               (s8, "S8_ROBUST")) if not ok] + rr
    gci = o["ZERO"].get("ci95")
    if all((s1, s2, s3, s4, s5, s6, s7, s8)):
        v = "SURVIVE"
    elif s3:
        v, fails = "WEAK", ["W1"] + fails
    elif gci and gci[0] > 0 and s1 and s7:
        v, fails = "WEAK", ["W2_COST_KILLED_GROSS_EDGE"] + fails
    else:
        v = "REJECT"
    if c["horizon_min"] not in PRIMARY and v != "REJECT":
        fails, v = [f"REFERENCE_ONLY({v})"] + fails, "REFERENCE_ONLY"
    return v, fails


def run() -> dict:
    sha = check_freeze()
    fees = load_scenarios(FEE_VERIFICATION)
    taker = float(fees["VIP_0"].taker_rate)
    micro = micro_costs()
    g = dataset.load()
    ts = g["ts"]
    n = len(ts)
    n_days = n // F.DAY_BARS
    day = (ts - ts[0]) // DAY_MS
    bounds = np.array([LH.ms(LH.SAMPLE_START)] + [LH.ms(d) for d in LH.FOLD_STARTS] + [LH.ms(LH.END)])
    period = np.searchsorted(bounds, ts, side="right") - 1
    next_bound = bounds[np.minimum(period + 1, len(bounds) - 1)]
    day_period = period[:: F.DAY_BARS][:n_days]

    x = load_inputs(g)
    f5 = features5(x)
    feats = {name: to_1m(f5[name], x["B"], ts) for name in f5}
    f1_train = (ts >= bounds[0]) & (ts < bounds[1])
    reg = F.regime_labels(g, f1_train)
    buckets = {name: F.bucketize(feats[name], n_days) for _, name in CONT}
    # C1 (section 4): S1 bucket B1 and Bybit 1h OI log change < 0 and vol regime HIGH
    with np.errstate(invalid="ignore", divide="ignore"):
        oi_chg = np.log(g["oi"] / F._lag(g["oi"], 60))
    c1 = (buckets["spot_basis_bybit"] == 0) & (oi_chg < 0) & (reg["vol"] == 0)
    buckets["dislocation_event"] = np.where(c1, 0, -1).astype(np.int8)
    ti = feats["ts_inverted"]
    buckets["ts_inverted"] = np.where(ti == 1, 0, -1).astype(np.int8)
    nb_of = {name: 5 for _, name in CONT} | {name: 1 for _, name in BINARY}

    rng = np.random.default_rng(BOOT_SEED)
    fold_days = {k: np.where(day_period == k)[0] for k in range(1, 10)}
    oos_days = np.where((day_period >= 1) & (day_period <= 9))[0]
    W_fold = {k: LH._weights(len(d), rng) for k, d in fold_days.items()}
    W_oos = LH._weights(len(oos_days), rng)
    AX = LH.AXES

    cells: dict[str, dict] = {}
    coverage = {name: {"bucketed_share_oos": float((buckets[name][(period >= 1) & (period <= 9)] >= 0).mean())} for name in buckets}
    for h in HORIZONS:
        T = F.targets(g, h)
        exit_ts = np.full(n, np.iinfo(np.int64).max)
        exit_ts[: n - 1 - h] = ts[1 + h:]
        base = (period >= 0) & (period <= 9) & (exit_ts < next_bound) & np.isfinite(T["ratio"])
        vals = {(side, sc): v for side in SIDES for sc, v in net_values(T, side, taker, micro).items()}
        for fcode, fname in CONT + BINARY:
            NB = nb_of[fname]
            b = buckets[fname]
            sel = base & (b >= 0)
            key = day[sel] * NB + b[sel]
            size = n_days * NB
            cnt = np.bincount(key, minlength=size).reshape(n_days, NB).astype(float)
            oos_sel = sel & (period >= 1)
            bo = b[oos_sel]
            for side in SIDES:
                sd = side.lower()
                S = {sc: np.bincount(key, weights=vals[(side, sc)][sel], minlength=size).reshape(n_days, NB) for sc in SCENARIOS}
                WIN = {sc: np.bincount(key, weights=(vals[(side, sc)][sel] > 0).astype(float), minlength=size).reshape(n_days, NB)
                       for sc in ("ZERO", "VIP0_BASE")}
                MAE = np.bincount(key, weights=T[f"mae_{sd}"][sel], minlength=size).reshape(n_days, NB)
                MFE = np.bincount(key, weights=T[f"mfe_{sd}"][sel], minlength=size).reshape(n_days, NB)
                med = {sc: [float(np.median(vals[(side, sc)][oos_sel][bo == k])) if (bo == k).any() else None for k in range(NB)]
                       for sc in ("ZERO", "VIP0_BASE")}
                regs = {}
                for axis, labels in AX.items():
                    lab = reg[axis]
                    ok = oos_sel & (lab >= 0)
                    L = len(labels)
                    rk = b[ok].astype(np.int64) * L + lab[ok]
                    rc = np.bincount(rk, minlength=NB * L).reshape(NB, L)
                    rn = np.bincount(rk, weights=vals[(side, "VIP0_BASE")][ok], minlength=NB * L).reshape(NB, L)
                    rg = np.bincount(rk, weights=vals[(side, "ZERO")][ok], minlength=NB * L).reshape(NB, L)
                    regs[axis] = (labels, rc, rn, rg)
                for k in range(NB):
                    lab = f"B{k+1}" if NB > 1 else "EVENT"
                    cid = f"{fcode}-{lab}-{h}m-{side}"
                    cells[cid] = summarize(cid, fcode, fname, k, h, side, S, WIN, MAE, MFE, cnt, med, regs,
                                           fold_days, oos_days, W_fold, W_oos, day_period)
        del T, vals
    for c in cells.values():
        c["verdict"], c["reasons"] = verdict(c)
    prim = [c for c in cells.values() if c["horizon_min"] in PRIMARY]
    survive = [c["cell"] for c in prim if c["verdict"] == "SURVIVE"]
    gate = {"case": "A" if survive else "C", "survive": survive}
    # section 11 repackaging check on SURVIVE / WEAK features
    flagged = sorted({c["feature_name"] for c in cells.values() if c["verdict"] in ("SURVIVE", "WEAK")})
    ref = {"G1_basis_mark_index": np.log(g["mark_close"] / g["index_close"]),
           "E3_fund_premium": F.compute_features(g)["fund_premium"]}
    oos_idx = np.where((period >= 1) & (period <= 9))[0][::60]
    rep = {}
    for name in flagged:
        a = feats[name][oos_idx] if name in feats else None
        if a is None:
            continue
        rep[name] = {}
        for rn_, r_ in ref.items():
            bvals = r_[oos_idx]
            m = np.isfinite(a) & np.isfinite(bvals)
            if m.sum() > 100:
                ra = np.argsort(np.argsort(a[m])); rb = np.argsort(np.argsort(bvals[m]))
                rho = float(np.corrcoef(ra, rb)[0, 1])
                rep[name][rn_] = {"spearman": rho, "flag": abs(rho) >= 0.7}
    out = {"contract_sha256": sha, "taker": taker, "micro": micro, "vol_cutoffs_f1_train": reg["vol_cutoffs"].tolist(),
           "coverage": coverage, "gate": gate, "repackaging": rep, "cells": cells}
    (OUTDIR / "cells_v1.json").write_text(json.dumps(out))
    return out


if __name__ == "__main__":
    import time
    from collections import Counter
    t0 = time.time()
    if sys.argv[1:] == ["qc"]:
        print(json.dumps(qc(), indent=1)[:6000])
    else:
        r = run()
        print(Counter(c["verdict"] for c in r["cells"].values()), r["gate"], f"{time.time()-t0:.0f}s")
