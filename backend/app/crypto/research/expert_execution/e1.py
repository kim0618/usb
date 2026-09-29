"""E1: maker adverse selection / execution edge on XBTUSD, per the frozen contract
docs/crypto/expert_execution/AOA_E1_MAKER_ADVERSE_SELECTION_CONTRACT_V1.md.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.expert_execution.e1

Requires market.py output under data/research/expert_execution/e1/market/. Reads the E0 normalized
ledger read-only. Section numbers in comments refer to the contract.
"""
from __future__ import annotations

import hashlib
import json
import resource
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import market as M

CONTRACT = Path("docs/crypto/expert_execution/AOA_E1_MAKER_ADVERSE_SELECTION_CONTRACT_V1.md")
FREEZE = M.E1 / "contract_freeze_v1.json"
NORM = Path("data/research/expert_execution/aoa_normalized")
BYBIT_FEE_JSON = Path("data/runtime/crypto/reference/fee_source_verification_v1.json")

HORIZONS_S = (1, 5, 15, 30, 60, 300, 900)          # 6
PRIMARY_H = 60
REGIME_H = (5, 60, 300)
STALE_NS = 60 * 10**9                              # 4
TICK = 0.5
MIN_NS = 60 * 10**9
SEC_NS = 10**9
DAY_NS = 86_400 * 10**9
BOOT_B, SEED = 2000, 20260927                      # 9
PRE_WINDOW_NS = 62 * MIN_NS                        # 60 completed minutes + slack
POST_WINDOW_NS = 900 * SEC_NS + STALE_NS + MIN_NS


# ------------------------------------------------------------------ contract
def verify_contract() -> str:
    frozen = json.loads(FREEZE.read_text())["sha256"]
    now = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if now != frozen:
        raise SystemExit(f"contract hash mismatch: frozen {frozen} now {now}")
    return now


def bybit_rates() -> dict:
    j = json.loads(BYBIT_FEE_JSON.read_text())
    a = j["adopted"]
    return {"maker": float(a["maker_rate"]), "taker": float(a["taker_rate"]), "vip_level": a["vip_level"],
            "source": j["url"], "html_sha256": j["html_sha256"], "page_last_updated": j["page_last_updated"]}


# ------------------------------------------------------------------ ledger (2, 3, 7)
def action_group(a: str) -> str:
    for p in ("OPEN", "ADD", "REDUCE", "CLOSE", "REVERSE"):
        if a.startswith(p):
            return p
    return "OTHER"


def role_of(liq: pd.Series) -> pd.Series:
    return liq.map({"AddedLiquidity": "MAKER", "RemovedLiquidity": "TAKER"}).fillna("UNKNOWN")


def load_fills() -> tuple[pd.DataFrame, dict]:
    ex = pd.read_parquet(NORM / "executions.parquet", columns=[
        "seq", "orderid", "symbol", "exectype", "text", "transact_ts", "side_sign", "lastqty", "lastpx",
        "commission", "execcost", "lastliquidityind"])
    pos = pd.read_parquet(NORM / "positions.parquet", columns=["seq", "action"])
    ex = ex[(ex.symbol == M.SYMBOL) & (ex.exectype == "Trade")]
    n_all = len(ex)
    liq = ex.text == "Liquidation"
    f = ex[~liq].merge(pos, on="seq")
    f["t"] = f.transact_ts.values.astype("datetime64[ns]").astype(np.int64)
    f["role"] = role_of(f.lastliquidityind)
    f["act"] = f.action.map(action_group)
    f["value_xbt"] = f.execcost.abs().astype(float) / 1e8
    f["fee_bp"] = -f.commission * 1e4
    first_day = f.groupby("orderid").t.transform("min") // DAY_NS
    f["cluster"] = first_day.astype(np.int64)       # 8: order's first-fill UTC day
    f["unit"] = f.orderid + "|" + f.role
    cols = ["seq", "orderid", "unit", "cluster", "t", "side_sign", "lastqty", "lastpx", "commission", "fee_bp",
            "value_xbt", "role", "act"]
    return f[cols].sort_values("t", kind="stable").reset_index(drop=True), {
        "xbtusd_trade_fills": int(n_all), "liquidation_excluded": int(liq.sum()),
        "unknown_role": int((f.role == "UNKNOWN").sum())}


# ------------------------------------------------------------------ market window
def load_day(kind: str, d: str) -> pd.DataFrame | None:
    p = M.MARKET / kind / f"{d}.parquet"
    return pd.read_parquet(p) if p.exists() else None


def window(frames: list[pd.DataFrame | None], lo: int, hi: int) -> pd.DataFrame:
    parts = [x[(x.ts >= lo) & (x.ts < hi)] for x in frames if x is not None]
    if not parts:
        return pd.DataFrame({"ts": np.array([], np.int64)})
    w = pd.concat(parts, ignore_index=True)
    return w.sort_values("ts", kind="stable").reset_index(drop=True)


# ------------------------------------------------------------------ fill metrics (4, 5, 10)
def quote_at(qts: np.ndarray, bid: np.ndarray, ask: np.ndarray, target: np.ndarray, strict: bool):
    """Last quote with ts < target (strict) or ts <= target. Returns (mid, bid, ask, idx, valid)."""
    idx = np.searchsorted(qts, target, side="left" if strict else "right") - 1
    ok = idx >= 0
    j = np.clip(idx, 0, None)
    if len(qts) == 0:
        nan = np.full(len(target), np.nan)
        return nan, nan, nan, idx, np.zeros(len(target), bool)
    b, a = bid[j], ask[j]
    age = target - qts[j]
    valid = ok & (age <= STALE_NS) & (b > 0) & (a > 0) & (b < a)
    mid = np.where(valid, (b + a) / 2, np.nan)
    return mid, np.where(valid, b, np.nan), np.where(valid, a, np.nan), idx, valid


def fill_metrics(f: pd.DataFrame, q: pd.DataFrame, tr: pd.DataFrame) -> pd.DataFrame:
    """f: fills (t ns, side_sign, lastpx, fee_bp, ...). q: quotes ts,bid,ask,bid_size,ask_size sorted.
    tr: trades ts sorted. Only quotes/trades strictly before t feed the reference and regime features."""
    out = f.copy()
    t = f.t.to_numpy()
    s = f.side_sign.to_numpy().astype(float)
    px = f.lastpx.to_numpy()
    qts = q.ts.to_numpy() if len(q) else np.array([], np.int64)
    bid = q.bid.to_numpy() if len(q) else np.array([])
    ask = q.ask.to_numpy() if len(q) else np.array([])
    mid0, b0, a0, i0, v0 = quote_at(qts, bid, ask, t, strict=True)
    j0 = np.clip(i0, 0, None)
    out["valid0"] = v0
    out["quote_age0_s"] = np.where(i0 >= 0, (t - qts[j0]) / 1e9 if len(qts) else np.nan, np.nan)
    out["mid0"], out["bid0"], out["ask0"] = mid0, b0, a0
    bsz = q.bid_size.to_numpy() if len(q) else np.array([])
    asz = q.ask_size.to_numpy() if len(q) else np.array([])
    out["bid_size0"] = np.where(v0, bsz[j0] if len(qts) else np.nan, np.nan)
    out["ask_size0"] = np.where(v0, asz[j0] if len(qts) else np.nan, np.nan)
    out["spread_ticks"] = (a0 - b0) / TICK
    out["spread_raw"] = s * (mid0 - px)
    out["spread_bp"] = out.spread_raw / mid0 * 1e4
    for h in HORIZONS_S:
        mh, _, _, _, vh = quote_at(qts, bid, ask, t + h * SEC_NS, strict=False)
        mh = np.where(v0 & vh, mh, np.nan)
        out[f"mid_{h}s"] = mh
        out[f"move_raw_{h}s"] = s * (mh - mid0)
        out[f"move_bp_{h}s"] = out[f"move_raw_{h}s"] / mid0 * 1e4
        out[f"svf_raw_{h}s"] = s * (mh - px)                       # signed move vs fill price (D)
        out[f"svf_bp_{h}s"] = out[f"svf_raw_{h}s"] / mid0 * 1e4
        out[f"net_bp_{h}s"] = out.fee_bp + out.spread_bp + out[f"move_bp_{h}s"]
    # regime features: 60 completed minutes before t
    floor = (t // MIN_NS) * MIN_NS
    bounds = floor[:, None] - np.arange(61)[None, :] * MIN_NS           # b0 (latest) ... b60
    mb, _, _, _, vb = quote_at(qts, bid, ask, bounds.ravel(), strict=True)
    mb = np.where(vb, mb, np.nan).reshape(bounds.shape)
    lr = np.log(mb[:, :-1] / mb[:, 1:])                                  # 60 one-minute returns
    full = np.isfinite(lr).all(axis=1)
    vol = np.where(full, np.std(np.where(full[:, None], lr, 0.0), axis=1, ddof=1), np.nan)
    ret60 = np.where(full, np.log(mb[:, 0] / mb[:, 60]), np.nan)
    out["vol60"] = vol
    out["ret60"] = ret60
    with np.errstate(divide="ignore", invalid="ignore"):
        z = ret60 / (vol * np.sqrt(60))
    out["trend_z"] = z
    tts = tr.ts.to_numpy() if len(tr) else np.array([], np.int64)
    out["trades_60s"] = np.searchsorted(tts, t, "left") - np.searchsorted(tts, t - 60 * SEC_NS, "left")
    own = np.where(s > 0, out.bid_size0, out.ask_size0)       # maker rests on its own side
    opp = np.where(s > 0, out.ask_size0, out.bid_size0)       # taker consumes the other side
    disp = np.where(f.role.to_numpy() == "MAKER", own, opp)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["depth_ratio"] = f.lastqty.to_numpy() / disp
    out["hour_bucket"] = ((t // (3600 * SEC_NS)) % 24) // 4
    out["year"] = pd.to_datetime(t, utc=True).year
    return out


def run_metrics(fills: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Day loop: each fill day sees quotes/trades from [day - 62m, day + 1d + 17m)."""
    fills = fills.copy()
    fills["day"] = pd.to_datetime(fills.t, utc=True).dt.strftime("%Y%m%d")
    res, cache = [], {}

    def get(kind, d):
        k = (kind, d)
        if k not in cache:
            cache[k] = load_day(kind, d)
        return cache[k]

    stats = {"days": 0, "days_missing_quote_file": 0}
    for d, g in fills.groupby("day", sort=True):
        dd = datetime.strptime(d, "%Y%m%d")
        prv, nxt = (dd - timedelta(1)).strftime("%Y%m%d"), (dd + timedelta(1)).strftime("%Y%m%d")
        for key in list(cache):
            if key[1] < prv:
                del cache[key]
        lo = int(pd.Timestamp(d, tz="UTC").value) - PRE_WINDOW_NS
        hi = int(pd.Timestamp(d, tz="UTC").value) + DAY_NS + POST_WINDOW_NS
        if get("quote", d) is None:
            stats["days_missing_quote_file"] += 1
        q = window([get("quote", prv), get("quote", d), get("quote", nxt)], lo, hi)
        tr = window([get("trade", prv), get("trade", d)], lo, hi)
        res.append(fill_metrics(g.drop(columns="day"), q, tr))
        stats["days"] += 1
    return pd.concat(res, ignore_index=True), stats


# ------------------------------------------------------------------ statistics (8, 9)
def cluster_boot(x: np.ndarray, w: np.ndarray, cl: np.ndarray, b: int = BOOT_B, seed: int = SEED) -> tuple[float, float, float, float]:
    """Ratio estimator sum(w x)/sum(w) with a day-cluster bootstrap. Returns (est, se, lo, hi)."""
    m = np.isfinite(x) & np.isfinite(w)
    x, w, cl = x[m], w[m], cl[m]
    if len(x) == 0 or w.sum() == 0:
        return (np.nan,) * 4
    est = float((w * x).sum() / w.sum())
    u, inv = np.unique(cl, return_inverse=True)
    swx = np.bincount(inv, weights=w * x)
    sw = np.bincount(inv, weights=w)
    rng = np.random.default_rng(seed)
    cnt = rng.multinomial(len(u), np.full(len(u), 1 / len(u)), size=b)
    with np.errstate(invalid="ignore", divide="ignore"):
        bs = (cnt @ swx) / (cnt @ sw)
    bs = bs[np.isfinite(bs)]
    return est, float(bs.std(ddof=1)), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


def describe(g: pd.DataFrame, col: str, with_rates: bool = True) -> dict:
    """g needs value_xbt, cluster, unit_id (int codes). Fill level: value-weighted ratio estimator.
    Order level: each order x role unit is its value-weighted mean, then units are equal-weighted."""
    x = g[col].to_numpy(float)
    ok = np.isfinite(x)
    w = g.value_xbt.to_numpy(float)
    cl = g.cluster.to_numpy()
    est, se, lo, hi = cluster_boot(x, w, cl)
    uid = g.unit_id.to_numpy()[ok]
    uu, inv = np.unique(uid, return_inverse=True)
    uw = np.bincount(inv, weights=w[ok])
    ux = np.bincount(inv, weights=(w * np.where(ok, x, 0))[ok]) / uw
    ucl = np.zeros(len(uu), np.int64)
    ucl[inv] = cl[ok]
    oe, ose, olo, ohi = cluster_boot(ux, np.ones(len(uu)), ucl)
    d = {"n_fills": int(ok.sum()), "n_orders": int(len(uu)), "value_xbt": float(w[ok].sum()),
         "vw_mean": est, "vw_se": se, "vw_ci95": [lo, hi],
         "mean": float(x[ok].mean()) if ok.any() else None,
         "median": float(np.median(x[ok])) if ok.any() else None,
         "p25": float(np.percentile(x[ok], 25)) if ok.any() else None,
         "p75": float(np.percentile(x[ok], 75)) if ok.any() else None,
         "order_mean": oe, "order_se": ose, "order_ci95": [olo, ohi]}
    if with_rates and ok.any():
        d.update({"favorable_rate": float((x[ok] > 0).mean()), "adverse_rate": float((x[ok] < 0).mean()),
                  "zero_rate": float((x[ok] == 0).mean())})
    return d


def add_buckets(m: pd.DataFrame) -> pd.DataFrame:
    a = m[m.valid0 & (m.role != "UNKNOWN")].copy()
    a["unit_id"] = pd.factorize(a.unit)[0]

    def terc(col, labels=("LOW", "MID", "HIGH")):
        q1, q2 = np.nanpercentile(a[col], [100 / 3, 200 / 3])
        return pd.Series(np.where(a[col].isna(), "NA", np.where(a[col] <= q1, labels[0],
                                                               np.where(a[col] <= q2, labels[1], labels[2]))),
                         index=a.index), [float(q1), float(q2)]
    a["vol_bucket"], vq = terc("vol60")
    a["intensity_bucket"], iq = terc("trades_60s")
    a["size_bucket"], sq = terc("value_xbt", ("SMALL", "MEDIUM", "LARGE"))
    ou = a.groupby("unit").value_xbt.transform("sum")
    q1, q2 = np.percentile(a.groupby("unit").value_xbt.sum(), [100 / 3, 200 / 3])
    a["order_size_bucket"] = np.where(ou <= q1, "SMALL", np.where(ou <= q2, "MEDIUM", "LARGE"))
    z = a.trend_z
    a["trend"] = np.where(z.isna(), "NA", np.where(z > 1, "UP", np.where(z < -1, "DOWN", "SIDEWAYS")))
    sz = z * a.side_sign
    a["trend_rel"] = np.where(z.isna(), "NA", np.where(sz > 1, "WITH", np.where(sz < -1, "AGAINST", "NEUTRAL")))
    a["spread_bucket"] = np.where(a.spread_ticks <= 1.0 + 1e-9, "1_TICK", "2PLUS_TICKS")
    dr = a.depth_ratio
    a["depth_bucket"] = np.where(~np.isfinite(dr), "NA", np.where(dr < 0.1, "<0.1", np.where(dr < 1, "0.1-1", ">=1")))
    a["tod"] = (a.hour_bucket * 4).astype(int).map(lambda h: f"{h:02d}-{h + 4:02d}UTC")
    a.attrs["thresholds"] = {"vol60_terciles": vq, "trades_60s_terciles": iq, "fill_value_xbt_terciles": sq,
                             "order_value_xbt_terciles": [float(q1), float(q2)]}
    return a


# ------------------------------------------------------------------ main
def run() -> dict:
    t_start = time.time()
    sha = verify_contract()
    bb = bybit_rates()
    fills, fstat = load_fills()
    m, dstat = run_metrics(fills)
    m.to_parquet(M.E1 / "maker_taker_metrics.parquet", index=False, compression="zstd")
    a = add_buckets(m)
    for role, r in (("MAKER", bb["maker"]), ("TAKER", bb["taker"])):
        sel = a.role == role
        for h in HORIZONS_S:
            a.loc[sel, f"cfa_bp_{h}s"] = -r * 1e4 + a.loc[sel, "spread_bp"] + a.loc[sel, f"move_bp_{h}s"]
            a.loc[sel, f"cfb_bp_{h}s"] = -r * 1e4 + a.loc[sel, f"move_bp_{h}s"]

    summ = {"contract_sha256": sha, "bybit_reference": bb, "fills": fstat, "days": dstat,
            "coverage": {}, "by_role": {}, "by_role_action": {}, "fee_schedule": {}, "verdict": {}}
    cov = summ["coverage"]
    cov["analysis_fills_after_liq_exclusion"] = int(len(m))
    cov["invalid_reference_quote"] = int((~m.valid0).sum())
    cov["valid_reference"] = int(m.valid0.sum())
    cov["valid_by_horizon"] = {f"{h}s": int(m[f"move_bp_{h}s"].notna().sum()) for h in HORIZONS_S}
    cov["reference_quote_age_s"] = {p: float(np.nanpercentile(m.quote_age0_s, p)) for p in (50, 90, 99, 100)}
    cov["fill_inside_quote_share"] = float(((m.lastpx >= m.bid0) & (m.lastpx <= m.ask0))[m.valid0].mean())
    cov["bucket_thresholds"] = a.attrs["thresholds"]
    cov["role_counts"] = a.role.value_counts().to_dict()

    cols = ["spread_bp", "fee_bp"] + [f"{p}_{h}s" for p in ("move_bp", "svf_bp", "net_bp", "cfa_bp", "cfb_bp")
                                      for h in HORIZONS_S]
    rows = []
    for role, g in a.groupby("role"):
        summ["by_role"][role] = {c: describe(g, c, with_rates=c.startswith(("move", "net", "svf", "cf"))) for c in cols}
        for c in cols:
            rows.append({"role": role, "slice": "ALL", "bucket": "ALL", "metric": c, **_flat(summ["by_role"][role][c])})
        for act, ga in g.groupby("act"):
            summ["by_role_action"][f"{role}|{act}"] = {c: describe(ga, c) for c in
                                                       ["spread_bp", "fee_bp", f"move_bp_{PRIMARY_H}s", f"net_bp_{PRIMARY_H}s",
                                                        "move_bp_5s", "net_bp_5s", "move_bp_300s", "net_bp_300s"]}
    pd.DataFrame(rows).to_parquet(M.E1 / "maker_taker_summary.parquet", index=False)

    # regimes (10) at REGIME_H
    rrows = []
    slices = {"year": "year", "vol": "vol_bucket", "trend": "trend", "trend_rel": "trend_rel",
              "spread": "spread_bucket", "intensity": "intensity_bucket", "tod": "tod",
              "fill_size": "size_bucket", "order_size": "order_size_bucket", "depth": "depth_bucket", "action": "act"}
    for role, g in a.groupby("role"):
        for sname, col in slices.items():
            for b, gb in g.groupby(col):
                for h in REGIME_H:
                    for met in (f"move_bp_{h}s", f"net_bp_{h}s", f"cfb_bp_{h}s"):
                        rrows.append({"role": role, "slice": sname, "bucket": str(b), "horizon_s": h,
                                      "metric": met.rsplit("_", 1)[0], **_flat(describe(gb, met, with_rates=True))})
                rrows.append({"role": role, "slice": sname, "bucket": str(b), "horizon_s": 0, "metric": "spread_bp",
                              **_flat(describe(gb, "spread_bp", with_rates=False))})
                rrows.append({"role": role, "slice": sname, "bucket": str(b), "horizon_s": 0, "metric": "fee_bp",
                              **_flat(describe(gb, "fee_bp", with_rates=False))})
    reg = pd.DataFrame(rrows)
    reg.to_parquet(M.E1 / "regime_metrics.parquet", index=False)

    # order-level table (8)
    ol = a.groupby("unit").apply(lambda h: pd.Series({
        "orderid": h.orderid.iloc[0], "role": h.role.iloc[0], "act_first": h.act.iloc[0], "year": h.year.iloc[0],
        "cluster": h.cluster.iloc[0], "fills": len(h), "value_xbt": h.value_xbt.sum(),
        **{f"{c}": np.average(h[c].fillna(0), weights=h.value_xbt * h[c].notna()) if h[c].notna().any() else np.nan
           for c in ["spread_bp", "fee_bp"] + [f"move_bp_{x}s" for x in HORIZONS_S] + [f"net_bp_{x}s" for x in HORIZONS_S]}}),
        include_groups=False).reset_index()
    ol.to_parquet(M.E1 / "order_level_metrics.parquet", index=False)

    # fee schedule actually charged (11.1)
    a["ym"] = pd.to_datetime(a.t, utc=True).dt.strftime("%Y-%m")
    fs = a.groupby(["role", "ym"]).commission.agg(lambda c: c.round(7).mode().iloc[0])
    summ["fee_schedule"] = {f"{r}|{ym}": float(v) for (r, ym), v in fs.items()}

    # verdict (12)
    mk = a[a.role == "MAKER"]
    c = f"net_bp_{PRIMARY_H}s"
    d_all = summ["by_role"]["MAKER"][c]
    yr = reg[(reg.role == "MAKER") & (reg.slice == "year") & (reg.horizon_s == PRIMARY_H) & (reg.metric == "net_bp")]
    vo = reg[(reg.role == "MAKER") & (reg.slice == "vol") & (reg.horizon_s == PRIMARY_H) & (reg.metric == "net_bp")
             & reg.bucket.isin(["LOW", "MID", "HIGH"])]
    C1 = d_all["vw_mean"] > 0 and d_all["vw_ci95"][0] > 0
    C2 = int((yr.vw_mean > 0).sum()) >= 3
    C3 = d_all["order_mean"] > 0
    C4 = len(vo) == 3 and bool((vo.vw_mean > 0).all())
    if C1 and C2 and C3 and C4:
        hv = "MAKER_RESEARCH_WORTH_CONTINUING"
    elif d_all["vw_mean"] <= 0 and d_all["order_mean"] <= 0:
        hv = "NOT_SUPPORTED"
    else:
        hv = "MIXED"
    cb = summ["by_role"]["MAKER"][f"cfb_bp_{PRIMARY_H}s"]
    bv = ("BYBIT_CF_POSITIVE" if cb["vw_mean"] > 0 and cb["vw_ci95"][0] > 0 else
          "BYBIT_CF_NEGATIVE" if cb["vw_mean"] <= 0 else "BYBIT_CF_INCONCLUSIVE")
    summ["verdict"] = {"historical": hv, "conditions": {"C1": C1, "C2": C2, "C3": C3, "C4": C4},
                       "C2_years_positive": {str(k): v for k, v in zip(yr.bucket, yr.vw_mean)},
                       "C4_vol_terciles": {k: v for k, v in zip(vo.bucket, vo.vw_mean)},
                       "bybit": bv, "maker_net_60s": d_all, "maker_cfb_60s": cb}
    summ["runtime"] = {"seconds": round(time.time() - t_start, 1),
                       "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}
    (M.E1 / "e1_summary.json").write_text(json.dumps(summ, indent=1, default=_jd))
    return summ


def _flat(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, list):
            out[f"{k}_lo"], out[f"{k}_hi"] = v
        else:
            out[k] = v
    return out


def _jd(o):
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)): return None if np.isnan(o) else float(o)
    if isinstance(o, np.bool_): return bool(o)
    raise TypeError(type(o))


if __name__ == "__main__":
    s = run()
    print(json.dumps(s["verdict"], indent=1, default=_jd))
