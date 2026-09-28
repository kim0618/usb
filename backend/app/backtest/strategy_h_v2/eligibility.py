"""H-V2 D1 / D1.1 E1 - Eligibility Filter.

E1 is not an alpha score. It removes securities where AI research would be wasted (data cannot
support it) or where a catastrophic, security-specific risk dominates any thesis before research
starts (`H_V2_D0_ARCHITECTURE_RESEARCH_CONTRACT_V1.md` §E1). Every threshold here is justified by
data reliability, tradability, or catastrophic-risk avoidance - never by which cutoff would have
produced a better historical return. No forward return is read anywhere in this module.

D1.1 fix: D1's original two-tier model conflated "we have not fetched local SEC data for this CIK
yet" with "this company is ineligible." A security with zero local companyfacts failed
`INSUFFICIENT_FUNDAMENTALS` exactly like a security that is a confirmed penny stock, even though
the two facts are not the same kind of fact. `CandidateStatus` now has four values, not three:
`MISSING LOCAL DATA != COMPANY INELIGIBLE`
(`docs/backtest/strategy_h_v2/H_V2_D1_1_UNIVERSE_DATA_COVERAGE_V1.md` §B).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.backtest.strategy_h_v2.universe import SecurityTypeStatus, UniverseRow

SCHEMA_VERSION = "h_v2_eligibility_v2"

# Thresholds and why each one exists. None of these were chosen or adjusted by looking at any
# forward return; changing any of them requires a documented reason in
# `docs/backtest/strategy_h_v2/H_V2_D1_UNIVERSE_CHANGE_ENGINE_V1.md`, not a backtest result.
MIN_CLOSE_PRICE = 1.00
"""Below $1 a security is commonly subject to minimum-bid-price delisting rules and its quoted
price is disproportionately affected by tick-size noise; this is a tradability/data-quality floor,
not a value judgment about the company."""

MIN_TRAILING_DOLLAR_VOLUME = 1_000_000.0
"""Trailing-21-session average dollar volume floor. Below this, execution is impractical and the
daily close itself becomes an unreliable, thinly-traded print rather than a researchable price."""

MIN_TRAILING_SESSIONS = 60
"""At least ~3 months of local daily bars are required before price_context/relative-strength
evidence (D1 brief §29) is meaningful at all. A security below this line is not proven illiquid -
our local daily store may simply not cover it yet (§D1.1 §C) - so this now routes to
`DATA_NOT_READY`, not `INELIGIBLE`."""

MIN_RESOLVED_CANONICAL_FIELDS = 4
"""Out of H0's 12 canonical fundamental fields (`strategy_h0.facts.FIELD_SPECS`), fewer than 4
resolved facts means E2 change detection has too little raw material to say anything about change.
Below this floor the candidate is `DATA_NOT_READY`, never `INELIGIBLE` - not having fetched or
resolved enough fields is a fact about our local cache, not about the company (§D1.1 §C)."""

MIN_REPORTING_HISTORY_DAYS = 730
"""If companyfacts were fetched but every known fact is younger than ~2 years, the company most
likely has not been reporting long enough yet (a recent IPO) rather than our cache being stale -
`INSUFFICIENT_REPORTING_HISTORY`, not `FUNDAMENTALS_NOT_FETCHED` (D1.1 brief §14). This is a
data-availability distinction, not a company-quality judgment."""

MATERIAL_DILUTION_RATIO = 0.10
"""A >=10% period-over-period rise in shares outstanding with no matching split event is treated as
a risk flag for E1 (see `change_detection.dilution_ratio`); this reuses the same evidence the E2
layer records, it does not introduce a second, independent dilution rule."""


class CandidateStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    DATA_NOT_READY = "DATA_NOT_READY"
    UNKNOWN = "UNKNOWN"


# Backward-compatible alias; D1 code and tests referred to this as `EligibilityStatus`.
EligibilityStatus = CandidateStatus


class EligibilityReason(StrEnum):
    # Hard facts about the security/market -> INELIGIBLE.
    NOT_COMMON_STOCK = "NOT_COMMON_STOCK"
    UNSUPPORTED_EXCHANGE = "UNSUPPORTED_EXCHANGE"
    MISSING_CIK = "MISSING_CIK"
    EXTREME_LOW_PRICE = "EXTREME_LOW_PRICE"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    DISTRESS_FLAG = "DISTRESS_FLAG"
    EXTREME_DILUTION_RISK = "EXTREME_DILUTION_RISK"

    # Local cache/history gaps, not a company fact -> DATA_NOT_READY.
    REFERENCE_DATA_NOT_READY = "REFERENCE_DATA_NOT_READY"
    REQUIRED_MARKET_DATA_NOT_AVAILABLE = "REQUIRED_MARKET_DATA_NOT_AVAILABLE"
    FUNDAMENTALS_NOT_FETCHED = "FUNDAMENTALS_NOT_FETCHED"
    INSUFFICIENT_REPORTING_HISTORY = "INSUFFICIENT_REPORTING_HISTORY"
    INSUFFICIENT_COMPARABLE_PERIODS = "INSUFFICIENT_COMPARABLE_PERIODS"

    # Data exists but its meaning/identity cannot be stably determined -> UNKNOWN. Defined for
    # schema completeness; none is currently wired into `evaluate_eligibility` below. An earlier
    # version of this module tried `FUNDAMENTAL_FACT_AMBIGUOUS` (>=3 of 12 fields resolving to
    # `FactStatus.AMBIGUOUS`), but a full-universe run showed it fired almost entirely on ordinary
    # 10-Q reporting - the same period-end legitimately carries both a discrete-quarter and a
    # year-to-date duration, which `facts.py::resolve_fact`'s snapshot-level narrowing does not
    # distinguish. That is not a data-identity conflict, so the check was removed rather than kept
    # to look complete (`H_V2_D1_1_UNIVERSE_DATA_COVERAGE_V1.md` §B).
    SECURITY_TYPE_AMBIGUOUS = "SECURITY_TYPE_AMBIGUOUS"
    CIK_CONFLICT = "CIK_CONFLICT"
    CORPORATE_ACTION_UNRESOLVED = "CORPORATE_ACTION_UNRESOLVED"
    FUNDAMENTAL_FACT_AMBIGUOUS = "FUNDAMENTAL_FACT_AMBIGUOUS"

    # Secondary risk checks that could not be evaluated -> stays ELIGIBLE, recorded as a caveat.
    DISTRESS_UNRESOLVED = "DISTRESS_UNRESOLVED"
    DILUTION_UNRESOLVED = "DILUTION_UNRESOLVED"


@dataclass(frozen=True)
class MarketSnapshot:
    """Locally available market facts for one ticker as of the data cutoff. `None` means the
    local store does not have this, never that the value is zero."""

    latest_close: float | None
    trailing_sessions: int
    trailing_avg_dollar_volume: float | None


@dataclass(frozen=True)
class FundamentalsCoverage:
    """Summary of H0 canonical-field resolution for one CIK as of the data cutoff."""

    resolved_field_count: int
    total_field_count: int
    ambiguous_field_count: int
    """Fields whose SEC facts resolved to `FactStatus.AMBIGUOUS` (conflicting values sharing the
    same winning provenance) rather than `OK` or `MISSING`. This is real, observed SEC data
    behaviour, not a hypothetical."""

    facts_fetched: bool
    """True only when a local companyfacts document exists for this CIK (`pipeline.py` sets this
    from whether `find_facts_root(cik)` found a store, not from whether any field happened to
    resolve). `False` means the acquisition queue has not (yet) retrieved this CIK at all."""

    earliest_fact_age_days: int | None
    """Age of the oldest known canonical fact relative to the data cutoff, in days. `None` when no
    facts were fetched at all. Used only to distinguish a recent IPO (`INSUFFICIENT_REPORTING_HISTORY`)
    from an older company whose local companyfacts still resolve too few fields
    (`INSUFFICIENT_COMPARABLE_PERIODS`) - never to judge whether the company is a good investment."""

    negative_equity: bool | None
    """True only when `equity` resolved OK and is negative. None means equity did not resolve, and
    must never be treated as False (`equity unknown` is not `equity is fine`)."""

    material_dilution: bool | None
    """True only when `change_detection.dilution_ratio` found an unexplained >=10% shares
    increase. None means dilution could not be evaluated (e.g. too few shares facts)."""


@dataclass(frozen=True)
class EligibilityResult:
    status: CandidateStatus
    reasons: tuple[EligibilityReason, ...] = field(default_factory=tuple)


def _fundamentals_reason(fundamentals: FundamentalsCoverage) -> EligibilityReason:
    if not fundamentals.facts_fetched:
        return EligibilityReason.FUNDAMENTALS_NOT_FETCHED
    if (fundamentals.earliest_fact_age_days is not None
            and fundamentals.earliest_fact_age_days < MIN_REPORTING_HISTORY_DAYS):
        return EligibilityReason.INSUFFICIENT_REPORTING_HISTORY
    return EligibilityReason.INSUFFICIENT_COMPARABLE_PERIODS


def evaluate_eligibility(
    row: UniverseRow,
    market: MarketSnapshot | None,
    fundamentals: FundamentalsCoverage | None,
) -> EligibilityResult:
    """Four tiers, in priority order when more than one applies:

    1. `hard` - a known fact makes the security ineligible outright (`INELIGIBLE`).
    2. `unknown_ambiguous` - the data that exists cannot be stably interpreted (`UNKNOWN`).
       Outranks a plain data gap: an ambiguous identity is a worse problem than a missing one. No
       current check populates this tier (see the `FUNDAMENTAL_FACT_AMBIGUOUS` comment above); the
       machinery is kept for `SECURITY_TYPE_AMBIGUOUS`/`CIK_CONFLICT`/`CORPORATE_ACTION_UNRESOLVED`
       once a reliable detector for one of them exists.
    3. `data_not_ready` - a *primary* input (reference identity, price history, or fundamentals
       coverage itself) simply has not been fetched or does not yet cover enough history
       (`DATA_NOT_READY`). Acquiring more local data, not a company judgment, is what would resolve
       this.
    4. `soft` - a *secondary* risk flag (distress, dilution) could not be evaluated. Recorded as a
       caveat, but never by itself blocks an otherwise well-covered candidate from `ELIGIBLE` -
       otherwise a single missing risk field would silently veto research
       (`H_V2_D0` §Y).
    """
    hard: list[EligibilityReason] = []
    unknown_ambiguous: list[EligibilityReason] = []
    data_not_ready: list[EligibilityReason] = []
    soft: list[EligibilityReason] = []

    if row.security_type_status == SecurityTypeStatus.NOT_COMMON_STOCK:
        hard.append(EligibilityReason.NOT_COMMON_STOCK)
    elif row.security_type_status == SecurityTypeStatus.UNKNOWN:
        data_not_ready.append(EligibilityReason.REFERENCE_DATA_NOT_READY)

    if not row.exchange_supported:
        hard.append(EligibilityReason.UNSUPPORTED_EXCHANGE)

    if row.cik is None:
        hard.append(EligibilityReason.MISSING_CIK)
    if row.security_id is None:
        data_not_ready.append(EligibilityReason.REFERENCE_DATA_NOT_READY)

    if market is None or market.latest_close is None:
        data_not_ready.append(EligibilityReason.REQUIRED_MARKET_DATA_NOT_AVAILABLE)
    else:
        if market.trailing_sessions < MIN_TRAILING_SESSIONS:
            data_not_ready.append(EligibilityReason.REQUIRED_MARKET_DATA_NOT_AVAILABLE)
        if market.latest_close < MIN_CLOSE_PRICE:
            hard.append(EligibilityReason.EXTREME_LOW_PRICE)
        if market.trailing_avg_dollar_volume is None:
            data_not_ready.append(EligibilityReason.REQUIRED_MARKET_DATA_NOT_AVAILABLE)
        elif market.trailing_avg_dollar_volume < MIN_TRAILING_DOLLAR_VOLUME:
            hard.append(EligibilityReason.LOW_LIQUIDITY)

    if fundamentals is None:
        data_not_ready.append(EligibilityReason.FUNDAMENTALS_NOT_FETCHED)
    else:
        # `fundamentals.ambiguous_field_count` is recorded as a diagnostic (see `data_quality` in
        # the evidence stub) but does not gate status - see the `FUNDAMENTAL_FACT_AMBIGUOUS`
        # comment above for why a full-universe run showed that check to be unreliable.
        if fundamentals.resolved_field_count < MIN_RESOLVED_CANONICAL_FIELDS:
            data_not_ready.append(_fundamentals_reason(fundamentals))
        if fundamentals.negative_equity is True:
            hard.append(EligibilityReason.DISTRESS_FLAG)
        elif fundamentals.negative_equity is None:
            soft.append(EligibilityReason.DISTRESS_UNRESOLVED)
        if fundamentals.material_dilution is True:
            hard.append(EligibilityReason.EXTREME_DILUTION_RISK)
        elif fundamentals.material_dilution is None:
            soft.append(EligibilityReason.DILUTION_UNRESOLVED)

    if hard:
        return EligibilityResult(CandidateStatus.INELIGIBLE, tuple(hard) + tuple(soft))
    if unknown_ambiguous:
        return EligibilityResult(
            CandidateStatus.UNKNOWN, tuple(unknown_ambiguous) + tuple(data_not_ready) + tuple(soft),
        )
    if data_not_ready:
        return EligibilityResult(CandidateStatus.DATA_NOT_READY, tuple(data_not_ready) + tuple(soft))
    return EligibilityResult(CandidateStatus.ELIGIBLE, tuple(soft))
