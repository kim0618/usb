"""Price Structure R1 replay: scores R0 and R1 with the same code, applies contract section 9.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.price_structure_r1.replay
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..price_structure_r0 import calibrate, data
from ..price_structure_r0.data import Bars, aggregate, wilder_atr
from ..price_structure_r0.engine import Engine as EngineR0
from ..price_structure_r0.engine import EngineConfig as ConfigR0
from . import metrics
from .engine import Engine, EngineConfig

OUT = Path(__file__).resolve().parents[5] / "data/research/crypto/price_structure_r1"
OVERLAY_DAYS = 10
DAY_MS = 86_400_000
MINUTE_MS = 60_000
CUT_POINTS = [data.REPLAY_START_MS + d for d in
              (3 * DAY_MS, 10 * DAY_MS, 15 * DAY_MS, 24 * DAY_MS, 29 * DAY_MS,
               32 * DAY_MS + 22 * 3_600_000)]


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def build_r1(bars: dict, cfg: EngineConfig) -> Engine:
    eng = Engine(bars, cfg)
    eng.run()
    return eng


def prefix_truncation(bars: dict, cfg_theta: tuple[float, float]) -> dict:
    """Contract section 7: the engine fed only closed bars must reproduce the full run at T."""
    b1 = bars["1m"]
    rows = []
    for T in CUT_POINTS:
        cut = int((T - int(b1.ts[0])) // MINUTE_MS)
        sl = lambda x: x[:cut]
        t1 = Bars("1m", MINUTE_MS, sl(b1.ts), sl(b1.open), sl(b1.high), sl(b1.low), sl(b1.close),
                  sl(b1.volume), sl(b1.turnover),
                  wilder_atr(sl(b1.high), sl(b1.low), sl(b1.close)))
        trunc = {"1m": t1, "5m": aggregate(t1, "5m", 5), "1h": aggregate(t1, "1h", 60)}
        a = build_r1(trunc, EngineConfig(*cfg_theta, T, T))
        b = build_r1(bars, EngineConfig(*cfg_theta, T, T))
        sa = a.hourly_snapshots[-1] if a.hourly_snapshots else None
        sb = b.hourly_snapshots[-1] if b.hourly_snapshots else None
        same = sa == sb
        diff = [] if same else sorted(k for k in (sa or {}) if (sa or {}).get(k) != (sb or {}).get(k))
        rows.append({"T": _iso(T), "T_ms": T, "identical": bool(same),
                     "fields_compared": len(sa or {}), "differing_fields": diff,
                     "publishable_levels": (sa or {}).get("active_level_count"),
                     "book_levels": (sa or {}).get("book_level_count"),
                     "published": len((sa or {}).get("published_levels", [])),
                     "retests_open": (sa or {}).get("retests_open")})
    return {"cut_points": len(rows), "identical": sum(1 for r in rows if r["identical"]),
            "rows": rows}


def _first_confirmed_at(eng: Engine) -> dict[int, int]:
    out: dict[int, int] = {}
    for t in eng.book.transitions:
        if t["to"] == "CONFIRMED" and t["id"] not in out:
            out[t["id"]] = t["at"]
    for l in eng.book.levels.values():
        if "1h" in l.sources and l.id not in out and l.tf == "1h":
            out[l.id] = l.created_at
    return out


def _vwap_at_5m(bars: dict, want_ts: list[int]) -> dict[int, float]:
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
    conf = _first_confirmed_at(eng)
    levels = []
    for l in eng.book.levels.values():
        end = l.expired_at or data.REPLAY_END_MS
        shown_from = conf.get(l.id)
        if shown_from is None or end < start or shown_from >= data.REPLAY_END_MS:
            continue  # never published: nothing to draw, which is the point of PROVISIONAL
        levels.append({"id": l.id, "tf": l.tf, "price": round(l.price, 2),
                       "origin_kind": l.origin_kind, "from": shown_from, "to": end,
                       "created_at": l.created_at, "provenance": l.provenance,
                       "sources": sorted(l.sources), "lifecycle": l.state,
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
                             "lifecycle": p["lifecycle"]} for p in s["published_levels"]],
              "active_level_count": s["active_level_count"],
              "book_level_count": s["book_level_count"],
              "provisional_count": s["provisional_count"]}
             for s in eng.hourly_snapshots if s["info_ts"] >= start]
    return {"symbol": "BTCUSDT", "tf": "5m", "window": {"from": start, "to": data.REPLAY_END_MS},
            "candles": candles, "levels": levels, "swings": sw, "breakouts": bos, "sweeps": sws,
            "retests": rts, "range_log": [r for r in eng.rng.log if r["at"] >= start],
            "track": track}


def _cmp(before, after) -> dict:
    return {"R0": before, "R1": after,
            "delta": (round(after - before, 4)
                      if isinstance(before, (int, float)) and isinstance(after, (int, float))
                      else None)}


def compare(s0: dict, s1: dict) -> dict:
    return {
        "M2_publishable_median": _cmp(s0["M2_active_levels"]["publishable"]["median"],
                                      s1["M2_active_levels"]["publishable"]["median"]),
        "M2_publishable_p95": _cmp(s0["M2_active_levels"]["publishable"]["p95"],
                                   s1["M2_active_levels"]["publishable"]["p95"]),
        "M2_book_median": _cmp(s0["M2_active_levels"]["book"]["median"],
                               s1["M2_active_levels"]["book"]["median"]),
        "M3_dup_set_share": _cmp(s0["M3_duplicate_density"]["dup_set_share"],
                                 s1["M3_duplicate_density"]["dup_set_share"]),
        "M3_dup_pairs_total": _cmp(s0["M3_duplicate_density"]["dup_pairs_total"],
                                   s1["M3_duplicate_density"]["dup_pairs_total"]),
        "M3_cross_tf_pairs": _cmp(s0["M3_duplicate_density"]["cross_tf_pairs"],
                                  s1["M3_duplicate_density"]["cross_tf_pairs"]),
        "M4_coverage": _cmp(s0["M4_coverage"]["coverage"], s1["M4_coverage"]["coverage"]),
        "M4_hits": _cmp(s0["M4_coverage"]["hits"], s1["M4_coverage"]["hits"]),
        "M4_total": _cmp(s0["M4_coverage"]["total_major_reversals"],
                         s1["M4_coverage"]["total_major_reversals"]),
        "M4_coverage_whole_book": _cmp(s0["M4_coverage"]["diagnostic_coverage_whole_book"],
                                       s1["M4_coverage"]["diagnostic_coverage_whole_book"]),
        "M5_liveness": _cmp(s0["M5_liveness"]["fraction"], s1["M5_liveness"]["fraction"]),
        "M5_resolved_share": _cmp(s0["M5_liveness"]["resolved_share"],
                                  s1["M5_liveness"]["resolved_share"]),
        "M5_max_concurrency": _cmp(s0["M5_liveness"]["max_concurrency"],
                                   s1["M5_liveness"]["max_concurrency"]),
        "M6_breakouts_per_day_median": _cmp(s0["M6_event_rate"]["breakouts_per_day_median"],
                                            s1["M6_event_rate"]["breakouts_per_day_median"]),
        "M6_breakouts_per_day_max": _cmp(s0["M6_event_rate"]["breakouts_per_day_max"],
                                         s1["M6_event_rate"]["breakouts_per_day_max"]),
        "M6_breakouts_total": _cmp(s0["M6_event_rate"]["breakouts_total"],
                                   s1["M6_event_rate"]["breakouts_total"]),
        "levels_created": _cmp(s0["counts"]["levels_created"], s1["counts"]["levels_created"]),
        "retest_machines": _cmp(s0["counts"]["retest_machines"], s1["counts"]["retest_machines"]),
        "SUPERSEDED_to_EXPIRED": {
            "R0_SUPERSEDED": s0["M5_liveness"]["termination_states"].get("SUPERSEDED", 0),
            "R1_EXPIRED": s1["M5_liveness"]["termination_states"].get("EXPIRED", 0),
            "R1_EXPIRED_reasons": {k: v for k, v in s1["M5_liveness"]["termination_reasons"].items()
                                   if k in ("RELEVEL", "LEVEL_EXPIRED", "UNDECIDED")}},
    }


def run() -> dict:
    cal = calibrate.run()
    theta = (cal["chosen"]["1h"]["theta"], cal["chosen"]["5m"]["theta"])
    bars = data.load()

    eng0 = EngineR0(bars, ConfigR0(theta[0], theta[1], data.REPLAY_START_MS, data.REPLAY_END_MS))
    eng0.run()
    s0 = metrics.score(eng0)
    v0, w0 = metrics.verdict(s0)

    eng1 = build_r1(bars, EngineConfig(theta[0], theta[1], data.REPLAY_START_MS,
                                       data.REPLAY_END_MS))
    s1 = metrics.score(eng1)
    v1, w1 = metrics.verdict(s1)

    prefix = prefix_truncation(bars, theta)

    eng1b = build_r1(bars, EngineConfig(theta[0], theta[1], data.REPLAY_START_MS,
                                        data.REPLAY_END_MS))
    s1b = metrics.score(eng1b)
    deterministic = json.dumps(s1, sort_keys=True) == json.dumps(s1b, sort_keys=True)

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "contract": json.loads((OUT / "contract_freeze_r1.json").read_text()),
        "provenance": data.provenance(), "d2_crosscheck": data.crosscheck_d2(),
        "calibration": cal["chosen"],
        "window": {"warmup_from": data.WARMUP_START_MS, "replay_from": data.REPLAY_START_MS,
                   "replay_to": data.REPLAY_END_MS, "snapshots": s1["snapshots"]},
        "verdict": v1, "verdict_reasons": w1,
        "verdict_r0_rescored": v0, "verdict_r0_reasons": w0,
        "prefix_truncation": prefix, "deterministic_two_runs": deterministic,
        "before_after": compare(s0, s1),
        "R1": s1, "R0_rescored": s0,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "metrics_r1.json").write_text(json.dumps(result, indent=2) + "\n")
    (OUT / "prefix_truncation_r1.json").write_text(json.dumps(prefix, indent=2) + "\n")
    with open(OUT / "snapshots_hourly_r1.jsonl", "w") as fh:
        for s in eng1.hourly_snapshots:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    with open(OUT / "events_r1.jsonl", "w") as fh:
        for e in eng1.events_log:
            fh.write(json.dumps(e, sort_keys=True) + "\n")
    with open(OUT / "level_transitions_r1.jsonl", "w") as fh:
        for t in eng1.book.transitions:
            fh.write(json.dumps(t, sort_keys=True) + "\n")
    with open(OUT / "retest_transitions_r1.jsonl", "w") as fh:
        for t in eng1.ev.transitions:
            fh.write(json.dumps(t, sort_keys=True) + "\n")
    (OUT / "overlay_r1.json").write_text(json.dumps(build_overlay(eng1, bars)) + "\n")
    (OUT / "range_log_r1.json").write_text(json.dumps(eng1.rng.log, indent=2) + "\n")
    return result


if __name__ == "__main__":
    r = run()
    print("verdict R1:", r["verdict"], r["verdict_reasons"])
    print("verdict R0 rescored:", r["verdict_r0_rescored"], r["verdict_r0_reasons"])
    print("deterministic:", r["deterministic_two_runs"],
          " prefix identical:", r["prefix_truncation"]["identical"], "/",
          r["prefix_truncation"]["cut_points"])
    print(json.dumps(r["before_after"], indent=2))
    print("M1 R1:", r["R1"]["M1_repaint"])
    print("lifecycle:", json.dumps(r["R1"]["lifecycle_census"], indent=2))
