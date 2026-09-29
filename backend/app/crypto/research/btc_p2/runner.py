"""Run BTC-P2: gate on P1's large-move state, then try to call the direction.

    PYTHONPATH=backend python -m app.crypto.research.btc_p2.runner

Every preregistered combination is reported, including the ones that fail. The headline metric is
discrimination between UP_FIRST and DOWN_FIRST, never each side's own AUC: P1 scored 0.65 to 0.73
per side while carrying no directional information at all, and that is the exact mistake this
study is built to avoid.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from app.crypto.research import dataset
from app.crypto.research.btc_p1 import metrics as MT
from app.crypto.research.btc_p1 import models as M

from . import contract as C
from . import external as X
from . import features as F
from . import folds as FD
from . import gate as G
from . import targets as T

OUT_DIR = Path("data/research/crypto/btc_p2")
MODELS = ("M0", "M1", "M2")


def _fit_predict(name: str, x_train: np.ndarray, y_train: np.ndarray,
                 x_valid: np.ndarray) -> np.ndarray:
    if name == "M0":
        return M.Baseline().fit(y_train).predict(x_valid)
    if name == "M1":
        return M.RidgeLogistic(l2=C.M1_L2, iters=C.M1_ITERS).fit(x_train, y_train).predict(x_valid)
    return M.GradientBoosting(
        n_trees=C.M2_TREES, max_depth=C.M2_MAX_DEPTH, learning_rate=C.M2_LEARNING_RATE,
        min_samples_leaf=C.M2_MIN_SAMPLES_LEAF, n_bins=C.M2_BINS,
        leaf_l2=C.M2_LEAF_L2).fit(x_train, y_train).predict(x_valid)


class Panel:
    """Everything for one (horizon, threshold) combo, aligned row by row."""

    def __init__(self, horizon: int, threshold_bp: int, grid: dict[str, np.ndarray],
                 columns: dict[str, np.ndarray]):
        self.horizon, self.threshold_bp = horizon, threshold_bp
        self.frame = G.load(horizon, threshold_bp)
        self.rows = np.searchsorted(grid["ts"], self.frame.ts_ms)
        if not np.array_equal(grid["ts"][self.rows], self.frame.ts_ms):
            raise RuntimeError("P1 prediction timestamps are not on the research grid")
        self.ts = self.frame.ts_ms
        self.matrices = {name: F.matrix(columns, self.rows, name) for name in F.FEATURE_SETS}
        self.labels = {
            T.FIRST_TOUCH: T.build(
                T.DirectionSpec("t1", T.FIRST_TOUCH, horizon, threshold_bp),
                grid["high"], grid["low"], grid["close"], self.rows),
            T.ENDPOINT: T.build(
                T.DirectionSpec("t2", T.ENDPOINT, horizon, threshold_bp),
                grid["high"], grid["low"], grid["close"], self.rows),
        }

    def label_name(self) -> str:
        return f"{self.horizon // 60}H_{self.threshold_bp:03d}"


def walk(panel: Panel, target_kind: str, feature_set: str, gated: bool,
         label_override: np.ndarray | None = None) -> dict[str, np.ndarray] | None:
    """Walk the folds and collect validation predictions. None when nothing survives the gate."""
    labels = panel.labels[target_kind] if label_override is None else label_override
    directional = T.directional_mask(labels)
    x_all = panel.matrices[feature_set]
    y_all = (labels == T.UP_FIRST).astype(np.float64)
    large_move = panel.frame.large_move
    p1_fold = panel.frame.fold

    collected: dict[str, list[np.ndarray]] = {name: [] for name in MODELS}
    meta: dict[str, list[np.ndarray]] = {"y": [], "fold": [], "year": [], "vol": [], "ts": []}

    for fold in FD.build():
        train_mask, valid_mask = fold.masks(p1_fold, panel.ts)
        if gated:
            # The cut comes from training rows only: validation must not choose its own size.
            train_gate = large_move[train_mask]
            if len(train_gate) == 0:
                continue
            cut = FD.gate_threshold(train_gate, C.GATE_QUANTILE)
            train_mask = train_mask & (large_move >= cut)
            valid_mask = valid_mask & (large_move >= cut)
        train_mask = train_mask & directional
        valid_mask = valid_mask & directional
        if train_mask.sum() < 50 or valid_mask.sum() < 10:
            continue
        if len(np.unique(y_all[train_mask])) < 2:
            continue

        x_train, y_train = x_all[train_mask], y_all[train_mask]
        x_valid = x_all[valid_mask]
        for name in MODELS:
            collected[name].append(_fit_predict(name, x_train, y_train, x_valid))
        meta["y"].append(y_all[valid_mask])
        meta["fold"].append(np.full(int(valid_mask.sum()), fold.index))
        meta["year"].append(panel.frame.year[valid_mask])
        meta["vol"].append(panel.frame.vol_regime[valid_mask])
        meta["ts"].append(panel.ts[valid_mask])

    if not meta["y"]:
        return None
    out = {name: np.concatenate(values) for name, values in collected.items()}
    out.update({key: np.concatenate(values) for key, values in meta.items()})
    return out


def _deciles(y: np.ndarray, p: np.ndarray) -> list[dict[str, Any]]:
    order = np.argsort(p, kind="stable")
    chunks = np.array_split(order, C.DECILES)
    return [{"decile": i + 1, "n": int(len(chunk)),
             "mean_predicted": float(np.mean(p[chunk])),
             "actual_up_rate": float(np.mean(y[chunk]))}
            for i, chunk in enumerate(chunks) if len(chunk)]


def _confidence(y: np.ndarray, p: np.ndarray) -> list[dict[str, Any]]:
    rows = []
    base = float(np.mean(y))
    for threshold in C.CONFIDENCE_HIGH:
        mask = p >= threshold
        rows.append({"side": "UP", "threshold": threshold, "n": int(mask.sum()),
                     "actual_up_rate": float(np.mean(y[mask])) if mask.any() else None,
                     "lift": (float(np.mean(y[mask])) / base) if mask.any() and base > 0 else None})
    for threshold in C.CONFIDENCE_LOW:
        mask = p <= threshold
        actual_down = float(np.mean(1 - y[mask])) if mask.any() else None
        rows.append({"side": "DOWN", "threshold": threshold, "n": int(mask.sum()),
                     "actual_down_rate": actual_down,
                     "lift": (actual_down / (1 - base)) if actual_down is not None
                             and base < 1 else None})
    return rows


def evaluate(result: dict[str, np.ndarray], model: str) -> dict[str, Any]:
    y, p = result["y"], result[model]
    reference = result["M0"]
    deciles = _deciles(y, p)
    gap = (deciles[-1]["actual_up_rate"] - deciles[0]["actual_up_rate"]) if len(deciles) >= 2 \
        else float("nan")
    predicted_class = (p >= 0.5).astype(float)
    up, down = y == 1, y == 0
    balanced = float(0.5 * (np.mean(predicted_class[up]) + np.mean(1 - predicted_class[down]))) \
        if up.any() and down.any() else float("nan")

    by_fold, by_year, by_vol = {}, {}, {}
    for fold in sorted(set(result["fold"].tolist())):
        sel = result["fold"] == fold
        by_fold[int(fold)] = MT.roc_auc(y[sel], p[sel]) if len(np.unique(y[sel])) > 1 else None
    for year in C.VALIDATION_YEARS:
        sel = result["year"] == year
        by_year[year] = (MT.roc_auc(y[sel], p[sel])
                         if sel.any() and len(np.unique(y[sel])) > 1 else None)
    for regime in ("LOW", "MID", "HIGH"):
        sel = result["vol"] == regime
        by_vol[regime] = (MT.roc_auc(y[sel], p[sel])
                          if sel.any() and len(np.unique(y[sel])) > 1 else None)

    return {
        "n": int(len(y)),
        "up_first": int(y.sum()),
        "down_first": int(len(y) - y.sum()),
        "up_share": float(np.mean(y)),
        "direction_auc": MT.roc_auc(y, p),
        "balanced_accuracy": balanced,
        "brier": MT.brier(y, p),
        "brier_skill_vs_base_rate": MT.brier_skill(y, p, reference),
        "log_loss": MT.log_loss(y, p),
        "ece": MT.expected_calibration_error(y, p, C.RELIABILITY_BUCKETS),
        **{f"calibration_{k}": v for k, v in MT.calibration_line(y, p).items()},
        "reliability": MT.reliability(y, p, C.RELIABILITY_BUCKETS),
        "deciles": deciles,
        "decile_gap": gap,
        "confidence": _confidence(y, p),
        "by_fold_auc": by_fold,
        "by_year_auc": by_year,
        "by_vol_regime_auc": by_vol,
        "predicted": {"mean": float(np.mean(p)), "std": float(np.std(p)),
                      "p05": float(np.quantile(p, 0.05)), "p95": float(np.quantile(p, 0.95))},
    }


def verdict(summary: dict[str, Any], permutation: dict[str, Any] | None) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    if summary["n"] < C.MIN_DIRECTIONAL_ROWS:
        return {"verdict": C.INCONCLUSIVE,
                "reason": f"{summary['n']} directional rows below the preregistered "
                          f"{C.MIN_DIRECTIONAL_ROWS}",
                "checks": checks}

    auc = summary["direction_auc"]
    folds_ok = sum(1 for v in summary["by_fold_auc"].values() if v is not None and v > C.D2_FOLD_AUC)
    years_ok = sum(1 for v in summary["by_year_auc"].values() if v is not None and v > C.D2_FOLD_AUC)

    checks["D1_direction_auc"] = {"value": auc, "threshold": C.D1_AUC, "pass": auc > C.D1_AUC}
    checks["D2_fold_consistency"] = {"value": folds_ok, "of": C.D2_TOTAL_FOLDS,
                                     "min": C.D2_MIN_FOLDS, "pass": folds_ok >= C.D2_MIN_FOLDS}
    checks["D3_year_consistency"] = {"value": years_ok, "of": C.D3_TOTAL_YEARS,
                                     "min": C.D3_MIN_YEARS, "pass": years_ok >= C.D3_MIN_YEARS}
    checks["D4_ece"] = {"value": summary["ece"], "max": C.D4_ECE_MAX,
                        "pass": summary["ece"] <= C.D4_ECE_MAX}
    gap = summary["decile_gap"]
    checks["D5_decile_gap"] = {"value": gap, "min": C.D5_DECILE_GAP,
                               "pass": bool(np.isfinite(gap) and abs(gap) >= C.D5_DECILE_GAP)}
    if permutation is None:
        checks["D6_permutation"] = {"value": None, "pass": False,
                                    "note": "permutation control is computed for the primary "
                                            "configuration only"}
    else:
        checks["D6_permutation"] = {"value": auc, "percentile_cut": permutation["percentile_value"],
                                    "pass": auc > permutation["percentile_value"]}
    checks["D7_sample"] = {"value": summary["n"], "min": C.MIN_DIRECTIONAL_ROWS, "pass": True}

    if all(check["pass"] for check in checks.values()):
        return {"verdict": C.STRONG, "reason": "all preregistered conditions met",
                "checks": checks}
    if auc > C.WEAK_AUC:
        failed = [k for k, v in checks.items() if not v["pass"]]
        return {"verdict": C.WEAK, "reason": "above the weak threshold but fails "
                                             + ", ".join(failed), "checks": checks}
    return {"verdict": C.NO_DIRECTION,
            "reason": f"direction AUC {auc:.4f} is at or below {C.WEAK_AUC}", "checks": checks}


def permutation_control(panel: Panel, repetitions: int, seed: int) -> dict[str, Any]:
    """Shuffle the direction labels and rerun the whole walk-forward, `repetitions` times.

    A label that has been permuted cannot carry information, so whatever AUC this produces is
    what the pipeline manufactures on its own: overlapping windows, fold structure, and the
    sheer number of fitted parameters.
    """
    rng = np.random.default_rng(seed)
    labels = panel.labels[T.FIRST_TOUCH]
    directional = np.flatnonzero(T.directional_mask(labels))
    aucs = []
    for _ in range(repetitions):
        shuffled = labels.copy()
        shuffled[directional] = labels[rng.permutation(directional)]
        result = walk(panel, T.FIRST_TOUCH, F.D2_ONLY, gated=True, label_override=shuffled)
        if result is None:
            continue
        auc = MT.roc_auc(result["y"], result["M2"])
        if np.isfinite(auc):
            aucs.append(float(auc))
    values = np.array(aucs)
    return {
        "repetitions": len(aucs),
        "mean": float(values.mean()) if len(values) else None,
        "std": float(values.std()) if len(values) else None,
        "percentile": C.PERMUTATION_PERCENTILE,
        "percentile_value": float(np.percentile(values, C.PERMUTATION_PERCENTILE))
        if len(values) else None,
        "max": float(values.max()) if len(values) else None,
    }


def run(write: bool = True, permutations: int = C.PERMUTATIONS,
        verbose: bool = True) -> dict[str, Any]:
    contract_hash = C.require_frozen()
    started = time.time()

    grid = dataset.load()
    ext = X.build()
    if not np.array_equal(grid["ts"], ext["ts"]):
        raise RuntimeError("the external grid is not aligned with the research grid")
    columns = F.build(grid, ext)

    results: dict[str, Any] = {}
    prediction_rows: list[dict[str, Any]] = []
    permutations_by_combo: dict[str, Any] = {}

    for horizon, threshold_bp in C.COMBOS:
        panel = Panel(horizon, threshold_bp, grid, columns)
        combo = panel.label_name()
        if verbose:
            print(f"  {combo}: panel ready  {time.time() - started:.0f}s", flush=True)

        control = permutation_control(panel, permutations, C.SEED) if permutations else None
        permutations_by_combo[combo] = control
        if verbose and control:
            print(f"    permutation control: {control['repetitions']} runs, "
                  f"p{C.PERMUTATION_PERCENTILE} = {control['percentile_value']:.4f}  "
                  f"{time.time() - started:.0f}s", flush=True)

        entry: dict[str, Any] = {
            "combo": combo, "horizon_minutes": horizon, "threshold_bp": threshold_bp,
            "class_counts": {kind: T.class_counts(labels)
                             for kind, labels in panel.labels.items()},
            "gate": G.describe(panel.frame, 0.5),
            "cells": {},
        }
        for target_kind in (T.FIRST_TOUCH, T.ENDPOINT):
            for feature_set in (F.D2_ONLY, F.WITH_EXTERNAL):
                for subset, gated in ((C.PRIMARY_SUBSET, True), (C.REFERENCE_SUBSET, False)):
                    result = walk(panel, target_kind, feature_set, gated)
                    key = f"{target_kind}|{feature_set}|{subset}"
                    if result is None:
                        entry["cells"][key] = {"status": "NO_ROWS"}
                        continue
                    cell: dict[str, Any] = {"status": "OK", "models": {}, "verdicts": {}}
                    for model in ("M1", "M2"):
                        summary = evaluate(result, model)
                        cell["models"][model] = summary
                        is_primary = (target_kind == T.FIRST_TOUCH
                                      and feature_set == F.D2_ONLY
                                      and subset == C.PRIMARY_SUBSET
                                      and model == "M2")
                        cell["verdicts"][model] = verdict(
                            summary, control if is_primary else None)
                    cell["models"]["M0"] = evaluate(result, "M0")
                    cell["verdict"] = _best(cell["verdicts"])
                    entry["cells"][key] = cell

                    if target_kind == T.FIRST_TOUCH and subset == C.PRIMARY_SUBSET:
                        for i in range(len(result["y"])):
                            prediction_rows.append({
                                "combo": combo, "feature_set": feature_set,
                                "ts_ms": int(result["ts"][i]),
                                "utc": str(np.datetime64(int(result["ts"][i]), "ms")) + "Z",
                                "fold": int(result["fold"][i]), "year": str(result["year"][i]),
                                "vol_regime": str(result["vol"][i]),
                                "y_up_first": float(result["y"][i]),
                                "p_up_m0": float(result["M0"][i]),
                                "p_up_m1": float(result["M1"][i]),
                                "p_up_m2": float(result["M2"][i]),
                            })
        results[combo] = entry

    payload = {
        "record": "CRYPTO_BTC_P2_RESULTS_V1",
        "contract_sha256": contract_hash,
        "seed": C.SEED,
        "runtime_seconds": round(time.time() - started, 1),
        "question": "given that a large move looks likely, which direction",
        "p1_interpretation": "P1 is a LARGE_MOVE model; its directional AUC was 0.502 to 0.533",
        "gate_source": "P1 per-fold validation predictions; the forward frozen artifact is not "
                       "used, because applying a model trained through 2025-12-31 to earlier "
                       "rows would let it gate its own evaluation set",
        "trading": {"orders": 0, "pnl_computed": False},
        "permutation_control": permutations_by_combo,
        "combos": results,
        "summary": _overall(results),
    }

    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "results_v1.json").write_text(json.dumps(payload, indent=1, default=str))
        if prediction_rows:
            import pyarrow as pa
            import pyarrow.parquet as pq
            pq.write_table(pa.Table.from_pylist(prediction_rows),
                           OUT_DIR / "predictions_v1.parquet")
    return payload


def _best(verdicts: dict[str, dict[str, Any]]) -> str:
    order = [C.STRONG, C.WEAK, C.NO_DIRECTION, C.INCONCLUSIVE]
    labels = [v["verdict"] for v in verdicts.values()]
    for label in order:
        if label in labels:
            return label
    return C.INCONCLUSIVE


def _overall(results: dict[str, Any]) -> dict[str, Any]:
    primary_key = f"{T.FIRST_TOUCH}|{F.D2_ONLY}|{C.PRIMARY_SUBSET}"
    counts: dict[str, int] = {}
    primary: dict[str, Any] = {}
    strong: list[str] = []
    for combo, entry in results.items():
        cell = entry["cells"].get(primary_key, {})
        if cell.get("status") != "OK":
            continue
        primary[combo] = {
            "verdict": cell["verdict"],
            "direction_auc_m2": cell["models"]["M2"]["direction_auc"],
            "direction_auc_m1": cell["models"]["M1"]["direction_auc"],
            "n": cell["models"]["M2"]["n"],
        }
        counts[cell["verdict"]] = counts.get(cell["verdict"], 0) + 1
        if cell["verdict"] == C.STRONG:
            strong.append(combo)

    all_cells = [(combo, key, cell) for combo, entry in results.items()
                 for key, cell in entry["cells"].items() if cell.get("status") == "OK"]
    best = max(all_cells, key=lambda item: max(
        item[2]["models"][m]["direction_auc"] for m in ("M1", "M2")), default=None)

    return {
        "primary_cells": primary,
        "primary_verdict_counts": counts,
        "strong_primary_combos": strong,
        "btc_p3": "AUTHORIZED" if strong else "NOT_AUTHORIZED",
        "btc_p3_rule": "at least one primary first-touch combo must reach STRONG_DIRECTION",
        "best_cell_anywhere": ({"combo": best[0], "cell": best[1],
                                "direction_auc": max(best[2]["models"][m]["direction_auc"]
                                                     for m in ("M1", "M2"))}
                               if best else None),
        "multiplicity_warning":
            "4 combos x 2 targets x 2 feature sets x 2 subsets x 2 models = 64 evaluated cells. "
            "The best of 64 noisy AUCs sits above any single one of them under the null, which "
            "is why the permutation control and the fold and year consistency conditions exist "
            "and why only the primary cells decide BTC-P3.",
    }


def main() -> int:
    payload = run()
    summary = payload["summary"]
    print(f"\nBTC-P2  contract {payload['contract_sha256'][:16]}  "
          f"{payload['runtime_seconds']:.0f}s")
    print("  primary cells (first touch, D2 only, gated):")
    for combo, row in summary["primary_cells"].items():
        print(f"    {combo:10s} n={row['n']:6d}  AUC M2 {row['direction_auc_m2']:.4f}  "
              f"M1 {row['direction_auc_m1']:.4f}  {row['verdict']}")
    print(f"  verdicts : {summary['primary_verdict_counts']}")
    print(f"  BTC-P3   : {summary['btc_p3']}")
    best = summary["best_cell_anywhere"]
    if best:
        print(f"  best anywhere: {best['combo']} {best['cell']} AUC {best['direction_auc']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
