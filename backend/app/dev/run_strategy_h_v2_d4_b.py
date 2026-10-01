"""H-V2-D4-B Tier B runner: the chained D3 -> D4 run on six issuers no live call has ever touched.

Every leg is an existing frozen contract, called unchanged. This file is orchestration and accounting
and nothing else - it contains no prompt, no schema, no threshold and no validator:

    D3 leg   `run_strategy_h_v2_d3_3.research_one_v3`   Prompt V2 + Schema V2 + Validation V2
    bundle   `run_strategy_h_v2_d4_2.build_bundle_for`  ExpectationEvidenceBundle, PIT-trimmed panel
    D4 leg   `run_strategy_h_v2_d4_2.analyze_one_v2`    the repaired D4 path, D4-S + D4-H1 validator
    budget   `d4_2_contract.TIER_B_BUDGET`              call-topology reserve, $45.60 ceiling

Why it is a new file rather than `run_strategy_h_v2_d4_1.run_tier_b`. That function exists and does
the same chaining, but it predates two repairs and would have run Tier B under both defects: its D4
leg is D4.1's `analyze_one` rather than the repaired `analyze_one_v2`, so Tier B would have tested a
contract D4.2 superseded and D4-S1 never validated; and its preflight reserves
`TIER_B_WORST_CASE_CANDIDATE_USD` = $3.60 per candidate, which is D4.1's per-call-cap-as-candidate-
reserve defect - a candidate that makes three calls can spend $6.00 on the D4 leg alone. Calling it
would have been a cheaper way to reach a wrong number.

Adjudicates nothing. E1-E8, SF1, C1/C4 and M8 are computed by `audit_strategy_h_v2_d4_b.py` over the
stored records, for the reason D4.1's and D4.2's runners both gave: a runner that graded itself would
be grading the only thing it can see.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Mapping
import json
import sys

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.expectation.contract_v2 import (
    CandidateSpend,
    D4_CONTRACT_VERSION,
    run_total_cost_usd,
)
from app.backtest.strategy_h_v2.expectation.d4_1_contract import TIER_B_SAMPLE, tier_b_checksum
from app.backtest.strategy_h_v2.expectation.d4_2_contract import TIER_B_BUDGET
from app.backtest.strategy_h_v2.expectation.d4_b_contract import (
    ANALYSES_ROOT,
    D3_LEG_ATTEMPTS_ROOT,
    D4_B_CONTRACT_VERSION,
    D4_B_ROOT,
    OBSERVED_PROJECTION_USD,
    TIER_B_HARD_CAP_USD,
)
from app.backtest.strategy_h_v2.expectation.gap_contract import GAP_CONTRACT_VERSION
from app.backtest.strategy_h_v2.expectation.ledger import store_analysis, store_evidence_bundle
from app.backtest.strategy_h_v2.expectation.prompt import PROMPT_VERSION
from app.backtest.strategy_h_v2.expectation.validate import VALIDATION_CONTRACT_VERSION
from app.backtest.strategy_h_v2.research.d3_3_contract import PACKAGES_DIR
from app.backtest.strategy_h_v2.research.prompt_builder_v2 import PROMPT_VERSION_V2
from app.backtest.strategy_h_v2.research.research_attempt_v2 import store_attempt
from app.backtest.strategy_h_v2.research.schema_v2 import SCHEMA_VERSION as D3_SCHEMA_VERSION
from app.backtest.strategy_h_v2.research.validate import package_checksum
from app.dev.run_strategy_h_v2_d3_3 import (
    MODEL as D3_MODEL,
    call_opus as d3_call_opus,
    research_one_v3,
)
from app.dev.run_strategy_h_v2_d4_2 import (
    MODEL as D4_MODEL,
    analyze_one_v2,
    build_bundle_for,
    call_opus as d4_call_opus,
    load_price_panel,
)

MODEL = D4_MODEL
assert D3_MODEL == D4_MODEL == "claude-opus-5-5", (
    "both legs must request the frozen contract model; a fallback is not permitted (brief §7)"
)


def _d3_spend(record) -> tuple[float, tuple[float, ...]]:
    """The D3 leg's cost, split initial vs per-repair (brief §9). `ResearchAttemptRecordV1.cost_usd`
    is the running total, so the initial cost is the total minus what the repair rounds charged."""
    repairs = tuple(r.cost_usd or 0.0 for r in (record.repair_attempts or []))
    return round((record.cost_usd or 0.0) - sum(repairs), 10), repairs


def run_tier_b(
    *, sample=TIER_B_SAMPLE, d3_call_fn=d3_call_opus, d4_call_fn=d4_call_opus,
    hard_cap_usd: float = TIER_B_HARD_CAP_USD,
    d3_leg_root: Path = D3_LEG_ATTEMPTS_ROOT, analyses_root: Path = ANALYSES_ROOT,
    manifest_root: Path = D4_B_ROOT,
    panel: Mapping[str, Mapping[date, float]] | None = None,
) -> dict:
    """Tier B end to end. One candidate at a time, budget checked BEFORE the candidate starts.

    A candidate whose D3 leg does not return OK is recorded `D3_LEG_FAILED` and gets no D4 leg and no
    substitute input - the brief forbids sample substitution, and a D4 call on a manufactured D3
    output would be measuring the manufacture. Its D3 cost is still charged, because a failed call was
    still paid for and a budget that counts only successes is not a budget.
    """
    run_id = datetime.now(timezone.utc).strftime("D4_B-%Y%m%dT%H%M%SZ")
    panel = panel if panel is not None else load_price_panel(e.ticker for e in sample)
    generated_at = datetime.now(timezone.utc)

    results: list[dict] = []
    spends: list[CandidateSpend] = []
    d3_leg: dict[str, dict] = {}
    spent = 0.0
    stopped_early: str | None = None
    budget_exhausted = False

    for entry in sample:
        ticker = entry.ticker
        if not TIER_B_BUDGET.admits_next_candidate(
            spent_so_far_usd=spent, overall_hard_cap_usd=hard_cap_usd
        ):
            stopped_early, budget_exhausted = ticker, True
            break

        package = AIResearchInputV1.model_validate_json(
            (PACKAGES_DIR / f"{ticker}.json").read_text())

        # --- D3 leg -------------------------------------------------------------------------
        d3_record = research_one_v3(package, ticker, entry.cik, run_id, call_fn=d3_call_fn)
        store_attempt(d3_leg_root, d3_record)
        d3_initial, d3_repairs = _d3_spend(d3_record)
        d3_leg[ticker] = {
            "attempt_id": d3_record.attempt_id, "final_status": d3_record.final_status,
            "model_requested": d3_record.model_requested,
            "canonical_model": d3_record.canonical_model,
            "model_mismatch": d3_record.model_mismatch,
            "input_package_checksum": d3_record.input_package_checksum,
            "initial_parse_status": d3_record.initial_parse_status,
            "initial_validation_status": d3_record.initial_validation_status,
            "initial_failure_codes": list(d3_record.initial_failure_codes or []),
            "repair_attempts": len(d3_record.repair_attempts or []),
            "repair_causes": [c for r in (d3_record.repair_attempts or [])
                              for c in (r.failure_codes_before or [])],
            "d3_initial_cost_usd": d3_initial,
            "d3_repair_costs_usd": list(d3_repairs),
            "d3_total_cost_usd": d3_record.cost_usd,
        }

        if d3_record.final_status != "OK" or not d3_record.final_output:
            spent += d3_record.cost_usd or 0.0
            spends.append(CandidateSpend(ticker=ticker, initial_cost_usd=0.0,
                                         preceding_stage_cost_usd=d3_record.cost_usd or 0.0))
            results.append({
                "ticker": ticker, "cik": entry.cik, "depth": entry.depth,
                "priority": entry.priority, "final_status": "D3_LEG_FAILED",
                "d3_final_status": d3_record.final_status,
                "spend": spends[-1].to_dict(),
            })
            continue

        # --- Expectation evidence -----------------------------------------------------------
        bundle = build_bundle_for(package, panel, bundle_id=f"EB-{run_id}-{ticker}",
                                  generated_at=generated_at)
        store_evidence_bundle(analyses_root, run_id, ticker, bundle.model_dump_json(indent=2))

        # --- D4 leg -------------------------------------------------------------------------
        record, d4_spend = analyze_one_v2(
            package=package,
            research_record={"final_output": d3_record.final_output,
                             "final_output_checksum": d3_record.final_output_checksum},
            bundle=bundle, analysis_run_id=run_id, call_fn=d4_call_fn,
        )
        store_analysis(analyses_root, record)
        spend = replace(d4_spend, preceding_stage_cost_usd=d3_record.cost_usd or 0.0)
        spends.append(spend)
        spent += spend.candidate_total_cost_usd

        results.append({
            "ticker": ticker, "cik": entry.cik, "depth": entry.depth, "priority": entry.priority,
            "final_status": record.final_status,
            "d3_final_status": d3_record.final_status,
            "d3_attempt_id": d3_record.attempt_id,
            "d3_output_checksum": d3_record.final_output_checksum,
            "input_package_checksum": package_checksum(package),
            "expectation_evidence_id": bundle.bundle_id,
            "model_mismatch": record.model_mismatch or d3_record.model_mismatch,
            "d3_canonical_model": d3_record.canonical_model,
            "d4_canonical_model": record.canonical_model,
            "analysis_id": record.analysis_id,
            "d3_initial_valid": d3_record.initial_validation_status == "OK",
            "d3_repair_rounds": len(d3_record.repair_attempts or []),
            "d4_initial_valid": record.initial_validation_status == "OK",
            "d4_repair_rounds": len(record.repair_rounds),
            "expectation_gap": (record.final_output or {}).get("expectation_gap"),
            "expectation_gap_confidence": (record.final_output or {}).get(
                "expectation_gap_confidence"),
            "priced_in": ((record.final_output or {}).get("priced_in_assessment") or {}).get(
                "state"),
            "applied_contract_rules": record.applied_contract_rules,
            "spend": spend.to_dict(),
            "within_candidate_worst_case": (
                spend.candidate_total_cost_usd
                <= TIER_B_BUDGET.candidate_worst_case_budget_usd),
        })

    manifest = {
        "schema": "H_V2_D4_B_RUN_MANIFEST_V1", "run_id": run_id, "tier": "B",
        "contract_version": D4_B_CONTRACT_VERSION,
        "d4_contract_version": D4_CONTRACT_VERSION,
        "d3_prompt_version": PROMPT_VERSION_V2, "d3_schema_version": D3_SCHEMA_VERSION,
        "d4_prompt_version": PROMPT_VERSION, "gap_contract_version": GAP_CONTRACT_VERSION,
        "validation_contract_version": VALIDATION_CONTRACT_VERSION,
        "model_requested": MODEL,
        "sample_size": len(sample), "attempted": len(results),
        "tier_b_checksum": tier_b_checksum(tuple(sample)),
        "stopped_early": stopped_early, "budget_exhausted": budget_exhausted,
        "budget_contract": TIER_B_BUDGET.to_dict(),
        "tier_b_hard_cap_usd": hard_cap_usd,
        "observed_projection_usd": OBSERVED_PROJECTION_USD,
        "run_total_cost_usd": run_total_cost_usd(spends),
        "candidate_spends": [s.to_dict() for s in spends],
        "d3_leg": d3_leg,
        "results": results,
    }
    manifest_root.mkdir(parents=True, exist_ok=True)
    (manifest_root / f"{run_id}.manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str))
    return manifest


def main() -> None:
    if "--execute" not in sys.argv:
        raise SystemExit(
            "H-V2-D4-B spends live money. Re-run with --execute to start the frozen Tier B run."
        )
    manifest = run_tier_b()
    print(json.dumps({
        "run_id": manifest["run_id"],
        "attempted": manifest["attempted"],
        "run_total_cost_usd": manifest["run_total_cost_usd"],
        "tier_b_hard_cap_usd": manifest["tier_b_hard_cap_usd"],
        "stopped_early": manifest["stopped_early"],
        "results": [{k: r.get(k) for k in
                     ("ticker", "final_status", "d4_initial_valid", "d4_repair_rounds",
                      "expectation_gap", "expectation_gap_confidence")}
                    for r in manifest["results"]],
    }, indent=2))


if __name__ == "__main__":
    main()
