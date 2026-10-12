"""Reads JSON lines {"ctx": <context_latest>, "snap": <structure_snapshot>} from stdin (one ssh
connection streaming production files read-only) and writes them atomically to the local preview
root, where the local V2 API reads them."""
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
    if n % 20 == 1: print("fed", n, flush=True)
