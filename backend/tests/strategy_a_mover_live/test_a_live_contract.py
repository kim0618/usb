"""A-MOVER-LIVE-V1 versioning, the shared schedule, raw persistence and the 09:15 snapshot.

Section S tests covered here: 3 (the cut excludes 09:15+), 4 (share volume exact), 5 (dollar
volume exact), 6 (high/low exact), 22 (the scanner version is persisted - the declaration side),
25 (an identical duplicate dedupes) and 26 (a conflicting duplicate fails).
"""

from dataclasses import replace
from datetime import date, datetime, time, timezone

import pytest

from app.backtest.mover_scanner_v1 import contract as K
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live import raw_store as RAW
from app.strategy_a_mover_live import schedule as SCHED
from app.strategy_a_mover_live import snapshot as SNAP
from tests.strategy_a_mover_live.fixtures import ET, SESSION

OBSERVED = datetime(2026, 9, 15, 13, 15, tzinfo=timezone.utc)


# -- versioning ------------------------------------------------------------------------------

def test_live_version_is_separate_from_the_research_parent():
    live = LC.current()
    assert live.scanner_version == "A-MOVER-LIVE-V1"
    assert live.research_parent_version == K.CONTRACT_VERSION == "a-mover-scanner-v1.2"
    assert live.research_parent_checksum == K.HANDOFF_CHECKSUM
    # the live checksum is computed over the live declaration, never borrowed
    assert live.scanner_checksum != live.research_parent_checksum
    assert live.scanner_checksum != live.research_parent_discovery_checksum


def test_live_metadata_names_every_required_field():
    body = LC.current().metadata()
    for name in ("research_parent_version", "research_parent_checksum", "live_provider",
                 "collector_version", "feature_contract_version", "baseline_version",
                 "scanner_version", "scanner_checksum"):
        assert body[name], name


def test_live_run_score_version_is_distinct_from_legacy_and_research():
    from app.services.candidate_source import LEGACY_SCORE_VERSION
    assert LC.RUN_SCORE_VERSION not in (LEGACY_SCORE_VERSION, K.RUN_SCORE_VERSION)


def test_hybrid_sources_are_declared_not_hidden():
    sources = LC.current().hybrid_sources
    assert sources["same_day_premarket_bars"] == "KIWOOM"
    assert sources["previous_regular_close"].startswith("MASSIVE")
    assert sources["daily_volume_baselines"].startswith("MASSIVE")
    assert sources["reference_universe"].startswith("MASSIVE")
    assert sources["split_calendar"].startswith("MASSIVE")


def test_verify_reproduces_the_frozen_research_checksums():
    body = LC.verify()
    assert body["research_parent"]["scanner_checksum"] == K.HANDOFF_CHECKSUM
    assert body["research_parent"]["pool_size"] == 35
    assert body["live"]["pool_size"] == 35
    assert body["live"]["top_count"] == 8


def test_verify_refuses_a_moved_research_parent(monkeypatch):
    monkeypatch.setattr(LC.K, "verify",
                        lambda: (_ for _ in ()).throw(K.ContractDrift("threshold moved")))
    with pytest.raises(LC.LiveContractDrift):
        LC.verify()


def test_parity_is_never_a_production_gate():
    assert LC.current().parity_is_a_production_gate is False


# -- the switch ------------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [("", False), ("false", False), ("0", False),
                                            ("no", False), ("true", True), ("1", True),
                                            ("YES", True), ("On", True)])
def test_the_flag_is_off_unless_explicitly_on(value, expected):
    assert CFG.enabled({CFG.ENV_FLAG: value}) is expected


def test_the_flag_is_off_when_absent():
    assert CFG.enabled({}) is False


def test_every_refusal_blocks_a_gpt_call():
    assert set(CFG.NO_GPT_CALL) == set(CFG.Refusal)


# -- the shared schedule ---------------------------------------------------------------------

def test_a_cut_is_the_scanners_own_declared_minute():
    assert SCHED.A_CUT_MINUTE == MoverScannerConfig().scan_cut_minute == 555
    assert SCHED.A_CUT == time(9, 15)
    assert SCHED.A_CUTOFF_LAST_MINUTE == 554


def test_a_window_is_a_strict_prefix_of_e_window():
    schedule = SCHED.SharedSchedule(SESSION)
    schedule.validate()
    assert schedule.a_window_is_prefix_of_e()
    assert SCHED.A_CUTOFF_LAST_MINUTE < SCHED.E_CUTOFF_LAST_MINUTE


@pytest.mark.parametrize("session", [date(2026, 3, 9), date(2026, 7, 1), date(2026, 11, 2)])
def test_the_schedule_is_dst_safe(session):
    """Every instant is built on the session's own date, so the wall clock never shifts."""
    schedule = SCHED.SharedSchedule(session)
    schedule.validate()
    assert schedule.a_cut.time() == time(9, 15)
    assert schedule.e_cut.time() == time(9, 25)
    assert schedule.regular_open.time() == time(9, 30)
    assert str(schedule.a_cut.tzinfo) == "America/New_York"


def test_kiwoom_calls_stop_before_strategy_a_first_call():
    schedule = SCHED.SharedSchedule(SESSION)
    assert schedule.kiwoom_deadline < schedule.regular_open


def test_a_pass_order_puts_the_e_tick_shard_last_in_its_own_order():
    order = SCHED.a_pass_order(["AAA", "BBB", "CCC"], ["BBB", "DDD"])
    assert order == ("AAA", "CCC", "BBB", "DDD")
    assert order[-2:] == ("BBB", "DDD")


def test_staleness_is_preserved_by_the_ordering_when_the_pass_ends_late():
    schedule = SCHED.SharedSchedule(SESSION)
    bound = SCHED.StalenessBound(schedule.a_cut.replace(minute=23, second=32),
                                 schedule.e_cut, 133.1, 260.0, 133.1)
    assert bound.preserves_measured_cost
    assert bound.mechanism == "A_PASS_ORDERED_TICK_SHARD_LAST"
    assert not bound.e_own_refresh_fits


def test_staleness_is_preserved_by_e_own_refresh_when_the_pass_ends_early():
    schedule = SCHED.SharedSchedule(SESSION)
    bound = SCHED.StalenessBound(schedule.a_cut.replace(minute=18), schedule.e_cut,
                                 133.1, 260.0, 133.1)
    assert bound.preserves_measured_cost
    assert bound.mechanism == "E_OWN_REFRESH_STILL_FITS"
    assert not bound.ordering_bounds_staleness     # the ordering alone would not cover this end


def test_a_pass_ending_after_e_cut_has_no_mechanism():
    schedule = SCHED.SharedSchedule(SESSION)
    bound = SCHED.StalenessBound(schedule.e_cut.replace(minute=26), schedule.e_cut,
                                 133.1, 260.0, 133.1)
    assert not bound.preserves_measured_cost
    assert bound.mechanism == "NONE"


# -- the 09:15 cut (section S test 3) --------------------------------------------------------

def test_the_cut_excludes_bars_at_or_after_0915():
    minutes = {240: [10, 10, 10, 10, 100], 554: [11, 11, 11, 11, 200],
               555: [99, 99, 99, 99, 9999], 560: [98, 98, 98, 98, 8888]}
    snapshot = SNAP.build("AAA", SESSION, minutes, observed_at=OBSERVED)
    assert snapshot.last_complete_minute == 554
    assert snapshot.raw["pm_bar_count"] == 2
    assert snapshot.raw["pm_share_volume"] == 300.0        # the 09:15 and 09:20 bars are absent
    assert snapshot.derived["pm_last_price"] == 11.0


def test_a_bar_exactly_at_the_cut_is_never_read():
    only_cut = SNAP.build("AAA", SESSION, {555: [1, 1, 1, 1, 1]}, observed_at=OBSERVED)
    assert only_cut.raw["pm_bar_count"] == 0
    assert not only_cut.has_premarket


# -- exact aggregation (section S tests 4, 5, 6) ---------------------------------------------

def test_share_volume_dollar_volume_and_extremes_are_exact():
    minutes = {
        240: [10.0, 10.5, 9.75, 10.25, 1_000],
        300: [10.25, 12.00, 10.00, 11.00, 2_500],
        554: [11.00, 11.10, 8.50, 9.00, 3_333],
    }
    snapshot = SNAP.build("AAA", SESSION, minutes, observed_at=OBSERVED)
    assert snapshot.raw["pm_share_volume"] == 1_000 + 2_500 + 3_333
    expected_dollars = 10.25 * 1_000 + 11.00 * 2_500 + 9.00 * 3_333
    assert snapshot.raw["pm_dollar_volume"] == pytest.approx(expected_dollars, rel=0, abs=1e-9)
    assert snapshot.raw["pm_high"] == 12.00
    assert snapshot.raw["pm_low"] == 8.50
    # the derived layer is the research function over the same facts
    assert snapshot.derived["pm_volume"] == snapshot.raw["pm_share_volume"]
    assert snapshot.derived["pm_dollar_volume"] == snapshot.raw["pm_dollar_volume"]
    assert snapshot.derived["pm_high"] == snapshot.raw["pm_high"]
    assert snapshot.derived["pm_low"] == snapshot.raw["pm_low"]


def test_the_momentum_reference_is_the_0815_band_not_0900():
    """``momentum_late_window_minutes`` is 60, so the reference is the last close before 08:15."""
    minutes = {240: [1, 1, 1, 1.5, 10], 494: [2, 2, 2, 2.5, 10], 500: [3, 3, 3, 3.5, 10],
               554: [4, 4, 4, 4.5, 10]}
    snapshot = SNAP.build("AAA", SESSION, minutes, observed_at=OBSERVED)
    assert snapshot.late_reference_minute == 494
    assert snapshot.derived["pm_late_reference_price"] == 2.5


def test_the_gate_window_is_named_unavailable_at_the_cut():
    snapshot = SNAP.build("AAA", SESSION, {240: [1, 1, 1, 1, 1]}, observed_at=OBSERVED)
    assert set(snapshot.unavailable) == {"gate_bars", "gate_volume", "gate_last_price"}
    assert snapshot.row()["unavailable_at_cut"]


def test_the_snapshot_refuses_a_cut_other_than_the_declared_one():
    with pytest.raises(ValueError):
        SNAP.build("AAA", SESSION, {240: [1, 1, 1, 1, 1]}, cut_minute=570)


def test_the_snapshot_carries_its_provenance():
    snapshot = SNAP.build("AAA", SESSION, {240: [1, 1, 1, 1, 1]}, observed_at=OBSERVED)
    assert snapshot.provider == "KIWOOM"
    assert snapshot.collector_version == LC.COLLECTOR_VERSION
    assert snapshot.feature_contract_version == LC.FEATURE_CONTRACT_VERSION
    assert snapshot.cut_time_label == "09:15 ET"


# -- raw persistence (section S tests 25, 26) ------------------------------------------------

def _bar(minute: int, volume: float = 100.0) -> RAW.RawBar:
    stamp = datetime.combine(SESSION, time(minute // 60, minute % 60), tzinfo=ET)
    return RAW.RawBar("AAA", SESSION, stamp, 1.0, 2.0, 0.5, 1.5, volume, observed_at=OBSERVED)


def test_identical_duplicate_minutes_are_deduped():
    raw = RAW.RawSession(SESSION)
    assert raw.add(_bar(240)) == "ACCEPTED"
    assert raw.add(_bar(240)) == "DEDUPED"
    assert len(raw) == 1
    assert raw.deduped == 1
    assert raw.quarantine == ()


def test_conflicting_duplicate_minutes_are_quarantined_with_both_readings():
    raw = RAW.RawSession(SESSION)
    raw.add(_bar(240, volume=100.0))
    assert raw.add(_bar(240, volume=999.0)) == "QUARANTINED"
    assert len(raw) == 1                                  # the first reading is not overwritten
    assert len(raw.quarantine) == 1
    conflict = raw.quarantine[0]
    assert conflict.stored[-1] == 100.0 and conflict.offered[-1] == 999.0
    assert raw.payload()["quarantined"] == 1


def test_strict_mode_raises_on_a_conflicting_minute():
    raw = RAW.RawSession(SESSION, strict=True)
    raw.add(_bar(240, volume=100.0))
    with pytest.raises(RAW.RawConflict):
        raw.add(_bar(240, volume=1.0))


def test_a_bar_from_another_session_is_refused():
    raw = RAW.RawSession(SESSION)
    other = replace(_bar(240), session_date=date(2026, 9, 16))
    with pytest.raises(ValueError):
        raw.add(other)


def test_raw_rows_carry_every_declared_field_and_round_trip(tmp_path):
    raw = RAW.RawSession(SESSION)
    raw.extend(RAW.bars_from_cache("AAA", SESSION, {240: [1, 2, 0.5, 1.5, 10],
                                                    554: [2, 3, 1.0, 2.5, 20],
                                                    555: [9, 9, 9, 9, 9]},
                                   OBSERVED, cut_minute=555))
    assert len(raw) == 2                                   # the cut is enforced at persistence too
    path = RAW.write_session(tmp_path, raw)
    body = RAW.read_session(tmp_path, SESSION)
    assert path.name == "2026-09-15.json.gz"
    assert body["format"] == RAW.RAW_FORMAT
    assert set(body["rows"][0]) == set(RAW.FIELDS)
    assert body["rows"][0]["provider"] == "KIWOOM"
    assert body["rows"][0]["collector_version"] == LC.COLLECTOR_VERSION
    assert body["digest"]
