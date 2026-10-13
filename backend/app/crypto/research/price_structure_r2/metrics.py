"""R2 scoring: R1's metric suite imported whole, plus the per source measurement of section 7.

Contract: ``docs/crypto/price_structure_r2/CONTRACT_R2.md`` sections 7, 8, 9, 11.

Nothing in M1 to M7, the publication cap check or the verdict function is reimplemented here. They
are imported from ``price_structure_r1.metrics`` and called on the R2 engine, which is what makes
every R0, R1 and R2 number in the result document a measurement on one ruler. What this module adds
is the attribution the task asks for: which reversal each family covers that the swing book misses,
what the family merged into, and what it cost on M2, M3 and M6.
"""
from __future__ import annotations

import statistics
from collections import Counter

from ..price_structure_r1 import metrics as m
from ..price_structure_r1.metrics import COVERAGE_TOL_PCT, score, verdict  # noqa: F401
from .levels import MAJOR, NEW_TOKENS, TOKENS


def _hourly_by_ts(eng) -> dict:
    return {s["info_ts"]: s for s in eng.hourly_snapshots}


def m4_cases_with_match(eng) -> list[dict]:
    """R1's own M4 cases, each annotated with the published levels that covered it.

    The annotation is read from the published view recorded at the check instant, so a level's
    ``sources`` here is what it was then and not what it became later.
    """
    cases = m.m4_coverage(eng)["cases"]
    hourly = _hourly_by_ts(eng)
    out = []
    for c in cases:
        snap = hourly.get(c["pivot_at"], {})
        tol = c["tol_abs"]
        match = [{"id": p["id"], "price": p["price"], "origin_kind": p["origin_kind"],
                  "sources": p["sources"], "lifecycle": p["lifecycle"], "tf": p["tf"],
                  "confirm_tf": p.get("confirm_tf"), "touches": p["touches"],
                  "strength": p["strength"], "delta": round(p["price"] - c["price"], 2)}
                 for p in snap.get("published_levels", [])
                 if abs(p["price"] - c["price"]) <= tol]
        out.append(dict(c, matched=match,
                        matched_sources=sorted({t for x in match for t in x["sources"]})))
    return out


def m4_attribution(base_cases: list[dict], var_cases: list[dict]) -> dict:
    """Which reversals a variant covers that the baseline misses, and the reverse."""
    base = {c["pivot_at"]: c for c in base_cases}
    gained, lost, shared = [], [], 0
    for c in var_cases:
        b = base.get(c["pivot_at"])
        if b is None:
            continue
        if c["hit"] and not b["hit"]:
            gained.append({"pivot_at": c["pivot_at"], "kind": c["kind"], "price": c["price"],
                           "move_pct": c["move_pct"],
                           "baseline_nearest_delta": b["nearest_published_delta"],
                           "covered_by": c["matched"]})
        elif b["hit"] and not c["hit"]:
            lost.append({"pivot_at": c["pivot_at"], "kind": c["kind"], "price": c["price"],
                         "move_pct": c["move_pct"],
                         "variant_nearest_delta": c["nearest_published_delta"],
                         "baseline_covered_by": b["matched"]})
        elif c["hit"] and b["hit"]:
            shared += 1
    return {"hits_gained": len(gained), "hits_lost": len(lost), "hits_shared": shared,
             "gained": gained, "lost": lost}


def source_census(eng) -> dict:
    """Section 7.2: what each family offered, and what it merged into."""
    book = eng.book.census_by_source()
    by_src = eng.counters.get("by_source") or {}
    pub = {}
    for t in TOKENS:
        series = by_src.get(t)
        pub[t] = ({"median": statistics.median(series), "p95": sorted(series)[int(0.95 * (len(series) - 1))],
                   "max": max(series), "mean": round(statistics.fmean(series), 2)}
                  if series else None)
    return {"book": book, "publishable_holding_token": pub,
            "day_objects_emitted": len(eng.day_objects),
            "day_objects_created_new_level": sum(1 for d in eng.day_objects if d["created"]),
            "range_objects_emitted": len(eng.range_objects),
            "range_objects_created_new_level": sum(1 for d in eng.range_objects if d["created"]),
            "range_episodes_seen": eng._episode,
            "range_edge_breaks_migrated_to_level": eng.range_edge_breaks_migrated,
            "flip_claims": len(eng.flip_claims),
            "flip_claim_modes": dict(Counter(f["mode"] for f in eng.flip_claims)),
            "flip_tokens_added": eng.book.flip_tokens_added,
            "flip_levels_created": eng.book.flip_levels_created}


def new_token_event_effect(eng) -> dict:
    """Section 8: breakouts that sit on the 1h frame only because a NEW token made a level major."""
    from ..price_structure_r0.data import REPLAY_START_MS
    bos = [b for b in eng.ev.breakouts if b.confirmed_at >= REPLAY_START_MS]
    by_token = {t: 0 for t in TOKENS}
    major_by_new = 0
    for b in bos:
        if b.source != "LEVEL":
            continue
        lvl = eng.book.levels.get(b.level_id)
        if lvl is None:
            continue
        for t in lvl.sources:
            by_token[t] += 1
        if b.tf == "1h" and lvl.tf == "5m" and "1h" not in lvl.sources and (NEW_TOKENS & lvl.sources):
            major_by_new += 1
    return {"breakouts_touching_a_level_holding_token": by_token,
            "breakouts_on_1h_frame_only_because_of_a_new_token": major_by_new,
            "note": ("a breakout is counted once per token its level holds, so the columns overlap "
                     "by design")}


def extras(eng) -> dict:
    return {"variant": eng.cfg.variant, "families": list(eng.cfg.families),
            "source_census": source_census(eng),
            "new_token_event_effect": new_token_event_effect(eng),
            "levels_major_at_end": sum(1 for l in eng.book.levels.values()
                                       if l.alive and l.major),
            "sources_seen": sorted({t for l in eng.book.levels.values() for t in l.sources})}


def cost_delta(base: dict, var: dict) -> dict:
    """Sections 7.3, 7.4, 7.5: what the family spent, on the axes R1 already passes."""
    b2, v2 = base["M2_active_levels"]["publishable"], var["M2_active_levels"]["publishable"]
    b3, v3 = base["M3_duplicate_density"], var["M3_duplicate_density"]
    b6, v6 = base["M6_event_rate"], var["M6_event_rate"]
    return {
        "M1_violations": {"base": base["M1_repaint"]["total"], "variant": var["M1_repaint"]["total"]},
        "M2_publishable_median": {"base": b2["median"], "variant": v2["median"],
                                  "delta": v2["median"] - b2["median"]},
        "M2_publishable_p95": {"base": b2["p95"], "variant": v2["p95"],
                               "delta": v2["p95"] - b2["p95"]},
        "M2_publishable_max": {"base": b2["max"], "variant": v2["max"],
                               "delta": v2["max"] - b2["max"]},
        "M3_dup_set_share": {"base": b3["dup_set_share"], "variant": v3["dup_set_share"],
                             "delta": round(v3["dup_set_share"] - b3["dup_set_share"], 4)},
        "M3_dup_pairs_total": {"base": b3["dup_pairs_total"], "variant": v3["dup_pairs_total"],
                               "delta": v3["dup_pairs_total"] - b3["dup_pairs_total"]},
        "M3_cross_tf_pairs": {"base": b3["cross_tf_pairs"], "variant": v3["cross_tf_pairs"],
                              "delta": v3["cross_tf_pairs"] - b3["cross_tf_pairs"]},
        "M4_coverage": {"base": base["M4_coverage"]["coverage"],
                        "variant": var["M4_coverage"]["coverage"],
                        "base_hits": base["M4_coverage"]["hits"],
                        "variant_hits": var["M4_coverage"]["hits"]},
        "M4_coverage_whole_book": {"base": base["M4_coverage"]["diagnostic_coverage_whole_book"],
                                   "variant": var["M4_coverage"]["diagnostic_coverage_whole_book"]},
        "M5_liveness": {"base": base["M5_liveness"]["fraction"],
                        "variant": var["M5_liveness"]["fraction"],
                        "base_resolved_share": base["M5_liveness"]["resolved_share"],
                        "variant_resolved_share": var["M5_liveness"]["resolved_share"]},
        "M6_breakouts_per_day_median": {"base": b6["breakouts_per_day_median"],
                                        "variant": v6["breakouts_per_day_median"],
                                        "delta": v6["breakouts_per_day_median"]
                                                 - b6["breakouts_per_day_median"]},
        "M6_breakouts_per_day_max": {"base": b6["breakouts_per_day_max"],
                                     "variant": v6["breakouts_per_day_max"]},
        "M6_breakouts_total": {"base": b6["breakouts_total"], "variant": v6["breakouts_total"],
                               "delta": v6["breakouts_total"] - b6["breakouts_total"]},
        "M6_by_frame": {"base": b6["breakouts_by_frame"], "variant": v6["breakouts_by_frame"]},
        "M6_by_source": {"base": b6["breakouts_by_source"], "variant": v6["breakouts_by_source"]},
        "levels_created": {"base": base["counts"]["levels_created"],
                           "variant": var["counts"]["levels_created"]},
    }


# -- contract section 9, the admission rule, frozen before any result ------------------
ADMISSION_BOUNDS = {"M2_publishable_median_max": 10, "M2_publishable_p95_max": 16,
                    "M3_dup_set_share_max": 0.10, "M5_liveness_exact": 1.0,
                    "M6_breakouts_per_day_median_max": 12}


def admission(base: dict, var: dict) -> dict:
    """Family F is admitted iff it adds an M4 hit and keeps every non M4 B bound of R1."""
    v2 = var["M2_active_levels"]["publishable"]
    checks = {
        "adds_an_M4_hit": var["M4_coverage"]["hits"] > base["M4_coverage"]["hits"],
        "M1_zero": var["M1_repaint"]["total"] == 0,
        "M2_median_le_10": v2["median"] <= ADMISSION_BOUNDS["M2_publishable_median_max"],
        "M2_p95_le_16": v2["p95"] <= ADMISSION_BOUNDS["M2_publishable_p95_max"],
        "M3_share_le_0.10": (var["M3_duplicate_density"]["dup_set_share"] is not None
                             and var["M3_duplicate_density"]["dup_set_share"]
                             <= ADMISSION_BOUNDS["M3_dup_set_share_max"]),
        "M5_liveness_eq_1": var["M5_liveness"]["fraction"] == ADMISSION_BOUNDS["M5_liveness_exact"],
        "M6_median_le_12": (var["M6_event_rate"]["breakouts_per_day_median"]
                            <= ADMISSION_BOUNDS["M6_breakouts_per_day_median_max"]),
    }
    return {"admitted": all(checks.values()), "checks": checks,
            "failed": [k for k, ok in checks.items() if not ok]}
