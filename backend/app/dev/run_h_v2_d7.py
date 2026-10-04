"""H-V2-D7: launch Strategy H as a forward shadow beside the A/E official paper, and operate it.

Strategy H's research is finished. D1-D6 produced, on the 2026-09-16 decision session, eight
decision-eligible issuers: no APPROVE, six WATCH, two REJECT. This step does not research anything.
It freezes that cohort into an immutable launch snapshot, writes H's append-only forward ledger, and
puts H on the same operating screens A and E already use - additively, with no change to either.

**Why the valuation is recomputed rather than read.** The launch snapshot must state which contract
produced each number, and the only way to state that honestly is to produce them here, with
``WindowSelectionContract.D5_D2R_V1`` passed explicitly, and then check that what comes out matches
the stored D5-D2R artifact byte for byte. The two published steps stay pinned to ``D5_D2_V1`` at
their own call sites, so this run cannot alter them. A launch under any other window contract is
refused by ``app.strategies.h_forward.contract.require_d5_contract``.

**Why the forward clock starts after launch, not at the decision session.** Three weeks separate the
decision session from this launch. Measuring 1D/5D/21D/63D from 2026-09-16 would backfill twelve
sessions of already-known prices into a "forward" result. The baseline is therefore the last settled
session at launch, every horizon lies in the future, and the 2026-09-16 -> baseline interval is
reported separately as pre-launch drift that is explicitly not forward evidence.

Offline except for daily price collection (one Massive grouped-daily call per session). Zero model
calls, zero research cost. Commands::

    python -m app.dev.run_h_v2_d7 preflight      # replay, verify, show what launch would write
    python -m app.dev.run_h_v2_d7 launch         # write the snapshot and the ledger (once)
    python -m app.dev.run_h_v2_d7 collect-prices # fetch missing sessions into H's own store
    python -m app.dev.run_h_v2_d7 update         # the daily job: prices, outcomes, refresh queue
    python -m app.dev.run_h_v2_d7 refresh-scan   # which issuers have new material evidence
    python -m app.dev.run_h_v2_d7 status         # the cohort as the files state it
    python -m app.dev.run_h_v2_d7 verify         # integrity of the snapshot, ledger and contract
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.backtest.strategy_c_e0.sec_store import read_gz_json, rows_of
from app.backtest.strategy_h_v2.decision.d6_contract import Eligibility, decide
from app.backtest.strategy_h_v2.valuation.fair_value import WindowSelectionContract
from app.market.calendar import MarketCalendar
from app.strategies.h_forward import cohort as CO
from app.strategies.h_forward import contract as C
from app.strategies.h_forward import outcomes as OUT
from app.strategies.h_forward import prices as PR
from app.strategies.h_forward import store as ST
from app.strategies.h_forward import views as VW

D7_CONTRACT = "h_v2_d7_forward_shadow_paper_integration_v1"
RUNTIME_ROOT = Path("data/runtime/strategy_h_v2")
D2R_DIR = RUNTIME_ROOT / "d5_d2r"
D2_1_ROOT = RUNTIME_ROOT / "d2_1"
REFRESH_QUEUE = "refresh_queue.json"

#: Where this repository already keeps raw SEC submissions. The refresh scan reads these caches and
#: never fetches: the acquisition layer owns fetching (``app.dev.acquire_strategy_h_v2_fundamentals``)
#: and its raw-source immutability guarantees are not re-implemented here.
SUBMISSION_ROOTS = (
    RUNTIME_ROOT / "d1_1/sec_raw",
    Path("data/runtime/strategy_h/h_pv2c/sec_raw"),
    Path("data/runtime/strategy_c/e0/raw"),
    Path("data/runtime/strategy_h/h0_5/sec_raw"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _enum(value: Any) -> str | None:
    """An enum's declared value, or ``None``. Never the repr of a Python object."""
    return None if value is None else getattr(value, "value", str(value))


def _sha(body: Any) -> str:
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                   default=str).encode()).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# -------------------------------------------------------------------------------------------------
# Prices: H's own store, never the frozen historical mirror
# -------------------------------------------------------------------------------------------------

def cohort_symbols() -> list[str]:
    """The symbols the price store tracks: the launch cohort (or D4's universe before launch) + SPY."""
    launched = [row["ticker"] for row in ST.launch_rows()]
    if launched:
        return sorted(set(launched) | {C.benchmark()})
    from app.dev.run_strategy_h_v2_d6 import D4_UNIVERSE
    return sorted(set(D4_UNIVERSE) | {C.benchmark()})


def collect_prices(*, upto: str | None = None, start: str | None = None) -> dict[str, Any]:
    """Fetch every expected session the store is missing, from the decision session to ``upto``."""
    from app.core.config import get_settings
    from app.integrations.massive.client import build_massive_client

    begin = start or C.decision_session()
    end = upto or date.today().isoformat()
    missing = PR.gaps(begin, end)
    if not missing:
        return {"written": [], "already_stored": [], "no_result": [], "failed": {},
                "requested": [], "latest_session": PR.latest_session()}
    client = build_massive_client(get_settings())
    report = PR.collect(missing, cohort_symbols(), client=client, fetched_at=_now())
    return report | {"requested": missing, "latest_session": PR.latest_session()}


# -------------------------------------------------------------------------------------------------
# Replay the frozen thesis under the required D5 contract
# -------------------------------------------------------------------------------------------------

def latest_d2r_artifact() -> Path:
    paths = sorted(D2R_DIR.glob("D5_D2R-*.json"))
    if not paths:
        raise SystemExit("no D5-D2R artifact found; D7 cannot launch without the repaired valuation")
    return paths[-1]


def replay() -> dict[str, Any]:
    """Value D4's thirteen under D5_D2R_V1 and decide them, using the published code unchanged."""
    from app.dev.run_strategy_h_v2_d4_1 import load_price_panel
    from app.dev.run_strategy_h_v2_d5_d2 import DECISION_SESSION, build_panel, value_issuer
    from app.dev.run_strategy_h_v2_d6 import (
        D4_UNIVERSE, METHOD_JUDGEMENTS as D6_JUDGEMENTS, load_legs, project_d5)

    contract_name = WindowSelectionContract.D5_D2R_V1.value
    C.require_d5_contract(contract_name)
    # The frozen contract names the decision session in words; the published runner owns it as a
    # date. If the two ever disagree the launch is valuing a different session than it claims to.
    if DECISION_SESSION.isoformat() != C.decision_session():
        raise SystemExit(f"decision session mismatch: contract {C.decision_session()} "
                         f"vs published {DECISION_SESSION.isoformat()}")

    closes = load_price_panel(D4_UNIVERSE)
    legs = load_legs()
    rows: dict[str, dict] = {}
    records: dict[str, Any] = {}
    for ticker in D4_UNIVERSE:
        panel, decision, _ = build_panel(ticker, closes[ticker], DECISION_SESSION)
        if decision is None:
            continue
        row = value_issuer(ticker, panel, decision, judgements=D6_JUDGEMENTS,
                           window_contract=WindowSelectionContract.D5_D2R_V1)
        rows[ticker] = row
        records[ticker] = decide(legs[ticker].d3, legs[ticker].d4, project_d5(row))
    return {"rows": rows, "records": records, "legs": legs, "d5_contract": contract_name}


def verify_replay(replayed: Mapping[str, Any]) -> dict[str, Any]:
    """Does this run reproduce the published D5-D2R artifact? A mismatch blocks the launch.

    The check is on the artifact's own `rows_new` and `d6_counterfactual.decisions`, compared by
    canonical hash, so a change anywhere in the valuation or the decision engine shows up here as a
    refusal to launch rather than as a quietly different launch snapshot.
    """
    path = latest_d2r_artifact()
    stored = json.loads(path.read_text(encoding="utf-8"))
    rows, records = replayed["rows"], replayed["records"]
    row_mismatch = [t for t, row in rows.items()
                    if _sha(row) != _sha(stored["rows_new"].get(t))]
    stored_decisions = {d["ticker"]: d for d in stored["d6_counterfactual"]["decisions"]}
    decision_mismatch = [t for t, record in records.items()
                         if _sha(record.to_dict()) != _sha(stored_decisions.get(t))]
    counts = {state: 0 for state in C.DECISION_STATES}
    for record in records.values():
        if record.decision is not None:
            counts[record.decision.value] += 1
    return {
        "artifact": str(path), "artifact_sha256": _file_sha(path),
        "artifact_contract": stored.get("contract"),
        "artifact_window_contract": stored.get("new_window_contract"),
        "rows_compared": len(rows), "row_mismatch": sorted(row_mismatch),
        "decisions_compared": len(records), "decision_mismatch": sorted(decision_mismatch),
        "decision_counts": counts,
        "decision_counts_match": counts == stored["d6_counterfactual"]["new_decision_counts"],
        "reproduces": not row_mismatch and not decision_mismatch
                      and counts == stored["d6_counterfactual"]["new_decision_counts"],
    }


# -------------------------------------------------------------------------------------------------
# Identity
# -------------------------------------------------------------------------------------------------

def _package_dirs() -> list[Path]:
    return sorted((p / "packages") for p in D2_1_ROOT.glob("D2_1-*") if (p / "packages").is_dir())


def identity(ticker: str, d3_record_cik: str | None) -> dict[str, Any]:
    """``cik`` and ``security_id``, from the evidence package D3 actually consumed.

    The CIK the D3 attempt recorded is the authority; the package supplies the FIGI-based
    ``security_id`` that D1 assigned. A package whose CIK disagrees with D3's raises, because an
    identity mismatch means the snapshot would be labelling the wrong issuer.
    """
    for packages in reversed(_package_dirs()):
        path = packages / f"{ticker}.json"
        if not path.is_file():
            continue
        bundle = (json.loads(path.read_text(encoding="utf-8")).get("evidence_bundle") or {})
        ident = bundle.get("identity") or {}
        if d3_record_cik and ident.get("cik") and ident["cik"] != d3_record_cik:
            raise SystemExit(f"{ticker}: package CIK {ident['cik']} != D3 CIK {d3_record_cik}")
        return {"cik": ident.get("cik") or d3_record_cik, "security_id": ident.get("security_id"),
                "exchange": ident.get("exchange"), "snapshot_date": ident.get("snapshot_date"),
                "identity_source": str(path.relative_to(Path.cwd())) if path.is_absolute() else str(path)}
    return {"cik": d3_record_cik, "security_id": None, "exchange": None, "snapshot_date": None,
            "identity_source": None}


# -------------------------------------------------------------------------------------------------
# The launch snapshot
# -------------------------------------------------------------------------------------------------

def _binding_clause(record: Any) -> str:
    """The one clause that decided this issuer, named in the contract's own vocabulary."""
    if record.reject_fired:
        return f"REJECT:{record.reject_fired[0]}"
    if record.approve_blockers:
        return f"APPROVE_BLOCKED:{record.approve_blockers[0]}"
    return "ALL_APPROVE_CLAUSES_HOLD"


def _valuation(row: Mapping[str, Any]) -> dict[str, Any]:
    targets = row.get("target_prices") or {}
    legs = row.get("fair_value") or {}
    bear_leg = legs.get("bear") or {}
    return {
        "status": row.get("status"),
        "primary_method": row.get("primary_method"),
        "secondary_method": row.get("secondary_method"),
        "contract_window": row.get("contract_window"),
        "contract_window_reason": row.get("contract_window_reason"),
        "confidence": (row.get("confidence") or {}).get("confidence"),
        "confidence_drivers": list((row.get("confidence") or {}).get("drivers") or ()),
        "reconciliation": (row.get("reconciliation") or {}).get("status"),
        "tp1": targets.get("TP1"), "tp2": targets.get("TP2"),
        "bear": targets.get("BEAR_ANCHOR"),
        "bear_refusal": bear_leg.get("refusal"),
        "bear_refusal_reason": bear_leg.get("reason") if bear_leg.get("refusal") else None,
        "upside_to_tp1": targets.get("upside_to_TP1"),
        "upside_to_tp2": targets.get("upside_to_TP2"),
        "downside_to_bear": targets.get("downside_to_bear"),
        # A range that lost a leg is reported as incomplete. VRRM's MEDIUM confidence carrying an
        # incomplete range is D5-D2R's own declared limitation, handed to D7 unresolved.
        "range_complete": bool(targets.get("TP1") is not None and targets.get("TP2") is not None
                               and targets.get("BEAR_ANCHOR") is not None),
        "valuation_risk": list(row.get("valuation_risk") or ()),
    }


def build_snapshot(replayed: Mapping[str, Any], *, baseline: str, launched_at: str,
                   cohort_tag: str = C.INITIAL_COHORT) -> list[dict[str, Any]]:
    """One immutable launch record per decision-eligible issuer."""
    rows, records, legs = replayed["rows"], replayed["records"], replayed["legs"]
    bars = PR.bars_of(baseline)
    out: list[dict[str, Any]] = []
    for ticker, record in records.items():
        if record.eligibility.eligibility is not Eligibility.DECISION_ELIGIBLE:
            continue
        row = rows[ticker]
        d3, d4 = legs[ticker].d3, legs[ticker].d4
        valuation = _valuation(row)
        d5_sha, d6_sha = _sha(row), _sha(record.to_dict())
        checksums = {
            "d3_output": d3.research_output_checksum, "d4_output": d4.final_output_checksum,
            "d5_row": d5_sha, "d6_decision": d6_sha,
            "d2r_artifact": replayed["verification"]["artifact_sha256"],
            "d7_contract": C.state()["contract_sha256"],
        }
        thesis_version = "T1:" + _sha([checksums["d3_output"], checksums["d4_output"], d5_sha, d6_sha])[:16]
        bar = bars.get(ticker)
        out.append({
            "record": "LAUNCH", "strategy_id": C.STRATEGY_H, "ticker": ticker,
            "cohort_tag": cohort_tag,
            **identity(ticker, _d3_cik(legs, ticker)),
            "d5_contract": replayed["d5_contract"],
            "d6_contract": record.contract_version,
            "d7_contract": D7_CONTRACT,
            "decision_session": C.decision_session(),
            "decision_close": row.get("current_price"),
            "decision": None if record.decision is None else record.decision.value,
            "eligibility": record.eligibility.eligibility.value,
            "thesis_version": thesis_version,
            "d3": {"provenance": d3.provenance.value, "research_id": d3.research_id,
                   "research_completeness": d3.research_completeness,
                   "checksum": d3.research_output_checksum},
            "d4": {"provenance": d4.provenance.value, "analysis_id": d4.analysis_id,
                   "expectation_gap": _enum(d4.gap), "gap_confidence": _enum(d4.gap_confidence),
                   "d6_approve_precondition": d4.d6_approve_precondition,
                   "checksum": d4.final_output_checksum,
                   "consumed_research_id": d4.research_input_id,
                   "consumed_research_checksum": d4.research_input_checksum},
            "d4_expectation_gap": _enum(d4.gap), "d4_gap_confidence": _enum(d4.gap_confidence),
            "valuation": valuation,
            "d6": {"decision": None if record.decision is None else record.decision.value,
                   "contract_version": record.contract_version,
                   "approve_blockers": list(record.approve_blockers),
                   "reject_fired": list(record.reject_fired),
                   "watch_matched": list(record.watch_matched)},
            "approve_blockers": list(record.approve_blockers),
            "reject_fired": list(record.reject_fired),
            "watch_matched": list(record.watch_matched),
            "key_binding_clause": _binding_clause(record),
            "checksums": checksums,
            "launch_timestamp": launched_at,
            "launch_baseline_session": baseline,
            "launch_price": None if bar is None else bar.close,
            "launch_price_source": PR.SOURCE,
            "position": None,
            "position_reason": CO.position_reason(None if record.decision is None
                                                  else record.decision.value),
        })
    out.sort(key=lambda r: r["ticker"])
    return out


def _d3_cik(legs: Mapping[str, Any], ticker: str) -> str | None:
    """The CIK the D3 attempt recorded, read from the attempt file the leg came from."""
    for run in sorted(RUNTIME_ROOT.glob("d3_3/attempts/*")):
        folder = run / ticker
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            cik = json.loads(path.read_text(encoding="utf-8")).get("cik")
            if cik:
                return str(cik)
    return None


def ledger_rows_for(snapshot: Sequence[Mapping[str, Any]], *, launched_at: str,
                    baseline: str) -> list[dict[str, Any]]:
    """The first forward-ledger row per issuer: the decision state H starts observing from."""
    return [{
        "strategy_id": C.STRATEGY_H, "ticker": row["ticker"],
        "thesis_version": row["thesis_version"],
        "decision_time": launched_at,
        "record": "LAUNCH_STATE",
        "cohort_tag": row["cohort_tag"],
        "decision": row["decision"], "previous_decision": None,
        "transition": None, "cause": "LAUNCH",
        "effective_session": baseline,
        "decision_session": row["decision_session"],
        "current_price": row["launch_price"],
        "tp1": row["valuation"]["tp1"], "tp2": row["valuation"]["tp2"],
        "bear": row["valuation"]["bear"],
        "bear_refusal": row["valuation"]["bear_refusal"],
        "valuation_confidence": row["valuation"]["confidence"],
        "valuation_method": row["valuation"]["primary_method"],
        "valuation_window": row["valuation"]["contract_window"],
        "key_binding_clause": row["key_binding_clause"],
        "position": None, "position_reason": row["position_reason"],
        "d3_checksum": row["checksums"]["d3_output"],
        "d4_checksum": row["checksums"]["d4_output"],
        "d5_checksum": row["checksums"]["d5_row"],
        "d6_checksum": row["checksums"]["d6_decision"],
        "d5_contract": row["d5_contract"],
    } for row in snapshot]


# -------------------------------------------------------------------------------------------------
# Material-event refresh queue (§13): what would need re-evaluating, from cached SEC filings only
# -------------------------------------------------------------------------------------------------

def _cached_submissions(cik: str) -> tuple[list[dict[str, Any]], str | None]:
    name = f"CIK{str(cik).zfill(10)}"
    for root in SUBMISSION_ROOTS:
        path = root / "submissions" / name / f"{name}.json.gz"
        if path.is_file():
            return rows_of(read_gz_json(path)), str(path)
    return [], None


def refresh_scan() -> dict[str, Any]:
    """Which cohort issuers have material evidence the thesis has not seen.

    Reads only the caches this repository already holds, and is careful about what that licenses it
    to say. A cache fetched before the decision session cannot show a newer filing at all
    (``CACHE_NOT_NEWER_THAN_THESIS``). A cache fetched after it, but before today, licenses only
    "nothing new *as of the cache date*" (``NO_NEW_MATERIAL_EVIDENCE_AS_OF_CACHE``), which is a
    weaker claim than "nothing new"; every row therefore carries ``evidence_known_through``. Only a
    cache refreshed today supports the flat ``NO_NEW_MATERIAL_EVIDENCE``. Dropping that qualifier is
    how a stale cache turns into a false all-clear.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    forms = set(C.material_events()) | {"10-Q", "10-K", "8-K"}
    out: list[dict[str, Any]] = []
    for snap in ST.launch_rows():
        cik = snap.get("cik")
        thesis_cutoff = (snap.get("d3") or {}).get("research_id") or ""
        asof = snap.get("snapshot_date")
        rows, source = _cached_submissions(cik) if cik else ([], None)
        material = [r for r in rows if r.get("form") in forms and r.get("filingDate")]
        latest = max((r["filingDate"] for r in material), default=None)
        cache_mtime = None
        if source:
            cache_mtime = datetime.fromtimestamp(Path(source).stat().st_mtime, timezone.utc).date().isoformat()
        decision = snap.get("decision_session")
        newer = [r for r in material if decision and r["filingDate"] > decision]
        out.append({
            "ticker": snap["ticker"], "cik": cik,
            "thesis_research_id": thesis_cutoff,
            "decision_session": decision,
            "submissions_cache": source,
            "cache_fetched_on": cache_mtime,
            "latest_material_filing": latest,
            "new_material_filings": [{"form": r["form"], "filingDate": r["filingDate"],
                                      "accession": r.get("accessionNumber")} for r in newer],
            "evidence_known_through": cache_mtime,
            "state": ("NO_SUBMISSIONS_CACHE" if source is None
                      else "REFRESH_DUE" if newer
                      else "CACHE_NOT_NEWER_THAN_THESIS" if (cache_mtime or "") <= (decision or "")
                      else "NO_NEW_MATERIAL_EVIDENCE" if cache_mtime == today
                      else "NO_NEW_MATERIAL_EVIDENCE_AS_OF_CACHE"),
        })
    body = {"generated_at": _now(), "contract": C.contract_id(), "material_forms": sorted(forms),
            "fetch_performed": False,
            "fetch_owner": "app.dev.acquire_strategy_h_v2_fundamentals (raw-source immutable, cached)",
            "issuers": out,
            "refresh_due": [r["ticker"] for r in out if r["state"] == "REFRESH_DUE"],
            "unverified_since_cache": [r["ticker"] for r in out
                                       if r["state"] == "NO_NEW_MATERIAL_EVIDENCE_AS_OF_CACHE"],
            "claim_limit": ("이 큐는 로컬 SEC 캐시만 읽는다. 캐시 날짜 이후의 공시는 보지 못하므로 "
                            "NO_NEW_MATERIAL_EVIDENCE_AS_OF_CACHE는 '새 증거 없음'이 아니라 "
                            "'캐시 시점까지 새 증거 없음'이다")}
    target = ST.path(REFRESH_QUEUE)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
                      encoding="utf-8")
    return body


# -------------------------------------------------------------------------------------------------
# Commands
# -------------------------------------------------------------------------------------------------

def _prepare(*, collect: bool, launched_at: str) -> dict[str, Any]:
    """One replay, verified, with the snapshot it would write. Shared by preflight and launch.

    The replay is the expensive step (every issuer is revalued over its whole observed panel), and
    running it twice would also mean the launch could in principle write something the preflight
    never checked. So it runs once and both commands read the same result.
    """
    price_report = collect_prices() if collect else {"latest_session": PR.latest_session()}
    baseline = PR.latest_session()
    replayed = dict(replay())
    verification = verify_replay(replayed)
    replayed["verification"] = verification
    snapshot = (build_snapshot(replayed, baseline=baseline, launched_at=launched_at)
                if baseline and verification["reproduces"] else [])
    return {"contract": C.state(), "prices": price_report, "baseline_session": baseline,
            "verification": verification, "would_write_snapshot_rows": len(snapshot),
            "snapshot": snapshot, "already_launched": ST.launched(),
            "launched_at": launched_at,
            "blockers": _launch_blockers(baseline, verification)}


def preflight(*, collect: bool = True) -> dict[str, Any]:
    return _prepare(collect=collect, launched_at=_now())


def _launch_blockers(baseline: str | None, verification: Mapping[str, Any]) -> list[str]:
    blockers = []
    if ST.launched():
        blockers.append("ALREADY_LAUNCHED")
    if baseline is None:
        blockers.append("NO_PRICE_SESSION_STORED")
    if not verification["reproduces"]:
        blockers.append("REPLAY_DOES_NOT_REPRODUCE_D5_D2R_ARTIFACT")
    if verification["artifact_window_contract"] != C.required_d5_contract():
        blockers.append("ARTIFACT_IS_NOT_D5_D2R_V1")
    return blockers


def launch() -> dict[str, Any]:
    launched_at = _now()
    pre = _prepare(collect=True, launched_at=launched_at)
    if pre["blockers"]:
        return {"launched": False, "blockers": pre["blockers"], "preflight": pre}
    baseline, snapshot = pre["baseline_session"], pre["snapshot"]
    written = ST.append_snapshots(snapshot)
    ledger = ST.append_ledger(ledger_rows_for(snapshot, launched_at=launched_at, baseline=baseline))
    queue = refresh_scan()
    return {"launched": True, "snapshot_rows": written, "ledger_rows": ledger,
            "baseline_session": baseline, "launched_at": launched_at,
            "decision_counts": CO.counts(), "refresh_due": queue["refresh_due"],
            "verification": pre["verification"]}


def update() -> dict[str, Any]:
    """The daily job: collect the settled session, re-read outcomes, refresh the event queue.

    No decision moves here. A transition requires a D3->D4->D5->D6 re-evaluation on new material
    evidence, which this command can only queue, never perform.
    """
    price_report = collect_prices()
    body = VW.forward()
    queue = refresh_scan() if ST.launched() else {"refresh_due": []}
    return {"prices": price_report, "decision_counts": body["decision_counts"],
            "maturity": body["maturity"], "evaluation": body["evaluation"],
            "refresh_due": queue["refresh_due"],
            "decisions_changed": 0,
            "note": "가격 갱신은 결정을 바꾸지 않는다 (D7 계약 §transitions)"}


def verify() -> dict[str, Any]:
    """Integrity of what has been written, independent of how it was written."""
    snapshot = ST.launch_rows()
    ledger = ST.ledger()
    problems: list[str] = []
    if snapshot:
        wrong_contract = [r["ticker"] for r in snapshot if r.get("d5_contract") != C.required_d5_contract()]
        if wrong_contract:
            problems.append(f"D5 contract is not {C.required_d5_contract()}: {wrong_contract}")
        duplicated = [t for t in {r["ticker"] for r in snapshot}
                      if sum(1 for r in snapshot if r["ticker"] == t) > 1]
        if duplicated:
            problems.append(f"duplicate launch records: {sorted(duplicated)}")
        zero_bear = [r["ticker"] for r in snapshot
                     if r["valuation"].get("bear") == 0 or
                     (r["valuation"].get("bear_refusal") and r["valuation"].get("bear") is not None)]
        if zero_bear:
            problems.append(f"a refused Bear carries a value: {sorted(zero_bear)}")
        positions = [r["ticker"] for r in snapshot if r.get("position")]
        if positions:
            problems.append(f"a launch record carries a position: {sorted(positions)}")
    allowed = C.allowed_transitions()
    for row in ledger:
        previous, now = row.get("previous_decision"), row.get("decision")
        if previous is None or now is None:
            continue
        if (previous, now) not in allowed:
            problems.append(f"{row.get('ticker')}: {previous}->{now} is not an allowed transition")
        if row.get("cause") in (None, "PRICE"):
            problems.append(f"{row.get('ticker')}: a transition with no evidential cause")
    thesis_versions = {(r.get("ticker"), r.get("thesis_version")): r for r in ledger}
    return {"snapshot_rows": len(snapshot), "ledger_rows": len(ledger),
            "distinct_thesis_versions": len(thesis_versions),
            "contract_sha256": C.state()["contract_sha256"],
            "problems": problems, "verdict": "PASS" if not problems else "FAIL"}


def status() -> dict[str, Any]:
    body = VW.forward()
    return {"launch": body["launch"], "decision_counts": body["decision_counts"],
            "maturity": body["maturity"], "evaluation": {k: v for k, v in body["evaluation"].items()
                                                         if k != "maturity"},
            "price_store": body["price_store"],
            "rows": [{"ticker": r["ticker"], "decision": r["decision"],
                      "method": r["valuation_method"], "window": r["valuation_window"],
                      "confidence": r["valuation_confidence"],
                      "price": r["current_price"], "tp1": r["tp1"], "tp2": r["tp2"],
                      "bear": r["bear"] if r["bear"] is not None else r["bear_na_reason"],
                      "tp1_distance": r["tp1_distance"],
                      "clause": r["key_binding_clause"],
                      "1D": r["forward"].get("1D", {}).get("state"),
                      "21D": r["forward"].get("21D", {}).get("state"),
                      "63D": r["forward"].get("63D", {}).get("state")}
                     for r in body["rows"]]}


COMMANDS = {"preflight": preflight, "launch": launch, "collect-prices": collect_prices,
            "update": update, "refresh-scan": refresh_scan, "status": status, "verify": verify}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--no-collect", action="store_true", help="preflight without fetching prices")
    args = parser.parse_args(argv)
    if args.command == "preflight":
        body = preflight(collect=not args.no_collect)
    else:
        body = COMMANDS[args.command]()
    print(json.dumps(body, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
