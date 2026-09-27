"""The G0 study: every number the report quotes, computed once, then the gate.

Nothing here reads a threshold that is not in the frozen rules. The diagnostic reporting cuts
that are not gate inputs (the "near-zero" entry bar below $1,000 of traded value, the 2026-04-20
tape-era split) are named constants below and never feed a verdict.
"""

from collections.abc import Mapping, Sequence
from datetime import date
import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.strategy_e0_overnight import stats as E0S
from app.backtest.strategy_e1_premarket.run import universe_volatility
from app.backtest.strategy_g0_after_premarket import evaluate, gate
from app.backtest.strategy_g0_after_premarket import stats as S
from app.backtest.strategy_g0_after_premarket.config import G0Rules
from app.backtest.strategy_g0_after_premarket.dataset import GRows

NEAR_ZERO_ENTRY_DOLLAR = 1_000.0          # reporting cut only
BROAD_TAPE_START = date(2026, 4, 20)      # first session of the broad minute collection
SECONDARY = ("E0800:0900", "E0800:0915", "E0700:0925", "E0800S:0925")


def _pct(values: np.ndarray) -> dict[str, float]:
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {}
    return {f"p{q}": float(np.percentile(values, q)) for q in (5, 25, 50, 75, 95)}


def code_digest(package_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(package_dir.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def cell_features(rows: GRows, prefix: str) -> dict[str, np.ndarray]:
    set_name = prefix.split(":")[0]
    return {"close_price": rows.features["close_price"],
            "regular_dollar_volume": rows.features["regular_dollar_volume"],
            "after_dollar_volume": rows.features["after_dollar_volume"],
            "premarket_dollar_before_entry": rows.target[f"{set_name}:pre_dollar_before_entry"]}


def _subset(rows: GRows, keep: np.ndarray) -> GRows:
    return GRows(source_sessions=rows.source_sessions[keep], next_sessions=rows.next_sessions[keep],
                 tickers=rows.tickers[keep], kinds=rows.kinds[keep], staging=rows.staging[keep],
                 features={k: v[keep] for k, v in rows.features.items()},
                 labels={k: v[keep] for k, v in rows.labels.items()},
                 target={k: v[keep] for k, v in rows.target.items()},
                 has_entry=rows.has_entry[keep], counters=dict(rows.counters))


def baseline_block(rows: GRows, prefix: str) -> dict[str, Any]:
    r = rows.labels[f"{prefix}:R"]
    ok = np.isfinite(r)
    return {"summary": S.summarise(r[ok]),
            "excursions": S.excursions(rows.labels[f"{prefix}:MFE"][ok], rows.labels[f"{prefix}:MAE"][ok])}


def execution_block(signal: GRows, selected_all: np.ndarray, prefix: str,
                    rules: G0Rules) -> dict[str, Any]:
    """Fill rate over every signal row of the hypothesis; liquidity over the filled ones."""
    set_name = prefix.split(":")[0]
    filled = selected_all & signal.has_entry
    dollar = signal.target[f"{set_name}:entry_dollar"][filled]
    volume = signal.target[f"{set_name}:entry_volume"][filled]
    prior = signal.target[f"{set_name}:prior30_bars"][filled]
    minute = signal.target[f"{set_name}:entry_minute"][filled]
    entry_minute = int(rules.raw["label_sets"][set_name]["entry_minute_et"])
    return {
        "signal_rows": int(selected_all.sum()), "filled_rows": int(filled.sum()),
        "entry_fill_rate": float(filled.sum() / selected_all.sum()) if selected_all.any() else float("nan"),
        "median_entry_dollar": float(np.median(dollar)) if dollar.size else float("nan"),
        "entry_dollar": _pct(dollar), "entry_volume": _pct(volume), "prior30_bars": _pct(prior),
        "prior30_zero_share": float(np.mean(prior == 0)) if prior.size else float("nan"),
        "near_zero_entry_share": float(np.mean(dollar < NEAR_ZERO_ENTRY_DOLLAR)) if dollar.size else float("nan"),
        "entry_on_exact_minute_share": float(np.mean(minute == entry_minute)) if minute.size else float("nan"),
        "entry_delay_minutes": _pct(minute - entry_minute),
    }


def gap_block(rows: GRows, selected: np.ndarray, prefix: str, rules: G0Rules) -> dict[str, Any]:
    gap = rows.labels[f"{prefix}:after_close_to_entry"][selected]
    r = rows.labels[f"{prefix}:R"][selected]
    ok = np.isfinite(gap) & np.isfinite(r)
    gap, r = gap[ok], r[ok]
    total = (1 + gap) * (1 + r) - 1
    out: dict[str, Any] = {"n": int(gap.size), "after_close_to_entry": S.summarise(gap),
                           "entry_to_exit": S.summarise(r), "after_close_to_exit": S.summarise(total)}
    if gap.size > 2:
        out["pearson"] = float(np.corrcoef(gap, r)[0, 1])
        rank_g = np.argsort(np.argsort(gap))
        rank_r = np.argsort(np.argsort(r))
        out["spearman"] = float(np.corrcoef(rank_g, rank_r)[0, 1])
        edges = rules.bucket_edges("after_close_to_entry")
        index = E0S.bucket_index(gap, edges)
        out["by_gap_bucket"] = [
            {"bucket": label, "n": int((index == i).sum()),
             "mean_gap": float(gap[index == i].mean()) if (index == i).any() else None,
             "mean_entry_to_exit": float(r[index == i].mean()) if (index == i).any() else None,
             "median_entry_to_exit": float(np.median(r[index == i])) if (index == i).any() else None,
             "p_ge_2pct": float(np.mean(r[index == i] >= 0.02)) if (index == i).any() else None}
            for i, label in enumerate(E0S.bucket_labels(edges))]
    return out


def split_table(rows: GRows, masks: Mapping[str, np.ndarray], groups: Mapping[str, np.ndarray],
                prefix: str) -> dict[str, Any]:
    r = rows.labels[f"{prefix}:R"]
    out: dict[str, Any] = {}
    for gname, gsel in groups.items():
        base = S.summarise(r[gsel])
        block: dict[str, Any] = {"baseline": {k: base.get(k) for k in ("n", "mean", "median", "win_rate",
                                                                       "p_gap_ge_2pct")}}
        for name, sel in masks.items():
            cand = S.summarise(r[gsel & sel])
            if cand.get("n") and base.get("n"):
                block[name] = {"n": cand["n"], "mean": cand["mean"], "median": cand["median"],
                               "win_rate": cand["win_rate"], "mean_lift": cand["mean"] - base["mean"],
                               "tail2_lift": cand["p_gap_ge_2pct"] - base["p_gap_ge_2pct"]}
            else:
                block[name] = {"n": cand.get("n", 0)}
        out[gname] = block
    return out


def regimes(rows: GRows, daily: Any, panel: Any) -> dict[str, np.ndarray]:
    grid = list(daily.sessions)
    index_of = {s: i for i, s in enumerate(grid)}
    vol = universe_volatility(panel)
    t_idx = np.array([index_of[s] for s in rows.source_sessions])
    trailing = vol[t_idx]
    cut = float(np.nanmedian(vol[np.unique(t_idx)]))
    # breadth: share of daily PIT rows with a positive day return at T
    dr = daily.features["day_return"]
    ok = np.isfinite(dr)
    up = np.bincount(daily.session_idx[ok], weights=(dr[ok] > 0).astype(float), minlength=len(grid))
    n = np.bincount(daily.session_idx[ok], minlength=len(grid))
    breadth = np.where(n > 0, up / np.maximum(n, 1), np.nan)[t_idx]
    spy = rows.features["spy_day_return"]
    return {"spy_t_positive": spy > 0, "spy_t_negative": spy < 0,
            "volatility_high": trailing >= cut, "volatility_low": trailing < cut,
            "risk_on": breadth >= 0.5, "risk_off": breadth < 0.5}


def study(signal: GRows, rules: G0Rules, daily: Any, panel: Any) -> dict[str, Any]:
    raw = rules.raw
    prefix = rules.primary_prefix
    rows = _subset(signal, signal.has_entry & np.isfinite(signal.labels[f"{prefix}:R"]))
    r = rows.labels[f"{prefix}:R"]
    base = baseline_block(rows, prefix)
    baseline, baseline_exc = base["summary"], base["excursions"]
    cells = cell_features(rows, prefix)

    masks = {name: evaluate.mask(name, rows.features, raw) for name in evaluate.HYPOTHESES}
    signal_masks = {name: evaluate.mask(name, signal.features, raw) for name in evaluate.HYPOTHESES}

    candidates: dict[str, Any] = {}
    verdicts = []
    for name in evaluate.HYPOTHESES:
        block = evaluate.candidate(name, masks[name], rows, prefix, baseline, baseline_exc, raw, cells)
        block["execution"] = execution_block(signal, signal_masks[name], prefix, rules)
        block["gap_decomposition"] = gap_block(rows, masks[name], prefix, rules)
        block["secondary"] = {}
        for label in SECONDARY:
            sub = _subset(signal, np.isfinite(signal.labels[f"{label}:R"]))
            sub_base = baseline_block(sub, label)
            sub_mask = evaluate.mask(name, sub.features, raw)
            block["secondary"][label] = evaluate.candidate(
                name, sub_mask, sub, label, sub_base["summary"], sub_base["excursions"], raw,
                cell_features(sub, label), full=False)
            block["secondary"][label]["baseline"] = sub_base
        verdict = gate.evaluate(block, block["execution"], rules.gate)
        verdicts.append(verdict)
        candidates[name] = block

    single = {}
    mfe, mae = rows.labels[f"{prefix}:MFE"], rows.labels[f"{prefix}:MAE"]
    for feature in rules.bucket_names:
        if feature == "after_close_to_entry":
            series = rows.labels[f"{prefix}:after_close_to_entry"]
        else:
            series = rows.features.get(feature)
        if series is None:
            continue
        single[feature] = evaluate.bucket_table(series, r, mfe, mae, rules.bucket_edges(feature),
                                                baseline)

    months = np.array([evaluate.month_of(s) for s in rows.source_sessions], dtype=object)
    half_cut = sorted(rows.source_sessions)[len(rows) // 2]
    era = {"early_tape_before_2026_04_20": rows.source_sessions < BROAD_TAPE_START,
           "broad_tape_from_2026_04_20": rows.source_sessions >= BROAD_TAPE_START}
    halves = {"first_half": rows.source_sessions < half_cut, "second_half": rows.source_sessions >= half_cut}
    kinds = {k: rows.kinds == k for k in ("normal_overnight", "weekend_gap", "holiday_gap")}
    regime_masks = regimes(rows, daily, panel)

    baseline_labels = {}
    for label in (prefix,) + SECONDARY:
        sub = _subset(signal, np.isfinite(signal.labels[f"{label}:R"]))
        baseline_labels[label] = baseline_block(sub, label)

    return {
        "rows": len(rows), "signal_rows": len(signal),
        "baseline": base, "baseline_by_label": baseline_labels,
        "baseline_monthly": S.period_table(r, months, r, months),
        "baseline_execution": execution_block(signal, np.ones(len(signal), dtype=bool), prefix, rules),
        "baseline_gap": gap_block(rows, np.ones(len(rows), dtype=bool), prefix, rules),
        "single_feature": single,
        "candidates": candidates,
        "weekend_holiday": split_table(rows, masks, kinds, prefix),
        "regimes": split_table(rows, masks, regime_masks, prefix),
        "halves": {"split_at": str(half_cut), **split_table(rows, masks, halves, prefix)},
        "tape_era": split_table(rows, masks, era, prefix),
        "gate": {"primary_label": rules.primary_label, "hypotheses": verdicts,
                 "verdict": gate.combine(verdicts)},
    }
