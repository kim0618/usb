"""H-V2-D5-P1.1: practical share-class resolution, the valuation-use policy, market cap and EV.

The pure tests do not skip: the contract they assert is code, not data. The data-backed tests read
`data/runtime/`, which is gitignored, and skip when it is absent.

Every reference fixture below is the shape of a real row in the stored snapshot, and the issuers named
in the test names are the ones whose rows the fixture was built from, so a test that stops holding
points at the data it came from.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.backtest.strategy_h0.facts import CanonicalFact
from app.backtest.strategy_h0.h0_5 import MAX_SHARES_STALENESS_DAYS
from app.backtest.strategy_h_v2.valuation.capital_structure import (
    MAX_INSTANT_STALENESS_DAYS,
    CASH_FOR_EV_FIELD,
    CapitalStructureStatus,
    DebtMethod,
    DebtResolution,
    DebtSlot,
    DebtStatus,
    InstantResolution,
    InstantStatus,
    net_debt_from,
)
from app.backtest.strategy_h_v2.valuation.market_cap_gate import MarketCapStatus, ShareClassState
from app.backtest.strategy_h_v2.valuation.share_class import (
    REFERENCE_SNAPSHOT_DIRS,
    REFERENCE_TOPOLOGY_MAX_AGE_DAYS,
    TICKER_IDENTITY_LIMITATION,
    UNLISTED_CLASS_BLIND_SPOT,
    VALUATION_ALLOWED_TOPOLOGIES,
    ReferenceSnapshot,
    SecurityRef,
    ShareClassConfidence,
    ShareClassReason,
    ShareClassTopology,
    load_reference_snapshot,
    reopened_enterprise_value,
    resolve_market_cap,
    resolve_share_class,
)

DECISION_DATE = date(2026, 9, 16)
DECISION_TIME = datetime(2026, 9, 28, 5, 49, 37, tzinfo=timezone.utc)
SNAPSHOT_DATE = date(2026, 7, 1)


# -------------------------------------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------------------------------------

def row(ticker: str, cik: str, *, share_class_figi: str | None = None,
        composite_figi: str | None = "BBG000000001", active: bool = True,
        type_: str = "CS", delisted: str | None = None) -> dict:
    return {"ticker": ticker, "cik": cik, "type": type_, "market": "stocks", "locale": "us",
            "active": active, "delisted_utc": delisted, "composite_figi": composite_figi,
            "share_class_figi": share_class_figi, "primary_exchange": "XNAS"}


def snapshot(*rows: dict, as_of: date = SNAPSHOT_DATE) -> ReferenceSnapshot:
    """A snapshot built through the real loader's row filter, not by hand."""
    from app.backtest.strategy_h_v2.valuation.share_class import _is_active_us_common
    by_cik: dict[str, list[SecurityRef]] = {}
    for item in rows:
        if not _is_active_us_common(item):
            continue
        by_cik.setdefault(item["cik"], []).append(SecurityRef(
            item["ticker"], item["cik"], item["composite_figi"] or None,
            item["share_class_figi"] or None, item["primary_exchange"]))
    return ReferenceSnapshot(as_of, Path(__file__), {k: tuple(v) for k, v in by_cik.items()},
                             len(rows))


def instant(field: str, tag: str, value: float, end: date, accepted: datetime, *,
            unit: str = "USD", taxonomy: str = "us-gaap") -> CanonicalFact:
    return CanonicalFact(
        field=field, taxonomy=taxonomy, tag=tag, unit=unit, value=value, start=None, end=end,
        filed=accepted.date(), accepted_at=accepted, accession="0000000000-26-000001",
        form="10-Q", fiscal_year=end.year, fiscal_period="Q2", frame=None)


def shares_fact(value: float, end: date, accepted: datetime) -> CanonicalFact:
    return instant("shares_outstanding", "EntityCommonStockSharesOutstanding", value, end,
                   accepted, unit="shares", taxonomy="dei")


def opens(through: date = DECISION_DATE, count: int = 400) -> list[datetime]:
    return [datetime.combine(through - timedelta(days=i), datetime.min.time(),
                             tzinfo=timezone.utc).replace(hour=13, minute=30)
            for i in range(count)][::-1]


# -------------------------------------------------------------------------------------------------
# G. The current active-CS rule
# -------------------------------------------------------------------------------------------------

def test_one_active_cs_with_a_figi_is_single_class_confirmed_high():
    """The ten D4 issuers' shape: AEYE, COLL, IDCC each have exactly one FIGI-identified CS row."""
    result = resolve_share_class("0001362190", DECISION_DATE,
                                 snapshot(row("AEYE", "0001362190",
                                              share_class_figi="BBG002ZQLQC5")))
    assert result.topology is ShareClassTopology.SINGLE_CLASS_CONFIRMED
    assert result.confidence is ShareClassConfidence.HIGH
    assert result.reason is ShareClassReason.ONE_FIGI_IDENTIFIED_CLASS
    assert result.valuation_allowed
    assert result.gate_state is ShareClassState.SINGLE_CLASS


def test_two_active_cs_under_one_cik_with_two_figis_is_multi_class_confirmed():
    """FOX/FOXA, CIK 0001754301. Both rows carry a share-class FIGI, so the count is positive."""
    result = resolve_share_class("0001754301", DECISION_DATE, snapshot(
        row("FOX", "0001754301", share_class_figi="BBG00JHNKKR3"),
        row("FOXA", "0001754301", share_class_figi="BBG00JHNJX06")))
    assert result.topology is ShareClassTopology.MULTI_CLASS_CONFIRMED
    assert result.reason is ShareClassReason.MULTIPLE_FIGI_IDENTIFIED_CLASSES
    assert not result.valuation_allowed
    assert result.gate_state is ShareClassState.MULTIPLE_CLASSES


def test_goog_googl_style_extra_untagged_siblings_is_unresolved_not_single():
    """CIK 0001652044 carries GOOG, GOOGL, GOOGM and GOOGN; the last two have no share-class FIGI.

    The reading that matters is that it is not single-class. Whether it is reported as a confirmed
    multi-class or as unresolved, the market cap is denied either way, and naming the unresolved
    sibling is the more honest of the two.
    """
    result = resolve_share_class("0001652044", DECISION_DATE, snapshot(
        row("GOOG", "0001652044", share_class_figi="BBG009S3NB21"),
        row("GOOGL", "0001652044", share_class_figi="BBG009S39JY5"),
        row("GOOGM", "0001652044", share_class_figi=None),
        row("GOOGN", "0001652044", share_class_figi=None)))
    assert result.topology is ShareClassTopology.UNRESOLVED
    assert result.reason is ShareClassReason.UNIDENTIFIABLE_SIBLING_SECURITY
    assert not result.valuation_allowed


def test_ticker_mutation_under_one_identity_is_not_multi_class():
    """Two rows, one share-class FIGI: a listing recorded twice, or a rename, is one class."""
    result = resolve_share_class("0000999999", DECISION_DATE, snapshot(
        row("OLDT", "0000999999", share_class_figi="BBG001SAME01",
            composite_figi="BBG000000001"),
        row("NEWT", "0000999999", share_class_figi="BBG001SAME01",
            composite_figi="BBG000000002")))
    assert result.topology is ShareClassTopology.SINGLE_CLASS_CONFIRMED
    assert result.class_identities == (("FIGI", "BBG001SAME01"),)
    assert result.valuation_allowed


def test_one_ticker_identified_class_is_single_class_likely_medium():
    """ACN's shape: one active CS row whose share_class_figi is absent from the snapshot."""
    result = resolve_share_class("0001467373", DECISION_DATE,
                                 snapshot(row("ACN", "0001467373", share_class_figi=None)))
    assert result.topology is ShareClassTopology.SINGLE_CLASS_LIKELY
    assert result.confidence is ShareClassConfidence.MEDIUM
    assert result.reason is ShareClassReason.ONE_TICKER_IDENTIFIED_CLASS
    assert result.valuation_allowed
    assert TICKER_IDENTITY_LIMITATION in result.limitations


def test_a_figi_identified_class_beside_an_untagged_sibling_is_denied():
    """FULT/FULTP, CIK 0000700564. FULTP is preferred stock the store types CS and leaves FIGI-less.

    Both readings - preferred, or an undisclosed second common class - are consistent with the
    snapshot, so the determination refuses rather than picking the one that would let a number out.
    """
    result = resolve_share_class("0000700564", DECISION_DATE, snapshot(
        row("FULT", "0000700564", share_class_figi="BBG001S5RF02"),
        row("FULTP", "0000700564", share_class_figi=None)))
    assert result.topology is ShareClassTopology.UNRESOLVED
    assert result.reason is ShareClassReason.UNIDENTIFIABLE_SIBLING_SECURITY
    assert not result.valuation_allowed


def test_all_siblings_untagged_is_unresolved_not_single():
    """LBTYA/LBTYB/LBTYK, CIK 0001570585: three genuine classes, none carrying a share-class FIGI."""
    result = resolve_share_class("0001570585", DECISION_DATE, snapshot(
        row("LBTYA", "0001570585", share_class_figi=None),
        row("LBTYB", "0001570585", share_class_figi=None),
        row("LBTYK", "0001570585", share_class_figi=None)))
    assert result.topology is ShareClassTopology.UNRESOLVED
    assert not result.valuation_allowed


def test_non_common_and_inactive_rows_are_excluded_before_counting():
    """An ETF, a warrant, a delisted class and an inactive row are not share classes of the issuer."""
    result = resolve_share_class("0000111111", DECISION_DATE, snapshot(
        row("AAA", "0000111111", share_class_figi="BBG001REAL01"),
        row("AAAF", "0000111111", share_class_figi="BBG001ETF001", type_="ETF"),
        row("AAAW", "0000111111", share_class_figi="BBG001WAR001", type_="WARRANT"),
        row("AAAB", "0000111111", share_class_figi="BBG001OLD001", active=False),
        row("AAAC", "0000111111", share_class_figi="BBG001GONE01",
            delisted="2026-02-03T00:00:00Z")))
    assert result.topology is ShareClassTopology.SINGLE_CLASS_CONFIRMED
    assert [ref.ticker for ref in result.securities] == ["AAA"]


def test_missing_cik_is_unresolved_and_never_single():
    for missing in (None, "", "   "):
        result = resolve_share_class(missing, DECISION_DATE,
                                     snapshot(row("AAA", "0000111111",
                                                  share_class_figi="BBG001REAL01")))
        assert result.topology is ShareClassTopology.UNRESOLVED
        assert result.reason is ShareClassReason.NO_CIK
        assert not result.valuation_allowed


def test_cik_absent_from_the_reference_is_unresolved_not_single():
    """DALN and BRY: in the D5-D1 twelve, absent from the 2026-07-01 snapshot."""
    result = resolve_share_class("0001413898", DECISION_DATE,
                                 snapshot(row("AAA", "0000111111",
                                              share_class_figi="BBG001REAL01")))
    assert result.topology is ShareClassTopology.UNRESOLVED
    assert result.reason is ShareClassReason.CIK_ABSENT_FROM_REFERENCE
    assert not result.valuation_allowed


def test_no_snapshot_at_all_is_unresolved():
    result = resolve_share_class("0000111111", DECISION_DATE, None)
    assert result.topology is ShareClassTopology.UNRESOLVED
    assert result.reason is ShareClassReason.NO_REFERENCE_SNAPSHOT
    assert not result.valuation_allowed


# -------------------------------------------------------------------------------------------------
# F. Freshness: the reference bound is its own, and it is enforced
# -------------------------------------------------------------------------------------------------

def test_a_snapshot_older_than_the_reference_bound_is_refused():
    stale = DECISION_DATE - timedelta(days=REFERENCE_TOPOLOGY_MAX_AGE_DAYS + 1)
    result = resolve_share_class("0000111111", DECISION_DATE,
                                 snapshot(row("AAA", "0000111111",
                                              share_class_figi="BBG001REAL01"), as_of=stale))
    assert result.topology is ShareClassTopology.UNRESOLVED
    assert result.reason is ShareClassReason.REFERENCE_SNAPSHOT_STALE
    assert result.snapshot_age_days == REFERENCE_TOPOLOGY_MAX_AGE_DAYS + 1


def test_a_snapshot_exactly_at_the_reference_bound_is_accepted():
    """The bound is inclusive, as `MAX_INSTANT_STALENESS_DAYS` is in `capital_structure`."""
    edge = DECISION_DATE - timedelta(days=REFERENCE_TOPOLOGY_MAX_AGE_DAYS)
    result = resolve_share_class("0000111111", DECISION_DATE,
                                 snapshot(row("AAA", "0000111111",
                                              share_class_figi="BBG001REAL01"), as_of=edge))
    assert result.topology is ShareClassTopology.SINGLE_CLASS_CONFIRMED


def test_the_reference_bound_is_not_the_financial_bound():
    """They are different numbers governing different data, and nothing may silently share them."""
    assert REFERENCE_TOPOLOGY_MAX_AGE_DAYS != MAX_INSTANT_STALENESS_DAYS
    assert MAX_INSTANT_STALENESS_DAYS == MAX_SHARES_STALENESS_DAYS == 135


def test_a_snapshot_published_after_the_decision_date_is_never_loaded(tmp_path):
    import gzip
    import json
    directory = tmp_path / "tickers"
    directory.mkdir()
    for as_of in ("2026-07-01", "2026-10-01"):
        payload = {"as_of": as_of,
                   "results": [row("AAA", "0000111111", share_class_figi="BBG001REAL01")]}
        (directory / f"CS_{as_of}.json.gz").write_bytes(
            gzip.compress(json.dumps(payload).encode()))
    loaded = load_reference_snapshot(DECISION_DATE, (directory,))
    assert loaded is not None and loaded.as_of == date(2026, 7, 1)


# -------------------------------------------------------------------------------------------------
# K. The valuation-use policy, stated once
# -------------------------------------------------------------------------------------------------

def test_the_allowed_topologies_are_exactly_the_two_single_class_readings():
    assert VALUATION_ALLOWED_TOPOLOGIES == frozenset({
        ShareClassTopology.SINGLE_CLASS_CONFIRMED, ShareClassTopology.SINGLE_CLASS_LIKELY})
    for topology in ShareClassTopology:
        allowed = topology in VALUATION_ALLOWED_TOPOLOGIES
        assert allowed is topology.value.startswith("SINGLE_CLASS")


def test_every_allowed_reading_discloses_the_unlisted_class_blind_spot():
    for cik, rows in (("0001362190", (row("AEYE", "0001362190",
                                          share_class_figi="BBG002ZQLQC5"),)),
                      ("0001467373", (row("ACN", "0001467373", share_class_figi=None),))):
        result = resolve_share_class(cik, DECISION_DATE, snapshot(*rows))
        assert result.valuation_allowed
        assert UNLISTED_CLASS_BLIND_SPOT in result.limitations


def test_every_denied_reading_maps_to_a_gate_state_that_blocks():
    denied = [
        resolve_share_class("0001754301", DECISION_DATE, snapshot(
            row("FOX", "0001754301", share_class_figi="BBG00JHNKKR3"),
            row("FOXA", "0001754301", share_class_figi="BBG00JHNJX06"))),
        resolve_share_class("0000700564", DECISION_DATE, snapshot(
            row("FULT", "0000700564", share_class_figi="BBG001S5RF02"),
            row("FULTP", "0000700564", share_class_figi=None))),
        resolve_share_class(None, DECISION_DATE, None),
    ]
    assert [r.gate_state for r in denied] == [ShareClassState.MULTIPLE_CLASSES,
                                             ShareClassState.UNRESOLVED,
                                             ShareClassState.UNRESOLVED]
    assert not any(r.valuation_allowed for r in denied)


# -------------------------------------------------------------------------------------------------
# L. Market cap
# -------------------------------------------------------------------------------------------------

def _market_cap(share_class, *, shares_end: date = date(2026, 7, 30),
                accepted: datetime = datetime(2026, 8, 13, 21, 11, tzinfo=timezone.utc),
                shares: float = 12_569_674.0, close: float | None = 7.21):
    facts = (shares_fact(shares, shares_end, accepted),)
    return resolve_market_cap("AEYE", facts, DECISION_TIME, DECISION_DATE, opens(), close,
                              DECISION_DATE, share_class)


def _single_class(cik: str = "0001362190") -> object:
    return resolve_share_class(cik, DECISION_DATE,
                              snapshot(row("AEYE", cik, share_class_figi="BBG002ZQLQC5")))


def test_market_cap_is_price_times_pit_raw_shares():
    """AEYE at 2026-09-16: 12,569,674 shares at 7.21."""
    record = _market_cap(_single_class())
    assert record.resolution.status is MarketCapStatus.OK
    assert record.value == pytest.approx(12_569_674.0 * 7.21)
    assert record.ok


def test_medium_confidence_market_cap_is_allowed_and_carries_its_limitations():
    likely = resolve_share_class("0001467373", DECISION_DATE,
                                 snapshot(row("ACN", "0001467373", share_class_figi=None)))
    record = _market_cap(likely)
    assert record.ok
    assert record.share_class.confidence is ShareClassConfidence.MEDIUM
    assert TICKER_IDENTITY_LIMITATION in record.limitations
    assert UNLISTED_CLASS_BLIND_SPOT in record.limitations


def test_low_confidence_market_cap_is_denied_with_no_value():
    unresolved = resolve_share_class(None, DECISION_DATE, None)
    record = _market_cap(unresolved)
    assert record.resolution.status is MarketCapStatus.UNKNOWN_SHARE_CLASS_UNRESOLVED
    assert record.value is None and not record.ok
    assert record.to_dict()["market_cap"] is None


def test_confirmed_multi_class_market_cap_is_denied_and_names_the_allocation():
    multi = resolve_share_class("0001754301", DECISION_DATE, snapshot(
        row("FOX", "0001754301", share_class_figi="BBG00JHNKKR3"),
        row("FOXA", "0001754301", share_class_figi="BBG00JHNJX06")))
    record = _market_cap(multi)
    assert record.resolution.status is MarketCapStatus.UNKNOWN_MULTIPLE_SHARE_CLASSES
    assert record.value is None
    assert "MULTI_CLASS_UNRESOLVED_ALLOCATION" in record.resolution.reason


def test_stale_pit_shares_are_still_refused_under_the_135_day_rule():
    """The reference bound does not loosen the shares bound; they are applied to their own inputs."""
    too_old = DECISION_DATE - timedelta(days=MAX_SHARES_STALENESS_DAYS + 1)
    record = _market_cap(_single_class(), shares_end=too_old,
                         accepted=datetime(2026, 1, 5, tzinfo=timezone.utc))
    assert record.resolution.status is MarketCapStatus.UNKNOWN_SHARES
    assert record.value is None


def test_no_price_is_refused_rather_than_valued():
    record = _market_cap(_single_class(), close=None)
    assert record.resolution.status is MarketCapStatus.UNKNOWN_PRICE
    assert record.value is None


def test_market_cap_provenance_carries_every_16_field():
    record = _market_cap(_single_class())
    payload = record.to_dict()
    for key in ("decision_time", "ticker", "cik", "security_id", "price", "price_date",
                "shares", "market_cap", "limitations", "status"):
        assert key in payload, key
    assert payload["share_class"]["topology"] == ShareClassTopology.SINGLE_CLASS_CONFIRMED.value
    assert payload["share_class"]["confidence"] == ShareClassConfidence.HIGH.value
    assert payload["share_class"]["reference_snapshot_date"] == SNAPSHOT_DATE.isoformat()
    assert payload["shares"]["shares_date"] == "2026-07-30"
    assert payload["shares"]["shares_age_days"] == (DECISION_DATE - date(2026, 7, 30)).days
    assert payload["shares"]["shares_max_age_days"] == MAX_SHARES_STALENESS_DAYS


# -------------------------------------------------------------------------------------------------
# M. EV reopened from P1's primitives
# -------------------------------------------------------------------------------------------------

def _net_debt(debt: float = 10_000_000.0, cash: float = 2_299_000.0,
              end: date = date(2026, 6, 30)) -> object:
    accepted = datetime(2026, 8, 13, 21, 11, tzinfo=timezone.utc)
    cash_fact = instant("cash_for_ev", "CashAndCashEquivalentsAtCarryingValue", cash, end, accepted)
    debt_fact = instant("debt_reported_total", "DebtLongtermAndShorttermCombinedAmount", debt, end,
                        accepted)
    return net_debt_from(
        DebtResolution(DebtStatus.OK, "composed", value=debt, unit="USD",
                       method=DebtMethod.REPORTED_TOTAL, period_end=end,
                       age_days=(DECISION_DATE - end).days,
                       components=((DebtSlot.REPORTED_TOTAL, debt_fact),)),
        InstantResolution(CASH_FOR_EV_FIELD, InstantStatus.OK, "resolved", fact=cash_fact,
                          period_end=end, age_days=(DECISION_DATE - end).days))


def test_enterprise_value_reopens_from_the_p1_primitive():
    record = _market_cap(_single_class())
    net = _net_debt()
    ev = reopened_enterprise_value(record, net, DECISION_TIME, DECISION_DATE)
    assert ev.status is CapitalStructureStatus.OK
    assert ev.value == pytest.approx(record.value + net.value)
    assert ev.components_sum == pytest.approx(ev.value)


def test_enterprise_value_stays_unknown_when_the_share_class_denies_the_market_cap():
    record = _market_cap(resolve_share_class(None, DECISION_DATE, None))
    ev = reopened_enterprise_value(record, _net_debt(), DECISION_TIME, DECISION_DATE)
    assert ev.status is CapitalStructureStatus.MULTI_CLASS_UNKNOWN
    assert ev.value is None


def test_enterprise_value_stays_unknown_when_debt_is_not_resolved():
    """FRPT and CRK: the market cap opens, the debt composition does not, and EV must still refuse."""
    record = _market_cap(_single_class())
    end = date(2026, 6, 30)
    accepted = datetime(2026, 8, 13, 21, 11, tzinfo=timezone.utc)
    incomplete = net_debt_from(
        DebtResolution(DebtStatus.INCOMPLETE_COMPONENTS, "the noncurrent slot did not resolve"),
        InstantResolution(CASH_FOR_EV_FIELD, InstantStatus.OK, "resolved",
                          fact=instant("cash_for_ev", "CashAndCashEquivalentsAtCarryingValue",
                                       2_299_000.0, end, accepted),
                          period_end=end, age_days=(DECISION_DATE - end).days))
    ev = reopened_enterprise_value(record, incomplete, DECISION_TIME, DECISION_DATE)
    assert ev.status is CapitalStructureStatus.INCOMPLETE_DEBT_COMPONENTS
    assert ev.value is None
    assert record.ok


# -------------------------------------------------------------------------------------------------
# Data-backed: the stored snapshot and the controls
# -------------------------------------------------------------------------------------------------

def _stored() -> ReferenceSnapshot | None:
    if not any(directory.exists() for directory in REFERENCE_SNAPSHOT_DIRS):
        return None
    return load_reference_snapshot(DECISION_DATE)


def test_stored_snapshot_is_within_its_own_bound_at_the_d5_decision_date():
    stored = _stored()
    if stored is None:
        pytest.skip("data/runtime reference snapshots absent")
    age = (DECISION_DATE - stored.as_of).days
    assert 0 <= age <= REFERENCE_TOPOLOGY_MAX_AGE_DAYS
    assert stored.rows > 1000 and len(stored.by_cik) > 1000


@pytest.mark.parametrize("name,cik", [
    ("GOOG/GOOGL", "0001652044"), ("FOX/FOXA", "0001754301"), ("BRK.A/BRK.B", "0001067983"),
    ("BF.A/BF.B", "0000014693"), ("BIO/BIO.B", "0000012208"), ("CENT/CENTA", "0000887733"),
    ("BELFA/BELFB", "0000729580"), ("AGM/AGM.A", "0000845877"),
    ("LBTYA/LBTYB/LBTYK", "0001570585"), ("LILA/LILAK", "0001712184"),
])
def test_known_multi_class_controls_are_blocked_in_the_stored_snapshot(name, cik):
    stored = _stored()
    if stored is None:
        pytest.skip("data/runtime reference snapshots absent")
    result = resolve_share_class(cik, DECISION_DATE, stored)
    assert not result.valuation_allowed, f"{name} was allowed as {result.topology.value}"


@pytest.mark.parametrize("name,cik", [
    ("AAPL", "0000320193"), ("MSFT", "0000789019"), ("NVDA", "0001045810"),
])
def test_known_single_class_controls_are_allowed_in_the_stored_snapshot(name, cik):
    stored = _stored()
    if stored is None:
        pytest.skip("data/runtime reference snapshots absent")
    result = resolve_share_class(cik, DECISION_DATE, stored)
    assert result.valuation_allowed, f"{name} was denied as {result.reason.value}"
    assert result.topology is ShareClassTopology.SINGLE_CLASS_CONFIRMED


@pytest.mark.parametrize("ticker,cik", [
    ("AEYE", "0001362190"), ("COLL", "0001267565"), ("FG", "0001934850"),
    ("VRRM", "0001682745"), ("IDCC", "0001405495"), ("DORM", "0000868780"),
    ("FRPT", "0001611647"), ("TG", "0000850429"), ("CRK", "0000023194"),
    ("SPSC", "0001092699"),
])
def test_the_d4_ten_resolve_single_class_in_the_stored_snapshot(ticker, cik):
    stored = _stored()
    if stored is None:
        pytest.skip("data/runtime reference snapshots absent")
    result = resolve_share_class(cik, DECISION_DATE, stored)
    assert result.topology is ShareClassTopology.SINGLE_CLASS_CONFIRMED
    assert result.confidence is ShareClassConfidence.HIGH
