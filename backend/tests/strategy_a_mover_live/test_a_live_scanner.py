"""The live scan: panel assembly, the fixed pool and output, and the config-driven mask.

Section S tests covered here: 10 (TOP35 is fixed), 11 (the output maximum of 8 is fixed) and
12 (actionability follows ``StrategyConfig``, it is not copied).
"""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.backtest.mover_scanner_v1 import contract as K
from app.backtest.mover_scanner_v1.scan import Rejection, scan_session
from app.market.calendar import MarketCalendar
from app.strategy.config import StrategyConfig
from app.strategy_a_mover_live import baseline as B
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live import features as FEAT
from app.strategy_a_mover_live import scanner as SCAN
from app.strategy_a_mover_live import snapshot as SNAP
from app.strategy_a_mover_live import universe as UNI
from app.services.mover_scanner_source import MoverDataUnavailableError, MoverScanInput
from tests.strategy_a_mover_live.fixtures import SESSION, daily_panel

CAL = MarketCalendar("America/New_York")
OBSERVED = datetime(2026, 9, 15, 13, 15, tzinfo=timezone.utc)
ON = {CFG.ENV_FLAG: "true"}


def prior(count: int, before: date = SESSION) -> list[date]:
    out, day = [], before
    for _ in range(count):
        day = CAL.previous_trading_day(day)
        out.append(day)
    return list(reversed(out))


def baseline_for(symbol: str, median: float,
                 provider: B.BaselineProvider = B.BaselineProvider.KIWOOM) -> B.AMoverBaseline:
    days = tuple(prior(20))
    return B.AMoverBaseline(B.BaselineStatus.AVAILABLE, symbol, "ND", SESSION, days, (),
                            tuple([median] * 20), median, B.BaselineRule(),
                            tuple([provider] * len(days)))


def snapshot_for(symbol: str, *, gap: float, volume: float, price: float = 10.0,
                 bars: int = 40) -> SNAP.SymbolSnapshot:
    """A premarket that rises steadily to ``price * (1 + gap)`` by the cut."""
    last = price * (1 + gap)
    step = (last - price) / max(bars - 1, 1)
    minutes = {}
    for index in range(bars):
        minute = 240 + index * 7
        level = price + step * index
        minutes[minute] = [level, level + 0.05, level - 0.05, level + 0.01,
                           volume / bars]
    minutes[554] = [last - 0.01, last + 0.02, last - 0.03, last, volume / bars]
    return SNAP.build(symbol, SESSION, minutes, observed_at=OBSERVED)


def panel_input(count: int = 60, *, strategy: StrategyConfig | None = None,
                gaps=None) -> MoverScanInput:
    symbols = [f"S{index:03d}" for index in range(count)]
    gaps = gaps or {symbol: 0.03 + 0.001 * index for index, symbol in enumerate(symbols)}
    snapshots, baselines = {}, {}
    for index, symbol in enumerate(symbols):
        snapshots[symbol] = snapshot_for(symbol, gap=gaps[symbol],
                                         volume=100_000 + 5_000 * index)
        baselines[symbol] = baseline_for(symbol, 50_000.0)
    premarket = FEAT.build_premarket_panel(SESSION, tuple(symbols), snapshots, baselines,
                                           calendar=CAL)
    return MoverScanInput(session=SESSION, symbols=tuple(symbols), premarket=premarket,
                          daily=daily_panel(symbols, SESSION), source_name=FEAT.SOURCE_NAME,
                          live=True, universe_as_of=date(2026, 7, 1),
                          universe_checksum="ref-checksum",
                          premarket_digest=premarket.cache_digest, baseline_sessions=20,
                          baseline_provider_mix=B.provider_mix(baselines))


# -- panel assembly --------------------------------------------------------------------------

def test_the_panel_puts_the_scan_session_last_and_the_baseline_before_it():
    data = panel_input(5)
    panel = data.premarket
    assert panel.sessions[-1] == SESSION
    assert panel.cut_minute == 555
    column = panel.session_index[SESSION]
    assert column == len(panel.sessions) - 1
    median, used = panel.rvol_baseline(0, column, 20)
    assert used == 20 and median == 50_000.0


def test_the_scan_sessions_own_volume_cannot_enter_its_own_denominator():
    data = panel_input(3)
    panel = data.premarket
    column = panel.session_index[SESSION]
    live_volume = panel.field("pm_volume", 0, column)
    median, _ = panel.rvol_baseline(0, column, 20)
    assert live_volume > 0
    assert median == 50_000.0                 # unchanged by the live column


def test_a_symbol_without_a_baseline_is_rejected_by_name():
    symbols = ("AAA",)
    snapshots = {"AAA": snapshot_for("AAA", gap=0.05, volume=500_000)}
    panel = FEAT.build_premarket_panel(SESSION, symbols, snapshots, {}, calendar=CAL)
    result = scan_session(SESSION, symbols, panel, daily_panel(list(symbols), SESSION),
                          K.final_config(), StrategyConfig())
    assert result.eligible == 0
    assert result.rejections[Rejection.NO_PREMARKET_RVOL_BASELINE] == 1


# -- the fixed pool and output (section S tests 10, 11) --------------------------------------

def test_the_discovery_pool_is_fixed_at_thirty_five():
    live = SCAN.run(panel_input(60), observed_at=OBSERVED, environ=ON)
    assert K.POOL_SIZE == 35
    assert len(live.scan.pool) == 35
    assert live.selection.discovery_pool_size == 35
    assert live.contract.pool_size == 35


def test_the_output_is_at_most_eight_and_is_never_padded():
    live = SCAN.run(panel_input(60), observed_at=OBSERVED, environ=ON)
    assert live.candidate_count == 8
    assert [item.rank for item in live.handoff] == list(range(1, 9))
    small = SCAN.run(panel_input(4), observed_at=OBSERVED, environ=ON)
    assert small.candidate_count == 4            # a maximum, never a quota
    assert [item.rank for item in small.handoff] == [1, 2, 3, 4]


def test_a_smaller_universe_produces_a_smaller_pool_not_a_padded_one():
    live = SCAN.run(panel_input(12), observed_at=OBSERVED, environ=ON)
    assert live.selection.discovery_pool_size == 12
    assert live.candidate_count == 8


# -- actionability follows the deployed config (section S test 12) ---------------------------

def test_the_mask_reads_the_deployed_gap_band():
    gaps = {f"S{index:03d}": value for index, value in
            enumerate([0.005, 0.01, 0.03, 0.08, 0.14, 0.18, 0.30, 0.45] * 5)}
    data = panel_input(40, gaps=gaps)
    live = SCAN.run(data, observed_at=OBSERVED, environ=ON)
    band = StrategyConfig()
    for item in live.handoff:
        gap = Decimal(repr(item.gap_pct))
        assert band.premarket_gap_min_pct <= gap <= band.premarket_gap_max_pct


def test_a_moved_gap_band_moves_the_mask_and_refuses_the_frozen_checksum():
    data = panel_input(40)
    tighter = replace(StrategyConfig(), premarket_gap_min_pct=Decimal("0.10"),
                      premarket_gap_max_pct=Decimal("0.12"))
    with pytest.raises(SCAN.LiveScanRefused) as refused:
        SCAN.run(data, observed_at=OBSERVED, strategy=tighter, environ=ON)
    assert refused.value.refusal is CFG.Refusal.CONTRACT_DRIFT


def test_the_mask_applies_no_volume_filter():
    assert K.final_rule().applies_volume_filter is False
    assert K.final_rule().mask_conditions == ("direction", "gap_min", "gap_max")


# -- refusals --------------------------------------------------------------------------------

def test_the_scan_refuses_when_the_flag_is_off():
    with pytest.raises(SCAN.LiveScanRefused) as refused:
        SCAN.run(panel_input(10), observed_at=OBSERVED, environ={})
    assert refused.value.refusal is CFG.Refusal.DISABLED


def test_the_scan_refuses_a_non_live_source():
    data = replace(panel_input(10), live=False, source_name="LOCAL_SNAPSHOT")
    with pytest.raises(SCAN.LiveScanRefused) as refused:
        SCAN.run(data, observed_at=OBSERVED, environ=ON)
    assert refused.value.refusal is CFG.Refusal.DATA_UNAVAILABLE


def test_a_data_refusal_from_the_source_is_named_not_absorbed():
    class Broken:
        def load(self, session, config):
            raise MoverDataUnavailableError(
                FEAT.DataUnavailable.NO_LIVE_PREMARKET_SOURCE, "nothing staged")
    with pytest.raises(SCAN.LiveScanRefused) as refused:
        SCAN.run_from_source(Broken(), SESSION, observed_at=OBSERVED, environ=ON)
    assert refused.value.refusal is CFG.Refusal.DATA_UNAVAILABLE
    assert "nothing staged" in refused.value.detail


def test_the_registered_feed_refuses_an_unstaged_session():
    feed = FEAT.LivePremarketFeed({})
    with pytest.raises(MoverDataUnavailableError):
        feed.load(SESSION, K.final_config())


# -- the live stamp --------------------------------------------------------------------------

def test_the_payload_carries_the_live_stamp_and_the_research_parent():
    live = SCAN.run(panel_input(40), observed_at=OBSERVED, environ=ON)
    payload = SCAN.candidate_payload(live)
    assert payload["scanner"] == "A-MOVER-LIVE-V1"
    assert payload["rules_checksum"] == LC.current().scanner_checksum
    assert payload["research_parent_version"] == "a-mover-scanner-v1.2"
    assert payload["research_parent_checksum"] == K.HANDOFF_CHECKSUM
    assert payload["rules_checksum"] != payload["research_parent_checksum"]
    assert payload["provider_contract"]["same_day_premarket_bars"] == "KIWOOM"
    for entry in payload["candidates"]:
        assert entry["live_scanner_rank"] and entry["discovery_output_rank"]
        assert entry["scanner_version"] == "A-MOVER-LIVE-V1"


def test_candidate_rows_carry_the_version_and_contiguous_ranks():
    live = SCAN.run(panel_input(40), observed_at=OBSERVED, environ=ON)
    rows = SCAN.candidate_rows(live)
    assert [row.rank for row in rows] == list(range(1, len(rows) + 1))
    assert all(row.is_top8 for row in rows)
    for row in rows:
        assert row.score_components["scanner_version"] == "A-MOVER-LIVE-V1"
        assert row.score_components["scanner_checksum"] == LC.current().scanner_checksum
        assert row.score_components["baseline_version"] == "A_MOVER_PM_VOLUME_V1"
        assert row.score_components["source_name"] == FEAT.SOURCE_NAME


def test_the_scan_is_deterministic():
    first = SCAN.run(panel_input(60), observed_at=OBSERVED, environ=ON)
    second = SCAN.run(panel_input(60), observed_at=OBSERVED, environ=ON)
    assert [item.symbol for item in first.handoff] == [item.symbol for item in second.handoff]
    assert [item.total_score for item in first.handoff] == [item.total_score
                                                            for item in second.handoff]


# -- parity is an audit metric (section M) ---------------------------------------------------

def test_parity_is_recorded_and_is_not_a_gate():
    live = SCAN.run(panel_input(40), observed_at=OBSERVED, environ=ON)
    body = SCAN.parity(live, ["ZZZ", "YYY"])
    assert body["is_production_gate"] is False
    assert body["overlap_count"] == 0
    assert live.candidate_count == 8          # a zero overlap changed nothing about the output


# -- the union universe's declared prunings --------------------------------------------------

def test_the_universe_declares_what_it_did_not_prune():
    assert "D_MINUS_1_CLOSE_FLOOR" in UNI.PRUNE_REJECTED
    assert "PRIOR_SESSION_PREMARKET_ACTIVITY" in UNI.PRUNE_REJECTED
    assert UNI.PRUNE_RULES == ("ACTIVE_COMMON_STOCK_REFERENCE_CACHE",
                               "FULL_DAILY_VOLUME_BASELINE_AT_D_MINUS_1",
                               "NO_SPLIT_EXECUTING_THIS_SESSION")


def test_the_union_is_a_union_and_reports_e_only_members():
    union = UNI.UnionUniverse(
        session=SESSION, a_symbols=("AAA", "BBB"), e_symbols=("BBB", "CCC"),
        union=("AAA", "BBB", "CCC"), reference_as_of=date(2026, 7, 1),
        reference_checksum="x", reference_active_rows=3, pruned_no_daily_baseline=0,
        pruned_split_session=0, e_artifact="universe_2026-09-15.json")
    assert union.e_only == ("CCC",)
    assert union.intersection == 1
    assert union.declaration()["union_symbols"] == 3


# -- the registered feed is the production source's own path ---------------------------------

def test_the_production_source_answers_once_a_feed_is_registered():
    """``LiveRuntimeSource`` refuses until a feed exists, then serves the staged panels."""
    from app.services import mover_scanner_source as M
    source = M.LiveRuntimeSource(feeds={})
    with pytest.raises(MoverDataUnavailableError) as refused:
        source.load(SESSION, K.final_config())
    assert refused.value.reason is M.DataUnavailable.NO_LIVE_PREMARKET_SOURCE

    registry: dict = {}
    panels = _panels(12)
    key = FEAT.register(panels, feeds=registry)
    assert key == FEAT.SOURCE_NAME
    live = SCAN.run_from_source(M.LiveRuntimeSource(feeds=registry), SESSION,
                               observed_at=OBSERVED, environ=ON)
    assert live.source_name == FEAT.SOURCE_NAME
    assert live.candidate_count > 0
    FEAT.unregister(feeds=registry)
    assert registry == {}


def _panels(count: int) -> FEAT.LivePanels:
    symbols = [f"S{index:03d}" for index in range(count)]
    snapshots = {symbol: snapshot_for(symbol, gap=0.05 + 0.002 * index,
                                      volume=200_000 + 5_000 * index)
                 for index, symbol in enumerate(symbols)}
    baselines = {symbol: baseline_for(symbol, 50_000.0) for symbol in symbols}
    premarket = FEAT.build_premarket_panel(SESSION, tuple(symbols), snapshots, baselines,
                                           calendar=CAL)
    union = UNI.UnionUniverse(
        session=SESSION, a_symbols=tuple(symbols), e_symbols=(), union=tuple(symbols),
        reference_as_of=date(2026, 7, 1), reference_checksum="ref", reference_active_rows=count,
        pruned_no_daily_baseline=0, pruned_split_session=0, e_artifact=None)
    return FEAT.LivePanels(session=SESSION, symbols=tuple(symbols), premarket=premarket,
                           daily=daily_panel(symbols, SESSION), universe=union,
                           baselines=baselines, snapshots=snapshots,
                           digest=premarket.cache_digest)


def test_a_feed_staged_for_another_session_is_refused():
    from app.services import mover_scanner_source as M
    registry: dict = {}
    FEAT.register(_panels(12), feeds=registry)
    with pytest.raises(SCAN.LiveScanRefused) as refused:
        SCAN.run_from_source(M.LiveRuntimeSource(feeds=registry), date(2026, 9, 16),
                            observed_at=OBSERVED, environ=ON)
    assert refused.value.refusal is CFG.Refusal.DATA_UNAVAILABLE
