"""What the screen is allowed to say.

The tests are grouped by the mistake they prevent: showing a lower bound as a quantity, showing
zero where nothing was observed, naming a nearest wall from a set that might be short one, drawing
an overlay on coordinates that are not trustworthy, and adding a judgement the data does not
contain.
"""
from __future__ import annotations

from decimal import Decimal

from app.crypto.liquidity_map import journal as J
from app.crypto.liquidity_map import view as V
from app.crypto.liquidity_map.wallstate import WallFilter, WallFollower, WallSet

from tests.crypto.liquidity_map_fixtures import (Handwriter, band_side, default_bands,
                                                 unknown_bands, written_journal)

WIDE = WallFilter(min_notional_usdt=Decimal("0"))


def build(tmp_path, **derived_kwargs):
    """A one-sample journal plus the view over it, so a test can state one condition."""
    writer = Handwriter(tmp_path)
    writer.session_start(started_ms=1_000)
    writer.derived(receive_ms=derived_kwargs.pop("receive_ms", 2_000), **derived_kwargs)
    session = J.latest_session(tmp_path)
    record, _ = J.last_record(tmp_path, "derived", session)
    return session, record


def view_of(tmp_path, *, now_ms=2_100, wall_set=None, wall_filter=WIDE, **derived_kwargs):
    session, record = build(tmp_path, **derived_kwargs)
    walls = wall_set if wall_set is not None else WallFollower().refresh(
        tmp_path, session, now_ms=now_ms)
    return V.snapshot_view(root=str(tmp_path), session=session, derived_record=record,
                           wall_set=walls, wall_filter=wall_filter, now_ms=now_ms)


# --- quality ----------------------------------------------------------------------------------

def test_a_fresh_synchronized_sample_is_live(tmp_path):
    view = view_of(tmp_path)
    assert view["quality"]["state"] == V.LIVE and view["quality"]["reasons"] == []


def test_a_journal_that_stopped_being_written_is_stale_with_its_age(tmp_path):
    view = view_of(tmp_path, now_ms=2_000 + V.JOURNAL_STALE_MS + 1_000)
    assert view["quality"]["state"] == V.STALE
    assert "JOURNAL_STALE" in view["quality"]["reasons"]
    assert view["quality"]["journal_age_ms"] == V.JOURNAL_STALE_MS + 1_000


def test_an_unsynchronized_book_is_syncing_not_stale(tmp_path):
    view = view_of(tmp_path, state="UNSYNCED", fresh=False, bands=unknown_bands())
    assert view["quality"]["state"] == V.SYNCING
    assert "BOOK_UNSYNCED" in view["quality"]["reasons"]


def test_a_stale_book_is_stale(tmp_path):
    view = view_of(tmp_path, state="STALE", fresh=False, age_ms=9_000, bands=unknown_bands())
    assert view["quality"]["state"] == V.STALE
    assert "BOOK_STALE" in view["quality"]["reasons"]


def test_a_crossed_book_is_stale_and_named(tmp_path):
    view = view_of(tmp_path, state="CROSSED_BOOK", fresh=False, bands=unknown_bands())
    assert view["quality"]["state"] == V.STALE
    assert "BOOK_CROSSED_BOOK" in view["quality"]["reasons"]


def test_an_ended_session_is_stale_even_when_its_last_sample_looked_fine(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start(started_ms=1_000)
    writer.derived(receive_ms=2_000)
    writer.session_end(ended_ms=2_100)
    session = J.latest_session(tmp_path)
    record, _ = J.last_record(tmp_path, "derived", session)
    view = V.snapshot_view(root=str(tmp_path), session=session, derived_record=record,
                           wall_set=WallSet(), wall_filter=WIDE, now_ms=2_200)
    assert view["quality"]["state"] == V.STALE
    assert "SESSION_ENDED" in view["quality"]["reasons"]


def test_a_disconnected_trade_stream_is_reported_separately_from_the_book(tmp_path):
    view = view_of(tmp_path, trade_connected=False)
    assert view["quality"]["trade_state"] == V.STALE
    assert "TRADE_STREAM_DISCONNECTED" in view["quality"]["reasons"]
    # The book is still fine, and the depth half of the screen must not be condemned with it.
    assert view["quality"]["book_state"] == "SYNCED"


def test_a_silent_trade_stream_past_the_threshold_is_stale(tmp_path):
    view = view_of(tmp_path, trade_age_ms=6_000)
    assert view["quality"]["trade_state"] == V.STALE
    assert "TRADE_STREAM_STALE" in view["quality"]["reasons"]


def test_a_trade_stream_that_has_delivered_nothing_yet_is_syncing_not_stale(tmp_path):
    view = view_of(tmp_path, trade_age_ms=None)
    assert view["quality"]["trade_state"] == V.SYNCING


def test_no_sample_at_all_is_no_data(tmp_path):
    view = V.empty_view(root=str(tmp_path), now_ms=1_000, reason="ROOT_NOT_FOUND")
    assert view["quality"]["state"] == V.NO_DATA
    assert view["quality"]["reasons"] == ["ROOT_NOT_FOUND"]
    assert view["price"]["mid"] is None and view["overlay"]["renderable"] is False


# --- price ------------------------------------------------------------------------------------

def test_the_spread_is_computed_from_the_two_sides_and_carries_its_bps(tmp_path):
    view = view_of(tmp_path)
    assert view["price"]["best_bid"] == "84749.9" and view["price"]["best_ask"] == "84750.1"
    assert view["price"]["spread"] == "0.2"
    assert view["price"]["spread_bps"] == "0.02"


def test_mark_is_null_and_says_which_clause_makes_it_unavailable(tmp_path):
    view = view_of(tmp_path)
    assert view["price"]["mark"] is None
    assert "mark" in view["price"]["mark_unavailable_reason"]
    assert view["price"]["mid_rule"] == "BEST_BID_AND_BEST_ASK"


def test_the_known_interval_is_reported_as_a_percentage_of_mid(tmp_path):
    view = view_of(tmp_path)
    assert view["price"]["known_low_pct"].startswith("-0.15")
    assert view["price"]["known_high_pct"].startswith("0.15")


# --- depth ------------------------------------------------------------------------------------

def test_a_complete_band_shows_a_canonical_quantity(tmp_path):
    bands = view_of(tmp_path)["sides"]["ASK"]["depth"]
    first = next(band for band in bands if band["band_pct"] == "0.1")
    assert first["coverage"] == "COMPLETE"
    assert first["qty"] == "100" and first["is_lower_bound"] is False


def test_a_partial_band_has_no_canonical_quantity_only_a_labelled_lower_bound(tmp_path):
    bands = view_of(tmp_path)["sides"]["ASK"]["depth"]
    for label in ("0.25", "0.5", "1"):
        band = next(item for item in bands if item["band_pct"] == label)
        assert band["coverage"] == "PARTIAL"
        assert band["qty"] is None and band["notional"] is None
        assert band["observed_qty"] == "100" and band["is_lower_bound"] is True


def test_an_unknown_band_is_null_and_never_zero(tmp_path):
    bands = view_of(tmp_path, state="UNSYNCED", fresh=False,
                    bands=unknown_bands())["sides"]["BID"]["depth"]
    for band in bands:
        assert band["coverage"] == "UNKNOWN"
        assert band["qty"] is None and band["observed_qty"] is None
        assert band["notional"] is None and band["observed_notional"] is None


def test_a_genuinely_empty_complete_band_keeps_its_zero(tmp_path):
    """Zero is a real reading. It must survive the same path that nulls an unobserved band."""
    bands = [{"band_pct": "0.1", "bid": band_side("COMPLETE", qty="0", notional="0"),
              "ask": band_side("COMPLETE", qty="0", notional="0"),
              "imbalance_btc": None, "imbalance_usdt": None}]
    depth = view_of(tmp_path, bands=bands)["sides"]["BID"]["depth"]
    assert depth[0]["coverage"] == "COMPLETE" and depth[0]["qty"] == "0"
    # The three bands the sample did not carry stay UNKNOWN rather than inheriting that zero.
    assert [band["coverage"] for band in depth[1:]] == ["UNKNOWN"] * 3


def test_every_contract_band_appears_in_contract_order(tmp_path):
    for side in ("ASK", "BID"):
        labels = [band["band_pct"] for band in view_of(tmp_path)["sides"][side]["depth"]]
        assert labels == ["0.1", "0.25", "0.5", "1"]


def test_band_imbalance_only_exists_where_both_sides_were_complete(tmp_path):
    bands = view_of(tmp_path)["sides"]["ASK"]["depth"]
    assert bands[0]["imbalance_btc"] == "0.01"
    assert all(band["imbalance_btc"] is None for band in bands[1:])


# --- walls ------------------------------------------------------------------------------------

def resting(side, price, **extra):
    """A candidate shaped like the collector's state checkpoint, which is now the normal source.

    `persistence_ms` is live there - 60 s of observation - rather than the 0 a journal OPENED row
    carries forever. That matters here because the frozen rule has a persistence floor, and a
    fixture built on the journal's 0 would be testing the rejection path in every test.
    """
    payload = {"side": side, "price": price, "qty": "10", "notional": "847500",
               "multiple": "9", "local_average": "1", "neighbours": 10,
               "first_seen_ms": 1_000, "persistence_ms": 60_000, "samples": 60,
               "coverage": "PARTIAL", "generation": 1}
    payload.update(extra)
    return payload


def complete_set(*rows):
    return WallSet(resting={(row["side"], row["price"]): row for row in rows},
                   coverage="COMPLETE", verified_by="STREAM_START", missing_count=0)


def test_the_nearest_ask_is_the_lowest_and_the_nearest_bid_is_the_highest(tmp_path):
    walls = complete_set(resting("ASK", "84800"), resting("ASK", "84760"),
                         resting("BID", "84700"), resting("BID", "84740"))
    view = view_of(tmp_path, wall_set=walls)
    assert view["sides"]["ASK"]["nearest_wall"]["price"] == "84760"
    assert view["sides"]["BID"]["nearest_wall"]["price"] == "84740"


def test_wall_distance_is_positive_away_from_mid_on_both_sides(tmp_path):
    walls = complete_set(resting("ASK", "84800"), resting("BID", "84700"))
    view = view_of(tmp_path, wall_set=walls)
    for side in ("ASK", "BID"):
        wall = view["sides"][side]["nearest_wall"]
        assert Decimal(wall["distance_bps"]) > 0


def test_an_unproven_candidate_set_yields_no_nearest_wall_and_no_overlay_lines(tmp_path):
    walls = WallSet(resting={("ASK", "84800"): resting("ASK", "84800")}, coverage="PARTIAL",
                    unverified_reason="SCAN_BUDGET_EXHAUSTED", missing_count=4)
    view = view_of(tmp_path, wall_set=walls)
    assert view["sides"]["ASK"]["nearest_wall"] is None
    assert view["sides"]["ASK"]["nearest_unavailable_reason"] == V.NO_WALL_SET
    assert view["overlay"]["sell_wall"] is None and view["overlay"]["buy_wall"] is None
    assert view["overlay"]["walls_suppressed_reason"] == V.NO_WALL_SET
    # The candidates themselves are still listed - they were observed - just not called nearest.
    assert len(view["sides"]["ASK"]["walls"]) == 1


def test_without_a_mid_there_is_no_nearest_wall_even_with_a_proven_set(tmp_path):
    walls = complete_set(resting("ASK", "84800"))
    view = view_of(tmp_path, wall_set=walls, state="UNSYNCED", fresh=False,
                   bands=unknown_bands(), mid=None)
    assert view["sides"]["ASK"]["nearest_wall"] is None
    assert view["sides"]["ASK"]["nearest_unavailable_reason"] == V.NO_BOOK


def test_no_candidate_on_a_side_is_reported_as_no_candidate_not_as_a_failure(tmp_path):
    view = view_of(tmp_path, wall_set=complete_set(resting("BID", "84700")))
    assert view["sides"]["ASK"]["nearest_wall"] is None
    assert view["sides"]["ASK"]["candidates_total"] == 0
    assert view["sides"]["ASK"]["walls"] == []
    assert view["sides"]["ASK"]["nearest_unavailable_reason"] is None
    assert view["overlay"]["sell_wall"] is None and view["overlay"]["buy_wall"] is not None


def test_the_display_filter_narrows_the_list_and_all_three_counts_are_published(tmp_path):
    """Three counts, because they answer three questions and one number cannot."""
    walls = complete_set(resting("ASK", "84800", notional="100000"),
                         resting("ASK", "84810", notional="900000"))
    view = view_of(tmp_path, wall_set=walls,
                   wall_filter=WallFilter(min_notional_usdt=Decimal("500000")))
    side = view["sides"]["ASK"]
    # V0 qualified two; the frozen rule kept one (100,000 is below its own floor); the filter
    # kept that one. A screen must be able to say which of the three removed something.
    assert side["candidates_total"] == 2
    assert side["walls_selected"] == 1 and side["walls_shown"] == 1
    assert side["nearest_wall"]["price"] == "84810"


def test_a_resting_candidate_s_persistence_is_measured_against_the_newest_sample(tmp_path):
    """The journal's own `persistence_ms` is 0 forever on an OPENED row, so it is not the answer."""
    walls = complete_set(resting("ASK", "84800", first_seen_ms=1_400, persistence_ms=0))
    view = view_of(tmp_path, wall_set=walls)
    candidate = view["candidates"]["ASK"][0]
    assert candidate["row_persistence_ms"] == 0
    assert candidate["observed_persistence_ms"] == 600
    assert candidate["persistence_rule"] == "LATEST_SAMPLE_MS_MINUS_FIRST_SEEN_MS"


def test_a_candidate_seen_for_one_sample_is_not_called_a_wall(tmp_path):
    """The journal's OPENED row for a just-opened candidate is exactly this case."""
    walls = complete_set(resting("ASK", "84800", first_seen_ms=1_400, persistence_ms=0))
    view = view_of(tmp_path, wall_set=walls)
    side = view["sides"]["ASK"]
    assert side["candidates_total"] == 1 and side["walls_selected"] == 0
    assert side["selection"]["rejected"]["BELOW_MIN_PERSISTENCE"] == 1
    assert side["nearest_wall"] is None


def test_every_wall_row_carries_the_contract_s_two_disclaimers(tmp_path):
    view = view_of(tmp_path, wall_set=complete_set(resting("ASK", "84800")))
    for row in (view["sides"]["ASK"]["walls"][0], view["candidates"]["ASK"][0]):
        assert row["persistence_is_sampled_span"] is True
        assert row["order_identity_proven"] is False


def test_the_wall_summary_carries_the_verification_evidence(tmp_path):
    walls = WallSet(coverage="PARTIAL", unverified_reason="SCAN_BUDGET_EXHAUSTED",
                    authority_active=12, reconstructed_at_authority=9, missing_count=3)
    summary = view_of(tmp_path, wall_set=walls)["walls"]
    assert summary["authority_active"] == 12
    assert summary["reconstructed_at_authority"] == 9
    assert summary["missing_count"] == 3
    assert summary["rule"]["min_multiple"] == "5"
    assert summary["rule"]["v0_rule"]["min_multiple"] == "3"
    assert summary["rule"]["identity"]["sha256_agrees"] is True


# --- flow -------------------------------------------------------------------------------------

def test_complete_flow_windows_carry_canonical_totals_and_an_imbalance(tmp_path):
    windows = view_of(tmp_path)["flow"]["windows"]
    assert set(windows) == {"5s", "15s", "60s"}
    assert windows["5s"]["buy_btc"] == "1.5" and windows["5s"]["imbalance_btc"] == "0.5"
    assert windows["5s"]["is_lower_bound"] is False


def test_a_partial_flow_window_has_only_observed_totals_and_no_imbalance(tmp_path):
    windows = view_of(tmp_path, flow_coverage="PARTIAL")["flow"]["windows"]
    window = windows["60s"]
    assert window["buy_btc"] is None and window["net_btc"] is None
    assert window["observed_buy_btc"] == "1.5" and window["is_lower_bound"] is True
    assert window["imbalance_btc"] is None
    assert window["coverage_reason"] == "WARMUP"


def test_an_unknown_flow_window_is_null_throughout(tmp_path):
    window = view_of(tmp_path, flow_coverage="UNKNOWN")["flow"]["windows"]["5s"]
    assert window["observed_buy_btc"] is None and window["buy_btc"] is None
    assert window["trades"] is None


def test_the_no_trade_state_keeps_coverage_and_shows_zero_volume(tmp_path):
    """A COMPLETE window with nothing in it is a real zero; its imbalance is still null."""
    quiet = {label: {"coverage": "COMPLETE", "coverage_reason": None, "coverage_age_ms": 70_000,
                     "trades": 0, "buy_btc": "0", "sell_btc": "0", "buy_usdt": "0",
                     "sell_usdt": "0", "net_btc": "0", "net_usdt": "0", "observed_buy_btc": "0",
                     "observed_sell_btc": "0", "observed_buy_usdt": "0", "observed_sell_usdt": "0",
                     "observed_is_lower_bound": False, "imbalance_btc": None,
                     "imbalance_usdt": None} for label in ("5s", "15s", "60s")}
    writer = Handwriter(tmp_path)
    writer.session_start(started_ms=1_000)
    writer.derived(receive_ms=2_000)
    session = J.latest_session(tmp_path)
    record, _ = J.last_record(tmp_path, "derived", session)
    record["payload"]["flow"] = quiet
    view = V.snapshot_view(root=str(tmp_path), session=session, derived_record=record,
                           wall_set=WallSet(), wall_filter=WIDE, now_ms=2_100)
    window = view["flow"]["windows"]["60s"]
    assert window["coverage"] == "COMPLETE" and window["trades"] == 0
    assert window["buy_btc"] == "0" and window["imbalance_btc"] is None


def test_the_aggressor_rule_is_stated_rather_than_assumed(tmp_path):
    flow = view_of(tmp_path)["flow"]
    assert "buyer is maker" in flow["aggressor_rule"]
    assert flow["clock"] == "LOCAL_MONOTONIC_RECEIPT"


# --- overlay ----------------------------------------------------------------------------------

def test_the_overlay_axis_is_the_snapshot_s_known_interval(tmp_path):
    overlay = view_of(tmp_path)["overlay"]
    assert overlay["renderable"] is True
    assert overlay["axis_low"] == "84620" and overlay["axis_high"] == "84880"
    assert overlay["axis_rule"] == "SNAPSHOT_KNOWN_INTERVAL"


def test_the_overlay_draws_nothing_when_the_feed_is_not_live(tmp_path):
    overlay = view_of(tmp_path, state="UNSYNCED", fresh=False, bands=unknown_bands())["overlay"]
    assert overlay["renderable"] is False
    assert overlay["suppressed_reason"]
    assert overlay["sell_wall"] is None and overlay["buy_wall"] is None


def test_the_overlay_is_suppressed_when_the_journal_is_stale(tmp_path):
    overlay = view_of(tmp_path, now_ms=2_000 + V.JOURNAL_STALE_MS + 1)["overlay"]
    assert overlay["renderable"] is False
    assert "JOURNAL_STALE" in overlay["suppressed_reason"]


# --- scope ------------------------------------------------------------------------------------

#: Keys whose value is prose *about* the payload rather than payload. They are stripped before
#: the banned-vocabulary scan for the same reason the collector's isolation test strips
#: docstrings: these sentences exist to say "no direction, no score", so scanning them for the
#: word "score" makes the documentation fail the test it is documenting.
PROSE_KEYS = {"scope", "mark_unavailable_reason", "note", "no_verdict_note", "imbalance_note",
              "aggressor_rule", "nearest_unavailable_reason", "coverage_note",
              "suppressed_reason", "walls_suppressed_reason", "unverified_reason"}


def data_only(value):
    """The payload with its self-describing prose removed. Every other string is kept."""
    if isinstance(value, dict):
        return {key: data_only(item) for key, item in value.items() if key not in PROSE_KEYS}
    if isinstance(value, list):
        return [data_only(item) for item in value]
    return value


def test_the_payload_contains_no_direction_no_score_and_no_order_vocabulary(tmp_path):
    view = view_of(tmp_path, wall_set=complete_set(resting("ASK", "84800"),
                                                   resting("BID", "84700")))
    text = repr(data_only(view)).lower()
    for banned in ("long", "short", "score", "entry_price", "take_profit", "stop_loss",
                   "position_size", "buy_signal", "sell_signal", "spoof", "absorption",
                   "iceberg", "recommend"):
        assert banned not in text, banned


def test_the_stripped_prose_is_the_text_that_declares_the_scope(tmp_path):
    """The exclusion above is not a hole: these are the sentences that state the bans."""
    view = view_of(tmp_path)
    assert "score" in view["preview"]["scope"]
    assert "spoofing" in view["walls"]["no_verdict_note"]
    assert "mark price" in view["price"]["mark_unavailable_reason"]
    assert "방향 판정" in view["flow"]["imbalance_note"]


def test_the_preview_declares_itself_a_read_only_viewer(tmp_path):
    view = view_of(tmp_path)
    assert view["preview"]["mode"] == "READ_ONLY_JOURNAL_VIEWER"
    assert view["source"]["root"] == str(tmp_path)


def test_the_view_over_the_real_collector_journal_is_live_and_whole(tmp_path):
    """Twelve samples, because the frozen rule wants ten seconds of observation before it calls
    anything a wall and a four-sample fixture would only ever test the rejection."""
    root = tmp_path / "journal"
    info = written_journal(root, samples=12, end_session=False, seal=False)
    session = J.latest_session(root)
    record, _ = J.last_record(root, "derived", session)
    walls = WallFollower().refresh(root, session, now_ms=info["last_sample_ms"])
    view = V.snapshot_view(root=str(root), session=session, derived_record=record,
                           wall_set=walls, wall_filter=WIDE,
                           now_ms=info["last_sample_ms"] + 200)
    assert view["quality"]["state"] == V.LIVE
    assert view["walls"]["coverage"] == "COMPLETE"
    assert view["sides"]["ASK"]["nearest_wall"] is not None
    assert view["sides"]["BID"]["nearest_wall"] is not None
    assert view["overlay"]["renderable"] is True
    assert view["source"]["wall_stream_lag_ms"] is not None


def test_the_real_collector_journal_is_read_through_its_state_checkpoint(tmp_path):
    """The collector publishes its live candidate set, so the viewer reads it instead of walking."""
    root = tmp_path / "journal"
    info = written_journal(root, samples=12, end_session=False, seal=False)
    session = J.latest_session(root)
    walls = WallFollower().refresh(root, session, now_ms=info["last_sample_ms"])
    assert walls.verified_by == "COLLECTOR_STATE_CHECKPOINT"
    assert walls.values_as_of == "CHECKPOINT_CURRENT"
    assert walls.coverage == "COMPLETE" and walls.truncated is False
    # The whole set came out of one small file, and the journal tail behind it was empty because
    # the file is replaced immediately while the stream is still buffered.
    assert walls.state is not None and walls.state.usable is True
    assert walls.tail_records == 0 and walls.tail_complete is True


# --- a stale sample must stop claiming the present ----------------------------------------------

def test_a_stale_journal_stops_the_trade_stream_claiming_to_be_live(tmp_path):
    """The stream's age was measured when the sample was written, not now."""
    fresh = view_of(tmp_path)
    assert fresh["quality"]["trade_state"] == V.LIVE
    assert fresh["quality"]["sample_is_current"] is True

    stale = view_of(tmp_path, now_ms=2_000 + V.JOURNAL_STALE_MS + 1_000)
    assert stale["quality"]["sample_is_current"] is False
    assert stale["quality"]["trade_state"] == V.STALE
    assert "SAMPLE_NOT_CURRENT" in stale["quality"]["reasons"]
    # The sample's own figures are still published; what is withdrawn is the claim they are now.
    assert stale["quality"]["trade_age_ms"] == 200


def test_an_ended_session_also_stops_the_trade_stream_claiming_to_be_live(tmp_path):
    writer = Handwriter(tmp_path)
    writer.session_start(started_ms=1_000)
    writer.derived(receive_ms=2_000)
    writer.session_end(ended_ms=2_050)
    session = J.latest_session(tmp_path)
    record, _ = J.last_record(tmp_path, "derived", session)
    view = V.snapshot_view(root=str(tmp_path), session=session, derived_record=record,
                           wall_set=WallSet(), wall_filter=WIDE, now_ms=2_100)
    assert view["quality"]["sample_is_current"] is False
    assert view["quality"]["trade_state"] == V.STALE


def test_no_sample_at_all_is_not_current_either(tmp_path):
    view = V.empty_view(root=str(tmp_path), now_ms=1_000, reason="ROOT_NOT_FOUND")
    assert view["quality"]["sample_is_current"] is False
