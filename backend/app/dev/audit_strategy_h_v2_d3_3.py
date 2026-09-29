"""D3.3 mechanical gate audit: L4-L7 plus the brief's diagnostics, over the stored attempt records.

Read-only, offline, zero model calls. Uses Validation Contract V2 (`validation_v2.py`) exactly as
frozen - no threshold in this file, every threshold comes from `d3_3_contract.py`. L1/L2 come from
the run manifest (mechanical), L3 is the manual audit and is supplied by the auditor, never inferred
here.

One V2-shape detail that has to be stated rather than left implicit: `ClaimV2` can cite either a
single `evidence_id` (the atomic default) or `evidence_ids` (the declared compound fallback, >= 2).
For the atomic form this audit scopes support to that one chunk, with same-source adjacency reported
separately as a precision defect - D3.2 §J.2's own definition. For the compound form, support found
in ANY of the chunks the claim itself declared is correct by construction: declaring them IS the
claim saying "these together support this sentence". Neither reading is a threshold change; they are
what "the chunk the claim cites" means for each of the two citation forms Schema V2 defines.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1, EvidenceChunk
from app.backtest.strategy_h_v2.research import validation_v2 as v2
from app.backtest.strategy_h_v2.research.schema import FUTURE_BUSINESS_MIN_EVIDENCE_FLAGS
from app.dev.run_strategy_h_v2_d3_3 import PACKAGES_DIR

MATERIAL_TYPES = {"FACT", "INTERPRETATION", "INFERENCE"}
FLAG_FIELDS = ("current_revenue_evidence", "order_backlog_evidence", "customer_evidence",
              "capacity_evidence", "margin_evidence")
#: Restated independently of `schema.py`'s own validator (same principle the D3.1/D3.2 audits used:
#: a gate checked only by the code that enforced it proves nothing).
AUDIT_STAGE_MIN_FLAGS = {"STORY": 0, "EARLY_EVIDENCE": 1, "COMMERCIALIZING": 2,
                        "REAL_BUSINESS": 2, "MATURE": 2, "UNKNOWN": 0}
AUDIT_STAGE_NEEDS_REVENUE_OR_BACKLOG = {"REAL_BUSINESS", "MATURE"}


def iter_claims(node, path: str = "root"):
    if isinstance(node, dict):
        if "claim_type" in node and "text" in node:
            yield path, node
        for key, value in node.items():
            yield from iter_claims(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_claims(value, f"{path}[{index}]")


def iter_text_fields(node, path: str = "root"):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from iter_text_fields(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_text_fields(value, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path, node


class PackageIndex:
    def __init__(self, package: AIResearchInputV1):
        self.by_id: dict[str, EvidenceChunk] = {c.evidence_id: c for c in package.chunks}
        self.by_source: dict[str, dict[int, EvidenceChunk]] = {}
        for c in package.chunks:
            self.by_source.setdefault(c.source_id, {})[c.chunk_index] = c
        self.source_ids = frozenset(c.source_id for c in package.chunks)
        self.evidence_ids = frozenset(self.by_id)

    def text(self, evidence_id: str | None) -> str | None:
        chunk = self.by_id.get(evidence_id) if evidence_id else None
        return chunk.text if chunk else None

    def adjacent_texts(self, evidence_id: str | None) -> tuple[str, ...]:
        chunk = self.by_id.get(evidence_id) if evidence_id else None
        if chunk is None:
            return ()
        siblings = self.by_source.get(chunk.source_id, {})
        return tuple(siblings[i].text for i in (chunk.chunk_index - 1, chunk.chunk_index + 1)
                     if i in siblings)


def _claim_citation_scope(claim: dict, index: PackageIndex) -> tuple[str, tuple[str, ...], str]:
    """Returns (primary_text, adjacent_texts, form) for whichever citation form the claim used."""
    eids = claim.get("evidence_ids") or []
    if eids:
        # Compound form: every declared chunk is primary (see module docstring).
        texts = [index.text(e) or "" for e in eids]
        return "\n".join(texts), (), "COMPOUND"
    eid = claim.get("evidence_id")
    return index.text(eid) or "", index.adjacent_texts(eid), "ATOMIC"


def audit_output(ticker: str, output: dict, index: PackageIndex) -> dict:
    claims = list(iter_claims(output))
    material = [(p, c) for p, c in claims if c.get("claim_type") in MATERIAL_TYPES]

    structural_bad: list[dict] = []
    precision: Counter = Counter()
    citation_defects: list[dict] = []
    numeric_total = numeric_cited = numeric_adjacent = numeric_unsupported = 0
    numeric_defects: list[dict] = []
    compound_claims = 0
    suspected_compound_single_cite = 0

    for path, claim in material:
        eids = claim.get("evidence_ids") or []
        if eids:
            compound_claims += 1
        # R4A structural provenance, per citation form.
        if eids:
            for e in eids:
                if e not in index.evidence_ids:
                    structural_bad.append({"path": path, "evidence_id": e,
                                           "reason": "EVIDENCE_UNKNOWN_TO_CANDIDATE"})
        else:
            status = v2.structural_provenance(
                source_id=claim.get("source_id"), evidence_id=claim.get("evidence_id"),
                candidate_source_ids=index.source_ids, candidate_evidence_ids=index.evidence_ids)
            if status != v2.ProvenanceStatus.OK:
                structural_bad.append({"path": path, "evidence_id": claim.get("evidence_id"),
                                       "reason": status.value})

        primary, adjacent, form = _claim_citation_scope(claim, index)
        result = v2.citation_precision(claim["text"], cited_chunk_text=primary,
                                       adjacent_chunk_texts=adjacent)
        precision[result.status.value] += 1
        for tr in result.token_results:
            numeric_total += 1
            if tr.scope == v2.SupportScope.CITED_CHUNK:
                numeric_cited += 1
            elif tr.scope == v2.SupportScope.ADJACENT_CHUNK:
                numeric_adjacent += 1
            else:
                numeric_unsupported += 1
        if result.status in (v2.PrecisionStatus.UNSUPPORTED, v2.PrecisionStatus.ADJACENT_RECOVERABLE):
            citation_defects.append({
                "path": path, "form": form, "status": result.status.value,
                "evidence_id": claim.get("evidence_id"), "evidence_ids": eids,
                "unsupported_values": [r.token.raw for r in result.unsupported],
                "adjacent_values": [r.token.raw for r in result.adjacent],
                "text": claim["text"][:220],
            })
        if result.status == v2.PrecisionStatus.UNSUPPORTED:
            numeric_defects.append({"path": path, "values": [r.token.raw for r in result.unsupported],
                                    "text": claim["text"][:220]})
        if not eids and v2.is_compound_claim(claim["text"]):
            suspected_compound_single_cite += 1

    # L4: Future Business stage escalation, re-derived from the model's own flags.
    escalations = []
    stage_distribution: Counter = Counter()
    missing_evidence_used = 0
    for i, item in enumerate(output.get("future_business") or []):
        stage = item["stage"]
        stage_distribution[stage] += 1
        flags = [bool(item[k]) for k in FLAG_FIELDS]
        if item.get("missing_evidence"):
            missing_evidence_used += 1
        if sum(flags) < AUDIT_STAGE_MIN_FLAGS.get(stage, 0) or (
            stage in AUDIT_STAGE_NEEDS_REVENUE_OR_BACKLOG and not (flags[0] or flags[1])
        ):
            escalations.append({"index": i, "name": item.get("name"), "stage": stage,
                                "flags": sum(flags)})

    # L7: investment decision leakage, Validation V2 semantics (GAAP context allowed).
    leaks = []
    for path, text in iter_text_fields(output):
        if path.startswith(("root.model", "root.prompt_version", "root.research_id",
                           "root.schema_version", "root.contract_version")):
            continue
        for match in v2.classify_investment_language(text):
            if match.verdict == v2.LanguageVerdict.VIOLATION:
                leaks.append({"path": path, "term": match.term, "context": match.context[:160]})

    unknown_claims = sum(1 for _, c in claims if c.get("claim_type") == "UNKNOWN")
    conflicts = output.get("evidence_conflicts") or []
    conflict_rows = []
    for c in conflicts:
        ids_valid = all(e in index.evidence_ids for e in c.get("evidence_ids", []))
        conflict_rows.append({"topic": c.get("topic"), "resolution_status": c.get("resolution_status"),
                              "n_evidence": len(c.get("evidence_ids", [])), "ids_valid": ids_valid})

    return {
        "ticker": ticker,
        "material_claims": len(material),
        "claims_total": len(claims),
        "unknown_claims": unknown_claims,
        "unknown_fields": len(output.get("unknown_fields") or []),
        "structural_provenance_defects": structural_bad,
        "citation_precision": dict(precision),
        "citation_defects": citation_defects,
        "citation_defective_claims": len(citation_defects),
        "numeric_tokens_total": numeric_total,
        "numeric_tokens_cited_chunk": numeric_cited,
        "numeric_tokens_adjacent_only": numeric_adjacent,
        "numeric_tokens_unsupported": numeric_unsupported,
        "numeric_defect_claims": numeric_defects,
        "future_business_stage_distribution": dict(stage_distribution),
        "future_business_escalations": escalations,
        "future_business_items": len(output.get("future_business") or []),
        "missing_evidence_used": missing_evidence_used,
        "investment_language_violations": leaks,
        "compound_claims_declared": compound_claims,
        "suspected_compound_single_cite": suspected_compound_single_cite,
        "conflicts": conflict_rows,
        "research_completeness": output.get("research_completeness"),
    }


def main() -> None:
    manifest_path = Path(sys.argv[1])
    attempts_root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(
        "data/runtime/strategy_h_v2/d3_3/attempts")
    manifest = json.loads(manifest_path.read_text())
    run_id = manifest["run_id"]

    per_ticker = []
    for row in manifest["results"]:
        ticker = row["ticker"]
        attempt_path = attempts_root / run_id / ticker / f"{row['attempt_id']}.json"
        record = json.loads(attempt_path.read_text())
        if record["final_status"] != "OK" or not record.get("final_output"):
            per_ticker.append({"ticker": ticker, "final_status": record["final_status"],
                               "audited": False})
            continue
        package = AIResearchInputV1.model_validate_json(
            (PACKAGES_DIR / f"{ticker}.json").read_text())
        audit = audit_output(ticker, record["final_output"], PackageIndex(package))
        audit["final_status"] = record["final_status"]
        audit["audited"] = True
        per_ticker.append(audit)

    audited = [a for a in per_ticker if a.get("audited")]
    totals = {
        "candidates_audited": len(audited),
        "material_claims": sum(a["material_claims"] for a in audited),
        "structural_provenance_defects": sum(len(a["structural_provenance_defects"]) for a in audited),
        "citation_defective_claims": sum(a["citation_defective_claims"] for a in audited),
        "numeric_tokens_total": sum(a["numeric_tokens_total"] for a in audited),
        "numeric_tokens_cited_chunk": sum(a["numeric_tokens_cited_chunk"] for a in audited),
        "numeric_tokens_adjacent_only": sum(a["numeric_tokens_adjacent_only"] for a in audited),
        "numeric_tokens_unsupported": sum(a["numeric_tokens_unsupported"] for a in audited),
        "future_business_items": sum(a["future_business_items"] for a in audited),
        "future_business_escalations": sum(len(a["future_business_escalations"]) for a in audited),
        "investment_language_violations": sum(len(a["investment_language_violations"]) for a in audited),
        "unknown_claims": sum(a["unknown_claims"] for a in audited),
        "unknown_fields": sum(a["unknown_fields"] for a in audited),
        "compound_claims_declared": sum(a["compound_claims_declared"] for a in audited),
        "suspected_compound_single_cite": sum(a["suspected_compound_single_cite"] for a in audited),
        "conflicts": sum(len(a["conflicts"]) for a in audited),
    }
    stage_totals: Counter = Counter()
    for a in audited:
        stage_totals.update(a["future_business_stage_distribution"])
    totals["future_business_stage_distribution"] = dict(stage_totals)

    report = {"schema": "H_V2_D3_3_GATE_AUDIT_V1", "run_id": run_id,
              "manifest": str(manifest_path), "live_model_calls_made": 0,
              "totals": totals, "per_ticker": per_ticker}
    out = manifest_path.with_suffix(".gateaudit.json")
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    print(json.dumps(totals, indent=2, ensure_ascii=False))
    print(f"\nfull audit: {out}")


if __name__ == "__main__":
    main()
