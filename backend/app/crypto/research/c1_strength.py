"""Continuous C1 signal-strength diagnostics on the frozen E1 trade universe."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
CONTRACT = ROOT / "docs/crypto/C1_STRENGTH_CONTRACT_V1.md"
TRADES = ROOT / "data/research/crypto/c1_exit/e1_trades_v1.parquet"
REGIMES = ROOT / "data/research/crypto/r0_regime/regime_minutes_v1.parquet"
OUT = ROOT / "data/research/crypto/c1_strength"
MINUTE_MS = 60_000
VOL_HIGH = 0.0010924950359531157
HORIZONS = (30, 60, 120, 240, 480)
BASE_FEATURES = ("premium_extreme_bp", "oi_drop_bp", "vol_intensity")
INTERACTIONS = ("premium_x_oi_rank", "oi_x_vol_rank")


def spearman(x: np.ndarray, y: np.ndarray) -> float | None:
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3:
        return None
    xr = pd.Series(x[ok]).rank(method="average").to_numpy()
    yr = pd.Series(y[ok]).rank(method="average").to_numpy()
    if xr.std() == 0 or yr.std() == 0:
        return None
    return float(np.corrcoef(xr, yr)[0, 1])


def quantiles(x: np.ndarray, groups: int = 4) -> np.ndarray:
    """Deterministic near-equal-count buckets; stable row order breaks feature ties."""
    ranks = pd.Series(x).rank(method="first").to_numpy().astype(int) - 1
    return np.minimum(groups, ranks * groups // len(x) + 1)


def perf(net: np.ndarray, gross: np.ndarray, mfe: np.ndarray, mae: np.ndarray) -> dict:
    pos, neg = net[net > 0], net[net < 0]
    return {"n": int(len(net)), "gross_mean_bp": float(gross.mean()),
            "net_mean_bp": float(net.mean()), "net_median_bp": float(np.median(net)),
            "win_rate": float((net > 0).mean()), "pf": float(pos.sum() / -neg.sum()),
            "mfe_mean_bp": float(mfe.mean()), "mfe_median_bp": float(np.median(mfe)),
            "mae_mean_bp": float(mae.mean()), "mae_median_bp": float(np.median(mae))}


def horizon_perf(net: np.ndarray, gross: np.ndarray) -> dict:
    pos, neg = net[net > 0], net[net < 0]
    return {"n": int(len(net)), "gross_mean_bp": float(gross.mean()),
            "net_mean_bp": float(net.mean()), "net_median_bp": float(np.median(net)),
            "win_rate": float((net > 0).mean()), "pf": float(pos.sum() / -neg.sum())}


def order_stats(means: list[float]) -> dict:
    a = np.asarray(means, dtype=float)
    return {"quantile_mean_rho": spearman(np.arange(1, len(a) + 1, dtype=float), a),
            "favorable_adjacent_steps": int(np.sum(np.diff(a) > 0)), "means_bp": means}


def horizon_fields(h: int) -> tuple[str, str]:
    if h == 240:
        return "gross_bp", "net_bp"
    return f"gross_{h}m_bp", f"net_{h}m_bp"


def analyze_feature(frame: pd.DataFrame, feature: str, top_keep: np.ndarray) -> dict:
    x = frame[feature].to_numpy(float)
    q = quantiles(x)
    continuous = {"horizons": {},
                  "mfe_4h_rho": spearman(x, frame.mfe_4h_bp.to_numpy(float)),
                  "mae_4h_rho": spearman(x, frame.mae_4h_bp.to_numpy(float))}
    for h in HORIZONS:
        gf, nf = horizon_fields(h)
        continuous["horizons"][str(h)] = {
            "gross_rho": spearman(x, frame[gf].to_numpy(float)),
            "net_rho": spearman(x, frame[nf].to_numpy(float))}
    quartiles = {}
    for group in range(1, 5):
        z = frame.loc[q == group]
        quartiles[f"Q{group}"] = perf(z.net_bp.to_numpy(), z.gross_bp.to_numpy(),
                                       z.mfe_4h_bp.to_numpy(), z.mae_4h_bp.to_numpy())
    quantiles_by_horizon = {}
    for h in HORIZONS:
        gf, nf = horizon_fields(h)
        quantiles_by_horizon[str(h)] = {
            f"Q{group}": horizon_perf(frame.loc[q == group, nf].to_numpy(float),
                                       frame.loc[q == group, gf].to_numpy(float))
            for group in range(1, 5)}
    full_order = order_stats([quartiles[f"Q{i}"]["net_mean_bp"] for i in range(1, 5)])
    regimes = {}
    for regime in ("R1", "R2", "R3", "R4", "R5"):
        m = frame.regime.eq(regime).to_numpy()
        qr = quantiles(x[m])
        # Quantiles are local to the context; use positional arrays to avoid treating regime as a gate.
        net_r = frame.loc[m, "net_bp"].to_numpy(float)
        qmeans = [float(net_r[qr == g].mean()) for g in range(1, 5)]
        regimes[regime] = {"n": int(m.sum()), "continuous_4h_net_rho": spearman(x[m], net_r),
                           "quantile_order": order_stats(qmeans)}
    folds = {}
    for fold in range(1, 10):
        m = frame.fold.eq(fold).to_numpy()
        xf, nf = x[m], frame.loc[m, "net_bp"].to_numpy(float)
        qf = quantiles(xf)
        qmeans = [float(nf[qf == g].mean()) for g in range(1, 5)]
        folds[f"F{fold}"] = {"n": int(m.sum()), "continuous_4h_net_rho": spearman(xf, nf),
                             "quantile_order": order_stats(qmeans)}
    xt, nt = x[top_keep], frame.loc[top_keep, "net_bp"].to_numpy(float)
    qt = quantiles(xt)
    tail = order_stats([float(nt[qt == g].mean()) for g in range(1, 5)])
    direction_core = (continuous["horizons"]["240"]["net_rho"] > 0 and
                      sum(continuous["horizons"][str(h)]["net_rho"] > 0 for h in HORIZONS) >= 3 and
                      full_order["quantile_mean_rho"] > 0 and full_order["favorable_adjacent_steps"] >= 2)
    positive_regimes = sum(v["continuous_4h_net_rho"] > 0 for v in regimes.values())
    positive_folds = sum(v["continuous_4h_net_rho"] > 0 for v in folds.values())
    robust = (positive_regimes >= 3 and positive_folds >= 6 and tail["quantile_mean_rho"] > 0 and
              tail["favorable_adjacent_steps"] >= 2)
    return {"continuous": continuous, "quantiles_by_horizon": quantiles_by_horizon,
            "quartiles_4h": quartiles, "full_order": full_order,
            "regimes": regimes, "positive_regimes": positive_regimes,
            "folds": folds, "positive_folds": positive_folds,
            "top5_removed_order": tail, "direction_core": bool(direction_core),
            "directionally_repeatable": bool(direction_core and robust)}


def prepare() -> pd.DataFrame:
    frame = pd.read_parquet(TRADES).sort_values("signal_id").reset_index(drop=True)
    regime = pd.read_parquet(REGIMES, columns=["ts_ms", "regime"])
    frame["decision_ts_ms"] = frame.signal_ts_ms - MINUTE_MS
    frame = frame.merge(regime, left_on="decision_ts_ms", right_on="ts_ms", how="left",
                        validate="many_to_one")
    if len(frame) != 1603 or frame.regime.isna().any() or frame.regime.eq("UNCLASSIFIED").any():
        raise RuntimeError("E1/R0 join parity failed")
    frame["premium_extreme_bp"] = (frame.s1_cut10 - frame.s1) * 1e4
    frame["oi_drop_bp"] = -frame.oi_chg_1h * 1e4
    frame["vol_intensity"] = frame.rv24h / VOL_HIGH - 1
    ranks = {f: frame[f].rank(method="average", pct=True) for f in BASE_FEATURES}
    frame["premium_x_oi_rank"] = ranks["premium_extreme_bp"] * ranks["oi_drop_bp"]
    frame["oi_x_vol_rank"] = ranks["oi_drop_bp"] * ranks["vol_intensity"]
    if not np.isfinite(frame[[*BASE_FEATURES, *INTERACTIONS]].to_numpy()).all():
        raise RuntimeError("non-finite strength feature")
    return frame


def run(write: bool = True) -> dict:
    frame = prepare()
    remove_n = math.ceil(.05 * len(frame))
    removed = frame.sort_values(["net_bp", "signal_id"], ascending=[False, True]).head(remove_n).index
    top_keep = ~frame.index.isin(removed)
    analyses = {f: analyze_feature(frame, f, top_keep) for f in (*BASE_FEATURES, *INTERACTIONS)}
    repeatable = [f for f in BASE_FEATURES if analyses[f]["directionally_repeatable"]]
    core = [f for f in BASE_FEATURES if analyses[f]["direction_core"]]
    if len(repeatable) >= 2:
        verdict = "USABLE_FOR_FORWARD_SCORING"
    elif len(repeatable) == 1 or len(core) >= 2:
        verdict = "WEAK_STRENGTH_SIGNAL"
    else:
        verdict = "NO_STRENGTH_SIGNAL"
    payload = {"contract_sha256": hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
               "lineage": "reused E1/R0 data; not new OOS confirmation",
               "trades": len(frame), "regime_counts": frame.regime.value_counts().sort_index().to_dict(),
               "top5_removed": remove_n, "features": analyses,
               "repeatable_base_features": repeatable, "direction_core_base_features": core,
               "verdict": verdict, "three_level_score_proposed": verdict == "USABLE_FOR_FORWARD_SCORING",
               "operational_application": "NOT_AUTHORIZED"}
    if write:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "results_v1.json").write_text(json.dumps(payload, indent=2) + "\n")
        frame[["signal_id", "signal_ts_ms", "fold", "regime", *BASE_FEATURES, *INTERACTIONS,
               "net_bp", "mfe_4h_bp", "mae_4h_bp"]].to_parquet(OUT / "strength_trades_v1.parquet", index=False)
    return payload


if __name__ == "__main__":
    result = run()
    print(result["verdict"], result["repeatable_base_features"], result["direction_core_base_features"])
