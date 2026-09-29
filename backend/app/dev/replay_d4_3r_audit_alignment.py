"""D4.3R offline replay (brief §16-18): the repaired M1-M12 audit applied to D4.3A's own stored,
already-graded records. Zero live calls, zero cost - every byte replayed here was already paid for
and stored by the actual Tier A V2 run.

What this answers, and only this: with M4's consensus classifier unified onto
`consensus_language.asserted_consensus_findings` and M8's numeric check routed through
`numeric_roles.fact_is_restated`, do D4.3A's two known audit-layer false positives (M4's one
GOOG limitation sentence, M8's six session/fiscal-period/rule-id false positives) disappear, and
does every OTHER gate reproduce its D4.3A value unchanged? `H_V2_D4_3A_TIER_A_V2_MECHANICAL_RESULT_V1.md`
is not edited by this and its verdict does not change - that document already declares §O's M4/M8
diagnosis as a defect in the audit layer, not in the model, and this replay is the counterfactual
check on the repair, produced as its own labelled artifact rather than folded backward into D4.3A.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import sys

from app.backtest.strategy_h_v2.expectation.d4_2_contract import D4_2_ROOT
from app.dev.audit_strategy_h_v2_d4_2 import audit_run

REPLAY_ROOT = D4_2_ROOT / "replay"

#: D4.3A's own stored gate audit (`H_V2_D4_3A_TIER_A_V2_MECHANICAL_RESULT_V1.md` §O), produced by
#: the OLD detectors and never overwritten - the "old" side of the comparison is read from disk
#: rather than recomputed, because recomputing it would require reintroducing the very regexes this
#: session removes.
D4_3A_RUN_ID = "D4_2_A-20260929T072105Z"


def _old_gateaudit(run_id: str, *, root: Path = D4_2_ROOT) -> dict:
    path = root / f"{run_id}.gateaudit.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found - D4.3A's stored gate audit is required as the 'old' side of this "
            "replay and is not recomputed, since recomputing it would need the removed detectors"
        )
    return json.loads(path.read_text())


def replay(run_id: str = D4_3A_RUN_ID) -> dict:
    old = _old_gateaudit(run_id)
    new = audit_run(run_id)

    old_by_gate = {g["gate"]: g for g in old["gates"]}
    new_by_gate = {g["gate"]: g for g in new["gates"]}
    gate_ids = [g["gate"] for g in old["gates"]]

    comparison = []
    for gate_id in gate_ids:
        o, n = old_by_gate[gate_id], new_by_gate[gate_id]
        comparison.append({
            "gate": gate_id, "name": o["name"],
            "old_status": o["status"], "new_status": n["status"],
            "old_observed": o["observed"], "new_observed": n["observed"],
            "changed": o["status"] != n["status"],
        })

    changed = [c for c in comparison if c["changed"]]
    unchanged_ids = [c["gate"] for c in comparison if not c["changed"]]
    #: Every gate this replay does not touch must reproduce byte-for-byte the same status D4.3A
    #: already recorded - brief §16's "other gates unchanged?" is this assertion, not a claim in
    #: prose.
    unexpected_changes = [c for c in changed if c["gate"] not in ("M4", "M8")]

    return {
        "schema": "H_V2_D4_3R_OFFLINE_REPLAY_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "live_opus_calls": 0,
        "cost_usd": 0.0,
        "source": "stored D4.3A records, replayed under the D4.3R M4/M8 audit repair",
        "d4_3a_verdict_unchanged": True,
        "d4_3a_verdict": old["verdict"],
        "gate_comparison": comparison,
        "changed_gates": [c["gate"] for c in changed],
        "unchanged_gates": unchanged_ids,
        "unexpected_changes": unexpected_changes,
        "denominators": new["denominators"],
        "d4_3r_counterfactual_verdict": new["verdict"],
        "verdict_means": (
            "whether the SAME stored Tier A V2 records would have graded MECHANICAL READY under "
            "the repaired audit layer. It is not a re-grading of D4.3A, which stands unedited, and "
            "it authorizes nothing by itself - brief §18/§19 route this into a Tier A V3 "
            "preregistration decision, not into a live re-run."
        ),
    }


def main() -> None:
    run_id = sys.argv[1] if len(sys.argv) > 1 else D4_3A_RUN_ID
    report = replay(run_id)
    REPLAY_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = REPLAY_ROOT / f"d4_3r_audit_alignment_replay-{stamp}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({
        "d4_3a_verdict": report["d4_3a_verdict"],
        "d4_3r_counterfactual_verdict": report["d4_3r_counterfactual_verdict"],
        "changed_gates": report["changed_gates"],
        "unexpected_changes": report["unexpected_changes"],
        "out_path": str(out_path),
    }, indent=2))


if __name__ == "__main__":
    main()
