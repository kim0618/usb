"""Price Structure R0 replay: runs the engine, computes section 13 metrics, applies section 14.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.price_structure_r0.replay
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import calibrate, data
from .engine import Engine, EngineConfig
from .events import TERMINAL

OUT = Path(__file__).resolve().parents[5] / "data/research/crypto/price_structure_r0"
MAJOR_REVERSAL_PCT = 2.0
COVERAGE_TOL_PCT = 0.25
OVERLAY_DAYS = 10
DAY_MS = 86_400_000
HOUR_MS = 3_600_000


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def m4_coverage(eng: Engine, hourly: dict[int, list[float]]) -> dict:
    swings = [s for s in eng.zz_h.confirmed if s.confirmed_at >= data.REPLAY_START_MS]
    cases, skipped = [], 0
    prev_hi = prev_lo = None
    for s in eng.zz_h.confirmed:
        opposite = prev_lo if s.kind == "SWING_HIGH" else prev_hi
        if s.confirmed_at >= data.REPLAY_START_MS and opposite is not None:
            move_pct = abs(s.price - opposite) / opposite * 100.0
            if move_pct >= MAJOR_REVERSAL_PCT:
                check_at = s.pivot_at  # close of the 1h bar immediately preceding the pivot bar
                pub = hourly.get(check_at)
                if pub is None:
                    skipped += 1
                else:
                    tol = s.price * COVERAGE_TOL_PCT / 100.0
                    hit = [p for p in pub if abs(p - s.price) <= tol]
                    cases.append({"kind": s.kind, "price": round(s.price, 2),
                                  "pivot_at": s.pivot_at, "confirmed_at": s.confirmed_at,
                                  "move_pct": round(move_pct, 3), "hit": bool(hit),
                                  "nearest_published_delta": (
                                      round(min(abs(p - s.price) for p in pub), 2) if pub else None),
                                  "published_count": len(pub)})
        if s.kind == "SWING_HIGH":
            prev_hi = s.price
        else:
            prev_lo = s.price
    hits = sum(1 for c in cases if c["hit"])
    return {"definition": {"major_reversal_move_pct": MAJOR_REVERSAL_PCT,
                           "coverage_tol_pct": COVERAGE_TOL_PCT,
                           "checked_at": "close of the 1h bar immediately preceding the pivot bar"},
            "total_major_reversals": len(cases), "hits": hits,
            "coverage": round(hits / len(cases), 4) if cases else None,
            "skipped_pivot_before_window": skipped,
            "replay_window_1h_swings": len(swings), "cases": cases}


def _vwap_at_5m(bars: dict, want_ts: list[int]) -> dict[int, float]:
    """Session VWAP sampled at 5m closes, recomputed from 1m exactly as section 12 defines it."""
    from .vwap import SessionVwap
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
    levels = []
    for l in eng.book.levels.values():
        end = l.expired_at or data.REPLAY_END_MS
        if end < start or l.created_at >= data.REPLAY_END_MS:
            continue
        levels.append({"id": l.id, "tf": l.tf, "price": round(l.price, 2),
                       "origin_kind": l.origin_kind, "from": l.created_at, "to": end,
                       "expired": l.expired_at is not None, "touches": l.touches,
                       "breaks": l.breaks, "flips": l.flips})
    sw = [{"tf": s.tf, "kind": s.kind, "price": round(s.price, 2), "pivot_at": s.pivot_at,
           "confirmed_at": s.confirmed_at, "label": s.label}
          for s in eng.zz_h.confirmed + eng.zz_5.confirmed if s.confirmed_at >= start]
    bos = [b.immutable() | {"level_price": round(b.level_price, 2)} for b in eng.ev.breakouts
           if b.confirmed_at >= start]
    sws = [s.immutable() | {"level_price": round(s.level_price, 2)} for s in eng.ev.sweeps
           if s.confirmed_at >= start]
    rts = [{"breakout_id": r.breakout.id, "level_price": round(r.breakout.level_price, 2),
            "direction": r.breakout.direction, "state": r.state,
            "confirmed_at": r.breakout.confirmed_at, "terminated_at": r.terminated_at}
           for r in eng.ev.retests if r.breakout.confirmed_at >= start]
    track = [{"info_ts": s["info_ts"], "close": s["close"], "trend": s["trend_structure"],
              "range_state": s["range_state"], "range_high": s["range_high"],
              "range_low": s["range_low"], "vwap": s["vwap"], "vwap_position": s["vwap_position"],
              "retest_state": s["retest_state"], "failed_break_state": s["failed_break_state"],
              "nearest_resistance": (s["nearest_resistance"] or {}).get("price"),
              "nearest_support": (s["nearest_support"] or {}).get("price"),
              "published": [p["price"] for p in s["published_levels"]],
              "active_level_count": s["active_level_count"]}
             for s in eng.hourly_snapshots if s["info_ts"] >= start]
    return {"symbol": "BTCUSDT", "tf": "5m", "window": {"from": start, "to": data.REPLAY_END_MS},
            "candles": candles, "levels": levels, "swings": sw, "breakouts": bos, "sweeps": sws,
            "retests": rts, "range_log": [r for r in eng.rng.log if r["at"] >= start],
            "track": track}


def run() -> dict:
    cal = calibrate.run()
    bars = data.load()
    cfg = EngineConfig(theta_1h=cal["chosen"]["1h"]["theta"], theta_5m=cal["chosen"]["5m"]["theta"],
                       publish_from_ms=data.REPLAY_START_MS, publish_to_ms=data.REPLAY_END_MS)
    eng = Engine(bars, cfg)
    eng.run()

    hourly_pub = {s["info_ts"]: [p["price"] for p in s["published_levels"]]
                  for s in eng.hourly_snapshots}
    act = eng.counters["active"]
    pub = eng.counters["published"]
    m4 = m4_coverage(eng, hourly_pub)

    elapsed = [r for r in eng.ev.retests if r.deadline_ms <= data.REPLAY_END_MS]
    terminal = [r for r in elapsed if r.state in TERMINAL]
    per_day_bo: dict[str, int] = defaultdict(int)
    for b in eng.ev.breakouts:
        if b.confirmed_at >= data.REPLAY_START_MS:
            per_day_bo[_day(b.confirmed_at)] += 1
    per_day_sw: dict[str, int] = defaultdict(int)
    for s in eng.ev.sweeps:
        if s.confirmed_at >= data.REPLAY_START_MS:
            per_day_sw[_day(s.confirmed_at)] += 1
    bo_days = sorted(per_day_bo.values()) or [0]
    retest_kinds = Counter(r.state for r in eng.ev.retests
                           if r.breakout.confirmed_at >= data.REPLAY_START_MS)
    bo_src = Counter(b.source for b in eng.ev.breakouts if b.confirmed_at >= data.REPLAY_START_MS)
    bo_tf = Counter(b.tf for b in eng.ev.breakouts if b.confirmed_at >= data.REPLAY_START_MS)

    m1 = len(eng.audit.violations) + len(eng.audit.vanished)
    m2 = {"median": statistics.median(act), "p95": sorted(act)[int(0.95 * (len(act) - 1))],
          "max": max(act), "min": min(act), "mean": round(statistics.fmean(act), 2)}
    m3 = {"max_published": max(pub), "median_published": statistics.median(pub),
          "cap": 10, "cap_respected": max(pub) <= 10}
    m5 = {"windows_elapsed": len(elapsed), "terminal": len(terminal),
          "fraction": round(len(terminal) / len(elapsed), 4) if elapsed else None,
          "non_terminal_examples": [{"breakout_id": r.breakout.id, "state": r.state}
                                    for r in elapsed if r.state not in TERMINAL][:5]}
    m6 = {"breakouts_per_day": dict(sorted(per_day_bo.items())),
          "breakouts_per_day_median": statistics.median(bo_days),
          "breakouts_per_day_max": max(bo_days),
          "breakouts_total": sum(per_day_bo.values()),
          "breakouts_by_source": dict(bo_src), "breakouts_by_frame": dict(bo_tf),
          "sweeps_per_day_median": statistics.median(sorted(per_day_sw.values()) or [0]),
          "sweeps_total": sum(per_day_sw.values()),
          "retest_terminations": dict(retest_kinds)}
    m7 = {"joint_trend_range": eng.counters["joint"], "trend": eng.counters["trend"],
          "range": eng.counters["range"], "vwap_position": eng.counters["vwap_pos"]}

    if m1 > 0 or m2["median"] > 15 or (m4["coverage"] is not None and m4["coverage"] < 0.40) \
            or (m5["fraction"] is not None and m5["fraction"] < 1.0):
        verdict, why = "A_NOT_USABLE", []
        if m1 > 0:
            why.append(f"M1 repaint_violations={m1}")
        if m2["median"] > 15:
            why.append(f"M2 median active levels={m2['median']}")
        if m4["coverage"] is not None and m4["coverage"] < 0.40:
            why.append(f"M4 coverage={m4['coverage']}")
        if m5["fraction"] is not None and m5["fraction"] < 1.0:
            why.append(f"M5 liveness={m5['fraction']}")
    elif (m1 == 0 and m2["median"] <= 10 and m2["p95"] <= 16
          and m4["coverage"] is not None and m4["coverage"] >= 0.60
          and m5["fraction"] == 1.0 and m6["breakouts_per_day_median"] <= 12):
        verdict, why = "B_USABLE_FOR_STRUCTURE_LAYER", []
    else:
        verdict, why = "C_NEEDS_LIMITED_REVISION", []
        if m2["median"] > 10:
            why.append(f"M2 median active levels={m2['median']} exceeds 10")
        if m2["p95"] > 16:
            why.append(f"M2 p95 active levels={m2['p95']} exceeds 16")
        if m4["coverage"] is not None and m4["coverage"] < 0.60:
            why.append(f"M4 coverage={m4['coverage']} below 0.60")
        if m6["breakouts_per_day_median"] > 12:
            why.append(f"M6 median breakouts/day={m6['breakouts_per_day_median']} exceeds 12")

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "contract": json.loads((OUT / "contract_freeze_v2.json").read_text()),
        "provenance": data.provenance(), "d2_crosscheck": data.crosscheck_d2(),
        "calibration": cal["chosen"],
        "window": {"warmup_from": data.WARMUP_START_MS, "replay_from": data.REPLAY_START_MS,
                   "replay_to": data.REPLAY_END_MS, "snapshots": eng.counters["snapshots"]},
        "counts": {"swings_1h": len(eng.zz_h.confirmed), "swings_5m": len(eng.zz_5.confirmed),
                   "levels_created": len(eng.book.levels),
                   "levels_active_at_end": len(eng.book.active),
                   "candidates_merged": len(eng.book.merged_into),
                   "candidates_suppressed_by_1h": eng.book.suppressed_by_1h,
                   "breakouts_total_all_time": len(eng.ev.breakouts),
                   "sweeps_total_all_time": len(eng.ev.sweeps),
                   "retest_machines": len(eng.ev.retests),
                   "breakout_events_suppressed_by_cooldown": eng.ev.cooldown_suppressed,
                   "range_episodes": sum(1 for r in eng.rng.log if r["event"] == "RANGE_FROZEN")},
        "M1_repaint": {"violations": len(eng.audit.violations), "vanished": len(eng.audit.vanished),
                       "total": m1, "violation_examples": eng.audit.violations[:5],
                       "vanished_examples": eng.audit.vanished[:5],
                       "objects_audited": len(eng.audit.registry)},
        "M2_active_levels": m2, "M3_published": m3, "M4_coverage": m4, "M5_liveness": m5,
        "M6_event_rate": m6, "M7_states": m7,
        "verdict": verdict, "verdict_reasons": why,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "metrics_v1.json").write_text(json.dumps(result, indent=2) + "\n")
    with open(OUT / "snapshots_hourly_v1.jsonl", "w") as fh:
        for s in eng.hourly_snapshots:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    with open(OUT / "events_v1.jsonl", "w") as fh:
        for e in eng.events_log:
            fh.write(json.dumps(e, sort_keys=True) + "\n")
    (OUT / "overlay_v1.json").write_text(json.dumps(build_overlay(eng, bars)) + "\n")
    (OUT / "range_log_v1.json").write_text(json.dumps(eng.rng.log, indent=2) + "\n")
    return result


if __name__ == "__main__":
    r = run()
    print(json.dumps({k: v for k, v in r.items()
                      if k in ("calibration", "counts", "M1_repaint", "M2_active_levels",
                               "M3_published", "M5_liveness", "verdict", "verdict_reasons")},
                     indent=2)[:4000])
    print("M4", {k: v for k, v in r["M4_coverage"].items() if k != "cases"})
    print("M6", {k: v for k, v in r["M6_event_rate"].items() if k != "breakouts_per_day"})
    print("M7 joint", r["M7_states"]["joint_trend_range"])
