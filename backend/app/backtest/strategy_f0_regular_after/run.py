"""F0 first execution: preflight -> features + PIT -> outcomes -> gates -> verdict -> diagnostics.

Order is structural:

1. rules checksum, static preflight (fail-closed);
2. per symbol (worker): features for every eligible session, PIT-1 poison and PIT-3 plant on the
   same features; after-close bars are only stashed, never read;
3. PIT-2 on the declared 12 sessions; any PIT failure stops the run with no verdict;
4. outcomes from the stash, baseline, hypotheses, gates, verdict -> verdict.json (O_EXCL);
5. diagnostics (single-feature buckets, matched control, secondary horizons, 16:15 entry,
   16:00 auction, SEC events). The verdict function takes none of them.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time as _time

import numpy as np

from app.backtest.strategy_f0_regular_after import (config, diagnostics as DG, execution as EX,
                                                   features as F, identity, params as PR, pit,
                                                   preflight, stats as S, tape as T, universe as UV,
                                                   verdict as V)
from app.market.calendar import MarketCalendar

REPO = Path(__file__).resolve().parents[4]
RUNS = REPO / "data/runtime/strategy_f_candidate/runs"
PART_KEYS = ("has_regular", "c1559", "vwap_unavailable", "rvol_history_count",
             "age_return_1500_1600", "age_return_1530_1600", "age_return_1545_1600")
NAN = float("nan")


class PitFailure(RuntimeError):
    pass


# -- json ----------------------------------------------------------------------------------------

def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return float(x) if np.isfinite(x) else None
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.ndarray):
        return _clean(x.tolist())
    return x


def dump(path: Path, payload, *, exclusive: bool = False) -> str:
    body = (json.dumps(_clean(payload), sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    fd = os.open(path, flags, 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(body)
    return hashlib.sha256(body).hexdigest()


# -- worker --------------------------------------------------------------------------------------

_G: dict = {}


def _init(p, window, sessions):
    _G.update(p=p, window=window, clock=T.DayClock(sessions))


def process_symbol(task: tuple) -> dict:
    symbol, days, prev_close, spy_orig, spy_pois, pit2_days = task
    p, window = _G["p"], _G["window"]
    tp = T.load(symbol, p.sessions, _G["clock"])
    if tp is None:
        return {"symbol": symbol, "missing": True, "days": days}
    integrity, read_set = tp.integrity, tp.read_set
    tp = T.subset(tp, tp.minute >= p.reg_start)       # F0 never reads a bar before 09:30 ET
    rows, pit2_parts = [], {}
    pit1 = Counter()
    examples = []
    for d in days:
        part = F.minute_part(tp, d, p)
        pt = pit.poisoned(tp, d, p, window)
        ppart = F.minute_part(pt, d, p)
        f0 = F.finalize(part, prev_close[d], spy_orig[d], p)
        f1 = F.finalize(ppart, prev_close[d], spy_pois[d], p)
        m0, m1 = F.masks(f0, p), F.masks(f1, p)
        valid0 = (np.isfinite(part["c1559"]), part["vwap_unavailable"], part["has_regular"])
        valid1 = (np.isfinite(ppart["c1559"]), ppart["vwap_unavailable"], ppart["has_regular"])
        pit1["rows"] += 1
        same_part = F.identical(part, ppart)
        same_f, same_m, same_v = F.identical(f0, f1), m0 == m1, valid0 == valid1
        pit1["part_diff"] += not same_part
        pit1["feature_diff"] += not same_f
        pit1["mask_diff"] += not same_m
        pit1["validity_diff"] += not same_v
        if not (same_part and same_f and same_m and same_v) and len(examples) < 5:
            examples.append([symbol, d])
        lk0 = F.finalize(F.leaky_minute_part(tp, d, p), prev_close[d], spy_orig[d], p)
        lk1 = F.finalize(F.leaky_minute_part(pt, d, p), prev_close[d], spy_pois[d], p)
        pit1["leak_rows"] += 1
        pit1["leak_detected_rows"] += not F.identical(lk0, lk1)
        sel = (tp.day == d) & (tp.minute >= p.reg_end)
        stash = (tp.minute[sel].astype(np.int16), tp.o[sel], tp.h[sel], tp.l[sel], tp.c[sel], tp.v[sel])
        reg = (tp.day == d) & (tp.minute < p.reg_end)
        p955, _ = F._prevailing(tp.minute[reg].astype(np.int32), tp.c[reg], 955, p.prevailing_lookback)
        rows.append({"symbol": symbol, "d": d, **{k: part[k] for k in PART_KEYS}, "features": f0,
                     "masks": m0, "stash": stash, "p955": p955,
                     "day_return_raw": F.day_return(part, prev_close[d])})
        if d in pit2_days:
            pit2_parts[d] = part
    return {"symbol": symbol, "missing": False, "rows": rows, "pit1": dict(pit1), "pit1_examples": examples,
            "integrity": integrity, "read_set": read_set, "pit2_parts": pit2_parts}


def select_baseline(rows: list[dict]) -> list[dict]:
    """Baseline = RESOLVED rows only (15:59 bar, exact 16:05 bar, resolved exit). H rows are drawn
    from this list, so H and baseline share one execution-eligibility definition."""
    return [r for r in rows if r.get("status") == "RESOLVED"]


# -- main ----------------------------------------------------------------------------------------

def _git() -> dict:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True).stdout
    return {"head": head, "dirty": bool(status.strip()), "dirty_entries": len(status.splitlines())}


def execute(workers: int = 5, log=print) -> Path:
    started = _time.time()
    rules = config.load_rules()                                  # RulesChanged -> stop
    failed = [r for r in preflight.validate(rules.raw) if not r["ok"]]
    if failed:
        raise config.F0HardFail(f"F0 EXECUTION BLOCKED - RULES/PREFLIGHT FAILURE: {failed}")
    p = PR.from_rules(rules)
    window = pit.synthetic_window(rules)
    sessions = list(p.sessions)
    cal = MarketCalendar("America/New_York")

    log("daily panel + universe")
    daily = UV.load_daily()
    panel, grid = daily.panel, daily.grid
    gidx = {s.isoformat(): i for i, s in enumerate(grid)}
    if any(s not in gidx for s in sessions):
        raise config.F0HardFail("a primary session is missing from the daily grid")
    col = {t: j for j, t in enumerate(panel.tickers)}
    eligible = {}
    for d, s in enumerate(sessions):
        eligible[d] = UV.eligibility(panel, grid, gidx[s], cal)
    prev_close = {t: {} for t in panel.tickers}
    tasks_days: dict[str, list[int]] = {}
    for d, s in enumerate(sessions):
        gi = gidx[s]
        for j in np.flatnonzero(eligible[d]):
            t = panel.tickers[j]
            tasks_days.setdefault(t, []).append(d)
            prev_close[t][d] = float(panel.close[gi - 1, j])
    spy = "SPY"
    spy_prev = {d: float(panel.close[gidx[s] - 1, col[spy]]) for d, s in enumerate(sessions)}
    pit2_days = set(PR.pit2_indices(p))

    log("SPY first")
    _init(p, window, sessions)
    nan_arr = {d: NAN for d in range(len(sessions))}
    spy_res = process_symbol((spy, list(range(len(sessions))), spy_prev, nan_arr, nan_arr, set()))
    if spy_res["missing"]:
        raise config.F0HardFail("SPY minute data missing")
    spy_orig = {r["d"]: r["day_return_raw"] for r in spy_res["rows"]}
    # poisoned SPY day return per d, from the same poisoned tape construction
    spy_tape = T.load(spy, p.sessions, T.DayClock(sessions))
    spy_tape = T.subset(spy_tape, spy_tape.minute >= p.reg_start)
    spy_pois = {d: F.day_return(F.minute_part(pit.poisoned(spy_tape, d, p, window), d, p), spy_prev[d])
                for d in range(len(sessions))}

    tasks = [(t, days, prev_close[t], spy_orig, spy_pois, pit2_days) for t, days in sorted(tasks_days.items())]
    log(f"symbols {len(tasks)} x workers {workers}")
    results = []
    from multiprocessing import get_context
    with get_context("fork").Pool(workers, initializer=_init, initargs=(p, window, sessions)) as pool:
        for i, res in enumerate(pool.imap(process_symbol, tasks, chunksize=4)):
            results.append(res)
            if (i + 1) % 250 == 0:
                log(f"  {i + 1}/{len(tasks)}")

    # -- integrity + PIT -------------------------------------------------------------------------
    integ = Counter()
    read_set = list(spy_res["read_set"])
    pit1 = Counter(spy_res["pit1"])
    examples = list(spy_res["pit1_examples"])
    for k, v in spy_res["integrity"].items():
        integ[k] += v
    missing_symbols = []
    for res in results:
        if res["missing"]:
            missing_symbols.append(res["symbol"])
            continue
        for k, v in res["integrity"].items():
            integ[k] += v
        pit1.update(res["pit1"])
        examples += res["pit1_examples"]
        read_set += res["read_set"]
    critical = {k: integ[k] for k in ("sha_mismatch", "dup_conflict", "non_minute_ts", "invalid_ohlc_used",
                                      "nonpositive_price_used", "null_field_used")}
    integrity_critical = any(v > 0 for v in critical.values())

    pit1_pass = pit1["part_diff"] == pit1["feature_diff"] == pit1["mask_diff"] == pit1["validity_diff"] == 0
    pit3_pass = pit1["leak_detected_rows"] > 0
    # PIT-2
    pit2 = {"sessions": [], "diff": Counter()}
    parts_by_day: dict[int, dict[str, dict]] = {}
    for res in results:
        if not res["missing"]:
            for d, part in res["pit2_parts"].items():
                parts_by_day.setdefault(d, {})[res["symbol"]] = part
    for d in sorted(pit2_days):
        gi = gidx[sessions[d]]
        pp = UV.poisoned_panel(panel, gi, p.pit2_seed)
        el1 = UV.eligibility(pp, grid, gi, cal)
        diff = pit2["diff"]
        diff["universe"] += int(np.count_nonzero(el1 != eligible[d]))
        spy_p0, spy_p1 = panel.close[gi - 1, col[spy]], pp.close[gi - 1, col[spy]]
        diff["spy_prev_close"] += int(not (spy_p0 == spy_p1))
        spy_part = next(r for r in spy_res["rows"] if r["d"] == d)
        for t, part in parts_by_day.get(d, {}).items():
            j = col[t]
            a, b = panel.close[gi - 1, j], pp.close[gi - 1, j]
            diff["prev_close"] += int(not (a == b))
            dr0 = F.day_return({"c1559": spy_part["c1559"]}, spy_p0)
            dr1 = F.day_return({"c1559": spy_part["c1559"]}, spy_p1)
            f0, f1 = F.finalize(part, a, dr0, p), F.finalize(part, b, dr1, p)
            diff["features"] += not F.identical(f0, f1)
            diff["masks"] += F.masks(f0, p) != F.masks(f1, p)
            diff["rows"] += 1
        pit2["sessions"].append(sessions[d])
    pit2_pass = all(v == 0 for k, v in pit2["diff"].items() if k != "rows")
    pit_report = {"PIT-1": {"pass": pit1_pass, **{k: pit1[k] for k in ("rows", "part_diff", "feature_diff",
                                                                          "mask_diff", "validity_diff")},
                            "examples": examples[:10], "synthetic_window": list(window), "seed": p.pit1_seed},
                  "PIT-2": {"pass": pit2_pass, "sessions": pit2["sessions"], **dict(pit2["diff"]), "seed": p.pit2_seed},
                  "PIT-3": {"pass": pit3_pass, "leak_rows": pit1["leak_rows"], "detected_rows": pit1["leak_detected_rows"]},
                  "all_pass": bool(pit1_pass and pit2_pass and pit3_pass),
                  "order": "computed before any entry/exit/return/MFE/MAE"}

    # -- identity + run dir ---------------------------------------------------------------------------
    rs_sorted = sorted((f, sha) for f, sha, _ in read_set)
    minute_digest = hashlib.sha256("".join(f"{f}\t{s}\n" for f, s in rs_sorted).encode()).hexdigest()
    daily_digest = hashlib.sha256("".join(f"{f}\t{s}\n" for f, s in daily.read_set).encode()).hexdigest()
    parts = {"rules_checksum": rules.checksum, "code_digest": identity.code_digest(),
             "minute_read_set": minute_digest, "daily_read_set": daily_digest,
             "sessions": hashlib.sha256(",".join(sessions).encode()).hexdigest()}
    ident_digest, run_id = identity.run_identity(parts)
    base_dir = RUNS / run_id
    out = base_dir
    if (base_dir / "verdict.json").exists() or (base_dir / "pit_audit.json").exists():
        k = 2
        while (base_dir / f"repeat-{k}").exists():
            k += 1
        out = base_dir / f"repeat-{k}"
    out.mkdir(parents=True, exist_ok=False)
    digests = {}
    digests["pit_audit.json"] = dump(out / "pit_audit.json", pit_report)
    manifest = {"run_id": run_id, "identity_digest": ident_digest, "identity_parts": parts,
                "rules_file": str(config.RULES_PATH.relative_to(REPO)), "git": _git(),
                "bootstrap": {"seed": p.bootstrap_seed, "iterations": p.bootstrap_iterations},
                "sessions": sessions, "minute_pages": len(read_set),
                "minute_sources": dict(Counter(src for _, _, src in read_set)),
                "run_started_utc": datetime.fromtimestamp(started, timezone.utc).isoformat(),
                "python": sys.version, "numpy": np.__version__, "platform": platform.platform(),
                "workers": workers, "output_dir": str(out.relative_to(REPO))}
    if not pit_report["all_pass"]:
        manifest["status"] = "F0 PIT FAIL - NO VERDICT"
        dump(out / "manifest.json", manifest)
        raise PitFailure(f"F0 PIT FAIL - NO VERDICT ({out})")

    # -- outcomes (first read of any after-close price) -------------------------------------------
    log("outcomes")
    all_rows = [r for res in results if not res["missing"] for r in res["rows"]]
    all_rows.sort(key=lambda r: (r["d"], r["symbol"]))
    status = Counter()
    for r in all_rows:
        if not r["has_regular"]:
            r["status"] = "NO_MINUTE"
        elif not np.isfinite(r["c1559"]):
            r["status"] = "FEATURE_INVALID_NO_1559"
        else:
            mi, o, h, l, c, _ = r["stash"]
            oc = EX.resolve(mi.astype(np.int32), o, h, l, c, p.entry_minute, p.exit_target, p.exit_lookback)
            r["outcome"] = oc
            r["status"] = oc.status
        status[r["status"]] += 1
    n_eligible = sum(int(eligible[d].sum()) for d in range(len(sessions)))
    status["NO_MINUTE"] += sum(len(res["days"]) for res in results if res["missing"])
    base = select_baseline(all_rows)
    B_sym = np.array([r["symbol"] for r in base])
    B_day = np.array([r["d"] for r in base], dtype=np.int64)
    B_g = np.array([r["outcome"].gross for r in base])
    B_mfe = np.array([r["outcome"].mfe for r in base])
    B_mae = np.array([r["outcome"].mae for r in base])
    B_key = np.arange(len(base))
    B = S.Group(B_sym, B_day, B_g, B_mfe, B_mae, B_key)
    feats = {n: np.array([r["features"][n] for r in base]) for n in PR.FEATURE_NAMES}
    hmask = {h: np.array([r["masks"][h] for r in base], dtype=bool) for h in p.hypotheses}

    valid_rows = [r for r in all_rows if np.isfinite(r["c1559"])]
    with_entry = [r for r in valid_rows if r["status"] in ("RESOLVED", "UNRESOLVED_EXIT")]
    ages = np.array([r["outcome"].exit_age_seconds for r in base])

    def dist(x):
        x = x[np.isfinite(x)]
        if not x.size:
            return {"N": 0}
        return {"N": int(x.size), **{f"p{q}": float(np.percentile(x, q)) for q in (1, 5, 25, 50, 75, 95, 99)},
                "min": float(x.min()), "max": float(x.max()), "mean": float(x.mean())}

    coverage = {
        "sessions": len(sessions), "pit_eligible_symbol_sessions": n_eligible,
        "status_counts": dict(status), "missing_minute_symbols": sorted(missing_symbols),
        "rows_with_regular_bar": sum(1 for r in all_rows if r["has_regular"]),
        "rows_with_1559_bar": len(valid_rows), "rows_with_exact_1605_bar": len(with_entry),
        "rows_with_resolved_exit": len(base), "baseline_N": len(base),
        "baseline_unique_symbols": int(np.unique(B_sym).size), "baseline_unique_sessions": int(np.unique(B_day).size),
        "vwap_unavailable_rows": sum(1 for r in valid_rows if r["vwap_unavailable"]),
        "exit_age_seconds": dist(ages), "exit_exact_target_bar_rate": float((ages == 0).mean()) if ages.size else None,
        "exit_lookback_fallback_rate": float((ages > 0).mean()) if ages.size else None,
        "price_age_seconds": {k: dist(np.array([r[f"age_{k}"] for r in valid_rows])) for k in p.anchors},
        "price_exact_anchor_rate": {k: float(np.mean([r[f"age_{k}"] == 0 for r in valid_rows if np.isfinite(r[f"age_{k}"])]))
                                    for k in p.anchors},
        "per_session_baseline": [int((B_day == d).sum()) for d in range(len(sessions))],
        "integrity": dict(integ), "integrity_critical": critical, "integrity_critical_any": integrity_critical,
        "ticker_suspect_baseline_rows": None,
    }
    figi = UV.figi_by_snapshot(UV.DAILY_ROOT, daily.snapshot_dates)
    prev_dates = [grid[gidx[s] - 1] for s in sessions]
    suspect = np.array([UV.ticker_suspect(figi, daily.snapshot_dates, prev_dates[d], t) for t, d in zip(B_sym, B_day)], bool)
    coverage["ticker_suspect_baseline_rows"] = int(suspect.sum())

    # feature quality (feature-valid rows)
    fq = {}
    for n in PR.FEATURE_NAMES:
        x = np.array([r["features"][n] for r in valid_rows])
        fin = x[np.isfinite(x)]
        fq[n] = {"valid_N": int(fin.size), "missing_N": int(x.size - fin.size),
                 "nan_rate": float(1 - fin.size / x.size) if x.size else None,
                 **({f"p{q}": float(np.percentile(fin, q)) for q in (1, 5, 50, 95, 99)} if fin.size else {}),
                 "min": float(fin.min()) if fin.size else None, "max": float(fin.max()) if fin.size else None}
    a = np.array([r["features"]["last15m_return"] for r in valid_rows])
    b = np.array([r["features"]["return_1545_1600"] for r in valid_rows])
    fq["alias_check"] = {"last15m_return==return_1545_1600_bitwise": bool(np.array_equal(a.view(np.int64), b.view(np.int64)))}
    hc = np.array([r["rvol_history_count"] for r in valid_rows])
    groups = {"0-4": (hc <= 4), "5-9": (hc >= 5) & (hc <= 9), "10-19": (hc >= 10) & (hc <= 19), "20": hc == 20}
    fq["rvol_history_groups"] = {k: int(v.sum()) for k, v in groups.items()}
    bhc = np.array([r["rvol_history_count"] for r in base])
    for h in ("H3", "H5"):
        sel = hmask[h]
        fq[f"rvol_history_share_{h}"] = {k: (float(((bhc >= lo) & (bhc <= hi))[sel].mean()) if sel.any() else None)
                                          for k, (lo, hi) in {"5-9": (5, 9), "10-19": (10, 19), "20": (20, 20)}.items()}

    # -- baseline, hypotheses, gates, verdict ------------------------------------------------------
    log("statistics")
    counts = S.draw_counts(p, len(sessions))
    baseline = {"definition": rules.raw["samples"]["baseline"], "unique_symbols": coverage["baseline_unique_symbols"],
                "unique_sessions": coverage["baseline_unique_sessions"], "metrics": S.metrics(B_g, B_mfe, B_mae, p)}
    hyp = {}
    for h in p.hypotheses:
        m = hmask[h]
        H = S.Group(B_sym[m], B_day[m], B_g[m], B_mfe[m], B_mae[m], B_key[m])
        hyp[h] = S.evaluate_hypothesis(H, B, counts, p, sessions)
        hyp[h]["gates"]["PIT"] = "PASS"
    matrix = {h: {g: hyp[h]["gates"].get(g, "N/A") for g in V.GATE_ORDER} for h in hyp}
    resolved_sessions = coverage["baseline_unique_sessions"]
    result = V.verdict(matrix, pit_pass=True, integrity_critical=integrity_critical,
                       resolved_sessions=resolved_sessions, p=p)

    digests["coverage.json"] = dump(out / "coverage.json", coverage)
    digests["feature_quality.json"] = dump(out / "feature_quality.json", fq)
    digests["baseline.json"] = dump(out / "baseline.json", baseline)
    core = {h: {k: v for k, v in r.items() if k in ("N", "unique_symbols", "unique_sessions", "sample_gate",
                                                     "metrics", "primary_tail", "mean_lift", "median", "gates")}
            for h, r in hyp.items()}
    digests["hypotheses.json"] = dump(out / "hypotheses.json", core)
    for name, key in (("bootstrap.json", "bootstrap"), ("extreme_removal.json", "extreme_removal"),
                      ("concentration.json", "concentration"), ("downside.json", "downside"),
                      ("time_consistency.json", "time_consistency"), ("cost_stress.json", "cost_stress")):
        digests[name] = dump(out / name, {h: r.get(key) for h, r in hyp.items()})
    digests["gate_matrix.json"] = dump(out / "gate_matrix.json", {"gates": list(V.GATE_ORDER), "matrix": matrix,
                                                                    "labels": result["labels"]})
    verdict_payload = {**result, "rules_checksum": rules.checksum, "run_id": run_id,
                       "resolved_sessions": resolved_sessions, "integrity_critical": integrity_critical,
                       "inputs": ["gate_matrix", "PIT", "integrity", "resolved_sessions"],
                       "excluded_inputs": ["secondary horizons", "16:15 diagnostic", "single-feature buckets",
                                           "matched control", "event diagnostic", "16:00 auction diagnostic"]}
    digests["verdict.json"] = dump(out / "verdict.json", verdict_payload, exclusive=True)
    log(f"VERDICT {result['verdict']} {result['labels']}")

    # -- diagnostics (after the verdict is on disk) --------------------------------------------------
    log("diagnostics")
    digests["feature_buckets.json"] = dump(out / "feature_buckets.json",
                                           {"note": "DIAGNOSTIC ONLY - no threshold search, verdict unchanged",
                                            **DG.feature_buckets(feats, B_g, B_mfe, p)})
    j_idx = np.array([col[t] for t in B_sym])
    mdv = {}
    for d in range(len(sessions)):
        mdv[d] = UV.median_dv20(panel, gidx[sessions[d]])
    cols = {"prev_close_bucket": np.array([prev_close[r["symbol"]][r["d"]] for r in base]),
            "median_dollar_volume_20_bucket": np.array([mdv[d][j] for d, j in zip(B_day, j_idx)]),
            "regular_dollar_volume_bucket": feats["regular_dollar_volume"]}
    digests["matched_control.json"] = dump(out / "matched_control.json",
                                           {h: DG.matched_control(cols, hmask[h], B_day, B_g, B_mfe, p) for h in p.hypotheses})

    # secondary horizons and 16:15 entry
    sec = {"note": "SECONDARY DIAGNOSTIC - computed after verdict.json; cannot change the verdict"}
    valid_arr = [r for r in all_rows if np.isfinite(r["c1559"])]
    vmask = {h: np.array([r["masks"][h] for r in valid_arr], bool) for h in p.hypotheses}
    specs = {**{k: (p.entry_minute, t) for k, t in p.secondary_targets.items()},
             "ENTRY_1615_EXIT_1700": (p.diag_entry_minute, p.exit_target)}
    for name, (em, tgt) in specs.items():
        oc = [EX.resolve(r["stash"][0].astype(np.int32), *r["stash"][1:5], em, tgt, p.exit_lookback) for r in valid_arr]
        ok = np.array([o.status == "RESOLVED" for o in oc])
        g = np.array([o.gross for o in oc]); mf = np.array([o.mfe for o in oc])
        entry_rows = sum(o.status != "NO_TRADE" for o in oc)
        block = {"entry_minute": em, "target_minute": tgt, "rows_with_entry": int(entry_rows),
                 "resolved": int(ok.sum()), "baseline": DG._brief(g[ok], mf[ok], p)}
        for h in p.hypotheses:
            sel = ok & vmask[h]
            ml, tl = S.lifts(g[sel], mf[sel], g[ok], mf[ok], p)
            block[h] = {**DG._brief(g[sel], mf[sel], p), "mean_lift": ml, "tail_lift": tl}
        sec[name] = block
    digests["secondary_diagnostics.json"] = dump(out / "secondary_diagnostics.json", sec)

    # 16:00 auction
    auc = {"note": "DIAGNOSTIC ONLY - the 16:00 bar is never a feature"}
    mv1555 = np.array([r["c1559"] / r["p955"] - 1 if np.isfinite(r["p955"]) else NAN for r in base])
    o1600, c1600, mv1604 = [], [], []
    for r in base:
        mi, o, h, l, c, _ = r["stash"]
        k = np.flatnonzero(mi == p.reg_end)
        o1600.append(o[k[0]] / r["c1559"] - 1 if k.size else NAN)
        c1600.append(c[k[0]] / r["c1559"] - 1 if k.size else NAN)
        w = np.flatnonzero((mi >= p.reg_end) & (mi < p.entry_minute))
        mv1604.append(c[w[-1]] / r["c1559"] - 1 if w.size else NAN)
    for label, arr in (("move_1550s_to_1559", mv1555), ("open_1600_vs_c1559", np.array(o1600)),
                       ("close_1600_vs_c1559", np.array(c1600)), ("last_1600_1604_vs_c1559", np.array(mv1604))):
        auc[label] = {"baseline": dist(arr), **{h: dist(arr[hmask[h]]) for h in p.hypotheses}}
    digests["auction_diagnostic.json"] = dump(out / "auction_diagnostic.json", auc)

    # SEC events
    cik_maps = [UV.cik_by_ticker(UV.DAILY_ROOT, daily.snapshot_dates, prev_dates[d]) for d in range(len(sessions))]
    ev = DG.sec_events(B_sym, B_day, sessions, prev_dates, cik_maps)
    evd = {"note": "DIAGNOSTIC ONLY - no news/earnings calendar; UNKNOWN is never merged into NON_EVENT",
           "counts": dict(Counter(ev.tolist()))}
    for grp in ("EVENT", "NON_EVENT", "UNKNOWN_EVENT_STATUS"):
        sel = ev == grp
        evd[grp] = {"baseline": DG._brief(B_g[sel], B_mfe[sel], p),
                    **{h: DG._brief(B_g[sel & hmask[h]], B_mfe[sel & hmask[h]], p) for h in p.hypotheses}}
    digests["event_diagnostic.json"] = dump(out / "event_diagnostic.json", evd)

    # rows (audit trail)
    import pyarrow as pa, pyarrow.parquet as pq
    table = pa.table({"symbol": B_sym.tolist(), "session": [sessions[d] for d in B_day],
                      "gross": B_g, "mfe": B_mfe, "mae": B_mae,
                      "exit_age_seconds": ages, **{f"feat_{n}": feats[n] for n in PR.FEATURE_NAMES},
                      **{f"mask_{h}": hmask[h] for h in p.hypotheses}, "ticker_suspect": suspect,
                      "event_status": ev.tolist()})
    pq.write_table(table, out / "baseline_rows.parquet")
    manifest["elapsed_seconds"] = round(_time.time() - started, 1)
    manifest["artifact_sha256"] = digests
    manifest["status"] = "COMPLETE"
    dump(out / "manifest.json", manifest)
    return out
