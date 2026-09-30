"""D4-S §17: apply the structured expectation state to stored bytes, for $0 and zero live calls.

What this answers, and only this:

  1. Would structured validation reject honest absence language?
  2. Would an unsupported affirmative expectation assertion still be caught?
  3. Do the audit-only unsourced findings classify deterministically?

What it deliberately does NOT do. It does not rewrite a stored response as though a new prompt had
produced it, it does not re-run a model, and it does not restate either run's verdict. D4.3A's
`MECHANICAL NOT READY` and Final Tier A V3's `MECHANICAL READY / Initial Validity UNSTABLE` both
stand untouched as historical evidence; this reads their stored text and reports what a different
enforcement layer would have said about the same bytes.

The load-bearing measurement is over the INITIAL responses, not the final ones. Final Tier A V3's
finals were already clean (M3/M4 PASS, 0 fabricated consensus); the two defects that made its
Initial Validity read UNSTABLE were in the first-attempt text, so that is where a change to the
enforcement layer either shows up or does not.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.expectation.consensus_language import (
    CONSENSUS_LANGUAGE_CONTRACT_VERSION,
    asserted_consensus_findings,
)
from app.backtest.strategy_h_v2.expectation.evidence_schema import ExpectationEvidenceBundleV1
from app.backtest.strategy_h_v2.expectation.expectation_state import (
    EXPECTATION_STATE_CONTRACT_VERSION,
    affirmative_expectation_findings,
    derive_expectation_knowledge_state,
)
from app.backtest.strategy_h_v2.research.d3_3_contract import PACKAGES_DIR
from app.backtest.strategy_h_v2.research.validate import ExtractionError, extract_json_object
from app.dev.audit_strategy_h_v2_d4_1 import (
    CLAIM_SOURCING_A,
    CLAIM_SOURCING_B,
    CLAIM_SOURCING_C,
    classify_claim_sourcing,
    iter_claims,
    iter_prose,
)
from app.dev.run_strategy_h_v2_d4_2 import ANALYSES_ROOT

#: Both stored Tier A runs under the frozen D4 contract. D4.3A first, so the two runs' shared
#: behaviour is visible rather than inferred from one.
RUNS = ("D4_2_A-20260929T072105Z", "D4_2_A-20260930T012115Z")
LABELS = {"D4_2_A-20260929T072105Z": "D4.3A", "D4_2_A-20260930T012115Z": "Final Tier A V3"}


def _records(run_id: str) -> list[tuple[str, dict, ExpectationEvidenceBundleV1]]:
    out = []
    for ticker_dir in sorted(p for p in (ANALYSES_ROOT / run_id).iterdir() if p.is_dir()):
        bundle = ExpectationEvidenceBundleV1.model_validate_json(
            (ticker_dir / "expectation_evidence.json").read_text())
        for path in sorted(ticker_dir.glob("*.json")):
            if path.name == "expectation_evidence.json":
                continue
            out.append((ticker_dir.name, json.loads(path.read_text()), bundle))
    return out


def _outputs(record: dict) -> list[tuple[str, dict]]:
    """Every response stored for one candidate, as (role, parsed object).

    The initial response is read from `initial_raw_response.raw_text` and parsed here rather than
    taken from a stored parsed copy, because a stored parsed copy only exists when validation
    passed - which is exactly the case this replay is about.
    """
    out: list[tuple[str, dict]] = []
    raw = (record.get("initial_raw_response") or {}).get("raw_text")
    if raw:
        try:
            out.append(("initial", extract_json_object(raw)))
        except (ExtractionError, ValueError):
            pass
    for index, rnd in enumerate(record.get("repair_rounds") or []):
        raw = (rnd.get("raw_repair_response") or {}).get("raw_text")
        if raw:
            try:
                out.append((f"repair#{index}", extract_json_object(raw)))
            except (ExtractionError, ValueError):
                pass
    if record.get("final_output"):
        out.append(("final", record["final_output"]))
    return out


def replay() -> dict:
    report: dict = {
        "schema": "H_V2_D4_S_OFFLINE_REPLAY_V1",
        "live_calls": 0, "cost_usd": 0.0,
        "expectation_state_contract": EXPECTATION_STATE_CONTRACT_VERSION,
        "consensus_language_contract": CONSENSUS_LANGUAGE_CONTRACT_VERSION,
        "runs": [],
    }
    for run_id in RUNS:
        if not (ANALYSES_ROOT / run_id).exists():
            report["runs"].append({"run_id": run_id, "status": "NOT_STORED"})
            continue
        entries = []
        for ticker, record, bundle in _records(run_id):
            state = derive_expectation_knowledge_state(bundle)
            per_response = []
            for role, output in _outputs(record):
                old_hits, new_hits = [], []
                for path, text in iter_prose(output):
                    for finding in asserted_consensus_findings(text):
                        old_hits.append({"path": path, "trigger": finding.trigger,
                                         "sentence": finding.sentence})
                    for finding in affirmative_expectation_findings(text, state=state):
                        new_hits.append({"path": path, "trigger": finding.trigger,
                                         "sentence": finding.sentence})
                new_sentences = {h["sentence"] for h in new_hits}
                per_response.append({
                    "role": role,
                    "old_rejections": len(old_hits),
                    "new_rejections": len(new_hits),
                    "absence_released": [h for h in old_hits
                                         if h["sentence"] not in new_sentences],
                    "still_rejected": new_hits,
                })
            classes = {CLAIM_SOURCING_A: 0, CLAIM_SOURCING_B: 0, CLAIM_SOURCING_C: 0}
            compound = atomic = 0
            final = record.get("final_output") or {}
            from app.backtest.strategy_h_v2.expectation.code_facts import build_code_fact_index
            package = AIResearchInputV1.model_validate_json(
                (PACKAGES_DIR / f"{ticker}.json").read_text())
            facts = build_code_fact_index(
                bundle, research_facts=package.evidence_bundle.fundamental_changes)
            for _path, cl in iter_claims(final):
                classes[classify_claim_sourcing(cl, code_facts=facts)] += 1
                if len(cl.get("evidence_ids") or []) >= 2:
                    compound += 1
                elif cl.get("source_id") and cl.get("evidence_id"):
                    atomic += 1
            entries.append({
                "ticker": ticker,
                "expectation_state": state.to_dict(),
                "responses": per_response,
                "claim_classes": classes,
                "citation_forms": {"ATOMIC": atomic, "COMPOUND": compound},
            })
        report["runs"].append({"run_id": run_id, "label": LABELS.get(run_id, run_id),
                               "status": "REPLAYED", "candidates": entries})
    return report


def main() -> None:
    report = replay()
    print(f"D4-S offline replay   live_calls={report['live_calls']}  "
          f"cost=${report['cost_usd']:.2f}")
    print(f"expectation state contract : {report['expectation_state_contract']}")
    print(f"consensus language contract: {report['consensus_language_contract']}")
    for run in report["runs"]:
        print("\n" + "=" * 96)
        print(f"{run.get('label', run['run_id'])}   {run['run_id']}   {run['status']}")
        if run["status"] != "REPLAYED":
            continue
        for entry in run["candidates"]:
            state = entry["expectation_state"]
            print(f"\n  {entry['ticker']}   status={state['status']}  "
                  f"claim_allowed={state['market_expectation_claim_allowed']}  "
                  f"forms={entry['citation_forms']}  classes={entry['claim_classes']}")
            for response in entry["responses"]:
                print(f"    {response['role']:9}  old_rejections={response['old_rejections']}  "
                      f"new_rejections={response['new_rejections']}")
                for released in response["absence_released"]:
                    print(f"      RELEASED (honest absence) [{released['trigger']}] "
                          f"{released['path']}")
                    print(f"        {released['sentence']}")
                for kept in response["still_rejected"]:
                    print(f"      STILL REJECTED [{kept['trigger']}] {kept['path']}")
                    print(f"        {kept['sentence']}")
    out = Path("data/runtime/strategy_h_v2/d4_s")
    out.mkdir(parents=True, exist_ok=True)
    target = out / "d4_s_offline_replay.json"
    target.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwritten: {target}")


if __name__ == "__main__":
    sys.exit(main())
