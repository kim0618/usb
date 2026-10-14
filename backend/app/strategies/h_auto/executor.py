"""Refresh executor V1: one queue item through the frozen D2 -> D3 -> D4 -> D5 -> D6 pipeline.

Order of operations, and why:

1. **Inputs first, money second.** Prices, SEC submissions and companyfacts, the D1 stub, the D2
   bundle and the D2.1 package are built before any model call (``h_auto.inputs``). A data gap here
   is ``DATA_NOT_READY`` and costs nothing. A package that did not chunk any of the filings that
   triggered the refresh would re-research the old thesis, so it is refused the same way.
2. **D5 before D3.** A candidate without a declared ``MethodJudgement`` cannot be valued, so it is
   refused before the model is called rather than after paying for D3 and D4.
3. **Budget.** ``h_auto.budget.check`` then ``reserve``; a refusal is ``BUDGET_DEFERRED``.
4. **D3, D4** through the runners' own functions (``research_one_v3``, ``analyze_one_v2``) with the
   metered, canonical-model-checked ``call_fn``. Their bounded repair loops are theirs; this module
   never calls a stage twice. A stage without a final output ends the item: the decision stands.
5. **D5** (``build_panel`` + ``value_issuer`` under ``D5_D2R_V1``, the D6 judgement table) inside
   ``inputs.d5_binding``; **D6** ``decide`` on the projections D6 itself uses.
6. **Write.** ``transitions.record`` (cohort) or ``transitions.admit`` (new candidate), then the
   runtime thesis evidence (``h_auto.dedup.record``), then the spend settlement. A refused
   transition (e.g. REJECT->REJECT) is recorded in the queue only.

The executor never reads a forward return, never picks a threshold and never retries a completed
stage because of its result.
"""

from __future__ import annotations

import hashlib
import json
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from app.strategies.h_auto import budget as BU
from app.strategies.h_auto import contract as AC
from app.strategies.h_auto import dedup as DD
from app.strategies.h_auto import inputs as IN
from app.strategies.h_auto import providers as PV
from app.strategies.h_auto import queue as Q
from app.strategies.h_auto import store as AS
from app.strategies.h_forward import contract as FC
from app.strategies.h_forward import store as ST
from app.strategies.h_forward import transitions as TR

VERSIONS = {
    "d2_version": "h_v2_d2_collector+d2_1_materialize (depth policy unchanged)",
    "d3_version": "d3_3: prompt_v2/schema_v2/h_v2_d3_2_validation_contract_v2",
    "d4_version": "d4_2: analyze_one_v2",
    "d5_version": "D5_D2R_V1",
    "d6_version": "h_v2_d6_integrated_decision_v1",
}


@dataclass
class Deps:
    """What the executor needs from the outside world. Tests replace every field."""

    call_fn: Callable[[str, str], dict] = PV.live_call
    sec_client_factory: Callable[[], Any] = IN.default_sec_client
    judgements: Mapping[str, Any] | None = None           # default: run_strategy_h_v2_d6.METHOD_JUDGEMENTS
    now: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    # Stage seams for the fake-provider dry run. ``None`` means the frozen stage below.
    prepare_fn: Callable[..., dict[str, Any]] | None = None
    d3_fn: Callable[..., dict[str, Any]] | None = None
    d4_fn: Callable[..., dict[str, Any]] | None = None
    d5d6_fn: Callable[..., tuple[dict, Any]] | None = None


@dataclass
class Outcome:
    status: str
    reason: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _judgements(deps: Deps) -> Mapping[str, Any]:
    if deps.judgements is not None:
        return deps.judgements
    from app.dev.run_strategy_h_v2_d6 import METHOD_JUDGEMENTS
    return METHOD_JUDGEMENTS


def identity_of(item: Mapping[str, Any]) -> IN.Identity:
    snap = next((r for r in ST.launch_rows() if r.get("ticker") == item["ticker"]), None)
    if snap is None:
        snap = next((r for r in reversed(ST.ledger()) if r.get("ticker") == item["ticker"] and r.get("cik")), None)
    if snap is None:
        from app.strategies.h_forward import sec_refresh as SR
        intake = SR.read_json(ST.path("new_candidates.json")) or {}
        snap = next((c for c in intake.get("candidates") or [] if c.get("ticker") == item["ticker"]), {})
    cik = item.get("cik") or snap.get("cik")
    if not cik:
        raise IN.InputsNotReady("NO_CIK", item["ticker"])
    return IN.Identity(ticker=item["ticker"], cik=str(cik).zfill(10), security_id=snap.get("security_id"),
                       exchange=snap.get("exchange"), snapshot_date=snap.get("snapshot_date"))


def prepare(item: Mapping[str, Any], deps: Deps, *, work: Path, run_id: str) -> dict[str, Any]:
    """Every input up to the D2.1 package. No model call. Raises InputsNotReady / SecUnavailable."""
    now = deps.now()
    ticker = item["ticker"]
    if ticker not in _judgements(deps):
        raise IN.InputsNotReady("D5_METHOD_JUDGEMENT_REQUIRED", ticker)
    ident = identity_of(item)
    session = IN.decision_session(now)
    try:
        panel = IN.load_panel([ticker], session)
    except OSError as exc:
        raise IN.SecUnavailable(f"PRICE_STORE_IO_ERROR:{type(exc).__name__}") from exc
    coverage = IN.check_panel(panel, ticker, session)
    client = deps.sec_client_factory()
    try:
        sec = IN.fetch_sec(ident.cik, BU.et_day(now), client=client)
        stub = IN.build_stub(ident, sec, panel, run_id=run_id, cutoff=now)
        triggers = list(item.get("source_accessions") or []) if item.get("reason") == "REFRESH_DUE" else []
        package, meta, package_path = IN.build_package(stub, sec, run_id=run_id, cutoff=now, client=client,
                                                       work=work, triggers=triggers)
    finally:
        close = getattr(client, "close", None)
        if close:
            close()
    from app.strategies.h_forward.research_queue import chunked_accessions
    chunked = set(chunked_accessions(json.loads(package.model_dump_json())))
    triggers = set(item.get("source_accessions") or [])
    if item.get("reason") == "REFRESH_DUE" and triggers and not (triggers & chunked):
        raise IN.InputsNotReady("NEW_EVIDENCE_NOT_MATERIALIZED", ",".join(sorted(triggers)))
    return {"identity": ident, "session": session, "panel": panel, "coverage": coverage, "sec": sec,
            "stub": stub, "package": package, "package_meta": meta, "package_path": package_path,
            "cutoff": now, "triggers_chunked": sorted(triggers & chunked)}


def _d3(prep: Mapping[str, Any], model: PV.MeteredModel, *, run_id: str, work: Path) -> dict[str, Any]:
    from app.backtest.strategy_h_v2.research.research_attempt_v2 import store_attempt
    from app.dev.run_strategy_h_v2_d3_3 import research_one_v3
    model.stage = "d3"
    ident = prep["identity"]
    record = research_one_v3(prep["package"], ident.ticker, ident.cik, run_id, call_fn=model)
    store_attempt(work / "d3", record)
    return json.loads(json.dumps(record.to_dict(), default=str))


def _d4(prep: Mapping[str, Any], d3: Mapping[str, Any], model: PV.MeteredModel, *, run_id: str,
        work: Path) -> dict[str, Any]:
    from app.backtest.strategy_h_v2.expectation.ledger import store_analysis, store_evidence_bundle
    from app.dev.run_strategy_h_v2_d4_2 import analyze_one_v2, build_bundle_for
    model.stage = "d4"
    ticker = prep["identity"].ticker
    bundle = build_bundle_for(prep["package"], prep["panel"].close_panel(),
                              bundle_id=f"{run_id}:{ticker}:expectation", generated_at=prep["cutoff"])
    store_evidence_bundle(work / "d4", run_id, ticker, bundle.model_dump_json(indent=2))
    record, _spend = analyze_one_v2(package=prep["package"], research_record=dict(d3), bundle=bundle,
                                    analysis_run_id=run_id, call_fn=model)
    store_analysis(work / "d4", record)
    return json.loads(json.dumps(record.to_dict(), default=str))


def _d5_d6(prep: Mapping[str, Any], d3: Mapping[str, Any], d4: Mapping[str, Any], deps: Deps) -> tuple[dict, Any]:
    from app.backtest.strategy_h_v2.decision.d6_contract import decide
    from app.backtest.strategy_h_v2.valuation.fair_value import WindowSelectionContract
    from app.dev.run_strategy_h_v2_d5_d2 import build_panel, value_issuer
    from app.dev.run_strategy_h_v2_d6 import project_d3, project_d4, project_d5

    ticker, session, panel = prep["identity"].ticker, prep["session"], prep["panel"]
    FC.require_d5_contract(WindowSelectionContract.D5_D2R_V1.value)
    with IN.d5_binding(packages_dir=prep["package_path"].parent, panel=panel, sec=prep["sec"]):
        observed, decision_row, coverage = build_panel(ticker, panel.closes(ticker), session)
        if decision_row is None:
            raise IN.InputsNotReady("D5_NO_DECISION_ROW", json.dumps(coverage)[:300])
        row = value_issuer(ticker, observed, decision_row, judgements=_judgements(deps),
                           window_contract=WindowSelectionContract.D5_D2R_V1)
    record = decide(project_d3(ticker, dict(d3)), project_d4(ticker, dict(d4)), project_d5(row))
    return row, record


def _thesis_version(ticker: str, checksums: Mapping[str, str]) -> str:
    n = 1 + len({r.get("thesis_version") for r in ST.history(ticker)} |
                {r.get("thesis_version") for r in DD.runtime_rows(ticker)} - {None})
    body = [checksums["d3_output"], checksums["d4_output"], checksums["d5_row"], checksums["d6_decision"]]
    return f"T{n}:" + hashlib.sha256(json.dumps(body).encode()).hexdigest()[:16]


def _write(item: Mapping[str, Any], prep: Mapping[str, Any], d3: Mapping[str, Any], d4: Mapping[str, Any],
           row: Mapping[str, Any], record: Any, deps: Deps) -> dict[str, Any]:
    from app.backtest.strategy_h_v2.decision.d6_contract import Eligibility
    from app.dev.run_h_v2_d7 import _binding_clause, _valuation

    ticker = item["ticker"]
    checksums = {"d3_output": d3.get("final_output_checksum"), "d4_output": d4.get("final_output_checksum"),
                 "d5_row": IN.sha(row), "d6_decision": IN.sha(record.to_dict())}
    version = _thesis_version(ticker, checksums)
    decision = None if record.decision is None else record.decision.value
    current = ST.latest_per_ticker().get(ticker)
    previous = None if current is None else current.get("decision")
    out = {"thesis_version": version, "previous_thesis_version": None if current is None else current.get("thesis_version"),
           "previous_decision": previous, "final_decision": decision or record.eligibility.eligibility.value,
           "decision_transition": None, "ledger_written": False,
           "d6": {"eligibility": record.eligibility.eligibility.value, "approve_blockers": list(record.approve_blockers),
                  "reject_fired": list(record.reject_fired), "watch_matched": list(record.watch_matched)},
           "valuation": _valuation(row)}
    evidence = {"queue_id": item["queue_id"], "source_accessions": list(item.get("source_accessions") or []),
                "triggers_chunked": prep["triggers_chunked"], "package": str(prep["package_path"]),
                "package_sha256": prep["package_meta"]["package_sha256"], "data_cutoff": _iso(prep["cutoff"]),
                "decision_session": prep["session"].isoformat(), "price_sources": prep["coverage"]["sources"],
                "autonomy_contract": AC.contract_id(), "autonomy_contract_sha256": AC.sha256()}
    decision_time = _iso(deps.now())
    if record.eligibility.eligibility is Eligibility.DECISION_ELIGIBLE and decision is not None:
        kwargs = dict(decision=decision, thesis_version=version, decision_time=decision_time,
                      effective_session=prep["session"].isoformat(), d5_contract=AC.D5_CONTRACT,
                      valuation=out["valuation"], checksums=checksums, evidence=evidence,
                      key_binding_clause=_binding_clause(record))
        try:
            if current is None:
                written = TR.admit(ticker, cik=prep["identity"].cik, **kwargs)
            else:
                written = TR.record(ticker, cause=TR.MATERIAL_REFRESH, **kwargs)
            out["decision_transition"] = written.get("transition")
            out["ledger_written"] = True
        except TR.TransitionRefused as exc:
            out["decision_transition"] = f"{previous}->{decision}"
            out["ledger_refusal"] = str(exc)
    else:
        out["decision_transition"] = f"{previous}->NOT_DECISION_ELIGIBLE"
    DD.record(ticker=ticker, cik=prep["identity"].cik, thesis_version=version, queue_id=item["queue_id"],
              package=json.loads(prep["package"].model_dump_json()),
              submissions_visible_through=prep["sec"].submissions_fetched_at.date().isoformat(),
              package_sha256=prep["package_meta"]["package_sha256"])
    return out


def execute(item: Mapping[str, Any], deps: Deps | None = None, *, run_id: str) -> Outcome:
    """Run one queue item. The caller has already moved it to RUNNING."""
    deps = deps or Deps()
    work = AS.root() / "research" / run_id / item["queue_id"].replace(":", "_")
    work.mkdir(parents=True, exist_ok=True)
    try:
        prep = (deps.prepare_fn or prepare)(item, deps, work=work, run_id=run_id)
    except IN.InputsNotReady as exc:
        return Outcome(Q.DATA_NOT_READY, exc.code, {"last_error": str(exc)})
    except IN.SecUnavailable as exc:
        return Outcome(Q.QUEUED, str(exc), {"last_error": str(exc)}, retryable=True)

    day = BU.et_day(deps.now())
    admission = BU.check(day=day, reason=item.get("reason", ""))
    if not admission["admitted"]:
        return Outcome(Q.BUDGET_DEFERRED, admission["why"], {"budget": admission.get("position")})
    reservation_id = BU.reserve(queue_id=item["queue_id"], day=day, run_id=run_id)
    model = PV.MeteredModel(call=deps.call_fn, ceiling_usd=AC.reservation())
    result: Outcome
    try:
        d3 = (deps.d3_fn or _d3)(prep, model, run_id=run_id, work=work)
        if d3.get("final_status") != "OK" or d3.get("model_mismatch"):
            transient = d3.get("final_status") == "MODEL_CALL_FAILED"
            result = Outcome(Q.QUEUED if transient else Q.FAILED, f"D3_{d3.get('final_status')}",
                             {"last_error": d3.get("final_status")}, retryable=transient)
        else:
            d4 = (deps.d4_fn or _d4)(prep, d3, model, run_id=run_id, work=work)
            if d4.get("final_status") != "OK" or d4.get("model_mismatch"):
                transient = d4.get("final_status") == "MODEL_CALL_FAILED"
                result = Outcome(Q.QUEUED if transient else Q.FAILED, f"D4_{d4.get('final_status')}",
                                 {"last_error": d4.get("final_status")}, retryable=transient)
            else:
                row, record = (deps.d5d6_fn or _d5_d6)(prep, d3, d4, deps)
                written = _write(item, prep, d3, d4, row, record, deps)
                result = Outcome(Q.COMPLETE, None, written | {"artifacts": str(work)})
    except PV.ModelContractViolation as exc:
        result = Outcome(Q.FAILED, "MODEL_CONTRACT_VIOLATION", {"last_error": str(exc)})
    except PV.ModelProcessError as exc:
        result = Outcome(Q.QUEUED, "MODEL_PROCESS_ERROR", {"last_error": str(exc)}, retryable=True)
    except PV.BudgetCeilingReached as exc:
        result = Outcome(Q.FAILED, "BUDGET_CEILING_REACHED", {"last_error": str(exc)})
    except IN.InputsNotReady as exc:
        result = Outcome(Q.FAILED, exc.code, {"last_error": str(exc)})
    except Exception as exc:                                     # recorded, never swallowed silently
        result = Outcome(Q.FAILED, f"EXECUTOR_ERROR:{type(exc).__name__}",
                         {"last_error": "".join(traceback.format_exception_only(type(exc), exc)).strip()[:500]})
    finally:
        BU.settle(reservation_id, actual_usd=model.spent_usd, calls=model.calls, detail=model.report())
    result.fields["cost_usd"] = round(model.spent_usd, 6)
    result.fields["model"] = model.report()
    return result
