"""Measure the Drive minute mirror: per (symbol, ET date) bar counts, ledger/sha integrity. Read-only."""
import gzip, hashlib, json, os, re, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

M = "/mnt/g/내 드라이브/1_US-B/market_data/raw/massive/minute"
OUT = sys.argv[1]
ET = ZoneInfo("America/New_York")
T = re.compile(rb'"t":\s*(\d+)')


def one(sym):
    folder = os.path.join(M, sym)
    res = {"sym": sym, "days": {}, "reg": {}, "bad": [], "ledgers": []}
    try:
        names = os.listdir(folder)
    except OSError as e:
        res["bad"].append(f"LISTDIR:{e}")
        return res
    expected = {}
    for n in names:
        if n.endswith(".request.json"):
            try:
                led = json.load(open(os.path.join(folder, n)))
                res["ledgers"].append({k: led.get(k) for k in ("start", "end", "status", "rows", "requested_sessions", "sessions_with_bars")})
                for p in led.get("pages", []):
                    expected[p["file"]] = p.get("file_sha256")
            except Exception as e:
                res["bad"].append(f"LEDGER:{n}:{e}")
    days = defaultdict(set); reg = defaultdict(set)
    for n in names:
        if not n.endswith(".json.gz"):
            continue
        try:
            raw = open(os.path.join(folder, n), "rb").read()
            if expected.get(n) and hashlib.sha256(raw).hexdigest() != expected[n]:
                res["bad"].append(f"SHA_MISMATCH:{n}")
            body = gzip.decompress(raw)
        except Exception as e:
            res["bad"].append(f"READ:{n}:{type(e).__name__}")
            continue
        if n not in expected:
            res["bad"].append(f"NO_LEDGER_PAGE:{n}")
        for m in T.finditer(body):
            t = datetime.fromtimestamp(int(m.group(1)) / 1000, timezone.utc).astimezone(ET)
            d = t.date().isoformat(); hm = t.hour * 60 + t.minute
            days[d].add(hm)
            if 570 <= hm < 960:
                reg[d].add(hm)
    res["days"] = {d: len(v) for d, v in days.items()}
    res["reg"] = {d: len(v) for d, v in reg.items()}
    return res


if __name__ == "__main__":
    syms = sorted(os.listdir(M))
    out = {}
    with ProcessPoolExecutor(6) as ex:
        for k, r in enumerate(ex.map(one, syms, chunksize=8)):
            out[r["sym"]] = r
            if k % 500 == 0:
                print("done", k, len(syms), flush=True)
    json.dump(out, open(OUT, "w"))
    print("wrote", OUT)
