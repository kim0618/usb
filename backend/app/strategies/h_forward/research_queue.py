"""H's research queue: material refreshes and new candidates, in one list, with no model call in it.

The refresh scan (``run_h_v2_d7.refresh_scan``) marks an issuer ``REFRESH_DUE`` when a material
filing is dated after the decision session. That comparison is too coarse to spend model money on:
the thesis evidence was cut off on 2026-09-28, twelve days after the 2026-09-16 decision session, so
a filing between the two can already be inside the thesis. AEYE's 2026-09-18 8-K is exactly that -
its text is chunked into the D2.1 package that D3 read. This module therefore checks every
``REFRESH_DUE`` filing against the accessions the thesis actually consumed (chunked evidence, from
``docs/operations/h_thesis_evidence_v1.json``, frozen from the packages because the production
host does not carry them) before it is queued for research.

The queue is a plan, not an execution: it never calls a model, and a research run consumes it only
under a preregistered batch with its own sample, model and hard cap. New-candidate intake is read
from ``new_candidates.json`` when a D1->E3 intake run has written one; until then it contributes no
row and says so (``intake_state``).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.strategies.h_forward import sec_refresh as SR
from app.strategies.h_forward import store as ST

REPO_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_INDEX = REPO_ROOT / "docs/operations/h_thesis_evidence_v1.json"
QUEUE = "research_queue.json"
NEW_CANDIDATES = "new_candidates.json"

REFRESH_DUE, NEW_CANDIDATE = "REFRESH_DUE", "NEW_CANDIDATE"
QUEUED = "QUEUED"
EVIDENCE_ALREADY_IN_THESIS = "EVIDENCE_ALREADY_IN_THESIS"
EVIDENCE_CHECK_UNAVAILABLE = "EVIDENCE_CHECK_UNAVAILABLE"
INTAKE_NOT_WIRED = "NOT_WIRED"

#: Research order. An earnings result (8-K item 2.02) or a periodic report changes the numbers D4
#: and D5 read, so it goes first; then other 8-Ks. Within a rank, the older filing first.
EARNINGS_ITEMS = {"2.02"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def chunked_accessions(package: Mapping[str, Any]) -> list[str]:
    """Accessions whose text reached D3: a filing listed but never chunked was not read."""
    out = set()
    for chunk in package.get("chunks") or []:
        source = str(chunk.get("source_id") or "")
        parts = source.split(":")
        if len(parts) >= 3 and parts[0] == "SEC":
            out.add(parts[2])
    return sorted(out)


def build_evidence_index(snapshot: Sequence[Mapping[str, Any]], *, repo_root: Path) -> dict[str, Any]:
    """The thesis evidence each launch row consumed, from the package its identity was read from."""
    issuers: dict[str, Any] = {}
    for row in snapshot:
        source = row.get("identity_source")
        path = (repo_root / source) if source else None
        if path is None or not path.is_file():
            issuers[row["ticker"]] = {"thesis_version": row.get("thesis_version"), "package": source,
                                      "state": EVIDENCE_CHECK_UNAVAILABLE}
            continue
        raw = path.read_bytes()
        package = json.loads(raw)
        issuers[row["ticker"]] = {
            "thesis_version": row.get("thesis_version"), "cik": row.get("cik"), "package": source,
            "package_sha256": hashlib.sha256(raw).hexdigest(),
            "data_cutoff": (package.get("evidence_bundle") or {}).get("data_cutoff"),
            "chunked_accessions": chunked_accessions(package)}
    return {"schema": "h_thesis_evidence_v1", "issuers": issuers,
            "rule": "an accession is in the thesis when at least one of its chunks is in the D2.1 package D3 read"}


def evidence_index(path: Path = EVIDENCE_INDEX) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"issuers": {}}


def _items_of(ticker: str, accession: str | None, caches: Mapping[str, SR.Cache]) -> str | None:
    cache = caches.get(ticker)
    for row in (cache.rows if cache else []):
        if row.get("accessionNumber") == accession:
            return row.get("items") or None
    return None


def _rank(form: str | None, items: str | None) -> int:
    if form in ("10-K", "10-Q"):
        return 1
    if form == "8-K" and items and EARNINGS_ITEMS & {i.strip() for i in items.split(",")}:
        return 1
    return 2


#: The autonomous pipeline's priority classes (``h_autonomous_pipeline_v1`` §priority). Rank 1 above
#: (earnings: 10-Q, 10-K, 8-K item 2.02) is P0, every other material refresh is P1; a new candidate is
#: P2 for E3 P1_HIGH and P3 for P2_MEDIUM. Item 7.01 alone stays P1: no code reads its text first.
PRIORITY_CLASS = {1: "P0", 2: "P1"}
NEW_CANDIDATE_CLASS = {"P1_HIGH": "P2", "P2_MEDIUM": "P3"}
CLASS_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def build(*, caches: Mapping[str, SR.Cache] | None = None, index: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The research queue from the refresh queue and any new-candidate intake. Pure read."""
    refresh = SR.read_json(ST.path("refresh_queue.json")) or {}
    index = index if index is not None else evidence_index()
    if caches is None:
        cohort = list(ST.launch_rows())
        cohort += [r for r in ST.ledger() if r.get("record") == "ADMISSION"
                   and r.get("ticker") not in {c["ticker"] for c in cohort}]
        ready = SR.readiness(cohort)
        caches = ready.get("caches") or {}
    from app.strategies.h_auto import dedup as DD
    consumed = index.get("issuers") or {}
    rows: list[dict[str, Any]] = []
    for issuer in refresh.get("issuers") or []:
        if issuer.get("state") != REFRESH_DUE:
            continue
        ticker = issuer["ticker"]
        known = consumed.get(ticker) or {}
        # The frozen T1 index entry plus what every later re-evaluation recorded (h_auto.dedup).
        thesis = DD.thesis_of(ticker, frozen_entry=known)
        filings = []
        for f in issuer.get("new_material_filings") or []:
            items = _items_of(ticker, f.get("accession"), caches)
            verdict = DD.classify(ticker, f, thesis=thesis) if thesis else {"state": DD.UNKNOWN, "rule": None}
            in_thesis = None if verdict["state"] == DD.UNKNOWN else verdict["state"] == DD.IN_THESIS
            filings.append(f | {"items": items, "in_thesis_evidence": in_thesis,
                                "dedup_rule": verdict.get("rule"),
                                "source_id": DD.source_id(issuer.get("cik"), f.get("accession")),
                                "rank": _rank(f.get("form"), items)})
        new = [f for f in filings if f["in_thesis_evidence"] is not True]
        if thesis is None:
            status = EVIDENCE_CHECK_UNAVAILABLE
        elif not new:
            status = EVIDENCE_ALREADY_IN_THESIS
        else:
            status = QUEUED
        needs = status != EVIDENCE_ALREADY_IN_THESIS
        rank = min((f["rank"] for f in new), default=9)
        rows.append({"ticker": ticker, "cik": issuer.get("cik"), "reason": REFRESH_DUE,
                     "source_event": new or filings, "priority_rank": rank,
                     "priority_class": PRIORITY_CLASS.get(rank, "P1"),
                     "first_new_filing": min((f["filingDate"] for f in new), default=None),
                     "queued_at": refresh.get("generated_at"), "research_status": status,
                     "thesis_version": known.get("thesis_version"),
                     "thesis_data_cutoff": known.get("data_cutoff"),
                     "d3_needed": needs, "d4_needed": needs, "d5_needed": needs, "d6_needed": needs})
    intake = SR.read_json(ST.path(NEW_CANDIDATES))
    ledgered = set(ST.latest_per_ticker())
    for cand in (intake or {}).get("candidates") or []:
        if cand["ticker"] in ledgered:
            continue                      # already in H's cohort: it is refreshed, not admitted again
        cls = NEW_CANDIDATE_CLASS.get(str(cand.get("e3_priority")), "P3")
        rows.append({"ticker": cand["ticker"], "cik": cand.get("cik"), "reason": NEW_CANDIDATE,
                     "source_event": cand.get("source_event"), "priority_rank": 3 + CLASS_ORDER[cls] - 2,
                     "priority_class": cls, "e3_priority": cand.get("e3_priority"),
                     "intake_order": cand.get("intake_order"),
                     "first_new_filing": None, "queued_at": (intake or {}).get("generated_at"),
                     "research_status": QUEUED, "thesis_version": None, "thesis_data_cutoff": None,
                     "d3_needed": True, "d4_needed": True, "d5_needed": True, "d6_needed": True})
    rows.sort(key=lambda r: (r["research_status"] != QUEUED, CLASS_ORDER[r["priority_class"]],
                             r["first_new_filing"] or "9999", r.get("intake_order") or 0, r["ticker"]))
    for position, row in enumerate(rows, 1):
        row["priority"] = position
    return {"generated_at": _now(), "schema": "h_research_queue_v1",
            "refresh_queue_generated_at": refresh.get("generated_at"),
            "refresh_queue_cache_as_of": refresh.get("cache_as_of"),
            "intake_state": "INTAKE_FILE_PRESENT" if intake else INTAKE_NOT_WIRED,
            "rows": rows,
            "queued": [r["ticker"] for r in rows if r["research_status"] == QUEUED],
            "model_calls_scheduled": 0,
            "note": "큐는 계획이다. 모델 호출은 사전등록된 배치(표본·모델·hard cap)로만 실행된다"}


def write(body: Mapping[str, Any]) -> str:
    return SR.atomic_write_json(ST.path(QUEUE), body)
