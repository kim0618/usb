"""H-V2 D1 E1 - Eligibility Filter.

E1 is not an alpha score. It removes securities where AI research would be wasted (data cannot
support it) or where a catastrophic, security-specific risk dominates any thesis before research
starts (`H_V2_D0_ARCHITECTURE_RESEARCH_CONTRACT_V1.md` §E1). Every threshold here is justified by
data reliability, tradability, or catastrophic-risk avoidance - never by which cutoff would have
produced a better historical return. No forward return is read anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.backtest.strategy_h_v2.universe import SecurityTypeStatus, UniverseRow

SCHEMA_VERSION = "h_v2_eligibility_v1"

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
evidence (§D1 §29 of the D1 brief) is meaningful at all; below this the daily store simply does not
have enough history for this security yet."""

MIN_RESOLVED_CANONICAL_FIELDS = 4
"""Out of H0's 12 canonical fundamental fields (`strategy_h0.facts.FIELD_SPECS`), fewer than 4
resolved facts means E2 change detection has too little raw material to say anything about change,
not that the company is unhealthy."""

MATERIAL_DILUTION_RATIO = 0.10
"""A >=10% period-over-period rise in shares outstanding with no matching split event is treated as
a risk flag for E1 (see `change_detection.dilution_evidence`); this reuses the same evidence the
E2 layer records, it does not introduce a second, independent dilution rule."""


class EligibilityStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    UNKNOWN = "UNKNOWN"


class EligibilityReason(StrEnum):
    NOT_COMMON_STOCK = "NOT_COMMON_STOCK"
    UNKNOWN_SECURITY_TYPE = "UNKNOWN_SECURITY_TYPE"
    UNSUPPORTED_EXCHANGE = "UNSUPPORTED_EXCHANGE"
    MISSING_CIK = "MISSING_CIK"
    MISSING_SECURITY_ID = "MISSING_SECURITY_ID"
    INSUFFICIENT_DAILY_HISTORY = "INSUFFICIENT_DAILY_HISTORY"
    EXTREME_LOW_PRICE = "EXTREME_LOW_PRICE"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    INSUFFICIENT_FUNDAMENTALS = "INSUFFICIENT_FUNDAMENTALS"
    DISTRESS_FLAG = "DISTRESS_FLAG"
    EXTREME_DILUTION_RISK = "EXTREME_DILUTION_RISK"
    NO_PRICE_DATA = "NO_PRICE_DATA"


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
    negative_equity: bool | None
    """True only when `equity` resolved OK and is negative. None means equity did not resolve, and
    must never be treated as False (`equity unknown` is not `equity is fine`)."""

    material_dilution: bool | None
    """True only when `change_detection.dilution_evidence` found an unexplained >=10% shares
    increase. None means dilution could not be evaluated (e.g. too few shares facts)."""


@dataclass(frozen=True)
class EligibilityResult:
    status: EligibilityStatus
    reasons: tuple[EligibilityReason, ...] = field(default_factory=tuple)


def evaluate_eligibility(
    row: UniverseRow,
    market: MarketSnapshot | None,
    fundamentals: FundamentalsCoverage | None,
) -> EligibilityResult:
    """Three tiers, not two:

    - `hard` reasons are known facts that make a security ineligible outright.
    - `blocking_unknown` reasons mean a *primary* input (security type, identity, price, or
      fundamentals coverage itself) could not be evaluated at all; without it, research priority
      and even a basic profile cannot be built, so the candidate stays UNKNOWN rather than ELIGIBLE.
    - `soft_unknown` reasons are *secondary* risk flags (distress, dilution) that could not be
      evaluated. An unresolved risk flag is recorded for the AI to see later, but it does not by
      itself block an otherwise well-covered candidate from reaching ELIGIBLE - that would let a
      single missing risk field silently veto research the way `H_V2_D0` §Y warns against treating
      UNKNOWN as if it were a known-bad fact.
    """
    hard: list[EligibilityReason] = []
    blocking_unknown: list[EligibilityReason] = []
    soft_unknown: list[EligibilityReason] = []

    if row.security_type_status == SecurityTypeStatus.NOT_COMMON_STOCK:
        hard.append(EligibilityReason.NOT_COMMON_STOCK)
    elif row.security_type_status == SecurityTypeStatus.UNKNOWN:
        blocking_unknown.append(EligibilityReason.UNKNOWN_SECURITY_TYPE)

    if not row.exchange_supported:
        hard.append(EligibilityReason.UNSUPPORTED_EXCHANGE)

    if row.cik is None:
        hard.append(EligibilityReason.MISSING_CIK)
    if row.security_id is None:
        blocking_unknown.append(EligibilityReason.MISSING_SECURITY_ID)

    if market is None or market.latest_close is None:
        blocking_unknown.append(EligibilityReason.NO_PRICE_DATA)
    else:
        if market.trailing_sessions < MIN_TRAILING_SESSIONS:
            hard.append(EligibilityReason.INSUFFICIENT_DAILY_HISTORY)
        if market.latest_close < MIN_CLOSE_PRICE:
            hard.append(EligibilityReason.EXTREME_LOW_PRICE)
        if market.trailing_avg_dollar_volume is None:
            blocking_unknown.append(EligibilityReason.LOW_LIQUIDITY)
        elif market.trailing_avg_dollar_volume < MIN_TRAILING_DOLLAR_VOLUME:
            hard.append(EligibilityReason.LOW_LIQUIDITY)

    if fundamentals is None:
        blocking_unknown.append(EligibilityReason.INSUFFICIENT_FUNDAMENTALS)
    else:
        if fundamentals.resolved_field_count < MIN_RESOLVED_CANONICAL_FIELDS:
            hard.append(EligibilityReason.INSUFFICIENT_FUNDAMENTALS)
        if fundamentals.negative_equity is True:
            hard.append(EligibilityReason.DISTRESS_FLAG)
        elif fundamentals.negative_equity is None:
            soft_unknown.append(EligibilityReason.DISTRESS_FLAG)
        if fundamentals.material_dilution is True:
            hard.append(EligibilityReason.EXTREME_DILUTION_RISK)
        elif fundamentals.material_dilution is None:
            soft_unknown.append(EligibilityReason.EXTREME_DILUTION_RISK)

    if hard:
        return EligibilityResult(EligibilityStatus.INELIGIBLE, tuple(hard) + tuple(soft_unknown))
    if blocking_unknown:
        return EligibilityResult(EligibilityStatus.UNKNOWN, tuple(blocking_unknown) + tuple(soft_unknown))
    return EligibilityResult(EligibilityStatus.ELIGIBLE, tuple(soft_unknown))
