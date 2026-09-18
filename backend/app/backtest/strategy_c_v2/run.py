"""C-V2A / C-V2B run: rebuild the frozen V1 study from the raw cache, prove it reproduces V1,
then add the declared horizons and volatility measures and judge them by the declared rules.

Refuses to run when the V1 rules, V2 rules, raw digest or V1 candidate table differ from the
baseline run. Writes only ``data/runtime/strategy_c/v2/runs/<run_id>/``.
"""

from datetime import date
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from app.backtest.engine.identity import code_digest, package_files, run_identity, source_provenance
from app.backtest.strategy_c_selection import evaluate
from app.backtest.strategy_c_selection.features import compute
from app.backtest.strategy_c_selection.labels import compute_labels
from app.backtest.strategy_c_selection.panel import load_panel
from app.backtest.strategy_c_selection.rules import REPO_ROOT, SelectionRules
from app.backtest.strategy_c_selection.run import (FORWARD, PRIMARY_START, SECONDARY_START, RunInputs,
                                                   candidate_table, dump_json, raw_digest, table_digest,
                                                   usable_sessions)
from app.backtest.strategy_c_v2 import analyze
from app.backtest.strategy_c_v2.measures import RETRACE_DAYS, compute_measures

IDENTITY_NAMESPACE = "strategy-c-v2ab-run"
RUN_ID_PREFIX = "cv2ab1"
RESULT_SCHEMA = "strategy-c-v2ab-result-v1"
CODE_PACKAGES = (Path(__file__).resolve().parent, Path(evaluate.__file__).resolve().parent)
V1_HORIZONS = (1, 3, 5, 10)
COST_REFERENCE = 0.0025


class BaselineMismatch(RuntimeError):
    pass


def _levels(n_tests: int) -> tuple[float, float]:
    return 0.95, 1.0 - 0.05 / n_tests


def _key(level: float) -> str:
    return f"{level:.6f}"


def _reproduce(rows: pd.DataFrame, rules: SelectionRules, baseline: dict, name: str) -> dict:
    """V1 horizon means recomputed from these rows must equal the baseline summary."""
    worst = 0.0
    for k in V1_HORIZONS:
        cand = evaluate.attach_matched_base(rows, rules, k)
        m = cand[cand["matched"]]
        ref = baseline["results"]["primary"][name]["means"][f"horizon_{k}"]
        if int(len(m)) != ref["n"]:
            raise BaselineMismatch(f"{name} horizon {k}: n {len(m)} != baseline {ref['n']}")
        for label, key in (("close", "candidate_mean_close"), ("base_close", "control_mean_close"),
                           ("mfe", "candidate_mean_mfe"), ("base_mfe", "control_mean_mfe"),
                           ("mae", "candidate_mean_mae"), ("base_mae", "control_mean_mae")):
            value = float(m[f"{label}_{k}"].mean())
            worst = max(worst, abs(value - ref[key]))
    if worst > 1e-12:
        raise BaselineMismatch(f"{name}: V1 horizon means differ by {worst}")
    return {"horizons": list(V1_HORIZONS), "max_abs_diff": worst}


def _attach(rows: pd.DataFrame, arrays: dict[str, np.ndarray]) -> pd.DataFrame:
    ii, jj = rows["date_idx"].to_numpy(), rows["ticker_idx"].to_numpy()
    extra = {name: array[ii, jj] for name, array in arrays.items()}
    return pd.concat([rows.reset_index(drop=True), pd.DataFrame(extra)], axis=1)


def _v2a(rows: pd.DataFrame, rows2: pd.DataFrame | None, spec: dict, rules: SelectionRules,
         window: list[int], window2: list[int] | None, draws, draws2) -> dict:
    horizons = spec["horizons"]
    levels = _levels(3 * len(horizons))
    out: dict = {"horizons": {}, "descriptive": {}}
    for k in horizons:
        entry = {}
        for metric in ("close", "mfe", "mae"):
            cand = analyze.matched_metric(rows, f"{metric}_{k}", f"valid_{k}", rules.min_controls)
            entry[metric] = analyze.excess_stats(cand, window, draws if metric == "close" else None,
                                                 levels, blocks=metric == "close")
        if rows2 is not None:
            cand2 = analyze.matched_metric(rows2, f"close_{k}", f"valid_{k}", rules.min_controls)
            entry["close_secondary"] = analyze.excess_stats(cand2, window2, draws2, (0.95,), blocks=False)
        out["horizons"][str(k)] = entry
    for metric in ["time_to_mfe_10", "time_to_mae_10", "giveback_10", "overnight_gap"] \
            + [f"post_mfe_retrace_{j}" for j in RETRACE_DAYS]:
        cand = analyze.matched_metric(rows, metric, "valid_10", rules.min_controls)
        out["descriptive"][metric] = analyze.excess_stats(cand, window, None, levels, blocks=False)
    out["levels"] = [_key(x) for x in levels]
    return out


def _v2a_verdict(per_variant: dict, spec: dict) -> dict:
    rule = spec["supported_rule"]
    bonf, unadj = _key(1.0 - 0.05 / (3 * len(spec["horizons"]))), _key(0.95)
    cells, supported, weak = {}, [], []
    for name, result in per_variant.items():
        for k in rule["eligible_horizons"]:
            e = result["horizons"][str(k)]
            close = e["close"]
            blocks_pos = sum(1 for b in close["block_excess"] if b is not None and b > 0)
            sec = e.get("close_secondary")
            cond = {"a_mean_excess_bonferroni_ci_low": close["ci"][bonf][0] > 0,
                    "b_median_excess": close["median_excess"] > 0,
                    "c_time_blocks_positive_mean_excess": blocks_pos >= 3,
                    "d_secondary_window_same_sign": True if sec is None else sec["excess"] > 0,
                    "e_matched_candidates": close["n"] >= 300}
            cells[f"{name}@{k}"] = {"conditions": cond, "blocks_positive": blocks_pos}
            if all(cond.values()):
                supported.append(f"{name}@{k}")
            elif close["ci"][unadj][0] > 0 and blocks_pos >= 3:
                weak.append(f"{name}@{k}")
    verdict = "SUPPORTED" if supported else "WEAK_DESCRIPTIVE" if weak else "REJECTED"
    return {"verdict": verdict, "supported_cells": supported, "weak_cells": weak, "cells": cells}


def _v2b(rows: pd.DataFrame, spec: dict, rules: SelectionRules, window: list[int], draws) -> dict:
    levels = _levels(15)
    out: dict = {"metrics": {}}
    names = ["range", "abs_close", "max_abs_move", "realized_vol", "atr_expansion", "log_up", "log_down",
             "log_asym", "mfe", "mae", "close"]
    for k in spec["horizons"]:
        for name in names:
            metric = f"{name}_{k}"
            cand = analyze.matched_metric(rows, metric, f"valid_{k}", rules.min_controls)
            out["metrics"][metric] = analyze.excess_stats(cand, window, draws, levels, blocks=True)
    key = _key(levels[1])
    signs = {m: analyze.sign_of(out["metrics"][m], key)
             for m in ("log_up_10", "log_down_10", "log_asym_10", "range_10", "close_10")}
    out["signs_bonferroni"] = signs
    out["label"] = analyze.classify(signs)
    out["levels"] = [_key(x) for x in levels]
    return out


def execute(inputs: RunInputs, rules: SelectionRules, v2: dict, v2_checksum: str, baseline_dir: Path,
            out_root: Path, *, log=print) -> dict:
    started = time.perf_counter()
    baseline = json.loads((baseline_dir / "summary.json").read_text(encoding="utf-8"))
    spec_base = v2["baseline"]
    if rules.checksum != spec_base["rules_checksum"] or baseline["rules_checksum"] != rules.checksum:
        raise BaselineMismatch("V1 rules checksum differs from the declared baseline")
    sessions, dropped = usable_sessions(inputs.raw_root, inputs.sessions)
    effective = RunInputs(inputs.raw_root, sessions, inputs.snapshot_dates, inputs.split_range)
    digest = raw_digest(effective)
    if digest != spec_base["baseline_raw_digest"]:
        raise BaselineMismatch(f"raw digest {digest} != baseline {spec_base['baseline_raw_digest']}")
    code = code_digest([p for pkg in CODE_PACKAGES for p in package_files(pkg)], root=REPO_ROOT)
    identity = run_identity(IDENTITY_NAMESPACE, RUN_ID_PREFIX, [
        f"v1_rules_checksum={rules.checksum}", f"v2_rules_checksum={v2_checksum}",
        f"raw_digest={digest}", f"baseline_run_id={baseline['run_id']}", f"code_digest={code}",
        f"sessions={sessions[0].isoformat()}..{sessions[-1].isoformat()}:{len(sessions)}"])
    log(f"run_id={identity.run_id}")

    panel = load_panel(inputs.raw_root, sessions, inputs.snapshot_dates, rules.allowed_exchanges,
                       inputs.split_range)
    features = compute(panel, rules)
    t = len(sessions)
    primary = list(range(PRIMARY_START, t - FORWARD))
    secondary = list(range(SECONDARY_START, t - FORWARD))
    variants = list(rules.variants)

    labels_v1 = compute_labels(panel, V1_HORIZONS, rules.ca_ratio)
    digest_v1 = table_digest(candidate_table(panel, features, labels_v1, rules, primary, variants, "primary"))
    if digest_v1 != baseline["digests"]["candidates_primary"]:
        raise BaselineMismatch(f"V1 candidate digest {digest_v1} != baseline")
    del labels_v1
    log(f"V1 candidate table reproduced digest={digest_v1[:12]} elapsed_s={time.perf_counter() - started:.1f}")

    v2a_spec, v2b_spec = v2["c_v2a_short_horizon"], v2["c_v2b_volatility_expansion"]
    horizons = tuple(sorted(set(v2a_spec["horizons"]) | set(v2b_spec["horizons"])))
    labels = compute_labels(panel, horizons, rules.ca_ratio)
    measures = compute_measures(panel, labels, features.values["atr_pct"], tuple(v2b_spec["horizons"]))
    measures["time_to_mae_10"] = labels.time_to_mae[10]
    log(f"labels+measures elapsed_s={time.perf_counter() - started:.1f}")

    draws = evaluate.block_indices(len(primary), replicates=v2a_spec["statistics"]["bootstrap_replicates"])
    draws2 = evaluate.block_indices(len(secondary), replicates=v2a_spec["statistics"]["bootstrap_replicates"])
    v2a, v2b, reproduction = {}, {}, {}
    for variant in variants:
        rows = _attach(evaluate.build_rows(variant, features, labels, primary, rules, panel.tickers), measures)
        reproduction[variant.name] = _reproduce(rows, rules, baseline, variant.name)
        rows2 = None
        if variant.history == "base":
            rows2 = evaluate.build_rows(variant, features, labels, secondary, rules, panel.tickers)
        v2a[variant.name] = _v2a(rows, rows2, v2a_spec, rules, primary, secondary, draws, draws2)
        v2b[variant.name] = _v2b(rows, v2b_spec, rules, primary, draws)
        log(f"{variant.name} done label={v2b[variant.name]['label']} elapsed_s={time.perf_counter() - started:.1f}")
    v2a_verdict = _v2a_verdict(v2a, v2a_spec)
    v2b_overall = analyze.overall([v2b[v]["label"] for v in v2b])

    summary = {
        "run_id": identity.run_id, "run_identity": identity.digest, "identity_lines": list(identity.lines),
        "result_schema": RESULT_SCHEMA, "study_kind": v2["study_kind"],
        "baseline_run_id": baseline["run_id"], "baseline_reproduction": {
            "candidate_digest": digest_v1, "horizon_means": reproduction},
        "windows": {"primary": [sessions[primary[0]].isoformat(), sessions[primary[-1]].isoformat(), len(primary)],
                    "secondary": [sessions[secondary[0]].isoformat(), sessions[secondary[-1]].isoformat(),
                                  len(secondary)]},
        "cost_reference": COST_REFERENCE,
        "c_v2a": {"per_variant": v2a, "verdict": v2a_verdict},
        "c_v2b": {"per_variant": v2b, "overall_label": v2b_overall},
        "provenance": source_provenance(REPO_ROOT, list(CODE_PACKAGES)),
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }
    out = out_root / identity.run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_bytes(dump_json(summary))
    _tables(v2a, v2b, out)
    (out / "rules.json").write_bytes((REPO_ROOT / "docs/backtest/strategy_c/v2/c_v2ab_rules_v1.json").read_bytes())
    manifest = {"run_id": identity.run_id, "v1_rules_checksum": rules.checksum, "v2_rules_checksum": v2_checksum,
                "raw_digest": digest, "code_digest": code, "baseline_run_id": baseline["run_id"],
                "files": sorted(p.name for p in out.iterdir()) + ["manifest.json"],
                "created": date.today().isoformat()}
    (out / "manifest.json").write_bytes(dump_json(manifest))
    log(f"V2A={v2a_verdict['verdict']} V2B={v2b_overall} out={out} elapsed_s={summary['elapsed_seconds']}")
    return summary


def _tables(v2a: dict, v2b: dict, out: Path) -> None:
    rows = []
    for name, result in v2a.items():
        bonf = result["levels"][1]
        for k, e in result["horizons"].items():
            c = e["close"]
            rows.append({"variant": name, "horizon": int(k), "n": c["n"], "cand_close_mean": c["candidate_mean"],
                         "ctrl_close_mean": c["control_mean"], "excess": c["excess"],
                         "median_excess": c["median_excess"], "ci95_low": c["ci"]["0.950000"][0],
                         "ci95_high": c["ci"]["0.950000"][1], "ci_bonf_low": c["ci"][bonf][0],
                         "ci_bonf_high": c["ci"][bonf][1],
                         "blocks": "|".join(f"{b:.5f}" for b in c["block_excess"]),
                         "secondary_excess": e.get("close_secondary", {}).get("excess"),
                         "cand_mfe": e["mfe"]["candidate_mean"], "ctrl_mfe": e["mfe"]["control_mean"],
                         "cand_mae": e["mae"]["candidate_mean"], "ctrl_mae": e["mae"]["control_mean"]})
    pd.DataFrame(rows).to_csv(out / "v2a_horizon_table.csv", index=False, float_format="%.6f")
    rows = []
    for name, result in v2b.items():
        bonf = result["levels"][1]
        for metric, s in result["metrics"].items():
            rows.append({"variant": name, "metric": metric, "n": s["n"], "candidate": s["candidate_mean"],
                         "matched": s["control_mean"], "excess": s["excess"],
                         "ci_bonf_low": s["ci"][bonf][0], "ci_bonf_high": s["ci"][bonf][1],
                         "blocks": "|".join(f"{b:.5f}" for b in s["block_excess"])})
    pd.DataFrame(rows).to_csv(out / "v2b_metric_table.csv", index=False, float_format="%.6f")
