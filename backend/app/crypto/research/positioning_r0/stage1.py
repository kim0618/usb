"""Positioning R0 Stage 1: TRAIN outcomes of the frozen candidates (contract sections 6 to 9).

Run from backend/: python -m app.crypto.research.positioning_r0.stage1 [out.json]
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

from ..ptm_r1 import data as PD
from ..ptm_r1 import r1_train as R
from . import data as MD
from . import stage0 as S0
from . import states as S

OUT = PD.ROOT / "data/research/crypto/positioning_r0/stage1_v1.json"
STAGE0 = S0.OUT
HORIZONS = {"15m": 15, "1h": 60, "4h": 240, "12h": 720, "24h": 1440}
PRIMARY = ("1h", "4h")
N_RANDOM = 200
K_MATCH = 20
SAFETY_10X = 0.40 * (1 / 10 - R.MMR)                 # 3.84 %
DIR = {1: "LONG", -1: "SHORT"}


def frozen_from_contract() -> list[str]:
    m = re.search(r"^FROZEN: (.+)$", S0.CONTRACT.read_text(), re.M)
    if not m:
        raise RuntimeError("contract has no CANDIDATE FREEZE line")
    return [c.strip() for c in m.group(1).split(",")]


def check_freeze() -> list[str]:
    frozen = frozen_from_contract()
    st0 = json.loads(STAGE0.read_text())
    if st0["selected"] != frozen:
        raise RuntimeError(f"freeze mismatch: contract {frozen} vs stage0 {st0['selected']}")
    return frozen


# --------------------------------------------------------------------------- outcome table


def outcome_table(mk: R.Market, hr: S.Hourly, fee=R.FEE_SIDE, spread=R.BASE_RT) -> dict:
    """net / gross / mfe / mae for every hour, direction and horizon (NaN where the window leaves TRAIN)."""
    tab = {}
    for hn, hm in HORIZONS.items():
        for d in (1, -1):
            arr = {k: np.full(hr.n, np.nan) for k in ("net", "gross", "mfe", "mae")}
            for k in range(hr.n):
                e = S.entry_minute(hr, k)
                if e + hm > mk.last:
                    continue
                t = R.simulate(mk, e, d, e + hm, [], fee, spread)
                for f in arr:
                    arr[f][k] = t[f]
            tab[(hn, d)] = arr
    return tab


def vals(tab, hn, ev, key="net"):
    return np.array([tab[(hn, d)][key][k] for k, d in ev])


# --------------------------------------------------------------------------- statistics


def describe(tab, hn, ev) -> dict:
    net = vals(tab, hn, ev)
    ok = ~np.isnan(net)
    net = net[ok]
    if len(net) == 0:
        return {"n": 0}
    g = vals(tab, hn, ev, "gross")[ok]
    mfe, mae = vals(tab, hn, ev, "mfe")[ok], vals(tab, hn, ev, "mae")[ok]
    win, lose = net[net > 0], net[net <= 0]
    return {"n": int(len(net)), "net_mean": float(net.mean()), "net_median": float(np.median(net)),
            "gross_mean": float(g.mean()), "net_pos_share": float((net > 0).mean()), "pf": R.pf(net),
            "avg_winner": float(win.mean()) if len(win) else 0.0, "avg_loser": float(-lose.mean()) if len(lose) else 0.0,
            "mfe_mean": float(mfe.mean()), "mae_mean": float(mae.mean()), "net_p5": float(np.percentile(net, 5)),
            "net_min": float(net.min()), "mae_ge_10x_safety_share": float((mae >= SAFETY_10X).mean())}


def infer(boot, hr, ev, v) -> dict:
    v = np.asarray(v, dtype=float)
    ok = ~np.isnan(v)
    mins = np.array([S.entry_minute(hr, k) for k, _ in ev])[ok]
    days = mins // 1440
    r = boot.infer(days, v[ok])
    r["cluster_48h"] = R.cluster_infer(mins // 2880, v[ok])
    return r


# --------------------------------------------------------------------------- controls


def random_same_direction(tab, hn, ev, valid, observed, reps=N_RANDOM, seed=R.SEED) -> dict:
    rng = np.random.default_rng(seed)
    dirs = np.array([d for _, d in ev])
    means = []
    for _ in range(reps):
        ks = rng.choice(valid, size=len(ev), replace=True)
        x = np.array([tab[(hn, int(d))]["net"][k] for k, d in zip(ks, dirs)])
        means.append(float(np.nanmean(x)))
    means = np.array(means)
    return {"reps": reps, "mean_of_means": float(means.mean()), "p5": float(np.percentile(means, 5)),
            "p95": float(np.percentile(means, 95)), "share_ge_observed": float((means >= observed).mean())}


def time_shift(tab, hn, ev, days) -> dict:
    x = []
    for k, d in ev:
        j = k + 24 * days
        if j < len(tab[(hn, d)]["net"]):
            x.append(tab[(hn, d)]["net"][j])
    x = np.array(x)
    x = x[~np.isnan(x)]
    return {"n": int(len(x)), "net_mean": float(x.mean()) if len(x) else None}


def matched(tab, hn, ev, hr, cond_any, sign_arr, tercile, month, seed) -> np.ndarray:
    """Per event: mean net of K_MATCH matched hours (same month, same direction, same relation of the trade
    to sign_arr, same |1 h return| tercile when `tercile` is given, candidate condition false)."""
    rng = np.random.default_rng(seed)
    out = np.full(len(ev), np.nan)
    pools: dict = {}
    for n, (k, d) in enumerate(ev):
        rel = int(d * sign_arr[k]) if sign_arr[k] in (1, -1) else 0
        key = (month[k], d, rel, tercile[k] if tercile is not None else -1)
        if key not in pools:
            rel_all = np.where(np.isin(sign_arr, (1, -1)), d * sign_arr.astype(int), 0)
            m = (month == month[k]) & (rel_all == rel) & ~cond_any & ~np.isnan(tab[(hn, d)]["net"])
            if tercile is not None:
                m &= tercile == tercile[k]
            pools[key] = np.flatnonzero(m)
        pool = pools[key]
        if len(pool) == 0:
            continue
        pick = rng.choice(pool, size=K_MATCH, replace=len(pool) < K_MATCH)
        out[n] = float(np.mean(tab[(hn, d)]["net"][pick]))
    return out


# --------------------------------------------------------------------------- run


def run(out: Path | None = OUT, random_reps: int = N_RANDOM) -> dict:
    frozen = check_freeze()
    sha = hashlib.sha256(S0.CONTRACT.read_bytes()).hexdigest()
    log = PD.ReadLog()
    m = MD.load_train(log)
    vol, _ = PD.load_train(log)
    px = PD.load_train_prices(log)
    funding = PD.load_train_funding(log)
    mk = R.make_market(px, funding)
    hr = S.build_hourly(m, px.close, vol)
    tab = outcome_table(mk, hr)
    stress = {(hn, d): v for (hn, d), v in outcome_table(mk, hr, R.STRESS_FEE_SIDE, R.STRESS_RT).items()
              if hn in PRIMARY}
    boot = R.Bootstrap(len(px.open) // 1440)
    ts_h = PD.TRAIN_START_MS + hr.h * 3_600_000
    import datetime as dt
    month = np.array([dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).year * 12
                      + dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).month for t in ts_h])
    absr = np.abs(hr.ret1h)
    cuts = np.nanpercentile(absr, [100 / 3, 200 / 3])
    tercile = np.where(np.isnan(absr), -1, np.digitize(absr, cuts))
    defined = (hr.price != S.NONE) & (hr.oi_dir != S.NONE)
    rep: dict = {"contract_sha256": sha, "frozen": frozen,
                 "venue": {"price": "Binance USD-M BTCUSDT 1m", "positioning": "Binance metrics 5m",
                           "funding": "Binance", "fee": "Binance 5 bp/side (measured, PTM)",
                           "flow": "Binance taker_buy_quote"},
                 "entry": "open of the 1 m bar at H + 1 min; metrics row stamped H - 5 min",
                 "candidates": {}}
    pvals_net, pvals_inc = {}, {}
    for c in frozen:
        ev_raw = S.onsets(hr, c)
        ev = S.dedup_4h(ev_raw)
        ev48 = S.overlap_48h(ev)
        lo, sh = S.conditions(hr, c)
        cond_any = lo | sh
        valid = np.flatnonzero(defined)
        cres: dict = {"name": S.NAMES[c], "n_raw": len(ev_raw), "n_dedup_4h": len(ev), "n_overlap_48h": len(ev48),
                      "horizons": {}}
        for hn in HORIZONS:
            hres = {"all": describe(tab, hn, ev), "overlap_48h": describe(tab, hn, ev48),
                    "by_direction": {DIR[d]: describe(tab, hn, [x for x in ev if x[1] == d]) for d in (1, -1)}}
            net = vals(tab, hn, ev)
            hres["inference"] = infer(boot, hr, ev, net)
            if hn in PRIMARY:
                hres["controls"] = {
                    "random_same_direction": random_same_direction(tab, hn, ev, valid, float(np.nanmean(net)),
                                                                   reps=random_reps),
                    "time_shift_1d": time_shift(tab, hn, ev, 1), "time_shift_3d": time_shift(tab, hn, ev, 3)}
                opp = np.array([tab[(hn, -d)]["net"][k] for k, d in ev])
                hres["controls"]["opposite"] = {"net_mean": float(np.nanmean(opp)),
                                                "paired_event_minus_opposite": infer(boot, hr, ev, net - opp)}
                pm = matched(tab, hn, ev, hr, cond_any, hr.price, tercile, month, R.SEED + 1)
                fm = matched(tab, hn, ev, hr, cond_any, hr.flow, None, month, R.SEED + 2)
                ip, if_ = infer(boot, hr, ev, net - pm), infer(boot, hr, ev, net - fm)
                hres["incremental"] = {
                    "price_only_matched_net_mean": float(np.nanmean(pm)), "flow_only_matched_net_mean": float(np.nanmean(fm)),
                    "vs_price_only": ip, "vs_flow_only": if_,
                    "by_direction": {DIR[d]: {"vs_price_only": float(np.nanmean((net - pm)[[x[1] == d for x in ev]])),
                                              "vs_flow_only": float(np.nanmean((net - fm)[[x[1] == d for x in ev]]))}
                                     for d in (1, -1)}}
                sn = np.array([stress[(hn, d)]["net"][k] for k, d in ev])
                hres["stress_costs"] = {"net_mean": float(np.nanmean(sn)), "pf": R.pf(sn[~np.isnan(sn)])}
                pvals_net[f"{c}:{hn}"] = hres["inference"]["p"] if hres["inference"]["p"] is not None else 1.0
                pvals_inc[f"{c}:{hn}"] = max(ip["p"] or 1.0, if_["p"] or 1.0)
            cres["horizons"][hn] = hres
        rep["candidates"][c] = cres
    holm_net, holm_inc = R.holm(pvals_net), R.holm(pvals_inc)
    rep["multiple_testing"] = {"primary_tests": len(pvals_net), "holm_net": holm_net, "holm_incremental": holm_inc,
                               "raw_net": pvals_net, "raw_incremental": pvals_inc}
    gate, weak = {}, {}
    for c in frozen:
        cres = rep["candidates"][c]
        for hn in PRIMARY:
            h = cres["horizons"][hn]
            a, inc = h["all"], h["incremental"]
            bd = h["by_direction"]
            ln, sn_ = bd["LONG"], bd["SHORT"]
            sums = [ln.get("net_mean", 0) * ln.get("n", 0), sn_.get("net_mean", 0) * sn_.get("n", 0)]
            tot = sum(x for x in sums if x > 0)
            g = {"n48>=100": cres["n_overlap_48h"] >= 100, "mean_net>0": a["net_mean"] > 0,
                 "pf>1": (a["pf"] or 0) > 1,
                 "adv_price>0_and_flow>0": inc["vs_price_only"]["mean"] > 0 and inc["vs_flow_only"]["mean"] > 0,
                 "holm_net<=0.05_and_holm_inc<=0.05": holm_net[f"{c}:{hn}"] <= 0.05 and holm_inc[f"{c}:{hn}"] <= 0.05,
                 "long_short_balanced": (ln.get("n", 0) >= 30 and sn_.get("n", 0) >= 30 and ln["net_mean"] > 0
                                         and sn_["net_mean"] > 0 and max(sums) / tot <= 0.8) if tot > 0 else False,
                 "tail_mae_ge_3.84pct<=2pct": a["mae_ge_10x_safety_share"] <= 0.02}
            gate[f"{c}:{hn}"] = g
            weak[f"{c}:{hn}"] = (a["net_mean"] > 0 and inc["vs_price_only"]["mean"] > 0 and inc["vs_flow_only"]["mean"] > 0
                                 and (inc["vs_price_only"]["p"] or 1) <= 0.10 and (inc["vs_flow_only"]["p"] or 1) <= 0.10)
    passing = sorted({k.split(":")[0] for k, g in gate.items() if all(g.values())},
                     key=lambda c: min(holm_net[f"{c}:{h}"] for h in PRIMARY))[:2]
    rep["gate"] = gate
    rep["weak_rule"] = weak
    rep["validation_candidates"] = passing
    rep["verdict"] = ("C. POSITIONING_TRAIN_CANDIDATE" if passing else
                      "B. POSITIONING_WEAK" if any(weak.values()) else "A. POSITIONING_NO_EDGE")
    rep["read_log"] = log.entries
    rep["contract_sha256_after"] = hashlib.sha256(S0.CONTRACT.read_bytes()).hexdigest()
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=1, sort_keys=True, default=_json))
    return rep


def _json(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, float) and math.isinf(o):
        return "inf"
    raise TypeError(type(o))


if __name__ == "__main__":
    r = run(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
    print(json.dumps({"verdict": r["verdict"], "validation_candidates": r["validation_candidates"]}, indent=1))
