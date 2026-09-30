"""D4.1 manual-audit harness: puts each audited D4 claim next to the actual text it cites.

Read-only, offline, zero model calls. This tool decides nothing - it assembles the evidence a human
read needs, and the verdict is recorded by the auditor and fed into
`evaluate_d4_1_gates(unknown_discipline_violations=..., unsupported_priced_in_claims=...)`.

WHY THE SELECTION RULE IS IN THIS FILE. `d4_1_contract.py` froze the pilot sample, the budget and
the E1-E8 thresholds, but it defined no manual-audit subset - D3.3's contract did
(`D3_3_MANUAL_AUDIT_TICKERS`) and D4.1's does not. Brief §22 therefore requires the rule to be
frozen deterministically BEFORE any result is read, and editing the frozen preregistration after the
fact is exactly what §0 forbids. So the rule is declared here, as an addendum, written before the
first D4 final output existed:

  COMPANIES: all six Tier B issuers. The sample is six; auditing every one of them is strictly
  stronger than any subset rule and needs no selection seed at all.

  PER COMPANY, ALWAYS AUDITED (these carry the gap conclusion, so sampling them would sample the
  thing under test): every `gap_rationale` claim; the whole `priced_in_assessment` with its
  evidence_ids and limitations; every `why_now` item and its claims; every `guidance_assessments`
  entry; every `result_vs_guidance` entry; every `management_signal_changes` entry; every
  `conflicts` entry.

  PLUS: the first `SAMPLED_NUMERIC_CLAIMS` remaining material claims whose text contains a digit,
  ordered by `sha256("H_V2_D4_1_MANUAL_AUDIT_V1:<ticker>|<json path>")` ascending - the same hash
  convention D3.1 §J, D3.2 §I.1 and D3.3's own manual audit each used.

Nine dimensions, from brief §22: D3 factual correctness, expectation-evidence correctness, gap-claim
support, citation precision, numeric fidelity, UNKNOWN discipline, consensus discipline, priced-in
support, why-now support.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.expectation.code_facts import build_code_fact_index
from app.backtest.strategy_h_v2.expectation.expectation_state import (
    derive_expectation_knowledge_state,
)
from app.backtest.strategy_h_v2.expectation.evidence_schema import ExpectationEvidenceBundleV1
from app.dev.audit_strategy_h_v2_d4_1 import MATERIAL_TYPES, iter_claims
from app.dev.run_strategy_h_v2_d4_1 import ANALYSES_ROOT, PACKAGES_DIR

SELECTION_SEED = "H_V2_D4_1_MANUAL_AUDIT_V1"
SAMPLED_NUMERIC_CLAIMS = 6
CHUNK_WINDOW = 2600

ALWAYS_AUDITED_PREFIXES = (
    "root.gap_rationale",
    "root.priced_in_assessment",
    "root.why_now",
    "root.market_expectation_evidence.guidance_assessments",
    "root.market_expectation_evidence.result_vs_guidance",
    "root.market_expectation_evidence.management_signal_changes",
    "root.conflicts",
)

AUDIT_DIMENSIONS = (
    "D3 factual correctness", "Expectation evidence correctness", "Gap claim support",
    "Citation precision", "Numeric fidelity", "UNKNOWN discipline", "Consensus discipline",
    "Priced-in support", "Why-now support",
)


def _rank(ticker: str, path: str) -> str:
    return hashlib.sha256(f"{SELECTION_SEED}:{ticker}|{path}".encode()).hexdigest()


def select_claims(ticker: str, output: dict) -> list[tuple[str, dict]]:
    always, rest = [], []
    for path, claim in iter_claims(output):
        if claim.get("claim_type") not in MATERIAL_TYPES:
            continue
        if any(path.startswith(prefix) for prefix in ALWAYS_AUDITED_PREFIXES):
            always.append((path, claim))
        elif any(ch.isdigit() for ch in claim["text"]):
            rest.append((path, claim))
    rest.sort(key=lambda item: _rank(ticker, item[0]))
    return always + rest[:SAMPLED_NUMERIC_CLAIMS]


def render(ticker: str, record: dict, package: AIResearchInputV1,
           bundle: ExpectationEvidenceBundleV1) -> str:
    output = record["final_output"]
    chunks = {c.evidence_id: c for c in package.chunks}
    excerpts = {}
    for block in (bundle.guidance, bundle.earnings_history,
                  bundle.management_expectation_signals):
        for excerpt in block.excerpts:
            excerpts[excerpt.evidence_id] = excerpt
    facts = build_code_fact_index(bundle,
                                  research_facts=package.evidence_bundle.fundamental_changes)

    lines = [
        "=" * 100,
        f"{ticker}  gap={output['expectation_gap']} "
        f"confidence={output['expectation_gap_confidence']} "
        f"ceiling={output['confidence_ceiling']} "
        f"priced_in={output['priced_in_assessment']['state']}",
        f"rules fired: {output['applied_contract_rules']}",
        f"d3 tokens: durability={output['fundamental_reality_summary']['d3_growth_durability_state']} "
        f"max_stage={output['fundamental_reality_summary']['d3_future_business_max_stage']}",
        f"consensus_status={output['market_expectation_evidence']['consensus_status']} "
        f"estimate_revisions_status={output['market_expectation_evidence']['estimate_revisions_status']}",
        # D4-S §19. Printed from the authoritative object, not re-derived here, so a manual reviewer
        # and the runtime validator cannot be reading two different answers to "may this output
        # state a market expectation at all?".
        f"expectation knowledge state: {json.dumps(derive_expectation_knowledge_state(bundle).to_dict(), ensure_ascii=False)}",
        f"limitations: {json.dumps(output['limitations'], ensure_ascii=False)}",
        f"unknown_fields: {json.dumps(output['unknown_fields'], ensure_ascii=False)}",
        "=" * 100,
    ]
    for path, claim in select_claims(ticker, output):
        lines.append(f"\n--- {path}  [{claim['claim_type']}]")
        lines.append(f"CLAIM: {claim['text']}")
        ids = ([claim["evidence_id"]] if claim.get("evidence_id") else []) + list(
            claim.get("evidence_ids") or [])
        for evidence_id in ids:
            if evidence_id in facts:
                fact = facts[evidence_id]
                lines.append(f"CODE FACT {evidence_id} = {fact.value!r} [{fact.unit}] "
                             f"- {fact.description}")
            elif evidence_id in excerpts:
                lines.append(f"BUNDLE EXCERPT {evidence_id}:\n"
                             f"    {excerpts[evidence_id].text[:CHUNK_WINDOW]}")
            elif evidence_id in chunks:
                lines.append(f"PACKAGE CHUNK {evidence_id}:\n"
                             f"    {chunks[evidence_id].text[:CHUNK_WINDOW]}")
            else:
                lines.append(f"UNRESOLVED evidence_id {evidence_id}")
    return "\n".join(lines)


def main() -> None:
    run_id = sys.argv[1]
    tickers = sys.argv[2:] or None
    run_dir = ANALYSES_ROOT / run_id
    for ticker_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        if tickers and ticker_dir.name not in tickers:
            continue
        bundle = ExpectationEvidenceBundleV1.model_validate_json(
            (ticker_dir / "expectation_evidence.json").read_text())
        package = AIResearchInputV1.model_validate_json(
            (PACKAGES_DIR / f"{ticker_dir.name}.json").read_text())
        for path in sorted(ticker_dir.glob("*.json")):
            if path.name == "expectation_evidence.json":
                continue
            record = json.loads(path.read_text())
            if record.get("final_output"):
                print(render(ticker_dir.name, record, package, bundle))


if __name__ == "__main__":
    main()
