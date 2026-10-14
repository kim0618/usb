"""Is a filing already inside the thesis an issuer currently carries?

AEYE showed why the refresh scan alone cannot license a model call: the scan compares a filing date
with the decision session, while the thesis evidence was cut off later, so a filing between the two
can already be in the thesis. This module answers the question per filing, with three keys -
accession, filing date and source id (``SEC:<cik>:<accession>``) - against two indexes:

* the frozen T1 index ``docs/operations/h_thesis_evidence_v1.json`` (chunked accessions of each
  launch package; frozen because the production host does not carry the packages), and
* the runtime index ``<store>/auto/thesis_evidence.jsonl``, appended once per completed
  re-evaluation or admission with what that new thesis read.

A filing is in the thesis when its accession was chunked into a package the thesis read, or the
thesis bundle listed it among its filings, or - runtime theses only, where the bundle records when
its submissions were current - its filing date is on or before that date. The last rule exists so a
filing the D2.1 depth policy saw and chose not to materialise is not re-queued forever: re-running
D2 on the same submissions would make the same choice and spend the same money for nothing.

The frozen T1 rule is unchanged (chunked only), so AEYE, FG and VRRM classify exactly as the
preregistration recorded. Historical states and documents are never rewritten.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from app.strategies.h_auto import store as AS

IN_THESIS = "EVIDENCE_ALREADY_IN_THESIS"
NEW_EVIDENCE = "NEW_EVIDENCE"
UNKNOWN = "EVIDENCE_CHECK_UNAVAILABLE"


def _frozen() -> dict[str, Any]:
    from app.strategies.h_forward import research_queue as RQ
    return (RQ.evidence_index().get("issuers") or {})


def source_id(cik: str | None, accession: str | None) -> str | None:
    if not cik or not accession:
        return None
    return f"SEC:{str(cik).zfill(10)}:{accession}"


def runtime_rows(ticker: str | None = None) -> list[dict[str, Any]]:
    rows = AS.read_jsonl(AS.THESIS_EVIDENCE)
    return [r for r in rows if ticker is None or r.get("ticker") == ticker]


def thesis_of(ticker: str, *, frozen_entry: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """The union of what every thesis this issuer carried has read, or None if nothing is known.

    ``frozen_entry`` is the issuer's row of the frozen T1 index when the caller already holds one
    (the research queue is given its index); otherwise it is read from the docs file.
    """
    frozen = dict(frozen_entry) if frozen_entry is not None else (_frozen().get(ticker) or {})
    runtime = runtime_rows(ticker)
    chunked = set(frozen.get("chunked_accessions") or [])
    listed: set[str] = set()
    visible = None
    for row in runtime:
        chunked |= set(row.get("chunked_accessions") or [])
        listed |= set(row.get("bundle_accessions") or [])
        through = row.get("submissions_visible_through")
        if through and (visible is None or through > visible):
            visible = through
    if not chunked and not listed:
        return None
    latest = runtime[-1] if runtime else frozen
    return {"ticker": ticker, "chunked_accessions": sorted(chunked), "bundle_accessions": sorted(listed),
            "submissions_visible_through": visible, "thesis_version": latest.get("thesis_version"),
            "data_cutoff": latest.get("data_cutoff"), "sources": ["frozen"] * bool(frozen) + ["runtime"] * bool(runtime)}


def classify(ticker: str, filing: Mapping[str, Any], *, thesis: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One filing against the issuer's thesis evidence. ``filing`` carries accession and filingDate."""
    thesis = thesis if thesis is not None else thesis_of(ticker)
    accession = filing.get("accession") or filing.get("accessionNumber")
    filed = filing.get("filingDate")
    if thesis is None:
        return {"state": UNKNOWN, "rule": None, "accession": accession}
    if accession and accession in set(thesis.get("chunked_accessions") or []):
        return {"state": IN_THESIS, "rule": "CHUNKED_IN_THESIS_PACKAGE", "accession": accession}
    if accession and accession in set(thesis.get("bundle_accessions") or []):
        return {"state": IN_THESIS, "rule": "LISTED_IN_THESIS_BUNDLE", "accession": accession}
    through = thesis.get("submissions_visible_through")
    if filed and through and str(filed) <= str(through)[:10]:
        return {"state": IN_THESIS, "rule": "FILED_BEFORE_THESIS_SUBMISSIONS_DATE", "accession": accession}
    return {"state": NEW_EVIDENCE, "rule": None, "accession": accession}


def record(*, ticker: str, cik: str | None, thesis_version: str, queue_id: str, package: Mapping[str, Any],
           submissions_visible_through: str | None, package_sha256: str) -> dict[str, Any]:
    """Append what a new thesis read. Called once, after its ledger row is written."""
    from app.strategies.h_forward.research_queue import chunked_accessions
    bundle = package.get("evidence_bundle") or {}
    listed = sorted({str(s.get("source_id", "")).split(":")[2] for s in bundle.get("filings") or []
                     if str(s.get("source_id", "")).startswith("SEC:") and len(str(s.get("source_id")).split(":")) >= 3})
    row = {"ticker": ticker, "cik": cik, "thesis_version": thesis_version, "queue_id": queue_id,
           "data_cutoff": bundle.get("data_cutoff"), "package_sha256": package_sha256,
           "chunked_accessions": chunked_accessions(package), "bundle_accessions": listed,
           "submissions_visible_through": submissions_visible_through,
           "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "rule": "runtime thesis evidence (h_autonomous_pipeline_v1 evidence_dedup)"}
    AS.append_jsonl(AS.THESIS_EVIDENCE, [row])
    return row


def filter_new(ticker: str, filings: Sequence[Mapping[str, Any]]) -> tuple[list[dict], list[dict]]:
    """(new, already_in_thesis), each filing annotated with its classification."""
    thesis = thesis_of(ticker)
    new, seen = [], []
    for f in filings:
        verdict = classify(ticker, f, thesis=thesis)
        row = dict(f) | {"dedup": verdict}
        (seen if verdict["state"] == IN_THESIS else new).append(row)
    return new, seen
