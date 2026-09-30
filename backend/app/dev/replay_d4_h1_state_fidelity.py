"""H-V2-D4-H1 offline replay: `CODE_OWNED_STATE_FIDELITY` under the S1/S2 semantic repair, measured on
the same frozen corpus D4-H measured V1 on. Zero live calls, zero cost.

The question this exists to answer is a PREREGISTERED one. `H_V2_D4_H_PRE_TIER_B_INTEGRITY_
HARDENING_V1.md` §K wrote down, before any repair was implemented:

    Predicted post-revision measurement on the same corpus: 0 violations of 38 eligible.
    That prediction is what makes the revision falsifiable, and it must be checked by re-running
    this replay after the revision lands rather than assumed.

So this file measures exactly that, on exactly D4-H's denominator, and the prediction is not edited.
`d4_h_eligible_claims` in the gate's report is V1's claim-level eligibility rule kept verbatim -
cites >= 1 STATE_TOKEN fact AND names >= 1 recognized state token - specifically so the 38 stays the
38. H1's own unit of adjudication is the ASSERTION, and both denominators are reported side by side
rather than one being quietly substituted for the other.

Also reported, because it is the only way to see whether the repair closed the mechanisms it claimed
to: each of D4-H §H's eight flagged tokens, resolved individually, with the reason it is no longer a
violation (or, if it still is, that it still is). Those eight are inlined below as the D4-H document
recorded them; they are not re-derived, because V1's matcher no longer exists to re-derive them with.

Nothing here edits a historical verdict. D4.3A, Final Tier A V3, D4-S1 and D4-H's own measurement all
stand: their documents are unedited, their stored `*.gateaudit.json` files are read-only inputs, and
D4-H's `d4_h_integrity_hardening_replay-20260930T042900Z.json` remains on disk as what V1 measured.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sys

from app.backtest.strategy_h_v2.expectation.d4_2_contract import D4_2_ROOT
from app.backtest.strategy_h_v2.expectation.state_fidelity import GATE_ID
from app.dev.audit_strategy_h_v2_d4_2 import audit_run
from app.dev.replay_d4_h_integrity_hardening import AUTHORITATIVE_RUNS, REPLAY_ROOT

#: The prediction, copied from `H_V2_D4_H_PRE_TIER_B_INTEGRITY_HARDENING_V1.md` §K and §I. Frozen
#: before S1/S2 were implemented; the H1 brief §12 forbids changing it after seeing the result.
FROZEN_PREDICTION = {"d4_h_eligible_claims": 38, "d4_h_violating_claims": 0}

D4_3A = "D4_2_A-20260929T072105Z"
V3 = "D4_2_A-20260930T012115Z"

#: D4-H §H's table of eight flagged tokens, verbatim, as `(run id, ticker, claim path, token,
#: mechanism)`. The V1 mechanism each was attributed to is recorded beside it, so "the repair closed
#: the mechanism it claimed to" is checkable rather than asserted. S1 = polarity; S2 = authority
#: derived from the claim's citations; S1+S2 = both applied to the same token.
#:
#: Keyed on the RUN ID, not on the document's prose label. The first version of this file keyed on the
#: label and silently missed all five D4.3A rows, which then reported `still_a_violation: False`
#: because nothing was found to violate anything - a lookup miss reading as a closure. `located` below
#: and `d4_h_flagged_tokens_not_located` exist so that failure mode cannot recur silently.
D4_H_FLAGGED_TOKENS: tuple[tuple[str, str, str, str, str], ...] = (
    (D4_3A, "BSY", "root.fundamental_reality_summary.improvement_claims[4]", "ACCELERATING", "S1"),
    (D4_3A, "BSY", "root.gap_rationale[2]", "IMPROVING", "S1"),
    (D4_3A, "BSY", "root.gap_rationale[2]", "DETERIORATING", "S2"),
    (D4_3A, "GOOG", "root.gap_rationale[1]", "IMPROVING", "S1+S2"),
    (D4_3A, "GOOG", "root.gap_rationale[6]", "IMPROVING", "S2"),
    (V3, "BSY", "root.fundamental_reality_summary.improvement_claims[3]", "IMPROVING", "S1"),
    (V3, "BSY", "root.gap_rationale[2]", "DECELERATING", "S2"),
    (V3, "BSY", "root.gap_rationale[2]", "DETERIORATING", "S2"),
)

#: How a surviving violation must be labelled if there is one. H1 brief §13: report each, classify it,
#: and do NOT expand a rule in the same step to make it go away.
TRUE_STATE_DEFECT = "TRUE_STATE_DEFECT"
SEMANTIC_FALSE_POSITIVE = "SEMANTIC_FALSE_POSITIVE"
UNRESOLVED = "UNRESOLVED"


def replay() -> dict:
    runs: list[dict] = []
    assertions_by_key: dict[tuple[str, str, str, str], list[dict]] = {}

    for run_id, label, document in AUTHORITATIVE_RUNS:
        report = audit_run(run_id)
        candidates: list[dict] = []
        for candidate in report["candidates"]:
            defects = candidate["defects"]
            state = defects["code_owned_state_fidelity"]
            assert state["gate"] == GATE_ID
            for claim in state["claims"]:
                for assertion in claim["assertions"]:
                    key = (run_id, candidate["ticker"], claim["path"], assertion["state"])
                    assertions_by_key.setdefault(key, []).append(assertion)
            candidates.append({
                "ticker": candidate["ticker"],
                "authority": defects["code_owned_state_authority"],
                "state_fact_inventory": defects["code_owned_state_fact_inventory"],
                "status": state["status"],
                "claims_naming_a_state": state["claims_naming_a_state"],
                "assertions_total": state["assertions_total"],
                "assertions_evaluated": state["assertions_evaluated"],
                "assertions_passed": state["assertions_passed"],
                "assertions_failed": state["assertions_failed"],
                "not_evaluated_by_reason": state["not_evaluated_by_reason"],
                "polarity_counts": state["polarity_counts"],
                "d4_h_eligible_claims": state["d4_h_eligible_claims"],
                "d4_h_violating_claims": state["d4_h_violating_claims"],
                "violating_claims": state["violating_claims"],
            })
        runs.append({"run_id": run_id, "label": label, "authoritative_document": document,
                     "candidates": candidates})

    def total(key: str) -> int:
        return sum(c[key] for run in runs for c in run["candidates"])

    #: Each of D4-H's eight, resolved. A token that is no longer a violation is reported with the
    #: outcome and reason the new contract gave the assertion that carries it, so the mechanism that
    #: closed it is visible and not inferred.
    resolved = []
    for run_id, ticker, path, token, mechanism in D4_H_FLAGGED_TOKENS:
        found = assertions_by_key.get((run_id, ticker, path, token), [])
        resolved.append({
            "run_id": run_id,
            "run": next(label for rid, label, _ in AUTHORITATIVE_RUNS if rid == run_id),
            "ticker": ticker, "path": path, "state": token,
            "d4_h_mechanism": mechanism,
            "located": bool(found),
            "found_as_occurrences": len(found),
            "outcomes": [
                {"polarity": a["polarity"], "metric": a["metric"], "binding": a["binding"],
                 "authoritative_state": a["authoritative_state"], "status": a["status"],
                 "reason": a["reason"]}
                for a in found
            ],
            "still_a_violation": any(a["status"] == "FAIL" for a in found),
        })

    # A row that was not located is not a closure, and must never be counted as one: the token may
    # have moved, the path may have changed, or the lookup may be wrong. Any of those is UNRESOLVED.
    not_located = [row for row in resolved if not row["located"]]

    measured = {"d4_h_eligible_claims": total("d4_h_eligible_claims"),
                "d4_h_violating_claims": total("d4_h_violating_claims")}
    surviving = [{"run": run["label"], "ticker": c["ticker"], "claim": claim}
                 for run in runs for c in run["candidates"] for claim in c["violating_claims"]]

    by_reason: dict[str, int] = {}
    for run in runs:
        for c in run["candidates"]:
            for reason, count in c["not_evaluated_by_reason"].items():
                by_reason[reason] = by_reason.get(reason, 0) + count

    return {
        "schema": "H_V2_D4_H1_OFFLINE_REPLAY_V1",
        "produced_at": datetime.now(timezone.utc).isoformat(),
        "live_calls": 0,
        "live_cost_usd": 0.0,
        "gate": GATE_ID,
        "frozen_prediction": FROZEN_PREDICTION,
        "measured_on_the_frozen_prediction_denominator": measured,
        "frozen_prediction_confirmed": measured == FROZEN_PREDICTION,
        "assertion_level": {
            "claims_naming_a_state": total("claims_naming_a_state"),
            "assertions_total": total("assertions_total"),
            "assertions_evaluated": total("assertions_evaluated"),
            "assertions_passed": total("assertions_passed"),
            "assertions_failed": total("assertions_failed"),
            "not_evaluated_by_reason": dict(sorted(by_reason.items())),
        },
        "d4_h_flagged_tokens_resolved": resolved,
        "d4_h_flagged_tokens_located": sum(1 for row in resolved if row["located"]),
        "d4_h_flagged_tokens_not_located": len(not_located),
        "d4_h_flagged_tokens_still_violating": sum(
            1 for row in resolved if row["still_a_violation"]),
        "surviving_violations": surviving,
        "surviving_violation_classification_required": bool(surviving),
        "runs": runs,
        "means": (
            "Whether the S1/S2 repair meets the prediction D4-H froze before it was implemented, on "
            "D4-H's own 38-claim denominator. It authorizes nothing: the gate is adjudicated offline "
            "and is wired to no M gate, so no Tier A or D4-S1 verdict is touched either way."
        ),
    }


def main() -> None:
    report = replay()
    REPLAY_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPLAY_ROOT / f"d4_h1_state_fidelity_replay-{stamp}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({
        "frozen_prediction": report["frozen_prediction"],
        "measured": report["measured_on_the_frozen_prediction_denominator"],
        "frozen_prediction_confirmed": report["frozen_prediction_confirmed"],
        "assertion_level": report["assertion_level"],
        "d4_h_flagged_tokens_located": report["d4_h_flagged_tokens_located"],
        "d4_h_flagged_tokens_not_located": report["d4_h_flagged_tokens_not_located"],
        "d4_h_flagged_tokens_still_violating": report["d4_h_flagged_tokens_still_violating"],
    }, indent=2))
    print(f"\nwritten: {path}")
    if report["d4_h_flagged_tokens_not_located"]:
        print(f"{report['d4_h_flagged_tokens_not_located']} of D4-H's flagged tokens were NOT LOCATED "
              f"- {UNRESOLVED}, not closed", file=sys.stderr)
    if report["surviving_violations"]:
        print(f"{len(report['surviving_violations'])} SURVIVING VIOLATION(S) - classify each as "
              f"{TRUE_STATE_DEFECT} / {SEMANTIC_FALSE_POSITIVE} / {UNRESOLVED}; do NOT expand a rule "
              "in this step", file=sys.stderr)


if __name__ == "__main__":
    main()
