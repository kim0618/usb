"""F2 existing-SEC-authority recovery audit V1. Network hard-blocked, read-only.
python f2_recovery_audit.py <c0 sec_extract.json> <server_sec_dir> <outdir>
"""
import socket
def _blocked(*a, **k): raise RuntimeError("NETWORK BLOCKED (F2 recovery audit)")
socket.socket = _blocked; socket.create_connection = _blocked; socket.getaddrinfo = _blocked

import gzip, json, os, re, sys
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import warnings; warnings.filterwarnings("ignore")

RT = Path.home() / "usb/data/runtime"; HA = Path.home() / "h_auto_completion"
ET = ZoneInfo("America/New_York"); UTC = timezone.utc
c0_extract = json.loads(Path(sys.argv[1]).read_text()); SERVER = Path(sys.argv[2]); OUT = Path(sys.argv[3]); OUT.mkdir(parents=True, exist_ok=True)

SOURCES = [  # (name, [submissions dirs]) in increment order; BASE = exactly C0's three roots
    ("BASE_C0", [RT / "strategy_h_v2/d1_1/sec_raw/submissions", RT / "strategy_h/h_pv2c/sec_raw/submissions", RT / "strategy_h/h0_5/sec_raw/submissions"]),
    ("STRATEGY_C_E0", [RT / "strategy_c/e0/raw/submissions"]),
    ("H_AUTO_LOCAL", [HA / "store/auto/sec/2026-10-13/submissions", HA / "store_e2e/auto/sec/2026-10-13/submissions",
                      HA / "store_e2e/auto/sec/2026-10-14/submissions", HA / "store_e2e/sec_submissions/2026-10-13/submissions",
                      HA / "measure_sec/2026-10-13/submissions"]),
    ("H_D7_SERVER", sorted((SERVER / "auto/sec").glob("*/submissions")) + sorted((SERVER / "sec_submissions").glob("*/submissions"))),
]

def gz(p): return json.loads(gzip.decompress(Path(p).read_bytes()))

# ---------------- inventory ----------------
def block_rows(b):
    if "filings" in b: return b["filings"]["recent"]
    return b  # paged submissions-00N file: columns at top level

inv = []; per_source = {}
for name, dirs in SOURCES:
    ciks = defaultdict(lambda: {"filings": {}, "fetched": [], "bodies": 0, "ledgers": 0, "unreadable": 0, "sic": None})
    stat = Counter(); fields_ok = Counter(); dmin = dmax = None
    for d in dirs:
        if not d.is_dir(): stat["missing_dir"] += 1; continue
        for root, _, files in os.walk(d):
            for fn in files:
                m = re.match(r"CIK(\d{10})", fn)
                if not m: continue
                cik = m.group(1); e = ciks[cik]; p = Path(root) / fn
                if fn.endswith(".request.json"):
                    e["ledgers"] += 1
                    try:
                        led = json.loads(p.read_text()); fa = led.get("fetched_at")
                        if fa and Path(str(p)[:-len(".request.json")]).is_file(): e["fetched"].append(fa)
                    except Exception: pass
                    continue
                if not fn.endswith(".json.gz"): continue
                try:
                    b = gz(p); b = b.get("body", b) if isinstance(b, dict) else b
                    rec = block_rows(b)
                except Exception:
                    e["unreadable"] += 1; continue
                e["bodies"] += 1
                if "sic" in b: e["sic"] = b.get("sic")
                cols = set(rec)
                for k in ("form", "items", "acceptanceDateTime", "filingDate", "accessionNumber"):
                    fields_ok[k] += k in cols
                n = len(rec.get("form", []))
                for i in range(n):
                    acc = rec["accessionNumber"][i]
                    row = (rec["form"][i], rec.get("items", [""] * n)[i] or "", rec.get("acceptanceDateTime", [""] * n)[i] or "", rec["filingDate"][i])
                    e["filings"][acc] = row
                    fd = row[3]
                    if fd: dmin = fd if dmin is None or fd < dmin else dmin; dmax = fd if dmax is None or fd > dmax else dmax
    bodies_ciks = [c for c, e in ciks.items() if e["bodies"]]
    ledger_only = [c for c, e in ciks.items() if e["ledgers"] and not e["bodies"]]
    inv.append({"source": name, "paths": [str(d) for d in dirs], "cik_dirs": len(ciks), "ciks_with_body": len(bodies_ciks),
                "ciks_ledger_only": len(ledger_only), "unreadable_files": sum(e["unreadable"] for e in ciks.values()),
                "body_files": sum(e["bodies"] for e in ciks.values()), "ledger_files": sum(e["ledgers"] for e in ciks.values()),
                "submission_rows": sum(len(e["filings"]) for e in ciks.values()), "filing_date_range": [dmin, dmax],
                "fields_present_in_bodies": dict(fields_ok),
                "fetched_at_range": [min((f for e in ciks.values() for f in e["fetched"]), default=None), max((f for e in ciks.values() for f in e["fetched"]), default=None)]})
    per_source[name] = {c: e for c, e in ciks.items() if e["bodies"]}
    print("inventory", inv[-1]["source"], inv[-1]["ciks_with_body"], inv[-1]["ciks_ledger_only"], flush=True)

# ---------------- conflicts across sources (same accession) ----------------
conflicts = Counter(); conflict_examples = []
seen = {}
for name, _ in SOURCES:
    for cik, e in per_source[name].items():
        for acc, row in e["filings"].items():
            k = (cik, acc)
            if k in seen and seen[k][1] != row:
                a, b = seen[k][1], row
                diff = [f for f, x, y in zip(("form", "items", "acceptance", "filingDate"), a, b) if x != y]
                conflicts[",".join(diff)] += 1
                if len(conflict_examples) < 10: conflict_examples.append({"cik": cik, "acc": acc, seen[k][0]: a, name: b})
            elif k not in seen:
                seen[k] = (name, row)

# ---------------- C0 denominator (exact replica of c0_census.py logic) ----------------
gdir = RT / "strategy_c/raw/grouped"
sessions = sorted(date.fromisoformat(f[:10]) for f in os.listdir(gdir)); S = len(sessions)
closes = []
symset = set()
for d in sessions:
    b = gz(gdir / f"{d}.json.gz"); x = b.get("body") or b
    r = x.get("results") or []
    closes.append({row["T"]: row.get("c") for row in r})
snaps = []
for f in sorted(os.listdir(RT / "strategy_c/raw/tickers")):
    b = gz(RT / "strategy_c/raw/tickers" / f)
    snaps.append((date.fromisoformat(f[3:13]), {x["ticker"]: (x.get("cik") or "").zfill(10) for x in b["results"] if x.get("type") == "CS"}))
snap_dates = [a for a, _ in snaps]
def snap_for(d):
    k = bisect_right(snap_dates, d) - 1; return snaps[max(k, 0)][1]
def shares_at(cik, d):
    sh = (c0_extract.get(cik) or {}).get("shares")
    if not sh: return None
    ds = d.isoformat(); best = None
    for filed, v in sh:
        if filed <= ds: best = (filed, v)
        else: break
    if best is None or (d - date.fromisoformat(best[0])).days > 400: return None
    return best[1]
jmax = sessions.index(date(2026, 10, 9))
denom = []  # (j, symbol, cik)
for j in range(0, jmax + 1, 5):
    snap = snap_for(sessions[j])
    for sym, c in closes[j].items():
        if c is None or not np.isfinite(c) or c < 5: continue
        cik = snap.get(sym)
        if cik is None: continue
        sh = shares_at(cik, sessions[j])
        if sh is None or sh * c < 300e6: continue
        denom.append((j, sym, cik))

# C0 coverage as computed then: from c0 extract (first-found root, recent earliest, presence of e202 key)
def c0_cov(j, cik):
    e = c0_extract.get(cik) or {}
    return "e202" in e and e.get("earliest") and e["earliest"] <= sessions[j].isoformat()
c0_n = sum(1 for j, s, c in denom if c0_cov(j, c))

# cumulative union coverage, two rules
def d1_cutoff_utc(j):
    nxt = sessions[j + 1] if j + 1 < S else sessions[j] + timedelta(days=1)
    return datetime.combine(nxt, time(9, 15), ET).astimezone(UTC).isoformat()
union = {}  # cik -> {"earliest", "latest_fetch"}
increments = []
for name, _ in SOURCES:
    for cik, e in per_source[name].items():
        fds = [r[3] for r in e["filings"].values() if r[3]]
        if not fds: continue
        u = union.setdefault(cik, {"earliest": None, "fetch": None})
        mn = min(fds); u["earliest"] = mn if u["earliest"] is None or mn < u["earliest"] else u["earliest"]
        f = max(e["fetched"]) if e["fetched"] else None
        if f and (u["fetch"] is None or f > u["fetch"]): u["fetch"] = f
    lower = strict = 0; covered_syms = set(); covered_syms_strict = set()
    for j, s, c in denom:
        u = union.get(c)
        if u and u["earliest"] <= sessions[j].isoformat():
            lower += 1; covered_syms.add(s)
            if u["fetch"] and u["fetch"].replace("Z", "+00:00") >= d1_cutoff_utc(j):
                strict += 1; covered_syms_strict.add(s)
    increments.append({"after_adding": name, "covered_c0_rule": lower, "pct_c0_rule": lower / len(denom),
                       "covered_strict_fetch_after_window": strict, "pct_strict": strict / len(denom),
                       "unique_covered_symbols": len(covered_syms), "ciks_in_union": len(union)})
    print(increments[-1], flush=True)

# ---------------- item 2.02 parser validation ----------------
tok = Counter(); fmt = Counter(); fp_examples = []
c0_rule = lambda items: "2.02" in (items or "").split(",")
strict_rule = lambda items: "2.02" in [t.strip() for t in re.split(r"[,\s;]+", items or "") if t.strip()]
disagree = 0; n8k = 0
for cik, (src, row) in ((k[0], v) for k, v in seen.items()):
    form, items = row[0], row[1]
    if form not in ("8-K", "8-K/A"): continue
    n8k += 1
    if items is None or items == "": fmt["EMPTY"] += 1
    elif re.fullmatch(r"\d+\.\d{2}(,\d+\.\d{2})*", items): fmt["CANONICAL_COMMA"] += 1
    else: fmt["OTHER"] += 1; fp_examples.append(items) if len(fp_examples) < 10 else None
    for t in (items or "").split(","): tok[t] += 1
    if c0_rule(items) != strict_rule(items): disagree += 1
substring_traps = {t: c for t, c in tok.items() if "2.02" in t and t != "2.02"}

# samples for manual comparison: AMD + header-verified filings + random 8-K 2.02
samples = []
hdr_dir = RT / "strategy_c/e0/raw/acceptance_headers"
hdr_check = []
for p in sorted(hdr_dir.glob("*.txt.gz")):
    t = gzip.decompress(p.read_bytes()).decode("latin-1")
    acc = re.search(r"<ACCESSION-NUMBER>(\S+)", t).group(1); cikh = re.search(r"<CIK>(\d+)", t).group(1).zfill(10)
    adt = re.search(r"<ACCEPTANCE-DATETIME>(\d{14})", t).group(1); items_h = re.findall(r"<ITEMS>(\S+)", t)
    form_h = re.search(r"<TYPE>(\S+)", t).group(1)
    k = (cikh, acc)
    js = seen.get(k)
    ok_time = ok_items = None
    if js:
        row = js[1]
        et_hdr = datetime.strptime(adt, "%Y%m%d%H%M%S").replace(tzinfo=ET)
        et_js = datetime.fromisoformat(row[2].replace("Z", "+00:00")).astimezone(ET) if row[2] else None
        ok_time = et_js is not None and abs((et_hdr - et_js).total_seconds()) < 1
        ok_items = sorted(items_h) == sorted(x for x in row[1].split(",") if x)
        ok_form = form_h == row[0]
        hdr_check.append({"cik": cikh, "acc": acc, "form_hdr": form_h, "form_json": row[0], "items_hdr": ",".join(items_h), "items_json": row[1],
                          "accept_hdr_ET": et_hdr.isoformat(), "accept_json_as_UTC_to_ET": None if et_js is None else et_js.isoformat(),
                          "time_match": ok_time, "items_match": ok_items, "form_match": ok_form,
                          "c0_rule_2_02": c0_rule(row[1]), "hdr_has_2_02": "2.02" in items_h})
amd = [(acc, row) for (c, acc), (src, row) in seen.items() if c == "0000002488" and row[0] == "8-K" and c0_rule(row[1])]
amd = sorted(amd, key=lambda x: x[1][2])[-8:]

# ---------------- PIT checks ----------------
pit = Counter(); pit_ex = []
for (cik, acc), (src, row) in seen.items():
    form, items, adt, fd = row
    if form != "8-K" or not c0_rule(items): continue
    if not adt: pit["NO_ACCEPTANCE"] += 1; continue
    t = datetime.fromisoformat(adt.replace("Z", "+00:00")).astimezone(ET)
    lag = (date.fromisoformat(fd) - t.date()).days
    if lag < 0: pit["FILINGDATE_BEFORE_ACCEPTANCE"] += 1; pit_ex.append((cik, acc, adt, fd))
    elif lag > 4: pit["FILINGDATE_GT4D_AFTER_ACCEPTANCE"] += 1; pit_ex.append((cik, acc, adt, fd))
    else: pit["CONSISTENT"] += 1

res = {"inventory": inv, "conflicts_same_accession": dict(conflicts), "conflict_examples": conflict_examples,
       "denominator": {"symbol_sessions": len(denom), "unique_symbols": len({s for _, s, _ in denom}),
                       "unique_ciks": len({c for _, _, c in denom}), "sampling": "every 5th grid session (C0 f2_coverage)"},
       "c0_reproduction": {"covered": c0_n, "pct": c0_n / len(denom), "c0_reported": [89005, 245149, 0.3630649115435919],
                           "unique_covered_symbols": len({s for j, s, c in denom if c0_cov(j, c)})},
       "increments": increments,
       "item_parser": {"8k_filings_union": n8k, "items_format": dict(fmt), "non_canonical_examples": fp_examples,
                       "substring_trap_tokens": substring_traps, "c0_vs_whitespace_tolerant_disagree": disagree,
                       "header_crosscheck": hdr_check, "amd_recent_2_02": amd},
       "pit": {"counts": dict(pit), "examples": pit_ex[:10]}}
(OUT / "f2_recovery_results.json").write_text(json.dumps(res, indent=1, default=str))
print(json.dumps({k: res[k] for k in ("denominator", "c0_reproduction", "increments", "conflicts_same_accession")}, indent=1, default=str))
