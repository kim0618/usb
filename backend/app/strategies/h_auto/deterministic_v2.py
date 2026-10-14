"""Production D3 and D4 without a model, under the frozen ``H_D3D4_DETERMINISTIC_PRODUCTION_V2``.

Contract: ``docs/operations/h_d3d4_deterministic_v2.json`` (checksum-verified on load, frozen before
any replay or real-data run). V1 (``deterministic.py``, ``h_d3d4_deterministic_v1.json``) stays as it
was: V2 runs the V1 generator and replaces exactly what the operator decided to change.

* **D3 (H_D3_DETERMINISTIC_V2)** = V1's record with R3.1 replaced by ``business_evidence`` (explicit
  BUSINESS chunk, else the Item 1 fallback, else ``BUSINESS_EVIDENCE_NOT_READY``). NOT_READY refuses
  the record (no ``final_output``) instead of writing an empty business model, so a collection gap is
  an abstention in D6, never ``thesis_unsupported``. Every other field keeps V1's rule and is
  re-validated by the same schema.
* **D4 (H_D4_DETERMINISTIC_V2)** = V1's record plus the code-owned guidance transcription and its
  arithmetic direction (``guidance_parser``). The gap state stays UNKNOWN: D0 §L's POSITIVE also needs
  the priced-in inference and its confidence, NEUTRAL a "reasonable reflection" test and NEGATIVE a
  "not yet reflected" reading, none of which a frozen contract gives to code; the large re-rating path
  is DEFERRED by operator decision C. The reason codes say which conjunct is missing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from app.strategies.h_auto import business_evidence as BE
from app.strategies.h_auto import deterministic as V1
from app.strategies.h_auto import guidance_parser as GP

REPO_ROOT = V1.REPO_ROOT
CONTRACT = REPO_ROOT / "docs/operations/h_d3d4_deterministic_v2.json"
CHECKSUM = REPO_ROOT / "docs/operations/h_d3d4_deterministic_v2.sha256"
CONTRACT_ID = "H_D3D4_DETERMINISTIC_PRODUCTION_V2"
D3_GENERATOR = "H_D3_DETERMINISTIC_V2"
D4_GENERATOR = "H_D4_DETERMINISTIC_V2"
D3_REFUSED = V1.D3_REFUSED
BUSINESS_NOT_READY_REFUSAL = "R3.1_BUSINESS_EVIDENCE_NOT_READY"
CLAIM_P1 = "Issuer business description disclosed in the latest 10-K (section BUSINESS)."
CLAIM_P2 = "Issuer business description disclosed in the latest 10-K (Item 1, deterministic fallback extraction)."

D3_NOT_PRODUCED = dict(V1.D3_NOT_PRODUCED) | {
    "business_model.interpretation": "R3.1 V2: cited (explicit BUSINESS chunk or Item 1 fallback), never read (D0 §V)"}

D4_REASONS_V2: tuple[str, ...] = (
    "G1_V2_POSITIVE_NOT_PRODUCED_PRICED_IN_CONJUNCT_AND_CONFIDENCE_AI_OWNED",
    "G1_V2_WIDE_POSITIVE_NOT_PRODUCED_C2_AND_DEFERRED_VALUATION_CONJUNCT",
    "G2_V2_LARGE_RERATING_NEGATIVE_DEFERRED_OPERATOR_DECISION_C",
    "G2_V2_NOT_YET_REFLECTED_NEGATIVE_UNFROZEN",
    "G3_V2_NEUTRAL_REASONABLE_REFLECTION_UNFROZEN",
    "PRICED_IN_AI_OWNED", "C3_INPUT_MODEL_DECLARED_NOT_EVALUATED",
)


class DeterministicContractError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    body = json.loads(CONTRACT.read_text(encoding="utf-8"))
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if hashlib.sha256(canonical.encode()).hexdigest() != CHECKSUM.read_text(encoding="utf-8").strip():
        raise DeterministicContractError(f"{CONTRACT_ID} does not match its frozen checksum")
    if body["declaration"]["contract_id"] != CONTRACT_ID:
        raise DeterministicContractError("unexpected contract id")
    return body


def sha256() -> str:
    return CHECKSUM.read_text(encoding="utf-8").strip()


# -------------------------------------------------------------------------------------------------
# D3
# -------------------------------------------------------------------------------------------------

def d3_from(package: Any, *, cik: str, decision_date: date, submissions_doc: Mapping[str, Any] | None,
            run_id: str, now: datetime | None = None) -> dict[str, Any]:
    """H_D3_DETERMINISTIC_V2 (contract §d3). ``package`` is an ``AIResearchInputV1``."""
    from pydantic import ValidationError
    from app.backtest.strategy_h_v2.research.schema_v2 import HResearchInterpretationV2
    from app.backtest.strategy_h_v2.research.validate import (
        cross_check_fundamental_change_states, valid_evidence_ids, valid_source_ids)

    contract()
    v1 = V1.d3_from(package, cik=cik, decision_date=decision_date, submissions_doc=submissions_doc,
                    run_id=run_id, now=now)
    content = json.loads(json.dumps(v1["final_output"] or v1["attempted_output"], default=str))
    content["research_id"] = content["research_id"].replace("D3D-", "D3D2-", 1)
    content["prompt_version"] = D3_GENERATOR
    business = BE.business_evidence(package)
    reasons = [r for r in v1["reason_codes"] if r != "R3.1_NO_10K_BUSINESS_SECTION"]
    sources = set(content.get("sources") or [])
    if business["state"] == "FOUND":
        text = CLAIM_P1 if business["source_priority"] == BE.P1 else CLAIM_P2
        content["business_model"] = {"revenue_drivers": [{
            "text": text, "claim_type": "FACT", "source_id": business["source_id"],
            "evidence_id": business["cited_evidence_id"], "confidence": "HIGH"}]}
        sources.add(business["source_id"])
        reasons.append(f"R3.1_V2_{business['source_priority']}")
    else:
        content["business_model"] = {"revenue_drivers": []}
        reasons.append(f"R3.1_V2_{BE.NOT_READY}:{business['reason']}")
    content["sources"] = sorted(sources)
    unknown = [u for u in content.get("unknown_fields") or [] if u != "business_model"]
    if not content["business_model"]["revenue_drivers"]:
        unknown.append("business_model")
    content["unknown_fields"] = unknown
    content["research_completeness"] = ("INSUFFICIENT_EVIDENCE" if not content["business_model"]["revenue_drivers"]
                                        and not content.get("fundamental_change") else "PARTIAL")
    record = {"record_schema": "h_d3_deterministic_v2", "generator": D3_GENERATOR, "contract_id": CONTRACT_ID,
              "contract_sha256": sha256(), "v1_contract_sha256": v1["contract_sha256"], "ticker": v1["ticker"],
              "cik": v1["cik"], "model": None, "model_calls": 0, "reason_codes": reasons,
              "business_evidence": business, "next_periodic_report": v1["next_periodic_report"],
              "not_produced": dict(D3_NOT_PRODUCED), "v1_final_status": v1["final_status"],
              "v1_refusal": v1.get("refusal")}
    refusal = None
    if business["state"] != "FOUND":
        refusal = BUSINESS_NOT_READY_REFUSAL
    elif v1.get("refusal") == "R3.5_CATALYST_UNDERIVABLE":
        refusal = "R3.5_CATALYST_UNDERIVABLE"
    errors: list[str] = []
    if refusal is None:
        errors = cross_check_fundamental_change_states(content, package)
        if not errors:
            try:
                validated = HResearchInterpretationV2.model_validate(content, context={
                    "valid_source_ids": valid_source_ids(package), "valid_evidence_ids": valid_evidence_ids(package)})
            except ValidationError as exc:
                errors = [f"{e['msg']} @ {'.'.join(str(p) for p in e['loc'])}" for e in exc.errors()]
        if errors:
            refusal = "R3.12_SCHEMA_VALIDATION_FAILED"
    if refusal:
        return record | {"final_status": D3_REFUSED, "refusal": refusal, "errors": errors, "final_output": None,
                         "final_output_checksum": None, "research_id": content["research_id"],
                         "attempted_output": content}
    final = json.loads(validated.model_dump_json())
    return record | {"final_status": "OK", "final_output": final, "final_output_checksum": V1.sha(final),
                     "research_id": final["research_id"]}


def d3_deterministic(prep: Mapping[str, Any], *, run_id: str, work: Path | None = None) -> dict[str, Any]:
    sec = prep.get("sec")
    record = d3_from(prep["package"], cik=prep["identity"].cik, decision_date=prep["session"],
                     submissions_doc=getattr(sec, "submissions_doc", None), run_id=run_id)
    if work is not None:
        (work / "d3_deterministic.json").write_text(json.dumps(record, indent=1, sort_keys=True, default=str),
                                                    encoding="utf-8")
    return record


# -------------------------------------------------------------------------------------------------
# D4
# -------------------------------------------------------------------------------------------------

def d4_from_bundle(bundle: Any, d3: Mapping[str, Any], *, package: Any, analysis_id: str) -> dict[str, Any]:
    """H_D4_DETERMINISTIC_V2 (contract §d4). Pure: the V1 record plus code-owned guidance."""
    from app.backtest.strategy_h_v2.expectation.gap_contract import ContractRule

    contract()
    v1 = V1.d4_from_bundle(bundle, d3, analysis_id=analysis_id)
    final = json.loads(json.dumps(v1["final_output"]))
    guidance = GP.guidance_evidence(package)
    non_price = guidance["non_price_items"]
    reasons = [r for r in final["reason_codes"] if not r.startswith(("G1_", "G2_", "G3_", "PRICED_IN", "C3_"))]
    reasons = list(D4_REASONS_V2) + [r for r in reasons if r not in D4_REASONS_V2]
    if non_price:
        reasons.insert(0, f"C1_SATISFIED_BY_{non_price}_CODE_OWNED_GUIDANCE_ITEMS")
    else:
        reasons.insert(0, "C1_NO_CODE_OWNED_NON_PRICE_ITEM")
    rules = [r for r in final["applied_contract_rules"] if r != ContractRule.C6_NO_EXPECTATION_EVIDENCE_FORCES_UNKNOWN.value]
    has_reaction = final["code_owned_expectation_evidence"]["price_reaction_events"] > 0
    reasons = [r for r in reasons if r != "G4_C6_NO_EXPECTATION_EVIDENCE_FORCES_UNKNOWN"]
    if not has_reaction and not non_price:
        reasons.append("G4_C6_NO_EXPECTATION_EVIDENCE_FORCES_UNKNOWN")
        rules.append(ContractRule.C6_NO_EXPECTATION_EVIDENCE_FORCES_UNKNOWN.value)
    final["analysis_id"] = analysis_id
    final["generator"] = D4_GENERATOR
    final["applied_contract_rules"] = rules
    final["reason_codes"] = reasons
    final["code_owned_expectation_evidence"]["non_price_expectation_items_code_owned"] = non_price
    final["code_owned_expectation_evidence"]["guidance"] = guidance
    return {"record_schema": "h_d4_deterministic_v2", "generator": D4_GENERATOR, "contract_id": CONTRACT_ID,
            "contract_sha256": sha256(), "final_status": "OK", "analysis_id": analysis_id, "model": None,
            "model_calls": 0, "research_input_id": d3.get("research_id"),
            "research_output_checksum": d3.get("final_output_checksum"),
            "final_output": final, "final_output_checksum": V1.sha(final)}


def d4_deterministic(prep: Mapping[str, Any], d3: Mapping[str, Any], *, run_id: str,
                     work: Path | None = None) -> dict[str, Any]:
    from app.dev.run_strategy_h_v2_d4_2 import build_bundle_for
    ticker = prep["identity"].ticker
    bundle = build_bundle_for(prep["package"], prep["panel"].close_panel(),
                              bundle_id=f"{run_id}:{ticker}:expectation", generated_at=prep["cutoff"])
    record = d4_from_bundle(bundle, d3, package=prep["package"], analysis_id=f"D4D2-{ticker}-{run_id}")
    if work is not None:
        (work / "expectation_evidence.json").write_text(bundle.model_dump_json(indent=1), encoding="utf-8")
        (work / "d4_deterministic.json").write_text(json.dumps(record, indent=1, sort_keys=True, default=str),
                                                    encoding="utf-8")
    return record
