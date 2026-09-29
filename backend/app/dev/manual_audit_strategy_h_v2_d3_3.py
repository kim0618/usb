"""D3.3 L3 manual-audit harness: puts each audited claim next to the actual text of the chunk it
cites, so a human can judge content accuracy, citation precision, numeric fidelity, qualification
preservation and Future Business staging against the real filing text.

Read-only, offline, zero model calls. This tool does not decide anything - it only assembles the
evidence a manual read needs. The verdict is recorded by the auditor in the result document and fed
into `evaluate_d3_3_gates(material_content_defects=...)`.

Selection per company, deterministic and declared (the preregistration froze WHICH companies, not
how many claims each): every `future_business` item and its claims (L4 is a core gate and the direct
target of Prompt V2's changes), every `evidence_conflicts` entry, plus the first N material claims
carrying a number by `sha256("H_V2_D3_3_MANUAL_AUDIT_V1:<ticker>|<json path>")` ascending - the same
hash convention D3.1 §J / D3.2 §I.1 used for R10.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.dev.run_strategy_h_v2_d3_3 import PACKAGES_DIR

MATERIAL_TYPES = {"FACT", "INTERPRETATION", "INFERENCE"}
SELECTION_SEED = "H_V2_D3_3_MANUAL_AUDIT_V1"
NUMERIC_CLAIMS_PER_TICKER = 8
INFERENCE_CLAIMS_PER_TICKER = 3
CHUNK_WINDOW = 2600
#: Hedging vocabulary Prompt V2 requires the model to preserve when its source uses it (brief §10 /
#: preregistration §9's qualification-preservation dimension).
QUALIFIERS = ("approximately", "about", "up to", "expected", "may", "could", "subject to",
              "non-binding", "preliminary", "estimated", "anticipated", "intends")


def iter_claims(node, path: str = ""):
    if isinstance(node, dict):
        if "claim_type" in node and "text" in node:
            yield path, node
        for key, value in node.items():
            yield from iter_claims(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_claims(value, f"{path}[{index}]")


def cited_ids(claim: dict) -> list[str]:
    eids = claim.get("evidence_ids") or []
    if eids:
        return list(eids)
    return [claim["evidence_id"]] if claim.get("evidence_id") else []


def _key(ticker: str, path: str) -> str:
    return hashlib.sha256(f"{SELECTION_SEED}:{ticker}|{path}".encode()).hexdigest()


def audit_dump(ticker: str, output: dict, package: AIResearchInputV1) -> str:
    chunks = {c.evidence_id: c for c in package.chunks}
    claims = [(p, c) for p, c in iter_claims(output) if c.get("claim_type") in MATERIAL_TYPES]
    numeric = sorted([pc for pc in claims if re.search(r"\d", pc[1]["text"])],
                     key=lambda pc: _key(ticker, pc[0]))[:NUMERIC_CLAIMS_PER_TICKER]
    inference = sorted([pc for pc in claims if pc[1]["claim_type"] == "INFERENCE"],
                       key=lambda pc: _key(ticker, pc[0]))[:INFERENCE_CLAIMS_PER_TICKER]
    picked = list(numeric)
    for pc in inference:
        if pc not in picked:
            picked.append(pc)

    out: list[str] = []
    w = out.append
    w(f"########## {ticker}: {len(claims)} material claims total, auditing {len(picked)} "
      f"selected + all future_business + all conflicts")
    w(f"research_completeness={output.get('research_completeness')} "
      f"unknown_fields={len(output.get('unknown_fields') or [])} "
      f"open_questions={len(output.get('open_questions') or [])}")

    for path, claim in picked:
        w(f"\n=== {path} [{claim['claim_type']}/{claim.get('confidence')}] form="
          f"{'COMPOUND' if claim.get('evidence_ids') else 'ATOMIC'}")
        w(f"CLAIM: {claim['text']}")
        for eid in cited_ids(claim):
            chunk = chunks.get(eid)
            if chunk is None:
                w(f"  CITES {eid} -> *** NOT IN PACKAGE ***")
                continue
            w(f"  CITES {eid} ({chunk.source_type.value}, chunk "
              f"{chunk.chunk_index}/{chunk.chunk_count}, section={chunk.section})")
            w(f"  CHUNK TEXT: {chunk.text[:CHUNK_WINDOW]}")
        present = [q for q in QUALIFIERS if q in claim["text"].lower()]
        w(f"  qualifiers in claim: {present or 'none'}")

    w(f"\n########## {ticker} FUTURE BUSINESS ({len(output.get('future_business') or [])} items)")
    for i, item in enumerate(output.get("future_business") or []):
        flags = {k: item[k] for k in ("current_revenue_evidence", "order_backlog_evidence",
                                      "customer_evidence", "capacity_evidence", "margin_evidence")}
        w(f"\n--- future_business[{i}] name={item.get('name')!r}")
        w(f"    stage={item['stage']} evidence_strength={item.get('evidence_strength')} "
          f"flags={sum(flags.values())} {flags}")
        w(f"    missing_evidence={item.get('missing_evidence')}")
        w(f"    sources={item.get('sources')}")
        for j, claim in enumerate(item.get("claims") or []):
            w(f"    claim[{j}] [{claim['claim_type']}] {claim['text']}")
            for eid in cited_ids(claim):
                chunk = chunks.get(eid)
                w(f"      CITES {eid}")
                if chunk is not None:
                    w(f"      CHUNK: {chunk.text[:1400]}")

    conflicts = output.get("evidence_conflicts") or []
    w(f"\n########## {ticker} CONFLICTS ({len(conflicts)})")
    for i, c in enumerate(conflicts):
        w(f"--- conflict[{i}] topic={c.get('topic')!r} status={c.get('resolution_status')} "
          f"confidence={c.get('confidence')}")
        w(f"    description: {c.get('description')}")
        for eid in c.get("evidence_ids", []):
            chunk = chunks.get(eid)
            w(f"    CITES {eid} {'(OK)' if chunk else '(*** NOT IN PACKAGE ***)'}")
            if chunk is not None:
                w(f"      CHUNK: {chunk.text[:1200]}")
    return "\n".join(out)


def main() -> None:
    manifest_path = Path(sys.argv[1])
    ticker = sys.argv[2]
    attempts_root = Path("data/runtime/strategy_h_v2/d3_3/attempts")
    manifest = json.loads(manifest_path.read_text())
    row = next(r for r in manifest["results"] if r["ticker"] == ticker)
    record = json.loads(
        (attempts_root / manifest["run_id"] / ticker / f"{row['attempt_id']}.json").read_text())
    if record["final_status"] != "OK":
        print(f"{ticker}: final_status={record['final_status']} - nothing to audit")
        return
    package = AIResearchInputV1.model_validate_json((PACKAGES_DIR / f"{ticker}.json").read_text())
    print(audit_dump(ticker, record["final_output"], package))


if __name__ == "__main__":
    main()
