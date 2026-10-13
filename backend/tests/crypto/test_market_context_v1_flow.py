"""The FLOW layer: freshness that cannot be borrowed, and the two things a vanished wall may say.

The first group is about a defect that was found by reading a live stale journal rather than by
reasoning: every age inside a `flow_view` window is measured from the sample the collector wrote, so
on a twenty-hour-old record `trade_stream.age_ms` reads 80 ms and is perfectly true - 80 ms before
that sample was written. Read on its own it says the trade feed is live. The journal's own freshness
is therefore the floor under every window state, and these tests pin that.

The second group is the vanished-wall classification. `traded_through` reproduces Market Context
R0's predicate, so one test runs the research module's own expression beside it on constructed
samples: a classifier that drifted from the measurement its base rates come from would make those
base rates a lie.
"""
from __future__ import annotations

import time
from decimal import Decimal

import numpy as np
import pytest

from app.crypto.market_context_v1 import contract as C
from app.crypto.market_context_v1 import flow as F


def liquidity(*, state=C.LIVE, mid="84750", sample_ms=1_000_000, session="s1",
              book_state="SYNCED", walls=(), generation=None):
    """A minimal LIQUIDITY payload shaped the way the tracker reads it."""
    sides = {"ASK": {"walls": [], "nearest_wall": None},
             "BID": {"walls": [], "nearest_wall": None}}
    for side, low, high, notional, generation in walls:
        wall = {"bin_low": low, "bin_high": high, "price": low, "notional_usdt": notional,
                "generation": generation, "persistence_ms": 60_000}
        sides[side]["walls"].append(wall)
        if sides[side]["nearest_wall"] is None:
            sides[side]["nearest_wall"] = wall
    return {"state": state, "sides": sides,
            "book": {"mid": mid},
            "journal": {"sample_receive_ms": sample_ms, "session_id": session,
                        "book_state": book_state, "book_generation": generation}}


def snapshot(*, coverage="COMPLETE", connected=True, age_ms=200, buy="100000",
             sell="50000"):
    window = lambda label: {
        "window": label, "coverage": coverage, "coverage_reason": None,
        "coverage_age_ms": 1_000, "trades": 12,
        "buy_btc": "1.0", "sell_btc": "0.5", "buy_usdt": buy, "sell_usdt": sell,
        "net_btc": "0.5", "net_usdt": "50000", "imbalance_usdt": "0.33",
        "imbalance_btc": "0.33", "is_lower_bound": coverage == "PARTIAL"}
    return {"flow": {"windows": {label: window(label) for label in C.FLOW_WINDOWS},
                     "aggressor_rule": "aggressor=SELL if buyer is maker (m=true), else BUY",
                     "clock": "LOCAL_MONOTONIC_RECEIPT",
                     "trade_stream": {"connected": connected, "age_ms": age_ms,
                                      "last_receive_ms": 1_000, "stale_ms": C.TRADE_STALE_MS},
                     "imbalance_note": "test"}}


# --- freshness cannot be borrowed ----------------------------------------------------------------

def test_a_live_journal_and_a_fresh_stream_read_live():
    view = F.flow_view(snapshot(), liquidity(), symbol=C.BTC, tracker=F.VanishTracker())
    assert view["state"] == C.LIVE and view["reasons"] == []
    assert {window["state"] for window in view["windows"].values()} == {C.LIVE}


def test_a_stale_journal_makes_every_window_stale_even_though_the_stream_age_reads_fresh():
    """The defect this layer was rewritten for. The stream age is 200 ms and still STALE."""
    view = F.flow_view(snapshot(age_ms=200), liquidity(state=C.STALE), symbol=C.BTC,
                       tracker=F.VanishTracker())
    assert view["state"] == C.STALE
    assert "JOURNAL_OLDER_THAN_FRESHNESS_BOUND" in view["reasons"]
    assert {window["state"] for window in view["windows"].values()} == {C.STALE}
    assert view["trade_stream"]["age_ms"] == 200
    assert view["trade_stream"]["state"] == C.STALE
    assert view["trade_stream"]["journal_state"] == C.STALE


def test_the_screen_is_told_what_the_stream_age_is_measured_from():
    view = F.flow_view(snapshot(), liquidity(), symbol=C.BTC, tracker=F.VanishTracker())
    assert view["trade_stream"]["age_measured_from"] == "THE_SAMPLE_THE_COLLECTOR_WROTE"


def test_an_unreadable_journal_makes_the_flow_unknown():
    view = F.flow_view(snapshot(), liquidity(state=C.UNKNOWN), symbol=C.BTC,
                       tracker=F.VanishTracker())
    assert view["state"] == C.UNKNOWN
    assert {window["state"] for window in view["windows"].values()} == {C.UNKNOWN}


def test_a_disconnected_stream_is_unknown_rather_than_zero_flow():
    view = F.flow_view(snapshot(connected=False), liquidity(), symbol=C.BTC,
                       tracker=F.VanishTracker())
    assert view["state"] == C.UNKNOWN
    assert "TRADE_STREAM_NOT_CONNECTED" in view["reasons"]


def test_a_stream_older_than_the_contract_bound_is_stale_on_a_live_journal():
    view = F.flow_view(snapshot(age_ms=C.TRADE_STALE_MS + 1), liquidity(), symbol=C.BTC,
                       tracker=F.VanishTracker())
    assert view["state"] == C.STALE
    assert "TRADE_STREAM_OLDER_THAN_FRESHNESS_BOUND" in view["reasons"]


def test_a_partial_window_is_a_bound_not_a_quantity():
    view = F.flow_view(snapshot(coverage="PARTIAL"), liquidity(), symbol=C.BTC,
                       tracker=F.VanishTracker())
    assert view["state"] == C.PARTIAL
    assert all(window["is_lower_bound"] for window in view["windows"].values())


# --- traded_through, against the research module's own expression ---------------------------------

@pytest.mark.parametrize("side,path,low,high,expected", [
    ("ASK", ["84750", "84800", "84760"], "84790", "84795", True),
    ("ASK", ["84750", "84760"], "84790", "84795", False),
    ("BID", ["84750", "84700", "84740"], "84705", "84710", True),
    ("BID", ["84750", "84740"], "84705", "84710", False),
])
def test_the_touch_predicate_matches_market_context_r0s_own(side, path, low, high, expected):
    """R0's rule: for an ask bin the mid path has to reach `bin_high`, for a bid bin `bin_low`."""
    decimals = [Decimal(value) for value in path]
    mine = F.traded_through(decimals, side=side, bin_low=Decimal(low), bin_high=Decimal(high))
    # The research expression, transcribed from `market_context_r0.diagnose.wall_fate`.
    array = np.asarray([float(value) for value in path])
    theirs = bool(array.size and (float(array.max()) >= float(high) if side == "ASK"
                                 else float(array.min()) <= float(low)))
    assert mine is expected and theirs is expected


def test_an_empty_path_is_not_evidence_of_no_touch():
    """The caller has to decide UNKNOWN before asking, which the tracker does."""
    assert F.traded_through([], side="ASK", bin_low=Decimal("1"),
                            bin_high=Decimal("2")) is False


# --- the vanish tracker ---------------------------------------------------------------------------

def test_a_wall_that_disappears_after_the_price_reached_its_bin_is_a_consumption_candidate():
    tracker = F.VanishTracker()
    tracker.observe(liquidity(sample_ms=1_000, mid="84750",
                              walls=[("ASK", "84790", "84795", "900000", 1)]))
    # One sample later the mid has reached into the bin and the wall is gone.
    gone = tracker.observe(liquidity(sample_ms=2_000, mid="84796"))
    assert len(gone) == 1
    assert gone[0]["state"] == C.VANISH_CONSUMED_CANDIDATE
    assert gone[0]["reason"] == "MID_PATH_REACHED_THE_BIN"
    assert gone[0]["order_identity_proven"] is False


def test_a_wall_that_disappears_without_the_price_reaching_it_reads_cancel_like():
    tracker = F.VanishTracker()
    tracker.observe(liquidity(sample_ms=1_000, mid="84750",
                              walls=[("ASK", "84790", "84795", "900000", 1)]))
    gone = tracker.observe(liquidity(sample_ms=2_000, mid="84751"))
    assert gone[0]["state"] == C.VANISH_CANCEL_LIKE
    assert gone[0]["reason"] == "MID_PATH_NEVER_REACHED_THE_BIN"


@pytest.mark.parametrize("second,reason", [
    (dict(sample_ms=2_000, session="s2"), "SESSION_BOUNDARY"),
    (dict(sample_ms=2_000, book_state="UNSYNCED"), "BOOK_NOT_SYNCED"),
    (dict(sample_ms=1_000 + F.PATH_GAP_TOLERANCE_MS + 1),
     "PATH_GAP_WIDER_THAN_SAMPLE_INTERVAL"),
    (dict(sample_ms=1_000), "SAME_SAMPLE_AS_PREVIOUS_READING"),
])
def test_a_disappearance_the_panel_cannot_decide_reads_unknown(second, reason):
    tracker = F.VanishTracker()
    tracker.observe(liquidity(sample_ms=1_000, mid="84750",
                              walls=[("ASK", "84790", "84795", "900000", 1)]))
    gone = tracker.observe(liquidity(mid="84796", **second))
    assert gone[0]["state"] == C.UNKNOWN
    assert gone[0]["reason"] == reason
    assert gone[0]["mid_path_touched_bin"] is None


def test_a_generation_change_from_a_resnapshot_is_undecidable_rather_than_a_cancel():
    tracker = F.VanishTracker()
    tracker.observe(liquidity(sample_ms=1_000, mid="84750", generation=1,
                              walls=[("ASK", "84790", "84795", "900000", 1)]))
    gone = tracker.observe(liquidity(sample_ms=2_000, mid="84751", generation=2))
    assert gone[0]["state"] == C.UNKNOWN
    assert gone[0]["reason"] == "RESNAPSHOT_GENERATION_CHANGED"


def test_a_resnapshot_that_emptied_the_set_is_still_detected_from_the_collectors_state():
    """The case the row fallback cannot see: no wall survived, so no row carries the generation."""
    tracker = F.VanishTracker()
    tracker.observe(liquidity(sample_ms=1_000, mid="84750", generation=7,
                              walls=[("ASK", "84790", "84795", "900000", 7)]))
    gone = tracker.observe(liquidity(sample_ms=2_000, mid="84751", generation=8, walls=()))
    assert gone[0]["reason"] == "RESNAPSHOT_GENERATION_CHANGED"


def test_a_book_with_no_state_file_still_classifies_rather_than_reading_unknown_forever():
    tracker = F.VanishTracker()
    tracker.observe(liquidity(sample_ms=1_000, mid="84750",
                              walls=[("ASK", "84790", "84795", "900000", None)]))
    gone = tracker.observe(liquidity(sample_ms=2_000, mid="84751"))
    assert gone[0]["state"] == C.VANISH_CANCEL_LIKE


def test_the_first_reading_can_decide_nothing_and_says_so():
    tracker = F.VanishTracker()
    assert tracker.observe(liquidity(walls=[("ASK", "84790", "84795", "900000", 1)])) == []


def test_the_running_totals_are_published_so_one_quiet_reading_is_not_a_quiet_market():
    tracker = F.VanishTracker()
    tracker.observe(liquidity(sample_ms=1_000, mid="84750",
                              walls=[("ASK", "84790", "84795", "900000", 1)]))
    tracker.observe(liquidity(sample_ms=2_000, mid="84751"))
    view = F.flow_view(snapshot(), liquidity(sample_ms=3_000, mid="84752"), symbol=C.BTC,
                       tracker=tracker)
    totals = view["vanished"]["totals"]
    assert totals[C.VANISH_CANCEL_LIKE] == 1
    assert totals["observed_disappearances"] == 1
    assert view["vanish_base_rate"]["ask_touched_bin_pct"] == 3.8
    assert view["vanish_base_rate"]["bid_touched_bin_pct"] == 6.9


def test_the_tracker_memory_stays_bounded_however_long_the_panel_runs():
    tracker = F.VanishTracker()
    for index in range(400):
        tracker.observe(liquidity(sample_ms=1_000 + index * 1_000, mid="84750",
                                  walls=[("ASK", str(84_790 + index), str(84_795 + index),
                                          "900000", 1)]))
    assert len(tracker.bins) == 1
    assert len(tracker.path) == 1 and tracker.pending == []
    assert len(tracker.vanished) <= 20


def test_the_judged_path_excludes_the_sample_the_wall_was_last_seen_in():
    """R0's `mids[anchor+1 : j+1]`, which is the half-open end this implementation got wrong once.

    The anchor's own mid is inside the bin here. Including it would make every wall that was last
    seen while the price sat on it a consumption candidate, no matter what happened next.
    """
    tracker = F.VanishTracker()
    tracker.observe(liquidity(sample_ms=1_000, mid="84792",
                              walls=[("ASK", "84790", "84795", "900000", 1)]))
    gone = tracker.observe(liquidity(sample_ms=2_000, mid="84700"))
    assert tracker.path == [Decimal("84700")]
    assert gone[0]["state"] == C.VANISH_CANCEL_LIKE


def test_an_intermediate_sample_inside_the_bin_is_kept_by_the_path():
    """A caller sampling the book faster than it judges walls keeps the resolution R0 measured at."""
    tracker = F.VanishTracker()
    tracker.observe(liquidity(sample_ms=1_000, mid="84750",
                              walls=[("ASK", "84790", "84795", "900000", 1)]))
    tracker.note_mid(liquidity(sample_ms=1_500, mid="84796"))
    gone = tracker.observe(liquidity(sample_ms=2_000, mid="84751"))
    assert gone[0]["state"] == C.VANISH_CONSUMED_CANDIDATE
    assert gone[0]["path_samples"] == 2


# --- the absorption candidate ----------------------------------------------------------------------

def test_a_wall_that_held_while_more_than_its_own_size_hit_it_is_a_candidate():
    tracker = F.VanishTracker()
    wall = [("ASK", "84790", "84795", "90000", 1)]
    tracker.observe(liquidity(sample_ms=1_000, mid="84750", walls=wall))
    view = F.flow_view(snapshot(buy="120000", sell="10000"),
                       liquidity(sample_ms=2_000, mid="84751", walls=wall),
                       symbol=C.BTC, tracker=tracker)
    absorption = view["absorption"]
    assert absorption["state"] == C.ABSORPTION_CANDIDATE
    assert absorption["side"] == "ASK"
    assert absorption["order_identity_proven"] is False
    assert "CANDIDATE" in absorption["state"]


def test_flow_below_the_wall_notional_is_not_a_candidate():
    """Without the size comparison the row would fire in about half of all samples, because a
    near wall is present in 99% of them."""
    tracker = F.VanishTracker()
    wall = [("ASK", "84790", "84795", "900000", 1)]
    tracker.observe(liquidity(sample_ms=1_000, mid="84750", walls=wall))
    view = F.flow_view(snapshot(buy="120000", sell="10000"),
                       liquidity(sample_ms=2_000, mid="84751", walls=wall),
                       symbol=C.BTC, tracker=tracker)
    assert view["absorption"]["state"] == C.WALL_NONE
    assert view["absorption"]["reason"] == "AGGRESSIVE_FLOW_BELOW_WALL_NOTIONAL"


def test_a_wall_the_price_went_through_is_not_absorbing():
    tracker = F.VanishTracker()
    wall = [("ASK", "84790", "84795", "90000", 1)]
    tracker.observe(liquidity(sample_ms=1_000, mid="84750", walls=wall))
    view = F.flow_view(snapshot(buy="120000", sell="10000"),
                       liquidity(sample_ms=2_000, mid="84796", walls=wall),
                       symbol=C.BTC, tracker=tracker)
    assert view["absorption"]["reason"] == "MID_PATH_REACHED_THE_BIN"


def test_a_stale_journal_cannot_produce_an_absorption_candidate():
    tracker = F.VanishTracker()
    wall = [("ASK", "84790", "84795", "90000", 1)]
    tracker.observe(liquidity(sample_ms=1_000, mid="84750", walls=wall))
    view = F.flow_view(snapshot(buy="120000", sell="10000"),
                       liquidity(state=C.STALE, sample_ms=2_000, mid="84751", walls=wall),
                       symbol=C.BTC, tracker=tracker)
    assert view["absorption"]["state"] == C.WALL_NONE
    assert view["absorption"]["reason"] == "JOURNAL_OLDER_THAN_FRESHNESS_BOUND"


def test_an_incomplete_flow_window_cannot_produce_a_candidate():
    tracker = F.VanishTracker()
    wall = [("ASK", "84790", "84795", "90000", 1)]
    tracker.observe(liquidity(sample_ms=1_000, mid="84750", walls=wall))
    view = F.flow_view(snapshot(coverage="PARTIAL", buy="120000", sell="10000"),
                       liquidity(sample_ms=2_000, mid="84751", walls=wall),
                       symbol=C.BTC, tracker=tracker)
    assert view["absorption"]["reason"] == "FLOW_WINDOW_NOT_COMPLETE"


# --- symbols -------------------------------------------------------------------------------------

@pytest.mark.parametrize("symbol", ["ETHUSDT", "SOLUSDT"])
def test_a_non_btc_symbol_gets_no_flow_and_no_btc_figure(symbol):
    view = F.flow_view(snapshot(), liquidity(), symbol=symbol, tracker=F.VanishTracker())
    assert view["state"] == C.UNAVAILABLE
    assert view["reasons"] == [C.REASON_COLLECTOR_BTC_ONLY]
    for key in ("windows", "trade_stream", "vanished", "absorption"):
        assert view[key] is None, key
    assert "84750" not in str(view)


def test_the_aggressor_rule_is_the_contracts_own_restated_by_the_viewer():
    view = F.flow_view(snapshot(), liquidity(), symbol=C.BTC, tracker=F.VanishTracker())
    assert "buyer is maker" in view["aggressor_rule"]
    assert view["clock"] == "LOCAL_MONOTONIC_RECEIPT"
