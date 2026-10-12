"""Series of one-shot production collector reads (write 0): per snapshot, walls per side with fields,
flow 60s, and a persistence summary across the series."""
import json, glob, os, sys, collections
R = sys.argv[1]
rows = []
for f in sorted(glob.glob(os.path.join(R, "series", "snap_*.json.raw"))):
    txt = open(f).read()
    if not txt.startswith("T="): continue
    t_line, _, body = txt.partition("\n")
    try: d = json.loads(body)
    except ValueError: continue
    rows.append((int(t_line[2:]), d))
print(f"snapshots: {len(rows)}  span: {rows[0][0]} .. {rows[-1][0]}  ({(rows[-1][0]-rows[0][0])/60000:.1f} min)")
print("ssh_T  | written_ms  | age_at_read | sess     | col  | liq  | ASK st sel shown pub | BID st sel shown pub | flow60 buy/sell % | mid")
seen = collections.defaultdict(list)
for t, d in rows:
    liq = d["liquidity"]; fl = d["flow"]; w = fl["windows"]["60s"]
    b, s = float(w["buy_usdt"]), float(w["sell_usdt"]); bp = round(b / (b + s) * 100) if b + s else None
    a, bd = liq["sides"]["ASK"], liq["sides"]["BID"]
    print(f"{t} | {d['written_ms']} | {t - d['written_ms']:>5} ms | {d['session_id'][:8]} | {d['collector']['state']:<4} | {liq['state']:<4} | "
          f"{a['wall_state']:<4} {a['walls_selected']:>3} {a['walls_shown']:>3} {len(a['walls']):>3} | "
          f"{bd['wall_state']:<4} {bd['walls_selected']:>3} {bd['walls_shown']:>3} {len(bd['walls']):>3} | "
          f"{fl['state']:<4}/{w['state']:<4} {bp}/{100-bp if bp is not None else None} | {liq['book']['mid']}")
    for side in ("ASK", "BID"):
        for wall in liq["sides"][side]["walls"]:
            seen[(side, wall["price"])].append((t, wall))
print("\n--- per-snapshot published walls (price, notional, rank, persistence_ms, own_persistence_ms, continuity, first_seen_ms, coverage) ---")
for t, d in rows[:: max(1, len(rows) // 6)]:
    print(f"T={t} written={d['written_ms']} sample={d['sample_index']} session={d['session_id']}")
    for side in ("ASK", "BID"):
        for i, wall in enumerate(d["liquidity"]["sides"][side]["walls"], 1):
            print(f"   {side} #{i} price={wall['price']} notional={float(wall['notional_usdt']):>12.0f} multiple={float(wall['multiple']):>7.1f} "
                  f"persist={wall['persistence_ms']} own={wall['own_persistence_ms']} cont={wall['continuity_status']} first_seen={wall['first_seen_ms']} "
                  f"cov={wall['coverage']} bin=[{wall['bin_low']},{wall['bin_high']}] values_as_of={wall['values_as_of']}")
print("\n--- walls by price across the series: in how many snapshots each published wall appeared ---")
for (side, price), hits in sorted(seen.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
    ns = [float(w["notional_usdt"]) for _, w in hits]
    print(f"{side} {price}: {len(hits)}/{len(rows)} snapshots, notional {min(ns):.0f}..{max(ns):.0f}, max persistence {max(w['persistence_ms'] for _, w in hits)} ms")
print("\n--- vanished (collector classification) at last snapshot ---")
van = rows[-1][1]["flow"]["vanished"]
print("totals", van["totals"], "classified", van["classified_totals"])
for item in van["recent"][:6]:
    print("  ", item["side"], item["price"], item["state"], item["end_class"], item.get("end_reason"), "touched", item["mid_path_touched_bin"], f"{float(item['notional_usdt']):.0f}")
print("\n--- fields present per wall ---", sorted(rows[-1][1]["liquidity"]["sides"]["ASK"]["walls"][0].keys()) if rows[-1][1]["liquidity"]["sides"]["ASK"]["walls"] else "no ask wall at last")
print("NOT published per wall: opened_ts (only first_seen_ms), size at open, status/consumed/cancel-like per wall (only in flow.vanished for ended walls), session_id (top-level only)")
