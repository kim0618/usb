"""D3.2 offline re-audit of the D3.1 Batch-2 run under Validation Contract V2.

Zero model calls, zero cost. Reads only artifacts that already exist on disk: the frozen Batch-2
manifest, the ledger outputs it produced, and the D2.1 evidence packages those outputs cite. Nothing
here re-runs the live model, amends the frozen sample, or changes any D3.1 result - see
`docs/backtest/strategy_h_v2/H_V2_D3_2_VALIDATION_CONTRACT_REPAIR_V1.md` §L for why a V1 gate
percentage and a V2 metric this script prints are not directly comparable.

Usage: audit_strategy_h_v2_d3_2 <manifest.json> [out.json]
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1, EvidenceChunk
from app.backtest.strategy_h_v2.research import validation_v2 as v2
from app.dev.run_strategy_h_v2_d3 import PACKAGES_DIR

MATERIAL_TYPES = {"FACT", "INTERPRETATION", "INFERENCE"}

#: Same deterministic R10 selection rule as D3.1's manual audit (kept, not re-rolled), so this
#: script's automated citation/numeric re-check runs on exactly the 50 claims that were already
#: read by hand - the two audits must be checking the same claims to be comparable at all.
R10_TICKERS = ("LNG", "AIP", "HL", "OOMA", "FLS", "PSNL", "FCUV", "SRCE")
R10_NUMERIC_PER_TICKER = 5
R10_INFERENCE_PER_TICKER = 2

#: D3.1 §J's manual content read (0/50 defects) - preserved as-is, not recomputed. Content truth is
#: a human judgment this script does not attempt to automate; see brief §23, "기존 수동 판독 결과를
#: 자동으로 덮어쓰지 않는다."
R10_MANUAL_CONTENT_DEFECTS = 0
R10_MANUAL_CONTENT_CLAIMS = 50

#: Qualifier/hedge vocabulary an INFERENCE or UNKNOWN-adjacent claim should carry if it is honestly
#: stating a limit rather than overclaiming (R10D proxy - a deterministic stand-in for the
#: qualitative "did it preserve its own uncertainty" judgment already made by hand in D3.1 §J.1).
_HEDGE_RE = re.compile(
    r"\bdoes not\b|\bmay\b|\bcould\b|\bplausible\b|\bunconfirmed\b|\bnot (?:clear|disclosed|shown|"
    r"attribute|break)\b|\blikely\b|\bnot established\b|\bnot documented\b", re.IGNORECASE)


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


def iter_claims_r10_path(node, path: str = ""):
    """Same walk as `iter_claims`, but with D3.1 §J's own path convention (no leading "root.") -
    the selection hash must be computed on exactly the string D3.1's manual audit hashed, or this
    script silently re-derives a *different* 50-ish claims than the ones already read by hand,
    which would make R10B's "independently reproducing the manual read" claim false."""
    if isinstance(node, dict):
        if "claim_type" in node and "text" in node:
            yield path, node
        for key, value in node.items():
            yield from iter_claims_r10_path(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_claims_r10_path(value, f"{path}[{index}]")


def _r10_selection(ticker: str, output: dict) -> list[tuple[str, dict]]:
    """Identical rule to D3.1 §J: first 5 numeric-bearing claims + first 2 INFERENCE claims, by
    `sha256("H_V2_D3_1_R10_V1:<ticker>|<path>")` ascending, over D3.1's own path convention."""
    claims = [(p, c) for p, c in iter_claims_r10_path(output) if c.get("claim_type") in MATERIAL_TYPES]
    key = lambda pc: hashlib.sha256(f"H_V2_D3_1_R10_V1:{ticker}|{pc[0]}".encode()).hexdigest()
    numeric = sorted([pc for pc in claims if re.search(r"\d", pc[1]["text"])], key=key)
    numeric = numeric[:R10_NUMERIC_PER_TICKER]
    infer = sorted([pc for pc in claims if pc[1]["claim_type"] == "INFERENCE"], key=key)
    infer = infer[:R10_INFERENCE_PER_TICKER]
    picked = list(numeric)
    for pc in infer:
        if pc not in picked:
            picked.append(pc)
    return picked


class PackageIndex:
    """Per-candidate lookup: evidence_id -> chunk text, plus (source_id, chunk_index) adjacency.
    Built once per package from ALL chunks (`validate.py::valid_evidence_ids` validates against the
    full package, not just the subset shown to the model - so audit adjacency uses the same scope
    a citation is actually checked against)."""

    def __init__(self, package: AIResearchInputV1):
        self.by_id: dict[str, EvidenceChunk] = {c.evidence_id: c for c in package.chunks}
        self.by_source: dict[str, dict[int, EvidenceChunk]] = {}
        for c in package.chunks:
            self.by_source.setdefault(c.source_id, {})[c.chunk_index] = c
        self.source_ids = frozenset(c.source_id for c in package.chunks)
        self.evidence_ids = frozenset(self.by_id)

    def chunk_text(self, evidence_id: str | None) -> str | None:
        chunk = self.by_id.get(evidence_id) if evidence_id else None
        return chunk.text if chunk else None

    def adjacent_texts(self, evidence_id: str | None) -> tuple[str, ...]:
        chunk = self.by_id.get(evidence_id) if evidence_id else None
        if chunk is None:
            return ()
        siblings = self.by_source.get(chunk.source_id, {})
        return tuple(
            siblings[i].text for i in (chunk.chunk_index - 1, chunk.chunk_index + 1)
            if i in siblings
        )


def audit_output(ticker: str, output: dict, index: PackageIndex) -> dict:
    claims = list(iter_claims(output))
    material = [(p, c) for p, c in claims if c.get("claim_type") in MATERIAL_TYPES]

    provenance_counts: dict[str, int] = {}
    precision_counts: dict[str, int] = {}
    numeric_tokens_total = numeric_tokens_supported_cited = 0
    numeric_tokens_adjacent = numeric_tokens_unsupported = 0
    compound_candidates: list[dict] = []
    citation_defects: list[dict] = []

    for path, claim in material:
        source_id, evidence_id = claim.get("source_id"), claim.get("evidence_id")
        status = v2.structural_provenance(
            source_id=source_id, evidence_id=evidence_id,
            candidate_source_ids=index.source_ids, candidate_evidence_ids=index.evidence_ids)
        provenance_counts[status.value] = provenance_counts.get(status.value, 0) + 1

        cited_text = index.chunk_text(evidence_id)
        result = v2.citation_precision(claim["text"], cited_chunk_text=cited_text,
                                       adjacent_chunk_texts=index.adjacent_texts(evidence_id))
        precision_counts[result.status.value] = precision_counts.get(result.status.value, 0) + 1
        for tr in result.token_results:
            numeric_tokens_total += 1
            if tr.scope == v2.SupportScope.CITED_CHUNK:
                numeric_tokens_supported_cited += 1
            elif tr.scope == v2.SupportScope.ADJACENT_CHUNK:
                numeric_tokens_adjacent += 1
            else:
                numeric_tokens_unsupported += 1
        if result.status in (v2.PrecisionStatus.ADJACENT_RECOVERABLE, v2.PrecisionStatus.UNSUPPORTED):
            citation_defects.append({
                "path": path, "status": result.status.value, "evidence_id": evidence_id,
                "unsupported_values": [r.token.raw for r in result.unsupported],
                "adjacent_values": [r.token.raw for r in result.adjacent],
                "text": claim["text"][:200],
            })
        if v2.is_compound_claim(claim["text"]):
            compound_candidates.append({"path": path, "text": claim["text"][:200]})

    violations = false_positives_prevented = unclassified = 0
    for _, text in iter_text_fields(output):
        for match in v2.classify_investment_language(text):
            if match.verdict == v2.LanguageVerdict.VIOLATION:
                violations += 1
            elif match.verdict == v2.LanguageVerdict.SAFE:
                false_positives_prevented += 1
            else:
                unclassified += 1

    return {
        "ticker": ticker,
        "material_claims": len(material),
        "structural_provenance": provenance_counts,
        "citation_precision": precision_counts,
        "citation_defects": citation_defects,
        "numeric_tokens_total": numeric_tokens_total,
        "numeric_tokens_supported_cited_chunk": numeric_tokens_supported_cited,
        "numeric_tokens_adjacent_only": numeric_tokens_adjacent,
        "numeric_tokens_unsupported": numeric_tokens_unsupported,
        "compound_claim_candidates": len(compound_candidates),
        "compound_claim_examples": compound_candidates[:5],
        "investment_language_violations": violations,
        "investment_language_false_positives_prevented": false_positives_prevented,
        "investment_language_unclassified": unclassified,
    }


def r10_v2(ledger_outputs: dict[str, dict], indices: dict[str, PackageIndex]) -> dict:
    per_claim = []
    for ticker in R10_TICKERS:
        out = ledger_outputs[ticker]
        for path, claim in _r10_selection(ticker, out):
            index = indices[ticker]
            evidence_id = claim.get("evidence_id")
            cited_text = index.chunk_text(evidence_id)
            result = v2.citation_precision(claim["text"], cited_chunk_text=cited_text,
                                           adjacent_chunk_texts=index.adjacent_texts(evidence_id))
            hedge = bool(_HEDGE_RE.search(claim["text"])) if claim["claim_type"] in (
                "INFERENCE",) or "does not" in claim["text"].lower() else None
            per_claim.append({
                "ticker": ticker, "path": path, "claim_type": claim["claim_type"],
                "citation_status": result.status.value,
                "hedge_language_present": hedge,
            })
    citation_ok = sum(1 for c in per_claim if c["citation_status"] == "PRECISE"
                      or c["citation_status"] == "NO_NUMERIC_CONTENT")
    citation_defect = [c for c in per_claim
                       if c["citation_status"] in ("ADJACENT_RECOVERABLE", "UNSUPPORTED")]
    inference_claims = [c for c in per_claim if c["claim_type"] == "INFERENCE"]
    return {
        "R10A_content_accuracy": {
            "method": "manual read, preserved from D3.1 §J (not recomputed)",
            "claims_audited": R10_MANUAL_CONTENT_CLAIMS,
            "defects": R10_MANUAL_CONTENT_DEFECTS,
        },
        "R10B_citation_precision": {
            "method": "computed by validation_v2.citation_precision against the cited chunk, "
                      "independently reproducing the manual read",
            "claims_checked": len(per_claim),
            "precise_or_no_numeric_content": citation_ok,
            "defects": len(citation_defect),
            "defect_detail": citation_defect,
        },
        "R10C_numeric_fidelity": {
            "numeric_tokens": sum(
                len([t for t in v2.parse_numeric_tokens(
                    next(c for p, c in iter_claims_r10_path(ledger_outputs[e["ticker"]])
                        if p == e["path"])["text"]) if t.unit != v2.NumericUnit.UNKNOWN])
                for e in per_claim
            ),
        },
        "R10D_unknown_qualifier_preservation": {
            "inference_claims_checked": len(inference_claims),
            "inference_claims_with_hedge_language": sum(
                1 for c in inference_claims if c["hedge_language_present"]),
        },
    }


def counterfactual_repair(manifest: dict) -> dict:
    """For every repair round D3.1 actually ran, whether V2's investment-language classifier would
    still have triggered it. The stage-floor rounds are NOT re-evaluated against a different
    ontology - the ontology is unchanged by design (brief §17) - so a stage-floor round always
    counts as "would still repair" here; only the language-only rounds can flip."""
    rows = []
    for result in manifest["results"]:
        for rep in result.get("repairs") or []:
            errors = rep["errors"]
            is_fair_value_error = any("fair value" in e for e in errors)
            is_stage_error = any("requires at least" in e for e in errors)
            other = [e for e in errors if "fair value" not in e and "requires at least" not in e]
            # A bundled round (both causes in one attempt, e.g. LNG) still repairs under V2 because
            # the stage cause remains; only an all-fair-value round with no other cause disappears.
            still_repairs_v2 = is_stage_error or bool(other)
            rows.append({
                "ticker": result["ticker"], "attempt": rep["attempt"],
                "v1_reason": rep["reason"], "fair_value_cause": is_fair_value_error,
                "stage_floor_cause": is_stage_error, "other_cause": other,
                "would_repair_under_v2": still_repairs_v2,
            })
    tickers = {r["ticker"] for r in manifest["results"] if r.get("repairs")}
    v2_repaired_tickers = {r["ticker"] for r in rows if r["would_repair_under_v2"]}
    v1_rounds, v2_rounds = len(rows), sum(1 for r in rows if r["would_repair_under_v2"])
    attempted = len(manifest["results"])
    reached = sum(1 for r in manifest["results"] if r["status"] != "MODEL_CALL_FAILED")
    return {
        "rounds": rows,
        "v1_repaired_candidates": len(tickers),
        "v2_repaired_candidates": len(v2_repaired_tickers),
        "v2_repair_free_ticker": sorted(tickers - v2_repaired_tickers),
        "v1_repair_rounds": v1_rounds,
        "v2_repair_rounds": v2_rounds,
        "v1_repair_rate_of_attempted": round(len(tickers) / attempted, 4),
        "v2_repair_rate_of_attempted": round(len(v2_repaired_tickers) / attempted, 4),
        "v1_repair_rate_of_reached": round(len(tickers) / reached, 4),
        "v2_repair_rate_of_reached": round(len(v2_repaired_tickers) / reached, 4),
        "note": "ontology (Future Business stage floor) unchanged by design - a stage-floor round "
               "always still repairs under V2; only the fair-value-only round(s) disappear",
    }


def future_business_ontology_table(manifest: dict) -> list[dict]:
    rows = []
    stage_re = re.compile(
        r"stage=(\w+) requires at least (\d+) evidence flag\(s\), got (\d+).*?@ (\S+)")
    for result in manifest["results"]:
        for rep in result.get("repairs") or []:
            for err in rep["errors"]:
                m = stage_re.search(err)
                if not m:
                    continue
                stage, required, got, field_path = m.groups()
                rows.append({
                    "ticker": result["ticker"], "attempt": rep["attempt"],
                    "proposed_stage": stage, "flags_required": int(required),
                    "flags_present": int(got), "field_path": field_path,
                    "candidate_status": result["status"],
                })
    return rows


def main() -> None:
    manifest_path = Path(sys.argv[1])
    manifest = json.loads(manifest_path.read_text())
    ok_results = [r for r in manifest["results"] if r["status"] == "OK"]

    indices: dict[str, PackageIndex] = {}
    ledger_outputs: dict[str, dict] = {}
    per_ticker = []
    for result in ok_results:
        ticker = result["ticker"]
        package = AIResearchInputV1.model_validate_json(
            (PACKAGES_DIR / f"{ticker}.json").read_text())
        index = PackageIndex(package)
        indices[ticker] = index
        output = json.loads(Path(result["ledger_path"]).read_text())
        ledger_outputs[ticker] = output
        per_ticker.append(audit_output(ticker, output, index))

    totals = {
        "candidates_audited": len(per_ticker),
        "material_claims": sum(t["material_claims"] for t in per_ticker),
        "structural_provenance": {},
        "citation_precision": {},
        "numeric_tokens_total": sum(t["numeric_tokens_total"] for t in per_ticker),
        "numeric_tokens_supported_cited_chunk": sum(
            t["numeric_tokens_supported_cited_chunk"] for t in per_ticker),
        "numeric_tokens_adjacent_only": sum(t["numeric_tokens_adjacent_only"] for t in per_ticker),
        "numeric_tokens_unsupported": sum(t["numeric_tokens_unsupported"] for t in per_ticker),
        "compound_claim_candidates": sum(t["compound_claim_candidates"] for t in per_ticker),
        "investment_language_violations": sum(
            t["investment_language_violations"] for t in per_ticker),
        "investment_language_false_positives_prevented": sum(
            t["investment_language_false_positives_prevented"] for t in per_ticker),
        "investment_language_unclassified": sum(
            t["investment_language_unclassified"] for t in per_ticker),
    }
    for t in per_ticker:
        for k, v in t["structural_provenance"].items():
            totals["structural_provenance"][k] = totals["structural_provenance"].get(k, 0) + v
        for k, v in t["citation_precision"].items():
            totals["citation_precision"][k] = totals["citation_precision"].get(k, 0) + v

    report = {
        "schema": "H_V2_D3_2_OFFLINE_REAUDIT_V1",
        "validation_contract": "V2",
        "source_manifest": str(manifest_path),
        "source_manifest_run_id": manifest["run_id"],
        "live_model_calls_made": 0,
        "totals": totals,
        "per_ticker": per_ticker,
        "r10_v2": r10_v2(ledger_outputs, indices),
        "counterfactual_repair": counterfactual_repair(manifest),
        "future_business_ontology_table": future_business_ontology_table(manifest),
        "mrvi_note": (
            "MRVI (SCHEMA_VALIDATION_FAILED, R2) is unaffected: both its repair rounds are "
            "stage-floor errors, which V2's ontology deliberately leaves unchanged (brief §17). "
            "Its final output text is not in the ledger (validation never passed) and the stored "
            "raw_output_preview is truncated before future_business - so its claim content is "
            "NOT_AUDITABLE beyond the validation error itself."
        ),
    }
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else manifest_path.with_suffix(".v2audit.json")
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    print(json.dumps({"totals": totals, "counterfactual_repair": {
        k: v for k, v in report["counterfactual_repair"].items() if k != "rounds"}}, indent=2))
    print(f"\nfull report: {out_path}")


if __name__ == "__main__":
    main()
