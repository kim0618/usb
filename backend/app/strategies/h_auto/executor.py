"""Evaluation executor (NO-AI, V2): one queue item through D2 -> code-owned D3/D4 -> D5 -> D6.

Order of operations, and why:

1. **Inputs.** Prices, SEC submissions and companyfacts, the D1 stub, the D2 bundle and the D2.1
   package (``h_auto.inputs``). A data gap here is ``DATA_NOT_READY``. A package that did not chunk
   any of the filings that triggered the refresh is refused the same way.
2. **MethodJudgement.** A cohort issuer has one in the frozen tables; any other issuer gets a
   code-owned one (``h_auto.method_judgement``) or is refused as METHOD_JUDGEMENT_NOT_READY.
3. **D3, D4** are the deterministic generators of ``h_auto.deterministic`` under the frozen
   ``H_D3D4_DETERMINISTIC_PRODUCTION_V1`` (``docs/operations/h_d3d4_deterministic_v1.json``). **No model is called.** The whole item runs
   inside ``no_ai_guard.forbid_ai()``; an attempt to reach a model fails the item.
4. **D5** (``build_panel`` + ``value_issuer`` under ``D5_D2R_V1``, the D6 judgement table) inside
   ``inputs.d5_binding``; **D6** ``decide`` on the projections D6 itself uses, unchanged.
5. **Write.** A decision-eligible D6 result goes through ``transitions.record`` (cohort) or
   ``transitions.admit`` (new candidate) and the runtime thesis evidence. A NOT_DECISION_ELIGIBLE
   result (a refused D3, e.g. the next periodic report is underivable) writes nothing to the
   ledger: the existing decision stands and the filing is not marked as read by a thesis.

V1 (``H_V2_AUTONOMOUS_PIPELINE_V1``) ran the research-time Opus runners here under a budget. That
wiring was the incident this version repairs; its artifacts are history.

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

from app.strategies.h_auto import clock as CK
from app.strategies.h_auto import contract as AC
from app.strategies.h_auto import dedup as DD
from app.strategies.h_auto import deterministic as DT
from app.strategies.h_auto import inputs as IN
from app.strategies.h_auto import no_ai_guard as NG
from app.strategies.h_auto import queue as Q
from app.strategies.h_auto import store as AS
from app.strategies.h_forward import contract as FC
from app.strategies.h_forward import store as ST
from app.strategies.h_forward import transitions as TR

VERSIONS = {
    "d2_version": "h_v2_d2_collector+d2_1_materialize (depth policy unchanged)",
    "d3_version": "H_D3_DETERMINISTIC_V1 (H_D3D4_DETERMINISTIC_PRODUCTION_V1, no model)",
    "d4_version": "H_D4_DETERMINISTIC_V1 (H_D3D4_DETERMINISTIC_PRODUCTION_V1, no model)",
    "d5_version": "D5_D2R_V1",
    "d6_version": "h_v2_d6_integrated_decision_v1",
}


@dataclass
class Deps:
    """What the executor needs from the outside world. Tests replace every field."""

    sec_client_factory: Callable[[], Any] = IN.default_sec_client
    judgements: Mapping[str, Any] | None = None           # default: run_strategy_h_v2_d6.METHOD_JUDGEMENTS
    now: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))
    # Stage seams for tests (synthetic fixtures). ``None`` means the production stage below.
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


def derive_judgement(prep: Mapping[str, Any], deps: Deps, *, work: Path) -> dict[str, Any]:
    """A code-owned MethodJudgement for an issuer outside the frozen tables, before D3/D4.

    The frozen D5 panel is built once here on the same inputs ``_d5_d6`` will use (deterministic, so
    the later build is identical) and ``h_auto.method_judgement.derive`` reads the decision session's
    row. NOT_READY is ``DATA_NOT_READY:METHOD_JUDGEMENT_NOT_READY``: nothing is spent and the reasons
    are written to ``work/method_judgement.json``.
    """
    from app.dev.run_strategy_h_v2_d5_d2 import build_panel
    from app.strategies.h_auto import method_judgement as MJ
    ticker, session, panel = prep["identity"].ticker, prep["session"], prep["panel"]
    with IN.d5_binding(packages_dir=prep["package_path"].parent, panel=panel, sec=prep["sec"]):
        _observed, decision_row, coverage = build_panel(ticker, panel.closes(ticker), session)
    if decision_row is None:
        raise IN.InputsNotReady("D5_NO_DECISION_ROW", json.dumps(coverage)[:300])
    doc = prep["sec"].submissions_doc or {}
    derived = MJ.derive(ticker, decision_row, sic=doc.get("sic"), sic_description=doc.get("sicDescription"))
    report = derived.report() | {"panel_coverage": coverage}
    (work / "method_judgement.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str),
                                                encoding="utf-8")
    if derived.state != MJ.AUTO_READY:
        raise IN.InputsNotReady(MJ.NOT_READY, f"{ticker}: " + "; ".join(derived.reasons))
    return {"judgement": derived.judgement, "report": report}


def prepare(item: Mapping[str, Any], deps: Deps, *, work: Path, run_id: str) -> dict[str, Any]:
    """Every input up to the D2.1 package. Raises InputsNotReady / SecUnavailable.

    A cohort issuer is valued through the frozen judgement tables; an issuer outside them gets a
    code-owned judgement (``derive_judgement``) or is refused as METHOD_JUDGEMENT_NOT_READY. Either
    way the judgement exists before D3 and D4 run.
    """
    now = deps.now()
    ticker = item["ticker"]
    frozen = ticker in _judgements(deps)
    ident = identity_of(item)
    session = IN.decision_session(now)
    try:
        panel = IN.load_panel([ticker], session)
    except OSError as exc:
        raise IN.SecUnavailable(f"PRICE_STORE_IO_ERROR:{type(exc).__name__}") from exc
    coverage = IN.check_panel(panel, ticker, session)
    client = deps.sec_client_factory()
    try:
        sec = IN.fetch_sec(ident.cik, CK.et_day(now), client=client)
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
    prep = {"identity": ident, "session": session, "panel": panel, "coverage": coverage, "sec": sec,
            "stub": stub, "package": package, "package_meta": meta, "package_path": package_path,
            "cutoff": now, "triggers_chunked": sorted(triggers & chunked),
            "judgements": _judgements(deps), "method_judgement": {"source": "FROZEN_TABLE", "ticker": ticker}}
    if not frozen:
        derived = derive_judgement(prep, deps, work=work)
        prep["judgements"] = dict(_judgements(deps)) | {ticker: derived["judgement"]}
        prep["method_judgement"] = {"source": "CODE_OWNED_V1", "ticker": ticker,
                                    "version": derived["report"]["version"], "state": derived["report"]["state"],
                                    "primary_preference": derived["report"]["judgement"]["primary_preference"],
                                    "secondary_preference": derived["report"]["judgement"]["secondary_preference"]}
    return prep


def _d3(prep: Mapping[str, Any], *, run_id: str, work: Path) -> dict[str, Any]:
    return DT.d3_deterministic(prep, run_id=run_id, work=work)


def _d4(prep: Mapping[str, Any], d3: Mapping[str, Any], *, run_id: str, work: Path) -> dict[str, Any]:
    return DT.d4_deterministic(prep, d3, run_id=run_id, work=work)


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
        row = value_issuer(ticker, observed, decision_row, judgements=prep.get("judgements") or _judgements(deps),
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
           "valuation": _valuation(row),
           "method_judgement": dict(prep.get("method_judgement") or {"source": "FROZEN_TABLE"})}
    evidence = {"queue_id": item["queue_id"], "source_accessions": list(item.get("source_accessions") or []),
                "triggers_chunked": prep["triggers_chunked"], "package": str(prep["package_path"]),
                "package_sha256": prep["package_meta"]["package_sha256"], "data_cutoff": _iso(prep["cutoff"]),
                "decision_session": prep["session"].isoformat(), "price_sources": prep["coverage"]["sources"],
                "method_judgement": dict(prep.get("method_judgement") or {"source": "FROZEN_TABLE"}),
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
    if out["ledger_written"]:
        DD.record(ticker=ticker, cik=prep["identity"].cik, thesis_version=version, queue_id=item["queue_id"],
                  package=json.loads(prep["package"].model_dump_json()),
                  submissions_visible_through=prep["sec"].submissions_fetched_at.date().isoformat(),
                  package_sha256=prep["package_meta"]["package_sha256"])
    out["generators"] = {"d3": d3.get("generator"), "d4": d4.get("generator"),
                         "d4_gap": (d4.get("final_output") or {}).get("expectation_gap"),
                         "d4_confidence": (d4.get("final_output") or {}).get("expectation_gap_confidence")}
    return out


def execute(item: Mapping[str, Any], deps: Deps | None = None, *, run_id: str) -> Outcome:
    """Run one queue item with no AI. The caller has already moved it to RUNNING."""
    deps = deps or Deps()
    work = AS.root() / "evaluation" / run_id / item["queue_id"].replace(":", "_")
    work.mkdir(parents=True, exist_ok=True)
    blocked: list[str] = []
    try:
        with NG.forbid_ai() as guard:
            try:
                result = _evaluate(item, deps, run_id=run_id, work=work)
            finally:
                blocked = list(guard.blocked)
    except NG.ProductionAICallBlocked as exc:
        result = Outcome(Q.FAILED, "PRODUCTION_AI_CALL_BLOCKED", {"last_error": str(exc)})
    result.fields["cost_usd"] = 0.0
    result.fields["ai_calls"] = 0
    if blocked:
        result.fields["ai_calls_blocked"] = blocked
    return result


def _evaluate(item: Mapping[str, Any], deps: Deps, *, run_id: str, work: Path) -> Outcome:
    try:
        prep = (deps.prepare_fn or prepare)(item, deps, work=work, run_id=run_id)
    except IN.InputsNotReady as exc:
        from app.strategies.h_auto import method_judgement as MJ
        status = Q.METHOD_NOT_READY if exc.code == MJ.NOT_READY else Q.DATA_NOT_READY
        return Outcome(status, exc.code, {"last_error": str(exc)})
    except IN.SecUnavailable as exc:
        return Outcome(Q.QUEUED, str(exc), {"last_error": str(exc)}, retryable=True)
    try:
        d3 = (deps.d3_fn or _d3)(prep, run_id=run_id, work=work)
        d4 = (deps.d4_fn or _d4)(prep, d3, run_id=run_id, work=work)
        if d4.get("final_status") != "OK":
            return Outcome(Q.FAILED, f"D4_{d4.get('final_status')}", {"last_error": d4.get("final_status")})
        row, record = (deps.d5d6_fn or _d5_d6)(prep, d3, d4, deps)
        written = _write(item, prep, d3, d4, row, record, deps)
        return Outcome(Q.COMPLETE, None, written | {"artifacts": str(work)})
    except NG.ProductionAICallBlocked:
        raise
    except IN.InputsNotReady as exc:
        return Outcome(Q.FAILED, exc.code, {"last_error": str(exc)})
    except Exception as exc:                                     # recorded, never swallowed silently
        return Outcome(Q.FAILED, f"EXECUTOR_ERROR:{type(exc).__name__}",
                       {"last_error": "".join(traceback.format_exception_only(type(exc), exc)).strip()[:500]})
