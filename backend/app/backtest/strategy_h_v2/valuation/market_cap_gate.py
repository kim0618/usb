"""H-V2-D5-P0 §13: the valuation-facing market-cap entry point, wired fail-closed on share class.

D5-D0 recorded the defect this module repairs (`d5_d0_contract.MULTI_CLASS_IS_UNDETECTED`):
`h0_5.resolve_pit_shares` takes `multiple_share_classes` and returns UNKNOWN when it is True, but
its only True caller is a test. Every production caller - the frozen H-PV1/PV2/PV2C/PV3 dev runners
- leaves it at its default of False, which is not "single class" but "nobody asked". A boolean that
defaults to the permissive answer is not a fail-closed gate.

This module adds no detector and no inference. It makes the question unskippable instead: the
valuation path must state a `ShareClassState`, and the two states that are not a positive
determination of single-class both yield UNKNOWN. Since this repository has no detector, the
honest state for every issuer today is UNRESOLVED, so this gate returns UNKNOWN for all of them.
That is the intended result. It is a refusal, not coverage.

`h0_5` itself is frozen and unchanged, so the historical PV runs are bit-identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Iterable, Sequence

from app.backtest.strategy_h0.facts import CanonicalFact
from app.backtest.strategy_h0.h0_5 import (
    SharesResolution,
    historical_market_cap,
    resolve_pit_shares,
)


class ShareClassState(StrEnum):
    SINGLE_CLASS = "SINGLE_CLASS"
    """Positively determined to have one class of common stock. No detector supplies this today."""
    MULTIPLE_CLASSES = "MULTIPLE_CLASSES"
    """Positively determined to have more than one, with no allocation between them."""
    UNRESOLVED = "UNRESOLVED"
    """Not determined. The only honest state in this repository, and it blocks like MULTIPLE."""


class MarketCapStatus(StrEnum):
    OK = "OK"
    UNKNOWN_SHARE_CLASS_UNRESOLVED = "UNKNOWN_SHARE_CLASS_UNRESOLVED"
    UNKNOWN_MULTIPLE_SHARE_CLASSES = "UNKNOWN_MULTIPLE_SHARE_CLASSES"
    UNKNOWN_SHARES = "UNKNOWN_SHARES"
    UNKNOWN_PRICE = "UNKNOWN_PRICE"


@dataclass(frozen=True)
class MarketCapResolution:
    status: MarketCapStatus
    value: float | None
    reason: str
    shares: SharesResolution | None = None

    @property
    def valuation_ready(self) -> bool:
        """A multiple may be formed only from an OK market cap. Anything else is NOT_READY."""
        return self.status is MarketCapStatus.OK and self.value is not None


def valuation_market_cap(
    facts: Iterable[CanonicalFact],
    decision_date: date,
    regular_opens: Sequence[datetime],
    unadjusted_close: float | None,
    share_class_state: ShareClassState,
) -> MarketCapResolution:
    """Market cap for the valuation layer. `share_class_state` is required and has no default.

    Ordering is deliberate: the share-class gate runs before anything else, so an unresolved class
    cannot be masked by a shares or price failure that happens to be reported first, and so the
    reason a valuation is NOT_READY names the gate that actually stopped it.
    """
    if share_class_state is ShareClassState.MULTIPLE_CLASSES:
        return MarketCapResolution(
            MarketCapStatus.UNKNOWN_MULTIPLE_SHARE_CLASSES, None,
            "multiple share classes with no allocation between them")
    if share_class_state is not ShareClassState.SINGLE_CLASS:
        return MarketCapResolution(
            MarketCapStatus.UNKNOWN_SHARE_CLASS_UNRESOLVED, None,
            "share class not determined; this repository has no multi-class detector")

    shares = resolve_pit_shares(facts, decision_date, regular_opens,
                                multiple_share_classes=False)
    if shares.fact is None:
        return MarketCapResolution(MarketCapStatus.UNKNOWN_SHARES, None, shares.reason, shares)
    if unadjusted_close is None:
        return MarketCapResolution(MarketCapStatus.UNKNOWN_PRICE, None,
                                   "no unadjusted close at the decision date", shares)
    return MarketCapResolution(MarketCapStatus.OK,
                               historical_market_cap(unadjusted_close, shares),
                               "unadjusted close x PIT raw shares, single class", shares)


MULTI_CLASS_WIRING = (
    "D5-P0 wires the existing fail-closed primitive into the valuation-facing path by making the "
    "share-class determination a required argument with no default. It does NOT add a detector, so "
    "`ShareClassState.SINGLE_CLASS` is unobtainable today and every issuer resolves UNKNOWN. "
    "Supplying the detector is D5-P1's, not P0's."
)
