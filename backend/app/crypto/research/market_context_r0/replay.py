"""Market Context R0 replay: mechanism checks, then the pre-registered families, then the verdict.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.market_context_r0.replay
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..price_structure_r0.data import Bars, aggregate, wilder_atr
from ..price_structure_r1.replay import CUT_POINTS
from . import data, families, journal, outcomes, stats, structure
from .families import FAMILIES, T2_HORIZONS, T3_HORIZONS
from .layers import RT_COST_BP, build_minute_frame, build_second_frame

OUT = Path(__file__).resolve().parents[5] / "data/research/crypto/market_context_r0"
DOC = Path(__file__).resolve().parents[5] / "docs/crypto/market_context_r0/CONTRACT_MC_R0.md"
MIN_EPISODES = 30
MINUTE_MS = 60_000


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def contract_identity() -> dict:
    sidecar = DOC.with_suffix(".md.sha256")
    now = hashlib.sha256(DOC.read_bytes()).hexdigest() if DOC.exists() else None
    rec = sidecar.read_text().split()[0] if sidecar.exists() else None
    return {"path": "docs/crypto/market_context_r0/CONTRACT_MC_R0.md", "sha256": now,
            "recorded_sha256": rec, "agrees": now == rec}


def mechanism_checks(mf) -> dict:
    """C1 to C7. A failed C1, C2 or C5 stops the study."""
    eng_rows = mf.rows
    bars = data.load()
    b1 = bars["1m"]
    # C2: prefix truncation at R1's cut points, on the Binance series.
    prefix = []
    for T in [t for t in CUT_POINTS if t <= data.SERIES_END_MS]:
        cut = int((T - int(b1.ts[0])) // MINUTE_MS)
        sl = lambda x: x[:cut]
        t1 = Bars("1m", MINUTE_MS, sl(b1.ts), sl(b1.open), sl(b1.high), sl(b1.low), sl(b1.close),
                  sl(b1.volume), sl(b1.turnover), wilder_atr(sl(b1.high), sl(b1.low), sl(b1.close)))
        trunc = {"1m": t1, "5m": aggregate(t1, "5m", 5), "1h": aggregate(t1, "1h", 60)}
        a = structure.ContextEngine(trunc, structure.EngineConfig(structure.THETA_1H,
                                                                  structure.THETA_5M, T, T))
        a.run()
        b = structure.ContextEngine(bars, structure.EngineConfig(structure.THETA_1H,
                                                                 structure.THETA_5M, T, T))
        b.run()
        sa = a.hourly_snapshots[-1] if a.hourly_snapshots else None
        sb = b.hourly_snapshots[-1] if b.hourly_snapshots else None
        diff = [] if sa == sb else sorted(k for k in (sa or {}) if (sa or {}).get(k) != (sb or {}).get(k))
        prefix.append({"T": _iso(T), "identical": sa == sb, "fields_compared": len(sa or {}),
                       "differing_fields": diff})
    eng_full = structure.run(data.REPLAY_START_MS, data.SERIES_END_MS)
    act = np.asarray(eng_full.counters["active"])
    return {
        "C1_repaint": {"violations": len(eng_full.audit.violations),
                       "vanished": len(eng_full.audit.vanished),
                       "snapshots": eng_full.counters["snapshots"], "bound": 0,
                       "pass": not eng_full.audit.violations and not eng_full.audit.vanished},
        "C2_prefix_truncation": {"cut_points": len(prefix),
                                 "identical": sum(1 for r in prefix if r["identical"]),
                                 "rows": prefix,
                                 "pass": all(r["identical"] for r in prefix)},
        "C3_theta_transfer": structure.warmup_theta_ladder(),
        "C4_curation_transfer": {"active_median": float(np.median(act)),
                                 "active_p95": float(np.percentile(act, 95)),
                                 "r1_bybit_median": 9, "r1_bybit_p95": 13,
                                 "breakouts": len(eng_full.ev.breakouts),
                                 "sweeps": len(eng_full.ev.sweeps),
                                 "levels_created": len(eng_full.book.levels)},
        "C5_wall_rule": {**journal.wallrule.rule_identity(),
                         "pass": journal.wallrule.rule_identity().get("sha256_agrees") is True},
        "C6_journal_wholeness": {
            "replay_starts_at_session_first_record": True,
            "note": ("every resting wall a sample sees was OPENED inside the records already read, "
                     "so the set is whole by construction rather than by reconstruction")},
        "C7_venue_basis": data.venue_basis(),
        "flow_dom_quantile_p80_warmup": mf.dom_quantile,
        "flow_warmup_minutes": mf.warmup_rows,
    }


def _describe(vals: list[dict], lean: str) -> dict:
    if not vals:
        return {"n": 0}
    f = lambda k: np.asarray([v[k] for v in vals], dtype=float)
    fm = {}
    for v in vals:
        d = v["first_meaningful_direction"]
        fm[d] = fm.get(d, 0) + 1
    rev = [v["reversal"] for v in vals if v["reversal"] is not None]
    con = [v["breakout_continuation"] for v in vals if v["breakout_continuation"] is not None]
    out = {"n": len(vals), "lean": lean}
    for k in ("net_move_bp", "reachable_bp", "mfe_up_bp", "mfe_dn_bp", "mae_bp"):
        a = f(k)
        out[k] = {"mean": round(float(a.mean()), 3), "median": round(float(np.median(a)), 3),
                  "p25": round(float(np.percentile(a, 25)), 3),
                  "p75": round(float(np.percentile(a, 75)), 3)}
    out["first_meaningful_direction"] = fm
    out["reversal_rate"] = None if not rev else round(sum(rev) / len(rev), 4)
    out["continuation_rate"] = None if not con else round(sum(con) / len(con), 4)
    out["net_above_zero_rate"] = round(float((f("net_move_bp") > 0).mean()), 4)
    return out


def _score_family(spec: dict, horizons, outcome_fn, frame) -> dict:
    fam, lean, side = spec["family"], spec["lean"], spec["side"]
    eps = spec["episodes"]
    res = {"family": fam, "variant": spec["variant"], "side": side, "lean": lean,
           "w": spec["w"], "episodes_admitted": len(eps),
           "episodes_firing": sum(1 for e in eps if e["fires"]),
           "excluded": spec.get("excluded"), "horizons": {}}
    for h in horizons:
        rows, fires = [], []
        for e in eps:
            o = outcome_fn(frame, e["i_dec"], lean, e["level_price"], h, side if side in ("RES", "SUP")
                           else ("RES" if side == "ASK" else "SUP"))
            if o is None:
                continue
            rows.append(o)
            fires.append(bool(e["fires"]))
        x = np.asarray(fires, dtype=bool)
        y = np.asarray([r["net_move_bp"] for r in rows], dtype=float)
        fam_rows = [r for r, fl in zip(rows, fires) if fl]
        comp_rows = [r for r, fl in zip(rows, fires) if not fl]
        entry = {"n_with_outcome": len(rows), "n_firing": int(x.sum()),
                 "n_complement": int((~x).sum()),
                 "family": _describe(fam_rows, lean), "complement": _describe(comp_rows, lean),
                 "all_approach": _describe(rows, lean)}
        if x.sum() and (~x).sum():
            entry["edge_vs_complement_bp"] = round(float(y[x].mean() - y[~x].mean()), 4)
            entry["edge_vs_all_approach_bp"] = round(float(y[x].mean() - y.mean()), 4)
            entry["test"] = stats.circular_shift_p(y, x)
            entry["bootstrap_ci"] = stats.bootstrap_ci(y, x)
        else:
            entry["edge_vs_complement_bp"] = None
            entry["test"] = {"p_value": None}
            entry["bootstrap_ci"] = {"lo": None, "hi": None}
        entry["measured"] = bool(entry["n_firing"] >= MIN_EPISODES)
        res["horizons"][str(h)] = entry
    res["measured"] = any(v["measured"] for v in res["horizons"].values())
    return res


def run() -> dict:
    mf = build_minute_frame()
    sf = build_second_frame()
    checks = mechanism_checks(mf)
    stop = [k for k in ("C1_repaint", "C2_prefix_truncation", "C5_wall_rule")
            if not checks[k].get("pass")]
    if stop:
        raise RuntimeError(f"mechanism check failed, study stops: {stop}")

    m_eps = families.minute_episodes(mf)
    w_eps = families.wall_episodes(sf)
    results: list[dict] = []
    for fam, (track, side, lean, _) in FAMILIES.items():
        if track == "T3":
            for variant in ("SIGN", "DOM"):
                spec = families.minute_family(mf, m_eps, fam, variant)
                results.append(_score_family(spec, T3_HORIZONS, outcomes.minute_outcome, mf))
        elif track == "T2":
            spec = families.wall_family(sf, w_eps, fam)
            results.append(_score_family(spec, T2_HORIZONS, outcomes.second_outcome, sf))
        else:
            spec = families.joint_family(mf, sf, m_eps, fam, "SIGN")
            results.append(_score_family(spec, T2_HORIZONS, outcomes.second_outcome, sf))

    grid = []
    for r in results:
        for h, e in r["horizons"].items():
            if e["measured"] and e["test"].get("p_value") is not None:
                grid.append({"family": r["family"], "variant": r["variant"], "horizon": h,
                             "lean": r["lean"], "p_value": e["test"]["p_value"],
                             "edge_bp": e["edge_vs_complement_bp"],
                             "family_mean_net_bp": e["family"]["net_move_bp"]["mean"],
                             "ci": e["bootstrap_ci"]})
    stats.holm(grid)

    verdict, reasons = "A_NO_CONTEXT_EDGE", []
    best_b = None
    for g in grid:
        lean_ok = (g["edge_bp"] > 0)
        pays = g["family_mean_net_bp"] > 0
        if lean_ok and g["holm_significant"] and pays:
            verdict = "C_USABLE_FOR_FORWARD_CONTEXT"
            reasons.append(g)
        elif lean_ok and (g["p_value"] <= 0.05) != pays:
            best_b = best_b or g
    if verdict != "C_USABLE_FOR_FORWARD_CONTEXT" and best_b is not None:
        verdict = "B_WEAK_CONTEXT_EDGE"
        reasons.append(best_b)

    unmeasured = {r["family"] + "/" + r["variant"]: max(h["n_firing"] for h in r["horizons"].values())
                  for r in results if not r["measured"]}
    payload = {
        "contract": contract_identity(),
        "generated_at_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
        "cost_model": {"round_trip_bp": RT_COST_BP, "meaningful_move_bp": 22.5,
                       "source": "CRYPTO_D5_4 / D5_5 K5"},
        "data": {"kline": data.provenance(), "journal": journal.census()},
        "mechanism_checks": checks,
        "episode_census": {
            "t3_structural_episodes": len(m_eps),
            "t3_by_side": {s: sum(1 for e in m_eps if e.side == s) for s in ("RES", "SUP")},
            "t2_wall_episodes": len(w_eps),
            "t2_by_side": {s: sum(1 for e in w_eps if e.side == s) for s in ("ASK", "BID")},
            "min_episodes_gate": MIN_EPISODES,
            "gate_reading": ("the gate is applied to firing episodes, the stricter of the two "
                             "readings of contract section 12")},
        "families": results,
        "holm_grid": grid,
        "verdict": verdict,
        "verdict_support": reasons,
        "not_measurable": unmeasured,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results_mc_r0.json").write_text(json.dumps(payload, indent=1, default=str) + "\n")
    return payload


if __name__ == "__main__":
    p = run()
    print("verdict:", p["verdict"])
    print("episodes T3", p["episode_census"]["t3_structural_episodes"],
          "T2", p["episode_census"]["t2_wall_episodes"])
    for r in p["families"]:
        print(f'{r["family"]}/{r["variant"]:4s} admitted={r["episodes_admitted"]:5d} '
              f'firing={r["episodes_firing"]:5d} measured={r["measured"]}')
