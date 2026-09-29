"""Phase 2: build the dataset, walk the folds, fit M0/M1/M2, calibrate, score, judge.

    PYTHONPATH=backend python -m app.crypto.research.btc_p1.runner

The contract hash is checked first. Every preregistered target is reported, including the ones
that fail, because reporting only the survivors is how six previous studies could have looked
like five.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from app.crypto.research import dataset

from . import contract as C
from . import features as F
from . import folds as FD
from . import gates as G
from . import metrics as MT
from . import models as M
from . import targets as T

OUT_DIR = Path("data/research/crypto/btc_p1")
MODELS = ("M0", "M1", "M2")
LEARNED = ("M1", "M2")


def _year(ts_ms: np.ndarray) -> np.ndarray:
    return np.array([str(np.datetime64(int(t), "ms"))[:4] for t in ts_ms])


def _tertiles(train_values: np.ndarray) -> tuple[float, float]:
    return float(np.quantile(train_values, 1 / 3)), float(np.quantile(train_values, 2 / 3))


def _regime(values: np.ndarray, cuts: tuple[float, float]) -> np.ndarray:
    out = np.full(len(values), "MID", dtype=object)
    out[values <= cuts[0]] = "LOW"
    out[values > cuts[1]] = "HIGH"
    return out


def build_panel() -> dict[str, Any]:
    """Features, labels and decision rows: everything that does not depend on a fold."""
    grid = dataset.load()
    ts = grid["ts"]
    columns = F.build(grid)
    specs = T.build_specs(C.TARGET_GRID)
    labels = T.build_all(specs, grid["high"], grid["low"], grid["close"])

    rows = FD.decision_rows(ts, step_minutes=C.STEP_MINUTES, warmup_minutes=C.WARMUP_MINUTES,
                            max_horizon_minutes=C.MAX_HORIZON_MINUTES)
    if len(rows) != C.EXPECTED_ROWS:
        raise RuntimeError(f"decision grid drifted: {len(rows)} rows, contract says "
                           f"{C.EXPECTED_ROWS}")
    x = F.matrix(columns, rows)
    if not np.isfinite(x).all():
        raise RuntimeError("non-finite feature at a decision row; the contract records zero "
                           "missing values, so this is a change in the data or the code")
    y = {spec.name: labels[spec.name][rows] for spec in specs}
    return {"ts": ts[rows], "x": x, "y": y, "specs": specs,
            "vol": columns["vol_regime_ln"][rows], "trend": columns["trend_regime_24h_bp"][rows]}


def run(write: bool = True, verbose: bool = True) -> dict[str, Any]:
    contract_hash = C.require_frozen()
    started = time.time()

    panel = build_panel()
    ts, x, specs = panel["ts"], panel["x"], panel["specs"]
    fold_list = FD.build(C.SAMPLE_START, C.FOLD_STARTS, C.END, C.EMBARGO_MINUTES)
    years = _year(ts)

    # --- per fold: fit once per target, keep the validation predictions ------------------
    store: dict[str, dict[str, list[np.ndarray]]] = {
        spec.name: {m: [] for m in MODELS} | {f"{m}_raw": [] for m in LEARNED}
        for spec in specs}
    truth: dict[str, list[np.ndarray]] = {spec.name: [] for spec in specs}
    index: list[np.ndarray] = []
    fold_of: list[np.ndarray] = []
    regimes: dict[str, list[np.ndarray]] = {"vol": [], "trend": []}
    fold_sizes: list[dict[str, Any]] = []
    last_fold_models: dict[str, M.GradientBoosting] = {}
    last_fold_slice: dict[str, Any] = {}

    for fold in fold_list:
        train_mask = (ts >= fold.train_start_ms) & (ts < fold.train_end_ms)
        valid_mask = (ts >= fold.valid_start_ms) & (ts < fold.valid_end_ms)
        train_ts = ts[train_mask]
        model_sel, calib_sel = FD.split_train(train_ts, C.EMBARGO_MINUTES)

        train_x = x[train_mask]
        xm, xc, xv = train_x[model_sel], train_x[calib_sel], x[valid_mask]

        vol_cuts = _tertiles(panel["vol"][train_mask])
        trend_cuts = _tertiles(panel["trend"][train_mask])
        regimes["vol"].append(_regime(panel["vol"][valid_mask], vol_cuts))
        regimes["trend"].append(_regime(panel["trend"][valid_mask], trend_cuts))
        index.append(ts[valid_mask])
        fold_of.append(np.full(int(valid_mask.sum()), fold.index))
        fold_sizes.append({**fold.as_dict(), "train_rows": int(train_mask.sum()),
                           "model_rows": int(model_sel.sum()),
                           "calibration_rows": int(calib_sel.sum()),
                           "valid_rows": int(valid_mask.sum())})

        for spec in specs:
            y_all = panel["y"][spec.name]
            ym, yc, yv = y_all[train_mask][model_sel], y_all[train_mask][calib_sel], y_all[valid_mask]
            if not np.isfinite(ym).all() or not np.isfinite(yv).all():
                raise RuntimeError(f"{spec.name}: NaN label inside a fold; the decision grid "
                                   f"should already exclude incomplete horizons")
            truth[spec.name].append(yv)

            base = M.Baseline().fit(ym)
            store[spec.name]["M0"].append(base.predict(xv))

            fitted = {
                "M1": M.RidgeLogistic(l2=C.M1_L2, iters=C.M1_ITERS, tol=C.M1_TOL).fit(xm, ym),
                "M2": M.GradientBoosting(n_trees=C.M2_TREES, max_depth=C.M2_MAX_DEPTH,
                                         learning_rate=C.M2_LEARNING_RATE,
                                         min_samples_leaf=C.M2_MIN_SAMPLES_LEAF,
                                         n_bins=C.M2_BINS, leaf_l2=C.M2_LEAF_L2).fit(xm, ym),
            }
            for name, model in fitted.items():
                raw_valid = model.predict(xv)
                store[spec.name][f"{name}_raw"].append(raw_valid)
                # Isotonic on the calibration slice only: the model has never seen those rows,
                # and validation has never been touched.
                calibrator = M.Isotonic().fit(model.predict(xc), yc)
                store[spec.name][name].append(calibrator.transform(raw_valid))

            if fold.index == fold_list[-1].index:
                last_fold_models[spec.name] = fitted["M2"]

        if fold.index == fold_list[-1].index:
            last_fold_slice = {"x": xv, "mask": valid_mask}
        if verbose:
            print(f"  fold {fold.index} done  train={int(train_mask.sum())} "
                  f"valid={int(valid_mask.sum())}  {time.time() - started:.0f}s", flush=True)

    valid_ts = np.concatenate(index)
    valid_fold = np.concatenate(fold_of)
    valid_year = _year(valid_ts)
    vol_regime = np.concatenate(regimes["vol"])
    trend_regime = np.concatenate(regimes["trend"])

    # --- metrics and verdicts ------------------------------------------------------------
    results: dict[str, Any] = {}
    for spec in specs:
        y = np.concatenate(truth[spec.name])
        pooled_ref = np.concatenate(store[spec.name]["M0"])
        entry: dict[str, Any] = {
            "target": spec.name, "kind": spec.kind, "direction": spec.direction,
            "threshold_bp": spec.threshold_bp, "horizon_minutes": spec.horizon_minutes,
            "horizon": spec.horizon_label,
            "validation_rows": int(len(y)), "validation_positives": int(y.sum()),
            "base_rate": float(np.mean(y)), "models": {}, "verdicts": {},
        }
        for name in MODELS:
            p = np.concatenate(store[spec.name][name])
            summary = MT.summary(y, p, pooled_ref, buckets=C.RELIABILITY_BUCKETS,
                                 thresholds=C.CONFIDENCE_THRESHOLDS)
            if name in LEARNED:
                raw = np.concatenate(store[spec.name][f"{name}_raw"])
                summary["raw"] = {
                    "brier": MT.brier(y, raw),
                    "brier_skill": MT.brier_skill(y, raw, pooled_ref),
                    "log_loss": MT.log_loss(y, raw),
                    "roc_auc": MT.roc_auc(y, raw),
                    "ece": MT.expected_calibration_error(y, raw, C.RELIABILITY_BUCKETS),
                    **{f"calibration_{k}": v for k, v in MT.calibration_line(y, raw).items()},
                }
            fold_skills, year_skills = [], {}
            for f in fold_list:
                sel = valid_fold == f.index
                fold_skills.append(MT.brier_skill(y[sel], p[sel], pooled_ref[sel])
                                   if sel.any() else float("nan"))
            for yr in C.VALIDATION_YEARS:
                sel = valid_year == yr
                year_skills[yr] = (MT.brier_skill(y[sel], p[sel], pooled_ref[sel])
                                   if sel.any() else float("nan"))
            summary["by_fold_brier_skill"] = fold_skills
            summary["by_year_brier_skill"] = year_skills
            summary["by_vol_regime_brier_skill"] = {
                r: (MT.brier_skill(y[vol_regime == r], p[vol_regime == r],
                                   pooled_ref[vol_regime == r])
                    if (vol_regime == r).any() else float("nan"))
                for r in ("LOW", "MID", "HIGH")}
            summary["by_trend_regime_brier_skill"] = {
                r: (MT.brier_skill(y[trend_regime == r], p[trend_regime == r],
                                   pooled_ref[trend_regime == r])
                    if (trend_regime == r).any() else float("nan"))
                for r in ("LOW", "MID", "HIGH")}
            entry["models"][name] = summary
            if name in LEARNED:
                entry["verdicts"][name] = G.evaluate(summary, fold_skills, year_skills)
        entry["verdict"] = G.best_of(entry["verdicts"])
        results[spec.name] = entry

    # --- UP/DOWN separation, diagnostic only ---------------------------------------------
    separations: dict[str, Any] = {}
    for spec in specs:
        if spec.direction != T.DOWN:
            continue
        up_name = spec.name.replace("_DOWN_", "_UP_")
        if up_name not in results:
            continue
        pair = f"{spec.horizon_label}_{spec.threshold_bp:03d}" + ("" if spec.kind == T.PATH else "_EP")
        for model in LEARNED:
            separations[f"{pair}_{model}"] = MT.separation(
                np.concatenate(truth[up_name]), np.concatenate(store[up_name][model]),
                np.concatenate(truth[spec.name]), np.concatenate(store[spec.name][model]))

    # --- permutation importance by family, last fold only, explanatory -------------------
    importance = _family_importance(specs, last_fold_models, last_fold_slice, panel, valid_fold,
                                    truth, fold_list[-1].index)

    payload = {
        "record": "CRYPTO_BTC_P1_MODEL_RESULTS_V1",
        "contract_sha256": contract_hash,
        "seed": C.SEED,
        "generated_utc": str(np.datetime64("now", "s")) + "Z",
        "runtime_seconds": round(time.time() - started, 1),
        "dependency_decision": "scikit-learn, LightGBM, XGBoost and scipy are neither installed "
                               "nor declared in requirements.txt; the contract forbids adding a "
                               "large dependency, so M1 and M2 are implemented in numpy and "
                               "validated against planted synthetic structure",
        "data": {
            "grid_source": "app.crypto.research.dataset (D5 grid, unchanged)",
            "decision_rows": int(len(ts)),
            "first_decision_utc": str(np.datetime64(int(ts[0]), "ms")) + "Z",
            "last_decision_utc": str(np.datetime64(int(ts[-1]), "ms")) + "Z",
            "validation_rows": int(len(valid_ts)),
            "feature_columns": len(F.NAMES),
            "feature_families": {k: FAM for k, FAM in F.FAMILY_TITLE.items()},
            "holdout_status": "NOT an untouched holdout: 2021-2026 was already used by D5, "
                              "D5.1, D5.2 and D6. True OOS requires forward shadow.",
        },
        "folds": fold_sizes,
        "targets": results,
        "separation": separations,
        "family_importance": importance,
        "summary": _overall(results),
    }

    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "model_results_v1.json").write_text(json.dumps(payload, indent=1, default=str))
        _write_parquet(specs, valid_ts, valid_fold, valid_year, vol_regime, trend_regime,
                       truth, store, results)
    return payload


def _family_importance(specs, models, last_slice, panel, valid_fold, truth,
                       last_index: int) -> dict[str, Any]:
    """Permute a whole family and see how much Brier gets worse. Explanatory, never a filter.

    Done on the last fold only and per family rather than per column: the families are the level
    at which the economics can be argued about, and the contract forbids using importance to
    reshape the feature set and then rerunning the same validation.
    """
    if not last_slice:
        return {}
    xv = last_slice["x"]
    sel = valid_fold == last_index
    out: dict[str, Any] = {}
    cols = {fam: [i for i, n in enumerate(F.NAMES) if F.FAMILY[n] == fam] for fam in F.FAMILIES}
    for spec in specs:
        if spec.kind != T.PATH:
            continue
        model = models.get(spec.name)
        if model is None:
            continue
        y = np.concatenate(truth[spec.name])[sel]
        baseline = MT.brier(y, model.predict(xv))
        row = {}
        for fam, idx in cols.items():
            permuted = xv.copy()
            # Reverse the rows rather than shuffle them: deterministic, and it destroys the
            # alignment between the family and the label just as well.
            permuted[:, idx] = permuted[::-1][:, idx]
            row[fam] = round(MT.brier(y, model.predict(permuted)) - baseline, 6)
        out[spec.name] = {"baseline_brier": round(baseline, 6), "brier_increase": row,
                          "note": "last fold only; higher means the family mattered more"}
    return out


def _overall(results: dict[str, Any]) -> dict[str, Any]:
    path = {k: v for k, v in results.items() if v["kind"] == T.PATH}
    counts: dict[str, int] = {}
    for entry in results.values():
        counts[entry["verdict"]] = counts.get(entry["verdict"], 0) + 1
    path_counts: dict[str, int] = {}
    for entry in path.values():
        path_counts[entry["verdict"]] = path_counts.get(entry["verdict"], 0) + 1
    strong_path = [k for k, v in path.items() if v["verdict"] == C.STRONG]
    ranked = sorted(results.items(),
                    key=lambda kv: max(
                        (kv[1]["models"][m]["brier_skill"] for m in LEARNED
                         if not np.isnan(kv[1]["models"][m]["brier_skill"])), default=-np.inf),
                    reverse=True)
    return {
        "verdict_counts_all_targets": counts,
        "verdict_counts_path_targets": path_counts,
        "strong_path_targets": strong_path,
        "btc_p2": "AUTHORIZED" if strong_path else "NOT_AUTHORIZED",
        "btc_p2_rule": "at least one PATH target must reach STRONG_SIGNAL (contract section 14)",
        "strongest_targets": [
            {"target": k,
             "best_model": max(LEARNED, key=lambda m: v["models"][m]["brier_skill"]),
             "brier_skill": max(v["models"][m]["brier_skill"] for m in LEARNED),
             "roc_auc": max(v["models"][m]["roc_auc"] for m in LEARNED),
             "verdict": v["verdict"]}
            for k, v in ranked[:5]],
        "selection_bias_warning":
            "2 learned models x 14 PATH targets = 28 comparisons, and a target's verdict takes "
            "the better model. Under the null, the best of 28 noisy estimates looks better than "
            "any single one of them. The fold and year consistency conditions (S6, S7) exist to "
            "make that harder, but they do not remove it; only forward shadow does.",
    }


def _write_parquet(specs, valid_ts, valid_fold, valid_year, vol_regime, trend_regime,
                   truth, store, results) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows_ts = np.tile(valid_ts, len(specs))
    table = {
        "ts_ms": rows_ts,
        "utc": [str(np.datetime64(int(t), "ms")) + "Z" for t in rows_ts],
        "target": np.repeat([s.name for s in specs], len(valid_ts)),
        "kind": np.repeat([s.kind for s in specs], len(valid_ts)),
        "fold": np.tile(valid_fold, len(specs)),
        "year": np.tile(valid_year, len(specs)),
        "vol_regime": np.tile(vol_regime.astype(str), len(specs)),
        "trend_regime": np.tile(trend_regime.astype(str), len(specs)),
        "y": np.concatenate([np.concatenate(truth[s.name]) for s in specs]),
        "p_m0": np.concatenate([np.concatenate(store[s.name]["M0"]) for s in specs]),
        "p_m1_raw": np.concatenate([np.concatenate(store[s.name]["M1_raw"]) for s in specs]),
        "p_m1_cal": np.concatenate([np.concatenate(store[s.name]["M1"]) for s in specs]),
        "p_m2_raw": np.concatenate([np.concatenate(store[s.name]["M2_raw"]) for s in specs]),
        "p_m2_cal": np.concatenate([np.concatenate(store[s.name]["M2"]) for s in specs]),
    }
    pq.write_table(pa.table(table), OUT_DIR / "predictions_v1.parquet")

    calib_rows = []
    for name, entry in results.items():
        for model in MODELS:
            for bucket in entry["models"][model]["reliability"]:
                calib_rows.append({"target": name, "kind": entry["kind"], "model": model,
                                   **bucket})
    pq.write_table(pa.Table.from_pylist(calib_rows), OUT_DIR / "calibration_v1.parquet")


def main() -> int:
    payload = run()
    s = payload["summary"]
    print(f"\nBTC-P1  contract {payload['contract_sha256'][:16]}  "
          f"{payload['runtime_seconds']:.0f}s")
    print(f"  all targets : {s['verdict_counts_all_targets']}")
    print(f"  PATH targets: {s['verdict_counts_path_targets']}")
    print(f"  BTC-P2      : {s['btc_p2']}")
    print("  strongest:")
    for row in s["strongest_targets"]:
        print(f"    {row['target']:16s} {row['best_model']}  skill {row['brier_skill']:+.5f}  "
              f"auc {row['roc_auc']:.4f}  {row['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
