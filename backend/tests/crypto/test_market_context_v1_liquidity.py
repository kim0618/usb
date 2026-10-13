"""The LIQUIDITY layer: parity with the Liquidity Map viewer, and the four ways there is no wall.

Parity is structural - the panel calls the viewer's own route and projects fields out of the dict it
returns - so the tests here check that the projection is a projection: every figure on the panel is
the identical object the viewer published, not a recomputation that happens to agree today.

The rest is the discrimination the panel exists for. `NONE`, `PARTIAL`, `STALE` and `UNKNOWN` call
for opposite reactions from the person watching, and a screen that renders all four as a dash is
hiding the dangerous one behind the innocent one.
"""
from __future__ import annotations

import time

import pytest

from app.crypto.liquidity_map import api as LMAPI
from app.crypto.liquidity_map.wallstate import WallFollower
from app.crypto.market_context_v1 import contract as C
from app.crypto.market_context_v1 import liquidity as L

from tests.crypto.liquidity_map_fixtures import (Handwriter, default_bands, unknown_bands,
                                                 written_journal)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    LMAPI.state = LMAPI.PreviewState()
    monkeypatch.delenv(LMAPI.ROOT_ENV, raising=False)
    yield


def point_at(monkeypatch, root):
    monkeypatch.setenv(LMAPI.ROOT_ENV, str(root))


def live_journal(root, *, now_ms=None, bands=None, flow_coverage="COMPLETE",
                 trade_connected=True, state="SYNCED", walls=True, end=False):
    """A handwritten session whose newest sample is current, so the viewer reads it LIVE."""
    now = int(time.time() * 1000) if now_ms is None else now_ms
    writer = Handwriter(root)
    writer.session_start(started_ms=now - 60_000)
    if walls:
        writer.wall(side="ASK", price="84800", event="OPENED", status="ACTIVE",
                    receive_ms=now - 30_000, first_seen_ms=now - 90_000)
        writer.wall(side="BID", price="84700", event="OPENED", status="ACTIVE",
                    receive_ms=now - 30_000, first_seen_ms=now - 90_000)
    writer.stats(active=2 if walls else 0, receive_ms=now - 20_000)
    writer.derived(receive_ms=now, bands=default_bands() if bands is None else bands,
                   flow_coverage=flow_coverage, trade_connected=trade_connected, state=state)
    if end:
        writer.session_end(ended_ms=now)
    return now


# --- parity ------------------------------------------------------------------------------------

def test_the_panel_reads_the_viewers_own_route_and_changes_no_figure(monkeypatch, tmp_path):
    written_journal(tmp_path)
    point_at(monkeypatch, tmp_path)
    snapshot = L.read()
    view = L.liquidity_view(snapshot, symbol=C.BTC)

    assert view["book"]["mid"] == snapshot["price"]["mid"]
    assert view["book"]["best_bid"] == snapshot["price"]["best_bid"]
    assert view["book"]["spread_bps"] == snapshot["price"]["spread_bps"]
    assert view["book"]["observed_low"] == snapshot["coverage"]["known_low"]
    assert view["coverage"]["bands"] is snapshot["coverage"]["bands"]
    assert view["walls"]["coverage"] == snapshot["walls"]["coverage"]
    assert view["walls"]["rule"] == snapshot["walls"]["rule"]["rule_version"]
    assert view["walls"]["continuity_rule"] == \
        snapshot["walls"]["continuity_rule"]["rule_version"]
    for side in ("ASK", "BID"):
        source = snapshot["sides"][side]
        projected = view["sides"][side]
        assert projected["candidates_total"] == source["candidates_total"]
        assert projected["walls_selected"] == source["walls_selected"]
        assert len(projected["depth"]) == len(source["depth"])
        if source["nearest_wall"] is not None:
            assert projected["nearest_wall"]["price"] == source["nearest_wall"]["price"]
            assert projected["nearest_wall"]["notional_usdt"] == \
                source["nearest_wall"]["notional_usdt"]
            assert projected["nearest_wall"]["persistence_ms"] == \
                source["nearest_wall"]["observed_persistence_ms"]


def test_the_frozen_rules_arrive_already_applied(monkeypatch, tmp_path):
    live_journal(tmp_path)
    point_at(monkeypatch, tmp_path)
    view = L.liquidity_view(L.read(), symbol=C.BTC)
    assert view["walls"]["rule"] == "lm-wall.v2"
    assert view["walls"]["continuity_rule"].startswith("lm-continuity.")


def test_the_distance_is_restated_in_percent_without_changing_the_bps(monkeypatch, tmp_path):
    live_journal(tmp_path)
    point_at(monkeypatch, tmp_path)
    snapshot = L.read()
    view = L.liquidity_view(snapshot, symbol=C.BTC)
    for side in ("ASK", "BID"):
        wall = view["sides"][side]["nearest_wall"]
        if wall is None:
            continue
        assert wall["distance_bps"] == snapshot["sides"][side]["nearest_wall"]["distance_bps"]
        assert float(wall["distance_pct"]) == pytest.approx(float(wall["distance_bps"]) / 100)


# --- the four ways there is no wall -------------------------------------------------------------

def test_a_wall_that_qualifies_reads_ok(monkeypatch, tmp_path):
    live_journal(tmp_path)
    point_at(monkeypatch, tmp_path)
    view = L.liquidity_view(L.read(), symbol=C.BTC)
    assert view["state"] == C.LIVE, view["reasons"]
    assert view["sides"]["ASK"]["wall_state"] == C.WALL_OK
    assert view["sides"]["ASK"]["nearest_wall"] is not None


def test_a_complete_book_with_nothing_qualifying_reads_none_not_unknown(monkeypatch, tmp_path):
    """The one case the panel may say 'no wall' in: a proven-complete set that is empty."""
    live_journal(tmp_path, walls=False)
    point_at(monkeypatch, tmp_path)
    snapshot = L.read()
    assert snapshot["walls"]["coverage"] == C.COMPLETE
    view = L.liquidity_view(snapshot, symbol=C.BTC)
    for side in ("ASK", "BID"):
        assert view["sides"][side]["wall_state"] == C.WALL_NONE
        assert view["sides"][side]["wall_state_reason"] == \
            "NO_CANDIDATE_QUALIFIES_UNDER_LM_WALL_V2"


def test_a_journal_older_than_the_freshness_bound_reads_stale(monkeypatch, tmp_path):
    now = int(time.time() * 1000)
    live_journal(tmp_path, now_ms=now - 10 * 60_000)
    point_at(monkeypatch, tmp_path)
    view = L.liquidity_view(L.read(), symbol=C.BTC)
    assert view["state"] == C.STALE
    for side in ("ASK", "BID"):
        assert view["sides"][side]["wall_state"] == C.STALE
        assert view["sides"][side]["wall_state_reason"] == \
            "JOURNAL_OLDER_THAN_FRESHNESS_BOUND"


def test_no_root_reads_unknown_and_never_a_neutral_middle(monkeypatch):
    view = L.liquidity_view(L.read(), symbol=C.BTC)
    assert view["state"] == C.UNKNOWN
    assert "MS_V0_ROOT_NOT_SET" in view["reasons"]
    assert "NEUTRAL" not in str(view)


def test_an_unsynced_book_reads_unknown(monkeypatch, tmp_path):
    live_journal(tmp_path, state="UNSYNCED")
    point_at(monkeypatch, tmp_path)
    view = L.liquidity_view(L.read(), symbol=C.BTC)
    assert view["state"] == C.UNKNOWN
    for side in ("ASK", "BID"):
        assert view["sides"][side]["wall_state"] == C.UNKNOWN


def test_an_unprovable_wall_set_reads_partial_rather_than_claiming_a_nearest(monkeypatch,
                                                                            tmp_path):
    """A set the follower could not prove complete cannot name a nearest wall.

    The walls it is missing are the longest-resting ones, which are exactly the ones being asked
    about, so an approximately-right nearest is worse than none.
    """
    now = int(time.time() * 1000)
    writer = Handwriter(tmp_path)
    writer.session_start(started_ms=now - 60_000)
    for index in range(300):
        writer.wall(side="ASK", price=str(84_800 + index), event="OPENED", status="ACTIVE",
                    receive_ms=now - 30_000 + index)
    writer.derived(receive_ms=now)
    point_at(monkeypatch, tmp_path)
    # A budget too small to walk back to the session start and no authority row to check the count
    # against: the reconstruction can only have undercounted, and it cannot prove it did not.
    LMAPI.state.followers[str(tmp_path)] = WallFollower(scan_budget_bytes=2_000)
    snapshot = L.read()
    assert snapshot["walls"]["coverage"] != C.COMPLETE
    view = L.liquidity_view(snapshot, symbol=C.BTC)
    assert view["state"] == C.PARTIAL
    assert any(reason.startswith("WALL_SET_COVERAGE_") for reason in view["reasons"])
    for side in ("ASK", "BID"):
        assert view["sides"][side]["wall_state"] == C.PARTIAL
        assert view["sides"][side]["nearest_wall"] is None


# --- bounds, nulls and the observed interval -----------------------------------------------------

def test_the_wider_bands_are_marked_as_lower_bounds_and_the_tightest_is_not(monkeypatch,
                                                                            tmp_path):
    live_journal(tmp_path)
    point_at(monkeypatch, tmp_path)
    view = L.liquidity_view(L.read(), symbol=C.BTC)
    bands = {band["band_pct"]: band for band in view["sides"]["ASK"]["depth"]}
    assert bands["0.1"]["is_lower_bound"] is False
    for label in ("0.25", "0.5", "1"):
        assert bands[label]["is_lower_bound"] is True
        assert bands[label]["coverage"] == C.PARTIAL


def test_an_unobserved_band_is_null_and_never_zero(monkeypatch, tmp_path):
    live_journal(tmp_path, bands=unknown_bands())
    point_at(monkeypatch, tmp_path)
    view = L.liquidity_view(L.read(), symbol=C.BTC)
    for band in view["sides"]["ASK"]["depth"]:
        assert band["coverage"] == C.UNKNOWN
        assert band["observed_notional"] is None
        assert band["notional"] is None


def test_the_observed_interval_is_published_with_every_wall_figure(monkeypatch, tmp_path):
    live_journal(tmp_path)
    point_at(monkeypatch, tmp_path)
    view = L.liquidity_view(L.read(), symbol=C.BTC)
    book = view["book"]
    assert book["observed_low"] is not None and book["observed_high"] is not None
    assert book["observed_symmetric_pct"] is not None
    assert "±0.15%" in view["observed_range_note"]


def test_the_presence_base_rate_travels_with_the_wall_rows(monkeypatch, tmp_path):
    """A condition true in 99% of samples cannot separate states, so the figure is on screen."""
    live_journal(tmp_path)
    point_at(monkeypatch, tmp_path)
    view = L.liquidity_view(L.read(), symbol=C.BTC)
    rate = view["presence_base_rate"]
    assert rate["ask_pct"] == 99.0 and rate["bid_pct"] == 98.5
    assert rate["mean_per_side_when_present"] == 8.8
    assert "bullish" in view["presence_note"]


# --- symbols ------------------------------------------------------------------------------------

@pytest.mark.parametrize("symbol", ["ETHUSDT", "SOLUSDT"])
def test_a_non_btc_symbol_gets_no_book_and_no_btc_figure(monkeypatch, tmp_path, symbol):
    live_journal(tmp_path)
    point_at(monkeypatch, tmp_path)
    snapshot = L.read()
    view = L.liquidity_view(snapshot, symbol=symbol)
    assert view["state"] == C.UNAVAILABLE
    assert view["reasons"] == [C.REASON_COLLECTOR_BTC_ONLY]
    for key in ("sides", "depth", "coverage", "book", "walls"):
        assert view[key] is None, key
    assert snapshot["price"]["mid"] not in str(view)


def test_a_viewer_fault_is_a_screen_state_not_a_blank_panel():
    view = L.liquidity_view({"__error__": 400}, symbol=C.BTC, reason="VIEWER_RAISED:ValueError")
    assert view["state"] == C.UNKNOWN
    assert view["reasons"] == ["VIEWER_RAISED:ValueError"]
