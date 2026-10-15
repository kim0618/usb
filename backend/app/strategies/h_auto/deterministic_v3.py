"""Production D3 and D4 without a model, under the frozen ``H_D3D4_DETERMINISTIC_PRODUCTION_V3``.

Contract: ``docs/operations/h_d3d4_deterministic_v3.json`` (checksum-verified on load, frozen before any
replay or real-data run). V1 and V2 stay as they were; V3 runs the V2 generator and replaces exactly what
operator decisions A-D of V3 cover:

* **Guidance** (A): ``guidance_parser_v2`` (structured context: headings, bullets, table rows,
  label/value lines), direction unchanged (``classify_range_movement``).
* **Priced-in** (B): the issuer's price against its own D5_D2R_V1 Base (TP1) and Bull (TP2) values, the
  "valuation vs the company's own historical range" expectation evidence of D0 §M, read on exactly the
  two boundaries D6 already uses (``base_case_supports_upside``: TP1 upside > 0; ``fully_priced``: TP2
  upside <= 0). No new number.
* **Catalyst** (C): the next periodic report (R3.5) is an earnings event of the frozen H forward
  taxonomy (``material_events`` 10-Q/10-K; research-queue rank 1), hence MATERIAL; its timing is the
  statutory deadline inside the frozen 3-month horizon, an upper bound (BOUNDED).
* **Invalidation** (D): every thesis-supporting code-owned state carries a reversal condition (ACTIVE); a
  condition from the issuer's previous deterministic thesis whose state has reversed is TRIGGERED, with
  its reason code and evidence id. Each ACTIVE condition and each adverse or reversed state is a
  code-owned risk.
* **D4 gap** (V3 table): POSITIVE = overall code-owned guidance RAISED + an improving core state +
  priced-in not PRICED + no guidance conflict; NEUTRAL = the same evidence present without that
  conjunction; UNKNOWN otherwise. Confidence = min(C4/C3 ceiling, the D5 confidence the priced-in reading
  rests on). NEGATIVE and WIDE_POSITIVE are not produced (large re-rating DEFERRED; C2 needs a
  future-business stage above STORY).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from app.strategies.h_auto import deterministic as V1
from app.strategies.h_auto import deterministic_v2 as V2
from app.strategies.h_auto import guidance_parser_v2 as GP2

REPO_ROOT = V1.REPO_ROOT
CONTRACT = REPO_ROOT / "docs/operations/h_d3d4_deterministic_v3.json"
CHECKSUM = REPO_ROOT / "docs/operations/h_d3d4_deterministic_v3.sha256"
CONTRACT_ID = "H_D3D4_DETERMINISTIC_PRODUCTION_V3"
D3_GENERATOR = "H_D3_DETERMINISTIC_V3"
D4_GENERATOR = "H_D4_DETERMINISTIC_V3"
D3_REFUSED = V1.D3_REFUSED

CORE_METRICS = V1.CORE_METRICS
SUPPORTING_STATES = ("IMPROVING", "ACCELERATING", "INFLECTION_POSITIVE", "LOSS_TO_PROFIT")
ADVERSE_STATES = ("DETERIORATING", "INFLECTION_NEGATIVE", "PROFIT_TO_LOSS")
#: validate.IMPROVING_STATES (C2's "evidenced improvement"), read on the core metrics only: INCREASING is
#: a share-count state, and a rising share count is dilution, not improvement.
IMPROVING_STATES = ("IMPROVING", "ACCELERATING", "INFLECTION_POSITIVE", "INCREASING")
EARNINGS_FORMS = ("10-Q", "10-K")                 # research_queue._rank 1 and forward material_events

PRICED_IN_NOT, PRICED_IN_PARTIAL, PRICED_IN_PRICED, PRICED_IN_UNKNOWN = (
    "LIKELY_NOT_PRICED", "PARTIALLY_PRICED", "LIKELY_PRICED", "UNKNOWN")
MATERIAL, STANDARD, CAT_UNKNOWN = "MATERIAL", "STANDARD", "UNKNOWN"
ACTIVE, TRIGGERED, INV_UNKNOWN = "ACTIVE", "TRIGGERED", "UNKNOWN"
#: catalyst state -> the D3 schema's Confidence fields D6 reads (materiality_candidate, timing_confidence)
MATERIALITY_FIELD = {MATERIAL: "HIGH", STANDARD: "LOW", CAT_UNKNOWN: "UNKNOWN"}
TIMING_FIELD = {"BOUNDED_STATUTORY_DEADLINE": "MEDIUM", "UNKNOWN": "UNKNOWN"}


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


def fiscal_year_end(submissions_doc: Mapping[str, Any] | None) -> str | None:
    value = (submissions_doc or {}).get("fiscalYearEnd")
    return str(value) if value else None


# -------------------------------------------------------------------------------------------------
# Priced-in contract V1
# -------------------------------------------------------------------------------------------------

def priced_in(row: Mapping[str, Any] | None) -> dict[str, Any]:
    """The price against the issuer's own D5 Base (TP1) and Bull (TP2) values. Pure."""
    if not row or row.get("status") != "VALUED":
        return {"state": PRICED_IN_UNKNOWN, "confidence": "UNKNOWN", "reason": "D5_NOT_VALUED",
                "evidence_ids": [], "limitations": []}
    targets = row.get("target_prices") or {}
    up1, up2 = targets.get("upside_to_TP1"), targets.get("upside_to_TP2")
    confidence = ((row.get("confidence") or {}).get("confidence") or "UNKNOWN")
    if up1 is None or up2 is None or confidence not in ("LOW", "MEDIUM", "HIGH"):
        return {"state": PRICED_IN_UNKNOWN, "confidence": "UNKNOWN", "reason": "D5_TARGETS_OR_CONFIDENCE_MISSING",
                "evidence_ids": [], "limitations": []}
    if up2 <= 0.0:
        state, reason = PRICED_IN_PRICED, "PRICE_AT_OR_ABOVE_OWN_BULL_TP2"
    elif up1 <= 0.0:
        state, reason = PRICED_IN_PARTIAL, "PRICE_BETWEEN_OWN_BASE_TP1_AND_BULL_TP2"
    else:
        state, reason = PRICED_IN_NOT, "PRICE_BELOW_OWN_BASE_TP1"
    evidence = f"CODE:D5:{row.get('ticker')}:{row.get('contract_window')}:{row.get('primary_method')}"
    return {"state": state, "confidence": confidence, "reason": reason, "evidence_ids": [evidence],
            "inputs": {"current_price": row.get("current_price"), "tp1": targets.get("TP1"), "tp2": targets.get("TP2"),
                       "upside_to_TP1": up1, "upside_to_TP2": up2, "d5_confidence": confidence,
                       "primary_method": row.get("primary_method"), "contract_window": row.get("contract_window")},
            "limitations": ["No PIT consensus exists (C4); the reading is the issuer's own D5_D2R_V1 multiple range, "
                            "not what analysts expect."]}


# -------------------------------------------------------------------------------------------------
# Catalyst state contract V1
# -------------------------------------------------------------------------------------------------

def catalyst_state(next_report: Mapping[str, Any]) -> dict[str, Any]:
    if next_report.get("state") != "DERIVED":
        return {"state": CAT_UNKNOWN, "timing": "UNKNOWN", "reason": "R3_5_UNDERIVABLE"}
    if not next_report.get("within_horizon"):
        return {"state": CAT_UNKNOWN, "timing": "UNKNOWN", "reason": "R3_5_DEADLINE_BEYOND_HORIZON"}
    if next_report.get("form") in EARNINGS_FORMS:
        return {"state": MATERIAL, "timing": "BOUNDED_STATUTORY_DEADLINE", "event_type": "earnings",
                "form": next_report["form"], "date": next_report["deadline"],
                "reason": "PERIODIC_REPORT_IS_A_FROZEN_MATERIAL_EARNINGS_EVENT"}
    return {"state": STANDARD, "timing": "BOUNDED_STATUTORY_DEADLINE", "form": next_report.get("form"),
            "date": next_report.get("deadline"), "reason": "NOT_AN_EARNINGS_FORM"}


# -------------------------------------------------------------------------------------------------
# Invalidation state contract V1
# -------------------------------------------------------------------------------------------------

def _states(final: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for entry in final.get("fundamental_change") or []:
        source = (entry.get("explanation") or [{}])[0].get("source_id")
        out[entry["metric"]] = {"state": entry["code_owned_state"], "source_id": source}
    return out


def _guidance_key(a: Mapping[str, Any]) -> str:
    return f"{a['metric_key']}|{a['period']}|{a['unit']}"


def invalidation(states: Mapping[str, Mapping[str, Any]], guidance: Mapping[str, Any],
                 previous: Mapping[str, Any] | None) -> dict[str, Any]:
    """ACTIVE conditions on the current thesis, TRIGGERED reversals of the previous one. Pure."""
    conditions, triggered = [], []
    for metric in CORE_METRICS:
        s = states.get(metric)
        if s and s["state"] in SUPPORTING_STATES and s.get("source_id"):
            conditions.append({"id": f"INV:FUNDAMENTAL:{metric}", "status": ACTIVE, "source_id": s["source_id"],
                               "trigger": f"{metric} code-owned state in {list(ADVERSE_STATES)}",
                               "supporting_state": s["state"]})
    for a in guidance.get("assessments") or []:
        if a["state"] == "RAISED" and a.get("current_evidence_ids"):
            ev = a["current_evidence_ids"][0]
            conditions.append({"id": f"INV:GUIDANCE:{_guidance_key(a)}", "status": ACTIVE,
                               "source_id": ev.split(":CHUNK:")[0], "evidence_id": ev,
                               "trigger": f"guidance {a['metric_key']} {a['period']} LOWERED by classify_range_movement",
                               "supporting_state": "RAISED"})
    current_guidance = {_guidance_key(a): a for a in guidance.get("assessments") or []}
    for prev in (previous or {}).get("conditions") or []:
        if prev.get("status") != ACTIVE:
            continue
        kind, key = prev["id"].split(":", 2)[1:]
        if kind == "FUNDAMENTAL":
            now = states.get(key)
            if now and now["state"] in ADVERSE_STATES:
                triggered.append(prev | {"status": TRIGGERED, "reason_code": f"FUNDAMENTAL_REVERSED:{key}:{now['state']}",
                                         "evidence_id": now.get("source_id")})
        elif kind == "GUIDANCE":
            now = current_guidance.get(key)
            if now and now["state"] == "LOWERED":
                triggered.append(prev | {"status": TRIGGERED, "reason_code": f"GUIDANCE_LOWERED:{key}",
                                         "evidence_id": (now.get("current_evidence_ids") or [None])[0]})
    state = TRIGGERED if triggered else ACTIVE if conditions else INV_UNKNOWN
    return {"state": state, "conditions": conditions, "triggered": triggered,
            "previous_thesis_available": previous is not None}


# -------------------------------------------------------------------------------------------------
# D3
# -------------------------------------------------------------------------------------------------

def d3_from(package: Any, *, cik: str, decision_date: date, submissions_doc: Mapping[str, Any] | None,
            run_id: str, previous: Mapping[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """H_D3_DETERMINISTIC_V3 (contract §d3). ``previous`` = the issuer's previous V3 thesis states, if any."""
    from pydantic import ValidationError
    from app.backtest.strategy_h_v2.research.schema_v2 import HResearchInterpretationV2
    from app.backtest.strategy_h_v2.research.validate import (
        cross_check_fundamental_change_states, valid_evidence_ids, valid_source_ids)

    contract()
    v2 = V2.d3_from(package, cik=cik, decision_date=decision_date, submissions_doc=submissions_doc,
                    run_id=run_id, now=now)
    fye = fiscal_year_end(submissions_doc)
    guidance = GP2.guidance_evidence(package, fiscal_year_end=fye)
    record = {k: v for k, v in v2.items() if k not in ("final_output", "attempted_output", "final_output_checksum",
                                                      "research_id", "errors", "refusal", "final_status")}
    record |= {"record_schema": "h_d3_deterministic_v3", "generator": D3_GENERATOR, "contract_id": CONTRACT_ID,
               "contract_sha256": sha256(), "v2_final_status": v2["final_status"], "v2_refusal": v2.get("refusal"),
               "fiscal_year_end": fye}
    content = json.loads(json.dumps(v2["final_output"] or v2["attempted_output"], default=str))
    content["research_id"] = content["research_id"].replace("D3D2-", "D3D3-", 1)
    content["prompt_version"] = D3_GENERATOR
    states = _states(content)
    catalyst = catalyst_state(v2["next_periodic_report"])
    for entry in content.get("catalyst_candidates") or []:
        entry["materiality_candidate"] = MATERIALITY_FIELD[catalyst["state"]]
        entry["timing_confidence"] = TIMING_FIELD.get(catalyst["timing"], "UNKNOWN")
    inv = invalidation(states, guidance, previous)
    sources = set(content.get("sources") or [])
    valid_sources = valid_source_ids(package)
    candidates = []
    for c in inv["conditions"]:
        if c["source_id"] in valid_sources:
            candidates.append({"description": f"{c['id']}: thesis-supporting {c['supporting_state']} reverses "
                                              f"({c['trigger']}).", "sources": [c["source_id"]]})
            sources.add(c["source_id"])
    content["invalidation_candidates"] = candidates
    risks = list(content.get("risks") or [])
    for metric in CORE_METRICS:
        s = states.get(metric)
        if s and s["state"] in ADVERSE_STATES and s.get("source_id") in valid_sources:
            risks.append({"category": "BUSINESS", "sources": [s["source_id"]],
                          "description": f"{metric}: code-owned change state {s['state']}."})
    for c in inv["conditions"]:
        if c["source_id"] in valid_sources:
            risks.append({"category": "BUSINESS", "sources": [c["source_id"]],
                          "description": f"Thesis-supporting state may reverse: {c['id']}."})
    for t in inv["triggered"]:
        src = (t.get("evidence_id") or "").split(":CHUNK:")[0]
        if src in valid_sources:
            risks.append({"category": "BUSINESS", "sources": [src], "description": f"Invalidation triggered: {t['reason_code']}."})
    content["risks"] = risks
    sources |= {s for r in risks for s in r["sources"]}
    content["sources"] = sorted(sources)
    unknown = [u for u in content.get("unknown_fields") or []
               if not (u == "catalyst_materiality" and catalyst["state"] != CAT_UNKNOWN)
               and not (u == "invalidation_candidates" and candidates)]
    content["unknown_fields"] = unknown
    record |= {"catalyst_state": catalyst, "invalidation": inv, "guidance_summary": {
        k: guidance[k] for k in ("parser", "overall_state", "state_counts", "paired", "non_price_items", "conflicts")},
        "thesis_states": {"conditions": inv["conditions"], "fundamental": {m: s["state"] for m, s in states.items()}}}
    refusal, errors = v2.get("refusal"), []
    if refusal is None:
        errors = cross_check_fundamental_change_states(content, package)
        if not errors:
            try:
                validated = HResearchInterpretationV2.model_validate(content, context={
                    "valid_source_ids": valid_sources, "valid_evidence_ids": valid_evidence_ids(package)})
            except ValidationError as exc:
                errors = [f"{e['msg']} @ {'.'.join(str(p) for p in e['loc'])}" for e in exc.errors()]
        if errors:
            refusal = "R3.12_SCHEMA_VALIDATION_FAILED"
    if refusal:
        return record | {"final_status": D3_REFUSED, "refusal": refusal, "errors": errors, "final_output": None,
                         "final_output_checksum": None, "research_id": content["research_id"], "attempted_output": content}
    final = json.loads(validated.model_dump_json())
    return record | {"final_status": "OK", "final_output": final, "final_output_checksum": V1.sha(final),
                     "research_id": final["research_id"]}


# -------------------------------------------------------------------------------------------------
# D4 gap V3
# -------------------------------------------------------------------------------------------------

_CONF = ("UNKNOWN", "LOW", "MEDIUM", "HIGH")


def d4_from(bundle: Any, d3: Mapping[str, Any], *, package: Any, row: Mapping[str, Any] | None,
            fiscal_year_end: str | None, analysis_id: str) -> dict[str, Any]:
    """H_D4_DETERMINISTIC_V3 (contract §d4). Pure: V1 record + guidance V2 + priced-in + the V3 table."""
    from app.backtest.strategy_h_v2.expectation.gap_contract import (
        ContractRule, ExpectationGapState, d6_approve_precondition)
    from app.backtest.strategy_h_v2.research.schema import Confidence

    contract()
    v1 = V1.d4_from_bundle(bundle, d3, analysis_id=analysis_id)
    final = json.loads(json.dumps(v1["final_output"]))
    guidance = GP2.guidance_evidence(package, fiscal_year_end=fiscal_year_end)
    pin = priced_in(row)
    out = d3.get("final_output")
    states = _states(out) if out else {}
    improving = sorted(m for m in CORE_METRICS if (states.get(m) or {}).get("state") in IMPROVING_STATES)
    overall = guidance["overall_state"]
    ceiling = final["confidence_ceiling"]
    rules = [r for r in final["applied_contract_rules"]
             if r != ContractRule.C5_UNKNOWN_GAP_FORCES_UNKNOWN_CONFIDENCE.value]
    reasons: list[str] = []
    if out is None:
        gap, reasons = "UNKNOWN", ["U1_D3_HAS_NO_FINAL_OUTPUT"]
    elif guidance["non_price_items"] == 0:
        gap, reasons = "UNKNOWN", ["U2_NO_COMPARABLE_GUIDANCE_PAIR_C1"]
    elif overall == "MIXED" or guidance["conflicts"]:
        gap, reasons = "UNKNOWN", ["U3_GUIDANCE_MIXED_OR_CONFLICT_UNRESOLVED"]
    elif pin["state"] == PRICED_IN_UNKNOWN:
        gap, reasons = "UNKNOWN", [f"U4_PRICED_IN_UNRESOLVED:{pin['reason']}"]
    elif overall == "RAISED" and improving and pin["state"] in (PRICED_IN_NOT, PRICED_IN_PARTIAL):
        gap, reasons = "POSITIVE", ["P1_GUIDANCE_RAISED", f"P2_IMPROVING:{','.join(improving)}",
                                    f"P3_PRICED_IN:{pin['state']}", "P4_NO_GUIDANCE_CONFLICT"]
        rules.append(ContractRule.C1_POSITIVE_NEEDS_NON_PRICE_EVIDENCE.value)
    else:
        why = []
        if overall != "RAISED":
            why.append(f"N_GUIDANCE_{overall}")
        if not improving:
            why.append("N_NO_IMPROVING_CORE_STATE")
        if pin["state"] == PRICED_IN_PRICED:
            why.append("N_PRICED_IN_LIKELY_PRICED")
        gap, reasons = "NEUTRAL", why
    if gap == "UNKNOWN":
        confidence = "UNKNOWN"
        rules.append(ContractRule.C5_UNKNOWN_GAP_FORCES_UNKNOWN_CONFIDENCE.value)
    else:
        confidence = _CONF[min(_CONF.index(ceiling), _CONF.index(pin["confidence"]))]
        rules.append(ContractRule.C7_PRICED_IN_NEEDS_EVIDENCE.value)
    reasons += ["NEGATIVE_NOT_PRODUCED_LARGE_RERATING_DEFERRED", "WIDE_POSITIVE_NOT_PRODUCED_C2_FUTURE_BUSINESS_NONE"]
    gap_state, conf_state = ExpectationGapState(gap), Confidence(confidence)
    final |= {
        "analysis_id": analysis_id, "generator": D4_GENERATOR,
        "expectation_gap": gap, "expectation_gap_confidence": confidence,
        "applied_contract_rules": sorted(set(rules)),
        "d6_approve_precondition": d6_approve_precondition(gap_state, conf_state).value,
        "priced_in_assessment": pin,
        "reason_codes": reasons,
        "evidence_ids": sorted({e for a in guidance["assessments"] for e in a.get("current_evidence_ids", [])
                                + a.get("previous_evidence_ids", [])} | set(pin["evidence_ids"])),
        "unknown_fields": [] if gap != "UNKNOWN" else ["expectation_gap"],
    }
    final["code_owned_expectation_evidence"]["guidance"] = guidance
    final["code_owned_expectation_evidence"]["non_price_expectation_items_code_owned"] = guidance["non_price_items"]
    final["code_owned_expectation_evidence"]["improving_core_states"] = improving
    return {"record_schema": "h_d4_deterministic_v3", "generator": D4_GENERATOR, "contract_id": CONTRACT_ID,
            "contract_sha256": sha256(), "final_status": "OK", "analysis_id": analysis_id, "model": None,
            "model_calls": 0, "research_input_id": d3.get("research_id"),
            "research_output_checksum": d3.get("final_output_checksum"),
            "d5_row_sha256": None if row is None else V1.sha(row),
            "final_output": final, "final_output_checksum": V1.sha(final)}


# -------------------------------------------------------------------------------------------------
# production wrappers
# -------------------------------------------------------------------------------------------------

def d3_deterministic(prep: Mapping[str, Any], *, run_id: str, work: Path | None = None) -> dict[str, Any]:
    sec = prep.get("sec")
    record = d3_from(prep["package"], cik=prep["identity"].cik, decision_date=prep["session"],
                     submissions_doc=getattr(sec, "submissions_doc", None), run_id=run_id,
                     previous=prep.get("previous_thesis"))
    if work is not None:
        (work / "d3_deterministic.json").write_text(json.dumps(record, indent=1, sort_keys=True, default=str),
                                                    encoding="utf-8")
    return record


def d4_deterministic(prep: Mapping[str, Any], d3: Mapping[str, Any], *, row: Mapping[str, Any] | None, run_id: str,
                     work: Path | None = None) -> dict[str, Any]:
    from app.dev.run_strategy_h_v2_d4_2 import build_bundle_for
    ticker = prep["identity"].ticker
    bundle = build_bundle_for(prep["package"], prep["panel"].close_panel(),
                              bundle_id=f"{run_id}:{ticker}:expectation", generated_at=prep["cutoff"])
    record = d4_from(bundle, d3, package=prep["package"], row=row,
                     fiscal_year_end=fiscal_year_end(getattr(prep.get("sec"), "submissions_doc", None)),
                     analysis_id=f"D4D3-{ticker}-{run_id}")
    if work is not None:
        (work / "expectation_evidence.json").write_text(bundle.model_dump_json(indent=1), encoding="utf-8")
        (work / "d4_deterministic.json").write_text(json.dumps(record, indent=1, sort_keys=True, default=str),
                                                    encoding="utf-8")
    return record


def previous_thesis(evaluation_root: Path, ticker: str, *, exclude: Path | None = None) -> dict[str, Any] | None:
    """The issuer's most recent V3 thesis states from an earlier evaluation artifact, if any."""
    best = None
    for path in evaluation_root.glob(f"*/*_{ticker}_*/d3_deterministic.json"):
        if exclude is not None and exclude in path.parents:
            continue
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if body.get("generator") != D3_GENERATOR or body.get("final_status") != "OK":
            continue
        if best is None or path.stat().st_mtime > best[0]:
            best = (path.stat().st_mtime, body.get("thesis_states"))
    return None if best is None else best[1]
