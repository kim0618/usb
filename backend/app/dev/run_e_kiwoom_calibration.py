"""E-KIWOOM-CAL K0: how far apart are the Massive RVOL and the Kiwoom RVOL on the same rows.

The protocol is frozen before this runs (``e_kiwoom_rvol_calibration_k0_rules.json``); this module
only measures and compares against the limits that file already fixed. It computes no return.

Both sides use the frozen definition, each from its own source:

* Massive: ``strategy_e1_premarket`` over the development minute tape, the same code the E-MAX V1
  research used, assembled through ``dataset.build`` so the non-RVOL H5 features are the frozen ones;
* Kiwoom: the runtime's own store (``RvolStore``), whose denominator is the rolling prior median of
  staged premarket dollar volume, window 20, minimum 5.

A row enters the comparison only when both sides hold that (symbol, session) and both produce a
finite RVOL. Because Kiwoom's history only starts in 2026, the overlap is reported with its range
and never described as a two-year validation.

One confound is measured rather than hidden: over the overlap the Kiwoom denominator is often built
from fewer prior sessions than Massive's twenty. The depth-matched control recomputes the Massive
denominator over the same number of priors, so a source difference is not read off a window
difference.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import sys

import numpy as np

from app.backtest.strategy_e1_premarket import premarket as P
from app.strategy_e_max_rt.rvol_store import RVOL_MINIMUM, RVOL_WINDOW

RULES_PATH = Path(__file__).resolve().parents[3] / "docs/backtest/strategy_e_max/e_kiwoom_rvol_calibration_k0_rules.json"
THRESHOLD = 3.0          # the Massive reference, read back from the frozen H5 rules below
BENCHMARK = "SPY"


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def load_rules() -> dict:
    import hashlib
    body = RULES_PATH.read_text(encoding="utf-8")
    canonical = json.dumps(json.loads(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {"rules": json.loads(body),
            "canonical_sha256": hashlib.sha256(canonical.encode()).hexdigest()}


# -- the two sides ----------------------------------------------------------------------------------

def kiwoom_rows(path: Path) -> dict[str, list[tuple[date, float]]]:
    """Staged (session, premarket dollar volume) per symbol, oldest first, from the runtime store."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for symbol, session, value in connection.execute(
            "SELECT symbol, session, pm_dollar_volume FROM kiwoom_premarket_sessions "
            "WHERE staged=1 AND complete=1 AND pm_dollar_volume IS NOT NULL ORDER BY symbol, session"):
        out[symbol].append((date.fromisoformat(session), float(value)))
    connection.close()
    return dict(out)


def rolling_rvol(series: list[tuple[date, float]]) -> dict[date, tuple[float, int]]:
    """The frozen rule applied to one symbol's series: (rvol, priors used) per session."""
    out: dict[date, tuple[float, int]] = {}
    history: list[float] = []
    for session, value in series:
        if len(history) >= RVOL_MINIMUM:
            window = history[-RVOL_WINDOW:]
            median = float(np.median(window))
            if median > 0:
                out[session] = (value / median, len(window))
        if math.isfinite(value) and value > 0:
            history.append(value)
    return out


def massive_frame(symbols: list[str], root: Path, workers: int):
    """The frozen E1 premarket frame: eligible rows with the sealed features, Massive only."""
    from app.backtest.strategy_e0_overnight.config import load_rules as load_e0_rules
    from app.backtest.strategy_e0_overnight.dataset import load_benchmark_close, load_daily_rows
    from app.backtest.strategy_e1_premarket import cohort as CO, config as E1C, dataset as DS
    e1_rules = E1C.load_rules()
    log(f"cohort: {len(symbols)} symbols")
    built = CO.build(symbols, root, decision_last=P.DECISION_LAST_BAR, workers=workers,
                     progress=lambda text: log(text))
    e0_rules = load_e0_rules()
    daily, history = load_daily_rows(root, e0_rules)
    # SPY is the declared benchmark and is read the way E0 reads it, not looked up in the CS panel.
    benchmark = load_benchmark_close(root, e0_rules.snapshot_id, history.panel.sessions)
    arrays = {"symbols": built.symbols, "sessions": built.sessions, **built.values}
    rows = DS.build(arrays, daily, benchmark, e1_rules)
    log(f"massive rows {len(rows)} sessions {len(set(rows.sessions.tolist()))}")
    return rows, arrays


# -- statistics -------------------------------------------------------------------------------------

def ranks(values: np.ndarray) -> np.ndarray:
    order = values.argsort(kind="stable")
    out = np.empty_like(order, dtype=float)
    out[order] = np.arange(values.size, dtype=float)
    return out


def correlations(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    return {"pearson": float(np.corrcoef(a, b)[0, 1]),
            "spearman": float(np.corrcoef(ranks(a), ranks(b))[0, 1])}


def quantiles(values: np.ndarray) -> dict[str, float]:
    p = np.percentile(values, [10, 25, 50, 75, 90])
    return {"p10": float(p[0]), "p25": float(p[1]), "p50": float(p[2]),
            "p75": float(p[3]), "p90": float(p[4])}


def confusion(massive: np.ndarray, kiwoom: np.ndarray, threshold: float) -> dict[str, float]:
    m, k = massive >= threshold, kiwoom >= threshold
    tp, fp = int((m & k).sum()), int((~m & k).sum())
    fn, tn = int((m & ~k).sum()), int((~m & ~k).sum())
    union = tp + fp + fn
    return {"massive_pass": int(m.sum()), "kiwoom_pass": int(k.sum()),
            "true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn,
            "precision": tp / (tp + fp) if tp + fp else float("nan"),
            "recall": tp / (tp + fn) if tp + fn else float("nan"),
            "jaccard": tp / union if union else float("nan"),
            "agreement": (tp + tn) / massive.size if massive.size else float("nan")}


# -- H5 / R1 / Top3 / B2 on the overlap ---------------------------------------------------------------

def structural(rows, index: np.ndarray, kiwoom_rvol: np.ndarray, threshold: float) -> dict:
    """Swap only the RVOL column and see what the frozen decision does differently.

    The comparison is restricted to overlap rows on both sides, so the breadth denominator is the
    same row set for both; it is not the live canonical denominator and is labelled as such.
    """
    from app.backtest.strategy_e1_premarket import evaluate as EV
    from app.strategy_e_max import breadth, ranking, v1
    sessions = rows.sessions[index]
    symbols = rows.tickers[index].astype(str)
    base = {name: values[index] for name, values in rows.features.items()}
    variants = {}
    for label, rvol in (("massive", base["premarket_rvol"]), ("kiwoom", kiwoom_rvol)):
        features = dict(base) | {"premarket_rvol": rvol}
        variants[label] = EV.mask("H5", features)

    per_session, top3_same, breadth_same, decision_sessions = [], 0, 0, 0
    for session in sorted(set(sessions.tolist())):
        where = sessions == session
        eligible = int(where.sum())
        row = {"session": str(session), "eligible_rows": eligible}
        picks = {}
        for label in ("massive", "kiwoom"):
            keep = variants[label][where]
            names = symbols[where][keep]
            feats = {s: {"premarket_rvol": float(v)} for s, v in
                     zip(names, (base["premarket_rvol"] if label == "massive" else kiwoom_rvol)[where][keep])}
            order = ranking.order(v1.RANKING, tuple(sorted(names)), feats)
            picks[label] = {"count": int(keep.sum()), "top3": list(order[:v1.MAX_SELECTED]),
                            "high_breadth": breadth.multiplier(int(keep.sum()), eligible) != 1}
            row[f"{label}_h5"] = picks[label]["count"]
            row[f"{label}_top3"] = picks[label]["top3"]
            row[f"{label}_high_breadth"] = picks[label]["high_breadth"]
        if picks["massive"]["count"] or picks["kiwoom"]["count"]:
            decision_sessions += 1
            top3_same += picks["massive"]["top3"] == picks["kiwoom"]["top3"]
        breadth_same += picks["massive"]["high_breadth"] == picks["kiwoom"]["high_breadth"]
        per_session.append(row)

    counts_m = np.array([r["massive_h5"] for r in per_session], dtype=float)
    counts_k = np.array([r["kiwoom_h5"] for r in per_session], dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        relative = np.abs(counts_k - counts_m) / np.where(counts_m > 0, counts_m, np.nan)
    return {"sessions": len(per_session), "decision_sessions": decision_sessions,
            "h5_total_massive": int(counts_m.sum()), "h5_total_kiwoom": int(counts_k.sum()),
            "h5_count_median_relative_distortion":
                float(np.nanmedian(relative)) if np.isfinite(relative).any() else float("nan"),
            "top3_agreement": top3_same / decision_sessions if decision_sessions else float("nan"),
            "b2_state_agreement": breadth_same / len(per_session) if per_session else float("nan"),
            "per_session": per_session}


# -- K0 --------------------------------------------------------------------------------------------

def k0(args) -> int:
    from app.backtest.workspace.discovery import resolve_workspace_root
    contract = load_rules()
    limits = contract["rules"]["acceptance_limits_declared_before_results"]
    gate = contract["rules"]["sufficiency_gate"]
    log(f"K0 protocol {contract['canonical_sha256'][:12]}")

    kiwoom_series = kiwoom_rows(args.kiwoom)
    kiwoom = {symbol: rolling_rvol(series) for symbol, series in kiwoom_series.items()}
    root = resolve_workspace_root(args.workspace_root)
    available = {p.name for p in (root / "market_data/raw/massive/minute").iterdir() if p.is_dir()}
    symbols = sorted(set(kiwoom) & available)
    log(f"kiwoom symbols {len(kiwoom)} | tape symbols {len(available)} | overlap symbols {len(symbols)}")

    rows, arrays = massive_frame(symbols, root, args.workers)
    massive_pm = defaultdict(dict)
    for symbol, session, value in zip(arrays["symbols"].astype(str),
                                      arrays["sessions"].astype(str),
                                      arrays["pm_dollar_volume"]):
        massive_pm[symbol][date.fromisoformat(session)] = float(value)

    keep, k_values, depth = [], [], []
    for i, (session, symbol) in enumerate(zip(rows.sessions, rows.tickers.astype(str))):
        day = session if isinstance(session, date) else date.fromisoformat(str(session))
        hit = kiwoom.get(symbol, {}).get(day)
        massive_rvol = rows.features["premarket_rvol"][i]
        if hit is None or not math.isfinite(float(massive_rvol)):
            continue
        keep.append(i)
        k_values.append(hit[0])
        depth.append(hit[1])
    index = np.array(keep, dtype=int)
    kiwoom_rvol = np.array(k_values, dtype=float)
    depths = np.array(depth, dtype=int)
    massive_rvol = rows.features["premarket_rvol"][index]
    overlap_sessions = sorted({str(s) for s in rows.sessions[index]})
    log(f"overlap rows {index.size} sessions {len(overlap_sessions)} "
        f"{overlap_sessions[0] if overlap_sessions else '-'}..{overlap_sessions[-1] if overlap_sessions else '-'}")

    report: dict = {
        "format": "e-kiwoom-cal-k0-result-v1",
        "protocol_sha256": contract["canonical_sha256"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overlap": {"kiwoom_symbols": len(kiwoom), "tape_symbols": len(available),
                    "overlap_symbols": len(symbols),
                    "massive_eligible_rows": int(len(rows)), "overlap_rows": int(index.size),
                    "overlap_sessions": len(overlap_sessions),
                    "session_range": [overlap_sessions[0], overlap_sessions[-1]] if overlap_sessions else None,
                    "rows_per_session": {s: int((rows.sessions[index].astype(str) == s).sum())
                                         for s in overlap_sessions},
                    "kiwoom_denominator_depth": {"median": float(np.median(depths)) if depths.size else None,
                                                 "min": int(depths.min()) if depths.size else None,
                                                 "max": int(depths.max()) if depths.size else None,
                                                 "full_window_rows": int((depths >= RVOL_WINDOW).sum())},
                    "note": "Kiwoom history begins in 2026; this is not a two-year validation"},
    }

    if (len(overlap_sessions) < gate["minimum_overlap_sessions"]
            or index.size < gate["minimum_overlap_rows"]):
        report["verdict"] = contract["rules"]["verdicts"]["insufficient"]
        _write(args.out, report)
        print(json.dumps({k: report[k] for k in ("overlap", "verdict")}, indent=1, default=str))
        return 0

    ratio = kiwoom_rvol / massive_rvol
    log_ratio = np.log(ratio[ratio > 0])
    report["distribution"] = {
        "correlation": correlations(massive_rvol, kiwoom_rvol),
        "massive_rvol": quantiles(massive_rvol), "kiwoom_rvol": quantiles(kiwoom_rvol),
        "ratio": quantiles(ratio) | {"mean": float(ratio.mean())},
        "log_ratio": {"mean": float(log_ratio.mean()), "sd": float(log_ratio.std(ddof=1)),
                      "median": float(np.median(log_ratio))},
        "absolute_error": quantiles(np.abs(kiwoom_rvol - massive_rvol)),
        "relative_error": quantiles(np.abs(kiwoom_rvol - massive_rvol) / massive_rvol),
    }
    report["threshold_3p0"] = confusion(massive_rvol, kiwoom_rvol, THRESHOLD)
    report["structural"] = structural(rows, index, kiwoom_rvol, THRESHOLD)

    # depth-matched control: the same number of priors on both sides, so a window difference is not
    # read as a source difference.
    control = []
    for position, i in enumerate(index):
        symbol = str(rows.tickers[i])
        day = rows.sessions[i]
        day = day if isinstance(day, date) else date.fromisoformat(str(day))
        series = sorted((d, v) for d, v in massive_pm.get(symbol, {}).items() if d < day)
        history = [v for _, v in series if math.isfinite(v) and v > 0]
        k = int(depths[position])
        if len(history) >= k >= RVOL_MINIMUM:
            median = float(np.median(history[-k:]))
            if median > 0:
                control.append((massive_pm[symbol][day] / median, kiwoom_rvol[position]))
    if control:
        matched = np.array(control, dtype=float)
        report["depth_matched_control"] = {
            "rows": int(matched.shape[0]),
            "correlation": correlations(matched[:, 0], matched[:, 1]),
            "ratio": quantiles(matched[:, 1] / matched[:, 0]),
            "threshold_3p0": confusion(matched[:, 0], matched[:, 1], THRESHOLD),
            "note": "Massive denominator recomputed over the same number of priors Kiwoom used; "
                    "a diagnostic control, not a rule change"}

    checks = {
        "rvol_spearman": (report["distribution"]["correlation"]["spearman"], ">=", limits["rvol_spearman_min"]),
        "rvol_pearson": (report["distribution"]["correlation"]["pearson"], ">=", limits["rvol_pearson_min"]),
        "median_ratio_low": (report["distribution"]["ratio"]["p50"], ">=", limits["median_ratio_band"][0]),
        "median_ratio_high": (report["distribution"]["ratio"]["p50"], "<=", limits["median_ratio_band"][1]),
        "ratio_p10": (report["distribution"]["ratio"]["p10"], ">=", limits["ratio_p10_p90_band"][0]),
        "ratio_p90": (report["distribution"]["ratio"]["p90"], "<=", limits["ratio_p10_p90_band"][1]),
        "jaccard": (report["threshold_3p0"]["jaccard"], ">=", limits["threshold_3p0_jaccard_min"]),
        "recall": (report["threshold_3p0"]["recall"], ">=", limits["threshold_3p0_recall_min"]),
        "precision": (report["threshold_3p0"]["precision"], ">=", limits["threshold_3p0_precision_min"]),
        "h5_count_distortion": (report["structural"]["h5_count_median_relative_distortion"], "<=",
                                limits["h5_candidate_count_median_relative_distortion_max"]),
        "top3_agreement": (report["structural"]["top3_agreement"], ">=", limits["top3_set_agreement_min"]),
        "b2_state_agreement": (report["structural"]["b2_state_agreement"], ">=",
                               limits["b2_high_breadth_state_agreement_min"]),
    }
    verdict_rows = {name: {"value": value, "limit": limit, "operator": op,
                           "pass": bool(value >= limit if op == ">=" else value <= limit)
                           if value == value else False}
                    for name, (value, op, limit) in checks.items()}
    accepted = all(row["pass"] for row in verdict_rows.values())
    report["limit_checks"] = verdict_rows
    report["verdict"] = contract["rules"]["verdicts"]["accepted" if accepted else "rejected"]

    # candidate thresholds, from the distribution only
    pass_rate = float((massive_rvol >= THRESHOLD).mean())
    mapped = float(np.quantile(kiwoom_rvol, 1.0 - pass_rate)) if 0 < pass_rate < 1 else float("nan")
    report["threshold_candidates"] = {
        "K3.0": THRESHOLD,
        "K_MAP": round(mapped, 4),
        "K_ROBUST": round(THRESHOLD * report["distribution"]["ratio"]["p50"], 2),
        "massive_pass_rate_at_3p0": pass_rate,
        "derivation": "quantile match and median-ratio scaling; no return was used, no grid was scanned",
    }
    _write(args.out, report)
    print(json.dumps({k: report[k] for k in ("overlap", "distribution", "threshold_3p0",
                                             "limit_checks", "verdict", "threshold_candidates")
                      if k in report}, indent=1, default=str))
    return 0


def _write(out: Path, report: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "k0_result.json").write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    log(f"written {out / 'k0_result.json'}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="E-KIWOOM-CAL K0 source translation")
    parser.add_argument("command", choices=("k0",))
    parser.add_argument("--kiwoom", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    return k0(args)


if __name__ == "__main__":
    sys.exit(main())
