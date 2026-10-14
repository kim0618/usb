"""HOT-EDGE R0 TRAIN study (contract docs/crypto/hot_coin_edge_r0/CONTRACT_HOT_EDGE_R0_V1.md).

Run from backend/: python -m app.crypto.research.hot_edge_r0.study [out.json]
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

from ..ptm_r1 import r1_train as R
from . import data as D

CONTRACT = D.ROOT / "docs/crypto/hot_coin_edge_r0/CONTRACT_HOT_EDGE_R0_V1.md"
OUT = D.ROOT / "data/research/crypto/hot_edge_r0/r0_train_v1.json"
SCAN_STEP = 48                      # 4 h in 5 m bars
BARS_1H, BARS_24H, BARS_DAY = 12, 288, 288
LISTING_AGE = 30 * BARS_DAY
QV24_MIN, QV1_MIN = 20e6, 0.5e6
TOP, DOSE_END = 5, 20
HORIZONS = {"4h": 48, "12h": 144, "24h": 288, "48h": 576, "72h": 864, "120h": 1440}
PRIMARY = ("V1", "24h")
VARIANTS = ("V1", "V2", "V3")
COSTS_RT = {"LOW": 0.0015, "BASE": 0.0040, "HIGH": 0.0080}
MMR = 0.01
LEVS = (5, 10, 20)
N_RANDOM = 200
EXCLUDE = ("BTCUSDT",)
GATE = {"n_c": 300, "sym_c": 30, "conc_c": 0.5, "breakeven_c": 0.0060, "n_b": 150, "p_b": 0.10}


# --------------------------------------------------------------------------- features at scan times


def rolling_sum(x: np.ndarray, w: int) -> np.ndarray:
    """Sum over the last w bars ending at each index (NaN if any NaN inside). Row-wise."""
    z = np.where(np.isnan(x), 0.0, x).astype(np.float64)
    bad = np.isnan(x).astype(np.int32)
    cz = np.concatenate((np.zeros((x.shape[0], 1)), np.cumsum(z, axis=1)), axis=1)
    cb = np.concatenate((np.zeros((x.shape[0], 1), dtype=np.int32), np.cumsum(bad, axis=1)), axis=1)
    out = np.full(x.shape, np.nan)
    s = cz[:, w:] - cz[:, :-w]
    b = cb[:, w:] - cb[:, :-w]
    s[b > 0] = np.nan
    out[:, w - 1:] = s
    return out


def rolling_max(x: np.ndarray, w: int) -> np.ndarray:
    out = np.full(x.shape, np.nan)
    win = np.lib.stride_tricks.sliding_window_view(x, w, axis=1)
    out[:, w - 1:] = np.max(win, axis=2)         # NaN propagates
    return out


def rolling_min(x: np.ndarray, w: int) -> np.ndarray:
    out = np.full(x.shape, np.nan)
    win = np.lib.stride_tricks.sliding_window_view(x, w, axis=1)
    out[:, w - 1:] = np.min(win, axis=2)
    return out


def scan_features(g: D.Grid, scans: np.ndarray, first_bar: np.ndarray) -> dict[str, np.ndarray]:
    """Features at scan boundaries (the bar ending at index s is bar s). Arrays [n_sym, n_scans]."""
    close, high, low, qv = g.close.astype(np.float64), g.high, g.low, g.qv
    qv24 = rolling_sum(qv, BARS_24H)[:, scans]
    qv1 = rolling_sum(qv, BARS_1H)[:, scans]
    hi1 = rolling_max(high, BARS_1H)[:, scans].astype(np.float64)
    lo1 = rolling_min(low, BARS_1H)[:, scans].astype(np.float64)
    c = close[:, scans]
    c1 = close[:, scans - BARS_1H]
    c24 = close[:, scans - BARS_24H]
    age_ok = (scans[None, :] - first_bar[:, None]) >= LISTING_AGE
    elig = age_ok & (qv24 >= QV24_MIN) & (qv1 >= QV1_MIN) & ~np.isnan(c) & ~np.isnan(c1) & ~np.isnan(c24)
    return {"qv24": qv24, "qv1": qv1, "range1h": (hi1 - lo1) / lo1, "ret1h": c / c1 - 1.0,
            "ret24h": c / c24 - 1.0, "surge": qv1 / (qv24 / 24.0), "elig": elig}


def rank_desc(score: np.ndarray, elig: np.ndarray) -> np.ndarray:
    """1-based rank among eligible per scan (column); 0 where not eligible."""
    s = np.where(elig, score, -np.inf)
    order = np.argsort(-s, axis=0, kind="stable")
    ranks = np.zeros(score.shape, dtype=np.int32)
    rows = np.arange(score.shape[0])
    for j in range(score.shape[1]):
        r = np.empty(score.shape[0], dtype=np.int32)
        r[order[:, j]] = rows + 1
        ranks[:, j] = np.where(elig[:, j], r, 0)
    return ranks


def variant_score_dir(f: dict, v: str) -> tuple[np.ndarray, np.ndarray]:
    if v == "V1":
        return f["range1h"], np.sign(f["ret1h"])
    if v == "V2":
        return np.abs(f["ret24h"]), np.sign(f["ret24h"])
    if v == "V3":
        return f["surge"], np.sign(f["ret1h"])
    raise ValueError(v)


# --------------------------------------------------------------------------- outcomes


def outcomes(g: D.Grid, i: int, s: int, dirn: int, h: int) -> dict | None:
    """Signed forward return from open[s+1] to open[s+1+h], MFE/MAE over 120 h, funding in the window."""
    e = s + 1
    x = e + h
    if x >= D.N_BARS:
        return None
    p0, px = float(g.open[i, e]), float(g.open[i, x])
    if math.isnan(p0) or math.isnan(px):
        return None
    gross = dirn * (px / p0 - 1.0)
    end120 = min(e + HORIZONS["120h"], D.N_BARS)
    hi = g.high[i, e:end120].astype(np.float64)
    lo = g.low[i, e:end120].astype(np.float64)
    fav = (hi / p0 - 1.0) if dirn == 1 else (1.0 - lo / p0)
    adv = (1.0 - lo / p0) if dirn == 1 else (hi / p0 - 1.0)
    ok = ~np.isnan(fav)
    mfe = float(np.max(fav[ok])) if ok.any() else 0.0
    mae = float(np.max(adv[ok])) if ok.any() else 0.0
    mae_h = float(np.nanmax(adv[:h])) if h <= len(adv) and (~np.isnan(adv[:h])).any() else 0.0
    fidx, frate = g.funding[g.symbols[i]]
    a, b = np.searchsorted(fidx, [e, x])
    funding = float(np.sum(dirn * frate[a:b]))           # rate x notional, mark ~ price
    return {"gross": gross, "funding": funding, "mfe": max(mfe, 0.0), "mae": max(mae, 0.0),
            "mae_h": max(mae_h, 0.0), "t_mfe_bars": int(np.nanargmax(fav)) if ok.any() else 0}



def net_cost(o: dict, rt: float) -> float:
    """Fees 5 bp/side are included in the round-trip scenario rt (15/40/80 bp are all-in)."""
    return o["gross"] - rt - o["funding"]


# --------------------------------------------------------------------------- statistics


def dist(v) -> dict | None:
    a = np.asarray(v, dtype=float)
    if len(a) == 0:
        return None
    return {"n": int(len(a)), "mean": float(a.mean()), "median": float(np.median(a)),
            "p5": float(np.percentile(a, 5)), "p95": float(np.percentile(a, 95)), "min": float(a.min()),
            "max": float(a.max())}


def cell_stats(ev: list[dict], h: str, boot: R.Bootstrap) -> dict:
    if not ev:
        return {"n": 0}
    g = np.array([e["out"][h]["gross"] for e in ev])
    fund = np.array([e["out"][h]["funding"] for e in ev])
    days = np.array([e["s"] // BARS_DAY for e in ev])
    scans = np.array([e["s"] for e in ev])
    res = {"n": len(ev), "scan_times": int(len(set(scans))), "symbols": int(len(set(e["sym"] for e in ev))),
           "long_share": float(np.mean([e["dir"] == 1 for e in ev])), "gross_mean": float(g.mean()),
           "gross_median": float(np.median(g)), "funding_mean": float(fund.mean()),
           "win_share_gross": float((g > 0).mean()), "breakeven_cost_rt": float(g.mean() - fund.mean()),
           "mfe_mean": float(np.mean([e["out"][h]["mfe"] for e in ev])),
           "mae_mean": float(np.mean([e["out"][h]["mae"] for e in ev])), "by_cost": {}}
    for name, rt in COSTS_RT.items():
        nt = g - rt - fund
        inf = boot.infer(days, nt)
        inf["cluster_scan"] = R.cluster_infer(scans, nt)
        res["by_cost"][name] = {"net_mean": float(nt.mean()), "net_median": float(np.median(nt)),
                                "pf": R.pf(nt), "win_share": float((nt > 0).mean()), "inference": inf}
    return res


def concentration(ev: list[dict], h: str) -> dict:
    g = np.array([e["out"][h]["gross"] for e in ev])
    pos = np.sort(g[g > 0])[::-1]
    k = max(1, int(math.ceil(0.05 * len(g))))
    top_share = float(pos[:k].sum() / pos.sum()) if len(pos) and pos.sum() > 0 else None
    by_sym: dict[str, float] = {}
    for e in ev:
        by_sym[e["sym"]] = by_sym.get(e["sym"], 0.0) + e["out"][h]["gross"]
    ranked = sorted(by_sym.items(), key=lambda kv: -kv[1])
    total_pos = sum(v for _, v in ranked if v > 0)
    return {"top5pct_events_share_of_positive_gross": top_share,
            "symbols_positive": sum(1 for _, v in ranked if v > 0), "symbols_total": len(ranked),
            "top3_symbols": [(s, round(v, 4)) for s, v in ranked[:3]],
            "top3_symbols_share_of_positive": (sum(v for _, v in ranked[:3] if v > 0) / total_pos)
            if total_pos > 0 else None}


def by_group(ev: list[dict], h: str, key) -> dict:
    out: dict[str, list[float]] = {}
    for e in ev:
        out.setdefault(str(key(e)), []).append(net_cost(e["out"][h], COSTS_RT["BASE"]))
    return {k: {"n": len(v), "net_mean_base": float(np.mean(v))} for k, v in sorted(out.items())}


def leverage_geometry(ev: list[dict], h: str) -> dict:
    mae = np.array([e["out"][h]["mae_h"] for e in ev])
    out = {}
    for L in LEVS:
        liq = 1.0 / L - MMR
        out[f"L{L}"] = {"liq_distance": liq, "liquidated_share": float((mae >= liq).mean()),
                        "safety_hit_share": float((mae >= 0.4 * liq).mean())}
    return out


# --------------------------------------------------------------------------- study


def build_events(g: D.Grid, f: dict, scans: np.ndarray, v: str, lo: int, hi: int, rng=None,
                 per_scan: int | None = None) -> list[dict]:
    score, dirs = variant_score_dir(f, v)
    ranks = rank_desc(score, f["elig"])
    ev = []
    for j, s in enumerate(scans):
        rows = np.flatnonzero((ranks[:, j] >= lo) & (ranks[:, j] <= hi) & (dirs[:, j] != 0))
        if rng is not None and per_scan is not None and len(rows):
            rows = rng.choice(rows, size=min(per_scan, len(rows)), replace=False)
        for i in rows:
            dirn = int(dirs[i, j])
            outs = {}
            for hn, hb in HORIZONS.items():
                o = outcomes(g, int(i), int(s), dirn, hb)
                if o is None:
                    break
                outs[hn] = o
            if len(outs) != len(HORIZONS):
                continue
            ev.append({"s": int(s), "i": int(i), "sym": g.symbols[i], "dir": dirn, "rank": int(ranks[i, j]),
                       "score": float(score[i, j]), "out": outs})
    return ev


def random_control(g: D.Grid, f: dict, scans: np.ndarray, v: str, hot: list[dict], h: str,
                   boot: R.Bootstrap, reps: int = N_RANDOM, seed: int = R.SEED) -> dict:
    """For every hot event, the mean outcome of `reps` random rank>20 eligible symbols at the same scan."""
    score, dirs = variant_score_dir(f, v)
    ranks = rank_desc(score, f["elig"])
    rng = np.random.default_rng(seed)
    cache: dict[int, np.ndarray] = {}
    exp_net, got = [], []
    for e in hot:
        j = int(np.searchsorted(scans, e["s"]))
        if j not in cache:
            rows = np.flatnonzero((ranks[:, j] > DOSE_END) & (dirs[:, j] != 0))
            vals = []
            if len(rows):
                pick = rng.choice(rows, size=reps, replace=True)
                for i in pick:
                    o = outcomes(g, int(i), e["s"], int(dirs[i, j]), HORIZONS[h])
                    if o is not None:
                        vals.append(net_cost(o, COSTS_RT["BASE"]))
            cache[j] = np.array(vals)
        if len(cache[j]):
            exp_net.append(float(cache[j].mean()))
            got.append(e)
    if not got:
        return {"n": 0}
    hot_net = np.array([net_cost(e["out"][h], COSTS_RT["BASE"]) for e in got])
    exp_net = np.array(exp_net)
    days = np.array([e["s"] // BARS_DAY for e in got])
    diff = boot.infer(days, hot_net - exp_net)
    diff["cluster_scan"] = R.cluster_infer(np.array([e["s"] for e in got]), hot_net - exp_net)
    return {"n": len(got), "random_net_mean": float(exp_net.mean()), "hot_net_mean": float(hot_net.mean()),
            "paired_hot_minus_random": diff}


def btc_same_time(g: D.Grid, f: dict, scans: np.ndarray, hot: list[dict], h: str) -> dict:
    if "BTCUSDT" not in g.symbols:
        return {"n": 0}
    i = g.symbols.index("BTCUSDT")
    vals = []
    for s in sorted(set(e["s"] for e in hot)):
        j = int(np.searchsorted(scans, s))
        d = int(np.sign(f["ret1h"][i, j])) if not np.isnan(f["ret1h"][i, j]) else 0
        if d == 0:
            continue
        o = outcomes(g, i, s, d, HORIZONS[h])
        if o is not None:
            vals.append(net_cost(o, COSTS_RT["LOW"]))
    return {"n": len(vals), "net_mean_low_cost": float(np.mean(vals)) if vals else None,
            "win_share": float(np.mean(np.array(vals) > 0)) if vals else None}


def time_shift(g: D.Grid, hot: list[dict], h: str, days: int) -> dict:
    vals = []
    for e in hot:
        o = outcomes(g, e["i"], e["s"] + days * BARS_DAY, e["dir"], HORIZONS[h])
        if o is not None:
            vals.append(net_cost(o, COSTS_RT["BASE"]))
    return {"n": len(vals), "net_mean_base": float(np.mean(vals)) if vals else None}


def verdict(cell: dict, rnd: dict, dose: dict, conc: dict, years: dict, holm_p: float) -> tuple[str, str, dict]:
    base = cell["by_cost"]["BASE"]
    m = base["net_mean"]
    sign = 1.0 if m > 0 else -1.0
    direction = "CONTINUATION" if m > 0 else "REVERSAL"
    ci = base["inference"]["ci95"]
    ci_excl = (ci[0] > 0) if sign > 0 else (ci[1] < 0)
    rd = rnd.get("paired_hot_minus_random", {}) if rnd.get("n") else {}
    rci = rd.get("ci95", [0.0, 0.0])
    r_excl = (rci[0] > 0) if sign > 0 else (rci[1] < 0)
    d_top, d_mid, d_rest = (dose.get(k, {}).get("net_mean_base") for k in ("top5", "rank6_20", "rank21plus"))
    mono = all(x is not None for x in (d_top, d_mid, d_rest)) and (
        (d_top > d_mid > d_rest) if sign > 0 else (d_top < d_mid < d_rest))
    yr_sign = sum(1 for v in years.values() if np.sign(v["net_mean_base"]) == sign)
    conc_ok = conc["top5pct_events_share_of_positive_gross"] is not None and \
        conc["top5pct_events_share_of_positive_gross"] <= GATE["conc_c"]
    be = cell["breakeven_cost_rt"] * sign
    gates_c = {"n>=300": cell["n"] >= GATE["n_c"], "symbols>=30": cell["symbols"] >= GATE["sym_c"],
               "ci_excludes_0_after_holm": ci_excl and holm_p <= 0.05,
               "beats_random_ci": r_excl, "dose_monotone": bool(mono),
               "concentration<=50pct": bool(conc_ok), "years_same_sign>=2of3": yr_sign >= 2,
               "breakeven>=60bp": be >= GATE["breakeven_c"]}
    p = base["inference"]["p"] or 1.0
    rp = rd.get("p", 1.0) or 1.0
    r_same_sign = np.sign(rd.get("mean", 0.0)) == sign
    gates_b = {"n>=150": cell["n"] >= GATE["n_b"], "p<=0.10": p <= GATE["p_b"],
               "beats_random_p<=0.10_same_sign": bool(r_same_sign and rp <= GATE["p_b"])}
    if all(gates_c.values()):
        v = "C. HOT_EDGE_TRAIN_CANDIDATE"
    elif all(gates_b.values()):
        v = "B. WEAK_HOT_EDGE"
    else:
        v, direction = "A. NO_HOT_EDGE", "NONE" if not all(gates_b.values()) else direction
    return v, direction, {"C": gates_c, "B": gates_b}


def run(out: Path | None = OUT, random_reps: int = N_RANDOM) -> dict:
    sha = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    log = D.ReadLog()
    g, cov = D.load_train(log)
    first_bar = np.array([cov["first_bar_index"][s] if cov["first_bar_index"][s] is not None else 10 ** 9
                          for s in g.symbols])
    scans = np.arange(SCAN_STEP - 1, D.N_BARS, SCAN_STEP)
    scans = scans[scans >= BARS_24H]
    f = scan_features(g, scans, first_bar)
    for s in EXCLUDE:
        if s in g.symbols:
            f["elig"][g.symbols.index(s), :] = False
    boot = R.Bootstrap(D.N_BARS // BARS_DAY)
    rep: dict = {"contract_sha256": sha, "coverage": {k: v for k, v in cov.items() if k != "first_bar_index"},
                 "scans": int(len(scans))}
    elig_per_scan = f["elig"].sum(axis=0)
    rep["census"] = {"eligible_per_scan": dist(elig_per_scan),
                     "scans_with_ge5_eligible": int((elig_per_scan >= 5).sum()),
                     "eligible_by_year": {y: float(np.mean(elig_per_scan[(g.ts[scans] >= t0) & (g.ts[scans] < t1)]))
                                          for y, t0, t1 in (("2021", 1_612_137_600_000, 1_640_995_200_000),
                                                            ("2022", 1_640_995_200_000, 1_672_531_200_000),
                                                            ("2023H1", 1_672_531_200_000, 1_688_169_600_000))},
                     "symbols_ever_eligible": int((f["elig"].any(axis=1)).sum())}
    year_of = lambda e: ("2021" if g.ts[e["s"]] < 1_640_995_200_000 else            # noqa: E731
                         "2022" if g.ts[e["s"]] < 1_672_531_200_000 else "2023H1")
    cells: dict = {}
    pvals: dict[str, float] = {}
    rng = np.random.default_rng(R.SEED)
    for v in VARIANTS:
        hot = build_events(g, f, scans, v, 1, TOP)
        mid = build_events(g, f, scans, v, TOP + 1, DOSE_END)
        rest = build_events(g, f, scans, v, DOSE_END + 1, 10 ** 6, rng=rng, per_scan=5)
        vres: dict = {"n_events": len(hot), "horizons": {}}
        for h in HORIZONS:
            c = cell_stats(hot, h, boot)
            c["dose"] = {"top5": {"n": len(hot), "net_mean_base": float(np.mean([net_cost(e["out"][h], COSTS_RT["BASE"]) for e in hot]))} if hot else {},
                         "rank6_20": {"n": len(mid), "net_mean_base": float(np.mean([net_cost(e["out"][h], COSTS_RT["BASE"]) for e in mid]))} if mid else {},
                         "rank21plus": {"n": len(rest), "net_mean_base": float(np.mean([net_cost(e["out"][h], COSTS_RT["BASE"]) for e in rest]))} if rest else {}}
            if hot:
                c["by_year"] = by_group(hot, h, year_of)
                c["by_direction"] = by_group(hot, h, lambda e: "LONG" if e["dir"] == 1 else "SHORT")
                c["concentration"] = concentration(hot, h)
                c["leverage_geometry"] = leverage_geometry(hot, h)
                c["btc_same_time"] = btc_same_time(g, f, scans, hot, h)
                c["time_shift"] = {f"+{d}d": time_shift(g, hot, h, d) for d in (1, 3)}
                if (v, h) == PRIMARY or h == "24h":
                    c["random_control"] = random_control(g, f, scans, v, hot, h, boot, reps=random_reps)
            vres["horizons"][h] = c
        if hot:
            pvals[v] = vres["horizons"][PRIMARY[1]]["by_cost"]["BASE"]["inference"]["p"] or 1.0
            vres["mfe_timing_bars_median_120h"] = float(np.median([e["out"]["120h"]["t_mfe_bars"] for e in hot]))
            vres["sample_events"] = [{k: e[k] for k in ("s", "sym", "dir", "rank", "score")} | {"gross_24h": e["out"]["24h"]["gross"]}
                                     for e in sorted(hot, key=lambda e: -abs(e["out"]["24h"]["gross"]))[:10]]
        cells[v] = vres
    hp = R.holm(pvals) if pvals else {}
    verdicts = {}
    for v in VARIANTS:
        c = cells[v]["horizons"].get(PRIMARY[1], {})
        if c.get("n", 0) == 0 or "random_control" not in c:
            verdicts[v] = ("A. NO_HOT_EDGE", "NONE", {})
            continue
        verdicts[v] = verdict(c, c["random_control"], c["dose"], c["concentration"], c["by_year"], hp.get(v, 1.0))
    rep["variants"] = cells
    rep["holm_primary_horizon"] = hp
    rep["verdicts"] = {v: {"verdict": a, "direction": b, "gates": gts} for v, (a, b, gts) in verdicts.items()}
    rep["primary"] = {"variant": PRIMARY[0], "horizon": PRIMARY[1], **rep["verdicts"][PRIMARY[0]]}
    rep["read_log_files"] = len(log.entries)
    rep["read_log_sha256"] = hashlib.sha256(json.dumps(log.entries, sort_keys=True).encode()).hexdigest()
    rep["contract_sha256_after"] = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, indent=1, sort_keys=True, default=_json))
        (out.parent / "r0_train_v1_readlog.json").write_text(json.dumps(log.entries, indent=0))
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
    print(json.dumps({"census": r["census"], "verdicts": {k: (v["verdict"], v["direction"]) for k, v in r["verdicts"].items()}}, indent=1))
