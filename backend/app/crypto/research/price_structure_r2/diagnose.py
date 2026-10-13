"""Post verdict diagnostics for R2. Nothing here feeds the verdict.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.price_structure_r2.diagnose

Four questions are asked after the frozen verdict of contract section 11 has been computed. All four
are attributions of measured numbers. No bound moves, no variant is adopted, and no threshold is
swept.

1. The admitted family's gain is one reversal. By what mechanism: a new line at a price that had
   none, or a line that stayed alive because a token extended it?
2. S3 gained two reversals and lost one. Was the lost one evicted by the publication cap, and if so
   by what?
3. In the final combination, how many reversals had a covering level in the book that was not
   published, and what outranked it?
4. The misses that survive. Had the market traded that price before the reversal, and how far was
   the nearest structure of any kind?

Reading a level's ``state`` after the run is over answers a different question, because by then
every level is EXPIRED. The publishable set and the strength the ranking used are therefore recorded
at the instant itself.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..price_structure_r0 import calibrate, data
from ..price_structure_r1 import metrics as m1
from . import metrics
from .engine import Engine, EngineConfig
from .replay import OUT, cfg_for


class _Digest:
    """Record the whole publishable set, with the strength the ranking used, at each hourly instant."""

    def _init_digest(self) -> None:
        self.digests: dict[int, list[dict]] = {}

    def snapshot(self, i: int, at_ms: int) -> dict:
        s = super().snapshot(i, at_ms)
        if at_ms % 3_600_000 == 0:
            cl = s["close"]
            self.digests[at_ms] = sorted(
                ({"id": l.id, "price": round(l.price, 2), "origin_kind": l.origin_kind,
                  "sources": sorted(l.sources), "lifecycle": l.state, "major": l.major,
                  "touches": l.touches, "strength": l.strength(at_ms),
                  "side_of_close": "ABOVE" if l.price > cl else "BELOW",
                  "distance": round(abs(l.price - cl), 2)}
                 for l in self.book.publishable),
                key=lambda d: (-d["strength"], d["distance"]))
        return s


class DigestEngine(_Digest, Engine):
    def __init__(self, *a, **k) -> None:
        Engine.__init__(self, *a, **k)
        self._init_digest()


def _level_history(eng: Engine, price: float, tol: float) -> list[dict]:
    """Every level the run ever created inside a tolerance of a price, with how it ended."""
    out = []
    for l in eng.book.levels.values():
        if abs(l.price - price) > tol:
            continue
        trans = [t for t in eng.book.transitions if t["id"] == l.id]
        out.append({"id": l.id, "price": round(l.price, 2), "tf": l.tf,
                    "origin_kind": l.origin_kind, "sources": sorted(l.sources),
                    "created_at": l.created_at, "expired_at": l.expired_at,
                    "final_state": l.state, "touches": l.touches, "merges": l.merges,
                    "transitions": [{"at": t["at"], "from": t["from"], "to": t["to"],
                                     "reason": t["reason"]} for t in trans]})
    return sorted(out, key=lambda d: d["created_at"])


def _was_traded_before(bars: dict, price: float, before_ms: int, tol: float) -> dict:
    """Had the market printed this price before the reversal? R0 asked the same question."""
    b1 = bars["1m"]
    i = int(np.searchsorted(b1.ts, before_ms))
    if i <= 0:
        return {"traded_before": None}
    hi = float(b1.high[:i].max())
    lo = float(b1.low[:i].min())
    touched = bool(np.any((b1.low[:i] <= price + tol) & (b1.high[:i] >= price - tol)))
    return {"traded_before": touched, "prior_high": round(hi, 2), "prior_low": round(lo, 2),
            "above_prior_high": price > hi, "below_prior_low": price < lo}


def run() -> dict:
    cal = calibrate.run()
    theta = (cal["chosen"]["1h"]["theta"], cal["chosen"]["5m"]["theta"])
    bars = data.load()
    frm, to = data.REPLAY_START_MS, data.REPLAY_END_MS
    res = json.loads((OUT / "metrics_r2.json").read_text())
    admitted = tuple(res["admitted_families"])

    engines, digests, cases = {}, {}, {}
    for fam in ((), ("S1",), ("S3",), admitted):
        eng = DigestEngine(bars, cfg_for(fam, theta, frm, to))
        eng.run()
        name = eng.cfg.variant
        engines[name] = eng
        digests[name] = eng.digests
        cases[name] = metrics.m4_cases_with_match(eng)
    final_name = engines[cfg_for(admitted, theta, 0, 0) and
                         DigestEngine(bars, cfg_for(admitted, theta, 0, 0)).cfg.variant].cfg.variant

    # the frozen verdict must be reproducible from the digest engine, which only adds a recorder
    reproduced = (m1.verdict(m1.score(engines[final_name]))[0]
                  == res["verdict_raw_from_r1_function"])

    base_cases = {c["pivot_at"]: c for c in cases["SWING_ONLY"]}

    # 1. the mechanism of the admitted family's single gain
    q1 = []
    for c in cases.get("SWING_S1", []):
        b = base_cases[c["pivot_at"]]
        if not (c["hit"] and not b["hit"]):
            continue
        covering = c["matched"]
        hist_var = _level_history(engines["SWING_S1"], c["price"], c["tol_abs"] * 2)
        hist_base = _level_history(engines["SWING_ONLY"], c["price"], c["tol_abs"] * 2)
        q1.append({"pivot_at": c["pivot_at"], "kind": c["kind"], "price": c["price"],
                   "move_pct": c["move_pct"], "tol_abs": c["tol_abs"],
                   "covered_by": covering,
                   "baseline_nearest_published_delta": b["nearest_published_delta"],
                   "levels_near_that_price_with_S1": hist_var,
                   "levels_near_that_price_in_the_baseline": hist_base})

    # 2. the reversal S3 lost: was the publication cap the binding constraint?
    q2 = []
    for c in cases.get("SWING_S3", []):
        b = base_cases[c["pivot_at"]]
        if not (b["hit"] and not c["hit"]):
            continue
        dig = digests["SWING_S3"].get(c["pivot_at"], [])
        in_tol = [d for d in dig if abs(d["price"] - c["price"]) <= c["tol_abs"]]
        side = in_tol[0]["side_of_close"] if in_tol else None
        same_side = [d for d in dig if d["side_of_close"] == side] if side else []
        q2.append({"pivot_at": c["pivot_at"], "kind": c["kind"], "price": c["price"],
                   "move_pct": c["move_pct"],
                   "baseline_covered_by": b["matched"],
                   "existed_but_unpublished": bool(in_tol),
                   "its_rank_on_its_side": ([same_side.index(d) + 1 for d in in_tol] or None),
                   "slots_per_side": 5,
                   "outranked_by": [d for d in same_side[:5]],
                   "candidate_in_tolerance": in_tol})

    # 3. the final combination: in the book, not on the screen
    q3 = []
    for c in cases[final_name]:
        if c["hit"] or not c["hit_in_book"]:
            continue
        dig = digests[final_name].get(c["pivot_at"], [])
        in_tol = [d for d in dig if abs(d["price"] - c["price"]) <= c["tol_abs"]]
        side = in_tol[0]["side_of_close"] if in_tol else None
        same_side = [d for d in dig if d["side_of_close"] == side] if side else []
        q3.append({"pivot_at": c["pivot_at"], "kind": c["kind"], "price": c["price"],
                   "move_pct": c["move_pct"], "nearest_book_delta": c["nearest_book_delta"],
                   "publishable_in_tolerance": in_tol,
                   "its_rank_on_its_side": ([same_side.index(d) + 1 for d in in_tol] or None),
                   "outranked_by": same_side[:5]})

    # 4. the misses that survive
    q4 = []
    for c in sorted((x for x in cases[final_name] if not x["hit"]),
                    key=lambda x: -x["move_pct"]):
        q4.append({"pivot_at": c["pivot_at"], "kind": c["kind"], "price": c["price"],
                   "move_pct": c["move_pct"], "tol_abs": c["tol_abs"],
                   "nearest_published_delta": c["nearest_published_delta"],
                   "nearest_book_delta": c["nearest_book_delta"],
                   "in_book": c["hit_in_book"],
                   "published_count": c["published_count"], "book_count": c["book_count"],
                   **_was_traded_before(bars, c["price"], c["pivot_at"], c["tol_abs"])})
    traded = [x for x in q4 if x.get("traded_before")]
    untraded = [x for x in q4 if x.get("traded_before") is False]

    out = {"verdict_reproduced_by_the_digest_engine": reproduced,
           "final_combination": final_name,
           "q1_mechanism_of_the_admitted_gain": q1,
           "q2_the_reversal_s3_lost": q2,
           "q3_in_the_book_but_not_published": q3,
           "q4_surviving_misses": q4,
           "q4_summary": {"misses": len(q4),
                          "at_a_price_already_traded": len(traded),
                          "at_a_price_never_traded_before": len(untraded),
                          "beyond_the_prior_high_or_low":
                              sum(1 for x in q4 if x.get("above_prior_high")
                                  or x.get("below_prior_low")),
                          "nearest_structure_over_1000":
                              sum(1 for x in q4 if (x["nearest_book_delta"] or 0) > 1000),
                          "median_nearest_book_delta":
                              sorted(x["nearest_book_delta"] for x in q4)[len(q4) // 2]
                              if q4 else None}}
    (OUT / "diagnostics_r2.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


if __name__ == "__main__":
    d = run()
    print("digest engine reproduces the verdict:", d["verdict_reproduced_by_the_digest_engine"])
    print("\nq1, the single admitted gain:")
    print(json.dumps(d["q1_mechanism_of_the_admitted_gain"], indent=2)[:4000])
    print("\nq2, what S3 lost:")
    print(json.dumps(d["q2_the_reversal_s3_lost"], indent=2)[:3000])
    print("\nq3, in the book but not published:")
    print(json.dumps(d["q3_in_the_book_but_not_published"], indent=2)[:3000])
    print("\nq4 summary:", json.dumps(d["q4_summary"], indent=2))
