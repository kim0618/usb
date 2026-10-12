"""DEBUG-ONLY preview feeder (not production code). Replays ONE cached one-shot production snapshot
into a local collector root so the local V3 API reads it as current: written_ms/sample_ms are moved
to now every second (the collector's READ_STALE_MS floor is 3 s). The structure snapshot's
bar_close_ms is moved likewise. Every value other than the clock fields is byte-identical to the
cached file. Usage: replay_feeder.py LATEST.json STRUCTURE.json ROOT STRUCTURE_OUT [--once]
"""
import json, os, sys, time
latest_path, structure_path, root, structure_out = sys.argv[1:5]
once = "--once" in sys.argv
latest = json.load(open(latest_path)); structure = json.load(open(structure_path))
os.makedirs(os.path.join(root, "state"), exist_ok=True)
def put(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(obj, f)
    os.replace(tmp, path)
while True:
    now = int(time.time() * 1000)
    shift = now - latest["written_ms"]
    l = dict(latest); l["written_ms"] = now; l["sample_ms"] = latest["sample_ms"] + shift
    l["_preview_replay_of_written_ms"] = latest["written_ms"]
    put(os.path.join(root, "state", "context_latest.json"), l)
    s = dict(structure)
    sshift = (now // 60000) * 60000 - structure["bar_close_ms"]
    s["bar_close_ms"] = structure["bar_close_ms"] + sshift; s["bar_open_ms"] = structure["bar_open_ms"] + sshift
    s["written_ms"] = now; s["_preview_replay_of_bar_close_ms"] = structure["bar_close_ms"]
    put(structure_out, s)
    if once: break
    time.sleep(1)
