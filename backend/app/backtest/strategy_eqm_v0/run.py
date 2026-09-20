"""EQM-V0 run: frozen C-M0 candidates -> C-E0 cohorts -> event magnitude -> GATE-EQM-V0.

The order is the declaration's. Three hard stops protect it: the C-M0 candidate rows must
reproduce the frozen baseline table row for row, the frozen C-E0 status table must cover exactly
those rows, and the pre-outcome checks P1..P8 are decided - and can end the run as INCONCLUSIVE -
before a single forward return is read. Strategy C is reproduced, never re-decided.
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
from app.backtest.strategy_c_e0 import audit as c_e0_audit
from app.backtest.strategy_c_e0 import sec_store, stats
from app.backtest.strategy_c_e0.events import build_cik_events, read_cik_rows
from app.backtest.strategy_c_e0.pit import build_grid, parse_acceptance
from app.backtest.strategy_c_e0.rules import load_declaration as load_c_e0_declaration
from app.backtest.strategy_c_e0.taxonomy import Taxonomy
from app.backtest.strategy_c_selection import evaluate
from app.backtest.strategy_c_selection.features import compute
from app.backtest.strategy_c_selection.labels import compute_labels
from app.backtest.strategy_c_selection.panel import load_panel
from app.backtest.strategy_c_selection.run import (RunInputs, candidate_table, dump_json, raw_digest,
                                                   table_digest, usable_sessions)
from app.backtest.strategy_c_selection.rules import REPO_ROOT, SelectionRules
from app.backtest.strategy_eqm_v0 import audit as eqm_audit
from app.backtest.strategy_eqm_v0 import cohorts, quality, rows as eqm_rows, xbrl_store
from app.backtest.strategy_eqm_v0.rules import EqmDeclaration

IDENTITY_NAMESPACE = "strategy-eqm-v0-run"
RUN_ID_PREFIX = "eqm0"
RESULT_SCHEMA = "strategy-eqm-v0-result-v1"
CODE_PACKAGE = Path(__file__).resolve().parent
PRIMARY_START = 251
FORWARD = 10
HORIZONS = (1, 3, 5, 10)
PRIMARY_K = 5
COHORT_FLAGS = {
    cohorts.M_ONLY: "is_m_only",
    cohorts.EM: "is_em",
    cohorts.EM_REST: "is_em_rest",
    cohorts.EQ1: "is_eq1",
    cohorts.EQ2: "is_eq2",
    cohorts.EQ3: "is_eq3",
    cohorts.EQ_MATERIAL_RISK: "is_material_risk",
}


class BaselineMismatch(RuntimeError):
    """The reproduced rows do not match a frozen artefact; the run must not continue."""


class StoreChanged(RuntimeError):
    """The XBRL store no longer hashes to the digest recorded in the declaration."""


@dataclass(frozen=True)
class EqmInputs:
    raw_root: Path
    event_root: Path
    facts_root: Path
    baseline: Path
    c_e0_run: Path
    features: Path
    sessions: tuple[date, ...]
    snapshot_dates: tuple[date, ...]
    split_range: tuple[date, date]


def execute(inputs: EqmInputs, rules: SelectionRules, declaration: EqmDeclaration,
            out_root: Path, *, snapshot_id: str | None = None, log=print) -> dict:
    started = time.perf_counter()
    c_e0 = load_c_e0_declaration()
    facts_digest = xbrl_store.store_digest(inputs.facts_root)
    if facts_digest != declaration.xbrl_store_digest:
        raise StoreChanged(f"xbrl store digest {facts_digest} != declared {declaration.xbrl_store_digest}")

    sessions, dropped = usable_sessions(inputs.raw_root, inputs.sessions)
    effective = RunInputs(inputs.raw_root, sessions, inputs.snapshot_dates, inputs.split_range)
    digest = raw_digest(effective)
    if digest != c_e0.baseline_raw_digest:
        raise BaselineMismatch(f"raw digest {digest} != C baseline {c_e0.baseline_raw_digest}")
    block, replicates, seed = declaration.bootstrap
    identity = run_identity(IDENTITY_NAMESPACE, RUN_ID_PREFIX, [
        f"eqm_rules_checksum={declaration.rules_checksum}",
        f"c_e0_run_id={declaration.c_e0_run_id}",
        f"baseline_run_id={declaration.baseline_run_id}",
        f"snapshot={snapshot_id or 'C_RAW_FREEZE_V1'}",
        f"xbrl_store_digest={facts_digest}",
        f"code_digest={code_digest(package_files(CODE_PACKAGE), root=REPO_ROOT)}",
        f"bootstrap={block}:{replicates}:{seed}",
    ])
    log(f"run_id={identity.run_id} sessions={len(sessions)} dropped={dropped} facts={facts_digest[:12]}")

    panel = load_panel(inputs.raw_root, sessions, inputs.snapshot_dates, rules.allowed_exchanges,
                       inputs.split_range)
    features = compute(panel, rules)
    labels = compute_labels(panel, rules.horizons, rules.ca_ratio)
    primary = list(range(PRIMARY_START, len(sessions) - FORWARD))
    variant = next(v for v in rules.variants if v.name == c_e0.base_variant)
    baseline_rows = _reproduce_baseline(inputs, rules, panel, features, labels, primary,
                                        c_e0.base_variant, log)

    status = pd.read_parquet(inputs.c_e0_run / "candidate_status.parquet")
    _check_status_covers_baseline(status, baseline_rows, sessions, log)
    quality_rows = pd.read_parquet(inputs.features / "quality_rows.parquet")
    frame = cohorts.assign(status, quality_rows, material_cut=declaration.material_cut)
    frame["bucket"] = frame["revenue_growth_yoy"].apply(
        lambda g: cohorts.bucket_of(g, declaration.ladder))
    log(f"cohorts={dict(cohorts.counts(frame))}")

    grid = build_grid(list(sessions))
    zone_result = c_e0_audit.timezone_audit(
        json.loads((inputs.event_root / "timezone_audit_samples.json").read_text(encoding="utf-8")),
        parse=parse_acceptance)
    pit = _pit_audit(frame, inputs, grid, c_e0, zone_result["zone"] or "UTC", log)
    pit_violations = int(pit["violations"]) + (0 if pit["positive_control_pass"] else 1)
    log(f"pit violations={pit_violations} shift_unexplained={pit['shift_plus_one_unexplained']}")

    rows = evaluate.build_rows(variant, features, labels, primary, rules, panel.tickers)
    frames = {k: evaluate.attach_matched_base(rows, rules, k) for k in HORIZONS}
    matched = frames[PRIMARY_K][["date_idx", "ticker", "matched"]].rename(columns={"matched": "matched_5"})
    pre_rows = frame.merge(matched, on=["date_idx", "ticker"], how="left")
    pre_rows["matched_5"] = pre_rows["matched_5"].astype("object").where(
        pre_rows["matched_5"].notna(), False).astype(bool)
    blocks = stats.time_blocks(primary)
    pre = _pre_outcome_checks(pre_rows, quality_rows, declaration, blocks,
                              timezone_passed=bool(zone_result["pass"]),
                              pit_violations=pit_violations, present_dates=len(primary))
    log(f"pre_outcome all_pass={pre['all_pass']} "
        + " ".join(f"{k}={v['pass']}" for k, v in pre["checks"].items()))

    summary: dict = {
        "run_id": identity.run_id, "run_identity": identity.digest,
        "identity_lines": list(identity.lines), "result_schema": RESULT_SCHEMA,
        "eqm_rules_checksum": declaration.rules_checksum,
        "strategy_c_status": "CLOSED",
        "baseline": {"c_m_run_id": declaration.baseline_run_id, "c_e0_run_id": declaration.c_e0_run_id,
                     "raw_digest": digest, "variant": c_e0.base_variant,
                     "rows": int(len(baseline_rows))},
        "xbrl_store": {"digest": facts_digest, "root": str(inputs.facts_root)},
        "material_cut": declaration.material_cut,
        "cohort_counts": dict(cohorts.counts(frame)),
        "quality_status_counts": quality_rows["quality_status"].value_counts().to_dict(),
        "bucket_counts": frame.loc[frame["is_em"], "bucket"].value_counts().to_dict(),
        "timezone_audit": zone_result,
        "pit_audit": {**pit, "violations_with_positive_control": pit_violations},
        "pre_outcome_checks": pre,
        "windows": {"primary": [sessions[primary[0]].isoformat(), sessions[primary[-1]].isoformat(),
                                len(primary)],
                    "time_blocks": [[sessions[b[0]].isoformat(), sessions[b[-1]].isoformat()]
                                    for b in blocks]},
        "bootstrap": {"block": block, "replicates": replicates, "seed": seed,
                      "levels": list(declaration.levels)},
        "provenance": source_provenance(REPO_ROOT, [CODE_PACKAGE]),
    }

    if not pre["all_pass"]:
        summary["decision"] = "GATE-EQM-V0 = INCONCLUSIVE"
        summary["outcomes_computed"] = False
        return _write(out_root, identity.run_id, summary, frame, started, log)

    draws = stats.block_indices(len(primary), replicates=replicates, seed=seed, block=block)
    levels = declaration.levels
    main = _main_table(frames, frame, primary, draws, levels)
    increments = _increments(frames, frame, primary, draws, levels)
    ladder = _ladder(frames, frame, primary, declaration)
    block_table = _time_blocks(frames, frame, blocks)
    robustness = _robustness(frames, frame, primary, draws, levels)
    concentration = stats.concentration(_cohort(frames, frame, PRIMARY_K, "is_eq2"))
    gate = _gate(main, increments, block_table, robustness, concentration, pre, pit_violations,
                 declaration)
    summary.update({"outcomes_computed": True, "main_table": main, "increments": increments,
                    "ladder": ladder, "time_blocks": block_table, "robustness": robustness,
                    "concentration": concentration,
                    "cost_reference_round_trip": stats.COST_ROUND_TRIP,
                    "scope": declaration.rules["gate"]["scope_rule"],
                    "gate": gate, "decision": f"GATE-EQM-V0 = {gate['decision']}"})
    return _write(out_root, identity.run_id, summary, frame, started, log)


def _reproduce_baseline(inputs: EqmInputs, rules: SelectionRules, panel, features, labels,
                        primary: Sequence[int], variant: str, log) -> pd.DataFrame:
    table = candidate_table(panel, features, labels, rules, primary, list(rules.variants), "primary")
    digest = table_digest(table)
    declared = json.loads((inputs.baseline / "summary.json").read_text(encoding="utf-8"))
    if digest != declared["digests"]["candidates_primary"]:
        raise BaselineMismatch(f"candidate table digest {digest} != baseline "
                               f"{declared['digests']['candidates_primary']}")
    mine = table[table["variant"] == variant].reset_index(drop=True)
    theirs = pd.read_parquet(inputs.baseline / "candidates_primary.parquet")
    theirs = theirs[theirs["variant"] == variant].reset_index(drop=True)
    if list(mine["signal_date"]) != list(theirs["signal_date"]) or list(mine["ticker"]) != list(theirs["ticker"]):
        raise BaselineMismatch(f"{variant} rows differ from the baseline table")
    log(f"baseline reproduced digest={digest[:12]} {variant}_rows={len(mine)}")
    return mine


def _check_status_covers_baseline(status: pd.DataFrame, baseline_rows: pd.DataFrame,
                                  sessions: Sequence[date], log) -> None:
    """The frozen C-E0 statuses must describe exactly the reproduced candidate rows."""
    mine = {(str(d), str(t)) for d, t in zip(baseline_rows["signal_date"], baseline_rows["ticker"])}
    theirs = {(str(d), str(t)) for d, t in zip(status["signal_date"], status["ticker"])}
    if mine != theirs:
        raise BaselineMismatch(f"C-E0 status table covers {len(theirs)} rows, baseline has {len(mine)}; "
                               f"symmetric difference {len(mine ^ theirs)}")
    for _, row in status.iterrows():
        if sessions[int(row["date_idx"])].isoformat() != str(row["signal_date"]):
            raise BaselineMismatch(f"date_idx {row['date_idx']} does not match {row['signal_date']}")
    log(f"C-E0 status table reproduced rows={len(status)}")


def _pit_audit(frame: pd.DataFrame, inputs: EqmInputs, grid, c_e0_declaration, zone: str, log) -> dict:
    """Re-derive the placement and the fact provenance of every row that carries a feature."""
    taxonomy = Taxonomy(c_e0_declaration.taxonomy, c_e0_declaration.addendum)
    used = frame[frame["observable"] & frame["accession"].notna()]
    records: list[eqm_audit.UsedFact] = []
    for cik, group in used.groupby("cik", sort=True):
        cik = str(cik)
        submissions = read_cik_rows(inputs.event_root, cik)
        index = eqm_rows.accession_index(submissions)
        view = build_cik_events(cik, submissions, taxonomy, grid, acceptance_zone=zone)
        document = xbrl_store.read_facts(inputs.facts_root, cik)
        for _, row in group.iterrows():
            accession = str(row["accession"])
            filing = index.get(accession) or {}
            acceptance = str(filing.get("acceptanceDateTime") or "")
            if not acceptance:
                continue
            facts = quality.facts_of_accession(document or {}, accession)
            extracted = quality.extract(facts, accession)
            fact_accessions = (accession,) * len(facts) if extracted.status == quality.OK else ()
            records.append(eqm_audit.UsedFact(
                date_idx=int(row["date_idx"]), ticker=str(row["ticker"]), cik=cik,
                accession=accession, acceptance=parse_acceptance(acceptance, zone),
                fact_accessions=fact_accessions,
                event_sessions=tuple(e.session_index for e in view.events if e.event_class == "E3")))
    log(f"pit audit records={len(records)}")
    return eqm_audit.audit(records, grid)


def _pre_outcome_checks(rows: pd.DataFrame, quality_rows: pd.DataFrame, declaration: EqmDeclaration,
                        blocks: Sequence[Sequence[int]], *, timezone_passed: bool,
                        pit_violations: int, present_dates: int) -> dict:
    checks_declared = declaration.checks
    matched = rows[rows["matched_5"]]
    eq2 = matched[matched["is_eq2"]]
    periodic = quality_rows[(quality_rows["status"] == cohorts.EM)
                            & (quality_rows["quality_status"] != eqm_rows.NO_EVENT)]
    unresolved = float((periodic["quality_status"] != cohorts.OBSERVABLE).mean()) if len(periodic) else 1.0
    per_block = [int(eq2["date_idx"].isin(list(b)).sum()) for b in blocks]
    checks = {
        "P1_timezone_audit": {"pass": bool(timezone_passed), "value": timezone_passed},
        "P2_pit_violations": {"pass": pit_violations == 0, "value": pit_violations},
        "P3_unresolved_share": {"pass": unresolved <= float(checks_declared["P3_unresolved_share_max"]),
                                "value": unresolved,
                                "limit": float(checks_declared["P3_unresolved_share_max"])},
        "P4_eq2_sample": {"pass": len(eq2) >= int(checks_declared["P4_eq2_matched_min"]),
                          "value": int(len(eq2)), "limit": int(checks_declared["P4_eq2_matched_min"])},
        "P5_eq2_unique_tickers": {
            "pass": eq2["ticker"].nunique() >= int(checks_declared["P5_eq2_unique_tickers_min"]),
            "value": int(eq2["ticker"].nunique()),
            "limit": int(checks_declared["P5_eq2_unique_tickers_min"])},
        "P6_comparators": {
            "pass": (int(matched["is_em_rest"].sum()) >= int(checks_declared["P6_comparator_matched_min"])
                     and int(matched["is_m_only"].sum()) >= int(checks_declared["P6_comparator_matched_min"])),
            "value": [int(matched["is_em_rest"].sum()), int(matched["is_m_only"].sum())],
            "limit": int(checks_declared["P6_comparator_matched_min"])},
        "P7_block_sample": {"pass": all(n >= int(checks_declared["P7_block_eq2_min"]) for n in per_block),
                            "value": per_block, "limit": int(checks_declared["P7_block_eq2_min"])},
        "P8_primary_dates": {"pass": present_dates == int(checks_declared["P8_primary_dates"]),
                             "value": present_dates, "limit": int(checks_declared["P8_primary_dates"])},
    }
    return {"checks": checks, "all_pass": all(c["pass"] for c in checks.values())}


def _cohort(frames: dict[int, pd.DataFrame], flags: pd.DataFrame, k: int, flag: str) -> pd.DataFrame:
    merged = frames[k].merge(flags, on=["date_idx", "ticker"], how="left", validate="one_to_one")
    return merged[merged["matched"] & cohorts.flag_column(merged[flag])]


def _main_table(frames, flags, dates, draws, levels) -> dict:
    out: dict = {}
    for name, flag in COHORT_FLAGS.items():
        row: dict = {}
        for k in HORIZONS:
            cohort = _cohort(frames, flags, k, flag)
            row[f"close_{k}"] = stats.excess(cohort, dates, f"close_{k}",
                                             draws if k in (5, 10) else None, levels)
            row[f"mfe_{k}"] = stats.excess(cohort, dates, f"mfe_{k}", None, levels)
            row[f"mae_{k}"] = stats.excess(cohort, dates, f"mae_{k}", None, levels)
            if k == PRIMARY_K:
                row["n"] = int(len(cohort))
                row["unique_tickers"] = int(cohort["ticker"].nunique())
                row["win_rate"] = float((cohort["close_5"] > 0).mean()) if len(cohort) else float("nan")
        out[name] = row
    matched_all = frames[PRIMARY_K].merge(flags, on=["date_idx", "ticker"], how="left")
    matched_all = matched_all[matched_all["matched"]]
    out["MATCHED_CONTROL"] = {
        "n": int(len(matched_all)), "unique_tickers": int(matched_all["ticker"].nunique()),
        **{f"control_mean_close_{k}": float(
            _cohort(frames, flags, k, "is_em")[f"base_close_{k}"].mean()) for k in HORIZONS}}
    return out


def _increments(frames, flags, dates, draws, levels) -> dict:
    pairs = {"EQ2_minus_EM_REST": ("is_eq2", "is_em_rest"), "EQ2_minus_M_ONLY": ("is_eq2", "is_m_only"),
             "EQ2_minus_EM": ("is_eq2", "is_em"), "EQ1_minus_EM_REST": ("is_eq1", "is_em_rest"),
             "EQ3_minus_EM_REST": ("is_eq3", "is_em_rest")}
    out: dict = {}
    for name, (left, right) in pairs.items():
        entry = {}
        for k in (5, 10):
            entry[f"close_{k}"] = stats.difference(_cohort(frames, flags, k, left),
                                                   _cohort(frames, flags, k, right), dates,
                                                   f"close_{k}", draws, levels)
        for metric in ("mfe_5", "mae_5", "mfe_10", "mae_10"):
            k = int(metric.split("_")[1])
            entry[metric] = stats.difference(_cohort(frames, flags, k, left),
                                             _cohort(frames, flags, k, right), dates, metric,
                                             None, levels)
        out[name] = entry
    return out


def _ladder(frames, flags, dates, declaration) -> dict:
    """Every declared magnitude bucket and secondary cut, descriptive only."""
    merged = frames[PRIMARY_K].merge(flags, on=["date_idx", "ticker"], how="left", validate="one_to_one")
    merged = merged[merged["matched"] & cohorts.flag_column(merged["is_em"])]
    out: dict = {"buckets": {}, "secondary_cuts": {}}
    for label, _low, _high in declaration.ladder:
        part = merged[merged["bucket"] == label]
        out["buckets"][label] = {**stats.excess(part, dates, "close_5", None),
                                 "unique_tickers": int(part["ticker"].nunique())}
    for cut in declaration.secondary_cuts:
        part = merged[cohorts.flag_column(merged["observable"]) & (merged["revenue_growth_yoy"] >= cut)]
        out["secondary_cuts"][f">={cut}"] = {
            **stats.excess(part, dates, "close_5", None),
            "unique_tickers": int(part["ticker"].nunique()),
            "descriptive_only": True}
    return out


def _time_blocks(frames, flags, blocks) -> list[dict]:
    out = []
    for i, block in enumerate(blocks, start=1):
        eq2 = _cohort(frames, flags, PRIMARY_K, "is_eq2")
        rest = _cohort(frames, flags, PRIMARY_K, "is_em_rest")
        m_only = _cohort(frames, flags, PRIMARY_K, "is_m_only")
        eq2_b = eq2[eq2["date_idx"].isin(block)]
        rest_b = rest[rest["date_idx"].isin(block)]
        only_b = m_only[m_only["date_idx"].isin(block)]
        out.append({
            "block": i, "n_eq2": int(len(eq2_b)), "n_em_rest": int(len(rest_b)),
            "eq2_excess": stats.excess(eq2_b, block, "close_5", None)["point"],
            "em_rest_excess": stats.excess(rest_b, block, "close_5", None)["point"],
            "m_only_excess": stats.excess(only_b, block, "close_5", None)["point"],
            "difference_vs_em_rest": (stats.excess(eq2_b, block, "close_5", None)["point"]
                                      - stats.excess(rest_b, block, "close_5", None)["point"]),
            "difference_vs_m_only": (stats.excess(eq2_b, block, "close_5", None)["point"]
                                     - stats.excess(only_b, block, "close_5", None)["point"]),
        })
    return out


def _robustness(frames, flags, dates, draws, levels) -> dict:
    eq2 = _cohort(frames, flags, PRIMARY_K, "is_eq2")
    rest = _cohort(frames, flags, PRIMARY_K, "is_em_rest")
    m_only = _cohort(frames, flags, PRIMARY_K, "is_m_only")
    out: dict = {"leave_out": {}}

    def measure(subset: pd.DataFrame, label: str) -> None:
        out["leave_out"][label] = {
            "n": int(len(subset)),
            "eq2_excess": stats.excess(subset, dates, "close_5", None)["point"],
            "difference_vs_em_rest": (stats.excess(subset, dates, "close_5", None)["point"]
                                      - stats.excess(rest, dates, "close_5", None)["point"]),
            "difference_vs_m_only": (stats.excess(subset, dates, "close_5", None)["point"]
                                     - stats.excess(m_only, dates, "close_5", None)["point"]),
        }

    top_tickers = eq2["ticker"].value_counts().head(5).index.tolist()
    measure(eq2[~eq2["ticker"].isin(top_tickers)], "top5_tickers")
    out["leave_out"]["top5_tickers"]["excluded"] = [str(t) for t in top_tickers]
    top_dates = eq2["date_idx"].value_counts().head(10).index.tolist()
    measure(eq2[~eq2["date_idx"].isin(top_dates)], "top10_dates")
    for label in sorted(x for x in eq2["period_label"].dropna().unique()):
        measure(eq2[eq2["period_label"] != label], f"period_{label}")
    out["period_counts"] = eq2["period_label"].value_counts().to_dict()
    return out


def _gate(main, increments, block_table, robustness, concentration, pre, pit_violations,
          declaration: EqmDeclaration) -> dict:
    union_key = f"ci{int(round(declaration.union_level * 10000))}"
    h1_key = f"ci{int(round(declaration.h1_level * 10000))}"
    eq2 = main[cohorts.EQ2]["close_5"]
    vs_rest = increments["EQ2_minus_EM_REST"]["close_5"]
    vs_only = increments["EQ2_minus_M_ONLY"]["close_5"]
    h1 = eq2["point"] > 0 and eq2[h1_key][0] > 0
    h2 = vs_rest["point"] > 0 and vs_rest[union_key][0] > 0
    h3 = vs_only["point"] > 0 and vs_only[union_key][0] > 0
    carrier = "EQ2_minus_EM_REST" if h2 else ("EQ2_minus_M_ONLY" if h3 else None)
    carrier_key = "difference_vs_em_rest" if carrier != "EQ2_minus_M_ONLY" else "difference_vs_m_only"
    mae_difference = increments["EQ2_minus_EM_REST"]["mae_5"]["point"]
    leave = robustness["leave_out"]
    conditions = {
        "1_eq2_5d_point": bool(eq2["point"] > 0),
        "2_h1_ci_low": bool(eq2[h1_key][0] > 0),
        "3_union_point": bool(vs_rest["point"] > 0 or vs_only["point"] > 0),
        "4_union_ci_low": bool(h2 or h3),
        "5_mae": bool(mae_difference >= -0.02),
        "6_time_blocks": bool(sum(b["eq2_excess"] > 0 for b in block_table) >= 3
                              and sum(b[carrier_key] > 0 for b in block_table) >= 3),
        "7_sample": all(pre["checks"][key]["pass"] for key in
                        ("P4_eq2_sample", "P5_eq2_unique_tickers", "P6_comparators", "P7_block_sample")),
        "8_concentration": bool(concentration.get("single_ticker_share", 1) <= 0.05
                                and concentration.get("top5_ticker_share", 1) <= 0.15
                                and concentration.get("single_date_share", 1) <= 0.08
                                and concentration.get("top10_date_share", 1) <= 0.40
                                and all(v["eq2_excess"] > 0 and v[carrier_key] > 0
                                        for v in leave.values())),
        "9_pit_violations": pit_violations == 0,
    }
    decision = "PASS" if all(conditions.values()) else "FAIL"
    return {"conditions": conditions, "decision": decision,
            "H1": "PASS" if h1 else "FAIL", "H2": "PASS" if h2 else "FAIL",
            "H3": "PASS" if h3 else "FAIL",
            "union_carrier": carrier,
            "requirement": declaration.rules["hypotheses"]["pass_requirement"],
            "scope": declaration.rules["gate"]["scope_rule"]}


def _write(out_root: Path, run_id: str, summary: dict, frame: pd.DataFrame, started: float, log) -> dict:
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    out = out_root / run_id
    out.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out / "cohort_rows.parquet", index=False)
    (out / "summary.json").write_bytes(dump_json(summary))
    (out / "rules.json").write_bytes(
        (REPO_ROOT / "docs/backtest/strategy_eqm_v0/eqm_v0_rules_v1.json").read_bytes())
    log(f"{summary.get('decision', 'GATE-EQM-V0 = NOT_REACHED')} out={out} "
        f"elapsed_s={summary['elapsed_seconds']}")
    return summary
