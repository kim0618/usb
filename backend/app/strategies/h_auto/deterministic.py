"""Production D3 and D4 without a model, under the frozen ``H_D3D4_DETERMINISTIC_PRODUCTION_V1``.

Contract: ``docs/operations/h_d3d4_deterministic_v1.json`` (checksum-verified on load, frozen before
any replay or real-data run).

Audit basis (2026-10-14, ``H_V2_PRODUCTION_NO_AI_RUNTIME_REPAIR_V1.md`` §3-§5):

* **D3.** Every content field of ``h_research_interpretation_v2`` is model-written. D0 §V assigns
  business-model interpretation, future-business classification (STORY vs REAL), catalyst
  materiality and timing, and the risk/invalidation reading to AI. The Future Business table in
  ``research/schema.py`` is a floor (stage -> minimum count of model-written evidence flags), not a
  map from evidence to a stage, and no rule or validator exists for ``growth_durability``. What code
  owns is upstream of D3: D1/E2's fundamental-change states (``change_detection``) and the D2.1
  chunks, each with an SEC source id.
* **D4.** No function maps evidence to an ``expectation_gap`` state. D0 §L: "a frozen categorical
  state, judged by the AI and checked by code"; C1-C7 can only reject a model's state. Code owns the
  expectation evidence bundle (prices, event reactions, located guidance passages, consensus
  availability), the C4 confidence ceiling and the D6 precondition function.

The frozen V1 contract therefore produces, per rule (cited in the JSON):

* ``d3_from`` / ``d3_deterministic``: a schema-valid ``h_research_interpretation_v2`` whose content is
  code-owned only - the 10-K BUSINESS disclosure cited (R3.1), D1/E2 change states with SEC
  citations (R3.2), the next periodic report inside the 3-month horizon as an earnings catalyst with
  UNKNOWN materiality (R3.5), EVIDENCE_GAP/DILUTION risks (R3.6), and abstention (UNKNOWN or empty)
  everywhere the contract leaves the reading to AI. When the catalyst cannot be derived or the
  output fails the D3 schema, the record has no ``final_output``: zero catalysts would fire D6's
  no-catalyst REJECT on missing data, and an empty interpretation would fire thesis_unsupported.
* ``d4_from_bundle`` / ``d4_deterministic``: UNKNOWN/UNKNOWN with the C4 ceiling and the frozen
  precondition function. G1 (C1), G2 and G3 are recorded as reason codes on every record.

Nothing here reads a forward return, sets a threshold or chooses a state for being convenient.
"""

from __future__ import annotations

import calendar
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT = REPO_ROOT / "docs/operations/h_d3d4_deterministic_v1.json"
CHECKSUM = REPO_ROOT / "docs/operations/h_d3d4_deterministic_v1.sha256"


class DeterministicContractError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    body = json.loads(CONTRACT.read_text(encoding="utf-8"))
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if hashlib.sha256(canonical.encode()).hexdigest() != CHECKSUM.read_text(encoding="utf-8").strip():
        raise DeterministicContractError("H_D3D4_DETERMINISTIC_PRODUCTION_V1 does not match its frozen checksum")
    return body

D3_GENERATOR = "H_D3_DETERMINISTIC_V1"
D4_GENERATOR = "H_D4_DETERMINISTIC_V1"
D3_REFUSED = "DETERMINISTIC_D3_REFUSED"

#: D3 fields the frozen deterministic contract does not produce (abstention), with the contract rule.
D3_NOT_PRODUCED: dict[str, str] = {
    "business_model.interpretation": "R3.1: cited, never read (D0 §V)",
    "fundamental_change.explanation(why)": "R3.2: AI-owned D0 §I",
    "growth_durability.state": "R3.3: no frozen rule (E, not D6-binding)",
    "future_business": "R3.4: no evidence-to-stage generator (E, APPROVE path)",
    "catalyst.materiality_and_timing_confidence": "R3.5: AI-owned (E, APPROVE path)",
    "risks.other_categories": "R3.6: need a reading of filing text",
    "invalidation_candidates": "R3.7: no frozen deterministic invalidation (E, APPROVE path)",
    "competitive_position/management_execution/evidence_conflicts": "R3.8: AI-owned",
}

#: D6 input backtrace (repair brief §3). Every stored-output key ``run_strategy_h_v2_d6.project_d3`` /
#: ``project_d4`` read, its producer before this repair, and what produces it now. A test pins the key
#: set to the projection functions' source, so a new D6 input cannot appear without a row here.
D6_INPUT_BACKTRACE: tuple[dict[str, str], ...] = (
    {"leg": "D3", "key": "research_id", "use": "provenance / D4 chain", "producer_v1": "code (assemble_v2 metadata)", "now": "code"},
    {"leg": "D3", "key": "research_completeness", "use": "basis text only", "producer_v1": "AI", "now": "R3.11 PARTIAL / INSUFFICIENT_EVIDENCE"},
    {"leg": "D3", "key": "business_model", "use": "APPROVE business_understood; REJECT thesis_unsupported", "producer_v1": "AI", "now": "R3.1 10-K BUSINESS chunk cited"},
    {"leg": "D3", "key": "revenue_drivers", "use": "as business_model", "producer_v1": "AI", "now": "R3.1"},
    {"leg": "D3", "key": "fundamental_change", "use": "APPROVE anchored change; REJECT thesis_unsupported", "producer_v1": "AI (state copied from code, explanation AI)", "now": "R3.2 E2 states with SEC citations"},
    {"leg": "D3", "key": "growth_durability", "use": "basis text only (deterioration trigger NOT_EVALUATED)", "producer_v1": "AI", "now": "R3.3 UNKNOWN (E, non-binding)"},
    {"leg": "D3", "key": "state", "use": "growth_durability.state", "producer_v1": "AI", "now": "R3.3 UNKNOWN"},
    {"leg": "D3", "key": "future_business", "use": "APPROVE future/catalyst; REJECT story-only; WATCH", "producer_v1": "AI", "now": "R3.4 [] (E, APPROVE path)"},
    {"leg": "D3", "key": "stage", "use": "future_business stages", "producer_v1": "AI (floor validator only)", "now": "R3.4 none"},
    {"leg": "D3", "key": "evidence_flags", "use": "§J flags (_JJ_EVIDENCE_FLAGS): APPROVE future_business; REJECT story-only", "producer_v1": "AI (model-authored booleans)", "now": "R3.4 none"},
    {"leg": "D3", "key": "source_id", "use": "fundamental_change anchoring (_claims_are_sourced)", "producer_v1": "AI-written citation, validated", "now": "R3.1/R3.2 code citations"},
    {"leg": "D3", "key": "catalyst_candidates", "use": "APPROVE; REJECT no_catalyst; WATCH undated", "producer_v1": "AI", "now": "R3.5 next periodic report within horizon"},
    {"leg": "D3", "key": "materiality_candidate", "use": "catalyst material+timed", "producer_v1": "AI", "now": "R3.5 UNKNOWN (E, APPROVE path)"},
    {"leg": "D3", "key": "timing_confidence", "use": "catalyst material+timed", "producer_v1": "AI", "now": "R3.5 UNKNOWN"},
    {"leg": "D3", "key": "competitive_position", "use": "count, no clause", "producer_v1": "AI", "now": "R3.8 []"},
    {"leg": "D3", "key": "risks", "use": "APPROVE risks_with_invalidation", "producer_v1": "AI", "now": "R3.6 EVIDENCE_GAP / DILUTION"},
    {"leg": "D3", "key": "invalidation_candidates", "use": "APPROVE risks_with_invalidation", "producer_v1": "AI", "now": "R3.7 [] (E, APPROVE path)"},
    {"leg": "D3", "key": "unknown_fields", "use": "APPROVE no_blocking_unknown", "producer_v1": "AI", "now": "R3.10"},
    {"leg": "D3", "key": "evidence_conflicts", "use": "count, no clause", "producer_v1": "AI", "now": "R3.8 []"},
    {"leg": "D3", "key": "final_output", "use": "provenance (absent -> D3_REFUSED_NO_FINAL_OUTPUT)", "producer_v1": "AI", "now": "present when R3.5 derivable and schema-valid"},
    {"leg": "D3", "key": "final_output_checksum", "use": "provenance", "producer_v1": "code", "now": "code"},
    {"leg": "D4", "key": "final_output", "use": "provenance", "producer_v1": "AI + code residue", "now": "code (H_D4_DETERMINISTIC_V1)"},
    {"leg": "D4", "key": "analysis_id", "use": "provenance", "producer_v1": "code", "now": "code"},
    {"leg": "D4", "key": "final_output_checksum", "use": "provenance", "producer_v1": "code", "now": "code"},
    {"leg": "D4", "key": "research_input_id", "use": "D3->D4 chain", "producer_v1": "code", "now": "code"},
    {"leg": "D4", "key": "research_output_checksum", "use": "D3->D4 chain", "producer_v1": "code", "now": "code (D3 final_output checksum)"},
    {"leg": "D4", "key": "initial_failure_codes", "use": "refused projection", "producer_v1": "code", "now": "n/a"},
    {"leg": "D4", "key": "expectation_gap", "use": "APPROVE §L precondition; REJECT adverse gap; WATCH", "producer_v1": "AI (C1/C2/C5/C6 reject only)", "now": "UNKNOWN (G1 C1 / G2 / G3 E)"},
    {"leg": "D4", "key": "expectation_gap_confidence", "use": "APPROVE §L precondition", "producer_v1": "AI (<= ceiling)", "now": "UNKNOWN (C5)"},
    {"leg": "D4", "key": "confidence_ceiling", "use": "record only", "producer_v1": "code (C4; C3 from AI conflicts)", "now": "code (C4)"},
    {"leg": "D4", "key": "d6_approve_precondition", "use": "cross-check vs D6's own §L", "producer_v1": "code on AI inputs", "now": "code: BLOCKED"},
    {"leg": "D4", "key": "conflicts", "use": "count, no clause", "producer_v1": "AI", "now": "[]"},
    {"leg": "D4", "key": "unknown_fields", "use": "count, no clause", "producer_v1": "AI", "now": "code list"},
    {"leg": "D4", "key": "terminal_failure_codes", "use": "record only", "producer_v1": "code", "now": "n/a"},
)

D4_REASONS_ALWAYS: tuple[str, ...] = (
    "GAP_STATE_HAS_NO_FROZEN_CODE_GENERATOR_D0_SL",
    "C1_POSITIVE_INADMISSIBLE_NON_PRICE_ITEMS_ARE_MODEL_WRITTEN",
    "NEUTRAL_OR_NEGATIVE_NEEDS_JUDGED_RATIONALE_NO_FROZEN_RULE",
    "PRICED_IN_ASSESSMENT_AI_OWNED",
    "C3_INPUT_MODEL_DECLARED_CONFLICTS_NOT_EVALUATED",
    "C5_UNKNOWN_GAP_PAIRS_UNKNOWN_CONFIDENCE",
)


def sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


# -------------------------------------------------------------------------------------------------
# R3.5: the next periodic report and its statutory deadline
# -------------------------------------------------------------------------------------------------

#: Form 10-K / 10-Q General Instruction A: days after the period end, by filer category.
DEADLINE_DAYS = {"10-K": {"LARGE_ACCELERATED": 60, "ACCELERATED": 75, "OTHER": 90},
                 "10-Q": {"LARGE_ACCELERATED": 40, "ACCELERATED": 40, "OTHER": 45}}
HORIZON_MONTHS = 3          # D0 §K / §R thesis horizon "weeks to 3 months"
CADENCE_LOOKBACK = 4        # the 4th latest periodic report is the same fiscal period one year earlier


def filer_category(submissions_doc: Mapping[str, Any] | None) -> str:
    text = str((submissions_doc or {}).get("category") or "").lower()
    if "large accelerated" in text:
        return "LARGE_ACCELERATED"
    if "accelerated" in text and "non-accelerated" not in text:
        return "ACCELERATED"
    return "OTHER"


def add_months(day: date, months: int) -> date:
    month0 = day.month - 1 + months
    year, month = day.year + month0 // 12, month0 % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def roll_weekend(day: date) -> date:
    return day + timedelta(days=(7 - day.weekday()) % 7) if day.weekday() >= 5 else day


def periodic_reports(submissions_doc: Mapping[str, Any] | None, cutoff: datetime) -> list[dict[str, Any]]:
    """10-K / 10-Q (no amendments) known at ``cutoff``, one per reportDate, newest period first."""
    block = ((submissions_doc or {}).get("filings") or {}).get("recent") or {}
    n = len(block.get("form") or [])
    seen: dict[str, dict[str, Any]] = {}
    for i in range(n):
        form = block["form"][i]
        report = (block.get("reportDate") or [""] * n)[i]
        accepted = (block.get("acceptanceDateTime") or [""] * n)[i]
        if form not in ("10-K", "10-Q") or not report:
            continue
        try:
            when = datetime.fromisoformat(str(accepted).replace("Z", "+00:00"))
            when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if when > cutoff:
            continue
        row = {"form": form, "reportDate": report, "accessionNumber": block["accessionNumber"][i],
               "filingDate": block["filingDate"][i]}
        seen.setdefault(report, row)
    return [seen[k] for k in sorted(seen, reverse=True)]


def next_periodic_report(submissions_doc: Mapping[str, Any] | None, *, cutoff: datetime,
                         decision_date: date) -> dict[str, Any]:
    reports = periodic_reports(submissions_doc, cutoff)
    if len(reports) < CADENCE_LOOKBACK:
        return {"state": "UNDERIVABLE", "reason": f"PERIODIC_REPORTS_{len(reports)}_LT_{CADENCE_LOOKBACK}"}
    anchor = reports[CADENCE_LOOKBACK - 1]
    period_end = add_months(date.fromisoformat(anchor["reportDate"]), 12)
    if period_end <= date.fromisoformat(reports[0]["reportDate"]):
        return {"state": "UNDERIVABLE", "reason": "CADENCE_NOT_ADVANCING"}
    category = filer_category(submissions_doc)
    deadline = roll_weekend(period_end + timedelta(days=DEADLINE_DAYS[anchor["form"]][category]))
    horizon_end = add_months(decision_date, HORIZON_MONTHS)
    return {"state": "DERIVED", "form": anchor["form"], "period_end": period_end.isoformat(),
            "filer_category": category, "deadline": deadline.isoformat(), "horizon_end": horizon_end.isoformat(),
            "within_horizon": deadline <= horizon_end, "latest": reports[0], "cadence_anchor": anchor}


# -------------------------------------------------------------------------------------------------
# D3
# -------------------------------------------------------------------------------------------------

CORE_METRICS = ("revenue", "operating_income", "eps_diluted", "free_cash_flow", "operating_margin")
SECTION_PREFERENCE = ("RESULTS_OF_OPERATIONS", "MD_AND_A")
ALWAYS_UNKNOWN = ("growth_durability", "future_business", "catalyst_materiality", "invalidation_candidates",
                  "competitive_position", "management_execution")


def _chunks_by_source(package: Any) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for chunk in package.chunks:
        out.setdefault(chunk.source_id, []).append(chunk)
    for rows in out.values():
        rows.sort(key=lambda c: c.chunk_index)
    return out


def _cite(source_id: str, chunks: Sequence[Any], prefer: Sequence[str] = ()) -> str:
    for section in prefer:
        for chunk in chunks:
            if chunk.section == section:
                return chunk.evidence_id
    return chunks[0].evidence_id


def _latest_10k_source(package: Any) -> str | None:
    docs = [d for d in package.materialization.documents if d.role == "LATEST_10K" and d.chunk_count > 0]
    return docs[0].source_id if docs else None


def d3_from(package: Any, *, cik: str, decision_date: date, submissions_doc: Mapping[str, Any] | None,
            run_id: str, now: datetime | None = None) -> dict[str, Any]:
    """H_D3_DETERMINISTIC_V1 (contract §d3). ``package`` is an ``AIResearchInputV1``."""
    from pydantic import ValidationError
    from app.backtest.strategy_h_v2.research.schema_v2 import HResearchInterpretationV2
    from app.backtest.strategy_h_v2.research.validate import (
        cross_check_fundamental_change_states, package_checksum, valid_evidence_ids, valid_source_ids)

    contract()
    bundle = package.evidence_bundle
    ticker = bundle.ticker
    cik = str(cik).zfill(10)
    sources = valid_source_ids(package)
    by_source = _chunks_by_source(package)
    reasons: list[str] = []
    cited: set[str] = set()

    def claim(text: str, source_id: str, evidence_id: str) -> dict[str, Any]:
        cited.add(source_id)
        return {"text": text, "claim_type": "FACT", "source_id": source_id, "evidence_id": evidence_id,
                "confidence": "HIGH"}

    # R3.1
    tenk = _latest_10k_source(package)
    revenue_drivers = []
    business = [c for c in by_source.get(tenk or "", []) if c.section == "BUSINESS"]
    if business:
        revenue_drivers.append(claim("Issuer business description disclosed in the latest 10-K (section BUSINESS).",
                                     tenk, business[0].evidence_id))
    else:
        reasons.append("R3.1_NO_10K_BUSINESS_SECTION")

    # R3.2 and R3.6
    changes, risks = [], []
    states = {}
    for metric, data in sorted((bundle.fundamental_changes or {}).items()):
        data = data or {}
        state = data.get("state") or "UNKNOWN"
        states[metric] = state
        points = [p for p in data.get("points") or [] if isinstance(p, Mapping) and p.get("current_accession")]
        source = f"SEC:{cik}:{points[-1]['current_accession']}" if points else None
        if state != "UNKNOWN" and source in by_source:
            changes.append({"metric": metric, "code_owned_state": state, "durability_relevant": False,
                            "explanation": [claim(f"{metric}: code-owned change state {state} (D1/E2 change detection).",
                                                  source, _cite(source, by_source[source], SECTION_PREFERENCE))]})
        if metric == "shares_outstanding" and state == "INCREASING" and source in sources:
            cited.add(source)
            risks.append({"category": "DILUTION", "sources": [source],
                          "description": "Shares outstanding state INCREASING (code-owned D1/E2 state)."})
    if not changes:
        reasons.append("R3.2_NO_ANCHORED_CODE_OWNED_CHANGE")
    if tenk in sources:
        for metric in CORE_METRICS:
            if states.get(metric, "UNKNOWN") == "UNKNOWN":
                cited.add(tenk)
                risks.append({"category": "EVIDENCE_GAP", "sources": [tenk],
                              "description": f"{metric}: no code-owned change state (not reported or not comparable)."})

    # R3.5
    nxt = next_periodic_report(submissions_doc, cutoff=bundle.data_cutoff, decision_date=decision_date)
    catalysts = []
    if nxt["state"] != "DERIVED":
        reasons.append(f"R3.5_UNDERIVABLE:{nxt['reason']}")
    else:
        latest_source = f"SEC:{cik}:{nxt['latest']['accessionNumber']}"
        source = latest_source if latest_source in sources else tenk if tenk in sources else None
        if source is None:
            nxt = {"state": "UNDERIVABLE", "reason": "NO_CITABLE_SOURCE"}
            reasons.append("R3.5_UNDERIVABLE:NO_CITABLE_SOURCE")
        elif nxt["within_horizon"]:
            cited.add(source)
            catalysts.append({"type": "earnings", "expected_time": nxt["deadline"],
                              "description": f"Next periodic report ({nxt['form']} for the period ending "
                                             f"{nxt['period_end']}); statutory filing deadline {nxt['deadline']}.",
                              "timing_confidence": "UNKNOWN", "materiality_candidate": "UNKNOWN",
                              "sources": [source]})
        else:
            reasons.append("R3.5_NEXT_REPORT_DEADLINE_BEYOND_HORIZON")

    unknown = list(ALWAYS_UNKNOWN) + (["business_model"] if not revenue_drivers else []) + \
        (["fundamental_change"] if not changes else [])
    created = now or datetime.now(timezone.utc)
    content = {
        "research_id": f"D3D-{ticker}-{run_id}", "version": 1, "company_id": bundle.identity.get("cik") or "UNKNOWN",
        "ticker": ticker, "decision_time": bundle.data_cutoff.isoformat(),
        "input_package_id": f"{package.run_id}:{ticker}", "input_package_checksum": package_checksum(package),
        "model": "NONE (deterministic)", "model_version": None, "prompt_version": D3_GENERATOR,
        "created_at": created.isoformat(),
        "business_model": {"revenue_drivers": revenue_drivers},
        "fundamental_change": changes,
        "growth_durability": {"state": "UNKNOWN", "rationale": [], "evidence_checklist": {}},
        "future_business": [], "competitive_position": [], "management_execution": [],
        "catalyst_candidates": catalysts,
        "why_now_candidate": {"summary": "Deterministic re-evaluation on code-owned evidence.", "reasons": []},
        "risks": risks, "invalidation_candidates": [], "evidence_conflicts": [], "open_questions": [],
        "unknown_fields": unknown, "sources": sorted(cited),
        "research_completeness": "INSUFFICIENT_EVIDENCE" if not revenue_drivers and not changes else "PARTIAL",
    }
    record = {"record_schema": "h_d3_deterministic_v1", "generator": D3_GENERATOR,
              "contract_sha256": CHECKSUM.read_text(encoding="utf-8").strip(), "ticker": ticker, "cik": cik,
              "model": None, "model_calls": 0, "reason_codes": reasons, "next_periodic_report": nxt,
              "not_produced": dict(D3_NOT_PRODUCED)}
    refusal = None
    if nxt["state"] != "DERIVED":
        refusal = "R3.5_CATALYST_UNDERIVABLE"
    errors = cross_check_fundamental_change_states(content, package)
    if not errors and refusal is None:
        try:
            validated = HResearchInterpretationV2.model_validate(content, context={
                "valid_source_ids": sources, "valid_evidence_ids": valid_evidence_ids(package)})
        except ValidationError as exc:
            errors = [f"{e['msg']} @ {'.'.join(str(p) for p in e['loc'])}" for e in exc.errors()]
    if errors and refusal is None:
        refusal = "R3.12_SCHEMA_VALIDATION_FAILED"
    if refusal:
        return record | {"final_status": D3_REFUSED, "refusal": refusal, "errors": errors,
                         "final_output": None, "final_output_checksum": None, "research_id": content["research_id"],
                         "attempted_output": content}
    final = json.loads(validated.model_dump_json())
    return record | {"final_status": "OK", "final_output": final, "final_output_checksum": sha(final),
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

def d4_from_bundle(bundle: Any, d3: Mapping[str, Any], *, analysis_id: str) -> dict[str, Any]:
    """H_D4_DETERMINISTIC_V1 (contract §d4) applied to a code-owned expectation bundle. Pure."""
    from app.backtest.strategy_h_v2.expectation.gap_contract import (
        ContractRule, ExpectationGapState, confidence_ceiling, d6_approve_precondition)
    from app.backtest.strategy_h_v2.research.schema import Confidence

    contract()
    body = bundle if isinstance(bundle, Mapping) else json.loads(bundle.model_dump_json())
    status = lambda block: ((body.get(block) or {}).get("status"))            # noqa: E731
    consensus_available = status("consensus") not in (None, "SOURCE_NOT_AVAILABLE")
    revisions_available = status("estimate_revisions") not in (None, "SOURCE_NOT_AVAILABLE")
    ceiling, fired = confidence_ceiling(consensus_available=consensus_available,
                                        estimate_revisions_available=revisions_available,
                                        has_unresolved_material_conflict=False)
    reactions = body.get("price_reaction") or []
    non_price_items = 0                       # every non-price item needs an AI-owned transcription (D4 §Q)
    reasons = ["G1_POSITIVE_INADMISSIBLE_C1_NO_CODE_OWNED_NON_PRICE_ITEM",
               "G2_NEGATIVE_NOT_PRODUCED_REFLECTED_MAGNITUDE_UNFROZEN",
               "G3_NEUTRAL_NOT_PRODUCED_REASONABLE_REFLECTION_UNFROZEN",
               "PRICED_IN_AI_OWNED", "C3_INPUT_MODEL_DECLARED_NOT_EVALUATED"]
    rules = [r.value for r in fired] + [ContractRule.C5_UNKNOWN_GAP_FORCES_UNKNOWN_CONFIDENCE.value]
    if not reactions:
        reasons.append("G4_C6_NO_EXPECTATION_EVIDENCE_FORCES_UNKNOWN")
        rules.append(ContractRule.C6_NO_EXPECTATION_EVIDENCE_FORCES_UNKNOWN.value)
    if d3.get("final_output") is None:
        reasons.append("D3_HAS_NO_FINAL_OUTPUT")
    gap, confidence = ExpectationGapState.UNKNOWN, Confidence.UNKNOWN
    located = {name: len((body.get(name) or {}).get("excerpts") or [])
               for name in ("guidance", "earnings_history", "management_expectation_signals")}
    final = {
        "analysis_id": analysis_id, "generator": D4_GENERATOR,
        "expectation_gap": gap.value, "expectation_gap_confidence": confidence.value,
        "confidence_ceiling": ceiling.value, "applied_contract_rules": rules,
        "d6_approve_precondition": d6_approve_precondition(gap, confidence).value,
        "priced_in_assessment": {"state": "UNKNOWN", "confidence": "UNKNOWN"},
        "code_owned_expectation_evidence": {
            "consensus_status": status("consensus"), "estimate_revisions_status": status("estimate_revisions"),
            "price_reaction_events": len(reactions), "located_excerpts": located,
            "non_price_expectation_items_code_owned": non_price_items,
            "expectation_bundle_checksum": sha(body)},
        "conflicts": [], "unknown_fields": ["expectation_gap", "priced_in_assessment"],
        "reason_codes": reasons,
    }
    return {"record_schema": "h_d4_deterministic_v1", "generator": D4_GENERATOR,
            "contract_sha256": CHECKSUM.read_text(encoding="utf-8").strip(), "final_status": "OK",
            "analysis_id": analysis_id, "model": None, "model_calls": 0,
            "research_input_id": d3.get("research_id"),
            "research_output_checksum": d3.get("final_output_checksum"),
            "final_output": final, "final_output_checksum": sha(final)}


def d4_deterministic(prep: Mapping[str, Any], d3: Mapping[str, Any], *, run_id: str,
                     work: Path | None = None) -> dict[str, Any]:
    """Build the frozen code expectation bundle on the evaluation's own panel, then ``d4_from_bundle``."""
    from app.dev.run_strategy_h_v2_d4_2 import build_bundle_for
    ticker = prep["identity"].ticker
    bundle = build_bundle_for(prep["package"], prep["panel"].close_panel(),
                              bundle_id=f"{run_id}:{ticker}:expectation", generated_at=prep["cutoff"])
    record = d4_from_bundle(bundle, d3, analysis_id=f"D4D-{ticker}-{run_id}")
    if work is not None:
        (work / "expectation_evidence.json").write_text(bundle.model_dump_json(indent=1), encoding="utf-8")
        (work / "d4_deterministic.json").write_text(json.dumps(record, indent=1, sort_keys=True, default=str),
                                                    encoding="utf-8")
    return record
