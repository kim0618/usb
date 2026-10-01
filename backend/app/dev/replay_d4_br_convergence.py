"""H-V2-D4-BR: the frozen offline replay. Zero live calls, $0.

What this proves, and what it must not be read as proving.

It re-runs the REPAIRED validator over Tier B's stored raw responses - the same bytes the live run
produced, never a new call - and reports, per candidate and per round, whether that payload validates
and why not. Two uses:

  1. The §13/§14 counterfactual: FRPT's and SPSC's own evidence CAN be expressed as a valid output.
     Each needs exactly one edit on top of a payload the model actually produced, which is what
     "the schema affords honest abstention" has to mean if it means anything measurable.

  2. The §16 recovery: the terminal failure reason for each failed candidate, which the live record
     never stored. Both turned out to differ from what the run was reading at the time.

It does NOT re-run Tier B and it does not change Tier B's verdict. `HISTORICAL` below is the frozen
result, asserted rather than recomputed: E1 stays 4/6 and Tier B stays FAIL no matter what this
replay finds, because the sample was graded on the contract that was live when it ran. A counterfactual
that validates here is evidence about the repaired contract's affordance, not a retroactive pass.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.expectation.code_facts import build_code_fact_index
from app.backtest.strategy_h_v2.expectation.evidence_schema import ExpectationEvidenceBundleV1
from app.backtest.strategy_h_v2.expectation.validate import assemble_and_validate_d4
from app.backtest.strategy_h_v2.research.d3_3_contract import PACKAGES_DIR

TIER_B_RUN_ID = "D4_B-20260930T053146Z"
D4_B_ROOT = Path("data/runtime/strategy_h_v2/d4_b")
ANALYSES_ROOT = D4_B_ROOT / "analyses"
D3_LEG_ROOT = D4_B_ROOT / "d3_leg" / "attempts"

HISTORICAL: dict[str, Any] = {
    "run_id": TIER_B_RUN_ID,
    "sample_n": 6,
    "d3_final_ok": 6,
    "d4_final_ok": 4,
    "e1_rate": 4 / 6,
    "e1_threshold": 0.95,
    "e1_verdict": "FAIL",
    "tier_b_verdict": "FAIL",
    "no_final_output": ("FRPT", "SPSC"),
    "final_fabricated_consensus": 0,
}
"""Tier B as graded, immutable. Nothing in this module may recompute these."""

COUNTERFACTUALS: dict[str, dict[str, Any]] = {
    # ticker -> the ONE edit, applied to the round named, and why that edit is a repair and not a
    # different answer. Both are moves §10 explicitly permits: downgrade to an existing state, and
    # reword a denial so it is not read as a claim.
    "FRPT": {
        "round": "repair2",
        "edit": "overall_guidance_state RAISED -> MIXED",
        "why": (
            "repair 2 had already abstained on the open-ended margin floor (state=UNKNOWN, no "
            "bounds). That left per-metric states {RAISED, MAINTAINED}, which the schema says is "
            "MIXED. The edit is the arithmetic consequence of the abstention the model had already "
            "chosen - not a new claim, and strictly less directional than RAISED."
        ),
    },
    "SPSC": {
        "round": "repair1",
        "edit": "unknown_fields[0] 'consensus expectations' -> 'analyst consensus: no source "
                "connected'",
        "why": (
            "repair 1 had already downgraded both guidance comparisons to UNKNOWN. The only thing "
            "left was the name it gave the quantity it was declaring unknown. Rewording an "
            "abstention label removes no evidence and adds no claim."
        ),
    },
}


def _payload(record: dict, which: str) -> dict:
    raw = (record["initial_raw_response"] if which == "initial"
           else record["repair_rounds"][int(which.removeprefix("repair")) - 1]
           ["raw_repair_response"])["raw_text"]
    return json.loads(raw[raw.index("{"):raw.rindex("}") + 1])


def _inputs(ticker: str) -> tuple[AIResearchInputV1, ExpectationEvidenceBundleV1, dict, dict]:
    package = AIResearchInputV1.model_validate_json((PACKAGES_DIR / f"{ticker}.json").read_text())
    analysis_dir = ANALYSES_ROOT / TIER_B_RUN_ID / ticker
    bundle = ExpectationEvidenceBundleV1.model_validate_json(
        (analysis_dir / "expectation_evidence.json").read_text())
    d3 = json.loads(next((D3_LEG_ROOT / TIER_B_RUN_ID / ticker).glob("*.json")).read_text())
    record = json.loads(next(
        p for p in analysis_dir.glob("*.json") if p.name != "expectation_evidence.json"
    ).read_text())
    return package, bundle, d3, record


def _validate(content: dict, package, bundle, d3: dict, record: dict) -> list[str]:
    _, errors = assemble_and_validate_d4(
        json.dumps(content), package=package, bundle=bundle,
        research_output=d3["final_output"],
        code_facts=build_code_fact_index(
            bundle, research_facts=package.evidence_bundle.fundamental_changes),
        analysis_id=record["analysis_id"], version=1,
        model_name=record["model_requested"], model_version=record["canonical_model"],
        prompt_version=record["prompt_version"],
        created_at=datetime.fromisoformat(record["started_at"]),
    )
    return errors


def replay_candidate(ticker: str) -> dict[str, Any]:
    package, bundle, d3, record = _inputs(ticker)
    rounds = ["initial"] + [f"repair{i}" for i in range(1, len(record["repair_rounds"]) + 1)]
    per_round = []
    for name in rounds:
        errors = _validate(_payload(record, name), package, bundle, d3, record)
        per_round.append({"round": name, "valid": not errors, "errors": errors})

    out: dict[str, Any] = {
        "ticker": ticker,
        "historical_final_status": record["final_status"],
        "rounds": per_round,
        "terminal_round": per_round[-1]["round"],
        "terminal_errors_recovered": per_round[-1]["errors"],
        "record_stored_terminal_failure": list(record.get("terminal_failure_details") or []),
    }
    plan = COUNTERFACTUALS.get(ticker)
    if plan:
        content = _payload(record, plan["round"])
        if ticker == "FRPT":
            content["market_expectation_evidence"]["overall_guidance_state"] = "MIXED"
        else:
            content["unknown_fields"][0] = "analyst consensus: no source connected"
        errors = _validate(content, package, bundle, d3, record)
        out["counterfactual"] = {
            "base_round": plan["round"], "edit": plan["edit"], "why": plan["why"],
            "valid": not errors, "errors": errors,
        }
    return out


def replay() -> dict[str, Any]:
    candidates = [replay_candidate(e.ticker) for e in _sample()]
    return {
        "live_calls": 0,
        "live_cost_usd": 0.0,
        "run_id": TIER_B_RUN_ID,
        "historical": dict(HISTORICAL),
        "candidates": candidates,
        "abstention_representable": {
            c["ticker"]: c["counterfactual"]["valid"]
            for c in candidates if "counterfactual" in c
        },
    }


def _sample():
    from app.backtest.strategy_h_v2.expectation.d4_1_contract import TIER_B_SAMPLE
    return TIER_B_SAMPLE


def main() -> None:
    print(json.dumps(replay(), indent=2, default=str))


if __name__ == "__main__":
    main()
