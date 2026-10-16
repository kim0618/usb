"""H-V2 Hierarchical Industry Intake R0: OFFLINE replay (research only, no production path).

    python -m app.dev.run_h_v2_industry_intake_r0 baseline <out_dir> [--limit N]
    python -m app.dev.run_h_v2_industry_intake_r0 analyze <out_dir>

``baseline`` re-runs the frozen D1 pipeline (``pipeline.assemble_candidate``: E1 -> E2 -> E3) point-in-time at
every replay session for every row of the research universe used by D1 (``CS_2024-10-25``), reading each
issuer's stored companyfacts and submissions once (``FACTS_ROOTS`` / ``SUBMISSION_ROOTS`` of
``run_strategy_h_v2_d1``), the HIST grouped-daily mirror for prices (truncated at the session), and writes one
compact row per (issuer, session). ``analyze`` evaluates the radar / bypass / rotation arms of
``industry_intake_r0`` against it. No network, no forward return, no write outside ``out_dir``.
"""

from __future__ import annotations

import argparse
import bisect
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from app.backtest.strategy_h_v2 import industry_intake_r0 as R
from app.backtest.strategy_h_v2.eligibility import (MIN_CLOSE_PRICE, MIN_TRAILING_DOLLAR_VOLUME,
                                                     MIN_TRAILING_SESSIONS)
from app.backtest.strategy_h_v2.research_priority import PriorityState

SESSION_FIRST, SESSION_LAST, SESSION_STEP = date(2026, 3, 2), date(2026, 9, 16), 5


def sessions_from(spy_days: list[date]) -> list[date]:
    days = [d for d in spy_days if SESSION_FIRST <= d <= SESSION_LAST]
    return days[::SESSION_STEP]


def _truncate(series: dict, days_sorted: list[date], session: date, keep: int = 260) -> dict:
    i = bisect.bisect_right(days_sorted, session)
    return {d: series[d] for d in days_sorted[max(0, i - keep):i]}


def baseline(out: Path, limit: int | None) -> None:
    from app.backtest.strategy_h0.facts import extract_companyfacts
    from app.backtest.strategy_h0.pilot import acceptance_index, read_gzip_json
    from app.backtest.strategy_h_v2.pipeline import assemble_candidate
    from app.backtest.strategy_h_v2.universe import build_universe
    from app.backtest.strategy_eqm_v0.xbrl_store import read_facts
    from app.dev import run_strategy_h_v2_d1 as D1

    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    reference_rows = [r for page in read_gzip_json(D1.REFERENCE)["pages"] for r in page.get("results", [])]
    known = datetime(2026, 9, 28, tzinfo=timezone.utc)
    universe = build_universe(reference_rows, snapshot_date=D1.SNAPSHOT_DATE, known_at=known)
    market, quality = D1.load_market(D1.DAILY)
    spy = market.get("SPY", {})
    spy_days = sorted(spy)
    sessions = sessions_from(spy_days)
    splits = defaultdict(list)
    for r in read_gzip_json(D1.SPLITS).get("results", []):
        splits[r["ticker"]].append(date.fromisoformat(r["execution_date"]))
    meta = {"universe": len(universe), "sessions": [s.isoformat() for s in sessions], "market_quality": quality,
            "reference": str(D1.REFERENCE), "daily": str(D1.DAILY), "limit": limit}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    closes_out = {}
    rows_written = 0
    with open(out / "baseline.jsonl", "w") as fh:
        for n, row in enumerate(universe[:limit] if limit else universe):
            cik = row.cik
            doc = sub = None
            root = D1.find_facts_root(cik) if cik else None
            if root is not None:
                doc = read_facts(root, cik)
            if cik:
                sub = D1.find_submission_doc(cik)
            facts = extract_companyfacts(doc, acceptance_index(sub)) if doc and sub else []
            fetched = doc is not None
            del doc
            recent_block = ((sub or {}).get("filings") or {}).get("recent") or {}
            filings = sorted(
                (datetime.fromisoformat(str(a).replace("Z", "+00:00")), f)
                for a, f in zip(recent_block.get("acceptanceDateTime") or [], recent_block.get("form") or []) if a)
            sic = R.industry_of((sub or {}).get("sic"))
            series = market.get(row.ticker, {})
            sdays = sorted(series)
            closes_out[row.ticker] = {d.isoformat(): series[d][0] for d in sdays if d >= date(2026, 1, 1)}
            for i, s in enumerate(sessions):
                cutoff = datetime(s.year, s.month, s.day, 21, 30, tzinfo=timezone.utc)
                tr = _truncate(series, sdays, s)
                snap = D1.market_snapshot(tr)
                accepted = [(ts, f) for ts, f in filings if ts <= cutoff]
                last_any = accepted[-1][0].date() if accepted else None
                last_periodic = next((ts.date() for ts, f in reversed(accepted) if f in R.PERIODIC_FORMS), None)
                ds_any = D1.days_since(last_any, s)
                result = assemble_candidate(
                    row, run_id="R0", generated_at=cutoff, data_cutoff=cutoff, facts=facts, facts_fetched=fetched,
                    split_dates=[d for d in splits.get(row.ticker, ()) if d <= s], market=snap,
                    days_since_latest_filing=ds_any, price_context=D1.price_context_of(tr, _truncate(spy, spy_days, s)))
                market_pass = bool(cik and snap and snap.latest_close is not None and snap.trailing_sessions >= MIN_TRAILING_SESSIONS
                                   and snap.latest_close >= MIN_CLOSE_PRICE and snap.trailing_avg_dollar_volume is not None
                                   and snap.trailing_avg_dollar_volume >= MIN_TRAILING_DOLLAR_VOLUME)
                fh.write(json.dumps({
                    "t": row.ticker, "cik": cik, "sic": sic, "s": i, "mp": market_pass,
                    "adv": None if not snap else snap.trailing_avg_dollar_volume,
                    "elig": result.eligibility.status.value,
                    "pri": None if result.priority is None else result.priority.value,
                    "ds_any": ds_any, "ds_per": D1.days_since(last_periodic, s), "facts": fetched}) + "\n")
                rows_written += 1
            if n % 250 == 0:
                print(f"{n}/{len(universe)} rows={rows_written} {time.time()-t0:.0f}s", flush=True)
    (out / "closes.json").write_text(json.dumps(closes_out))
    meta |= {"rows_written": rows_written, "secs": round(time.time() - t0)}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print("done", meta["rows_written"], meta["secs"])


def _benchmark_closes(ticker: str = "SPY") -> dict:
    """Benchmark closes from the same HIST grouped-daily mirror the baseline used (one ticker, streamed)."""
    import gzip
    from app.dev import run_strategy_h_v2_d1 as D1
    out = {}
    for path in sorted(D1.DAILY.rglob("*.json.gz")):
        doc = json.loads(gzip.decompress(path.read_bytes()))
        if "body" not in doc or "session" not in doc:
            continue
        day = date.fromisoformat(doc["session"])
        if day < date(2026, 1, 1):
            continue
        for row in doc["body"].get("results", []):
            if row.get("T") == ticker and row.get("c"):
                out[day] = float(row["c"])
    return out


def analyze(out: Path) -> None:
    meta = json.loads((out / "meta.json").read_text())
    sessions = [date.fromisoformat(s) for s in meta["sessions"]]
    closes_raw = json.loads((out / "closes.json").read_text())
    closes = {t: {date.fromisoformat(d): c for d, c in v.items()} for t, v in closes_raw.items()}
    rows = [json.loads(l) for l in open(out / "baseline.jsonl")]
    by_s = defaultdict(dict)
    for r in rows:
        by_s[r["s"]][r["t"]] = r
    sic = {r["t"]: r["sic"] for r in rows}
    spy = closes.get("SPY") or _benchmark_closes()     # the CS universe has no SPY row; read it from the mirror
    cal = sorted(spy)
    P = (PriorityState.P1_HIGH.value, PriorityState.P2_MEDIUM.value)
    report: dict = {"meta": meta}

    # -- taxonomy (issuers ever in the full-scan pool) ---------------------------------------------
    pool_all = {r["t"] for r in rows if r["mp"]}
    members = defaultdict(set)
    for t in pool_all:
        if sic.get(t):
            members[sic[t]].add(t)
    sizes = [len(v) for v in members.values()]
    report["taxonomy"] = {"source": "SEC submissions sic (4-digit)", "pool_issuers": len(pool_all),
                          "classified": sum(1 for t in pool_all if sic.get(t)),
                          "unclassified": sum(1 for t in pool_all if not sic.get(t)),
                          "industries": len(members), "issuers_per_industry_p10_p50_p90": R.quantiles(sizes),
                          "median": R.med(sizes),
                          "sic2_groups": len({k[:2] for k in members}), "sic3_groups": len({k[:3] for k in members})}
    size_cut = R.quantiles(sizes, (1 / 3, 2 / 3))
    size_bucket = {ind: ("small" if n <= size_cut[0] else "medium" if n <= size_cut[1] else "large") for ind, n in
                   ((k, len(v)) for k, v in members.items())}

    # -- representatives per quarter, radar RS per session -------------------------------------------
    reps_q: dict[str, dict[str, list[str]]] = {}
    rs: dict[int, dict[str, float | None]] = {}
    repr_stats = defaultdict(list)
    for i, s in enumerate(sessions):
        q = R.quarter_key(s)
        if q not in reps_q:
            snap = by_s[i]
            per = defaultdict(dict)
            for t, r in snap.items():
                if r["mp"] and r["sic"]:
                    per[r["sic"]][t] = r["adv"]
            reps_q[q] = {ind: R.representatives(m) for ind, m in per.items()}
        reps = reps_q[q]
        spy_r = R.window_return(spy, cal, s)
        rs[i] = {ind: R.basket_rs(reps.get(ind, []), closes, cal, s, spy_r) for ind in members}
        # representativeness: basket vs remainder median (21-session excess return)
        xs, ys = [], []
        for ind, mem in members.items():
            rest = [R.window_return(closes.get(t, {}), cal, s) for t in mem - set(reps.get(ind, []))
                    if by_s[i].get(t, {}).get("mp")]
            rest = [x - spy_r for x in rest if x is not None and spy_r is not None]
            if rs[i][ind] is None or not rest:
                continue
            m = R.med(rest)
            xs.append(rs[i][ind]); ys.append(m)
            repr_stats[size_bucket[ind]].append((rs[i][ind] > 0) == (m > 0))
            repr_stats["all"].append((rs[i][ind] > 0) == (m > 0))
        rho = R.spearman(xs, ys)
        repr_stats["rho"].append(rho)
    report["representativeness"] = {
        "spearman_basket_vs_remainder_median_per_session_p10_p50_p90": R.quantiles([r for r in repr_stats["rho"] if r is not None]),
        "direction_agreement_all": sum(repr_stats["all"]) / max(1, len(repr_stats["all"])),
        **{f"direction_agreement_{b}": sum(repr_stats[b]) / max(1, len(repr_stats[b])) for b in ("small", "medium", "large")},
        "size_bucket_cut_members": size_cut,
        "representatives_per_quarter": {q: sum(len(v) for v in r.values()) for q, r in reps_q.items()}}

    # -- arms -----------------------------------------------------------------------------------------
    arms = [R.Arm(v, b, n) for v in R.RADAR_VARIANTS for b in (None, "PERIODIC", "ANY") for n in R.ROTATIONS]
    arms.append(R.Arm("NONE", "PERIODIC", None))
    arms.append(R.Arm("NONE", "PERIODIC", 5))
    arms.append(R.Arm("NONE", "PERIODIC", 10))
    # comparison arms: production today refreshes at most 700 of ~3,100 market-passing CIKs per run,
    # oldest first, i.e. a full rotation every ~4-5 runs with no radar and no event bypass
    arms.append(R.Arm("NONE", None, 4))
    arms.append(R.Arm("NONE", None, 5))
    industries = sorted(members)
    baseline_cands = {i: {t for t, r in by_s[i].items() if r["pri"] in P} for i in range(len(sessions))}
    p1 = {i: {t for t, r in by_s[i].items() if r["pri"] == "P1_HIGH"} for i in range(len(sessions))}
    top10 = {}
    for i in range(len(sessions)):
        c = [r for r in by_s[i].values() if r["pri"] in P]
        c.sort(key=lambda r: (0 if r["pri"] == "P1_HIGH" else 1, r["ds_any"] if r["ds_any"] is not None else 10**6, r["t"]))
        top10[i] = {r["t"] for r in c[:10]}
    arm_out = {}
    for arm in arms:
        deep, cap_same, cap_idx, last_scan = [], Counter(), Counter(), {}
        miss_causes = Counter()
        size_miss = defaultdict(lambda: [0, 0])
        for i in range(len(sessions)):
            snap = by_s[i]
            pool = {t for t, r in snap.items() if r["mp"]}
            states = ({ind: R.ACTIVE for ind in industries} if arm.radar == "ALL" else
                      {ind: R.DORMANT for ind in industries} if arm.radar == "NONE" else R.radar_states(rs[i], arm.radar))
            byp = {t: (R.recent(snap[t]["ds_per"]) if arm.bypass == "PERIODIC" else R.recent(snap[t]["ds_any"]) if arm.bypass == "ANY" else False)
                   for t in pool}
            ds = R.deep_scan_set(pool=pool, industry=sic, states=states,
                                 rotation=R.rotation_industries(industries, i, arm.rotation), bypass=byp)
            deep.append((len(pool), len(ds)))
            for t in ds:
                last_scan[t] = i
            reps = reps_q[R.quarter_key(sessions[i])]
            for t in baseline_cands[i]:
                key = "P1" if t in p1[i] else "P2"
                cap_same[key + "_total"] += 1
                cap_same[key + "_hit"] += t in ds
                cap_same["top10_total"] += t in top10[i]
                cap_same["top10_hit"] += t in top10[i] and t in ds
                j = last_scan.get(t)
                fresh = j is not None and by_s[j].get(t, {}).get("pri") == snap[t]["pri"] and by_s[j][t]["elig"] == snap[t]["elig"]
                cap_idx[key + "_total"] += 1
                cap_idx[key + "_hit"] += fresh
                cap_idx["top10_total"] += t in top10[i]
                cap_idx["top10_hit"] += t in top10[i] and fresh
                ind = sic.get(t)
                mem = sorted(members.get(ind, ()), key=lambda u: -(snap.get(u, {}).get("adv") or 0))
                rank = mem.index(t) if t in mem else None
                bucket = ("unclassified" if ind is None else "representative" if t in reps.get(ind, []) else
                          "top_quartile" if rank is not None and rank < len(mem) / 4 else
                          "small_half" if rank is not None and rank >= len(mem) / 2 else "mid")
                size_miss[bucket][0] += 1
                if not fresh:
                    size_miss[bucket][1] += 1
                    if ind is None:
                        miss_causes["taxonomy_unclassified"] += 1
                    elif states.get(ind) == R.UNKNOWN:
                        miss_causes["radar_unknown_no_representative_data"] += 1
                    elif j is not None:
                        miss_causes["state_changed_since_last_deep_scan"] += 1
                    elif bucket == "small_half":
                        miss_causes["dormant_industry_small_cap_idiosyncratic"] += 1
                    else:
                        miss_causes["dormant_industry_top3_quiet"] += 1
        tot_pool = sum(p for p, _ in deep)
        tot_deep = sum(d for _, d in deep)
        rate = lambda c, k: c[k + "_hit"] / c[k + "_total"] if c[k + "_total"] else None   # noqa: E731
        arm_out[arm.name] = {
            "deep_scan_mean_per_session": tot_deep / len(deep), "pool_mean_per_session": tot_pool / len(deep),
            "deep_scan_reduction": 1 - tot_deep / tot_pool,
            "same_session_capture": {k: rate(cap_same, k) for k in ("P1", "P2", "top10")},
            "index_capture": {k: rate(cap_idx, k) for k in ("P1", "P2", "top10")},
            "index_capture_all": (cap_idx["P1_hit"] + cap_idx["P2_hit"]) / max(1, cap_idx["P1_total"] + cap_idx["P2_total"]),
            "baseline_candidate_sessions": cap_idx["P1_total"] + cap_idx["P2_total"],
            "misses": sum(miss_causes.values()), "miss_causes": dict(miss_causes.most_common()),
            "miss_rate_by_size": {b: (m / n if n else None, n) for b, (n, m) in size_miss.items()}}
    report["arms"] = arm_out
    report["baseline"] = {"pool_mean": sum(len([1 for r in by_s[i].values() if r["mp"]]) for i in range(len(sessions))) / len(sessions),
                          "P1_mean": sum(len(p1[i]) for i in p1) / len(p1),
                          "P1P2_mean": sum(len(baseline_cands[i]) for i in baseline_cands) / len(baseline_cands)}
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: report[k] for k in ("taxonomy", "representativeness", "baseline")}, indent=1, default=str))
    for name, a in arm_out.items():
        print(f"{name:34} reduce {a['deep_scan_reduction']:.1%} deep/day {a['deep_scan_mean_per_session']:.0f} "
              f"idx P1 {a['index_capture']['P1']:.1%} P2 {a['index_capture']['P2']:.1%} top10 {a['index_capture']['top10']:.1%} "
              f"all {a['index_capture_all']:.1%} | same P1 {a['same_session_capture']['P1']:.1%}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=("baseline", "analyze"))
    p.add_argument("out")
    p.add_argument("--limit", type=int)
    a = p.parse_args(argv)
    if a.cmd == "baseline":
        baseline(Path(a.out), a.limit)
    else:
        analyze(Path(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
