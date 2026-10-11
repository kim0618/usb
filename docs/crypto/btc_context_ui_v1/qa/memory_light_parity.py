"""Full R1 vs incremental parity on the frozen 53-day BTC 1m file. Usage: parity.py <backend_dir> <raw_jsonl>"""
import sys, json, pickle, random, time
sys.path.insert(0, sys.argv[1])
from app.crypto.btc_context_ui_v1 import structure_state as S
from app.crypto.market_context_v1 import structure as STR
from app.crypto.research.market_context_r0.structure import ContextEngine
from app.crypto.research.price_structure_r1.engine import EngineConfig
from app.crypto.market_context_v1 import contract as C
rows = [json.loads(l) for l in open(sys.argv[2])]
arr = S.rows_to_arrays(rows)
n = len(arr["ts"]); DAY = 1440
base = 30 * DAY                        # first build: 30 days, day aligned (file starts 00:00Z)
print("bars", n, "days", n / DAY)
# 1. bars_from_arrays == build_bars (31b6f2d construction)
a = S.bars_from_arrays(arr); b = STR.build_bars(rows)
for tf in a:
    for f in ("ts", "open", "high", "low", "close", "volume", "turnover", "atr"):
        import numpy as np
        assert np.array_equal(getattr(a[tf], f), getattr(b[tf], f), equal_nan=True), (tf, f)
print("bars_from_arrays == build_bars: OK")
# 2. full reference: one R1 run over everything, publishing every minute after the base
t = time.time()
ref = ContextEngine(a, EngineConfig(C.THETA_1H, C.THETA_5M, int(arr["ts"][base]) + 60_000, int(arr["ts"][-1]) + 60_000))
ref.run()
ref_rows = {r["info_ts"]: r for r in ref.rows}
print("full reference rows", len(ref_rows), "in", round(time.time() - t, 1), "s")
sub = lambda k: {key: v[:k] for key, v in arr.items()}
# 3. incremental, one minute at a time, with checkpoint restarts and multi-minute steps
eng = S.IncrementalEngine.build(S.bars_from_arrays(sub(base)), anchor_ms=int(arr["ts"][0]))
assert eng.rows[-1] == ref_rows.get(eng.rows[-1]["info_ts"], eng.rows[-1])
rng = random.Random(7)
k = base; compared = 0; restarts = 0; multi = 0; mismatches = []
while k < n:
    step = 1 if rng.random() < 0.9 else rng.choice([2, 3, 5, 17, 60, 241])
    k2 = min(n, k + step); multi += step > 1
    eng.step_to(S.bars_from_arrays(sub(k2)))
    for r in eng.rows[-(k2 - k):]:
        compared += 1
        if r != ref_rows[r["info_ts"]]:
            mismatches.append(r["info_ts"])
    k = k2
    if rng.random() < 0.003:
        eng = pickle.loads(pickle.dumps(eng, protocol=pickle.HIGHEST_PROTOCOL)); restarts += 1
print("incremental minutes compared", compared, "mismatches", len(mismatches), "multi-steps", multi, "checkpoint restarts", restarts)
print("audit violations full/incr", len(ref.audit.violations), len(eng.audit.violations), "vanished", len(ref.audit.vanished), len(eng.audit.vanished))
# 4. the 31b6f2d code path itself (Series.run over truncated dict rows) at sampled minutes
pts = sorted(rng.sample(range(base + 1, n + 1), 25)) + [n]
bad = 0
for p in pts:
    s = STR.Series("BTCUSDT", anchor_ms=rows[0]["timestamp_ms"]); s.rows = rows[:p]
    old = s.run(publish_window_ms=10 * 60_000).latest
    if old != ref_rows[old["info_ts"]]: bad += 1
print("31b6f2d Series.run latest == reference at", len(pts), "sampled minutes; mismatches", bad)
# 5. coverage: what happened inside the compared span
lo = int(arr["ts"][base]) + 60_000
kinds = {}
for e in ref.events_log:
    at = e.get("at", e.get("confirmed_at", 0)) or 0
    if at >= lo: kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
lv = [t for t in ref.book.transitions if t["at"] >= lo]
tr = {}
for t in lv: tr[t["to"]] = tr.get(t["to"], 0) + 1
days = len({r["info_ts"] // 86_400_000 for r in ref_rows.values()})
ids = set(); chg = 0; prev = None
for ts_ in sorted(ref_rows):
    r = ref_rows[ts_]; cur = (r["res_id"], r["sup_id"]); ids |= {x for x in cur if x}
    chg += prev is not None and cur != prev; prev = cur
closes = [ref_rows[t]["close"] for t in sorted(ref_rows)]
big = max(abs(closes[i + 60] / closes[i] - 1) for i in range(len(closes) - 60))
print("coverage: events", kinds, "level transitions", tr, "utc days", days,
      "distinct nearest ids", len(ids), "nearest-id changes", chg, "max 1h move %", round(big * 100, 2))
print("PARITY", "PASS" if not mismatches and not bad else "FAIL")
