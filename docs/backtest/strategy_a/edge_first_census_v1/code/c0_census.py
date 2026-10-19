"""US-B A edge-first census C0 (preregistration sha 3baea825...3162). Read-only, cache-only."""
import gzip, json, os, sys, hashlib
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import warnings; warnings.filterwarnings("ignore", category=RuntimeWarning)

RT = Path.home() / "usb/data/runtime"
OUT = RT / "research_reports/a_edge_first_census_v1"
CACHE = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "_cache"
ET = ZoneInfo("America/New_York")
SEC_ROOTS = [RT / p / "sec_raw" for p in ("strategy_h_v2/d1_1", "strategy_h/h_pv2c", "strategy_h/h0_5")]
YEAR_B_START = date(2025, 9, 18)
D_MAX = date(2026, 10, 9)

def gz(p): return json.loads(gzip.decompress(Path(p).read_bytes()))

# ---------- grid + panel ----------
gdir = RT / "strategy_c/raw/grouped"
sessions = sorted(date.fromisoformat(f[:10]) for f in os.listdir(gdir))
S = len(sessions); sidx = {d: i for i, d in enumerate(sessions)}
assert sessions[20] == date(2024, 10, 14) and sessions[252] == YEAR_B_START
rows_by_s = []; grid_fallback = []
symset = set()
for d in sessions:
    b = gz(gdir / f"{d}.json.gz"); x = b.get("body") or b
    if "results" not in x:  # strategy_c error file -> same-source Massive mirror
        alt = RT / "strategy_b_e0/mirror/market_data/raw/massive/grouped_daily" / str(d.year) / f"{d}.json.gz"
        b = gz(alt); x = b.get("body") or b
        grid_fallback.append(f"{d}:{'MIRROR_OK' if 'results' in x else 'DATA_GAP_NO_BARS'}")
    r = x.get("results") or []
    rows_by_s.append(r); symset.update(x["T"] for x in r)
syms = sorted(symset); yidx = {s: i for i, s in enumerate(syms)}; N = len(syms)
O = np.full((N, S), np.nan); H = O.copy(); L = O.copy(); C = O.copy(); V = O.copy()
for j, r in enumerate(rows_by_s):
    for x in r:
        i = yidx[x["T"]]
        O[i, j], H[i, j], L[i, j], C[i, j], V[i, j] = x.get("o"), x.get("h"), x.get("l"), x.get("c"), x.get("v")
del rows_by_s
print("panel", N, S, flush=True)

# grid completeness vs XNYS
try:
    import exchange_calendars as xc
    xs = xc.get_calendar("XNYS").sessions_in_range(str(sessions[0]), str(sessions[-1]))
    missing_grid = sorted(set(x.date() for x in xs) - set(sessions))
except Exception as e:
    missing_grid = f"UNCHECKED {e}"

# ---------- CS snapshots ----------
snaps = []
for f in sorted(os.listdir(RT / "strategy_c/raw/tickers")):
    b = gz(RT / "strategy_c/raw/tickers" / f)
    asof = date.fromisoformat(f[3:13])
    snaps.append((asof, {x["ticker"]: (x.get("cik") or "").zfill(10) for x in b["results"] if x.get("type") == "CS"}))
snap_dates = [a for a, _ in snaps]
def snap_for(d):
    k = bisect_right(snap_dates, d) - 1
    return snaps[max(k, 0)][1]
sess_snap = [snap_for(d) for d in sessions]

# ---------- splits ----------
split_days = defaultdict(list)
for f in sorted(os.listdir(RT / "strategy_c/raw/splits")):
    for x in gz(RT / "strategy_c/raw/splits" / f)["results"]:
        ed = date.fromisoformat(x["execution_date"])
        k = bisect_right(sessions, ed - timedelta(days=0)) - 1  # session on/before execution date
        if ed not in sidx:  # non-session execution date -> next session
            k += 1
        if 0 <= k < S: split_days[x["ticker"]].append(k)
def split_hit(sym, lo, hi):
    return any(lo <= k <= hi for k in split_days.get(sym, ()))

# ---------- SEC: submissions (8-K 2.02, SIC, coverage) + shares ----------
CACHE.mkdir(parents=True, exist_ok=True)
all_ciks = set()
for _, m in snaps: all_ciks.update(c for c in m.values() if c.strip("0"))
def sec_file(kind, cik):
    for r in SEC_ROOTS:
        for p in (r / kind / f"CIK{cik}" / f"CIK{cik}.json.gz", r / kind / f"CIK{cik}.json.gz"):
            if p.is_file(): return p
    return None
sec_pkl = CACHE / "sec_extract.json"
if sec_pkl.is_file():
    sec = json.loads(sec_pkl.read_text())
else:
    sec = {}
    for n, cik in enumerate(sorted(all_ciks)):
        ent = {}
        p = sec_file("submissions", cik)
        if p:
            b = gz(p); b = b.get("body", b); rec = b["filings"]["recent"]
            fd = [x for x in rec.get("filingDate", []) if x]
            ent["sic"] = b.get("sic") or ""
            ent["earliest"] = min(fd) if fd else None
            ent["e202"] = [(rec["acceptanceDateTime"][k], rec["form"][k]) for k in range(len(rec["form"]))
                           if rec["form"][k] == "8-K" and "2.02" in (rec["items"][k] or "").split(",")]
        p = sec_file("companyfacts", cik)
        if p:
            b = gz(p); b = b.get("body", b)
            u = (((b.get("facts") or {}).get("dei") or {}).get("EntityCommonStockSharesOutstanding") or {}).get("units", {}).get("shares", [])
            agg = defaultdict(float)
            for x in u:
                if x.get("filed") and x.get("val") is not None:
                    agg[(x["filed"], x.get("accn"), x.get("end"))] += float(x["val"])
            ent["shares"] = sorted((k[0], v) for k, v in agg.items())
        sec[cik] = ent
        if n % 500 == 0: print("sec", n, len(all_ciks), flush=True)
    sec_pkl.write_text(json.dumps(sec))

def shares_at(cik, d):
    sh = (sec.get(cik) or {}).get("shares")
    if not sh: return None
    ds = d.isoformat(); best = None
    for filed, v in sh:  # sorted by filed; keep latest filed<=d (ties: last wins = same filed different accn/end, take max end order)
        if filed <= ds: best = (filed, v)
        else: break
    if best is None or (d - date.fromisoformat(best[0])).days > 400: return None
    return best[1]

# ---------- common eligibility (per symbol-session), computed lazily ----------
_shares_cache = {}
def common(i, j):
    """returns (ok, reason, cik)"""
    sym = syms[i]; cik = sess_snap[j].get(sym)
    if cik is None: return False, "NOT_CS", None
    c = C[i, j]
    if not np.isfinite(c) or c < 5: return False, "PRICE", cik
    key = (cik, j)
    if key not in _shares_cache: _shares_cache[key] = shares_at(cik, sessions[j])
    sh = _shares_cache[key]
    if sh is None: return False, "MCAP_MISSING", cik
    if sh * c < 300e6: return False, "MCAP_LOW", cik
    return True, "OK", cik

def d1_bar(i, j):
    if j + 1 >= S: return None
    o, h, l, c = O[i, j + 1], H[i, j + 1], L[i, j + 1], C[i, j + 1]
    return None if not np.isfinite(o * h * l * c) or o <= 0 else (o, h, l, c)

# ---------- families ----------
fam = {"F1": [], "F2": [], "F3": []}
excl = {k: Counter() for k in fam}
jmax = sidx[D_MAX]

# F1
for j in range(20, jmax + 1):
    prev = C[:, j - 1]; cur = C[:, j]
    with np.errstate(invalid="ignore", divide="ignore"):
        ret = cur / prev - 1
        w = V[:, j - 20:j]; cnt = np.isfinite(w).sum(1); avg = np.nanmean(np.where(np.isfinite(w), w, np.nan), 1)
        hit = (ret >= 0.05) & (cnt >= 15) & (V[:, j] >= 2.0 * avg)
    for i in np.flatnonzero(hit):
        ok, why, cik = common(i, j)
        if not ok: excl["F1"][why] += 1; continue
        if split_hit(syms[i], j - 20, j + 1): excl["F1"]["SPLIT_EXCLUDED"] += 1; continue
        fam["F1"].append((i, j, cik))

# F2
ciks_to_syms_by_s = {}
def tickers_of(cik, j):
    m = sess_snap[j]
    key = (id(m), cik)
    if key not in ciks_to_syms_by_s:
        ciks_to_syms_by_s[key] = [t for t, c in m.items() if c == cik]
    return ciks_to_syms_by_s[key]
et_hours = Counter(); f2_raw = 0; f2_window = 0; f2_out = Counter()
seen_f2 = set(); f2_tag = {}
for cik, ent in sec.items():
    for acc, form in ent.get("e202", []):
        if not acc: f2_out["NO_ACCEPTANCE"] += 1; continue
        t = datetime.fromisoformat(acc.replace("Z", "+00:00")).astimezone(ET)
        if not (sessions[0] <= t.date() <= sessions[-1] + timedelta(days=3)): continue
        f2_raw += 1; et_hours[t.hour] += 1
        # find D: last session with close-time 16:00 <= t, and t < next session 09:15
        k = bisect_right(sessions, t.date()) - 1
        if k < 0: continue
        tag = "EVENING"
        if sessions[k] == t.date() and t.time() < time(16, 0):
            if t.time() >= time(9, 15):
                f2_out["SESSION_DAY_0915_1600"] += 1; continue
            k -= 1; tag = "D1_EARLY"  # D+1 00:00-09:15 belongs to the previous session's window
            if k < 0: continue
        if k + 1 >= S: continue
        j = k
        if j > jmax: continue
        f2_window += 1; f2_out["IN_WINDOW_" + tag] += 1
        for sym in tickers_of(cik, j):
            if sym not in yidx: excl["F2"]["NO_BAR"] += 1; continue
            i = yidx[sym]
            if (i, j) in seen_f2: continue
            seen_f2.add((i, j))
            ok, why, _ = common(i, j)
            if not ok: excl["F2"][why] += 1; continue
            if split_hit(sym, j - 1, j + 1): excl["F2"]["SPLIT_EXCLUDED"] += 1; continue
            fam["F2"].append((i, j, cik)); f2_tag[(i, j)] = tag

# NOTE: an acceptance at D1 00:00-09:15 (premarket of D1) falls in the [D 16:00, D1 09:15) window by spec
# ("before D+1 09:15 ET"). The prereg calls "장전 발표 제외" = anything outside the window; we report the
# split of in-window filings into evening (D 16:00-24:00 / non-session days) vs D1 early-morning separately.

# F3
for j in range(252, jmax + 1):
    w = H[:, j - 252:j]; cnt = np.isfinite(w).sum(1)
    with np.errstate(invalid="ignore"):
        mx = np.nanmax(np.where(np.isfinite(w), w, -np.inf), 1)
        hit = (cnt >= 227) & (C[:, j] >= mx) & np.isfinite(C[:, j])
    for i in np.flatnonzero(hit):
        ok, why, cik = common(i, j)
        if not ok: excl["F3"][why] += 1; continue
        if split_hit(syms[i], j - 252, j + 1): excl["F3"]["SPLIT_EXCLUDED"] += 1; continue
        fam["F3"].append((i, j, cik))

# F2 in-window time split
def f2_timing():
    ev = Counter()
    for cik, ent in sec.items():
        for acc, form in ent.get("e202", []):
            if not acc: continue
            t = datetime.fromisoformat(acc.replace("Z", "+00:00")).astimezone(ET)
            if not (YEAR_B_START - timedelta(days=400) <= t.date() <= D_MAX): continue
            if t.time() >= time(16, 0): ev["16:00-24:00"] += 1
            elif t.time() < time(9, 15): ev["00:00-09:15"] += 1
            else: ev["09:15-16:00"] += 1
    return ev

# ---------- SEC coverage for F2 (common-eligible symbol-sessions) ----------
def f2_coverage(sample_every=5):
    tot = cov = 0
    for j in range(0, jmax + 1, sample_every):
        idx = np.flatnonzero(np.isfinite(C[:, j]) & (C[:, j] >= 5))
        for i in idx:
            ok, why, cik = common(i, j)
            if not ok: continue
            tot += 1
            e = sec.get(cik) or {}
            if "e202" in e and e.get("earliest") and e["earliest"] <= sessions[j].isoformat(): cov += 1
    return tot, cov

# ---------- stats ----------
def q(a):
    a = np.asarray(a, float)
    if len(a) == 0: return {"n": 0}
    p = np.percentile(a, [10, 25, 50, 75, 90])
    return {"n": int(len(a)), "mean": float(a.mean()), "median": float(p[2]), "p10": float(p[0]), "p25": float(p[1]),
            "p75": float(p[3]), "p90": float(p[4]), "share_pos": float((a > 0).mean())}

def sic2(cik):
    s = (sec.get(cik) or {}).get("sic") or ""
    return s[:2] if s else "UNKNOWN"

def summarize(cands, j_lo, j_hi):
    cs = [(i, j, cik) for i, j, cik in cands if j_lo <= j <= j_hi]
    no_d1 = 0; m = defaultdict(list); kept = []
    for i, j, cik in cs:
        b = d1_bar(i, j)
        if b is None: no_d1 += 1; continue
        o1, h1, l1, c1 = b; c0 = C[i, j]
        m["gap"].append(o1 / c0 - 1); m["oc"].append(c1 / o1 - 1); m["cc"].append(c1 / c0 - 1)
        m["hx"].append(h1 / o1 - 1); m["lx"].append(l1 / o1 - 1); kept.append((i, j, cik))
    n = len(kept)
    if n == 0: return {"candidates": 0, "no_d1_bar": no_d1}
    sym_c = Counter(syms[i] for i, _, _ in kept); ses_c = Counter(j for _, j, _ in kept)
    eval_sessions = j_hi - j_lo + 1
    per_session_all = [ses_c.get(j, 0) for j in range(j_lo, j_hi + 1)]
    sic_c = Counter(sic2(c) for _, _, c in kept); known = n - sic_c.get("UNKNOWN", 0)
    top_sic = [(k, v / known) for k, v in Counter({k: v for k, v in sic_c.items() if k != "UNKNOWN"}).most_common(3)] if known else []
    return {
        "candidates": n, "no_d1_bar": no_d1, "candidate_sessions": len(ses_c), "evaluable_sessions": eval_sessions,
        "unique_symbols": len(sym_c), "repeat_ratio": n / len(sym_c),
        "median_per_candidate_session": float(np.median(list(ses_c.values()))),
        "median_per_evaluable_session": float(np.median(per_session_all)),
        "top5_symbol_share": sum(v for _, v in sym_c.most_common(5)) / n, "top5_symbols": sym_c.most_common(5),
        "max_single_session_share": max(ses_c.values()) / n,
        "max_session": sessions[max(ses_c, key=ses_c.get)].isoformat(),
        "sic2_top3_share_of_known": top_sic, "sic_unknown_share": sic_c.get("UNKNOWN", 0) / n,
        "d1": {k: q(v) for k, v in m.items()},
    }

WIN = {"F1": (20, jmax), "F2": (0, jmax), "F3": (252, jmax)}
res = {}
for f, (lo, hi) in WIN.items():
    r = {"ALL": summarize(fam[f], lo, hi)}
    if f == "F3":
        r["YEAR_A"] = "NOT_AVAILABLE"; r["YEAR_B"] = summarize(fam[f], 252, jmax)
    else:
        r["YEAR_A"] = summarize(fam[f], lo, 251); r["YEAR_B"] = summarize(fam[f], 252, jmax)
    r["exclusions_after_family_condition"] = dict(excl[f])
    res[f] = r

tot, cov = f2_coverage()
res["F2"]["sec_coverage"] = {"common_symbol_sessions_sampled_every5": tot, "covered": cov, "ratio": cov / tot if tot else None}
res["F2"]["acceptance_et_hour_hist_8k_202"] = dict(sorted(et_hours.items()))
res["F2"]["filings_in_range"] = f2_raw; res["F2"]["filings_in_window"] = f2_window; res["F2"]["filings_outside"] = dict(f2_out)
res["F2"]["in_window_timing_check"] = dict(f2_timing())
res["F2"]["candidate_timing_split"] = dict(Counter(f2_tag[(i, j)] for i, j, _ in fam["F2"]))

# gates
def gates(f):
    a = res[f]["ALL"]; g = {}
    if a.get("candidates", 0) == 0:
        return {"gates": {"S1": False}, "fails": ["S1"], "verdict": "C0_STRUCTURAL_FAIL"}
    g["S1"] = a["candidates"] >= 100; g["S2"] = a["unique_symbols"] >= 30; g["S3"] = a["top5_symbol_share"] <= 0.25
    g["S4"] = a["candidate_sessions"] >= 50; g["S5"] = a["max_single_session_share"] <= 0.10
    if f == "F3": g["S6"] = "NOT_APPLIED (AVAILABLE_PERIOD_ONLY)"
    else: g["S6"] = res[f]["YEAR_A"].get("candidates", 0) >= 30 and res[f]["YEAR_B"].get("candidates", 0) >= 30
    if f == "F2": g["S7"] = (res["F2"]["sec_coverage"]["ratio"] or 0) >= 0.5
    fails = [k for k, v in g.items() if v is False]
    verdict = "C0_PASS" if not fails else ("C0_DATA_LIMITED" if fails == ["S7"] else "C0_STRUCTURAL_FAIL")
    return {"gates": g, "fails": fails, "verdict": verdict}
for f in fam: res[f]["C0"] = gates(f)

res["_meta"] = {
    "prereg_sha256": (OUT / "PREREGISTRATION_C0_C1.sha256").read_text().split()[0],
    "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "grid": [sessions[0].isoformat(), sessions[-1].isoformat(), S], "grid_missing_vs_xnys": [str(x) for x in missing_grid] if isinstance(missing_grid, list) else missing_grid,
    "symbols_in_panel": N, "grid_fallback_to_b_e0_mirror": grid_fallback, "cs_snapshots": [d.isoformat() for d in snap_dates],
    "ciks_in_cs": len(all_ciks), "ciks_with_submissions": sum(1 for v in sec.values() if "e202" in v),
    "ciks_with_shares": sum(1 for v in sec.values() if v.get("shares")),
    "splits_cover_to": "2026-10-09", "acceptance_zone": "UTC",
    "generated_at": datetime.now(timezone.utc).isoformat(),
}
(OUT / "c0_results.json").write_text(json.dumps(res, indent=1, default=str))
with open(OUT / "c0_candidates.csv", "w") as fh:
    fh.write("family,date,symbol,cik\n")
    for f, cs in fam.items():
        for i, j, cik in sorted(cs, key=lambda x: (x[1], syms[x[0]])):
            fh.write(f"{f},{sessions[j]},{syms[i]},{cik}\n")
print(json.dumps({f: res[f]["C0"] for f in fam}, indent=1))
