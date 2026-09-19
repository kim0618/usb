"""C-E0 run: frozen C-M0 candidates -> PIT event split -> pre-outcome checks -> GATE-CE0.

Order is the declaration's `execution_order_after_snapshot_id`. Two hard stops protect it: the
C-M0 candidate rows must reproduce the frozen baseline table row for row before any event is
joined, and the pre-outcome checks P1..P8 are decided (and can end the run as INCONCLUSIVE)
before a single forward return is read.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from app.backtest.engine.identity import code_digest, package_files, run_identity, source_provenance
from app.backtest.strategy_c_e0 import audit, cohorts, sec_store, stats
from app.backtest.strategy_c_e0.cik_map import load_snapshot_maps, map_candidate
from app.backtest.strategy_c_e0.events import build_cik_events, read_cik_rows
from app.backtest.strategy_c_e0.pit import build_grid, parse_acceptance
from app.backtest.strategy_c_e0.rules import E0Declaration
from app.backtest.strategy_c_e0.taxonomy import MATERIAL_CLASSES, Taxonomy
from app.backtest.strategy_c_selection import evaluate
from app.backtest.strategy_c_selection.features import compute
from app.backtest.strategy_c_selection.labels import compute_labels
from app.backtest.strategy_c_selection.panel import load_panel
from app.backtest.strategy_c_selection.run import (RunInputs, candidate_table, dump_json, raw_digest,
                                                   table_digest, usable_sessions)
from app.backtest.strategy_c_selection.rules import REPO_ROOT, SelectionRules

IDENTITY_NAMESPACE = "strategy-c-e0-run"
RUN_ID_PREFIX = "ce01"
RESULT_SCHEMA = "strategy-c-e0-result-v1"
CODE_PACKAGE = Path(__file__).resolve().parent
PRIMARY_START = 251
FORWARD = 10
HORIZONS = (1, 3, 5, 10)
MAIN_ROWS = (cohorts.M_ONLY, cohorts.EM, cohorts.EM_NEGATIVE_RISK)


class BaselineMismatch(RuntimeError):
    """The C-M0 candidate rows do not reproduce the frozen baseline; the run must not continue."""


@dataclass(frozen=True)
class E0Inputs:
    raw_root: Path
    event_root: Path
    baseline: Path
    sessions: tuple[date, ...]
    snapshot_dates: tuple[date, ...]
    split_range: tuple[date, date]


def _reproduce_baseline(inputs: E0Inputs, rules: SelectionRules, panel, features, labels,
                        primary: Sequence[int], variant: str, log) -> pd.DataFrame:
    table = candidate_table(panel, features, labels, rules, primary, list(rules.variants), "primary")
    digest = table_digest(table)
    baseline_summary = json.loads((inputs.baseline / "summary.json").read_text(encoding="utf-8"))
    declared = baseline_summary["digests"]["candidates_primary"]
    if digest != declared:
        raise BaselineMismatch(f"candidate table digest {digest} != baseline {declared}")
    mine = table[table["variant"] == variant].reset_index(drop=True)
    theirs = pd.read_parquet(inputs.baseline / "candidates_primary.parquet")
    theirs = theirs[theirs["variant"] == variant].reset_index(drop=True)
    if list(mine["signal_date"]) != list(theirs["signal_date"]) or list(mine["ticker"]) != list(theirs["ticker"]):
        raise BaselineMismatch(f"{variant} rows differ from the baseline table")
    log(f"baseline reproduced digest={digest[:12]} {variant}_rows={len(mine)}")
    return mine


def _decide_zone(event_root: Path, declaration: E0Declaration, log) -> dict:
    path = event_root / "timezone_audit_samples.json"
    samples = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    result = audit.timezone_audit(samples, parse=parse_acceptance)
    log(f"timezone audit samples={result['samples']} agreement={result['agreement']} "
        f"zone={result['zone']} pass={result['pass']}")
    return result


def _event_views(event_root: Path, ciks: Sequence[str], taxonomy: Taxonomy, grid, zone: str, log):
    manifest_path = event_root / "store_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    per_cik = {row["cik"]: row for row in manifest.get("per_cik", ())}
    views, coverage, forms = {}, {}, {}
    for n, cik in enumerate(ciks, start=1):
        entry = per_cik.get(cik)
        rows = read_cik_rows(event_root, cik)
        coverage[cik] = cohorts.Coverage(
            covered=bool(entry and entry.get("covered")),
            status=str(entry.get("status")) if entry else "NOT_IN_MANIFEST",
            earliest_filing_date=date.fromisoformat(entry["earliest_filing_date"])
            if entry and entry.get("earliest_filing_date") else None,
            latest_filing_date=date.fromisoformat(entry["latest_filing_date"])
            if entry and entry.get("latest_filing_date") else None)
        if not rows:
            continue
        views[cik] = build_cik_events(cik, rows, taxonomy, grid, acceptance_zone=zone)
        for form, count in views[cik].form_counts.items():
            forms[form] = forms.get(form, 0) + count
        if n % 500 == 0:
            log(f"event view {n}/{len(ciks)}")
    return views, coverage, forms


def _cohort_frame(frames: dict[int, pd.DataFrame], status: pd.DataFrame, k: int) -> pd.DataFrame:
    frame = frames[k].merge(status, on=["date_idx", "ticker"], how="left", validate="one_to_one")
    return frame[frame["matched"]]


def _main_table(frames: dict[int, pd.DataFrame], status: pd.DataFrame, dates: Sequence[int],
                draws: np.ndarray) -> dict:
    out: dict = {}
    for name in MAIN_ROWS:
        row: dict = {}
        for k in HORIZONS:
            frame = _cohort_frame(frames, status, k)
            cohort = frame[frame["status"] == name]
            row[f"close_{k}"] = stats.excess(cohort, dates, f"close_{k}", draws if k in (5, 10) else None)
            row[f"mfe_{k}"] = stats.excess(cohort, dates, f"mfe_{k}", None)
            row[f"mae_{k}"] = stats.excess(cohort, dates, f"mae_{k}", None)
            if k == 5:
                row["n"] = int(len(cohort))
                row["unique_tickers"] = int(cohort["ticker"].nunique())
                row["win_rate"] = float((cohort["close_5"] > 0).mean()) if len(cohort) else float("nan")
            if k == 10 and len(cohort):
                for hit in ("mfe10_ge_10", "mfe10_ge_15"):
                    row[hit] = stats.excess(cohort, dates, hit, None)
                row["time_to_mfe_10_mean"] = float(cohort["time_to_mfe_10"].mean())
                # Not V2A's giveback: simply how much of the 10D peak the close gave back.
                row["mfe10_minus_close10_mean"] = float((cohort["mfe_10"] - cohort["close_10"]).mean())
        out[name] = row
    matched_all = _cohort_frame(frames, status, 5)
    out["MATCHED_CONTROL"] = {
        "n": int(len(matched_all)),
        "unique_tickers": int(matched_all["ticker"].nunique()),
        **{f"control_mean_close_{k}": float(_cohort_frame(frames, status, k)[f"base_close_{k}"].mean())
           for k in HORIZONS},
        **{f"control_mean_mfe_{k}": float(_cohort_frame(frames, status, k)[f"base_mfe_{k}"].mean())
           for k in (5, 10)},
        **{f"control_mean_mae_{k}": float(_cohort_frame(frames, status, k)[f"base_mae_{k}"].mean())
           for k in (5, 10)}}
    return out


def _incremental(frames: dict[int, pd.DataFrame], status: pd.DataFrame, dates: Sequence[int],
                 draws: np.ndarray) -> dict:
    out = {}
    for column, k in (("close", 5), ("close", 10), ("mfe", 5), ("mfe", 10), ("mae", 5), ("mae", 10)):
        frame = _cohort_frame(frames, status, k)
        em = frame[frame["status"] == cohorts.EM]
        m_only = frame[frame["status"] == cohorts.M_ONLY]
        out[f"{column}_{k}"] = stats.difference(em, m_only, dates, f"{column}_{k}", draws)
    return out


def _time_blocks(frames: dict[int, pd.DataFrame], status: pd.DataFrame, blocks: Sequence[Sequence[int]]) -> list:
    frame = _cohort_frame(frames, status, 5)
    out = []
    for block in blocks:
        part = frame[frame["date_idx"].isin(list(block))]
        em = part[part["status"] == cohorts.EM]
        m_only = part[part["status"] == cohorts.M_ONLY]
        em_excess = stats.excess(em, block, "close_5", None)["point"]
        m_excess = stats.excess(m_only, block, "close_5", None)["point"]
        out.append({"dates": [int(block[0]), int(block[-1])], "n_em": int(len(em)),
                    "n_m_only": int(len(m_only)), "em_excess": em_excess, "m_only_excess": m_excess,
                    "difference": em_excess - m_excess})
    return out


def _robustness(frames: dict[int, pd.DataFrame], status: pd.DataFrame, dates: Sequence[int]) -> dict:
    """Condition 8: EM must survive dropping any 10%+ event class and its top 5 tickers."""
    frame = _cohort_frame(frames, status, 5)
    em = frame[frame["status"] == cohorts.EM]
    m_only = frame[frame["status"] == cohorts.M_ONLY]
    base_em = stats.excess(em, dates, "close_5", None)["point"]
    base_diff = base_em - stats.excess(m_only, dates, "close_5", None)["point"]
    out: dict = {"em_excess": base_em, "difference": base_diff, "leave_one_class_out": {},
                 "class_counts": {}}
    for event_class in MATERIAL_CLASSES:
        has = em[em["event_types"].apply(lambda types, c=event_class: c in (types or []))]
        share = float(len(has) / len(em)) if len(em) else 0.0
        out["class_counts"][event_class] = {"n": int(len(has)), "share": share}
        if share >= 0.10:
            kept = em.drop(has.index)
            point = stats.excess(kept, dates, "close_5", None)["point"]
            out["leave_one_class_out"][event_class] = {
                "n_dropped": int(len(has)), "em_excess": point,
                "difference": point - stats.excess(m_only, dates, "close_5", None)["point"]}
    top5 = list(em["ticker"].value_counts().head(5).index)
    kept = em[~em["ticker"].isin(top5)]
    point = stats.excess(kept, dates, "close_5", None)["point"]
    out["leave_top5_tickers_out"] = {
        "tickers": [str(t) for t in top5], "n_dropped": int(len(em) - len(kept)), "em_excess": point,
        "difference": point - stats.excess(m_only, dates, "close_5", None)["point"]}
    return out


def _secondary_windows(frames: dict[int, pd.DataFrame], status: pd.DataFrame, dates: Sequence[int],
                       draws: np.ndarray) -> dict:
    out = {}
    for window in ("status_W_D5", "status_W_D1", "status_W_D0"):
        alt = status.rename(columns={"status": "status_primary", window: "status"})
        out[window] = {"main": {name: {"close_5": stats.excess(
            _cohort_frame(frames, alt, 5).pipe(lambda f, n=name: f[f["status"] == n]), dates, "close_5", None)}
            for name in MAIN_ROWS},
            "incremental_close_5": stats.difference(
                _cohort_frame(frames, alt, 5).pipe(lambda f: f[f["status"] == cohorts.EM]),
                _cohort_frame(frames, alt, 5).pipe(lambda f: f[f["status"] == cohorts.M_ONLY]),
                dates, "close_5", draws),
            "note": "DESCRIPTIVE"}
    return out


def _gate(main: dict, incremental: dict, blocks: list, robustness: dict, concentration: dict,
          pre: dict, pit_violations: int, gate_rules: dict) -> dict:
    em_close5 = main[cohorts.EM]["close_5"]
    diff_close5 = incremental["close_5"]
    mae_diff = incremental["mae_5"]["point"]
    conditions = {
        "1_em_5d_excess_point": em_close5["point"] > 0,
        "2_h1_ci_low": em_close5.get("ci9500", [float("nan")])[0] > 0,
        "3_em_minus_m_only_5d_point": diff_close5["point"] > 0,
        "4_h2_ci_low": diff_close5.get("ci9500", [float("nan")])[0] > 0,
        "5_mae5_not_worse": mae_diff >= -0.02,
        "6_time_blocks": (sum(b["difference"] > 0 for b in blocks) >= 3
                          and sum(b["em_excess"] > 0 for b in blocks) >= 3),
        "7_sample": all(pre["checks"][key]["pass"] for key in ("P4_em_sample", "P5_em_unique_tickers",
                                                               "P6_m_only_sample", "P7_block_sample")),
        "8_concentration": (concentration["single_ticker_share"] <= 0.05
                            and concentration["top5_ticker_share"] <= 0.15
                            and concentration["single_date_share"] <= 0.05
                            and concentration["top10_date_share"] <= 0.25
                            and all(v["em_excess"] > 0 and v["difference"] > 0
                                    for v in robustness["leave_one_class_out"].values())
                            and robustness["leave_top5_tickers_out"]["em_excess"] > 0
                            and robustness["leave_top5_tickers_out"]["difference"] > 0),
        "9_pit_violations": pit_violations == 0,
    }
    decision = "PASS" if all(conditions.values()) else "FAIL"
    dominant = max(robustness["class_counts"].items(), key=lambda kv: kv[1]["share"], default=(None, {}))
    return {"conditions": {k: bool(v) for k, v in conditions.items()}, "decision": decision,
            "H1": "PASS" if conditions["1_em_5d_excess_point"] and conditions["2_h1_ci_low"] else "FAIL",
            "H2": "PASS" if conditions["3_em_minus_m_only_5d_point"] and conditions["4_h2_ci_low"] else "FAIL",
            "scope": dominant[0] if dominant[1].get("share", 0) > 0.60 else "EVENTS_AS_DECLARED",
            "declared": gate_rules}


def execute(inputs: E0Inputs, rules: SelectionRules, declaration: E0Declaration, out_root: Path,
            *, snapshot_id: str | None = None, log=print) -> dict:
    started = time.perf_counter()
    sessions, dropped = usable_sessions(inputs.raw_root, inputs.sessions)
    effective = RunInputs(inputs.raw_root, sessions, inputs.snapshot_dates, inputs.split_range)
    digest = raw_digest(effective)
    if digest != declaration.baseline_raw_digest:
        raise BaselineMismatch(f"raw digest {digest} != declared baseline {declaration.baseline_raw_digest}")
    store_digest = sec_store.store_digest(inputs.event_root) if inputs.event_root.exists() else "EMPTY"
    identity = run_identity(IDENTITY_NAMESPACE, RUN_ID_PREFIX, [
        f"e0_rules_checksum={declaration.rules_checksum}",
        f"taxonomy_checksum={declaration.taxonomy_checksum}",
        f"baseline_run_id={declaration.baseline_run_id}",
        f"snapshot={snapshot_id or 'C_RAW_FREEZE_V1'}",
        f"event_store_digest={store_digest}",
        f"code_digest={code_digest(package_files(CODE_PACKAGE), root=REPO_ROOT)}",
        f"bootstrap={stats.BLOCK_LENGTH}:{stats.REPLICATES}:{stats.SEED}",
    ])
    log(f"run_id={identity.run_id} sessions={len(sessions)} dropped={dropped} store={store_digest[:12]}")

    panel = load_panel(inputs.raw_root, sessions, inputs.snapshot_dates, rules.allowed_exchanges,
                       inputs.split_range)
    features = compute(panel, rules)
    labels = compute_labels(panel, rules.horizons, rules.ca_ratio)
    primary = list(range(PRIMARY_START, len(sessions) - FORWARD))
    variant_name = declaration.base_variant
    variant = next(v for v in rules.variants if v.name == variant_name)
    baseline_rows = _reproduce_baseline(inputs, rules, panel, features, labels, primary, variant_name, log)

    grid = build_grid(list(sessions))
    taxonomy = Taxonomy(declaration.taxonomy, declaration.addendum)
    zone_result = _decide_zone(inputs.event_root, declaration, log)
    zone = zone_result["zone"] or "ET"

    maps = load_snapshot_maps(inputs.raw_root)
    index = {day: i for i, day in enumerate(sessions)}
    pairs = [(index[date.fromisoformat(d)], t) for d, t in zip(baseline_rows["signal_date"],
                                                               baseline_rows["ticker"])]
    mappings = {(i, t): map_candidate(maps, t, sessions[i]) for i, t in pairs}
    ciks = sorted({m.cik for m in mappings.values() if m.cik})
    views, coverage, form_counts = _event_views(inputs.event_root, ciks, taxonomy, grid, zone, log)

    status_rows = []
    row_inputs = []
    for i, ticker in pairs:
        mapping = mappings[(i, ticker)]
        view = views.get(mapping.cik) if mapping.cik else None
        cover = coverage.get(mapping.cik) if mapping.cik else None
        assigned = cohorts.assign(grid, i, mapping, view, cover)
        status_rows.append({"date_idx": i, "ticker": ticker, **assigned})
        row_inputs.append(audit.RowInput(i, ticker, mapping, view, cover, assigned["status"]))
    status = pd.DataFrame(status_rows)
    counts = cohorts.status_counts(status_rows)
    unknown = cohorts.unknown_share(status_rows)
    log(f"statuses={counts} unknown_share={unknown:.4f}")

    pit = audit.pit_audit(grid, row_inputs)
    # As in V1: a positive control that was never demonstrated counts as a violation, so an audit
    # that could not run cannot be mistaken for an audit that passed.
    pit_violations = int(pit["violations"]) + (0 if pit["positive_control_detected"] else 1)
    log(f"pit violations={pit_violations} raw={pit['violations']} "
        f"positive_control={pit['positive_control_detected']}")

    rows = evaluate.build_rows(variant, features, labels, primary, rules, panel.tickers)
    frames = {k: evaluate.attach_matched_base(rows, rules, k) for k in HORIZONS}
    matched5 = frames[5][["date_idx", "ticker", "matched"]].rename(columns={"matched": "matched_5"})
    pre_rows = status.merge(matched5, on=["date_idx", "ticker"], how="left")
    pre_rows["matched_5"] = pre_rows["matched_5"].fillna(False).astype(bool)
    blocks = stats.time_blocks(primary)
    pre = audit.pre_outcome_checks(pre_rows, timezone_audit_passed=bool(zone_result["pass"]),
                                   pit_violations=pit_violations, unknown_share=unknown,
                                   blocks=blocks, expected_dates=240, present_dates=len(primary),
                                   declaration=declaration.rules)
    log(f"pre_outcome all_pass={pre['all_pass']} "
        + " ".join(f"{k}={v['pass']}" for k, v in pre["checks"].items()))

    summary: dict = {
        "run_id": identity.run_id, "run_identity": identity.digest, "identity_lines": list(identity.lines),
        "result_schema": RESULT_SCHEMA, "e0_rules_checksum": declaration.rules_checksum,
        "taxonomy_checksum": declaration.taxonomy_checksum,
        "addendum_checksum": declaration.addendum_checksum,
        "baseline": {"run_id": declaration.baseline_run_id, "raw_digest": digest,
                     "variant": variant_name, "rows": int(len(baseline_rows))},
        "event_store": {"digest": store_digest, "acceptance_zone": zone, "ciks": len(ciks),
                        "form_counts": dict(sorted(form_counts.items(), key=lambda kv: -kv[1])[:80])},
        "alias_audit": {"unknown_forms_in_store": sorted(set(form_counts) - set(taxonomy.known_forms))[:80]},
        "timezone_audit": zone_result,
        "status_counts": counts, "unknown_share": unknown,
        "pit_audit": {**pit, "violations_with_positive_control": pit_violations},
        "pre_outcome_checks": pre,
        "windows": {"primary": [sessions[primary[0]].isoformat(), sessions[primary[-1]].isoformat(),
                                len(primary)],
                    "time_blocks": [[sessions[b[0]].isoformat(), sessions[b[-1]].isoformat()] for b in blocks]},
        "provenance": source_provenance(REPO_ROOT, [CODE_PACKAGE]),
    }

    if not pre["all_pass"]:
        summary["decision"] = "GATE-CE0 = INCONCLUSIVE"
        summary["outcomes_computed"] = False
        return _write(out_root, identity.run_id, summary, status, declaration, started, log)

    draws = stats.block_indices(len(primary))
    main = _main_table(frames, status, primary, draws)
    incremental = _incremental(frames, status, primary, draws)
    block_table = _time_blocks(frames, status, blocks)
    robustness = _robustness(frames, status, primary)
    em5 = _cohort_frame(frames, status, 5)
    concentration = stats.concentration(em5[em5["status"] == cohorts.EM])
    gate = _gate(main, incremental, block_table, robustness, concentration, pre,
                 pit_violations, declaration.rules["gate"]["conditions"])
    summary.update({"outcomes_computed": True, "main_table": main, "incremental": incremental,
                    "time_blocks": block_table, "robustness": robustness,
                    "concentration": concentration,
                    "secondary_windows": _secondary_windows(frames, status, primary, draws),
                    "cost_reference_round_trip": stats.COST_ROUND_TRIP,
                    "gate": gate, "decision": f"GATE-CE0 = {gate['decision']}"})
    return _write(out_root, identity.run_id, summary, status, declaration, started, log)


def _write(out_root: Path, run_id: str, summary: dict, status: pd.DataFrame,
           declaration: E0Declaration, started: float, log) -> dict:
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    out = out_root / run_id
    out.mkdir(parents=True, exist_ok=True)
    status.to_parquet(out / "candidate_status.parquet", index=False)
    (out / "summary.json").write_bytes(dump_json(summary))
    (out / "rules.json").write_bytes(
        (REPO_ROOT / "docs/backtest/strategy_c/v2/c_e0_rules_v1.json").read_bytes())
    (out / "taxonomy.json").write_bytes(
        (REPO_ROOT / "docs/backtest/strategy_c/v2/c_e0_event_taxonomy_v1.json").read_bytes())
    log(f"{summary.get('decision', 'GATE-CE0 = NOT_REACHED')} out={out} "
        f"elapsed_s={summary['elapsed_seconds']}")
    return summary
