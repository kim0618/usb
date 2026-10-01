"""H-V2-D4-BR-C runner: the chained D3 -> D4 run on the four issuers the confirmation preregistered.

Orchestration and accounting only, and almost none of it is new. The chaining, the per-candidate
preflight and the manifest are `run_strategy_h_v2_d4_b.run_tier_b`, called with the confirmation's
sample, its $30.40 ceiling, its own store and its own run-id prefix; every leg underneath is the
frozen contract Tier B ran, unchanged:

    D3 leg   `run_strategy_h_v2_d3_3.research_one_v3`   Prompt V2 + Schema V2 + Validation V2
    bundle   `run_strategy_h_v2_d4_2.build_bundle_for`  ExpectationEvidenceBundle, PIT-trimmed panel
    D4 leg   `run_strategy_h_v2_d4_2.analyze_one_v2`    now carrying D4-BR's repaired convergence
    budget   `d4_br_c_contract.D4_BR_C_HARD_CAP_USD`    4 x $7.60, a ceiling and not a forecast

Reusing the Tier B runner rather than copying it is deliberate: the question this run asks is whether
the SAME pipeline converges on unseen issuers, and a second implementation of the chaining would make
"the same pipeline" an assertion instead of a fact. What differs from Tier B is the sample, the
ceiling, the store and the label - nothing inside either leg.

Adjudicates nothing. E1-E8, SF1, C1/C4 and M8 are computed afterwards by
`audit_strategy_h_v2_d4_br_c.py` over the stored records, for the reason every D4 runner has given: a
runner that graded itself would be grading the only thing it can see.
"""

from __future__ import annotations

import json
import sys

from app.backtest.strategy_h_v2.expectation.d4_br_c_contract import (
    ANALYSES_ROOT,
    D3_LEG_ATTEMPTS_ROOT,
    D4_BR_C_CONTRACT_VERSION,
    D4_BR_C_HARD_CAP_USD,
    D4_BR_C_ROOT,
    D4_BR_C_RUN_ID_PREFIX,
    OBSERVED_PROJECTION_USD,
    sample_integrity,
)
from app.backtest.strategy_h_v2.expectation.d4_br_contract import (
    D4_BR_CONFIRMATION_SAMPLE,
    confirmation_checksum,
)
from app.dev.run_strategy_h_v2_d4_b import MODEL, run_tier_b

SCHEMA = "H_V2_D4_BR_C_RUN_MANIFEST_V1"


def run_confirmation(**overrides) -> dict:
    """The preregistered four, one candidate at a time, budget checked before each one starts."""
    integrity = sample_integrity()
    if not integrity["checksum_matches"]:
        raise RuntimeError(
            "the confirmation sample's checksum does not match the frozen literal; the sample moved "
            "and a run against a moved sample is not the preregistered confirmation"
        )
    kwargs = dict(
        sample=D4_BR_CONFIRMATION_SAMPLE,
        hard_cap_usd=D4_BR_C_HARD_CAP_USD,
        d3_leg_root=D3_LEG_ATTEMPTS_ROOT,
        analyses_root=ANALYSES_ROOT,
        manifest_root=D4_BR_C_ROOT,
        run_id_prefix=D4_BR_C_RUN_ID_PREFIX,
        tier="BR_C",
        schema=SCHEMA,
        contract_version=D4_BR_C_CONTRACT_VERSION,
        checksum_fn=confirmation_checksum,
        observed_projection_usd=OBSERVED_PROJECTION_USD,
    )
    kwargs.update(overrides)
    return run_tier_b(**kwargs)


def main() -> None:
    if "--execute" not in sys.argv:
        raise SystemExit(
            "H-V2-D4-BR-C spends live money. Re-run with --execute to start the confirmation."
        )
    manifest = run_confirmation()
    print(json.dumps({
        "run_id": manifest["run_id"],
        "model_requested": MODEL,
        "attempted": manifest["attempted"],
        "run_total_cost_usd": manifest["run_total_cost_usd"],
        "hard_cap_usd": manifest["tier_b_hard_cap_usd"],
        "stopped_early": manifest["stopped_early"],
        "budget_exhausted": manifest["budget_exhausted"],
        "results": [{k: r.get(k) for k in
                     ("ticker", "final_status", "d3_final_status", "d4_initial_valid",
                      "d4_repair_rounds", "expectation_gap", "expectation_gap_confidence",
                      "d4_canonical_model")}
                    for r in manifest["results"]],
    }, indent=2))


if __name__ == "__main__":
    main()
