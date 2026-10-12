"""Reads JSON lines {"ctx": ..., "snap": ...} from stdin (one ssh connection reading production files)
and writes them atomically where the local V3 API reads them."""
import json, os, sys
root, snap = sys.argv[1], sys.argv[2]
def put(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f)
    os.replace(tmp, path)
n = 0
for line in sys.stdin:
    try: d = json.loads(line)
    except ValueError: continue
    if d.get("ctx"): put(os.path.join(root, "state", "context_latest.json"), d["ctx"])
    if d.get("snap"): put(snap, d["snap"])
    n += 1
    if n % 60 == 1: print("fed", n, flush=True)
