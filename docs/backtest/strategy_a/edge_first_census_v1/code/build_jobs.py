"""C0 candidates -> C1 V2 jobs. No outcome is read here. python build_jobs.py <minute_census.json> <outdir>"""
import csv, gzip, hashlib, json, os, sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

REP = Path.home() / "usb/data/runtime/research_reports/a_edge_first_census_v1"
GROUPED = Path.home() / "usb/data/runtime/strategy_c/raw/grouped"
C0_CSV_SHA = "303be9f236544b6f2c598c86d69d4e58f5f471741d36f01400ab80dd8f9ce9d8"
COVER = ("2026-04-20", "2026-09-16")

census = json.load(open(sys.argv[1])); out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
raw = (REP / "c0_candidates.csv").read_bytes()
assert hashlib.sha256(raw).hexdigest() == C0_CSV_SHA, "C0 candidate file changed"
grid = sorted(f[:10] for f in os.listdir(GROUPED)); nxt = {grid[i]: grid[i + 1] for i in range(len(grid) - 1)}
covered = {d for d in grid if COVER[0] <= d <= COVER[1]}
rows = [r for r in csv.DictReader(raw.decode().splitlines()) if r["family"] in ("F1", "F3")]
cons = {f: Counter() for f in ("F1", "F3")}
jobs = defaultdict(list); missing = []
for r in rows:
    f, D, sym = r["family"], r["date"], r["symbol"]; cons[f]["c0_total"] += 1
    E = nxt.get(D)
    if not (D in covered and E in covered):
        cons[f]["outside_c1_window"] += 1; continue
    cons[f]["c1_window"] += 1
    m = census.get(sym)
    if m is None:
        cons[f]["minute_missing_symbol"] += 1; missing.append((f, D, sym, "NO_SYMBOL_FOLDER")); continue
    if not m["reg"].get(D):
        cons[f]["minute_missing_D"] += 1; missing.append((f, D, sym, "NO_D_REGULAR")); continue
    if not m["days"].get(E):
        cons[f]["minute_missing_E"] += 1; missing.append((f, D, sym, "NO_E_BARS")); continue
    cons[f]["c1_eligible"] += 1
    E1 = nxt.get(E)
    jobs[sym].append({"family": f, "D": D, "E": E, "E1": E1 if E1 in covered else None})
# daily denominator rows from grouped (Massive daily aggregate = V2 basis)
need = defaultdict(set)
for sym, cs in jobs.items():
    for c in cs:
        e = date.fromisoformat(c["E"])
        for d in grid:
            if e - timedelta(days=60) <= date.fromisoformat(d) < e: need[d].add(sym)
daily = defaultdict(dict)
for d in sorted(need):
    b = json.loads(gzip.decompress((GROUPED / f"{d}.json.gz").read_bytes())); x = b.get("body") or b
    for row in x.get("results") or []:
        if row["T"] in need[d]:
            daily[row["T"]][d] = [row["o"], row["h"], row["l"], row["c"], row["v"]]
joblist = [{"symbol": s, "candidates": cs, "daily": daily.get(s, {})} for s, cs in sorted(jobs.items())]
json.dump(joblist, open(out / "jobs.json", "w"))
json.dump({"consistency": cons, "minute_missing": missing, "symbols": len(joblist),
           "candidates": sum(len(j["candidates"]) for j in joblist)}, open(out / "c0_c1_consistency.json", "w"), indent=1)
print(json.dumps(cons, indent=1)); print("symbols", len(joblist))
