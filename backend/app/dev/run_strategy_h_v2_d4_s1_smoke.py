"""H-V2-D4-S1: the SCCO limited live smoke driver.

One question, and only one. After D4-S moved expectation AVAILABILITY authority out of the prose
classifier and into `ExpectationKnowledgeStateV1`, does a real Claude Opus 5.5 response for one
issuer pass schema, validator and audit from its INITIAL response, without honest
uncertainty/absence language being rejected as a fabricated expectation?

Not an investment-quality check. Not a Tier B generalization check. One issuer is not a rate.

Why a driver rather than calling `run_tier_a_v2` by hand. Three things have to be true before a
live call is made, and a human typing them at an interpreter is how one of them gets skipped:

  1. the hard cap is the FROZEN per-candidate worst case, asserted in code, not a number retyped
     here (D4.1's budget defect was exactly a retyped number meaning something else);
  2. every input exists and every reference resolves BEFORE any spend, so a missing D3 object
     costs $0 rather than one call;
  3. the run is one ticker, and `SCCO` is a member of the frozen Tier A sample rather than a
     freshly chosen issuer.

`run_strategy_h_v2_d4_2.run_tier_a_v2` is reused UNCHANGED - this file passes it a one-element
sample and the one-candidate cap. No D4-S, expectation-state, consensus-classifier or M8 code is
touched, and nothing here adjudicates a gate: `audit_strategy_h_v2_d4_2.audit_run` does that over
the stored records, for the reason D4.1's runner gave.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.expectation.code_facts import build_code_fact_index, code_source_id
from app.backtest.strategy_h_v2.expectation.d4_1_contract import (
    TIER_A_CHECKSUM,
    tier_a_checksum,
)
from app.backtest.strategy_h_v2.expectation.d4_2_contract import (
    PER_CALL_MAX_BUDGET_USD,
    TIER_A_BUDGET,
    TIER_A_TICKERS_UNCHANGED,
)
from app.backtest.strategy_h_v2.expectation.expectation_state import (
    EXPECTATION_STATE_CONTRACT_VERSION,
    derive_expectation_knowledge_state,
)
from app.backtest.strategy_h_v2.expectation.prompt import PROMPT_VERSION, build_d4_prompt
from app.backtest.strategy_h_v2.expectation.validate import bundle_checksum
from app.backtest.strategy_h_v2.research.d3_3_contract import PACKAGES_DIR
from app.dev.run_strategy_h_v2_d4_2 import (
    D3_3_ATTEMPTS_ROOT,
    MODEL,
    build_bundle_for,
    d3_output_index,
    load_price_panel,
    run_tier_a_v2,
)

#: The one issuer. A member of the frozen Tier A sample, deliberately: a mechanical smoke learns
#: nothing from an unseen issuer that it does not learn from a seen one, and spending on a new
#: issuer to find that out would be spending to learn nothing.
SMOKE_TICKER = "SCCO"

#: The cap is the frozen per-candidate worst case, READ from the budget contract rather than
#: retyped. The assertion below is what makes "$6.00" a derived fact instead of an intention.
SMOKE_HARD_CAP_USD = TIER_A_BUDGET.candidate_worst_case_budget_usd
assert SMOKE_HARD_CAP_USD == PER_CALL_MAX_BUDGET_USD * 3 == 6.00, (
    "the smoke cap must be the frozen per-call cap times the frozen call topology"
)

SMOKE_ROOT = Path("data/runtime/strategy_h_v2/d4_s1")


def preflight() -> dict:
    """Every check that can fail for free, before the first call. Zero model calls."""
    report: dict = {"ticker": SMOKE_TICKER, "checks": {}, "failures": []}

    def check(name: str, value, ok: bool | None = None) -> None:
        passed = bool(value) if ok is None else ok
        report["checks"][name] = value if not isinstance(value, bool) else passed
        if not passed:
            report["failures"].append(name)

    check("sample_checksum_verifies",
          tier_a_checksum(TIER_A_TICKERS_UNCHANGED) == TIER_A_CHECKSUM)
    check("ticker_in_frozen_sample", SMOKE_TICKER in TIER_A_TICKERS_UNCHANGED)
    check("execpath_available", bool(os.environ.get("CLAUDE_CODE_EXECPATH")))

    package_path = PACKAGES_DIR / f"{SMOKE_TICKER}.json"
    check("runtime_package_exists", package_path.exists())
    if not package_path.exists():
        return report

    package = AIResearchInputV1.model_validate_json(package_path.read_text())
    report["checks"]["package_sha256"] = hashlib.sha256(package_path.read_bytes()).hexdigest()
    report["checks"]["cik"] = package.evidence_bundle.identity.get("cik")
    report["checks"]["chunks"] = len(package.chunks)
    report["checks"]["sources"] = len(package.source_manifest)
    source_ids = {s.source_id for s in package.source_manifest}
    check("every_chunk_source_resolves",
          all(c.source_id in source_ids for c in package.chunks))

    d3 = d3_output_index(D3_3_ATTEMPTS_ROOT)
    check("d3_object_exists", SMOKE_TICKER in d3)
    if SMOKE_TICKER not in d3:
        return report
    record = d3[SMOKE_TICKER]
    report["checks"]["d3_attempt_id"] = record["attempt_id"]
    report["checks"]["d3_output_checksum"] = record["final_output_checksum"]
    check("d3_cik_matches_package", record["cik"] == report["checks"]["cik"])

    panel = load_price_panel((SMOKE_TICKER,))
    report["checks"]["panel_sessions"] = len(panel.get(SMOKE_TICKER, {}))
    check("price_panel_present", bool(panel.get(SMOKE_TICKER)))

    bundle = build_bundle_for(package, panel, bundle_id=f"EB-PREFLIGHT-{SMOKE_TICKER}",
                              generated_at=datetime.now(timezone.utc))
    check("expectation_evidence_builds", True)
    report["checks"]["consensus_status"] = bundle.consensus.status.value
    report["checks"]["estimate_revisions_status"] = bundle.estimate_revisions.status.value
    report["checks"]["price_reaction_events"] = len(bundle.price_reaction)

    state = derive_expectation_knowledge_state(bundle)
    check("expectation_knowledge_state_builds", True)
    report["checks"]["expectation_state"] = state.to_dict()
    report["checks"]["expectation_state_contract"] = EXPECTATION_STATE_CONTRACT_VERSION

    facts = build_code_fact_index(
        bundle, research_facts=package.evidence_bundle.fundamental_changes)
    report["checks"]["code_facts"] = len(facts)
    valid_evidence = ({c.evidence_id for c in package.chunks}
                      | set(bundle.valid_evidence_ids()) | set(facts))
    valid_sources = source_ids | {code_source_id(bundle.bundle_id)}
    report["checks"]["valid_evidence_ids"] = len(valid_evidence)
    report["checks"]["valid_source_ids"] = len(valid_sources)
    check("references_resolve", bool(valid_evidence) and bool(valid_sources))

    system, user = build_d4_prompt(package=package, bundle=bundle,
                                   research_output=record["final_output"], code_facts=facts)
    check("d4_prompt_builds", True)
    report["checks"]["prompt_system_chars"] = len(system)
    report["checks"]["prompt_user_chars"] = len(user)
    report["checks"]["preflight_bundle_checksum"] = bundle_checksum(bundle)
    report["checks"]["prompt_version"] = PROMPT_VERSION
    report["checks"]["requested_model"] = MODEL
    return report


def main() -> None:
    SMOKE_ROOT.mkdir(parents=True, exist_ok=True)
    report = preflight()
    print("=" * 96)
    print(f"H-V2-D4-S1 preflight   ticker={SMOKE_TICKER}   hard_cap=${SMOKE_HARD_CAP_USD:.2f}")
    print("=" * 96)
    for name, value in report["checks"].items():
        rendered = json.dumps(value) if isinstance(value, dict) else value
        print(f"  {name:36} = {rendered}")

    if report["failures"]:
        print("\nINPUT_NOT_READY   live calls = 0   live cost = $0.00")
        for name in report["failures"]:
            print(f"  FAILED: {name}")
        (SMOKE_ROOT / "preflight_failed.json").write_text(json.dumps(report, indent=2, default=str))
        sys.exit(2)

    print("\nINPUT_READY   proceeding to ONE live candidate")
    print(f"  reserve check: 0.00 + {SMOKE_HARD_CAP_USD:.2f} <= {SMOKE_HARD_CAP_USD:.2f}  -> admit")

    manifest = run_tier_a_v2(
        tickers=(SMOKE_TICKER,),
        hard_budget_usd=SMOKE_HARD_CAP_USD,
    )

    out = {"schema": "H_V2_D4_S1_SMOKE_V1", "preflight": report, "manifest": manifest}
    (SMOKE_ROOT / f"{manifest['run_id']}.smoke.json").write_text(
        json.dumps(out, indent=2, default=str))

    print("\n" + "=" * 96)
    print(f"run_id           = {manifest['run_id']}")
    print(f"attempted        = {manifest['attempted']} / {manifest['sample_size']}")
    print(f"run total cost   = ${manifest['run_total_cost_usd']:.7f}  of ${SMOKE_HARD_CAP_USD:.2f}")
    print(f"stopped_early    = {manifest['stopped_early']}")
    print(f"budget_exhausted = {manifest['budget_exhausted']}")
    for result in manifest["results"]:
        print(f"\n  {result['ticker']}")
        for key in ("final_status", "canonical_model", "model_mismatch", "repair_rounds",
                    "expectation_gap", "expectation_gap_confidence", "priced_in",
                    "analysis_id", "within_candidate_worst_case"):
            print(f"    {key:28} = {result[key]}")
        print(f"    spend                        = {json.dumps(result['spend'])}")
    print(f"\nwritten: {SMOKE_ROOT / (manifest['run_id'] + '.smoke.json')}")


if __name__ == "__main__":
    main()
