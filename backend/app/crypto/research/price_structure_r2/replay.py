"""Price Structure R2 replay: the equivalence blocker, the eight variants, the frozen rule.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.price_structure_r2.replay

Order of operations is the contract's and it matters:

1. contract section 2.1, the equivalence blocker. ``SWING_ONLY`` must reproduce R1's frozen metric
   payload byte for byte. Nothing else runs if it does not.
2. contract section 8, the four required variants plus the four remaining subsets, all scored by
   R1's imported metric suite in one process.
3. contract section 9, the admission rule, applied to the four required variants only.
4. the final combination, scored once. Prefix truncation, determinism, then the verdict.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..price_structure_r0 import calibrate, data
from ..price_structure_r0.data import Bars, aggregate, wilder_atr
from ..price_structure_r1 import metrics as m1
from ..price_structure_r1.replay import CUT_POINTS
from . import metrics
from .engine import Engine, EngineConfig

OUT = Path(__file__).resolve().parents[5] / "data/research/crypto/price_structure_r2"
R1_METRICS = Path(__file__).resolve().parents[5] / \
    "data/research/crypto/price_structure_r1/metrics_r1.json"
OVERLAY_DAYS = 10
DAY_MS = 86_400_000
MINUTE_MS = 60_000

FAMILY_FLAGS = {"S1": "enable_d", "S2": "enable_range", "S3": "enable_flip"}
REQUIRED_VARIANTS = [(), ("S1",), ("S2",), ("S3",)]
DIAGNOSTIC_VARIANTS = [("S1", "S2"), ("S1", "S3"), ("S2", "S3"), ("S1", "S2", "S3")]


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def cfg_for(families: tuple, theta: tuple, frm: int, to: int) -> EngineConfig:
    kw = {FAMILY_FLAGS[f]: True for f in families}
    return EngineConfig(theta[0], theta[1], frm, to, **kw)


def build(bars: dict, families: tuple, theta: tuple,
          frm: int = data.REPLAY_START_MS, to: int = data.REPLAY_END_MS) -> Engine:
    eng = Engine(bars, cfg_for(families, theta, frm, to))
    eng.run()
    return eng


def prefix_truncation(bars: dict, families: tuple, theta: tuple) -> dict:
    """Contract section 10, at R1's six cut points, on the final combination."""
    b1 = bars["1m"]
    rows = []
    for T in CUT_POINTS:
        cut = int((T - int(b1.ts[0])) // MINUTE_MS)
        sl = lambda x: x[:cut]
        t1 = Bars("1m", MINUTE_MS, sl(b1.ts), sl(b1.open), sl(b1.high), sl(b1.low), sl(b1.close),
                  sl(b1.volume), sl(b1.turnover),
                  wilder_atr(sl(b1.high), sl(b1.low), sl(b1.close)))
        trunc = {"1m": t1, "5m": aggregate(t1, "5m", 5), "1h": aggregate(t1, "1h", 60)}
        a = build(trunc, families, theta, T, T)
        b = build(bars, families, theta, T, T)
        sa = a.hourly_snapshots[-1] if a.hourly_snapshots else None
        sb = b.hourly_snapshots[-1] if b.hourly_snapshots else None
        same = sa == sb
        diff = [] if same else sorted(k for k in (sa or {}) if (sa or {}).get(k) != (sb or {}).get(k))
        rows.append({"T": _iso(T), "T_ms": T, "identical": bool(same),
                     "fields_compared": len(sa or {}), "differing_fields": diff,
                     "publishable_levels": (sa or {}).get("active_level_count"),
                     "book_levels": (sa or {}).get("book_level_count"),
                     "published": len((sa or {}).get("published_levels", [])),
                     "retests_open": (sa or {}).get("retests_open"),
                     "publishable_by_source": (sa or {}).get("publishable_by_source")})
    return {"cut_points": len(rows), "identical": sum(1 for r in rows if r["identical"]),
            "rows": rows}


def _first_shown_at(eng: Engine) -> dict:
    """When a level first became publishable. Majors are born CONFIRMED and log no transition."""
    out: dict = {}
    for t in eng.book.transitions:
        if t["to"] == "CONFIRMED" and t["id"] not in out:
            out[t["id"]] = t["at"]
    for l in eng.book.levels.values():
        if l.id not in out and (l.major or not eng.cfg.require_touch_to_publish):
            out[l.id] = l.created_at
    return out


def _vwap_at_5m(bars: dict, want_ts: list) -> dict:
    from ..price_structure_r0.vwap import SessionVwap
    b1, v, out = bars["1m"], SessionVwap(), {}
    want = set(want_ts)
    for i in range(len(b1)):
        ts = int(b1.ts[i])
        v.on_1m(ts, float(b1.high[i]), float(b1.low[i]), float(b1.close[i]),
                float(b1.volume[i]), float(b1.turnover[i]))
        close_ms = ts + 60_000
        if close_ms % 300_000 == 0 and (close_ms - 300_000) in want:
            out[close_ms - 300_000] = v.vwap
    return out


def build_overlay(eng: Engine, bars: dict) -> dict:
    b5 = bars["5m"]
    start = data.REPLAY_END_MS - OVERLAY_DAYS * DAY_MS
    idx = [i for i in range(len(b5)) if start <= int(b5.ts[i]) < data.REPLAY_END_MS]
    vw = _vwap_at_5m(bars, [int(b5.ts[i]) for i in idx])
    candles = [{"t": int(b5.ts[i]) // 1000, "o": float(b5.open[i]), "h": float(b5.high[i]),
                "l": float(b5.low[i]), "c": float(b5.close[i]),
                "vwap": round(vw.get(int(b5.ts[i]), float("nan")), 2)} for i in idx]
    shown = _first_shown_at(eng)
    levels = []
    for l in eng.book.levels.values():
        end = l.expired_at or data.REPLAY_END_MS
        frm = shown.get(l.id)
        if frm is None or end < start or frm >= data.REPLAY_END_MS:
            continue
        levels.append({"id": l.id, "tf": l.tf, "price": round(l.price, 2),
                       "origin_kind": l.origin_kind, "from": frm, "to": end,
                       "created_at": l.created_at, "provenance": l.provenance,
                       "sources": sorted(l.sources), "lifecycle": l.state, "major": l.major,
                       "expired": l.expired_at is not None, "touches": l.touches,
                       "merges": l.merges, "breaks": l.breaks, "flips": l.flips})
    sw = [{"tf": s.tf, "kind": s.kind, "price": round(s.price, 2), "pivot_at": s.pivot_at,
           "confirmed_at": s.confirmed_at, "label": s.label}
          for s in eng.zz_h.confirmed + eng.zz_5.confirmed if s.confirmed_at >= start]
    bos = [b.immutable() | {"level_price": round(b.level_price, 2)} for b in eng.ev.breakouts
           if b.confirmed_at >= start]
    sws = [s.immutable() | {"level_price": round(s.level_price, 2)} for s in eng.ev.sweeps
           if s.confirmed_at >= start]
    rts = [{"breakout_id": r.breakout.id, "level_id": r.breakout.level_id,
            "level_price": round(r.breakout.level_price, 2),
            "direction": r.breakout.direction, "state": r.state, "reason": r.reason,
            "confirmed_at": r.breakout.confirmed_at, "terminated_at": r.terminated_at}
           for r in eng.ev.retests if r.breakout.confirmed_at >= start]
    track = [{"info_ts": s["info_ts"], "close": s["close"], "trend": s["trend_structure"],
              "range_state": s["range_state"], "range_high": s["range_high"],
              "range_low": s["range_low"], "vwap": s["vwap"], "vwap_position": s["vwap_position"],
              "retest_state": s["retest_state"], "retests_open": s["retests_open"],
              "retests": s["retests"], "failed_break_state": s["failed_break_state"],
              "nearest_resistance": (s["nearest_resistance"] or {}).get("price"),
              "nearest_support": (s["nearest_support"] or {}).get("price"),
              "published": [{"price": p["price"], "tf": p["tf"], "provenance": p["provenance"],
                             "lifecycle": p["lifecycle"], "origin_kind": p["origin_kind"],
                             "sources": p["sources"]} for p in s["published_levels"]],
              "active_level_count": s["active_level_count"],
              "book_level_count": s["book_level_count"],
              "provisional_count": s["provisional_count"],
              "publishable_by_source": s["publishable_by_source"]}
             for s in eng.hourly_snapshots if s["info_ts"] >= start]
    return {"symbol": "BTCUSDT", "tf": "5m", "variant": eng.cfg.variant,
            "window": {"from": start, "to": data.REPLAY_END_MS},
            "candles": candles, "levels": levels, "swings": sw, "breakouts": bos, "sweeps": sws,
            "retests": rts, "range_log": [r for r in eng.rng.log if r["at"] >= start],
            "day_objects": [d for d in eng.day_objects if d["at"] >= start],
            "range_objects": [d for d in eng.range_objects if d["at"] >= start],
            "flip_claims": [f for f in eng.flip_claims if f["at"] >= start],
            "track": track}


def run() -> dict:
    cal = calibrate.run()
    theta = (cal["chosen"]["1h"]["theta"], cal["chosen"]["5m"]["theta"])
    bars = data.load()

    # 1. contract section 2.1, the blocker
    base_eng = build(bars, (), theta)
    base = m1.score(base_eng)
    r1_ref = json.loads(R1_METRICS.read_text())["R1"]
    equivalent = json.dumps(base, sort_keys=True) == json.dumps(r1_ref, sort_keys=True)
    blocker = {"swing_only_reproduces_r1": equivalent,
               "r1_reference": str(R1_METRICS.relative_to(R1_METRICS.parents[4])),
               "r1_verdict_rescored": list(m1.verdict(base)),
               "differing_blocks": ([] if equivalent else
                                    sorted(k for k in set(base) | set(r1_ref)
                                           if json.dumps(base.get(k), sort_keys=True)
                                           != json.dumps(r1_ref.get(k), sort_keys=True)))}
    if not equivalent:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "metrics_r2.json").write_text(json.dumps(
            {"equivalence_blocker": blocker,
             "verdict": "BLOCKED_IMPLEMENTATION_DEFECT"}, indent=2) + "\n")
        raise SystemExit("contract section 2.1 blocker: SWING_ONLY does not reproduce R1")

    # 2. contract section 8, every subset
    base_cases = metrics.m4_cases_with_match(base_eng)
    variants: dict = {"SWING_ONLY": {"families": [], "core": base, "extras": metrics.extras(base_eng),
                                     "verdict": list(m1.verdict(base)), "role": "baseline"}}
    engines = {"SWING_ONLY": base_eng}
    for fam in REQUIRED_VARIANTS[1:] + DIAGNOSTIC_VARIANTS:
        eng = build(bars, fam, theta)
        core = m1.score(eng)
        name = eng.cfg.variant
        engines[name] = eng
        variants[name] = {
            "families": list(fam), "core": core, "extras": metrics.extras(eng),
            "verdict": list(m1.verdict(core)),
            "cost_vs_swing_only": metrics.cost_delta(base, core),
            "m4_attribution": metrics.m4_attribution(base_cases,
                                                     metrics.m4_cases_with_match(eng)),
            "role": "required" if len(fam) == 1 else "diagnostic",
        }

    # 3. contract section 9, admission, from the four required variants only
    admission = {}
    for fam in ("S1", "S2", "S3"):
        name = f"SWING_{fam}"
        admission[fam] = metrics.admission(base, variants[name]["core"]) | {"variant": name}
    admitted = tuple(f for f in ("S1", "S2", "S3") if admission[f]["admitted"])

    # 4. the final combination, scored once
    final_name = Engine(bars, cfg_for(admitted, theta, 0, 0)).cfg.variant
    if final_name in engines:
        final_eng = engines[final_name]
        final_core = variants[final_name]["core"]
    else:
        final_eng = build(bars, admitted, theta)
        final_core = m1.score(final_eng)
    v, why = m1.verdict(final_core)
    prefix = prefix_truncation(bars, admitted, theta)
    again = build(bars, admitted, theta)
    deterministic = json.dumps(m1.score(again), sort_keys=True) == json.dumps(final_core,
                                                                             sort_keys=True)
    failing = [w.split()[0] for w in why]
    m4_only = failing == ["M4"] or (v == "A_NOT_USABLE" and failing == ["M4"])

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "contract": json.loads((OUT / "contract_freeze_r2.json").read_text()),
        "provenance": data.provenance(), "d2_crosscheck": data.crosscheck_d2(),
        "calibration": cal["chosen"],
        "window": {"warmup_from": data.WARMUP_START_MS, "replay_from": data.REPLAY_START_MS,
                   "replay_to": data.REPLAY_END_MS, "snapshots": final_core["snapshots"]},
        "equivalence_blocker": blocker,
        "admission": admission,
        "admitted_families": list(admitted),
        "final_combination": final_name,
        "verdict": ("C_SOURCE_COVERAGE_STILL_INSUFFICIENT"
                    if v == "C_NEEDS_NEW_LEVEL_SOURCE" else v),
        "verdict_raw_from_r1_function": v,
        "verdict_reasons": why,
        "failing_axes": failing,
        "m4_is_the_only_failing_axis": m4_only,
        "prefix_truncation": prefix, "deterministic_two_runs": deterministic,
        "final": {"core": final_core, "extras": metrics.extras(final_eng),
                  "cost_vs_swing_only": metrics.cost_delta(base, final_core),
                  "m4_attribution": metrics.m4_attribution(
                      base_cases, metrics.m4_cases_with_match(final_eng))},
        "variants": variants,
        "m4_cases_swing_only": base_cases,
        "m4_cases_final": metrics.m4_cases_with_match(final_eng),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "metrics_r2.json").write_text(json.dumps(result, indent=2) + "\n")
    (OUT / "prefix_truncation_r2.json").write_text(json.dumps(prefix, indent=2) + "\n")
    summary = {"verdict": result["verdict"], "failing_axes": failing,
               "admitted": list(admitted), "final_combination": final_name,
               "table": {n: {"M1": d["core"]["M1_repaint"]["total"],
                             "M2_med": d["core"]["M2_active_levels"]["publishable"]["median"],
                             "M2_p95": d["core"]["M2_active_levels"]["publishable"]["p95"],
                             "M3_share": d["core"]["M3_duplicate_density"]["dup_set_share"],
                             "M4": d["core"]["M4_coverage"]["coverage"],
                             "M4_hits": d["core"]["M4_coverage"]["hits"],
                             "M4_book": d["core"]["M4_coverage"]["diagnostic_coverage_whole_book"],
                             "M5": d["core"]["M5_liveness"]["fraction"],
                             "M6_med": d["core"]["M6_event_rate"]["breakouts_per_day_median"],
                             "levels_created": d["core"]["counts"]["levels_created"],
                             "verdict": d["verdict"][0]}
                         for n, d in variants.items()}}
    (OUT / "variant_table_r2.json").write_text(json.dumps(summary, indent=2) + "\n")
    with open(OUT / "snapshots_hourly_r2.jsonl", "w") as fh:
        for s in final_eng.hourly_snapshots:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    with open(OUT / "events_r2.jsonl", "w") as fh:
        for e in final_eng.events_log:
            fh.write(json.dumps(e, sort_keys=True) + "\n")
    with open(OUT / "level_transitions_r2.jsonl", "w") as fh:
        for t in final_eng.book.transitions:
            fh.write(json.dumps(t, sort_keys=True) + "\n")
    (OUT / "overlay_r2.json").write_text(json.dumps(build_overlay(final_eng, bars)) + "\n")
    (OUT / "range_log_r2.json").write_text(json.dumps(final_eng.rng.log, indent=2) + "\n")
    return result


if __name__ == "__main__":
    r = run()
    print("equivalence blocker, SWING_ONLY == R1:", r["equivalence_blocker"]["swing_only_reproduces_r1"])
    print(json.dumps(json.loads((OUT / "variant_table_r2.json").read_text()), indent=2))
    print("admission:", json.dumps({k: v["failed"] or "ADMITTED" for k, v in r["admission"].items()}))
    print("final:", r["final_combination"], "verdict:", r["verdict"], r["verdict_reasons"])
    print("prefix identical:", r["prefix_truncation"]["identical"], "/",
          r["prefix_truncation"]["cut_points"], " deterministic:", r["deterministic_two_runs"])
