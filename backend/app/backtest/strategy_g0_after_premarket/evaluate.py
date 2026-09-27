"""The declared G0 hypotheses and the evaluation battery.

Masks are built generically from the machine-readable conditions in the frozen rules; this
module carries no threshold. A condition on a feature that is undefined on a row excludes that
row (it is never read as zero), so each block reports its own ``n``.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import stats as E0S
from app.backtest.strategy_e1_h5_confirm import matching as MATCH
from app.backtest.strategy_g0_after_premarket import stats as S

HYPOTHESES = ("H1", "H2", "H3", "H4", "H5")
_OPS = {">": np.greater, ">=": np.greater_equal, "<": np.less, "<=": np.less_equal}


def mask(name: str, features: Mapping[str, np.ndarray], rules_raw: Mapping[str, Any]) -> np.ndarray:
    conditions = rules_raw["hypotheses"][name]["conditions"]
    first = next(iter(features.values()))
    out = np.ones(first.shape, dtype=bool)
    for feature, op, value in conditions:
        series = features[feature]
        with np.errstate(invalid="ignore"):
            out &= np.isfinite(series) & _OPS[op](series, float(value))
    return out


def month_of(day: date) -> str:
    return f"{day.year}-{day.month:02d}"


def quarter_of(day: date) -> str:
    return E0S.quarter_of(day)


def bootstrap_block(selected: np.ndarray, r: np.ndarray, mfe: np.ndarray,
                    clusters: np.ndarray, settings: Mapping[str, Any]) -> dict[str, Any]:
    def metrics(sel: np.ndarray) -> dict[str, np.ndarray]:
        return {"mean": r[sel], "p_ge_2pct": (r[sel] >= 0.02).astype(float),
                "p_ge_3pct": (r[sel] >= 0.03).astype(float),
                "p_mfe_ge_3pct": (mfe[sel] >= 0.03).astype(float)}
    everyone = np.ones(r.shape, dtype=bool)
    return S.cluster_bootstrap(metrics(selected), clusters[selected], metrics(everyone), clusters,
                               resamples=int(settings["resamples"]), seed=int(settings["seed"]))


def matched_block(rows: Any, selected: np.ndarray, r: np.ndarray, rules_raw: Mapping[str, Any],
                  cell_features: Mapping[str, np.ndarray]) -> dict[str, Any]:
    """Coarsened exact matching with the E1-H5 estimators (imported unchanged)."""
    spec = rules_raw["neutralization"]
    ids = np.zeros(r.shape, dtype=np.int64)
    valid = np.ones(r.shape, dtype=bool)
    balance: dict[str, Any] = {}
    for name, edges in spec["cells"].items():
        edges_t = tuple(None if e is None else float(e) for e in edges)
        index = E0S.bucket_index(cell_features[name], edges_t)
        valid &= index >= 0
        ids = ids * 16 + np.maximum(index, 0)
        counts_sel = np.bincount(index[selected & (index >= 0)], minlength=len(edges_t) - 1)
        counts_all = np.bincount(index[~selected & (index >= 0)], minlength=len(edges_t) - 1)
        balance[name] = {"candidate_share": (counts_sel / max(counts_sel.sum(), 1)).tolist(),
                         "control_share": (counts_all / max(counts_all.sum(), 1)).tolist()}
    cells = np.where(valid, ids, -1)
    sessions = rows.source_sessions.astype(str)
    tickers = rows.tickers.astype(str)
    primary = MATCH.session_demeaned(r, sessions, tickers, cells, selected)
    secondary = MATCH.same_session(r, sessions, tickers, cells, selected)
    block = {"estimator_primary": primary.to_dict(), "estimator_secondary": secondary.to_dict(),
             "balance": balance}
    if primary.differences.size > 1:
        settings = rules_raw["bootstrap"]
        rng = np.random.default_rng(int(settings["seed"]))
        diffs, sess = primary.differences, np.asarray(primary.matched_sessions).astype(str)
        keys, inv = np.unique(sess, return_inverse=True)
        sums = np.bincount(inv, weights=diffs, minlength=keys.size)
        counts = np.bincount(inv, minlength=keys.size).astype(float)
        means = []
        for _ in range(int(settings["resamples"]) // 10):
            w = np.bincount(rng.integers(0, keys.size, keys.size), minlength=keys.size)
            means.append(float(w @ sums / max(w @ counts, 1)))
        block["matched_session_bootstrap"] = {
            "resamples": len(means), "ci95": [float(np.percentile(means, 2.5)),
                                              float(np.percentile(means, 97.5))]}
    return block


def candidate(name: str, selected: np.ndarray, rows: Any, label_prefix: str,
              baseline: Mapping[str, Any], baseline_exc: Mapping[str, Any],
              rules_raw: Mapping[str, Any], cell_features: Mapping[str, np.ndarray],
              *, full: bool = True) -> dict[str, Any]:
    r = rows.labels[f"{label_prefix}:R"]
    mfe = rows.labels[f"{label_prefix}:MFE"]
    mae = rows.labels[f"{label_prefix}:MAE"]
    chosen = r[selected]
    block: dict[str, Any] = {"name": name, "summary": S.summarise(chosen)}
    if not block["summary"].get("n"):
        return block
    block["lift"] = S.lift(block["summary"], baseline)
    block["excursions"] = S.excursions(mfe[selected], mae[selected])
    block["excursion_lift"] = S.excursion_lift(block["excursions"], baseline_exc)
    block["unique_symbols"] = int(np.unique(rows.tickers[selected].astype(str)).size)
    block["sessions"] = int(np.unique(rows.source_sessions[selected].astype(str)).size)
    block["cost"] = S.cost_stress(block["summary"]["mean"], rules_raw["cost"]["grid_bp_round_trip"])
    if not full:
        return block
    block["extreme_removal"] = S.extreme_removal(chosen, baseline)
    block["concentration"] = S.concentration(chosen, rows.tickers[selected].astype(str),
                                             float(baseline["mean"]))
    settings = rules_raw["bootstrap"]
    block["bootstrap_session"] = bootstrap_block(selected, r, mfe, rows.source_sessions.astype(str), settings)
    block["bootstrap_symbol"] = bootstrap_block(selected, r, mfe, rows.tickers.astype(str), settings)
    months = np.array([month_of(s) for s in rows.source_sessions], dtype=object)
    quarters = np.array([quarter_of(s) for s in rows.source_sessions], dtype=object)
    block["monthly"] = S.period_table(chosen, months[selected], r, months)
    block["quarterly"] = S.period_table(chosen, quarters[selected], r, quarters)
    block["matched"] = matched_block(rows, selected, r, rules_raw, cell_features)
    return block


def bucket_table(feature: np.ndarray, r: np.ndarray, mfe: np.ndarray, mae: np.ndarray,
                 edges: Sequence[float | None], baseline: Mapping[str, Any]) -> list[dict[str, Any]]:
    index = E0S.bucket_index(feature, tuple(edges))
    out = []
    for i, label in enumerate(E0S.bucket_labels(tuple(edges))):
        sel = index == i
        block = S.summarise(r[sel])
        row: dict[str, Any] = {"bucket": label, "n": block.get("n", 0)}
        if block.get("n"):
            row.update({k: block[k] for k in ("mean", "median", "win_rate", "p_gap_ge_1pct",
                                              "p_gap_ge_2pct", "p_gap_ge_3pct", "p_gap_ge_5pct",
                                              "p_gap_le_minus_1pct", "p_gap_le_minus_2pct")})
            row["mean_lift"] = block["mean"] - baseline["mean"]
            exc = S.excursions(mfe[sel], mae[sel])
            row["mfe_mean"] = exc.get("mfe_mean")
            row["mae_mean"] = exc.get("mae_mean")
            row["p_mfe_ge_3pct"] = exc.get("p_mfe_ge_3pct")
        out.append(row)
    undefined = int((index == -1).sum())
    if undefined:
        out.append({"bucket": "UNDEFINED_OR_OUTSIDE", "n": undefined})
    return out
