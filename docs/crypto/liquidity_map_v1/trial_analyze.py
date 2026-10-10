"""Read a harness run's journal and report the facts from it, not from the collector.

V1.5 added the sections below the V1.4 ones: what happened to every REST response and who owned
it, the coverage series computed from `derived` rather than from a counter, and the one check that
names the incident V1.5 closed - an install whose snapshot id was *behind* the chain position the
book had already reached.
"""
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


# --------------------------------------------------------------------------- V1.5

print("\n--- V1.5 snapshot responses, by disposition ---")
snapshots = [payload(r) for r in rows("snapshot")]
by_disposition = {}
for s in snapshots:
    key = s.get("response_disposition") or "UNRECORDED"
    by_disposition[key] = by_disposition.get(key, 0) + 1
print(" reads=%d  %s" % (len(snapshots), json.dumps(by_disposition)))
for s in snapshots:
    print(" %-12s %-14s purpose=%-14s attempt=%-12s age=%-6s state=%s" % (
        s.get("snapshot_request_id"), s.get("response_disposition"), s.get("purpose"),
        s.get("refresh_attempt_id"), s.get("response_age_ms"), s.get("request_state")))

discarded = [payload(r) for r in tel if payload(r).get("event") == "snapshot_discarded"]
print("\n--- V1.5 discarded responses (none of these touched the live book) ---")
print(" count=%d" % len(discarded))
for d in discarded:
    print(" %-12s %-22s closed=%-34s age=%-6s snap=%-14s live=%-14s rollback_avoided=%s" % (
        d.get("snapshot_request_id"), d.get("response_disposition"), d.get("closed_reason"),
        d.get("response_age_ms"), d.get("snapshot_update_id"), d.get("live_last_update_id"),
        d.get("would_have_rolled_back_ids")))

ineffective = [payload(r) for r in tel if payload(r).get("event") == "refresh_ineffective"]
print("\n--- V1.5 refreshes that installed and still could not cover the band ---")
print(" count=%d" % len(ineffective))
for i in ineffective:
    print(" margin=%s trigger=%s generation=%s" % (
        i.get("margin_bps"), i.get("trigger_bps"), i.get("generation")))

# The incident signature, straight from the journal: an install whose snapshot id sits behind
# the chain position the book had already reached. One of these is the 141,000-id rollback.
print("\n--- V1.5 install-behind-chain check (the V1.4 defect's signature) ---")
derived = [payload(r) for r in rows("derived")]
seen_u, behind = None, 0
timeline = []
for r in rows("telemetry") + rows("derived"):
    p = payload(r)
    if p.get("event") == "resync":
        timeline.append(("resync", p))
for kind, p in timeline:
    snap = p.get("snapshot_update_id")
    if seen_u is not None and isinstance(snap, int) and snap < seen_u and not p.get("staged"):
        behind += 1
        print(" BEHIND resync snapshot_update_id=%s < previous last_update_id=%s" % (snap, seen_u))
    last = p.get("last_update_id")
    if isinstance(last, int):
        seen_u = last if seen_u is None else max(seen_u, last)
print(" installs behind the chain: %d" % behind)

print("\n--- V1.5 coverage series, computed from derived ---")
margins, complete, total = [], 0, 0
for d in derived:
    book = d.get("book") or {}
    mid, low, high = book.get("mid"), book.get("known_low"), book.get("known_high")
    if book.get("state") == "SYNCED" and mid and low and high:
        mid_f, low_f, high_f = float(mid), float(low), float(high)
        if mid_f > 0:
            reach = min((mid_f - low_f) / mid_f, (high_f - mid_f) / mid_f) * 10_000
            margins.append(reach - 10.0)
    for band in book.get("bands") or []:
        if str(band.get("band_pct")) != "0.1":
            continue
        for side in ("bid", "ask"):
            total += 1
            complete += 1 if (band.get(side) or {}).get("coverage") == "COMPLETE" else 0
if margins:
    ordered = sorted(margins)
    negative = [m for m in margins if m < 0]
    runs, run = [], 0
    for m in margins:
        if m < 0:
            run += 1
        elif run:
            runs.append(run); run = 0
    if run:
        runs.append(run)
    print(" samples=%d min=%.2f p05=%.2f median=%.2f max=%.2f" % (
        len(margins), ordered[0], ordered[int(0.05 * len(ordered))],
        ordered[len(ordered) // 2], ordered[-1]))
    print(" below trigger (1.0 bp): %.2f%%   negative: %.2f%% (%d samples, longest run %ds)" % (
        100.0 * sum(1 for m in margins if m < 1.0) / len(margins),
        100.0 * len(negative) / len(margins), len(negative), max(runs) if runs else 0))
if total:
    print(" +-0.1%% band COMPLETE: %d/%d = %.2f%%" % (complete, total, 100.0 * complete / total))

print("\n--- V1.5 coverage floor between installed coverage refreshes ---")
coverage_applied = [a for a in applied if a.get("reason") == "coverage_edge"]
stamps = [a.get("swapped_ms") for a in coverage_applied if a.get("swapped_ms")]
gaps_s = [round((b - a) / 1000.0, 1) for a, b in zip(stamps, stamps[1:])]
print(" installed coverage refreshes=%d  intervals_s=%s  min=%s  floor=10.0" % (
    len(coverage_applied), gaps_s, min(gaps_s) if gaps_s else None))
