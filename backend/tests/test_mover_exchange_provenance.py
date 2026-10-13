"""The exchange of an A-MOVER-LIVE candidate survives from the collector's listing to entry.

Production 2026-10-12: six approved mover candidates (AMT, APH, CCI, ECHO, FSLY, ONTO) failed
every minute with ``Exchange MISSING is not a supported US exchange``. The mover scanner's
components carry no venue, the live handoff persisted them as they were, and entry binds every
lookup to the exchange stored on the candidate. These tests hold the venue at each hop and hold
the refusal when no listing exists.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, create_db_engine
from app.core.exceptions import MarketDataError
from app.dev import repair_mover_candidate_exchange as REPAIR
from app.integrations.kiwoom.mapping import exchange_code
from app.models.analytics import PremarketVolumeSession
from app.models.scanner import ScannerCandidate, ScannerRun
from app.services.exchange_authority import stored_exchange
from app.strategy_a_mover_live import gpt_handoff as GPT
from app.strategy_a_mover_live import scanner as SCAN
from tests.strategy_a_mover_live.test_a_live_scanner import OBSERVED, ON, panel_input

NOW = datetime(2026, 9, 15, 13, 16, tzinfo=timezone.utc)
# The 2026-10-12 six, with the Kiwoom listing premarket_volume_sessions holds for each.
OCT_12 = {"AMT": "NY", "APH": "NY", "CCI": "NY", "ECHO": "ND", "FSLY": "ND", "ONTO": "NY"}
NAMES = {"ND": "NASDAQ", "NY": "NYSE", "NA": "AMEX"}


@pytest.fixture
def database(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'exchange.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def live_scan(count: int = 8):
    return SCAN.run(panel_input(count), observed_at=OBSERVED, environ=ON)


def symbols_of(live) -> list[str]:
    return [item.symbol for item in live.selection.handoff]


# -- 1-3: the three supported venues map to their canonical names, nothing else does -----------

@pytest.mark.parametrize("raw,expected", [("NY", "NYSE"), ("ND", "NASDAQ"), ("NA", "AMEX"),
                                          ("NYSE", "NYSE"), ("nasdaq", "NASDAQ"), ("AMEX", "AMEX")])
def test_supported_venues_are_stored_by_canonical_name(raw, expected):
    assert SCAN.stored_exchange({"X": raw}, "X") == expected


@pytest.mark.parametrize("raw", ["", None, "MISSING", "UNKNOWN", "LSE", "XX"])
def test_an_unsupported_or_missing_listing_is_never_defaulted(raw):
    assert SCAN.stored_exchange({"X": raw}, "X") is None
    assert SCAN.stored_exchange({}, "X") is None
    assert SCAN.stored_exchange(None, "X") is None


# -- 4-5: mover rows carry the venue, and carry nothing else differently -----------------------

def test_mover_rows_carry_the_collectors_listing_and_nothing_else_changes():
    live = live_scan()
    listing = {symbol: ("NY" if index % 2 else "ND") for index, symbol in enumerate(symbols_of(live))}
    plain = SCAN.candidate_rows(live)
    listed = SCAN.candidate_rows(live, listing)
    assert all("exchange" not in row.score_components for row in plain)      # how 10/12 was written
    for before, after in zip(plain, listed, strict=True):
        assert after.score_components["exchange"] == NAMES[listing[after.symbol]]
        assert {k: v for k, v in after.score_components.items() if k != "exchange"} == before.score_components
        assert (after.symbol, after.rank, after.score, after.is_top8) == \
            (before.symbol, before.rank, before.score, before.is_top8)       # ranking is untouched


def test_the_classic_scanner_contract_is_unchanged():
    from app.integrations.kiwoom.mapping import EXCHANGES
    assert EXCHANGES == {"ND": "NASDAQ", "NY": "NYSE", "NA": "AMEX"}


# -- 6: persistence and reload ------------------------------------------------------------------

def test_the_venue_survives_persistence_and_reload(database):
    live = live_scan()
    listing = {symbol: ("NY" if index % 2 else "ND") for index, symbol in enumerate(symbols_of(live))}
    result = GPT.persist(database, live, now=NOW, exchanges=listing)
    database.expire_all()
    rows = database.scalars(select(ScannerCandidate).where(
        ScannerCandidate.scanner_run_id == result.run_id)).all()
    assert len(rows) == len(listing)
    assert {row.symbol: stored_exchange(row) for row in rows} == {s: NAMES[c] for s, c in listing.items()}


# -- 7: entry no longer meets MISSING -----------------------------------------------------------

def test_entry_resolves_every_persisted_venue_without_missing(database):
    live = live_scan()
    listing = {symbol: "NY" for symbol in symbols_of(live)}
    result = GPT.persist(database, live, now=NOW, exchanges=listing)
    for row in database.scalars(select(ScannerCandidate).where(
            ScannerCandidate.scanner_run_id == result.run_id)):
        assert exchange_code(stored_exchange(row)) == "NY"      # what kiwoom.bind_exchange calls


# -- 8: a symbol with no listing is refused by name, not guessed --------------------------------

def test_a_symbol_the_listing_lacks_stays_unlisted_and_entry_refuses_it(database):
    live = live_scan()
    symbols = symbols_of(live)
    listing = {symbol: "ND" for symbol in symbols[1:]}            # the first one is not listed
    result = GPT.persist(database, live, now=NOW, exchanges=listing)
    row = database.scalar(select(ScannerCandidate).where(
        ScannerCandidate.scanner_run_id == result.run_id, ScannerCandidate.symbol == symbols[0]))
    assert "exchange" not in row.score_components_json
    with pytest.raises(MarketDataError, match="Exchange MISSING is not a supported US exchange"):
        exchange_code(stored_exchange(row))


# -- 10/12's six symbols: the failure as it was, and the repair that fixes only run 31-style rows --

def seed_run(session, *, score_version="a_mover_live_v1", symbols=tuple(OCT_12), exchange=None):
    run = ScannerRun(trading_date=datetime(2026, 10, 9).date(), started_at=NOW, completed_at=NOW,
                     status="COMPLETED", provider="KIWOOM_AE_SHARED_PREMARKET",
                     score_version=score_version, universe_count=10, excluded_count=2,
                     candidate_count=len(symbols), top8_count=len(symbols))
    session.add(run)
    session.flush()
    for rank, symbol in enumerate(symbols, start=1):
        components = {"raw": {}, "normalized": {}, "weighted_contributions": {}}
        if exchange:
            components["exchange"] = exchange
        session.add(ScannerCandidate(scanner_run_id=run.id, symbol=symbol, rank=rank, is_top8=True,
                                     score=1.0, score_components_json=components,
                                     observed_at=NOW, available_at=NOW))
    session.commit()
    return run


def seed_listing(session, symbol: str, code: str, *, days=3):
    for day in range(days):
        session.add(PremarketVolumeSession(
            symbol=symbol, exchange=code, trading_date=datetime(2026, 9, 1 + day).date(),
            source="KIWOOM", collector_version="v1", premarket_volume=1000, bar_count=10,
            regular_bar_count=10, quality_status="COMPLETE", collected_at=NOW))
    session.commit()


def test_the_october_12_set_failed_exactly_as_logged(database):
    run = seed_run(database)
    for row in database.scalars(select(ScannerCandidate).where(ScannerCandidate.scanner_run_id == run.id)):
        with pytest.raises(MarketDataError, match="Exchange MISSING"):
            exchange_code(stored_exchange(row))


def test_the_repair_fills_only_the_unambiguous_stored_listing(database):
    run = seed_run(database)
    for symbol, code in OCT_12.items():
        seed_listing(database, symbol, code)
    records = REPAIR.plan(database, run.id)
    assert {r["symbol"]: r["exchange"] for r in records} == {s: NAMES[c] for s, c in OCT_12.items()}
    assert all(r["action"] == "SET" for r in records)
    # a dry run wrote nothing
    assert all("exchange" not in row.score_components_json for row in database.scalars(
        select(ScannerCandidate).where(ScannerCandidate.scanner_run_id == run.id)))
    assert REPAIR.apply(database, records) == 6
    database.expire_all()
    for row in database.scalars(select(ScannerCandidate).where(ScannerCandidate.scanner_run_id == run.id)):
        assert exchange_code(stored_exchange(row)) == OCT_12[row.symbol]
        assert row.score_components_json["raw"] == {}                   # nothing else was touched


def test_the_repair_never_guesses(database):
    run = seed_run(database, symbols=("AMT", "AMBIG", "NOLIST"))
    seed_listing(database, "AMT", "NY")
    seed_listing(database, "AMBIG", "ND")
    seed_listing(database, "AMBIG", "NY", days=2)                 # two different stored listings
    records = {r["symbol"]: r for r in REPAIR.plan(database, run.id)}
    assert records["AMT"]["action"] == "SET"
    assert records["AMBIG"] == {**records["AMBIG"], "action": "SKIP", "reason": "AMBIGUOUS_OR_UNSUPPORTED_LISTING"}
    assert records["NOLIST"]["reason"] == "NO_STORED_LISTING"
    assert REPAIR.apply(database, list(records.values())) == 1
    database.expire_all()
    untouched = database.scalar(select(ScannerCandidate).where(ScannerCandidate.symbol == "NOLIST"))
    assert "exchange" not in untouched.score_components_json


def test_the_repair_refuses_other_runs_and_keeps_a_stored_venue(database):
    legacy = seed_run(database, score_version="quant_v0")
    with pytest.raises(SystemExit):
        REPAIR.plan(database, legacy.id)
    kept = seed_run(database, symbols=("AMT",), exchange="NASDAQ")
    seed_listing(database, "AMT", "NY")
    (record,) = REPAIR.plan(database, kept.id)
    assert record["action"] == "SKIP" and record["reason"] == "ALREADY_HAS_EXCHANGE"
    assert REPAIR.apply(database, [record]) == 0
