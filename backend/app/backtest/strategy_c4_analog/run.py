"""C-4 internal screen: inputs -> context -> library -> analogs -> engineering gate -> statistics.

The order is the declaration's order. Nothing in the evaluation stage runs until the engineering
and point-in-time checks have been written to the run directory and have all passed.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from app.backtest.engine.identity import code_digest, package_files, run_identity, source_provenance
from app.backtest.strategy_c4_analog import audit as c4_audit
from app.backtest.strategy_c4_analog import context as c4_context
from app.backtest.strategy_c4_analog import evaluate as c4_eval
from app.backtest.strategy_c4_analog import features as c4_features
from app.backtest.strategy_c4_analog import labels4, neighbors, stores, universe
from app.backtest.strategy_c4_analog.rules import (C4Rules, GATE_NAME, REPO_ROOT, STRATEGY_ID,
                                                   feature_names, load_rules)
from app.backtest.strategy_c_e0.pit import build_grid
from app.backtest.strategy_c_e0.taxonomy import Taxonomy
from app.backtest.strategy_c_selection.features import compute as compute_c_features
from app.backtest.strategy_c_selection.labels import compute_labels
from app.backtest.strategy_c_selection.panel import truncate
from app.backtest.strategy_c_selection.rules import SelectionRules
from app.backtest.strategy_c_selection.rules import load_rules as load_c_rules

RUNS = Path("data/runtime/strategy_c4/runs")
CACHE = Path("data/runtime/strategy_c4/cache")
TAXONOMY_PATH = REPO_ROOT / "docs/backtest/strategy_c/v2/c_e0_event_taxonomy_v1.json"
C_RUN = Path("data/runtime/strategy_c/runs/cmsel1-855b6a0ce64e3698fc74")
IDENTITY_NAMESPACE = "strategy-c4-analog-run"
RUN_PREFIX = "c4scr1"
PIT_AUDIT_DATES = 12
ACTIVE_FILER_DAYS = 400


def _clean(obj: object) -> object:
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj) if np.isfinite(obj) else None
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, np.ndarray)):
        return [_clean(v) for v in obj]
    return obj


def dump_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(_clean(payload), indent=2, sort_keys=True, ensure_ascii=False,
                               allow_nan=False) + "\n", encoding="utf-8")


@dataclass
class Inputs:
    sessions: tuple[date, ...]
    panel: object
    vw: np.ndarray
    c_rules: SelectionRules
    c_features: object
    labels: object
    base: np.ndarray
    ca_excluded: np.ndarray
    cik_code: np.ndarray
    cik_names: tuple[str, ...]
    figi: np.ndarray
    snapshots: tuple


def load_inputs(log) -> Inputs:
    c_rules = load_c_rules()
    root = universe.C_RAW_ROOT
    sessions = universe.usable_sessions(root, universe.all_sessions(root))
    snapshots = universe.load_reference(root, universe.SNAPSHOT_DATES, c_rules.allowed_exchanges)
    panel, vw = universe.load_panel_and_vw(root, sessions, snapshots, universe.SPLIT_RANGE)
    log(f"panel {panel.shape} sessions {sessions[0]}..{sessions[-1]}")
    c_features = compute_c_features(panel, c_rules)
    labels = compute_labels(panel, (5, 10), c_rules.ca_ratio)
    base = universe.base_eligible(c_features, c_rules)
    cik_code, cik_names = universe.cik_codes(panel, snapshots)
    figi = universe.entity_codes(panel, snapshots)
    log("c features, labels, codes built")
    return Inputs(sessions, panel, vw, c_rules, c_features, labels, base,
                  c_features.ca_excluded, cik_code, cik_names, figi, snapshots)


def context_cache_path(name: str) -> Path:
    return CACHE / name


def build_event_tables(inputs: Inputs, rules: C4Rules, log) -> tuple[c4_context.CikTables, dict]:
    grid = build_grid(inputs.sessions)
    taxonomy = Taxonomy(json.loads(TAXONOMY_PATH.read_text(encoding="utf-8")))
    roots = stores.StoreRoots()
    covered = c4_context.load_covered_ciks()
    log(f"covered ciks from manifests: {len(covered)}")
    tables, periodic = c4_context.build_cik_tables(
        roots, inputs.cik_names, grid, taxonomy, acceptance_zone=rules.acceptance_zone,
        covered_ciks=covered, log=log)
    return tables, periodic


def save_event_tables(tables: c4_context.CikTables, periodic: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        context_cache_path("event_tables.npz"), covered=tables.covered,
        material_count=tables.material_count, ma_pin=tables.ma_pin,
        unclassifiable=tables.unclassifiable, unknown_items=tables.unknown_items,
        pending=tables.pending, active=tables.active, periodic=tables.periodic,
        **{f"class_{k}": v for k, v in tables.class_flags.items()})
    payload = {cik: [[int(s), moment.isoformat(), accession] for s, moment, accession in entries]
               for cik, entries in periodic.items()}
    context_cache_path("periodic_accessions.json").write_text(json.dumps(payload), encoding="utf-8")


def load_event_tables(names: Sequence[str]) -> tuple[c4_context.CikTables, dict]:
    data = np.load(context_cache_path("event_tables.npz"))
    class_flags = {key[6:]: data[key] for key in data.files if key.startswith("class_")}
    shape = data["material_count"].shape
    tables = c4_context.CikTables(
        tuple(names), data["covered"], data["material_count"], class_flags, data["ma_pin"],
        data["unclassifiable"], data["unknown_items"], data["pending"], data["active"],
        data["periodic"], np.full(shape, -1, dtype=np.int8), np.full(shape, np.nan, dtype=np.float32))
    raw = json.loads(context_cache_path("periodic_accessions.json").read_text(encoding="utf-8"))
    from datetime import datetime
    periodic = {cik: [(int(s), datetime.fromisoformat(m), a) for s, m, a in entries]
                for cik, entries in raw.items()}
    return tables, periodic


def xbrl_targets(periodic: dict) -> list[str]:
    roots = stores.StoreRoots()
    stored = stores.stored_xbrl_ciks(roots)
    return sorted(set(periodic) - stored)


def build_family_matrices(normalized: dict[str, np.ndarray], rules: C4Rules,
                          rows_i: np.ndarray, rows_j: np.ndarray,
                          include_vwap: bool) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for family in ("F0", "F1", "F2", "F3"):
        names = [name for name in feature_names(rules, family)
                 if include_vwap or name not in c4_features.VWAP_FEATURES]
        out[family] = c4_features.matrix(normalized, names, rows_i, rows_j)
    return out


#: The frozen C-M run wrote C-M0 over its secondary window, session index 60..N-1-10.
C_M_FIRST_IDX = 60
C_M_FORWARD = 10


def recompute_c_m0(inputs: Inputs) -> set[tuple[str, str]]:
    """C-M0 recomputed from the panel, on exactly the window the frozen tables cover."""
    variant = next(v for v in inputs.c_rules.variants if v.name == "C-M0")
    mask = inputs.c_features.candidates(variant)
    index = np.arange(inputs.panel.shape[0])[:, None]
    last = len(inputs.sessions) - 1 - C_M_FORWARD
    mask = mask & (index >= C_M_FIRST_IDX) & (index <= last)
    ii, jj = np.nonzero(mask)
    return {(inputs.sessions[i].isoformat(), inputs.panel.tickers[j]) for i, j in zip(ii, jj)}


def reproduce_c_e0(event, inputs: Inputs) -> dict:
    """Does the C-4 event pipeline reproduce the frozen C-E0 cohort on C-E0's own 6,680 rows?

    An extra check the declaration does not require. It can only make the gate stricter, and it
    is the strongest available evidence that the CIK mapping, taxonomy, acceptance placement,
    window and precedence are the closed study's and not a re-implementation of them.
    """
    path = Path("data/runtime/strategy_c/e0/runs/ce01-234478e8f7ff27570e13/candidate_status.parquet")
    if not path.exists():
        return {"available": False, "identical": None}
    frame = pd.read_parquet(path, columns=["date_idx", "ticker", "status", "event_count"])
    column = {ticker: j for j, ticker in enumerate(inputs.panel.tickers)}
    j = frame.ticker.map(column).to_numpy()
    i = frame.date_idx.to_numpy()
    material = {"RESULTS", "AGREEMENT", "ACQUISITION_DISPOSITION", "OTHER_MATERIAL"}
    names = np.asarray(c4_context.CATEGORIES)
    mine = []
    for code, unknown in zip(event.category[i, j], event.unknown[i, j]):
        if unknown:
            mine.append("UNKNOWN_OR_EXCLUDED")
            continue
        name = names[code]
        mine.append("EM_NEGATIVE_RISK" if name == "NEGATIVE_RISK"
                    else "EM" if name in material else "M_ONLY")
    theirs = np.where(frame.status.isin(["UNKNOWN_MAPPING", "UNKNOWN_COVERAGE", "UNKNOWN_PIT",
                                         "UNKNOWN_FORM", "EXCLUDED_MA_TARGET"]),
                      "UNKNOWN_OR_EXCLUDED", frame.status)
    agree = np.asarray(mine) == theirs
    known = theirs != "UNKNOWN_OR_EXCLUDED"
    counts_agree = frame.event_count.to_numpy()[known] == np.nan_to_num(
        event.event_count[i, j])[known]
    return {"available": True, "rows": int(agree.size), "status_agree": int(agree.sum()),
            "event_count_rows": int(known.sum()), "event_count_agree": int(counts_agree.sum()),
            "identical": bool(agree.all() and counts_agree.all())}


def frozen_c_m0() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for name in ("candidates_primary.parquet", "candidates_secondary.parquet"):
        frame = pd.read_parquet(C_RUN / name, columns=["variant", "signal_date", "ticker"])
        frame = frame[frame.variant == "C-M0"]
        out |= set(zip(frame.signal_date, frame.ticker))
    return out


@dataclass
class Rows:
    library_i: np.ndarray
    library_j: np.ndarray
    query_i: np.ndarray
    query_j: np.ndarray
    library_mask: np.ndarray
    query_mask: np.ndarray


def continuous_names(include_vwap: bool) -> tuple[str, ...]:
    names = list(c4_features.MARKET_FEATURES) + ["obv_return_5", "obv_return_10", "obv_return_20",
                                                 "obv_slope_5", "obv_slope_10", "return_10d_helper"]
    if include_vwap:
        names += list(c4_features.VWAP_FEATURES)
    return tuple(names)


def build_rows(inputs: Inputs, rules: C4Rules, raw: c4_features.RawFeatures,
               c_m0: np.ndarray, include_vwap: bool) -> Rows:
    """A row is usable when it is base-eligible, label-valid and every continuous coordinate exists."""
    defined = inputs.base & ~inputs.ca_excluded & inputs.labels.valid(5) & inputs.labels.valid(10)
    for name in continuous_names(include_vwap):
        defined = defined & np.isfinite(raw.values[name])
    index = np.arange(inputs.panel.shape[0])[:, None]
    library_mask = defined & (index >= rules.library_start_idx) & (index <= rules.last_query_idx)
    query_mask = (library_mask & c_m0 & (index >= rules.first_query_idx)
                  & (index <= rules.last_query_idx))
    li, lj = np.nonzero(library_mask)
    qi, qj = np.nonzero(query_mask)
    return Rows(li, lj, qi, qj, library_mask, query_mask)


def pit_audit(inputs: Inputs, rules: C4Rules, tables: c4_context.CikTables,
              raw: c4_features.RawFeatures, normalized: dict[str, np.ndarray],
              include_vwap: bool, log, raw_dates: int = 4, norm_dates: int = 8) -> dict:
    """Recompute row D in a world truncated at D and require the result to match bit for bit."""
    window = list(range(rules.first_query_idx, rules.last_query_idx + 1))
    picks = sorted({window[int(round(i * (len(window) - 1) / max(1, norm_dates - 1)))]
                    for i in range(norm_dates)})
    raw_picks = picks[:: max(1, len(picks) // raw_dates)][:raw_dates]
    names = sorted(normalized)
    mismatches = checked = raw_mismatches = raw_checked = 0
    for d in picks:
        truncated = c4_features.RawFeatures(
            {name: value[: d + 1] for name, value in raw.values.items()},
            raw.category[: d + 1], raw.event_unknown[: d + 1])
        again = c4_features.normalize(truncated, inputs.base[: d + 1], rules.library_start_idx,
                                      include_vwap=include_vwap)
        columns = inputs.base[d]
        for name in names:
            left, right = normalized[name][d][columns], again[name][d][columns]
            mismatches += int((left != right).sum())
            checked += int(columns.sum())
    for d in raw_picks:
        panel_t = truncate(inputs.panel, inputs.sessions[d])
        c_t = compute_c_features(panel_t, inputs.c_rules)
        event_t = c4_context.project(_truncated_tables(tables, d), inputs.cik_code[: d + 1],
                                     inputs.sessions[: d + 1])
        raw_t = c4_features.build_raw(panel_t, inputs.vw[: d + 1], c_t, event_t)
        columns = inputs.base[d]
        for name in list(continuous_names(include_vwap)) + ["event_present", "event_count",
                                                            "negative_risk_flag", "revenue_yoy",
                                                            "periodic_event_flag"]:
            left, right = raw.values[name][d][columns], raw_t.values[name][d][columns]
            equal = (left == right) | (~np.isfinite(left) & ~np.isfinite(right))
            raw_mismatches += int((~equal).sum())
            raw_checked += int(columns.sum())
        equal_category = raw.category[d][columns] == raw_t.category[d][columns]
        raw_mismatches += int((~equal_category).sum())
    log(f"pit audit: normalization mismatches={mismatches}/{checked} "
        f"raw mismatches={raw_mismatches}/{raw_checked}")
    return {"normalization_dates": picks, "raw_dates": raw_picks,
            "normalization_cells_checked": checked, "normalization_mismatches": mismatches,
            "raw_cells_checked": raw_checked, "raw_mismatches": raw_mismatches}


def _truncated_tables(tables: c4_context.CikTables, d: int) -> c4_context.CikTables:
    cut = d + 1
    return c4_context.CikTables(
        tables.names, tables.covered, tables.material_count[:, :cut],
        {k: v[:, :cut] for k, v in tables.class_flags.items()}, tables.ma_pin[:, :cut],
        tables.unclassifiable[:, :cut], tables.unknown_items[:, :cut], tables.pending[:, :cut],
        tables.active[:, :cut], tables.periodic[:, :cut], tables.quality_status[:, :cut],
        tables.revenue_yoy[:, :cut])


@dataclass
class SearchResult:
    forecast: dict[str, np.ndarray]
    complete: np.ndarray
    neighbor_dates: np.ndarray
    neighbor_tickers: np.ndarray
    neighbor_count: np.ndarray


def run_family(family: str, matrices: dict[str, np.ndarray], rows: Rows, inputs: Inputs,
               rules: C4Rules, library_outcomes: dict[str, np.ndarray], log,
               keep_neighbors: bool) -> tuple[SearchResult, c4_audit.AuditLog]:
    index = neighbors.build_index(matrices[f"{family}_library"], rows.library_i,
                                  rows.library_j.astype(np.int32),
                                  inputs.figi[rows.library_i, rows.library_j])
    outcomes = {name: values[index.order] for name, values in library_outcomes.items()}
    queries = matrices[f"{family}_query"]
    query_figi = inputs.figi[rows.query_i, rows.query_j]
    log_audit = c4_audit.AuditLog()
    n = rows.query_i.size
    forecast = {name: np.full(n, np.nan) for name in
                ("median_excess_5", "mean_excess_5", "median_excess_10", "positive_rate_5",
                 "positive_rate_10", "median_mfe_5", "median_mae_5", "median_mfe_10",
                 "median_mae_10")}
    complete = np.zeros(n, dtype=bool)
    count = np.zeros(n, dtype=np.int32)
    top_k = rules.top_k
    keep_dates = np.full((n, top_k), -1, dtype=np.int32) if keep_neighbors else None
    keep_tickers = np.full((n, top_k), -1, dtype=np.int32) if keep_neighbors else None
    started = time.perf_counter()
    for position, day in enumerate(np.unique(rows.query_i)):
        slots = np.flatnonzero(rows.query_i == day)
        cut = neighbors.cut_for(index, int(day), rules.embargo_sessions)
        chosen = neighbors.search_date(index, queries[slots], rows.query_j[slots].tolist(),
                                       query_figi[slots].tolist(), cut, top_k=top_k,
                                       ticker_cap=rules.ticker_cap, date_cap=rules.date_cap,
                                       figi_cap=rules.figi_cap)
        for slot, picks in zip(slots, chosen):
            picked = np.asarray(picks, dtype=np.int64)
            count[slot] = picked.size
            if picked.size == 0:
                continue
            c4_audit.check_neighbors(
                log_audit, query_date=int(day), embargo=rules.embargo_sessions,
                query_ticker=int(rows.query_j[slot]), query_figi=int(query_figi[slot]),
                neighbor_dates=index.date_idx[picked], neighbor_tickers=index.ticker_col[picked],
                neighbor_figis=index.figi[picked], ticker_cap=rules.ticker_cap,
                date_cap=rules.date_cap, max_horizon=max(rules.horizons))
            if picked.size < top_k:
                continue
            complete[slot] = True
            excess5 = outcomes["excess_5"][picked]
            excess10 = outcomes["excess_10"][picked]
            forecast["median_excess_5"][slot] = float(np.median(excess5))
            forecast["mean_excess_5"][slot] = float(np.mean(excess5))
            forecast["median_excess_10"][slot] = float(np.median(excess10))
            forecast["positive_rate_5"][slot] = float(np.mean(excess5 > 0))
            forecast["positive_rate_10"][slot] = float(np.mean(excess10 > 0))
            for name, key in (("median_mfe_5", "mfe_5"), ("median_mae_5", "mae_5"),
                              ("median_mfe_10", "mfe_10"), ("median_mae_10", "mae_10")):
                forecast[name][slot] = float(np.median(outcomes[key][picked]))
            if keep_neighbors:
                keep_dates[slot] = index.date_idx[picked]
                keep_tickers[slot] = index.ticker_col[picked]
        if position % 60 == 0:
            log(f"{family} date {position + 1} cut={cut} elapsed={time.perf_counter() - started:.0f}s")
    log(f"{family} search done in {time.perf_counter() - started:.0f}s complete={int(complete.sum())}/{n}")
    return SearchResult(forecast, complete,
                        keep_dates if keep_neighbors else np.zeros((0, 0), dtype=np.int32),
                        keep_tickers if keep_neighbors else np.zeros((0, 0), dtype=np.int32),
                        count), log_audit


def evaluate_families(results: dict[str, SearchResult], rows: Rows, inputs: Inputs, rules: C4Rules,
                      query_outcomes: dict[str, np.ndarray], raw: c4_features.RawFeatures,
                      log) -> dict:
    bootstrap = rules.bootstrap
    alpha = float(bootstrap["levels"][0])
    excess5 = query_outcomes["excess_5"]
    realized = {"excess_5": excess5, "excess_10": query_outcomes["excess_10"],
                "mfe_5": query_outcomes["mfe_5"], "mae_5": query_outcomes["mae_5"],
                "mfe_10": query_outcomes["mfe_10"], "mae_10": query_outcomes["mae_10"]}
    date_idx = rows.query_i
    series: dict[str, c4_eval.DailySeries] = {}
    for family, result in results.items():
        usable = result.complete & np.isfinite(excess5)
        series[family] = c4_eval.daily_ic(result.forecast["median_excess_5"][usable],
                                          excess5[usable], date_idx[usable], rules.min_queries_for_ic)
        log(f"{family} IC dates={series[family].dates.size} point={series[family].mean:.5f}")

    primary = rules.primary_family
    reference = rules.reference_family
    shared_dates, primary_values, reference_values = c4_eval.align(series[primary], series[reference])
    draws_primary = c4_eval.block_draws(series[primary].dates.size,
                                        block_length=int(bootstrap["block_length"]),
                                        replicates=int(bootstrap["replicates"]),
                                        seed=int(bootstrap["seed"]))
    draws_shared = c4_eval.block_draws(shared_dates.size, block_length=int(bootstrap["block_length"]),
                                       replicates=int(bootstrap["replicates"]),
                                       seed=int(bootstrap["seed"]))
    ic_summary = {family: c4_eval.summarize(
        value.values, c4_eval.block_draws(value.dates.size, block_length=int(bootstrap["block_length"]),
                                          replicates=int(bootstrap["replicates"]),
                                          seed=int(bootstrap["seed"])), alpha)
        for family, value in series.items()}
    difference = c4_eval.summarize(primary_values - reference_values, draws_shared, alpha)

    usable_primary = results[primary].complete & np.isfinite(excess5)
    baskets = c4_eval.daily_baskets(
        results[primary].forecast["median_excess_5"][usable_primary], date_idx[usable_primary],
        rows.query_j[usable_primary].astype(float),
        {name: values[usable_primary] for name, values in realized.items()},
        rules.min_queries_for_quintiles)
    spread5 = c4_eval.nan_safe(c4_eval.spread_series(baskets, "excess_5"))
    q5_series = c4_eval.nan_safe(c4_eval.column_series(baskets, "excess_5", 4))
    draws_spread = c4_eval.block_draws(spread5.size, block_length=int(bootstrap["block_length"]),
                                       replicates=int(bootstrap["replicates"]),
                                       seed=int(bootstrap["seed"]))
    draws_q5 = c4_eval.block_draws(q5_series.size, block_length=int(bootstrap["block_length"]),
                                   replicates=int(bootstrap["replicates"]),
                                   seed=int(bootstrap["seed"]))
    quintile_table = {
        name: {f"Q{q + 1}": float(np.nanmean(baskets.per_date[name][:, q])) for q in range(5)}
        for name in realized}
    quintile_table["hit_rate_excess_5_positive"] = {
        f"Q{q + 1}": float(np.nanmean(baskets.per_date["excess_5"][:, q] > 0)) for q in range(5)}

    blocks = c4_eval.time_blocks(series[primary].dates, rules.time_blocks)
    block_rows = []
    for part in blocks:
        selected = np.isin(series[primary].dates, part)
        shared_selected = np.isin(shared_dates, part)
        block_rows.append({
            "first_date": inputs.sessions[int(part[0])].isoformat(),
            "last_date": inputs.sessions[int(part[-1])].isoformat(),
            "dates": int(part.size),
            "ic_primary": float(np.mean(series[primary].values[selected])) if selected.any() else None,
            "ic_reference": float(np.mean(series[reference].values[
                np.isin(series[reference].dates, part)])) if np.isin(series[reference].dates, part).any() else None,
            "ic_difference": float(np.mean((primary_values - reference_values)[shared_selected]))
            if shared_selected.any() else None,
            "q5_minus_q1_excess_5": float(np.nanmean(
                c4_eval.spread_series(baskets, "excess_5")[np.isin(baskets.dates, part)]))
            if np.isin(baskets.dates, part).any() else None,
        })

    tickers = rows.query_j[usable_primary]
    dates = rows.query_i[usable_primary]
    top_tickers = c4_eval.top_labels(tickers, 5)
    top_dates = c4_eval.top_labels(dates, 10)
    leave_out = {}
    for name, keep in (("drop_top_5_tickers", ~np.isin(tickers, top_tickers)),
                       ("drop_top_10_dates", ~np.isin(dates, top_dates))):
        forecast = results[primary].forecast["median_excess_5"][usable_primary][keep]
        actual = excess5[usable_primary][keep]
        subset = c4_eval.daily_ic(forecast, actual, dates[keep], rules.min_queries_for_ic)
        sub_baskets = c4_eval.daily_baskets(forecast, dates[keep],
                                            tickers[keep].astype(float),
                                            {"excess_5": actual}, rules.min_queries_for_quintiles)
        leave_out[name] = {"ic_point": subset.mean, "ic_dates": int(subset.dates.size),
                           "q5_minus_q1": float(np.nanmean(c4_eval.spread_series(sub_baskets, "excess_5")))
                           if sub_baskets.dates.size else None}

    analog_dates = results[primary].neighbor_dates
    analog_tickers = results[primary].neighbor_tickers
    used = analog_dates[analog_dates >= 0]
    used_tickers = analog_tickers[analog_tickers >= 0]
    concentration = {
        "query_ticker": c4_eval.concentration(tickers, 5),
        "query_date": c4_eval.concentration(dates, 10),
        "analog_date": c4_eval.concentration(used, 10),
        "analog_ticker": c4_eval.concentration(used_tickers, 5),
        "top_query_tickers": [inputs.panel.tickers[int(t)] for t in top_tickers],
        "top_query_dates": [inputs.sessions[int(d)].isoformat() for d in top_dates],
    }

    baselines = {}
    event_present = raw.values["event_present"][rows.query_i, rows.query_j]
    material = (raw.values["revenue_yoy"][rows.query_i, rows.query_j] >= 0.10).astype(float)
    momentum = raw.values["return_5d"][rows.query_i, rows.query_j]
    for name, forecast in (("C_E0_event_presence", event_present),
                           ("EQM_material", material),
                           ("simple_momentum_return_5d", momentum)):
        usable = np.isfinite(forecast) & np.isfinite(excess5) & results[primary].complete
        value = c4_eval.daily_ic(forecast[usable], excess5[usable], date_idx[usable],
                                 rules.min_queries_for_ic)
        draws = c4_eval.block_draws(value.dates.size, block_length=int(bootstrap["block_length"]),
                                    replicates=int(bootstrap["replicates"]), seed=int(bootstrap["seed"]))
        baselines[name] = c4_eval.summarize(value.values, draws, alpha)
        if name != "simple_momentum_return_5d":
            hot = usable & (forecast > 0.5)
            cold = usable & (forecast <= 0.5)
            baselines[name]["group_mean_excess_5_flag_1"] = float(np.mean(excess5[hot])) if hot.any() else None
            baselines[name]["group_mean_excess_5_flag_0"] = float(np.mean(excess5[cold])) if cold.any() else None
            baselines[name]["n_flag_1"] = int(hot.sum())
    pool_daily = np.asarray([float(np.mean(excess5[(date_idx == day) & results[primary].complete]))
                             for day in np.unique(date_idx)
                             if ((date_idx == day) & results[primary].complete).any()])
    baselines["C_M0_pool_mean_excess_5"] = c4_eval.summarize(
        pool_daily, c4_eval.block_draws(pool_daily.size, block_length=int(bootstrap["block_length"]),
                                        replicates=int(bootstrap["replicates"]),
                                        seed=int(bootstrap["seed"])), alpha)

    return {
        "ic": ic_summary,
        "ic_difference_primary_minus_reference": difference,
        "quintiles": quintile_table,
        "q5_minus_q1_excess_5": c4_eval.summarize(spread5, draws_spread, alpha),
        "q5_excess_5": c4_eval.summarize(q5_series, draws_q5, alpha),
        "q5_minus_q1_excess_10": float(np.nanmean(c4_eval.spread_series(baskets, "excess_10"))),
        "q5_minus_q1_mfe_5": float(np.nanmean(c4_eval.spread_series(baskets, "mfe_5"))),
        "q5_minus_q1_mae_5": float(np.nanmean(c4_eval.spread_series(baskets, "mae_5"))),
        "time_blocks": block_rows,
        "leave_out": leave_out,
        "concentration": concentration,
        "baselines": baselines,
        "basket_dates": int(baskets.dates.size),
        "ic_dates": {family: int(value.dates.size) for family, value in series.items()},
        "bootstrap": bootstrap,
    }


def decide(statistics: dict, engineering: dict, coverage: dict, rules: C4Rules) -> dict:
    primary = rules.primary_family
    ic = statistics["ic"][primary]
    difference = statistics["ic_difference_primary_minus_reference"]
    spread = statistics["q5_minus_q1_excess_5"]
    q5 = statistics["q5_excess_5"]
    quint = statistics["quintiles"]
    blocks = [row["ic_primary"] for row in statistics["time_blocks"]]
    leave = statistics["leave_out"]
    conc = statistics["concentration"]
    c1_pattern = (quint["mfe_5"]["Q5"] > quint["mfe_5"]["Q1"]
                  and quint["mae_5"]["Q5"] < quint["mae_5"]["Q1"]
                  and abs(quint["excess_5"]["Q5"]) < 0.001)
    conditions = {
        "C1": bool(ic["point"] > 0),
        "C2": bool(np.isfinite(ic["ci_low"]) and ic["ci_low"] > 0),
        "C3": bool(spread["point"] > 0 and spread["ci_low"] > 0),
        "C4": bool(q5["point"] > 0),
        "C5": bool(not c1_pattern),
        "C6": bool(quint["mae_5"]["Q5"] - quint["mae_5"]["Q1"] >= -0.02),
        "C7": bool(sum(1 for value in blocks if value is not None and value > 0) >= 3),
        "C8": bool(all(row["ic_point"] > 0 and (row["q5_minus_q1"] or 0) > 0
                       for row in leave.values())),
        "C9": bool(engineering["pass"]),
        "C10": bool(difference["point"] > 0 and difference["ci_low"] > 0),
    }
    if not coverage["pass"]:
        decision = "INCONCLUSIVE"
    elif not engineering["pass"]:
        decision = "FAIL"
    else:
        decision = "PASS" if all(conditions.values()) else "FAIL"
    return {"conditions": conditions, "decision": f"{GATE_NAME} = {decision}", "verdict": decision,
            "failed": [name for name, value in conditions.items() if not value],
            "concentration_reference": conc["query_ticker"]}


def prepare_context(inputs: Inputs, rules: C4Rules, log, *, rebuild: bool) -> tuple:
    if not rebuild and context_cache_path("event_tables.npz").exists():
        tables, periodic = load_event_tables(inputs.cik_names)
        log("event tables loaded from cache")
    else:
        tables, periodic = build_event_tables(inputs, rules, log)
        save_event_tables(tables, periodic)
        log("event tables built and cached")
    quality_cache = context_cache_path("quality_tables.npz")
    if not rebuild and quality_cache.exists():
        data = np.load(quality_cache)
        tables.quality_status[:] = data["quality_status"]
        tables.revenue_yoy[:] = data["revenue_yoy"]
        log("quality tables loaded from cache")
    else:
        c4_context.fill_quality(tables, stores.StoreRoots(), periodic,
                                session_count=len(inputs.sessions), log=log)
        np.savez_compressed(quality_cache, quality_status=tables.quality_status,
                            revenue_yoy=tables.revenue_yoy)
        log("quality tables built and cached")
    return tables, periodic


def execute(out_root: Path = RUNS, *, rebuild_context: bool = False,
            stop_after_gate: bool = False, log=print) -> dict:
    started = time.perf_counter()
    rules = load_rules()
    inputs = load_inputs(log)
    tables, periodic = prepare_context(inputs, rules, log, rebuild=rebuild_context)
    event = c4_context.project(tables, inputs.cik_code, inputs.sessions)
    log("context projected")
    raw = c4_features.build_raw(inputs.panel, inputs.vw, inputs.c_features, event)
    log("raw coordinates built")

    index = np.arange(inputs.panel.shape[0])[:, None]
    window_mask = inputs.base & (index >= rules.first_query_idx) & (index <= rules.last_query_idx)
    vw_ok = np.isfinite(inputs.vw) & (inputs.vw > 0)
    vw_share = float(vw_ok[window_mask].mean())
    include_vwap = vw_share >= rules.vwap_coverage_min
    log(f"vw coverage {vw_share:.5f} -> vwap block {'IN' if include_vwap else 'OUT'} of F3")

    normalized = c4_features.normalize(raw, inputs.base, rules.library_start_idx,
                                       include_vwap=include_vwap, log=log)
    digest_one = c4_audit.array_digest([normalized[name] for name in sorted(normalized)])
    again = c4_features.normalize(raw, inputs.base, rules.library_start_idx,
                                  include_vwap=include_vwap)
    digest_two = c4_audit.array_digest([again[name] for name in sorted(again)])
    del again
    log(f"normalisation digest {digest_one[:16]} repeat_identical={digest_one == digest_two}")

    populations = {h: inputs.base & ~inputs.ca_excluded & inputs.labels.valid(h) for h in rules.horizons}
    excess = {h: labels4.build(inputs.labels, inputs.c_features.values, populations[h], h,
                               rules.min_cell_members) for h in rules.horizons}
    variant = next(v for v in inputs.c_rules.variants if v.name == "C-M0")
    c_m0 = inputs.c_features.candidates(variant)
    rows = build_rows(inputs, rules, raw, c_m0, include_vwap)
    log(f"library rows={rows.library_i.size} query rows={rows.query_i.size}")

    e0_reproduction = reproduce_c_e0(event, inputs)
    log(f"c-e0 cohort reproduction: {e0_reproduction}")
    recomputed = recompute_c_m0(inputs)
    frozen = frozen_c_m0()
    candidate_match = recomputed == frozen

    library_outcomes = {
        "excess_5": excess[5].excess[rows.library_i, rows.library_j],
        "excess_10": excess[10].excess[rows.library_i, rows.library_j],
        "mfe_5": inputs.labels.mfe[5][rows.library_i, rows.library_j],
        "mae_5": inputs.labels.mae[5][rows.library_i, rows.library_j],
        "mfe_10": inputs.labels.mfe[10][rows.library_i, rows.library_j],
        "mae_10": inputs.labels.mae[10][rows.library_i, rows.library_j],
    }
    query_outcomes = {
        "excess_5": excess[5].excess[rows.query_i, rows.query_j],
        "excess_10": excess[10].excess[rows.query_i, rows.query_j],
        "mfe_5": inputs.labels.mfe[5][rows.query_i, rows.query_j],
        "mae_5": inputs.labels.mae[5][rows.query_i, rows.query_j],
        "mfe_10": inputs.labels.mfe[10][rows.query_i, rows.query_j],
        "mae_10": inputs.labels.mae[10][rows.query_i, rows.query_j],
    }
    cell_matched_share = float(excess[5].cell_matched[rows.query_i, rows.query_j].mean())

    matrices: dict[str, np.ndarray] = {}
    for family in ("F0", "F1", "F2", "F3"):
        names = [name for name in feature_names(rules, family)
                 if include_vwap or name not in c4_features.VWAP_FEATURES]
        matrices[f"{family}_library"] = c4_features.matrix(normalized, names, rows.library_i,
                                                           rows.library_j)
        matrices[f"{family}_query"] = c4_features.matrix(normalized, names, rows.query_i,
                                                         rows.query_j)
    library_digest = c4_audit.array_digest([matrices["F3_library"], rows.library_i, rows.library_j])
    log(f"family matrices built F3 dims={matrices['F3_library'].shape[1]}")

    pit = pit_audit(inputs, rules, tables, raw, normalized, include_vwap, log)
    del normalized

    coverage = {
        "library_rows": int(rows.library_i.size),
        "library_unique_tickers": int(np.unique(rows.library_j).size),
        "library_unique_dates": int(np.unique(rows.library_i).size),
        "query_rows": int(rows.query_i.size),
        "query_unique_tickers": int(np.unique(rows.query_j).size),
        "query_unique_dates": int(np.unique(rows.query_i).size),
        "event_context_available_rows": int((~event.unknown[rows.library_i, rows.library_j]).sum()),
        "quality_context_available_rows": int(np.isfinite(
            event.revenue_yoy[rows.library_i, rows.library_j]).sum()),
        "periodic_rows": int(np.nan_to_num(
            event.periodic_flag[rows.library_i, rows.library_j]).sum()),
        "vw_coverage_share": vw_share,
        "vwap_block_included": include_vwap,
        "cell_matched_share_on_queries": cell_matched_share,
        "ciks_covered": int(tables.covered.sum()),
        "xbrl_documents": len(stores.stored_xbrl_ciks(stores.StoreRoots())),
        "xbrl_ciks_with_periodic_events": len(periodic),
    }

    results: dict[str, SearchResult] = {}
    audit_log = c4_audit.AuditLog()
    for family in ("F0", "F1", "F2", "F3"):
        result, family_audit = run_family(family, matrices, rows, inputs, rules, library_outcomes,
                                          log, keep_neighbors=(family == rules.primary_family))
        results[family] = result
        for code, amount in family_audit.counts.items():
            audit_log.add(code, amount)
        del matrices[f"{family}_library"]
    complete_share = float(results[rules.primary_family].complete.mean())
    coverage["query_share_with_full_top_k"] = complete_share
    gate = rules.coverage_gate
    coverage["pass"] = bool(
        coverage["library_rows"] >= gate["min_f3_usable_library_rows"]
        and coverage["library_unique_tickers"] >= gate["min_f3_usable_unique_tickers"]
        and coverage["library_unique_dates"] >= gate["min_f3_usable_unique_dates"]
        and complete_share >= gate["min_query_share_with_full_top_k"])
    log(f"coverage pass={coverage['pass']} complete_share={complete_share:.4f}")

    audit_log.add("E5_normalization_pit", pit["normalization_mismatches"] + pit["raw_mismatches"])
    audit_log.note("E7_determinism", bool(digest_one == digest_two))
    audit_log.note("E8_candidate_match", bool(candidate_match))
    audit_log.note("E9_c_e0_cohort_reproduction", e0_reproduction)
    audit_log.note("normalisation_digest", digest_one)
    audit_log.note("library_digest", library_digest)
    audit_log.note("pit", pit)
    audit_log.note("c_m0_recomputed", len(recomputed))
    audit_log.note("c_m0_frozen", len(frozen))
    engineering = c4_audit.gate_report(audit_log)
    log(f"engineering gate pass={engineering['pass']} {engineering}")

    identity = run_identity(IDENTITY_NAMESPACE, RUN_PREFIX, [
        f"strategy_id={STRATEGY_ID}", f"rules_checksum={rules.checksum}",
        f"c_m_run={rules.c_m_run_id}",
        f"code_digest={code_digest(package_files(Path(__file__).resolve().parent), root=REPO_ROOT)}",
        f"sessions={inputs.sessions[0].isoformat()}..{inputs.sessions[-1].isoformat()}:{len(inputs.sessions)}",
        f"library_digest={library_digest}", f"normalisation_digest={digest_one}",
        f"store_digests={json.dumps(stores.digests(stores.StoreRoots()), sort_keys=True)}",
    ])
    out = out_root / identity.run_id
    out.mkdir(parents=True, exist_ok=True)

    summary: dict = {
        "run_id": identity.run_id, "run_identity": identity.digest,
        "identity_lines": list(identity.lines), "strategy_id": STRATEGY_ID,
        "rules_checksum": rules.checksum, "coverage": coverage, "engineering_gate": engineering,
        "windows": {
            "library": [inputs.sessions[rules.library_start_idx].isoformat(),
                        inputs.sessions[rules.last_query_idx].isoformat()],
            "query": [inputs.sessions[rules.first_query_idx].isoformat(),
                      inputs.sessions[rules.last_query_idx].isoformat()],
        },
        "store_digests": stores.digests(stores.StoreRoots()),
        "provenance": source_provenance(REPO_ROOT, [Path(__file__).resolve().parent]),
    }
    if stop_after_gate:
        summary["decision"] = "SMOKE_STOP_BEFORE_RETURNS"
        summary["verdict"] = "SMOKE"
        dump_json(out / "summary.json", summary)
        log("smoke stop: the engineering gate ran, no return was read")
        return summary
    if not engineering["pass"] or not coverage["pass"]:
        verdict = "INCONCLUSIVE" if not coverage["pass"] else "FAIL"
        summary["decision"] = f"{GATE_NAME} = {verdict}"
        summary["verdict"] = verdict
        summary["note"] = "return analysis was not run: the pre-result gate did not pass"
        dump_json(out / "summary.json", summary)
        log(f"stopped before returns: {summary['decision']}")
        return summary

    statistics = evaluate_families(results, rows, inputs, rules, query_outcomes, raw, log)
    summary["statistics"] = statistics
    summary.update(decide(statistics, engineering, coverage, rules))
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    dump_json(out / "summary.json", summary)

    frame = pd.DataFrame({
        "signal_date": [inputs.sessions[i].isoformat() for i in rows.query_i],
        "ticker": [inputs.panel.tickers[j] for j in rows.query_j],
        "excess_5": query_outcomes["excess_5"], "excess_10": query_outcomes["excess_10"],
        "mfe_5": query_outcomes["mfe_5"], "mae_5": query_outcomes["mae_5"],
    })
    for family, result in results.items():
        frame[f"{family}_median_excess_5"] = result.forecast["median_excess_5"]
        frame[f"{family}_complete"] = result.complete
    frame.to_parquet(out / "query_forecasts.parquet", index=False)
    dump_json(out / "rules.json", rules.raw)
    log(f"decision={summary['decision']} out={out} elapsed={summary['elapsed_seconds']}s")
    return summary
