"""Builds `ExpectationEvidenceBundleV1` from a D2.1 package and a PIT price panel. Code only - no
model call, no network, no interpretation.

What this module decides and what it refuses to decide:

- It LOCATES guidance and management-expectation language deterministically, by the frozen term
  lists in `guidance_arithmetic`, and hands over the source's own words with a citable
  `evidence_id`. It never reads them.
- It COMPUTES every price number through `price_engine`, and attaches the event alignment's own
  ambiguity flag rather than smoothing it away.
- It REFUSES to compute an earnings or revenue multiple. That refusal is measured, not stylistic:
  across the 2,010 D2.1 packages, `fundamentals.revenue` resolves to `OK` for 353 (17.6%), and
  among the 268 candidates where both revenue and operating income resolve, 8 (3.0%) report
  operating income LARGER than revenue - an impossible relation, which means the level fields are
  not reliably the same period and scope even when their status says OK. A PER or EV/Sales built on
  that would be wrong for a material minority and unavailable for the large majority, and a
  valuation input that is quietly wrong 3% of the time is worse than one that is honestly absent.
  `valuation_context_stub` therefore carries only what is exactly computable from instant-dated
  balance-sheet and share-count facts, and states `NOT_COMPUTABLE` for the rest, with the reason.

The D5 Valuation Engine is where multiples belong, and it will need a fundamentals layer this one
does not have. That is a finding D4 hands forward, not a gap D4 papers over.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import re
from typing import Any, Mapping

from app.backtest.strategy_h_v2.evidence.chunk_schema import AIResearchInputV1, EvidenceChunk
from app.backtest.strategy_h_v2.evidence.sources import DatePrecision, SourceProvenance
from app.backtest.strategy_h_v2.expectation.evidence_schema import (
    MAX_EXCERPT_CHARS,
    MAX_EXCERPTS_PER_BLOCK,
    EvidenceBlock,
    EventPriceReaction,
    ExpectationEvidenceBundleV1,
    LocatedExcerpt,
)
from app.backtest.strategy_h_v2.expectation.gap_contract import EvidenceAvailability
from app.backtest.strategy_h_v2.expectation.guidance_arithmetic import (
    GUIDANCE_TERMS,
    MANAGEMENT_SIGNAL_TERMS,
)
from app.backtest.strategy_h_v2.expectation import price_engine

#: Characters of surrounding context kept on each side of a located term, so the excerpt carries
#: the sentence rather than the keyword. Bounded by `MAX_EXCERPT_CHARS` at the schema.
EXCERPT_CONTEXT_CHARS = 420

#: Why no consensus block exists, stated once and reused. Measured across the full D2.1 snapshot:
#: `earnings.status` is `UNKNOWN` in 2,010 of 2,010 packages.
CONSENSUS_ABSENT_NOTE = (
    "No PIT analyst-consensus provider is connected to this repository. Measured across the full "
    "D2.1 snapshot, evidence_bundle.earnings.status is UNKNOWN for 2,010 of 2,010 candidates. This "
    "is a statement that the source does not exist here, not that consensus is neutral or zero."
)
ESTIMATE_REVISIONS_ABSENT_NOTE = (
    "No structured estimate-revision history provider is connected. D0 §M already recorded this as "
    "DEFERRED (H0 §4 'H4 REVISION = DEFERRED') and nothing has connected one since."
)
MULTIPLES_NOT_COMPUTABLE_NOTE = (
    "Trailing earnings/revenue multiples are not computed. fundamentals.revenue resolves to OK for "
    "353 of 2,010 D2.1 candidates (17.6%), and among the 268 where revenue and operating income "
    "both resolve, 8 (3.0%) report operating income above revenue - so the level fields are not "
    "reliably the same period and scope even at status OK. A multiple built on them would be "
    "silently wrong for a material minority. Multiples are D5 Valuation's problem, with a "
    "fundamentals layer D4 does not have."
)
HISTORICAL_MULTIPLE_ABSENT_NOTE = (
    "A historical multiple percentile needs a PIT trailing-fundamentals series per session. The "
    "daily panel here carries closes only, and evidence_bundle.fundamental_changes carries "
    "year-over-year growth rates rather than levels, so no such series is code-derivable. D5."
)


def _compile(terms: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join(terms), re.IGNORECASE)


_GUIDANCE_RE = _compile(GUIDANCE_TERMS)
_MANAGEMENT_RE = _compile(MANAGEMENT_SIGNAL_TERMS)


def _excerpt_around(text: str, start: int, end: int) -> str:
    left = max(0, start - EXCERPT_CONTEXT_CHARS)
    right = min(len(text), end + EXCERPT_CONTEXT_CHARS)
    return text[left:right].strip()[:MAX_EXCERPT_CHARS]


def locate_excerpts(
    chunks: list[EvidenceChunk], pattern: re.Pattern[str], *, limit: int = MAX_EXCERPTS_PER_BLOCK,
) -> list[LocatedExcerpt]:
    """Deterministic: most recent `published_at` first, then the chunk's own order, then the
    match's own position. Never re-ranked by match count or any other content heuristic - a chunk
    that happens to repeat the word "guidance" eight times is not eight times more relevant, and
    ranking by it would make the selection depend on filing style."""
    ordered = sorted(
        chunks,
        key=lambda c: (-(c.published_at or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
                       c.chunk_index),
    )
    found: list[LocatedExcerpt] = []
    for chunk in ordered:
        if len(found) >= limit:
            break
        matches = list(pattern.finditer(chunk.text))
        if not matches:
            continue
        first = matches[0]
        found.append(LocatedExcerpt(
            source_id=chunk.source_id, evidence_id=chunk.evidence_id,
            source_type=chunk.source_type.value, published_at=chunk.published_at,
            text=_excerpt_around(chunk.text, first.start(), first.end()),
            matched_terms=sorted({m.group(0).lower() for m in matches}),
        ))
    return found


def _earnings_material_chunks(package: AIResearchInputV1) -> list[EvidenceChunk]:
    """Chunks belonging to this candidate's earnings release / call material.

    Not selectable by `EvidenceChunk.source_type`: an earnings release reaches D2.1 as an EX-99
    exhibit of an item-2.02 8-K, so its chunks carry `SEC_8K`, and filtering on an
    `EARNINGS_RELEASE` source type finds nothing. Verified against the real LUV package, whose 111
    chunks are SEC_10Q/SEC_8K/SEC_10K only while its materialization record does carry an
    EARNINGS_RELEASE document. The code-owned identifier is the materialization ROLE, with the D2
    item-2.02 classification as a fallback for a package whose materialization predates that role.
    """
    prefixes = {
        doc.source_id for doc in package.materialization.documents
        if doc.role in ("EARNINGS_RELEASE", "EARNINGS_CALL")
    }
    prefixes |= {
        ref.source_id for ref in package.evidence_bundle.company_releases
        if ref.item_category == "EARNINGS_RESULTS"
    }
    return [
        chunk for chunk in package.chunks
        if any(chunk.source_id == p or chunk.source_id.startswith(f"{p}:") for p in prefixes)
    ]


def _block(excerpts: list[LocatedExcerpt], absent_note: str) -> EvidenceBlock:
    if excerpts:
        return EvidenceBlock(status=EvidenceAvailability.AVAILABLE, excerpts=excerpts)
    return EvidenceBlock(status=EvidenceAvailability.NOT_FOUND_FOR_CANDIDATE, note=absent_note)


def _event_kind(source: SourceProvenance, package: AIResearchInputV1) -> str:
    for ref in package.evidence_bundle.company_releases:
        if ref.source_id == source.source_id:
            return ref.item_category
    for ref in package.evidence_bundle.investor_materials:
        if ref.source_id == source.source_id:
            return ref.item_category
    return source.source_type.value


def build_price_reactions(
    package: AIResearchInputV1, series: Mapping[date, float], benchmark: Mapping[date, float],
) -> list[EventPriceReaction]:
    """One record per PIT-eligible official source, most recent first.

    `series` must already be PIT-filtered (`price_engine.pit_eligible_series`); this function does
    not re-check, because a caller that skipped that step has a data-assembly bug the error there
    is meant to surface, and swallowing it here would move the failure somewhere quieter.
    """
    sessions = sorted(series)
    reactions: list[EventPriceReaction] = []
    for source in sorted(
        (s for s in package.source_manifest if s.pit_eligible and s.available_at is not None),
        key=lambda s: s.available_at, reverse=True,
    ):
        alignment = price_engine.align_event(
            sessions, source.available_at,
            timestamp_is_exact=source.date_precision == DatePrecision.DATETIME,
        )
        r1 = price_engine.event_window_return(series, alignment, 0)
        r3 = price_engine.event_window_return(series, alignment, 2)
        reactions.append(EventPriceReaction(
            source_id=source.source_id, event_kind=_event_kind(source, package),
            available_at=source.available_at,
            event_session=alignment.event_session, prior_session=alignment.prior_session,
            alignment_ambiguous=alignment.alignment_ambiguous,
            ambiguity_reason=alignment.ambiguity_reason,
            return_1d=r1.to_dict(), return_3d=r3.to_dict(),
            benchmark_adjusted_1d=price_engine.benchmark_adjusted(r1, series, benchmark).to_dict(),
            benchmark_adjusted_3d=price_engine.benchmark_adjusted(r3, series, benchmark).to_dict(),
            pre_event_return_5d=price_engine.pre_event_return(series, alignment, 5).to_dict(),
            pre_event_return_20d=price_engine.pre_event_return(series, alignment, 20).to_dict(),
        ))
    return reactions


def build_valuation_context_stub(
    package: AIResearchInputV1, last_close: float | None,
) -> dict[str, Any]:
    """Raw, exactly-computable context only (D4 brief §12). No judgement, and no multiple - see
    this module's docstring for the measured reason multiples are excluded rather than attempted.

    Every figure carries its own as-of date, because a market capitalization computed today from a
    share count reported at a quarter end is a mixed-date quantity and a reader who cannot see both
    dates cannot tell how stale it is.
    """
    balance = package.evidence_bundle.balance_sheet

    def _ok(field: str) -> dict[str, Any] | None:
        item = balance.get(field)
        if isinstance(item, dict) and item.get("status") == "OK" and item.get("value") is not None:
            return item
        return None

    cash, debt = _ok("cash"), _ok("total_debt")
    #: `shares_outstanding` is NOT in `balance_sheet` despite `data_quality` scoring it as a
    #: canonical field - D1 files it in `fundamental_changes` as a trend block whose `current_value`
    #: is the latest cover-page share count. Read from where it actually is, verified against the
    #: real D2.1 packages rather than assumed from the coverage table's field list.
    shares_trend = package.evidence_bundle.fundamental_changes.get("shares_outstanding")
    shares_value: float | None = None
    shares_as_of: str | None = None
    if isinstance(shares_trend, dict) and shares_trend.get("current_value") is not None:
        shares_value = float(shares_trend["current_value"])
        points = shares_trend.get("points") or []
        shares_as_of = points[-1].get("end") if points else None
    market_cap = (
        None if shares_value is None or last_close is None else shares_value * last_close
    )
    net_debt = (
        None if cash is None or debt is None
        else float(debt["value"]) - float(cash["value"])
    )
    return {
        "status": "PARTIAL" if market_cap is not None else "NOT_COMPUTABLE",
        "market_cap": {
            "value": market_cap,
            "shares_outstanding": shares_value,
            "shares_as_of": shares_as_of,
            "close_used": last_close,
            "note": None if market_cap is not None else
                    "fundamental_changes.shares_outstanding carries no current_value for this "
                    "candidate, or no PIT close is available",
        },
        "net_debt": {
            "value": net_debt,
            "total_debt": None if debt is None else float(debt["value"]),
            "total_debt_as_of": None if debt is None else debt.get("end"),
            "cash": None if cash is None else float(cash["value"]),
            "cash_as_of": None if cash is None else cash.get("end"),
        },
        "enterprise_value": {
            "value": None if market_cap is None or net_debt is None else market_cap + net_debt,
            "definition": "market_cap + total_debt - cash, from code-owned instant-dated facts only",
        },
        "trailing_multiples": {"status": "NOT_COMPUTABLE", "note": MULTIPLES_NOT_COMPUTABLE_NOTE},
        "historical_multiple_percentile": {
            "status": "SOURCE_NOT_AVAILABLE", "note": HISTORICAL_MULTIPLE_ABSENT_NOTE,
        },
    }


def build_expectation_evidence_bundle(
    package: AIResearchInputV1,
    *,
    bundle_id: str,
    series: Mapping[date, float],
    benchmark: Mapping[date, float],
    generated_at: datetime,
) -> ExpectationEvidenceBundleV1:
    """The whole code-owned D4 input for one candidate.

    `series` and `benchmark` are the candidate's and the benchmark's daily closes. Both are passed
    through `price_engine.pit_eligible_series` here, so a caller cannot reach the arithmetic with a
    post-decision bar even by mistake.
    """
    bundle = package.evidence_bundle
    decision_time = bundle.data_cutoff
    safe = price_engine.pit_eligible_series(series, decision_time)
    safe_benchmark = price_engine.pit_eligible_series(benchmark, decision_time)

    context = price_engine.price_level_context(safe, safe_benchmark)
    unknown: list[str] = []
    if not safe:
        unknown.append("price_context: no PIT-eligible price sessions for this candidate")
    if context.realized_volatility_60s is None:
        unknown.append("price_context.realized_volatility_60s")

    guidance = _block(
        locate_excerpts(package.chunks, _GUIDANCE_RE),
        "No guidance-bearing language was located in this candidate's evidence chunks by the frozen "
        "term list. The sources were read; the language is not there.",
    )
    management = _block(
        locate_excerpts(package.chunks, _MANAGEMENT_RE),
        "No management target/milestone language was located in this candidate's evidence chunks.",
    )
    earnings_chunks = _earnings_material_chunks(package)
    earnings = _block(
        locate_excerpts(earnings_chunks, _GUIDANCE_RE) if earnings_chunks else [],
        "No earnings release or call material with guidance language is present for this candidate.",
    )
    if guidance.status != EvidenceAvailability.AVAILABLE:
        unknown.append("guidance")
    if earnings.status != EvidenceAvailability.AVAILABLE:
        unknown.append("earnings_history")
    unknown.extend(["consensus", "estimate_revisions", "valuation_context_stub.trailing_multiples"])

    return ExpectationEvidenceBundleV1(
        bundle_id=bundle_id,
        company_id=bundle.identity.get("cik") or "UNKNOWN",
        ticker=bundle.ticker,
        decision_time=decision_time,
        data_cutoff=decision_time,
        generated_at=generated_at,
        research_input_package_id=f"{package.run_id}:{bundle.ticker}",
        guidance=guidance,
        earnings_history=earnings,
        management_expectation_signals=management,
        consensus=EvidenceBlock(status=EvidenceAvailability.SOURCE_NOT_AVAILABLE,
                                note=CONSENSUS_ABSENT_NOTE),
        estimate_revisions=EvidenceBlock(status=EvidenceAvailability.SOURCE_NOT_AVAILABLE,
                                         note=ESTIMATE_REVISIONS_ABSENT_NOTE),
        price_reaction=build_price_reactions(package, safe, safe_benchmark),
        pre_event_price_context=context.to_dict(),
        valuation_context_stub=build_valuation_context_stub(package, context.last_close),
        source_manifest=list(package.source_manifest),
        unknown_fields=unknown,
    )
