"""H-V2-D5-P1.1: a practical share-class determination, so market cap and EV can open.

D5-P0 made the share-class determination a required argument of `valuation_market_cap` with no
default, and added no detector. D5-P1 measured whether the stored data could supply one and recorded
`capital_structure.SHARE_CLASS_DETERMINATION_IS_STILL_ABSENT`: SEC companyfacts carries no class
signal at all, while the local Polygon reference-ticker store carries a validated one - one CIK with
more than one active common-stock security - whose only snapshot P1 knew of was 381 days old.

What P1.1 adds is not a new source. It is the discovery that the store P1 found has a second, denser
series: `data/runtime/strategy_c/raw/tickers` holds a quarterly sequence of full CS snapshots, the
newest at or before the D5 decision date being 77 days old rather than 381. The topology question is
therefore answerable from data already on disk, and this module answers it to a stated confidence
instead of refusing everything.

The design rule of this step is `PRACTICALLY_RELIABLE_NOT_PERFECTLY_PROVEN` below: a determination
that cannot be proved is allowed to be *stated with its confidence and its blind spot*, and is not
allowed to be silently wrong. Every state that is not a positive single-class reading still denies
market cap, and the one blind spot that cannot be closed from listed-security reference data - an
unlisted second class - is attached to the provenance of every reading rather than used as a reason
to refuse all of them.

Nothing here changes `h0_5`, `market_cap_gate` or `capital_structure`. The PIT-shares freshness rule
(`h0_5.MAX_SHARES_STALENESS_DAYS`) and the market-cap arithmetic (`h0_5.historical_market_cap`) are
reused unchanged, and enterprise value is reassembled from P1's own primitive.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from app.backtest.strategy_h0.facts import CanonicalFact
from app.backtest.strategy_h0.h0_5 import (
    MAX_SHARES_STALENESS_DAYS,
    SharesResolution,
    resolve_pit_shares,
)
from app.backtest.strategy_h_v2.valuation.capital_structure import (
    MAX_INSTANT_STALENESS_DAYS,
    EnterpriseValueResolution,
    NetDebtResolution,
    enterprise_value,
)
from app.backtest.strategy_h_v2.valuation.market_cap_gate import (
    MarketCapResolution,
    MarketCapStatus,
    ShareClassState,
    valuation_market_cap,
)

SHARE_CLASS_CONTRACT_VERSION = "h_v2_d5_p1_1_practical_share_class_v1"

PRACTICALLY_RELIABLE_NOT_PERFECTLY_PROVEN = (
    "P0 and P1 refused every market cap because the share class could not be proved. That is the "
    "right default for a gate and the wrong default for a programme: held to it, no multiple is ever "
    "formed, because no listed-security reference can prove the absence of an unlisted share class. "
    "P1.1 replaces proof with a stated confidence. A reading is allowed to be used when the reference "
    "positively shows one common share class and shows nothing that competes with it; it carries the "
    "confidence of that reading and the named limitation of what the reference cannot see; and every "
    "other reading - more than one class, an unidentifiable sibling security, a missing or stale "
    "snapshot, a missing CIK - still denies market cap. What is forbidden is not an imperfect number, "
    "it is a wrong number presented as a fact."
)


# -------------------------------------------------------------------------------------------------
# Reference snapshot: freshness is a separate question from financial freshness
# -------------------------------------------------------------------------------------------------

#: Directories holding `CS_<as_of>.json.gz` reference-ticker snapshots, searched in order. Two shapes
#: exist on disk and both are read: a flat `{"as_of", "results"}` document (the `strategy_c` series)
#: and a `{"pages": [<raw API page>, ...]}` document (the `research_universe` series). The `as_of`
#: date is taken from the filename in both cases, which is the one field both shapes agree on.
REFERENCE_SNAPSHOT_DIRS: tuple[Path, ...] = (
    Path("data/runtime/strategy_c/raw/tickers"),
    Path("data/runtime/research_universe_v2/reference/tickers"),
    Path("data/runtime/research_universe_u1/reference/tickers"),
)

#: The maximum age of a reference snapshot used for a share-class determination, in days.
#:
#: This is deliberately NOT `MAX_SHARES_STALENESS_DAYS` / `MAX_INSTANT_STALENESS_DAYS` (135), and the
#: two must not be conflated. That bound governs *financial* facts - cash, debt, shares, EPS - which
#: are restated every quarter and whose value at T is a different number from their value a quarter
#: earlier. Share-class topology is not that kind of quantity: a company has the same number of common
#: share classes for years at a time, and a change to it is a corporate action, not a measurement.
#:
#: The bound is derived from the collector rather than from the quantity, because what can actually go
#: wrong is that the snapshot series stopped. The observed series in `strategy_c/raw/tickers` is
#: quarterly (2024-10-01, 2025-01-02, 2025-04-01, 2025-07-01, 2025-10-01, 2026-01-02, 2026-04-01,
#: 2026-07-01) with a maximum interval of 93 days. Two intervals tolerate exactly one missed
#: collection and no more; a third would mean the series has stopped and is being used anyway.
REFERENCE_TOPOLOGY_MAX_AGE_DAYS = 186

REFERENCE_FRESHNESS_IS_NOT_FINANCIAL_FRESHNESS = (
    f"`REFERENCE_TOPOLOGY_MAX_AGE_DAYS` is {REFERENCE_TOPOLOGY_MAX_AGE_DAYS} and "
    f"`MAX_INSTANT_STALENESS_DAYS` is {MAX_INSTANT_STALENESS_DAYS}; the first is not a relaxation of "
    "the second, it is a bound on a different kind of data. A balance-sheet instant 136 days old has "
    "been superseded by a filing that exists. A reference snapshot 136 days old has not been "
    "superseded by anything unless the issuer did a corporate action, and the bound's job is to catch "
    "a stopped collector rather than to track a restatement. The two bounds are applied to their own "
    "inputs in the same market cap: PIT shares are still held to 135 days inside `h0_5`, which this "
    "module does not touch."
)


@dataclass(frozen=True)
class SecurityRef:
    """One active common-stock row of a reference snapshot, reduced to what identity needs."""

    ticker: str
    cik: str
    composite_figi: str | None
    share_class_figi: str | None
    primary_exchange: str | None

    @property
    def class_identity(self) -> tuple[str, str]:
        """The share class this row belongs to, as an identity rather than a ticker string.

        A `share_class_figi` is the identity when the snapshot carries one: it survives a ticker
        rename, so two rows sharing it are one class listed twice and not two classes. When it is
        absent the row is still a security that exists, so it becomes its own identity keyed by
        ticker - and the `TICKER` tag travels with it, because a determination resting on a ticker
        string is weaker than one resting on a FIGI and the caller has to be able to see which it got.
        """
        figi = (self.share_class_figi or "").strip()
        return ("FIGI", figi) if figi else ("TICKER", self.ticker)

    def to_dict(self) -> dict:
        return {"ticker": self.ticker, "cik": self.cik, "composite_figi": self.composite_figi,
                "share_class_figi": self.share_class_figi,
                "primary_exchange": self.primary_exchange}


@dataclass(frozen=True)
class ReferenceSnapshot:
    """One `CS_<as_of>` snapshot, indexed by CIK over active US common-stock rows only."""

    as_of: date
    path: Path
    by_cik: Mapping[str, tuple[SecurityRef, ...]]
    rows: int

    def securities(self, cik: str) -> tuple[SecurityRef, ...]:
        return self.by_cik.get(cik, ())


def _snapshot_rows(document: dict) -> list[dict]:
    """The result rows of either stored shape."""
    if isinstance(document.get("results"), list):
        return [row for row in document["results"] if isinstance(row, dict)]
    out: list[dict] = []
    for page in document.get("pages") or ():
        if isinstance(page, dict):
            out.extend(row for row in (page.get("results") or ()) if isinstance(row, dict))
    return out


def _is_active_us_common(row: dict) -> bool:
    """Whether the row is an active US common-stock listing.

    `type == "CS"` is necessary and, in this store, not sufficient to mean common stock: the snapshot
    types FULTP, AGNCL and BHFAO - preferred shares and baby bonds - as CS alongside their issuer's
    common. That is why `resolve_share_class` counts share-class *identities* and fails closed when a
    sibling security cannot be identified, instead of trusting this field to separate the two.
    """
    return (row.get("type") == "CS" and row.get("market") == "stocks"
            and row.get("locale") == "us" and row.get("active") is True
            and not row.get("delisted_utc") and bool((row.get("cik") or "").strip()))


def load_reference_snapshot(
    on_or_before: date,
    dirs: Sequence[Path] = REFERENCE_SNAPSHOT_DIRS,
) -> ReferenceSnapshot | None:
    """The newest snapshot whose `as_of` is at or before `on_or_before`, or None.

    `on_or_before` is the decision date, so a snapshot published after it is never read: a share-class
    topology observed in the future is the same kind of leak as a future filing.
    """
    best: tuple[date, Path] | None = None
    for directory in dirs:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("CS_*.json.gz")):
            try:
                as_of = date.fromisoformat(path.name[3:13])
            except ValueError:
                continue
            if as_of > on_or_before:
                continue
            if best is None or as_of > best[0]:
                best = (as_of, path)
    if best is None:
        return None
    as_of, path = best
    document = json.loads(gzip.decompress(path.read_bytes()))
    by_cik: dict[str, list[SecurityRef]] = {}
    rows = _snapshot_rows(document)
    for row in rows:
        if not _is_active_us_common(row):
            continue
        cik = str(row["cik"]).strip()
        by_cik.setdefault(cik, []).append(SecurityRef(
            ticker=str(row["ticker"]),
            cik=cik,
            composite_figi=(row.get("composite_figi") or None),
            share_class_figi=(row.get("share_class_figi") or None),
            primary_exchange=(row.get("primary_exchange") or None),
        ))
    return ReferenceSnapshot(as_of, path,
                             {cik: tuple(sorted(refs, key=lambda r: r.ticker))
                              for cik, refs in by_cik.items()},
                             len(rows))


# -------------------------------------------------------------------------------------------------
# States, confidence and the policy that binds them to valuation
# -------------------------------------------------------------------------------------------------

class ShareClassTopology(StrEnum):
    SINGLE_CLASS_CONFIRMED = "SINGLE_CLASS_CONFIRMED"
    """One common share class, identified by a share-class FIGI, with no other active CS security."""
    SINGLE_CLASS_LIKELY = "SINGLE_CLASS_LIKELY"
    """One active CS security and no competing one, but its identity rests on the ticker, not a FIGI."""
    MULTI_CLASS_CONFIRMED = "MULTI_CLASS_CONFIRMED"
    """Two or more distinct share-class FIGIs under the CIK, every one of them identified."""
    UNRESOLVED = "UNRESOLVED"
    """Not determined. Includes the ambiguous middle, and blocks exactly like MULTI_CLASS_CONFIRMED."""


class ShareClassConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ShareClassReason(StrEnum):
    ONE_FIGI_IDENTIFIED_CLASS = "ONE_FIGI_IDENTIFIED_CLASS"
    ONE_TICKER_IDENTIFIED_CLASS = "ONE_TICKER_IDENTIFIED_CLASS"
    MULTIPLE_FIGI_IDENTIFIED_CLASSES = "MULTIPLE_FIGI_IDENTIFIED_CLASSES"
    UNIDENTIFIABLE_SIBLING_SECURITY = "UNIDENTIFIABLE_SIBLING_SECURITY"
    CIK_ABSENT_FROM_REFERENCE = "CIK_ABSENT_FROM_REFERENCE"
    NO_CIK = "NO_CIK"
    NO_REFERENCE_SNAPSHOT = "NO_REFERENCE_SNAPSHOT"
    REFERENCE_SNAPSHOT_STALE = "REFERENCE_SNAPSHOT_STALE"


#: The limitation that cannot be closed from this data and is therefore disclosed on every reading
#: that is used. A reference of listed securities sees listed classes. A company with one listed
#: common class and a second class held privately reads as single-class here, and the market cap
#: formed from the cover-page total share count would then be attributed to one class's price.
UNLISTED_CLASS_BLIND_SPOT = (
    "KNOWN LIMITATION: an unlisted or privately held secondary common class is not observable in a "
    "listed-security reference, so a single-class reading cannot exclude one. This is the permissive "
    "direction of error and it is accepted deliberately under P1.1's confidence model rather than "
    "used to deny every single-class issuer. It is recorded on the reading, not resolved by it."
)

#: Recorded on a `SINGLE_CLASS_LIKELY` reading, whose single class has no share-class FIGI.
TICKER_IDENTITY_LIMITATION = (
    "KNOWN LIMITATION: this issuer's one active CS security carries no share_class_figi in the "
    "snapshot, so its class identity is the ticker string. A ticker rename between the snapshot and "
    "the decision date would read as a different security, and a second class added without a FIGI "
    "would not be distinguishable from it. One security was seen and none competed with it, which is "
    "the MEDIUM reading."
)

#: Topologies from which a market cap may be formed. Frozen before the resolver was run on any issuer.
VALUATION_ALLOWED_TOPOLOGIES: frozenset[ShareClassTopology] = frozenset({
    ShareClassTopology.SINGLE_CLASS_CONFIRMED,
    ShareClassTopology.SINGLE_CLASS_LIKELY,
})

VALUATION_USE_POLICY = (
    "SINGLE_CLASS_CONFIRMED (HIGH) -> market cap allowed. "
    "SINGLE_CLASS_LIKELY (MEDIUM) -> market cap allowed, with its limitations attached. "
    "MULTI_CLASS_CONFIRMED -> market cap denied: company-wide cover-page shares times one class's "
    "price is not this company's market cap, and no class-level share allocation exists here. "
    "UNRESOLVED (LOW) -> market cap denied. "
    "The policy was frozen before the resolver was run on any issuer, and it is expressed once, as "
    "`VALUATION_ALLOWED_TOPOLOGIES`, so the gate and the audit cannot disagree about it."
)

NO_SILENT_AGGREGATION_FOR_MULTI_CLASS = (
    "For a confirmed multi-class issuer the one thing P1.1 must not do is multiply "
    "`dei:EntityCommonStockSharesOutstanding` by the price of whichever class the panel priced. The "
    "cover-page total counts every class; the price is one class's. Their product is not a market cap "
    "of anything. Since no class-level share allocation is available in this repository, the reading "
    "stays MARKET_CAP = UNKNOWN with reason MULTI_CLASS_UNRESOLVED_ALLOCATION, which is the same "
    "fail-closed behaviour `market_cap_gate` already had."
)


@dataclass(frozen=True)
class ShareClassResolution:
    topology: ShareClassTopology
    confidence: ShareClassConfidence
    reason: ShareClassReason
    cik: str | None
    snapshot_date: date | None
    snapshot_age_days: int | None
    securities: tuple[SecurityRef, ...] = ()
    class_identities: tuple[tuple[str, str], ...] = ()
    limitations: tuple[str, ...] = ()

    @property
    def valuation_allowed(self) -> bool:
        return self.topology in VALUATION_ALLOWED_TOPOLOGIES

    @property
    def gate_state(self) -> ShareClassState:
        """The determination translated into D5-P0's gate vocabulary, which is left unchanged.

        P0's three states are the gate's alphabet; P1.1's four are the determination's. The mapping is
        the whole of the policy: the two allowed topologies become SINGLE_CLASS, a confirmed
        multi-class becomes MULTIPLE_CLASSES so the gate names the right refusal, and everything else
        becomes UNRESOLVED.
        """
        if self.valuation_allowed:
            return ShareClassState.SINGLE_CLASS
        if self.topology is ShareClassTopology.MULTI_CLASS_CONFIRMED:
            return ShareClassState.MULTIPLE_CLASSES
        return ShareClassState.UNRESOLVED

    def to_dict(self) -> dict:
        return {
            "contract_version": SHARE_CLASS_CONTRACT_VERSION,
            "topology": self.topology.value,
            "confidence": self.confidence.value,
            "reason": self.reason.value,
            "cik": self.cik,
            "reference_snapshot_date": (None if self.snapshot_date is None
                                        else self.snapshot_date.isoformat()),
            "reference_snapshot_age_days": self.snapshot_age_days,
            "reference_topology_max_age_days": REFERENCE_TOPOLOGY_MAX_AGE_DAYS,
            "securities": [ref.to_dict() for ref in self.securities],
            "class_identities": [list(identity) for identity in self.class_identities],
            "valuation_allowed": self.valuation_allowed,
            "gate_state": self.gate_state.value,
            "limitations": list(self.limitations),
        }


def resolve_share_class(
    cik: str | None,
    decision_date: date,
    snapshot: ReferenceSnapshot | None,
) -> ShareClassResolution:
    """The practical determination: count share-class identities under one CIK, fail closed otherwise.

    The order of the refusals is the order in which they are worth knowing. A missing CIK, a missing
    snapshot and a stale snapshot are failures of the *inputs* and say nothing about the issuer, so
    they come first and are reported as themselves rather than as "no second class was seen".
    """
    if not (cik or "").strip():
        return ShareClassResolution(
            ShareClassTopology.UNRESOLVED, ShareClassConfidence.LOW, ShareClassReason.NO_CIK,
            None, None, None)
    cik = str(cik).strip()
    if snapshot is None:
        return ShareClassResolution(
            ShareClassTopology.UNRESOLVED, ShareClassConfidence.LOW,
            ShareClassReason.NO_REFERENCE_SNAPSHOT, cik, None, None)
    age = (decision_date - snapshot.as_of).days
    if age > REFERENCE_TOPOLOGY_MAX_AGE_DAYS:
        return ShareClassResolution(
            ShareClassTopology.UNRESOLVED, ShareClassConfidence.LOW,
            ShareClassReason.REFERENCE_SNAPSHOT_STALE, cik, snapshot.as_of, age)

    securities = snapshot.securities(cik)
    if not securities:
        return ShareClassResolution(
            ShareClassTopology.UNRESOLVED, ShareClassConfidence.LOW,
            ShareClassReason.CIK_ABSENT_FROM_REFERENCE, cik, snapshot.as_of, age)

    identities = tuple(sorted({ref.class_identity for ref in securities}))
    ticker_identified = any(kind == "TICKER" for kind, _ in identities)

    if len(identities) == 1:
        if not ticker_identified:
            return ShareClassResolution(
                ShareClassTopology.SINGLE_CLASS_CONFIRMED, ShareClassConfidence.HIGH,
                ShareClassReason.ONE_FIGI_IDENTIFIED_CLASS, cik, snapshot.as_of, age,
                securities, identities, (UNLISTED_CLASS_BLIND_SPOT,))
        return ShareClassResolution(
            ShareClassTopology.SINGLE_CLASS_LIKELY, ShareClassConfidence.MEDIUM,
            ShareClassReason.ONE_TICKER_IDENTIFIED_CLASS, cik, snapshot.as_of, age,
            securities, identities,
            (UNLISTED_CLASS_BLIND_SPOT, TICKER_IDENTITY_LIMITATION))

    if not ticker_identified:
        return ShareClassResolution(
            ShareClassTopology.MULTI_CLASS_CONFIRMED, ShareClassConfidence.HIGH,
            ShareClassReason.MULTIPLE_FIGI_IDENTIFIED_CLASSES, cik, snapshot.as_of, age,
            securities, identities)

    # More than one security, at least one of which has no share-class FIGI. The FIGI-less row may be
    # a preferred share or a baby bond that the store types CS, or it may be a second common class;
    # the snapshot does not say which, and guessing from the ticker's suffix is exactly the kind of
    # string heuristic a FIGI exists to replace. Both readings deny market cap, so this is UNRESOLVED
    # rather than a confirmed multi-class: the refusal names what is actually unknown.
    return ShareClassResolution(
        ShareClassTopology.UNRESOLVED, ShareClassConfidence.LOW,
        ShareClassReason.UNIDENTIFIABLE_SIBLING_SECURITY, cik, snapshot.as_of, age,
        securities, identities)


# -------------------------------------------------------------------------------------------------
# Market cap and the reopened enterprise value
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketCapRecord:
    """A market cap with the provenance of every input that produced or blocked it."""

    ticker: str
    decision_time: datetime
    decision_date: date
    share_class: ShareClassResolution
    resolution: MarketCapResolution
    price: float | None
    price_date: date | None
    shares: SharesResolution | None = None
    limitations: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.resolution.valuation_ready

    @property
    def value(self) -> float | None:
        return self.resolution.value

    def to_dict(self) -> dict:
        fact = None if self.shares is None else self.shares.fact
        return {
            "contract_version": SHARE_CLASS_CONTRACT_VERSION,
            "ticker": self.ticker,
            "cik": self.share_class.cik,
            "decision_time": self.decision_time.isoformat(),
            "decision_date": self.decision_date.isoformat(),
            "status": self.resolution.status.value,
            "reason": self.resolution.reason,
            "market_cap": self.resolution.value,
            "share_class": self.share_class.to_dict(),
            "security_id": [ref.composite_figi for ref in self.share_class.securities],
            "price": self.price,
            "price_date": None if self.price_date is None else self.price_date.isoformat(),
            "shares": None if fact is None else {
                "value": fact.value,
                "fact_id": f"{fact.tag}:{fact.unit}:{fact.end.isoformat()}:"
                           f"{fact.accepted_at.isoformat()}",
                "shares_date": fact.end.isoformat(),
                "shares_age_days": (self.decision_date - fact.end).days,
                "shares_max_age_days": MAX_SHARES_STALENESS_DAYS,
                "acceptance_time": fact.accepted_at.isoformat(),
            },
            "shares_reason": None if self.shares is None else self.shares.reason,
            "limitations": list(self.limitations),
        }


def resolve_market_cap(
    ticker: str,
    facts: Iterable[CanonicalFact],
    decision_time: datetime,
    decision_date: date,
    regular_opens: Sequence[datetime],
    unadjusted_close: float | None,
    price_date: date | None,
    share_class: ShareClassResolution,
) -> MarketCapRecord:
    """Market cap for the valuation layer, from the practical determination.

    The arithmetic is `h0_5.historical_market_cap` and the share gate is `h0_5.resolve_pit_shares`,
    both reached through P0's `valuation_market_cap`, which this function does not reimplement: it
    supplies the state P0 demanded and records the provenance P0 had nowhere to put. PIT shares are
    still held to their own 135-day bound inside `h0_5`.
    """
    facts = tuple(facts)
    resolution = valuation_market_cap(facts, decision_date, regular_opens, unadjusted_close,
                                      share_class.gate_state)
    if resolution.status is MarketCapStatus.UNKNOWN_MULTIPLE_SHARE_CLASSES:
        resolution = MarketCapResolution(
            resolution.status, None, "MULTI_CLASS_UNRESOLVED_ALLOCATION: "
            "company-wide cover-page shares times one class's price is not a market cap",
            resolution.shares)
    shares = resolution.shares
    if shares is None:
        # The gate refused before reaching the shares primitive. Resolve them anyway, for provenance:
        # what a refusal cost is only legible next to what the other inputs would have supplied.
        shares = resolve_pit_shares(facts, decision_date, regular_opens)
    limitations = share_class.limitations if resolution.valuation_ready else ()
    return MarketCapRecord(ticker, decision_time, decision_date, share_class, resolution,
                           unadjusted_close, price_date, shares, limitations)


def reopened_enterprise_value(
    market_cap: MarketCapRecord,
    net_debt: NetDebtResolution,
    decision_time: datetime,
    decision_date: date,
) -> EnterpriseValueResolution:
    """EV from P1's own primitive, with P1.1's market cap as its first input.

    `capital_structure.enterprise_value` is called unchanged. P1.1 adds no EV arithmetic of its own:
    the only thing that changed between P1 and P1.1 is whether the market cap this function is handed
    is OK, and reusing the primitive is what makes that statement checkable.
    """
    return enterprise_value(market_cap.resolution, net_debt, decision_time, decision_date)
