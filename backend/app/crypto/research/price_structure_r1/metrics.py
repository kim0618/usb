"""The pre-registered metric suite of contract section 8, applied to either engine.

The whole point of this module is that R0 and R1 are scored by the same code in the same process, so
every before and after number in the results is a re-measurement rather than a quotation. Everything
here is duck typed over the two engines: where R1 added a field, R0's absence of it is handled
explicitly and the R0 reading is stated.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone

from ..price_structure_r0.data import REPLAY_END_MS, REPLAY_START_MS
from ..price_structure_r0.levels import MERGE_TOL_ATR

MAJOR_REVERSAL_PCT = 2.0
COVERAGE_TOL_PCT = 0.25

# Contract section 8: the R0 state names map onto the R1 ones for the resolved / unresolved split.
RESOLVED_STATES = {"RETESTED", "FAILED", "HELD",            # R1
                   "RETEST_HELD", "RETEST_FAILED", "NO_RETEST"}  # R0
TERMINAL_STATES = RESOLVED_STATES | {"EXPIRED", "SUPERSEDED", "RETEST_INCONCLUSIVE"}


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _is_1h_sourced(view: dict) -> bool:
    src = view.get("sources")
    return ("1h" in src) if src else view["tf"] == "1h"


def _level_is_1h_sourced(lvl) -> bool:
    src = getattr(lvl, "sources", None)
    return ("1h" in src) if src else lvl.tf == "1h"


def _confirm_tf(lvl) -> str:
    return getattr(lvl, "confirm_tf", lvl.tf)


def _dist(xs: list) -> dict:
    s = sorted(xs)
    return {"median": statistics.median(s), "p95": s[int(0.95 * (len(s) - 1))], "max": max(s),
            "min": min(s), "mean": round(statistics.fmean(s), 2)}


def m1(eng) -> dict:
    a = eng.audit
    return {"violations": len(a.violations), "vanished": len(a.vanished),
            "total": len(a.violations) + len(a.vanished),
            "violation_examples": a.violations[:5], "vanished_examples": a.vanished[:5],
            "objects_audited": len(a.registry)}


def m2(eng) -> dict:
    """Publishable is the verdict bearing count; book is reported alongside (contract section 8)."""
    act = eng.counters["active"]
    book = eng.counters.get("book") or act
    prov = eng.counters.get("provisional") or [0] * len(act)
    return {"publishable": _dist(act), "book": _dist(book),
            "provisional_median": statistics.median(prov),
            "note": ("R0 has no PROVISIONAL state, so publishable and book are the same series"
                     if eng.counters.get("book") is None else
                     "publishable = CONFIRMED + WEAKENED; book adds PROVISIONAL")}


def m3_duplicate_density(eng) -> dict:
    """Near duplicate pairs inside each hourly published set, tolerance 0.25 * ATR_5m."""
    sets_with_dup = 0
    pairs_total = 0
    cross_tf = 0
    sets_counted = 0
    examples = []
    for s in eng.hourly_snapshots:
        atr5 = s.get("atr_5m")
        pub = s["published_levels"]
        if atr5 is None or len(pub) < 2:
            continue
        sets_counted += 1
        tol = MERGE_TOL_ATR * atr5
        found = 0
        for i in range(len(pub)):
            for j in range(i + 1, len(pub)):
                if abs(pub[i]["price"] - pub[j]["price"]) <= tol:
                    found += 1
                    if pub[i]["tf"] != pub[j]["tf"]:
                        cross_tf += 1
                    if len(examples) < 8:
                        examples.append({"info_ts": s["info_ts"],
                                         "a": {"price": pub[i]["price"], "tf": pub[i]["tf"],
                                               "id": pub[i]["id"]},
                                         "b": {"price": pub[j]["price"], "tf": pub[j]["tf"],
                                               "id": pub[j]["id"]},
                                         "tol": round(tol, 2)})
        pairs_total += found
        if found:
            sets_with_dup += 1
    return {"hourly_sets_counted": sets_counted, "sets_with_duplicate": sets_with_dup,
            "dup_set_share": round(sets_with_dup / sets_counted, 4) if sets_counted else None,
            "dup_pairs_total": pairs_total, "cross_tf_pairs": cross_tf,
            "tolerance": "0.25 * ATR_5m of the snapshot", "examples": examples}


def _alive_prices_at(eng, t_ms: int) -> list[float]:
    out = []
    for l in eng.book.levels.values():
        if l.created_at <= t_ms and (l.expired_at is None or l.expired_at > t_ms):
            out.append(l.price)
    return out


def m4_coverage(eng) -> dict:
    """Unchanged definition, plus the three diagnostics of contract section 8 that do not score."""
    hourly_pub = {s["info_ts"]: s["published_levels"] for s in eng.hourly_snapshots}
    cases, skipped = [], 0
    prev_hi = prev_lo = None
    for s in eng.zz_h.confirmed:
        opposite = prev_lo if s.kind == "SWING_HIGH" else prev_hi
        if s.confirmed_at >= REPLAY_START_MS and opposite is not None:
            move_pct = abs(s.price - opposite) / opposite * 100.0
            if move_pct >= MAJOR_REVERSAL_PCT:
                check_at = s.pivot_at  # close of the 1h bar immediately preceding the pivot bar
                pub = hourly_pub.get(check_at)
                if pub is None:
                    skipped += 1
                else:
                    tol = s.price * COVERAGE_TOL_PCT / 100.0
                    prices = [p["price"] for p in pub]
                    px_1h = [p["price"] for p in pub if _is_1h_sourced(p)]
                    alive = _alive_prices_at(eng, check_at)
                    cases.append({
                        "kind": s.kind, "price": round(s.price, 2), "pivot_at": s.pivot_at,
                        "confirmed_at": s.confirmed_at, "move_pct": round(move_pct, 3),
                        "hit": any(abs(p - s.price) <= tol for p in prices),
                        "hit_1h_only": any(abs(p - s.price) <= tol for p in px_1h),
                        "hit_in_book": any(abs(p - s.price) <= tol for p in alive),
                        "nearest_published_delta": (round(min(abs(p - s.price) for p in prices), 2)
                                                    if prices else None),
                        "nearest_book_delta": (round(min(abs(p - s.price) for p in alive), 2)
                                               if alive else None),
                        "published_count": len(prices), "book_count": len(alive),
                        "tol_abs": round(tol, 2)})
        if s.kind == "SWING_HIGH":
            prev_hi = s.price
        else:
            prev_lo = s.price
    n = len(cases)
    hits = sum(1 for c in cases if c["hit"])
    hit_moves = [c["move_pct"] for c in cases if c["hit"]]
    miss_moves = [c["move_pct"] for c in cases if not c["hit"]]
    return {"definition": {"major_reversal_move_pct": MAJOR_REVERSAL_PCT,
                           "coverage_tol_pct": COVERAGE_TOL_PCT,
                           "checked_at": "close of the 1h bar immediately preceding the pivot bar"},
            "total_major_reversals": n, "hits": hits,
            "coverage": round(hits / n, 4) if n else None,
            "skipped_pivot_before_window": skipped,
            "diagnostic_coverage_1h_sourced_only":
                round(sum(1 for c in cases if c["hit_1h_only"]) / n, 4) if n else None,
            "diagnostic_coverage_whole_book":
                round(sum(1 for c in cases if c["hit_in_book"]) / n, 4) if n else None,
            "diagnostic_mean_move_hit": round(statistics.fmean(hit_moves), 2) if hit_moves else None,
            "diagnostic_mean_move_miss": (round(statistics.fmean(miss_moves), 2)
                                          if miss_moves else None),
            "cases": cases}


def m5_liveness(eng) -> dict:
    rt = eng.ev.retests
    elapsed = [r for r in rt if r.deadline_ms <= REPLAY_END_MS]
    terminal = [r for r in elapsed if r.state in TERMINAL_STATES]
    in_window = [r for r in rt if r.breakout.confirmed_at >= REPLAY_START_MS]
    resolved = [r for r in in_window if r.state in RESOLVED_STATES]
    reasons = Counter(getattr(r, "reason", None) or "-" for r in in_window)
    return {"windows_elapsed": len(elapsed), "terminal": len(terminal),
            "fraction": round(len(terminal) / len(elapsed), 4) if elapsed else None,
            "machines_in_window": len(in_window),
            "resolved": len(resolved),
            "resolved_share": round(len(resolved) / len(in_window), 4) if in_window else None,
            "termination_states": dict(Counter(r.state for r in in_window)),
            "termination_reasons": dict(reasons),
            "max_concurrency": getattr(eng.ev, "max_concurrency", 1),
            "non_terminal_examples": [{"breakout_id": r.breakout.id, "state": r.state}
                                      for r in elapsed if r.state not in TERMINAL_STATES][:5]}


def m6_event_rate(eng) -> dict:
    per_day_bo: dict[str, int] = defaultdict(int)
    per_day_sw: dict[str, int] = defaultdict(int)
    bos = [b for b in eng.ev.breakouts if b.confirmed_at >= REPLAY_START_MS]
    for b in bos:
        per_day_bo[_day(b.confirmed_at)] += 1
    for s in eng.ev.sweeps:
        if s.confirmed_at >= REPLAY_START_MS:
            per_day_sw[_day(s.confirmed_at)] += 1
    bo_days = sorted(per_day_bo.values()) or [0]
    # Contract section 3: how many 1h frame breakouts exist only because a 5m originated level was
    # merged up to the 1h confirming frame. Reported separately so the effect cannot hide.
    promoted = 0
    for b in bos:
        if b.tf != "1h" or b.source != "LEVEL":
            continue
        lvl = eng.book.levels.get(b.level_id)
        if lvl is not None and lvl.tf == "5m" and _level_is_1h_sourced(lvl):
            promoted += 1
    return {"breakouts_per_day": dict(sorted(per_day_bo.items())),
            "breakouts_per_day_median": statistics.median(bo_days),
            "breakouts_per_day_max": max(bo_days),
            "breakouts_total": len(bos),
            "breakouts_by_source": dict(Counter(b.source for b in bos)),
            "breakouts_by_frame": dict(Counter(b.tf for b in bos)),
            "breakouts_on_1h_frame_from_merged_5m_levels": promoted,
            "sweeps_per_day_median": statistics.median(sorted(per_day_sw.values()) or [0]),
            "sweeps_total": sum(per_day_sw.values())}


def m7_states(eng) -> dict:
    return {"joint_trend_range": eng.counters["joint"], "trend": eng.counters["trend"],
            "range": eng.counters["range"], "vwap_position": eng.counters["vwap_pos"]}


def publication_cap(eng) -> dict:
    pub = eng.counters["published"]
    return {"max_published": max(pub), "median_published": statistics.median(pub),
            "cap": 10, "cap_respected": max(pub) <= 10}


def lifecycle_census(eng) -> dict:
    """Reported, not verdict bearing: what the 5m lifecycle actually did."""
    lv = list(eng.book.levels.values())
    if not lv or not hasattr(lv[0], "sources"):
        return {"applicable": False,
                "note": "R0 has no lifecycle: every level is publishable from creation"}
    trans = Counter((t["from"], t["to"]) for t in eng.book.transitions)
    never_confirmed = [l for l in lv if l.tf == "5m" and "1h" not in l.sources
                       and not any(t["id"] == l.id and t["to"] == "CONFIRMED"
                                   for t in eng.book.transitions)]
    return {"applicable": True, "levels_created": len(lv),
            "levels_5m_only": sum(1 for l in lv if "1h" not in l.sources),
            "levels_multi_tf": sum(1 for l in lv if len(l.sources) > 1),
            "levels_1h_only": sum(1 for l in lv if l.sources == {"1h"}),
            "never_left_provisional": len(never_confirmed),
            "promotions_by_1h_source": eng.book.promotions,
            "transitions": {f"{a}->{b}": n for (a, b), n in sorted(trans.items())},
            "expiry_reasons": dict(Counter(t["reason"] for t in eng.book.transitions
                                           if t["to"] == "EXPIRED"))}


def level_counts(eng) -> dict:
    return {"swings_1h": len(eng.zz_h.confirmed), "swings_5m": len(eng.zz_5.confirmed),
            "levels_created": len(eng.book.levels),
            "levels_publishable_at_end": len(eng.book.active),
            "candidates_merged": len(eng.book.merged_into),
            "candidates_merged_cross_tf": getattr(eng.book, "cross_tf_merges", None),
            "candidates_merged_same_tf": getattr(eng.book, "same_tf_merges", None),
            "candidates_suppressed_by_1h": getattr(eng.book, "suppressed_by_1h", 0),
            "breakouts_total_all_time": len(eng.ev.breakouts),
            "sweeps_total_all_time": len(eng.ev.sweeps),
            "retest_machines": len(eng.ev.retests),
            "breakout_events_suppressed_by_cooldown": eng.ev.cooldown_suppressed,
            "range_episodes": sum(1 for r in eng.rng.log if r["event"] == "RANGE_FROZEN")}


def score(eng) -> dict:
    """The full section 8 suite for one engine."""
    return {"counts": level_counts(eng), "M1_repaint": m1(eng), "M2_active_levels": m2(eng),
            "M3_duplicate_density": m3_duplicate_density(eng), "M4_coverage": m4_coverage(eng),
            "M5_liveness": m5_liveness(eng), "M6_event_rate": m6_event_rate(eng),
            "M7_states": m7_states(eng), "publication_cap": publication_cap(eng),
            "lifecycle_census": lifecycle_census(eng),
            "snapshots": eng.counters["snapshots"]}


def verdict(s: dict) -> tuple[str, list[str]]:
    """Contract section 9, fixed before any result. First match wins."""
    m1_total = s["M1_repaint"]["total"]
    m2_med = s["M2_active_levels"]["publishable"]["median"]
    m2_p95 = s["M2_active_levels"]["publishable"]["p95"]
    m3_share = s["M3_duplicate_density"]["dup_set_share"]
    m4 = s["M4_coverage"]["coverage"]
    m5 = s["M5_liveness"]["fraction"]
    m6_med = s["M6_event_rate"]["breakouts_per_day_median"]
    why: list[str] = []
    if m1_total > 0 or m2_med > 15 or (m4 is not None and m4 < 0.40) \
            or (m5 is not None and m5 < 1.0):
        if m1_total > 0:
            why.append(f"M1 repaint_violations={m1_total}")
        if m2_med > 15:
            why.append(f"M2 publishable median={m2_med} exceeds 15")
        if m4 is not None and m4 < 0.40:
            why.append(f"M4 coverage={m4} below 0.40")
        if m5 is not None and m5 < 1.0:
            why.append(f"M5 liveness={m5} below 1.0")
        return "A_NOT_USABLE", why
    if (m1_total == 0 and m2_med <= 10 and m2_p95 <= 16
            and m3_share is not None and m3_share <= 0.10
            and m4 is not None and m4 >= 0.60 and m5 == 1.0 and m6_med <= 12):
        return "B_USABLE_FOR_STRUCTURE_LAYER", []
    if m2_med > 10:
        why.append(f"M2 publishable median={m2_med} exceeds 10")
    if m2_p95 > 16:
        why.append(f"M2 publishable p95={m2_p95} exceeds 16")
    if m3_share is not None and m3_share > 0.10:
        why.append(f"M3 dup_set_share={m3_share} exceeds 0.10")
    if m4 is not None and m4 < 0.60:
        why.append(f"M4 coverage={m4} below 0.60")
    if m6_med > 12:
        why.append(f"M6 median breakouts/day={m6_med} exceeds 12")
    return "C_NEEDS_NEW_LEVEL_SOURCE", why
