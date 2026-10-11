"""After-the-fact analysis of a recorder store. Reads the store only; writes nothing to it.

`analyze` is contract section 8 applied to what the recorder kept: the detector's own
`stats.summarise` (R0's circular shift test and bootstrap CI against the complement, Holm over
4 questions x {5, 15, 60}) on the stored detector records and outcomes, plus descriptive columns
of the firing events alone. Nothing here selects a horizon, changes a gate or produces a direction:
the signal review block is a list of the inputs R0 section 14 names, each with its value, and the
answer to "promote automatically" is always PROHIBITED.

`summary_window` is the first-24-hours report: counts per question and the quality ledger, for
measuring events per day. It never computes an outcome statistic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..market_context_r0 import stats as R0S
from . import contract as K
from . import stats as S
from . import store as STO

OWN_BOOTSTRAP_DRAWS = R0S.BOOTSTRAP
SEED = R0S.SEED


def _load(root: Path) -> STO.Store:
    return STO.Store.open(Path(root), read_only=True)


def holm_adjusted(grid: list[dict]) -> None:
    """Step-down Holm adjusted p (the same procedure as R0's `holm`, stated as a p value)."""
    have = sorted((g for g in grid if g.get("p_value") is not None), key=lambda g: g["p_value"])
    m = len(have)
    running = 0.0
    for i, g in enumerate(have):
        running = max(running, min(1.0, (m - i) * g["p_value"]))
        g["holm_adjusted_p"] = round(running, 6)
    for g in grid:
        g.setdefault("holm_adjusted_p", None)


def own_mean_ci(net: np.ndarray) -> dict:
    if net.size < 2:
        return {"lo": None, "hi": None, "draws": 0}
    rng = np.random.default_rng(SEED)
    d = np.asarray([rng.choice(net, net.size, replace=True).mean()
                    for _ in range(OWN_BOOTSTRAP_DRAWS)])
    return {"lo": round(float(np.percentile(d, 2.5)), 4),
            "hi": round(float(np.percentile(d, 97.5)), 4), "draws": OWN_BOOTSTRAP_DRAWS}


def concentration(rows: list[dict], events: dict[str, dict]) -> dict:
    """Descriptive only; no frozen threshold exists for "acceptable"."""
    if not rows:
        return {"n": 0}
    net = np.asarray([r["net_bp"] for r in rows], dtype=float)
    pos = np.sort(net[net > 0])[::-1]
    k10 = max(1, int(np.ceil(0.10 * net.size)))
    days: dict[str, float] = {}
    for r in rows:
        d = datetime.fromtimestamp(events[r["event_id"]]["event_ts"] / 1000, timezone.utc).date()
        days[str(d)] = days.get(str(d), 0.0) + r["net_bp"]
    total_abs = sum(abs(v) for v in days.values())
    return {"n": int(net.size), "distinct_utc_days": len(days),
            "top10pct_share_of_positive_sum": round(float(pos[:k10].sum() / pos.sum()), 4)
            if pos.size else None,
            "largest_day_share_of_abs_daily_sum": round(max(abs(v) for v in days.values())
                                                        / total_abs, 4) if total_abs else None,
            "acceptable": "NO_FROZEN_THRESHOLD - human review"}


def analyze(root: Path) -> dict:
    store = _load(root)
    events = store.rows[STO.EVENTS]
    outcomes = store.rows[STO.OUTCOMES]
    records = sorted((e["detector"] for e in events.values()),
                     key=lambda r: (r["anchor_ts"], r["question"], r["event_id"]))
    det_outcomes = [o["detector"] for o in outcomes.values()]
    summary = S.summarise(records, det_outcomes)
    holm_adjusted(summary["grid"])
    by_q: dict = {}
    for q in K.QUESTIONS:
        entry = {"stage": summary["questions"][q]["stage"],
                 "stage_n_firing_60m": summary["questions"][q]["stage_n"], "horizons": {}}
        for h in K.HORIZONS_MIN:
            rows = [o for o in outcomes.values() if o["question"] == q and o["horizon_min"] == h
                    and o["fires"] and o["status"] == "MEASURED"
                    and events[o["event_id"]]["validity"] == "VALID"]
            missing = sum(1 for o in outcomes.values() if o["question"] == q
                          and o["horizon_min"] == h and o["fires"] and o["status"] == "MISSING")
            net = np.asarray([r["net_bp"] for r in rows], dtype=float)
            he = summary["questions"][q]["horizons"][str(h)]
            entry["horizons"][str(h)] = {
                "role": "PRIMARY_GRID" if h in K.TEST_GRID_HORIZONS_MIN else "DIAGNOSTIC_ONLY",
                "firing": he["firing"], "complement": he["complement"],
                "firing_missing": missing,
                "own_mean_net_bootstrap_ci": own_mean_ci(net),
                "concentration": concentration(rows, events),
                "edge_vs_complement_bp": he.get("edge_vs_complement_bp"),
                "test": he.get("test"), "bootstrap_ci_vs_complement": he.get("bootstrap_ci"),
                "measurable": he.get("measurable")}
        by_q[q] = entry
    review = []
    for g in summary["grid"]:
        review.append({"question": g["question"], "horizon_min": g["horizon_min"],
                       "n_firing_60m": by_q[g["question"]]["stage_n_firing_60m"],
                       "stage": by_q[g["question"]]["stage"],
                       "lean_sign_ok": g["lean_sign_ok"], "p_value": g["p_value"],
                       "holm_adjusted_p": g["holm_adjusted_p"],
                       "holm_significant": g.get("holm_significant", False),
                       "own_mean_net_bp": g["own_mean_net_bp"],
                       "own_mean_net_positive": (g["own_mean_net_bp"] or 0) > 0,
                       "concentration": by_q[g["question"]]["horizons"][str(g["horizon_min"])]
                       ["concentration"],
                       "automatic_promotion": "PROHIBITED"})
    return {"schema": "q123-forward-analyzer.v1", "contract_sha256": store.meta.get(
                "contract_sha256"),
            "rule": "review inputs only; no horizon chosen after results; 30m is diagnostic; "
                    "no LONG / SHORT / WAIT output",
            "store_counts": {n: len(store.keys[n]) for n in STO.FILES},
            "questions": by_q, "grid_holm": summary["grid"], "signal_review_inputs": review}


def summary_window(root: Path, from_ms: int | None, to_ms: int | None, *,
                   runtime: Path | None = None) -> dict:
    """Counts for events/day. `from_ms` defaults to the store's coverage start, `to_ms` to +24h."""
    store = _load(root)
    start = from_ms if from_ms is not None else store.meta.get("coverage_start_ms")
    end = to_ms if to_ms is not None else (start + 86_400_000 if start is not None else None)
    inside = lambda ts: start is not None and start <= ts < end  # noqa: E731
    out: dict = {"window_ms": [start, end],
                 "window_utc": [None if x is None else
                                datetime.fromtimestamp(x / 1000, timezone.utc).isoformat()
                                for x in (start, end)],
                 "rule": "counts only: no outcome statistic, no direction", "questions": {}}
    approaches = [a for a in store.rows[STO.APPROACHES].values() if inside(a["anchor_ts"] + 60_000)]
    for q in K.QUESTIONS:
        mine = [a for a in approaches if a["question"] == q]
        admitted = [a for a in mine if a["status"] == "ADMITTED"]
        valid = [a for a in admitted if a.get("validity") == "VALID"]
        fired = [a for a in valid if a.get("fires")]
        reasons: dict[str, int] = {}
        for a in mine:
            r = a["status"] if a["status"] != "ADMITTED" else a.get("invalid_reason")
            if r:
                reasons[r] = reasons.get(r, 0) + 1
        comp = {}
        for h in K.HORIZONS_MIN:
            os_ = [store.rows[STO.OUTCOMES].get(f'{a["event_id"]}:{h}') for a in valid]
            comp[str(h)] = {"measured": sum(1 for o in os_ if o and o["status"] == "MEASURED"),
                            "missing": sum(1 for o in os_ if o and o["status"] == "MISSING"),
                            "pending": sum(1 for o in os_ if o is None),
                            "fired_measured": sum(1 for a, o in zip(valid, os_)
                                                  if a.get("fires") and o
                                                  and o["status"] == "MEASURED")}
        out["questions"][q] = {"structure_approaches": len(mine),
                               "decision_capable": len(admitted), "valid": len(valid),
                               "fired": len(fired), "outcomes": comp,
                               "invalid_or_excluded_reasons": reasons}
    qual: dict = {"hours": 0, "samples": 0, "r0_ok": 0}
    qual["granularity"] = "UTC hours overlapping the window"
    for row in store.rows[STO.QUALITY].values():
        if start is None or not (row["hour_start_ms"] + 3_600_000 > start
                                 and row["hour_start_ms"] < end):
            continue
        qual["hours"] += 1
        qual["samples"] += row["samples"]
        qual["r0_ok"] += row["r0_ok"]
        for f in ("collector_state", "book_state", "liquidity_state", "flow_state",
                  "band01_bid_coverage", "flow60_coverage", "absorption"):
            for k, v in row[f].items():
                qual.setdefault(f, {})[k] = qual.setdefault(f, {}).get(k, 0) + v
    gen = sum(a.get("invalid_reason") == K.GENERATION_CHANGED for a in approaches)
    qual["GENERATION_CHANGED_IN_W"] = gen
    qual["OUT_OF_COVERAGE_wall_at_anchor"] = sum(
        1 for e in store.rows[STO.EVENTS].values()
        if inside(e["event_ts"] + 60_000) and e.get("wall_coverage_at_anchor") not in
        (None, "COMPLETE"))
    out["quality"] = qual
    days = (end - start) / 86_400_000 if start is not None and end else None
    out["events_per_day"] = {q: (round(out["questions"][q]["fired"] / days, 2) if days else None)
                             for q in K.QUESTIONS}
    out["admitted_per_day"] = {q: (round(out["questions"][q]["decision_capable"] / days, 2)
                                   if days else None) for q in K.QUESTIONS}
    out["store_counts"] = {n: len(store.keys[n]) for n in STO.FILES}
    if runtime is not None and (runtime / "status.json").exists():
        import json
        st = json.loads((runtime / "status.json").read_text())
        out["recorder_state"] = {k: st.get(k) for k in ("state", "tail_sample_ms", "lag_ms",
                                                        "unrecoverable_sessions",
                                                        "store_conflicts")}
    return out


__all__ = ["analyze", "summary_window"]
