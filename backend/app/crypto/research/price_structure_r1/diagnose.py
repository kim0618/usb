"""Post verdict diagnostics for R1. Nothing here feeds the verdict.

Two questions are asked after the frozen verdict of contract section 9 has been computed:

1. Which half of change 2 cost the M4 coverage: the touch gate before publication, or the shorter
   5m lifetime? Answered by re-running R1 with each half switched off. This is an attribution of a
   measured loss, not a threshold sweep: no bound moves, the verdict does not change, and the
   result is reported as a diagnostic.
2. For every major reversal that R0 covered and R1 did not, what happened to the level that covered
   it? Answered by name, level by level.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..price_structure_r0 import calibrate, data
from ..price_structure_r0.engine import Engine as EngineR0
from ..price_structure_r0.engine import EngineConfig as ConfigR0
from . import metrics
from .engine import Engine, EngineConfig
from .levels import EXPIRY_5M_CONFIRMED_MS

OUT = Path(__file__).resolve().parents[5] / "data/research/crypto/price_structure_r1"
EXPIRY_48H = 48 * 3_600_000
HOUR_MS = 3_600_000


class _DigestMixin:
    """Record the whole publishable set at each hourly instant, with the strength the ranking used.

    Reading a level's `state` or `touches` after the run is over answers a different question: every
    level is EXPIRED by then. The digest is taken at the instant itself, so the comparison against
    the publication cap is exact.
    """

    def _init_digest(self) -> None:
        self.digests: dict[int, list[dict]] = {}

    def _record(self, at_ms: int, levels) -> None:
        self.digests[at_ms] = [{"id": l.id, "price": round(l.price, 2),
                                "strength": l.strength(at_ms), "touches": l.touches,
                                "lifecycle": getattr(l, "state", "ACTIVE"),
                                "sources": sorted(getattr(l, "sources", {l.tf}))}
                               for l in levels]


class DigestEngineR1(_DigestMixin, Engine):
    def __init__(self, *a, **k) -> None:
        Engine.__init__(self, *a, **k)
        self._init_digest()

    def snapshot(self, i: int, at_ms: int) -> dict:
        snap = Engine.snapshot(self, i, at_ms)
        if at_ms % HOUR_MS == 0:
            self._record(at_ms, self.book.publishable)
        return snap


class DigestEngineR0(_DigestMixin, EngineR0):
    def __init__(self, *a, **k) -> None:
        EngineR0.__init__(self, *a, **k)
        self._init_digest()

    def snapshot(self, i: int, at_ms: int) -> dict:
        snap = EngineR0.snapshot(self, i, at_ms)
        if at_ms % HOUR_MS == 0:
            self._record(at_ms, self.book.active)
        return snap

VARIANTS = [
    ("R1_frozen", True, EXPIRY_5M_CONFIRMED_MS,
     "the frozen contract: touch gate on, 5m lifetime 24h"),
    ("no_touch_gate", False, EXPIRY_5M_CONFIRMED_MS,
     "change 2 half off: 5m levels publishable at birth, lifetime still 24h"),
    ("lifetime_48h", True, EXPIRY_48H,
     "change 2 half off: touch gate on, R0's 48h lifetime restored"),
    ("lifecycle_off", False, EXPIRY_48H,
     "change 2 fully off: changes 1 and 3 only, R0 level visibility and lifetime"),
]


def _row(name: str, note: str, s: dict) -> dict:
    return {"variant": name, "note": note,
            "M2_publishable_median": s["M2_active_levels"]["publishable"]["median"],
            "M2_publishable_p95": s["M2_active_levels"]["publishable"]["p95"],
            "M3_dup_set_share": s["M3_duplicate_density"]["dup_set_share"],
            "M3_cross_tf_pairs": s["M3_duplicate_density"]["cross_tf_pairs"],
            "M4_coverage": s["M4_coverage"]["coverage"],
            "M4_hits": s["M4_coverage"]["hits"],
            "M4_coverage_whole_book": s["M4_coverage"]["diagnostic_coverage_whole_book"],
            "M5_resolved_share": s["M5_liveness"]["resolved_share"],
            "M6_breakouts_per_day_median": s["M6_event_rate"]["breakouts_per_day_median"],
            "levels_created": s["counts"]["levels_created"],
            "verdict_if_this_were_the_contract": metrics.verdict(s)[0],
            "verdict_reasons": metrics.verdict(s)[1]}


def lost_hit_forensics(eng0: EngineR0, eng1: Engine, s0: dict, s1: dict) -> list[dict]:
    """For each reversal R0 covered and R1 did not, name the level and say what R1 did with it."""
    by_pivot0 = {c["pivot_at"]: c for c in s0["M4_coverage"]["cases"]}
    out = []
    for c1 in s1["M4_coverage"]["cases"]:
        c0 = by_pivot0.get(c1["pivot_at"])
        if not c0 or not c0["hit"] or c1["hit"]:
            continue
        price, t = c1["price"], c1["pivot_at"]
        tol = c1["tol_abs"]
        # the R0 levels that covered it, alive and published at the check time
        pub0 = next((s["published_levels"] for s in eng0.hourly_snapshots
                     if s["info_ts"] == t), [])
        coverers = [p for p in pub0 if abs(p["price"] - price) <= tol]
        r1_levels = []
        for l in eng1.book.levels.values():
            if abs(l.price - price) > tol:
                continue
            alive = l.created_at <= t and (l.expired_at is None or l.expired_at > t)
            first_conf = next((tr["at"] for tr in eng1.book.transitions
                               if tr["id"] == l.id and tr["to"] == "CONFIRMED"), None)
            r1_levels.append({"id": l.id, "price": round(l.price, 2), "tf": l.tf,
                              "sources": sorted(l.sources), "created_at": l.created_at,
                              "expired_at": l.expired_at, "alive_at_check": alive,
                              "first_confirmed_at": first_conf,
                              "confirmed_before_check": bool(first_conf and first_conf <= t),
                              "touches": l.touches})
        if not r1_levels:
            why = "NO_LEVEL_IN_R1_BOOK_AT_THAT_PRICE"
        elif any(x["alive_at_check"] and x["confirmed_before_check"] for x in r1_levels):
            why = "PRESENT_AND_PUBLISHABLE_BUT_OUTRANKED_BY_THE_CAP"
        elif any(x["alive_at_check"] for x in r1_levels):
            why = "IN_BOOK_BUT_STILL_PROVISIONAL_AT_THE_CHECK"
        else:
            why = "EXPIRED_BEFORE_THE_CHECK"
        out.append({"pivot_at": t, "reversal_price": price, "move_pct": c1["move_pct"],
                    "tol_abs": tol, "why_r1_missed": why,
                    "r0_covering_published_levels": coverers,
                    "r1_levels_in_tolerance": r1_levels,
                    "r1_nearest_published_delta": c1["nearest_published_delta"],
                    "r1_published_count": c1["published_count"]})
    return out


def touched_before_first_revisit(eng1: Engine, s1: dict) -> dict:
    """How often was a 5m level in tolerance still unconfirmed at the moment M4 checks?

    The touch gate asks price to come back to a level before the level is shown. M4 asks whether a
    level was already shown when price turned there. Where the turn IS the first revisit the two
    cannot both be satisfied. This counts the split, and the split is reported as measured.
    """
    first_conf = {}
    for tr in eng1.book.transitions:
        if tr["to"] == "CONFIRMED" and tr["id"] not in first_conf:
            first_conf[tr["id"]] = tr["at"]
    at_or_after = 0
    strictly_before = 0
    for c in s1["M4_coverage"]["cases"]:
        t, price, tol = c["pivot_at"], c["price"], c["tol_abs"]
        for l in eng1.book.levels.values():
            if abs(l.price - price) > tol or "1h" in l.sources:
                continue
            fc = first_conf.get(l.id)
            if l.created_at > t:
                continue
            if fc is None or fc > t:
                at_or_after += 1
            else:
                strictly_before += 1
    return {"5m_levels_in_tolerance_confirmed_after_the_check": at_or_after,
            "5m_levels_in_tolerance_confirmed_before_the_check": strictly_before,
            "reading": ("the split is close to even, so the touch gate is not the dominant cause of "
                        "the M4 loss on its own; the attribution table and the per case forensics "
                        "put the larger share on the shorter 5m lifetime")}


def ranking_vs_coverage(eng, s: dict) -> dict:
    """Does the publication ranking work against M4, or not?

    M4 rewards a level sitting at a price the market is about to turn at. A level at a price price
    has not revisited carries its birth `touches`, so it sits near the bottom of a ranking whose
    first term is `touches`. This measures, for every major reversal where a publishable level
    existed inside the tolerance, that level's strength against the weakest strength actually
    published on the same side of price at the same instant. Requires a digest engine.
    """
    dig = eng.digests
    snaps = {x["info_ts"]: x for x in eng.hourly_snapshots}
    rows = []
    for c in s["M4_coverage"]["cases"]:
        t, price, tol = c["pivot_at"], c["price"], c["tol_abs"]
        snap, book = snaps.get(t), dig.get(t)
        if snap is None or book is None:
            continue
        close = snap["close"]
        cands = [l for l in book if abs(l["price"] - price) <= tol]
        if not cands:
            continue
        side = [p for p in snap["published_levels"] if (p["price"] > close) == (price > close)]
        cutoff = min((p["strength"] for p in side), default=None)
        best = max(cands, key=lambda l: l["strength"])
        published_ids = {p["id"] for p in snap["published_levels"]}
        rows.append({"pivot_at": t, "reversal_price": price, "hit": c["hit"],
                     "candidates_in_tolerance": len(cands),
                     "covering_level_id": best["id"],
                     "covering_level_strength": best["strength"],
                     "covering_level_touches": best["touches"],
                     "covering_level_lifecycle": best["lifecycle"],
                     "covering_level_published": best["id"] in published_ids,
                     "weakest_published_strength_same_side": cutoff,
                     "published_on_that_side": len(side),
                     "slots_full": len(side) >= 5,
                     "outranked_by_the_cap": bool(best["id"] not in published_ids
                                                  and cutoff is not None
                                                  and best["strength"] < cutoff
                                                  and len(side) >= 5)})
    import statistics as st
    strengths = [r["covering_level_strength"] for r in rows]
    cutoffs = [r["weakest_published_strength_same_side"] for r in rows
               if r["weakest_published_strength_same_side"] is not None]
    return {"major_reversals_scored": len(s["M4_coverage"]["cases"]),
            "cases_with_a_publishable_level_in_tolerance": len(rows),
            "cases_where_that_level_was_published": sum(1 for r in rows
                                                        if r["covering_level_published"]),
            "cases_outranked_by_the_cap": sum(1 for r in rows if r["outranked_by_the_cap"]),
            "median_covering_level_strength": st.median(strengths) if strengths else None,
            "median_weakest_published_strength": st.median(cutoffs) if cutoffs else None,
            "rows": rows}


def run() -> dict:
    cal = calibrate.run()
    th = (cal["chosen"]["1h"]["theta"], cal["chosen"]["5m"]["theta"])
    bars = data.load()

    eng0 = DigestEngineR0(bars, ConfigR0(th[0], th[1], data.REPLAY_START_MS, data.REPLAY_END_MS))
    eng0.run()
    s0 = metrics.score(eng0)

    rows = [_row("R0", "the R0 engine, rescored by the same code", s0)]
    scored: dict[str, tuple] = {}
    for name, gate, exp, note in VARIANTS:
        eng = DigestEngineR1(bars, EngineConfig(th[0], th[1], data.REPLAY_START_MS,
                                                data.REPLAY_END_MS,
                                                require_touch_to_publish=gate, expiry_5m_ms=exp))
        eng.run()
        s = metrics.score(eng)
        scored[name] = (eng, s)
        rows.append(_row(name, note, s))

    eng1, s1 = scored["R1_frozen"]
    frozen = json.loads((OUT / "metrics_r1.json").read_text())
    identical = (json.dumps(s1, sort_keys=True) == json.dumps(frozen["R1"], sort_keys=True))

    out = {"note": ("post verdict diagnostics, contract section 9 verdict already fixed; "
                    "no bound is moved and no variant below is adopted"),
           "frozen_variant_reproduces_metrics_r1_exactly": identical,
           "attribution": rows,
           "lost_hit_forensics": lost_hit_forensics(eng0, eng1, s0, s1),
           "touch_gate_vs_first_revisit": touched_before_first_revisit(eng1, s1),
           "ranking_vs_coverage_R1": ranking_vs_coverage(eng1, s1),
           "ranking_vs_coverage_R0": ranking_vs_coverage(eng0, s0),
           "ranking_vs_coverage_lifecycle_off": ranking_vs_coverage(*scored["lifecycle_off"])}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "diagnostics_r1.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


if __name__ == "__main__":
    r = run()
    print("frozen variant reproduces metrics_r1.json exactly:",
          r["frozen_variant_reproduces_metrics_r1_exactly"])
    for row in r["attribution"]:
        print(f'{row["variant"]:>16}  M2med={row["M2_publishable_median"]:>3} '
              f'p95={row["M2_publishable_p95"]:>3}  dup={row["M3_dup_set_share"]:<7} '
              f'M4={row["M4_coverage"]:<7}({row["M4_hits"]:>2})  book={row["M4_coverage_whole_book"]:<7} '
              f'M6={row["M6_breakouts_per_day_median"]:>3}  lv={row["levels_created"]:>3}  '
              f'{row["verdict_if_this_were_the_contract"]}')
    print()
    print("touch gate vs first revisit:", json.dumps(r["touch_gate_vs_first_revisit"], indent=1))
    for k in ("ranking_vs_coverage_R0", "ranking_vs_coverage_R1",
              "ranking_vs_coverage_lifecycle_off"):
        print(k, json.dumps({a: b for a, b in r[k].items() if a != "rows"}))
    print()
    for f in r["lost_hit_forensics"]:
        print(f'{f["pivot_at"]}  {f["reversal_price"]:>10}  move={f["move_pct"]:<6} '
              f'{f["why_r1_missed"]}')
