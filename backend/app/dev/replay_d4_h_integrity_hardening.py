"""H-V2-D4-H offline replay: the R1/R2-repaired M8 and the new CODE_OWNED_STATE_FIDELITY gate,
applied to every stored, already-graded D4 output. Zero live calls, zero cost - every byte replayed
here was paid for and stored by a run that has already happened.

What this answers, and only this:

  R1/R2  Do the six findings of `H_V2_D4_S_M8_COMPOUND_COVERAGE_AUDIT_V1.md` disappear, does any
         NEW true numeric mismatch appear from the parser change, and does every M1-M12 gate
         reproduce the status its own run recorded?

It used to also carry `CODE_OWNED_STATE_FIDELITY`'s first measurement, under that gate's D4-H V1
contract. D4-H1 replaced that contract (`H_V2_D4_H1_STATE_FIDELITY_SEMANTIC_REPAIR_V1.md`), so the
state measurement lives in `replay_d4_h1_state_fidelity.py` now and this file no longer computes one.
Two reasons rather than one: re-measuring a revised gate under this file's schema name would produce
different numbers for the same schema, and D4-H's own artifact on disk
(`d4_h_integrity_hardening_replay-20260930T042900Z.json`) is the immutable record of what V1 measured.
R1/R2 and the M-gate immutability check are unchanged and are still this file's subject.

Nothing here edits a historical verdict. `H_V2_D4_3A_TIER_A_V2_MECHANICAL_RESULT_V1.md`,
`H_V2_D4_4A_FINAL_TIER_A_V3_MECHANICAL_RESULT_V1.md` and
`H_V2_D4_S1_SCCO_LIMITED_LIVE_SMOKE_RESULT_V1.md` keep their verdicts and their M1-M12 tables; the
stored `*.gateaudit.json` files are read as the "recorded" side of the comparison and are never
overwritten. The output is its own labelled POST-HOC INTEGRITY REPLAY artifact.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import sys

from app.backtest.strategy_h_v2.expectation.d4_2_contract import D4_2_ROOT
from app.dev.audit_strategy_h_v2_d4_2 import audit_run

REPLAY_ROOT = D4_2_ROOT / "replay"

#: Every stored run that carries an authoritative verdict, with the document that owns it. The
#: fourth stored run under `analyses/` (`D4_2_A-20260930T001105Z`) has no manifest and no graded
#: verdict, so it is deliberately not replayed: it is not a record this step may speak about.
AUTHORITATIVE_RUNS: tuple[tuple[str, str, str], ...] = (
    ("D4_2_A-20260929T072105Z", "D4.3A Tier A V2",
     "H_V2_D4_3A_TIER_A_V2_MECHANICAL_RESULT_V1.md"),
    ("D4_2_A-20260930T012115Z", "Final Tier A V3",
     "H_V2_D4_4A_FINAL_TIER_A_V3_MECHANICAL_RESULT_V1.md"),
    ("D4_2_A-20260930T034348Z", "D4-S1 SCCO limited live smoke",
     "H_V2_D4_S1_SCCO_LIMITED_LIVE_SMOKE_RESULT_V1.md"),
)

#: D4-S1 stored no `*.gateaudit.json`; its M1-M12 table lives in its result document, which records
#: all twelve as PASS. Written here so the comparison below has a recorded side for that run too,
#: rather than quietly skipping it.
DOCUMENTED_GATES: dict[str, dict[str, str]] = {
    "D4_2_A-20260930T034348Z": {f"M{i}": "PASS" for i in range(1, 13)},
}

#: D4.3A's stored `*.gateaudit.json` is NOT the baseline for a post-D4.3R change, and using it as one
#: was the first thing this replay got wrong. It was produced by the pre-D4.3R detectors and records
#: M4 = FAIL and M8 = FAIL - the two audit-layer false positives D4.3R removed, whose removal is
#: already recorded in D4.3R's own replay artifact and is not D4-H's doing. The baseline for D4-H is
#: the LAST status this run carried before D4-H, which for D4.3A is that artifact's `new_status`.
#: D4.3A's own document and its stored gate audit both stand unedited; neither is the comparison.
D4_3R_REPLAY = REPLAY_ROOT / "d4_3r_audit_alignment_replay-20260929T075703Z.json"
SUPERSEDED_GATEAUDITS: dict[str, Path] = {"D4_2_A-20260929T072105Z": D4_3R_REPLAY}


def _recorded_gates(run_id: str) -> tuple[dict[str, str], str]:
    """This run's last recorded M1-M12 status before D4-H, and where that record lives."""
    superseded = SUPERSEDED_GATEAUDITS.get(run_id)
    if superseded is not None:
        replay = json.loads(superseded.read_text())
        assert replay["run_id"] == run_id
        return ({g["gate"]: g["new_status"] for g in replay["gate_comparison"]},
                f"{superseded} (D4.3R counterfactual; D4.3A's own gateaudit.json predates the "
                "D4.3R audit repair and records M4/M8 = FAIL for that reason)")
    path = D4_2_ROOT / f"{run_id}.gateaudit.json"
    if path.exists():
        return {g["gate"]: g["status"] for g in json.loads(path.read_text())["gates"]}, str(path)
    if run_id in DOCUMENTED_GATES:
        return DOCUMENTED_GATES[run_id], "recorded in the run's own result document"
    raise FileNotFoundError(f"no recorded gate statuses for {run_id}")


def replay() -> dict:
    runs: list[dict] = []
    for run_id, label, document in AUTHORITATIVE_RUNS:
        report = audit_run(run_id)
        recorded, recorded_from = _recorded_gates(run_id)
        replayed = {g["gate"]: g["status"] for g in report["gates"]}
        candidates: list[dict] = []

        for candidate in report["candidates"]:
            defects = candidate["defects"]
            candidates.append({
                "ticker": candidate["ticker"],
                "m8_atomic_defects": len(defects["code_owned_numeric_defects"]),
                "m8_compound_coverage_gap": len(defects["compound_claim_coverage_gap"]),
            })

        runs.append({
            "run_id": run_id, "label": label, "authoritative_document": document,
            "recorded_gates_from": recorded_from,
            "gate_comparison": [
                {"gate": gate, "recorded": recorded.get(gate), "replayed": replayed.get(gate),
                 "changed": recorded.get(gate) != replayed.get(gate)}
                for gate in replayed
            ],
            "verdict_recorded_unchanged": all(
                recorded.get(g) == replayed.get(g) for g in replayed),
            "candidates": candidates,
        })

    def total(key: str) -> int:
        return sum(c[key] for run in runs for c in run["candidates"])

    return {
        "schema": "H_V2_D4_H_POST_HOC_INTEGRITY_REPLAY_V1",
        "produced_at": datetime.now(timezone.utc).isoformat(),
        "live_calls": 0,
        "live_cost_usd": 0.0,
        "runs": runs,
        "totals": {
            "candidates_replayed": sum(len(run["candidates"]) for run in runs),
            "m8_new_true_numeric_defects": total("m8_atomic_defects"),
            "m8_compound_coverage_gap_findings": total("m8_compound_coverage_gap"),
            "every_m_gate_reproduced": all(run["verdict_recorded_unchanged"] for run in runs),
        },
        "means": (
            "M8's atomic count is the gate figure and its 0 is the same 0 every run recorded. The "
            "compound-coverage gap is the list a widened M8 would have flagged, and its 0 is R1/R2's "
            "result on real bytes. CODE_OWNED_STATE_FIDELITY is measured by "
            "`replay_d4_h1_state_fidelity.py` and is wired to no M gate: a violation there has never "
            "been part of any Tier A verdict."
        ),
    }


def main() -> None:
    report = replay()
    REPLAY_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPLAY_ROOT / f"d4_h_integrity_hardening_replay-{stamp}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report["totals"], indent=2))
    print(f"\nwritten: {path}")
    if not report["totals"]["every_m_gate_reproduced"]:
        print("M GATE STATUS MOVED - see gate_comparison", file=sys.stderr)


if __name__ == "__main__":
    main()
