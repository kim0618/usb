"""The mixed Kiwoom/Massive denominator: section Q's fifteen cases, and the mix as a record.

Section Q is about one claim: twenty *combined* covered sessions is the precondition for a
live calculation, and the provider of each session is recorded rather than required. So these
tests do not assert that the walk prefers Kiwoom as a style choice - they assert the
consequences of preferring it, which is that the bootstrap half shrinks on its own as the
shared collector stores one more morning, and reaches zero after at most twenty of them with
no operator step in between.
"""

import dataclasses
from datetime import date, datetime, timezone
import json
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.backtest.mover_scanner_v1 import contract as K
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.backtest.strategy_b_e0.session_cache import CACHE_VERSION, VALUE_COLUMNS
from app.core.database import Base, create_db_engine
from app.market.calendar import MarketCalendar
from app.models.analytics import PremarketVolumeSession
from app.services import premarket_volume_history as V
from app.strategy_a_mover_live import baseline as B
from app.strategy_a_mover_live import bootstrap as BOOT
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live import snapshot as SNAP
from app.strategy_a_mover_live.schedule import A_CUT_MINUTE, ET
from tests.strategy_a_mover_live.fixtures import SESSION

CAL = MarketCalendar("America/New_York")
NOW = datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc)
SESSIONS = B.BASELINE_SESSIONS


@pytest.fixture
def database(tmp_path: Path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'mixed.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def prior(count: int, before: date = SESSION) -> list[date]:
    """``count`` completed sessions strictly before ``before``, newest last."""
    out, day = [], before
    for _ in range(count):
        day = CAL.previous_trading_day(day)
        out.append(day)
    return list(reversed(out))


def store(session, day: date, volume: int | None, provider: B.BaselineProvider, *,
          symbol: str = "AAA", exchange: str = "ND",
          quality: str = V.SessionQuality.COMPLETE.value) -> None:
    rule = B.BaselineRule()
    source, version = ((rule.source, rule.collector_version)
                       if provider is B.BaselineProvider.KIWOOM
                       else (rule.bootstrap_source, rule.bootstrap_collector_version))
    session.add(PremarketVolumeSession(
        symbol=symbol, exchange=exchange, trading_date=day, source=source,
        collector_version=version, premarket_volume=volume, bar_count=1 if volume else 0,
        regular_bar_count=0, first_timestamp=None, last_timestamp=None, pages_used=1,
        target_reached=True, quality_status=quality, quality_reason=None, collected_at=NOW))
    session.flush()


def mixed(session, kiwoom: int, massive: int, *, before: date = SESSION) -> None:
    """``kiwoom`` newest sessions from Kiwoom, the ``massive`` behind them from the tape.

    Newest-first is how the two halves really arrive: the collector stores this morning, so
    Kiwoom history grows at the recent end and the bootstrap half is always the older tail.
    """
    days = prior(kiwoom + massive, before)
    for day in days[-kiwoom:] if kiwoom else []:
        store(session, day, 100, B.BaselineProvider.KIWOOM)
    for day in days[:massive]:
        store(session, day, 300, B.BaselineProvider.MASSIVE)
    session.commit()


# -- section Q 1-5, 10, 11: every mix, its counts and its mode ------------------------------

@pytest.mark.parametrize("kiwoom,massive,mode", [
    (0, 20, B.BaselineMode.MASSIVE_BOOTSTRAP),
    (1, 19, B.BaselineMode.MIXED_BOOTSTRAP),
    (10, 10, B.BaselineMode.MIXED_BOOTSTRAP),
    (19, 1, B.BaselineMode.MIXED_BOOTSTRAP),
    (20, 0, B.BaselineMode.KIWOOM_NATIVE),
])
def test_every_provider_mix_is_available_with_exact_counts_and_mode(
        database, kiwoom, massive, mode):
    mixed(database, kiwoom, massive)
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.status is B.BaselineStatus.AVAILABLE
    assert item.baseline_session_count == SESSIONS
    assert item.kiwoom_session_count == kiwoom
    assert item.massive_session_count == massive
    assert item.mode is mode
    body = item.declaration()
    assert body["baseline_mode"] == str(mode)
    assert body["kiwoom_session_count"] == kiwoom
    assert body["massive_session_count"] == massive
    assert body["baseline_session_count"] == SESSIONS


def test_the_median_is_over_both_halves_not_one_of_them(database):
    mixed(database, 10, 10)
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    # Ten 100s and ten 300s: the median of the mixed twenty, not of either half.
    assert item.median_volume == pytest.approx(200.0)
    assert sorted(item.volumes) == [100.0] * 10 + [300.0] * 10


# -- section Q 6, 9: only the latest twenty completed sessions ------------------------------

def test_twenty_one_kiwoom_sessions_use_the_latest_twenty(database):
    days = prior(SESSIONS + 1)
    for index, day in enumerate(days):
        store(database, day, 100 + index, B.BaselineProvider.KIWOOM)
    database.commit()
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.status is B.BaselineStatus.AVAILABLE
    assert item.baseline_session_count == SESSIONS
    assert item.used_sessions == tuple(days[1:])          # the oldest is dropped, not the newest
    assert days[0] not in item.used_sessions
    assert item.kiwoom_session_count == SESSIONS


def test_the_window_is_the_newest_sessions_even_when_older_ones_are_stored(database):
    days = prior(SESSIONS + 10)
    for day in days:
        store(database, day, 100, B.BaselineProvider.MASSIVE)
    database.commit()
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.used_sessions == tuple(days[-SESSIONS:])
    assert item.newest_used_session == days[-1]
    assert item.newest_used_session == CAL.previous_trading_day(SESSION)


# -- section Q 7: both providers on one session -> Kiwoom ------------------------------------

def test_a_session_both_providers_cover_is_a_kiwoom_session(database):
    for day in prior(SESSIONS):
        store(database, day, 100, B.BaselineProvider.KIWOOM)
        store(database, day, 999, B.BaselineProvider.MASSIVE)
    database.commit()
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.kiwoom_session_count == SESSIONS
    assert item.massive_session_count == 0
    assert item.mode is B.BaselineMode.KIWOOM_NATIVE
    assert set(item.volumes) == {100.0}                   # the bootstrap value is not consulted


def test_kiwoom_wins_the_session_even_when_the_bootstrap_row_is_newer(database):
    """Priority is by provider, not by ``collected_at``: a later bootstrap write never wins."""
    day = prior(1)[0]
    store(database, day, 100, B.BaselineProvider.KIWOOM)
    database.commit()
    store(database, day, 777, B.BaselineProvider.MASSIVE)
    for older in prior(SESSIONS)[:-1]:
        store(database, older, 300, B.BaselineProvider.MASSIVE)
    database.commit()
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.provider_sessions(B.BaselineProvider.KIWOOM) == (day,)
    assert item.volumes[-1] == 100.0


# -- section Q 8: the current session is never a denominator ---------------------------------

def test_the_scan_session_is_excluded_from_its_own_denominator_under_either_provider(database):
    mixed(database, 10, 10)
    store(database, SESSION, 999_999, B.BaselineProvider.KIWOOM)
    store(database, SESSION, 888_888, B.BaselineProvider.MASSIVE)
    database.commit()
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert SESSION not in item.used_sessions
    assert 999_999.0 not in item.volumes and 888_888.0 not in item.volumes
    assert item.rule.declaration()["current_session_in_denominator"] is False


# -- section Q 12, 14, 15: the forward replacement, one session at a time --------------------

def test_each_forward_session_replaces_one_bootstrap_session(database):
    """Nineteen bootstrap sessions become nineteen Kiwoom sessions over nineteen mornings."""
    mixed(database, 1, 19)
    seen = []
    day = SESSION
    for _ in range(SESSIONS):
        item = B.load_baseline(database, "AAA", "ND", day, calendar=CAL)
        assert item.status is B.BaselineStatus.AVAILABLE, day
        assert item.baseline_session_count == SESSIONS
        seen.append((item.kiwoom_session_count, item.massive_session_count, str(item.mode)))
        # the morning of ``day`` stores its own observation, exactly as the collector does
        store(database, day, 100, B.BaselineProvider.KIWOOM)
        database.commit()
        day = CAL.next_trading_day(day)
    kiwoom = [row[0] for row in seen]
    massive = [row[1] for row in seen]
    assert kiwoom == list(range(1, SESSIONS + 1))
    assert massive == list(range(SESSIONS - 1, -1, -1))
    assert seen[0][2] == str(B.BaselineMode.MIXED_BOOTSTRAP)
    assert seen[-1][2] == str(B.BaselineMode.KIWOOM_NATIVE)
    assert massive[-1] == 0


def test_twenty_forward_sessions_reach_kiwoom_native_from_a_pure_bootstrap_start(database):
    mixed(database, 0, 20)
    first = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert first.mode is B.BaselineMode.MASSIVE_BOOTSTRAP
    day = SESSION
    for _ in range(SESSIONS):
        store(database, day, 100, B.BaselineProvider.KIWOOM)
        database.commit()
        day = CAL.next_trading_day(day)
    item = B.load_baseline(database, "AAA", "ND", day, calendar=CAL)
    assert item.kiwoom_session_count == SESSIONS
    assert item.massive_session_count == 0
    assert item.mode is B.BaselineMode.KIWOOM_NATIVE


def test_covered_count_separates_the_two_halves(database):
    mixed(database, 7, 13)
    combined = B.covered_count(database, "AAA", "ND", SESSION, calendar=CAL)
    kiwoom = B.covered_count(database, "AAA", "ND", SESSION, calendar=CAL,
                             provider=B.BaselineProvider.KIWOOM)
    massive = B.covered_count(database, "AAA", "ND", SESSION, calendar=CAL,
                              provider=B.BaselineProvider.MASSIVE)
    assert (combined, kiwoom, massive) == (20, 7, 13)


# -- the precondition is combined, not Kiwoom (section K) ------------------------------------

def test_nineteen_combined_sessions_is_insufficient_and_says_so(database):
    mixed(database, 1, 18)
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.status is B.BaselineStatus.INSUFFICIENT_COVERED_SESSIONS
    assert item.median_volume is None
    assert item.baseline_session_count == 19


def test_a_run_with_the_bootstrap_half_turned_off_falls_back_to_kiwoom_only(database):
    mixed(database, 3, 17)
    off = B.BaselineRule(bootstrap_enabled=False)
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL, rule=off)
    assert item.status is B.BaselineStatus.INSUFFICIENT_COVERED_SESSIONS
    assert item.kiwoom_session_count == 3
    assert item.massive_session_count == 0
    assert off.declaration()["provider_priority"] == ["KIWOOM"]


def test_an_uncovered_quality_is_skipped_under_either_provider(database):
    days = prior(SESSIONS + 1)
    store(database, days[0], 100, B.BaselineProvider.MASSIVE)
    for day in days[1:]:
        store(database, day, None, B.BaselineProvider.MASSIVE,
              quality=V.SessionQuality.PROVIDER_FAILURE.value)
        store(database, day, 250, B.BaselineProvider.KIWOOM)
    database.commit()
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.status is B.BaselineStatus.AVAILABLE
    assert item.kiwoom_session_count == SESSIONS
    assert item.massive_session_count == 0


def test_a_silent_covered_bootstrap_session_contributes_zero(database):
    days = prior(SESSIONS)
    store(database, days[0], 0, B.BaselineProvider.MASSIVE,
          quality=V.SessionQuality.NO_PREMARKET_BARS.value)
    for day in days[1:]:
        store(database, day, 100, B.BaselineProvider.MASSIVE)
    database.commit()
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.status is B.BaselineStatus.AVAILABLE
    assert item.volumes[0] == 0.0
    assert item.massive_session_count == SESSIONS


# -- section J, Q 13: the forward write itself ----------------------------------------------

def snapshot_for(day: date, *, volume: float, bars: int) -> SNAP.SymbolSnapshot:
    minutes = {240 + index: (10.0, 10.5, 9.5, 10.2, volume / max(bars, 1), 0.0, 0.0)
               for index in range(bars)}
    return SNAP.build("AAA", day, minutes, observed_at=NOW, contiguous=True)


def test_the_forward_observation_is_the_sessions_own_a_quantity(database):
    day = prior(1)[0]
    record = B.forward_observation(snapshot_for(day, volume=4_000.0, bars=4), "ND",
                                  collected_at=NOW)
    assert record.source == B.SOURCE
    assert record.collector_version == B.COLLECTOR_VERSION
    assert record.quality_status is V.SessionQuality.COMPLETE
    assert record.quality_reason == B.FORWARD_QUALITY_REASON
    assert record.premarket_volume == 4_000
    assert record.regular_bar_count == 0


def test_a_morning_with_no_premarket_print_is_a_covered_zero_not_a_gap(database):
    day = prior(1)[0]
    record = B.forward_observation(snapshot_for(day, volume=0.0, bars=0), "ND", collected_at=NOW)
    assert record.quality_status is V.SessionQuality.NO_PREMARKET_BARS
    assert record.premarket_volume == 0
    assert record.bar_count == 0


def test_recording_the_same_morning_twice_writes_one_row(database):
    day = prior(1)[0]
    snapshots = {"AAA": snapshot_for(day, volume=4_000.0, bars=4)}
    first = B.record_forward_observations(database, snapshots, {"AAA": "ND"}, collected_at=NOW)
    second = B.record_forward_observations(database, snapshots, {"AAA": "ND"}, collected_at=NOW)
    assert first["written"] == 1
    assert second["written"] == 1                        # the same row, updated in place
    rows = list(database.scalars(select(PremarketVolumeSession).where(
        PremarketVolumeSession.trading_date == day)))
    assert len(rows) == 1
    assert B.covered_count(database, "AAA", "ND", CAL.next_trading_day(day), calendar=CAL,
                           provider=B.BaselineProvider.KIWOOM) == 1


def test_a_forward_row_becomes_a_baseline_candidate_from_the_next_session_on(database):
    mixed(database, 0, 20)
    snapshots = {"AAA": snapshot_for(SESSION, volume=4_000.0, bars=4)}
    B.record_forward_observations(database, snapshots, {"AAA": "ND"}, collected_at=NOW)
    same = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert same.kiwoom_session_count == 0               # its own session, excluded
    following = B.load_baseline(database, "AAA", "ND", CAL.next_trading_day(SESSION),
                                calendar=CAL)
    assert following.kiwoom_session_count == 1
    assert following.mode is B.BaselineMode.MIXED_BOOTSTRAP


def test_an_unmapped_exchange_is_reported_rather_than_stored_under_a_guess(database):
    snapshots = {"AAA": snapshot_for(SESSION, volume=1_000.0, bars=2)}
    body = B.record_forward_observations(database, snapshots, {}, collected_at=NOW)
    assert body["skipped_unmapped_exchange"] == ["AAA"]
    assert body["written"] == 0


# -- the run-level mix -----------------------------------------------------------------------

def base(kiwoom: int, massive: int, *, available: bool = True) -> B.AMoverBaseline:
    days = tuple(prior(kiwoom + massive))
    providers = tuple([B.BaselineProvider.MASSIVE] * massive
                      + [B.BaselineProvider.KIWOOM] * kiwoom)
    status = (B.BaselineStatus.AVAILABLE if available
              else B.BaselineStatus.INSUFFICIENT_COVERED_SESSIONS)
    return B.AMoverBaseline(status, "AAA", "ND", SESSION, days, (), (100.0,) * len(days),
                            100.0 if available else None, B.BaselineRule(), providers)


def test_the_run_level_mode_is_kiwoom_native_only_when_no_symbol_used_the_tape():
    body = B.provider_mix({"A": base(20, 0), "B": base(20, 0)})
    assert body["baseline_mode"] == str(B.BaselineMode.KIWOOM_NATIVE)
    assert body["massive_session_count"] == 0
    assert body["sessions_until_kiwoom_native"] == 0


def test_one_symbol_on_the_tape_makes_the_whole_run_mixed():
    body = B.provider_mix({"A": base(20, 0), "B": base(19, 1)})
    assert body["baseline_mode"] == str(B.BaselineMode.MIXED_BOOTSTRAP)
    assert body["total_kiwoom_session_observations"] == 39
    assert body["total_massive_session_observations"] == 1
    assert body["per_symbol_kiwoom_sessions"]["min"] == 19
    assert body["per_symbol_kiwoom_sessions"]["max"] == 20
    assert body["sessions_until_kiwoom_native"] == 1


def test_the_run_level_counts_are_sessions_out_of_twenty_not_a_sum():
    """Six of twenty over sixty symbols must not print as 360: the units are section I's."""
    body = B.provider_mix({f"S{index}": base(6, 14) for index in range(60)})
    assert body["kiwoom_session_count"] == 6
    assert body["massive_session_count"] == 14
    assert body["baseline_session_count"] == SESSIONS
    assert body["total_kiwoom_session_observations"] == 360


def test_a_run_where_nobody_has_a_kiwoom_session_is_massive_bootstrap():
    body = B.provider_mix({"A": base(0, 20), "B": base(0, 20)})
    assert body["baseline_mode"] == str(B.BaselineMode.MASSIVE_BOOTSTRAP)
    assert body["kiwoom_session_count"] == 0
    assert body["massive_session_count"] == SESSIONS
    assert body["sessions_until_kiwoom_native"] == 20


def test_an_insufficient_symbol_is_counted_but_never_averaged_into_the_mix():
    body = B.provider_mix({"A": base(20, 0), "B": base(3, 0, available=False)})
    assert body["available"] == 1
    assert body["insufficient"] == 1
    assert body["kiwoom_session_count"] == 20            # the short symbol contributes nothing
    assert body["total_kiwoom_session_observations"] == 20
    assert body["baseline_mode"] == str(B.BaselineMode.KIWOOM_NATIVE)
    assert body["per_symbol_modes"]["KIWOOM_NATIVE"] == 2


# -- the bootstrap reader, over a real SessionCache ------------------------------------------

def write_tape(directory: Path, symbols: list[str], sessions: list[date], *,
               bars_per_pair: dict[tuple[str, date], list[tuple[int, float]]],
               uncovered: list[tuple[str, date]] | None = None) -> Path:
    """A minimal but real B-E0 minute cache: the format ``SessionCache`` actually reads.

    ``bars_per_pair`` is ``(minute of day, volume)`` per covered pair; a pair in ``uncovered``
    gets no ledger entry at all, which is how the tape says it does not know rather than zero.
    """
    directory.mkdir(parents=True, exist_ok=True)
    skip = set(uncovered or ())
    stamps: list[int] = []
    values: list[list[float]] = []
    index = {name: [] for name in ("symbol", "session", "start", "end", "rows", "regular_rows",
                                   "volume", "regular_volume", "first_ts", "last_ts")}
    for symbol_id, symbol in enumerate(symbols):
        for session_id, day in enumerate(sessions):
            if (symbol, day) in skip:
                continue
            bars = bars_per_pair.get((symbol, day), [])
            start = len(stamps)
            for minute, volume in bars:
                moment = datetime(day.year, day.month, day.day, minute // 60, minute % 60,
                                  tzinfo=ET)
                stamps.append(int(moment.timestamp() * 1000))
                values.append([10.0, 10.5, 9.5, 10.2, volume, 10.0, 1.0])
            index["symbol"].append(symbol_id)
            index["session"].append(session_id)
            index["start"].append(start)
            index["end"].append(len(stamps))
            index["rows"].append(len(bars))
            index["regular_rows"].append(0)
            index["volume"].append(float(sum(volume for _, volume in bars)))
            index["regular_volume"].append(0.0)
            index["first_ts"].append(stamps[start] if bars else 0)
            index["last_ts"].append(stamps[-1] if bars else 0)
    np.asarray(stamps, dtype="<i8").tofile(directory / "t.bin")
    (np.asarray(values, dtype="<f8") if values
     else np.zeros((0, len(VALUE_COLUMNS)), dtype="<f8")).tofile(directory / "v.bin")
    np.savez(directory / "coverage_index.npz",
             **{name: np.asarray(items, dtype=np.int64 if name in (
                 "symbol", "session", "start", "end", "rows", "regular_rows", "first_ts",
                 "last_ts") else np.float64) for name, items in index.items()})
    (directory / "cache_meta.json").write_text(json.dumps({
        "cache_version": CACHE_VERSION, "cache_digest": "fixture-tape",
        "source_dataset_digest": "fixture", "rows": len(stamps),
        "symbols": symbols, "sessions": [day.isoformat() for day in sessions],
        "value_columns": list(VALUE_COLUMNS), "uncovered_sessions": [],
        "overlap_sessions": [], "sanitation": {},
        "index_sha256": "0" * 64, "t_sha256": "0" * 64, "v_sha256": "0" * 64,
        "universe_sha256": "0" * 64,
    }), encoding="utf-8")
    return directory


@pytest.fixture
def tape(tmp_path: Path):
    days = prior(SESSIONS)
    bars = {}
    for day in days:
        # one minute inside the scan window and one strictly after the cut
        bars[("AAA", day)] = [(300, 1_000.0), (A_CUT_MINUTE + 5, 9_999.0)]
        bars[("BBB", day)] = []                          # covered, nothing printed: a zero
    directory = write_tape(tmp_path / "cache" / "fixture", ["AAA", "BBB", "CCC"], days,
                           bars_per_pair=bars, uncovered=[("CCC", day) for day in days])
    return BOOT.select_tape(tmp_path, cache_directory=directory), days


def test_the_bootstrap_reads_a_share_volume_that_stops_at_the_cut(tape):
    selected, days = tape
    observations = BOOT.observe(selected, ["AAA"], days, config=K.final_config())
    for day in days:
        item = observations[("AAA", day)]
        assert item.covered
        assert item.volume == pytest.approx(1_000.0)     # the post-cut minute is not summed
        assert item.bar_count == 1


def test_a_covered_pair_with_no_premarket_print_is_a_zero(tape):
    selected, days = tape
    item = BOOT.observe(selected, ["BBB"], days, config=K.final_config())[("BBB", days[0])]
    assert item.covered
    assert item.volume == 0.0
    assert item.bar_count == 0


def test_a_pair_the_tape_does_not_cover_is_named_never_zeroed(tape):
    selected, days = tape
    item = BOOT.observe(selected, ["CCC"], days, config=K.final_config())[("CCC", days[0])]
    assert not item.covered
    assert item.coverage is BOOT.Coverage.SYMBOL_SESSION_NOT_COVERED
    assert item.volume is None


def test_a_symbol_outside_the_tape_is_named_never_zeroed(tape):
    selected, days = tape
    item = BOOT.observe(selected, ["ZZZ"], days, config=K.final_config())[("ZZZ", days[0])]
    assert item.coverage is BOOT.Coverage.SYMBOL_NOT_IN_TAPE
    assert item.volume is None


def test_a_session_outside_the_tape_is_named_never_zeroed(tape):
    selected, days = tape
    absent = CAL.previous_trading_day(days[0])
    item = BOOT.observe(selected, ["AAA"], [absent], config=K.final_config())[("AAA", absent)]
    assert item.coverage is BOOT.Coverage.SESSION_NOT_IN_TAPE
    assert item.volume is None


def test_the_bootstrap_refuses_a_cut_that_is_not_as(tape):
    """A different premarket cut would be a different quantity under A's own identity."""
    selected, days = tape
    other = dataclasses.replace(MoverScannerConfig(), scan_cut_minute=540)   # 09:00 ET
    with pytest.raises(ValueError, match="A's declared cut"):
        BOOT.observe(selected, ["AAA"], days, config=other)


def test_materializing_the_tape_makes_a_pure_bootstrap_denominator(database, tape):
    selected, _ = tape
    report = BOOT.materialize(database, Path("/does/not/matter"), [("AAA", "ND")], SESSION,
                              calendar=CAL, config=K.final_config(), tape=selected,
                              clock=lambda: NOW)
    body = report.declaration()
    assert body["covered_pairs"] == SESSIONS
    assert body["inserted"] == SESSIONS
    assert body["network_requests"] == 0
    item = B.load_baseline(database, "AAA", "ND", SESSION, calendar=CAL)
    assert item.status is B.BaselineStatus.AVAILABLE
    assert item.mode is B.BaselineMode.MASSIVE_BOOTSTRAP
    assert item.massive_session_count == SESSIONS
    assert item.median_volume == pytest.approx(1_000.0)


def test_materializing_twice_writes_the_same_rows_once(database, tape):
    selected, _ = tape
    first = BOOT.materialize(database, Path("/x"), [("AAA", "ND")], SESSION, calendar=CAL,
                             config=K.final_config(), tape=selected, clock=lambda: NOW)
    second = BOOT.materialize(database, Path("/x"), [("AAA", "ND")], SESSION, calendar=CAL,
                              config=K.final_config(), tape=selected, clock=lambda: NOW)
    assert first.inserted == SESSIONS and first.updated == 0
    assert second.inserted == 0 and second.updated == SESSIONS
    rows = list(database.scalars(select(PremarketVolumeSession).where(
        PremarketVolumeSession.source == B.BOOTSTRAP_SOURCE)))
    assert len(rows) == SESSIONS


def test_an_uncovered_symbol_gets_no_rows_and_stays_insufficient(database, tape):
    selected, _ = tape
    report = BOOT.materialize(database, Path("/x"), [("CCC", "ND")], SESSION, calendar=CAL,
                              config=K.final_config(), tape=selected, clock=lambda: NOW)
    assert report.covered_pairs == 0
    assert report.written == 0
    assert report.uncovered[str(BOOT.Coverage.SYMBOL_SESSION_NOT_COVERED)] >= SESSIONS
    item = B.load_baseline(database, "CCC", "ND", SESSION, calendar=CAL)
    assert item.status is B.BaselineStatus.INSUFFICIENT_COVERED_SESSIONS
    assert item.median_volume is None


def test_coverage_reports_who_can_be_bootstrapped_before_anything_is_written(tape):
    selected, _ = tape
    body = BOOT.coverage_of(Path("/x"), [("AAA", "ND"), ("CCC", "ND")], SESSION, calendar=CAL,
                            config=K.final_config(), tape=selected)
    assert body["symbols_with_enough_bootstrap_sessions"] == 1
    assert body["symbols_short"] == 1
    assert body["covered_sessions_per_symbol"]["AAA"] == SESSIONS
    assert body["covered_sessions_per_symbol"]["CCC"] == 0
    assert body["network_requests"] == 0


def test_the_bootstrap_identity_is_not_the_kiwoom_identity():
    rule = B.BaselineRule()
    assert rule.bootstrap_source != rule.source
    assert rule.bootstrap_collector_version != rule.collector_version
    assert BOOT.declaration()["uncovered_pair_becomes_zero"] is False


def test_the_live_contract_declares_the_provider_mix_rather_than_one_provider():
    live = LC.current()
    assert live.hybrid_sources["premarket_rvol_baseline"] == LC.BASELINE_PROVIDER_CONTRACT
    assert "KIWOOM" in live.baseline_provider_contract
    assert "MASSIVE" in live.baseline_provider_contract
    assert live.hybrid_sources["same_day_premarket_bars"] == "KIWOOM"
    assert live.declaration()["baseline_provider_contract"] == LC.BASELINE_PROVIDER_CONTRACT


# -- the batch read and the per-symbol read are one walk ------------------------------------

def test_the_batch_read_and_the_per_symbol_read_agree_exactly(database):
    """Two readers over the same rows must not be able to produce two denominators."""
    for index, symbol in enumerate(["AAA", "BBB", "CCC"]):
        days = prior(SESSIONS + index)
        for position, day in enumerate(days):
            provider = (B.BaselineProvider.KIWOOM if position >= len(days) - 1 - index
                        else B.BaselineProvider.MASSIVE)
            store(database, day, 100 + position, provider, symbol=symbol)
    database.commit()
    candidates = [("AAA", "ND"), ("BBB", "ND"), ("CCC", "ND")]
    batch = B.load_baselines(database, candidates, SESSION, calendar=CAL)
    for symbol, exchange in candidates:
        one = B.load_baseline(database, symbol, exchange, SESSION, calendar=CAL)
        assert batch[symbol].declaration() == one.declaration(), symbol


def test_the_batch_read_keeps_symbols_apart(database):
    """One symbol's rows must never reach another symbol's median."""
    for day in prior(SESSIONS):
        store(database, day, 100, B.BaselineProvider.MASSIVE, symbol="AAA")
    for day in prior(5):
        store(database, day, 9_999, B.BaselineProvider.KIWOOM, symbol="BBB")
    database.commit()
    batch = B.load_baselines(database, [("AAA", "ND"), ("BBB", "ND")], SESSION, calendar=CAL)
    assert batch["AAA"].status is B.BaselineStatus.AVAILABLE
    assert batch["AAA"].median_volume == pytest.approx(100.0)
    assert batch["BBB"].status is B.BaselineStatus.INSUFFICIENT_COVERED_SESSIONS
    assert batch["BBB"].baseline_session_count == 5


def test_the_batch_read_keeps_exchanges_apart(database):
    """The exchange is part of the row identity, so two listings are two denominators."""
    for day in prior(SESSIONS):
        store(database, day, 100, B.BaselineProvider.MASSIVE, symbol="AAA", exchange="ND")
        store(database, day, 700, B.BaselineProvider.MASSIVE, symbol="AAA", exchange="NY")
    database.commit()
    nd = B.load_baselines(database, [("AAA", "ND")], SESSION, calendar=CAL)["AAA"]
    ny = B.load_baselines(database, [("AAA", "NY")], SESSION, calendar=CAL)["AAA"]
    assert nd.median_volume == pytest.approx(100.0)
    assert ny.median_volume == pytest.approx(700.0)


def test_the_batch_read_chunks_without_losing_a_symbol(database):
    """More symbols than one ``IN`` clause holds must still all come back."""
    count = B.SYMBOL_CHUNK + 7
    symbols = [f"S{index:04d}" for index in range(count)]
    for day in prior(SESSIONS):
        for symbol in symbols:
            store(database, day, 100, B.BaselineProvider.MASSIVE, symbol=symbol)
    database.commit()
    batch = B.load_baselines(database, [(symbol, "ND") for symbol in symbols], SESSION,
                             calendar=CAL)
    assert len(batch) == count
    assert all(item.status is B.BaselineStatus.AVAILABLE for item in batch.values())
    assert B.provider_mix(batch)["available"] == count


# -- A off: nothing is written to the shared table -------------------------------------------

def test_with_the_flag_off_no_forward_row_is_written(database, tmp_path):
    """The forward write is a new side effect on a shared table, so its off-path is a test.

    ``attach`` returning None is what guarantees it: with no handle there is no ``run_cut``,
    so neither the baseline read nor the forward write exists for E's worker to perform.
    """
    from app.strategy_a_mover_live import integration as INT
    handle = INT.attach(session=SESSION, repo=tmp_path, caches={}, shard_minute=(),
                        shard_tick=(), lane_minute=None, lane_tick=None, now=lambda: NOW,
                        exchanges={}, environ={},
                        session_factory=lambda: database)
    assert handle is None
    assert list(database.scalars(select(PremarketVolumeSession))) == []


def test_the_forward_write_is_skipped_when_there_is_no_database(tmp_path):
    """No database means no row, not a row written somewhere else."""
    from app.strategy_a_mover_live import integration as INT
    handle = INT.SharedCollectorIntegration(
        session=SESSION, repo=tmp_path, caches={}, shard_minute=(), shard_tick=(),
        lane_minute=None, lane_tick=None, now=lambda: NOW,
        union=None, exchanges={}, log=lambda _: None, session_factory=None, calendar=CAL)
    body = handle._record_forward({"AAA": snapshot_for(SESSION, volume=1.0, bars=1)}, NOW)
    assert body == {"status": "NOT_RECORDED_NO_DATABASE", "written": 0}
