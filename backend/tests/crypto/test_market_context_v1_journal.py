"""The forward context journal: append-only, per symbol, and a gap is a record rather than a silence.

The property that decides whether a later study can trust this tape is the last one. If a minute
whose liquidity layer was `STALE` were skipped, the tape would quietly become a survivorship sample
of the market's calm minutes - and the Q1-Q3 re-measurement it exists for would be wrong in a way
nobody could see from the file.
"""
from __future__ import annotations

import json
import time

import pytest

from app.crypto.market_context_v1 import contract as C
from app.crypto.market_context_v1 import journal as J

NOW = 1_791_000_000_000


def payload(*, symbol="BTCUSDT", bar_close_ms=NOW, breakout="UP", retest="BROKEN",
            level_id=16, wall_ask=C.WALL_OK, liquidity_state=C.LIVE, flow_state=C.LIVE):
    level = lambda side, available, price: {
        "side": side, "price": price if available else None,
        "distance_pct": -0.5 if side == "SUPPORT" else 0.4,
        "lifecycle": "CONFIRMED", "origin_kind": "SWING_HIGH", "strength": 9, "touches": 6,
        "confirm_tf": "1h", "sources": "1h", "level_id": level_id, "available": available,
        "unavailable_reason": None if available else "NO_PUBLISHED_LEVEL_ON_THIS_SIDE"}
    return {
        "panel_version": "mc-v1.0",
        "symbol": symbol,
        "layers": {
            "PRICE": {
                "state": C.LIVE, "reasons": [],
                "series": {"anchor_ms": NOW - 30 * 86_400_000, "series_rows": 43_200,
                           "repaint_violations": 0, "vanished_levels": 0},
                "row": {"bar_open_ms": bar_close_ms - 60_000, "bar_close_ms": bar_close_ms,
                        "close": 86_000.0, "trend_structure": "BULLISH",
                        "active_level_count": 6, "atr_5m": 120.0, "atr_1h": 310.0},
                "levels": {"resistance": level("RESISTANCE", True, 86_400.0),
                           "support": level("SUPPORT", True, 85_600.0)},
                "events": {"breakout_state": breakout, "breakout_level_id": level_id,
                           "breakout_age_min": 3, "retest_state": retest,
                           "failed_break_state": "NONE"},
                "range": {"state": "NO_RANGE", "high": None, "low": None, "position": None},
                "prev_session": {"high": 86_770.0, "low": 84_677.3, "in_level_book": False},
            },
            "LIQUIDITY": {
                "state": liquidity_state, "reasons": [],
                "sides": {
                    "ASK": {"wall_state": wall_ask, "wall_state_reason": None,
                            "nearest_wall": {"price": "86100", "notional_usdt": "900000",
                                             "distance_bps": "11.6", "persistence_ms": 60_000,
                                             "persistence_source": "OWN",
                                             "continuity_status": "NEW", "carried_members": 0,
                                             "bin_low": "86100", "bin_high": "86105",
                                             "coverage": "COMPLETE"},
                            "depth": [{"band_pct": "0.1", "coverage": "COMPLETE",
                                       "observed_notional": "1000", "is_lower_bound": False}]},
                    "BID": {"wall_state": C.WALL_NONE,
                            "wall_state_reason": "NO_CANDIDATE_QUALIFIES_UNDER_LM_WALL_V2",
                            "nearest_wall": None,
                            "depth": [{"band_pct": "0.1", "coverage": "PARTIAL",
                                       "observed_notional": "900", "is_lower_bound": True}]},
                },
                "book": {"mid": "86000.5", "observed_low": "85900", "observed_high": "86100",
                         "observed_symmetric_pct": "0.11"},
                "coverage": {"partial_bands": ["0.25", "0.5", "1"]},
            },
            "FLOW": {
                "state": flow_state, "reasons": [],
                "windows": {label: {"buy_usdt": "1000", "sell_usdt": "900", "net_usdt": "100",
                                    "imbalance_usdt": "0.05", "trades": 10,
                                    "coverage": "COMPLETE", "state": flow_state}
                            for label in C.FLOW_WINDOWS},
                "trade_stream": {"connected": True, "age_ms": 100},
                "vanished": {"this_reading": []},
                "absorption": {"state": C.WALL_NONE, "reason": "x"},
            },
            "DERIVATIVES": {
                "state": C.LIVE, "reasons": [],
                "open_interest": {"base": "98500.0", "venue_time_ms": NOW},
                "open_interest_change": {"windows": {label: {"base": "1.0"}
                                                     for label in C.OI_CHANGE_WINDOWS}},
                "funding": {"last_rate": "0.00006", "next_funding_time_ms": NOW + 3_600_000},
                "basis": {"mark_price": "86000.5", "index_price": "86010.5",
                          "basis_usdt": "-10.0", "premium_pct": "-0.0116",
                          "venue_time_ms": NOW},
            },
            "AUXILIARY": {"state": C.LIVE, "reasons": []},
        },
    }


# --- the record shape ------------------------------------------------------------------------------

def test_every_field_the_contract_requires_is_in_the_record():
    line = J.record(payload(), written_at_ms=NOW)
    assert line["record"] == C.JOURNAL_RECORD
    assert line["contract"] == C.CONTRACT_VERSION
    assert line["written_at_ms"] == NOW and line["symbol"] == "BTCUSDT"
    assert line["price"]["last_closed_close"] == 86_000.0
    assert line["price"]["mark_price"] == "86000.5"
    nearest = line["structure"]["nearest"]
    assert nearest["side"] in ("SUPPORT", "RESISTANCE")
    assert nearest["type"] == "CONFIRMED|SWING_HIGH"
    assert nearest["distance_pct"] is not None
    assert line["structure"]["breakout_state"] == "UP"
    assert line["structure"]["retest_state"] == "BROKEN"
    assert line["wall"]["ask"]["state"] == C.WALL_OK
    assert line["wall"]["ask"]["persistence_ms"] == 60_000
    assert line["wall"]["ask"]["persistence_source"] == "OWN"
    assert line["wall"]["bid"]["state"] == C.WALL_NONE
    for label in C.FLOW_WINDOWS:
        assert line["flow"][f"w{label}"]["buy_usdt"] == "1000"
    assert line["depth"]["observed_symmetric_pct"] == "0.11"
    assert line["coverage"]["partial_bands"] == ["0.25", "0.5", "1"]
    assert line["derivatives"]["open_interest"] == "98500.0"
    assert line["derivatives"]["oi_change_4h"] == "1.0"
    assert line["derivatives"]["funding_rate"] == "0.00006"
    assert line["derivatives"]["basis_usdt"] == "-10.0"
    assert line["derivatives"]["premium_pct"] == "-0.0116"
    assert set(line["quality"]) == set(C.LAYERS)


def test_the_nearest_level_is_resolved_and_the_other_side_is_kept():
    """A reader with only the nearer level cannot tell a pinned price from one in open space."""
    line = J.record(payload(), written_at_ms=NOW)
    nearest = line["structure"]["nearest"]
    assert nearest["available"] is True
    assert nearest["other_side"] is not None
    assert nearest["other_side"]["side"] != nearest["side"]


def test_no_published_level_is_named_rather_than_left_as_a_null():
    body = payload()
    for side in ("resistance", "support"):
        body["layers"]["PRICE"]["levels"][side]["available"] = False
        body["layers"]["PRICE"]["levels"][side]["price"] = None
    line = J.record(body, written_at_ms=NOW)
    assert line["structure"]["nearest"]["available"] is False
    assert line["structure"]["nearest"]["unavailable_reason"] == "NO_PUBLISHED_LEVEL"


def test_the_record_is_not_linked_to_an_order_and_holds_no_decision():
    """Section 10: a context tape, not a trade journal. Checked against the keys only a trade
    record has, rather than against loose words - `range.position` is where the price sits inside
    its range, which is structure, and a ban broad enough to catch it would have to be deleted."""
    text = json.dumps(J.record(payload(), written_at_ms=NOW))
    for banned in ("order_id", "client_order_id", "trade_ref", "position_qty", "position_side",
                   "realized_pnl", "unrealized_pnl", "entry_price", "exit_price", "leverage",
                   "used_margin", "signal_id", "attribution"):
        assert banned not in text, banned


# --- append-only -----------------------------------------------------------------------------------

def test_a_record_is_appended_and_the_file_is_never_rewritten(tmp_path):
    writer = J.ForwardJournal(tmp_path)
    assert writer.append(J.record(payload(), written_at_ms=NOW), written_at_ms=NOW) is True
    path = J.day_file(tmp_path, NOW)
    first = path.read_bytes()
    body = payload(bar_close_ms=NOW + 60_000)
    assert writer.append(J.record(body, written_at_ms=NOW + 60_000),
                         written_at_ms=NOW + 60_000) is True
    second = path.read_bytes()
    assert second.startswith(first)
    assert len(J.read_records(path)) == 2


def test_a_day_boundary_opens_a_new_file_and_leaves_the_old_one_alone(tmp_path):
    writer = J.ForwardJournal(tmp_path)
    writer.append(J.record(payload(), written_at_ms=NOW), written_at_ms=NOW)
    later = NOW + 86_400_000
    writer.append(J.record(payload(bar_close_ms=later), written_at_ms=later),
                  written_at_ms=later)
    files = sorted(path.name for path in tmp_path.glob("context-*.jsonl"))
    assert len(files) == 2, files
    assert all(len(J.read_records(tmp_path / name)) == 1 for name in files)


def test_a_torn_final_line_is_dropped_by_the_reader_rather_than_repaired(tmp_path):
    writer = J.ForwardJournal(tmp_path)
    writer.append(J.record(payload(), written_at_ms=NOW), written_at_ms=NOW)
    path = J.day_file(tmp_path, NOW)
    with open(path, "a") as handle:
        handle.write('{"record": "MC_V1_FORWARD_CONT')
    assert len(J.read_records(path)) == 1


def test_an_unconfigured_root_means_the_journal_is_off_and_says_so():
    writer = J.ForwardJournal(None)
    assert writer.enabled is False
    assert writer.append(J.record(payload(), written_at_ms=NOW), written_at_ms=NOW) is False
    view = writer.view()
    assert view["enabled"] is False and view["root"] is None
    assert view["root_env"] == "MC_V1_JOURNAL_ROOT"


def test_a_write_failure_is_reported_rather_than_raised(tmp_path, monkeypatch):
    writer = J.ForwardJournal(tmp_path / "blocked")

    def refuse(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(J.Path, "mkdir", refuse)
    assert writer.append(J.record(payload(), written_at_ms=NOW), written_at_ms=NOW) is False
    assert "read-only filesystem" in (writer.last_error or "")


# --- cadence ---------------------------------------------------------------------------------------

def test_a_closed_minute_earns_a_record_and_an_unchanged_reading_does_not(tmp_path):
    writer = J.ForwardJournal(tmp_path)
    body = J.record(payload(), written_at_ms=NOW)
    assert writer.append(body, written_at_ms=NOW) is True
    assert writer.append(body, written_at_ms=NOW + 500) is False
    assert writer.skipped_duplicate == 1
    later = J.record(payload(bar_close_ms=NOW + 60_000), written_at_ms=NOW + 60_000)
    assert writer.append(later, written_at_ms=NOW + 60_000) is True
    assert J.read_records(J.day_file(tmp_path, NOW))[-1]["written_because"] == \
        "NEW_MINUTE_BAR_CLOSE"


@pytest.mark.parametrize("change,expected", [
    (dict(breakout="DOWN"), "CONTEXT_CHANGE"),
    (dict(retest="PENDING"), "CONTEXT_CHANGE"),
    (dict(level_id=99), "CONTEXT_CHANGE"),
    (dict(wall_ask=C.WALL_NONE), "CONTEXT_CHANGE"),
    (dict(liquidity_state=C.STALE), "CONTEXT_CHANGE"),
    (dict(flow_state=C.STALE), "CONTEXT_CHANGE"),
])
def test_a_change_in_an_anchor_field_earns_a_line_between_closed_minutes(tmp_path, change,
                                                                        expected):
    writer = J.ForwardJournal(tmp_path)
    writer.append(J.record(payload(), written_at_ms=NOW), written_at_ms=NOW)
    changed = J.record(payload(**change), written_at_ms=NOW + 1_000)
    assert writer.append(changed, written_at_ms=NOW + 1_000) is True
    assert J.read_records(J.day_file(tmp_path, NOW))[-1]["written_because"] == expected


def test_a_figure_moving_is_not_a_change_because_every_figure_moves_every_second(tmp_path):
    writer = J.ForwardJournal(tmp_path)
    writer.append(J.record(payload(), written_at_ms=NOW), written_at_ms=NOW)
    body = payload()
    body["layers"]["LIQUIDITY"]["book"]["mid"] = "86001.5"
    body["layers"]["FLOW"]["windows"]["5s"]["buy_usdt"] = "999999"
    assert writer.append(J.record(body, written_at_ms=NOW + 1_000),
                         written_at_ms=NOW + 1_000) is False


def test_each_symbol_keeps_its_own_cursor(tmp_path):
    """A shared cursor would make every tab switch look like a context change, and would let one
    symbol's unchanged reading suppress another's."""
    writer = J.ForwardJournal(tmp_path)
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        assert writer.append(J.record(payload(symbol=symbol), written_at_ms=NOW),
                             written_at_ms=NOW) is True
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        assert writer.append(J.record(payload(symbol=symbol), written_at_ms=NOW + 500),
                             written_at_ms=NOW + 500) is False
    assert writer.written == 3 and writer.skipped_duplicate == 3
    assert sorted(writer.cursors) == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_a_symbol_with_no_structure_layer_still_keeps_a_minute_cadence(tmp_path):
    """Otherwise the tape would hold exactly one line for ETH and SOL for the whole run."""
    writer = J.ForwardJournal(tmp_path)
    body = payload(symbol="ETHUSDT")
    body["layers"]["PRICE"] = {"state": C.UNAVAILABLE, "reasons": [], "row": None,
                               "levels": None, "events": None, "range": None,
                               "prev_session": None, "series": None}
    first = J.record(body, written_at_ms=NOW)
    assert first["price"]["bar_close_ms"] is None
    assert writer.append(first, written_at_ms=NOW) is True
    assert writer.append(J.record(body, written_at_ms=NOW + 1_000),
                         written_at_ms=NOW + 1_000) is False
    assert writer.append(J.record(body, written_at_ms=NOW + 60_000),
                         written_at_ms=NOW + 60_000) is True
    assert J.read_records(J.day_file(tmp_path, NOW))[-1]["written_because"] == \
        "NEW_MINUTE_WALL_CLOCK_MINUTE"


# --- a gap is a record -----------------------------------------------------------------------------

def test_a_minute_whose_liquidity_was_stale_is_written_not_skipped(tmp_path):
    writer = J.ForwardJournal(tmp_path)
    body = payload(liquidity_state=C.STALE, flow_state=C.STALE, wall_ask=C.STALE)
    line = J.record(body, written_at_ms=NOW)
    assert writer.append(line, written_at_ms=NOW) is True
    written = J.read_records(J.day_file(tmp_path, NOW))[0]
    assert written["quality"]["LIQUIDITY"]["state"] == C.STALE
    assert written["wall"]["ask"]["state"] == C.STALE


def test_an_unknown_layer_is_written_with_its_reason_rather_than_as_a_zero(tmp_path):
    writer = J.ForwardJournal(tmp_path)
    body = payload()
    body["layers"]["LIQUIDITY"] = {"state": C.UNKNOWN, "reasons": ["MS_V0_ROOT_NOT_SET"],
                                   "sides": None, "book": None, "coverage": None}
    line = J.record(body, written_at_ms=NOW)
    assert writer.append(line, written_at_ms=NOW) is True
    written = J.read_records(J.day_file(tmp_path, NOW))[0]
    assert written["quality"]["LIQUIDITY"]["reasons"] == ["MS_V0_ROOT_NOT_SET"]
    assert written["wall"]["ask"]["state"] == C.UNKNOWN
    assert written["wall"]["ask"]["price"] is None
    assert written["price"]["book_mid"] is None


def test_the_panel_never_reads_the_tape_back():
    view = J.ForwardJournal(None).view()
    assert view["read_back_by_the_panel"] is False
    assert view["linked_to_orders"] is False
