"""Read a harness run's journal and report the V1.4 facts from it, not from the collector."""
import json, sys
from pathlib import Path

root = Path(sys.argv[1])

def rows(kind):
    out = []
    for f in sorted((root / kind).glob("*.jsonl*")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out

tel = rows("telemetry")
def ev(name):
    return [r for r in tel if (r.get("payload") or {}).get("event") == name
            or r.get("event") == name]

def payload(r):
    return r.get("payload") or r

wc = [payload(r) for r in tel if payload(r).get("event") == "wall_continuity"]
applied = [payload(r) for r in tel if payload(r).get("event") == "refresh_applied"]
# The collector publishes an abandoned staged attempt as `refresh_rejected`.
abandoned = [payload(r) for r in tel if payload(r).get("event") == "refresh_rejected"]
storm = [payload(r) for r in tel if payload(r).get("event") == "refresh_storm"]
gaps = [payload(r) for r in tel if payload(r).get("event") == "gap"]
resync = [payload(r) for r in tel if payload(r).get("event") == "resync"]
events = sorted({payload(r).get("event") for r in tel})

print("telemetry events present:", events)
print("gaps:", len(gaps), "| resyncs:", len(resync), "| applied:", len(applied),
      "| abandoned:", len(abandoned), "| storms:", len(storm))

print("\n--- refresh_applied (round trip, swap, replay) ---")
for a in applied:
    print(" reason=%-13s round_trip=%-6s elapsed=%-6s replayed=%-3s outcome=%s div=%s" % (
        a.get("reason"), a.get("round_trip_ms"), a.get("elapsed_ms"), a.get("replayed_frames"),
        a.get("outcome"), json.dumps(a.get("divergence"))))

print("\n--- wall_continuity CLASSIFIED ---")
for c in wc:
    if c.get("phase") != "CLASSIFIED":
        continue
    print(" type=%-4s reason=%-22s window=%-6s verdict=%-22s exempt=%-5s chain=%-5s replayed=%s"
          % (c.get("refresh_type"), c.get("continuity_reason"), c.get("window_ms"),
             c.get("window_verdict"), c.get("window_exempt"), c.get("chain_preserved"),
             c.get("replayed_frames")))

print("\n--- wall_continuity APPLIED (carry outcome) ---")
tot = {"carried": 0, "ended": 0, "unknown": 0, "before": 0}
for c in wc:
    if c.get("phase") != "APPLIED":
        continue
    before = c.get("candidates_before")
    carried, ended, unknown = c.get("wall_carried"), c.get("wall_ended"), c.get("wall_unknown")
    ok = (carried or 0) + (ended or 0) + (unknown or 0) == (before or 0)
    rate = None if not before else round(100.0 * (carried or 0) / before, 1)
    print(" type=%-4s before=%-4s carried=%-4s ended=%-4s unknown=%-4s sum_ok=%-5s carry=%s%%"
          % (c.get("refresh_type"), before, carried, ended, unknown, ok, rate))
    tot["carried"] += carried or 0; tot["ended"] += ended or 0
    tot["unknown"] += unknown or 0; tot["before"] += before or 0
if tot["before"]:
    print(" TOTAL before=%d carried=%d (%.1f%%) ended=%d unknown=%d" % (
        tot["before"], tot["carried"], 100.0 * tot["carried"] / tot["before"],
        tot["ended"], tot["unknown"]))

print("\n--- abandoned (refresh_rejected) ---")
for a in abandoned:
    print(" failure=%-28s elapsed=%-6s cooldown_consumed=%s backoff=%s failures=%s" % (
        a.get("failure") or a.get("outcome"), a.get("elapsed_ms"), a.get("cooldown_consumed"),
        a.get("retry_backoff_s") or a.get("backoff_s"), a.get("consecutive_failures")))

# rollback check straight from the checkpoint stream
cp = rows("checkpoint")
ids, gens, drops = [], [], 0
for r in cp:
    p = payload(r)
    i, g = p.get("last_update_id"), p.get("generation")
    if isinstance(i, int):
        if ids and i < ids[-1]:
            drops += 1
        ids.append(i)
    if isinstance(g, int):
        gens.append(g)
print("\ncheckpoints=%d  last_update_id decreases=%d  generation %s..%s" % (
    len(cp), drops, gens[0] if gens else None, gens[-1] if gens else None))
