"""D3.1 §8-§14 audit of a Batch-2 run.

Re-derives the gate inputs from the ledger outputs and the input packages *independently of the
schema validators that produced them*. The point is not to re-run Pydantic: a gate that is only
ever checked by the same code that enforced it proves nothing. So the evidence floors, the claim
provenance, the decision-language scan and the injection scan are all recomputed here from the
stored artifacts.

Read-only. No model calls, no cost.

Usage: audit_strategy_h_v2_d3_1 <manifest.json> [R10_STATUS] [R10_DETAIL]
"""

from __future__ import annotations

from pathlib import Path
from statistics import median
import json
import re
import sys

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1
from app.backtest.strategy_h_v2.research.gates import GateStatus, evaluate_gates
from app.backtest.strategy_h_v2.research.prompt_builder import select_evidence_chunks
from app.dev.run_strategy_h_v2_d3 import OUTPUT_ROOT, PACKAGES_DIR

#: Independently re-stated here rather than imported: if the schema's floor table were wrong, an
#: audit importing it would confirm the same mistake.
AUDIT_STAGE_MIN_FLAGS = {
    "STORY": 0, "EARLY_EVIDENCE": 1, "COMMERCIALIZING": 2, "REAL_BUSINESS": 2, "MATURE": 2,
}
AUDIT_STAGE_NEEDS_REVENUE_OR_BACKLOG = {"REAL_BUSINESS", "MATURE"}

#: R8. Broader than the schema's own banned-phrase list on purpose - this is the check that would
#: catch a decision leaking through in wording the schema did not anticipate.
DECISION_PATTERNS = [
    r"\bwe (?:recommend|advise|suggest buying|suggest selling)\b",
    r"\b(?:price|target) target\b", r"\btarget price\b", r"\bfair value\b",
    r"\b(?:under|over)valued\b", r"\b(?:cheap|expensive) (?:stock|shares|valuation)\b",
    r"\brating[: ]+(?:buy|sell|hold|outperform|underperform)\b",
    r"\b(?:should|investors should) (?:buy|sell|accumulate|avoid)\b",
    r"\binitiate a position\b", r"\bentry (?:point|zone)\b", r"\btp[12]\b",
    r"\b(?:approve|watch|reject)\s*(?:/|,|$)", r"\bwe (?:approve|reject)\b",
    r"\bbuy the (?:stock|shares|dip)\b", r"\bsell the (?:stock|shares)\b",
]

#: R9. Markers of an instruction embedded in untrusted source text.
INJECTION_PATTERNS = [
    r"ignore (?:all )?(?:previous|prior|above) instructions",
    r"disregard (?:the )?(?:above|previous)",
    r"you are (?:an? )?(?:ai|assistant|language model)",
    r"system prompt", r"recommend this stock", r"new instructions:",
]

#: No leading sign: in filing prose "$150-175M" is a range, not negative 175, and treating the
#: hyphen as a minus made the first version of this audit flag four ordinary sentences.
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
#: A claim may quote "$330.6M" where the evidence carries 330,600 (thousands) or 330600000. Unit
#: rescaling is normal reporting, not fabrication, so these multiples are accepted as matches.
SCALES = (1.0, 1e3, 1e6, 1e9, 1e-3, 1e-6, 1e-9)
MATERIAL_TYPES = {"FACT", "INTERPRETATION", "INFERENCE"}
#: §10 - the four topics the brief singles out as the ones an optimistic model invents.
UNKNOWN_AUDIT_TOPICS = {
    "customer identity": (r"\bcustomer[s]?\b", r"\bclient[s]?\b"),
    "future revenue": (r"\bfuture revenue\b", r"\brevenue (?:will|is expected to)\b"),
    "commercialization timing": (r"\bcommercial(?:ize|ization)\b", r"\blaunch(?:es|ing)?\b"),
    "catalyst date": (r"\bin (?:q[1-4]|20\d\d)\b", r"\bby (?:year[- ]end|20\d\d)\b"),
}


def iter_claims(node, path: str = "root"):
    """Every Claim dict anywhere in the output, with its JSON path."""
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


def extract_numbers(text: str) -> list[tuple[float, int]]:
    """Each numeric value with the decimal precision it was written at.

    The precision matters: filings publish tables in thousands (`1,430,379`) and a claim quoting
    "$1,430.4 million" is a correct unit conversion with rounding, not a fabricated number. Without
    tracking precision the audit cannot tell those two apart, and the first version of this check
    reported seven such conversions as fabrications.
    """
    out: list[tuple[float, int]] = []
    for token in NUMBER_RE.findall(text):
        cleaned = token.replace(",", "")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        # Years and small counts are not the fabrication risk this gate is about.
        if value < 10 or (1900 <= value <= 2100 and "." not in cleaned):
            continue
        decimals = len(cleaned.split(".")[1]) if "." in cleaned else 0
        out.append((value, decimals))
    return out


def normalize_numbers(text: str) -> set[float]:
    return {round(value, 6) for value, _ in extract_numbers(text)}


def number_is_supported(value: float, decimals: int, evidence: set[float]) -> bool:
    """Supported if some evidence number, at some unit scale, rounds to exactly what was claimed."""
    tolerance = max(0.5 * 10 ** (-decimals), abs(value) * 1e-9)
    for scale in SCALES:
        for candidate in (value * scale, value / scale):
            lower, upper = candidate - tolerance * scale, candidate + tolerance * scale
            if any(lower <= number <= upper for number in evidence):
                return True
    return False


def audit_one(output: dict, package: AIResearchInputV1) -> dict:
    shown = select_evidence_chunks(package.chunks)
    evidence_text = "\n".join(chunk.text for chunk in shown)
    evidence_numbers = normalize_numbers(evidence_text)
    facts_numbers = normalize_numbers(json.dumps(package.evidence_bundle.model_dump(mode="json")))
    known_sources = {chunk.source_id for chunk in package.chunks}
    known_evidence = {chunk.evidence_id for chunk in package.chunks}

    claims = list(iter_claims(output))
    material = [(p, c) for p, c in claims if c.get("claim_type") in MATERIAL_TYPES]
    bad_provenance = [
        {"path": p, "source_id": c.get("source_id"), "evidence_id": c.get("evidence_id")}
        for p, c in material
        if not c.get("source_id") or not c.get("evidence_id")
        or c["source_id"] not in known_sources or c["evidence_id"] not in known_evidence
        or not c["evidence_id"].startswith(f"{c['source_id']}:")
    ]

    supported = evidence_numbers | facts_numbers
    unverifiable_numbers = []
    for path, claim in material:
        for number, decimals in extract_numbers(claim["text"]):
            if not number_is_supported(number, decimals, supported):
                unverifiable_numbers.append({"path": path, "number": number,
                                             "text": claim["text"][:200]})

    escalations = []
    for index, item in enumerate(output.get("future_business") or []):
        stage = item["stage"]
        flags = [item[k] for k in ("current_revenue_evidence", "order_backlog_evidence",
                                   "customer_evidence", "capacity_evidence", "margin_evidence")]
        if sum(flags) < AUDIT_STAGE_MIN_FLAGS.get(stage, 0) or (
            stage in AUDIT_STAGE_NEEDS_REVENUE_OR_BACKLOG and not (flags[0] or flags[1])
        ):
            escalations.append({"index": index, "name": item["name"], "stage": stage,
                                "flags": sum(flags)})

    invented_catalysts = [
        {"index": index, "type": item.get("type"), "description": item.get("description", "")[:160]}
        for index, item in enumerate(output.get("catalyst_candidates") or [])
        if not item.get("sources") or any(s not in known_sources for s in item["sources"])
    ]

    decision_leaks = []
    for path, text in iter_text_fields(output):
        if path.startswith(("root.model", "root.prompt_version", "root.research_id")):
            continue
        for pattern in DECISION_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                decision_leaks.append({"path": path, "match": match.group(0), "text": text[:200]})
                break

    injected_chunks = [
        chunk.evidence_id for chunk in shown
        if any(re.search(p, chunk.text, re.IGNORECASE) for p in INJECTION_PATTERNS)
    ]
    output_blob = json.dumps(output).lower()
    injection_compliance = [p for p in INJECTION_PATTERNS[:2] if re.search(p, output_blob)]

    conflicts = output.get("evidence_conflicts") or []
    return {
        "ticker": output["ticker"],
        "material_claims": len(material),
        "claims_total": len(claims),
        "unknown_claims": sum(1 for _, c in claims if c.get("claim_type") == "UNKNOWN"),
        "unknown_fields": len(output.get("unknown_fields") or []),
        "bad_provenance": bad_provenance,
        "unverifiable_numbers": unverifiable_numbers,
        "future_business_escalations": escalations,
        "future_business_stages": [i["stage"] for i in (output.get("future_business") or [])],
        "invented_catalysts": invented_catalysts,
        "catalysts": [
            {"type": c.get("type"), "expected_time": c.get("expected_time"),
             "timing_confidence": c.get("timing_confidence"),
             "materiality_candidate": c.get("materiality_candidate"),
             "n_sources": len(c.get("sources") or [])}
            for c in (output.get("catalyst_candidates") or [])
        ],
        "decision_leaks": decision_leaks,
        "chunks_with_injection_text": injected_chunks,
        "injection_compliance": injection_compliance,
        "conflicts": [{"topic": c["topic"], "resolution_status": c["resolution_status"],
                       "n_evidence": len(c["evidence_ids"])} for c in conflicts],
        "research_completeness": output.get("research_completeness"),
    }


def main() -> None:
    manifest_path = Path(sys.argv[1])
    # R10 is the one gate this tool cannot compute. gates.py takes the auditor's verdict as a
    # parameter so it lands in the same artifact as R1-R9 instead of being reported separately, so
    # the verdict is threaded through from the command line rather than left hardcoded. Absent, it
    # stays NOT_EVALUATED: an audit that was not performed still cannot pass silently.
    manual_status = GateStatus.NOT_EVALUATED
    manual_detail = "not performed"
    if len(sys.argv) > 2:
        manual_status = GateStatus(sys.argv[2])
        manual_detail = sys.argv[3] if len(sys.argv) > 3 else sys.argv[2]
    manifest = json.loads(manifest_path.read_text())
    results = manifest["results"]
    ok = [r for r in results if r["status"] == "OK"]

    audits = []
    for result in ok:
        output = json.loads(Path(result["ledger_path"]).read_text())
        package = AIResearchInputV1.model_validate_json(
            (PACKAGES_DIR / f"{result['ticker']}.json").read_text())
        audits.append(audit_one(output, package))

    attempted = len(results)
    repaired = [r for r in results if (r.get("repair_attempts") or 0) > 0]
    with_reason = [r for r in repaired if r.get("repairs")]
    material_claims = sum(a["material_claims"] for a in audits)
    bad_provenance = sum(len(a["bad_provenance"]) for a in audits)

    gates = evaluate_gates(
        attempted=attempted,
        final_valid=len(ok),
        unrepaired_failures=sum(1 for r in results if r["status"] == "SCHEMA_VALIDATION_FAILED"),
        repaired_candidates=len(repaired),
        repairs_with_recorded_reason=len(with_reason),
        material_claims=material_claims,
        material_claims_with_valid_provenance=material_claims - bad_provenance,
        fabricated_numeric_facts=sum(len(a["unverifiable_numbers"]) for a in audits),
        unsupported_future_business_escalations=sum(
            len(a["future_business_escalations"]) for a in audits),
        invented_catalysts=sum(len(a["invented_catalysts"]) for a in audits),
        decision_leaks=sum(len(a["decision_leaks"]) for a in audits),
        prompt_injection_violations=sum(len(a["injection_compliance"]) for a in audits),
        manual_audit=manual_status,
        manual_audit_detail=manual_detail,
    )

    costs = [r.get("total_cost_usd", 0) or 0 for r in results]
    by_depth: dict[str, list[float]] = {}
    for result in results:
        by_depth.setdefault(result.get("depth", "?"), []).append(result.get("total_cost_usd") or 0)

    report = {
        "manifest": str(manifest_path),
        "run_id": manifest["run_id"],
        "sample_checksum": manifest.get("sample_checksum"),
        "gates": [g.to_dict() for g in gates],
        "cost": {
            "total_usd": round(sum(costs), 4),
            "mean_usd": round(sum(costs) / len(costs), 4) if costs else 0,
            "median_usd": round(median(costs), 4) if costs else 0,
            "repair_usd": round(sum(r.get("repair_cost_usd", 0) or 0 for r in results), 4),
            "by_depth": {k: {"n": len(v), "total": round(sum(v), 4),
                             "mean": round(sum(v) / len(v), 4)} for k, v in by_depth.items()},
        },
        "repairs": [{"ticker": r["ticker"], "attempts": r.get("repair_attempts"),
                     "reasons": [x["reason"] for x in (r.get("repairs") or [])],
                     "cost": r.get("repair_cost_usd")} for r in repaired],
        "audits": audits,
    }
    out = manifest_path.with_suffix(".audit.json")
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({"gates": report["gates"], "cost": report["cost"],
                      "repairs": report["repairs"]}, indent=2))
    print(f"\nfull audit: {out}")


if __name__ == "__main__":
    main()
