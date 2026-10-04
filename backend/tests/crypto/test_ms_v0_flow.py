"""Trade normalization, deduplication and the 5/15/60 s flow windows.

Two of these tests guard against errors that produce a complete, plausible, inverted dataset:
`test_buyer_is_maker_means_an_aggressive_sell` (one boolean, every imbalance in the file flipped)
and the warmup tests (a window reported COMPLETE while it only saw part of its own span).
"""
from __future__ import annotations

from decimal import Decimal

from app.crypto.market_structure_v0 import flow as F
from app.crypto.market_structure_v0 import trades as T
from app.crypto.market_structure_v0.contract import COMPLETE, PARTIAL, UNKNOWN

from tests.crypto.ms_v0_fixtures import S, agg_trade


def trade(trade_id: int, *, second: float, qty: str = "1", sell: bool = False,
          price: str = "84750", first: int | None = None, last: int | None = None) -> T.Trade:
    mono = int(second * S)
    return T.Trade.parse(agg_trade(trade_id=trade_id, qty=qty, buyer_is_maker=sell, price=price,
                                   first=first, last=last, event_ms=1_000 + int(second * 1000)),
                         1_000 + int(second * 1000), mono)


def tape_with(*trades: T.Trade) -> tuple[T.TradeTape, F.FlowWindows]:
    tape, windows = T.TradeTape(), F.FlowWindows()
    windows.start_coverage(0)
    for item in trades:
        if tape.on_trade(item) != T.DUPLICATE:
            windows.add(item)
    return tape, windows


# --- normalization ----------------------------------------------------------------------------

def test_buyer_is_maker_means_an_aggressive_sell():
    """`m=true` says the buyer rested, so the seller crossed. Inverting this inverts the dataset."""
    assert trade(1, second=1, sell=True).aggressor == T.SELL
    assert trade(2, second=1, sell=False).aggressor == T.BUY


def test_the_aggregate_id_is_the_trade_id_and_the_individual_ids_are_kept():
    item = trade(7, second=1, first=100, last=118)
    payload = item.payload()
    assert payload["trade_id"] == 7
    assert payload["first_trade_id"] == 100 and payload["last_trade_id"] == 118
    # Measured live: one frame carried 19 individual fills. Counting frames undercounts trades.
    assert payload["individual_fills"] == 19
    assert payload["aggregate_stream"] is True


def test_notional_is_price_times_quantity_as_exact_decimal():
    item = trade(1, second=1, qty="0.003", price="84750.70")
    assert item.payload()["notional"] == "254.2521"


def test_quantities_never_pass_through_a_float():
    item = T.Trade.parse(agg_trade(trade_id=1, qty="0.1", price="0.3"), 1, 1)
    assert item.notional == Decimal("0.03")  # 0.1 * 0.3 as a float is 0.030000000000000002
    assert item.payload()["notional"] == "0.03"


# --- deduplication and ordering ---------------------------------------------------------------

def test_a_repeated_aggregate_id_is_a_duplicate_and_is_counted():
    tape = T.TradeTape()
    assert tape.on_trade(trade(10, second=1)) == T.ACCEPTED
    assert tape.on_trade(trade(10, second=2)) == T.DUPLICATE
    assert tape.on_trade(trade(9, second=3)) == T.DUPLICATE
    assert tape.counters()["duplicates"] == 2 and tape.counters()["accepted"] == 1


def test_a_skipped_aggregate_id_is_an_id_jump_and_still_counts_the_volume():
    """Measured live: `a` advanced by exactly 1 on 63 of 63 frames, so a skip means missed data."""
    tape, windows = T.TradeTape(), F.FlowWindows()
    windows.start_coverage(0)
    tape.on_trade(trade(1, second=1))
    jumped = trade(5, second=2)
    assert tape.on_trade(jumped) == T.ID_JUMP
    windows.add(jumped)
    assert tape.counters()["id_jumps"] == 1
    assert len(windows.tape) == 1


def test_exchange_time_going_backwards_is_recorded_without_dropping_volume():
    tape = T.TradeTape()
    tape.on_trade(trade(1, second=5))
    tape.on_trade(trade(2, second=1))
    assert tape.counters()["out_of_order_event_time"] == 1
    assert tape.counters()["accepted"] == 2


# --- windows ----------------------------------------------------------------------------------

def test_each_window_covers_its_own_span_only():
    _, windows = tape_with(trade(1, second=10, qty="1"), trade(2, second=50, qty="2"),
                           trade(3, second=58, qty="4"))
    view = windows.view(at_ns=60 * S, connected=True, age_ms=0)
    assert view["5s"]["buy_btc"] == "4"      # (55, 60]
    assert view["15s"]["buy_btc"] == "6"     # (45, 60]
    assert view["60s"]["buy_btc"] == "7"     # (0, 60]


def test_the_window_interval_is_half_open_so_the_boundary_trade_is_excluded():
    _, windows = tape_with(trade(1, second=55, qty="3"))
    view = windows.view(at_ns=60 * S, connected=True, age_ms=0)
    assert view["5s"]["trades"] == 0 and view["5s"]["buy_btc"] == "0"
    assert view["15s"]["trades"] == 1


def test_buy_and_sell_sides_net_and_normalize():
    _, windows = tape_with(trade(1, second=59, qty="3"), trade(2, second=59.5, qty="1", sell=True))
    view = windows.view(at_ns=60 * S, connected=True, age_ms=0)["5s"]
    assert view["buy_btc"] == "3" and view["sell_btc"] == "1"
    assert view["net_btc"] == "2"
    assert view["imbalance_btc"] == "0.5"  # (3-1)/(3+1)
    assert Decimal(view["buy_usdt"]) == Decimal("84750") * 3


def test_a_complete_window_with_no_trades_reports_zero_volume_and_a_null_ratio():
    _, windows = tape_with()
    view = windows.view(at_ns=60 * S, connected=True, age_ms=None)["5s"]
    assert view["coverage"] == COMPLETE
    assert view["buy_btc"] == "0" and view["sell_btc"] == "0" and view["net_btc"] == "0"
    assert view["imbalance_btc"] is None and view["imbalance_usdt"] is None


def test_trades_older_than_the_longest_window_are_pruned():
    _, windows = tape_with(trade(1, second=1), trade(2, second=2))
    windows.view(at_ns=120 * S, connected=True, age_ms=0)
    assert len(windows.tape) == 0


# --- coverage ---------------------------------------------------------------------------------

def test_warmup_makes_a_window_partial_until_coverage_reaches_back_its_whole_span():
    windows = F.FlowWindows()
    windows.start_coverage(10 * S)
    at = 20 * S  # 10 s of continuous coverage
    view = windows.view(at_ns=at, connected=True, age_ms=0)
    assert view["5s"]["coverage"] == COMPLETE
    assert view["15s"]["coverage"] == PARTIAL
    assert view["60s"]["coverage"] == PARTIAL
    assert view["60s"]["coverage_reason"] == F.WARMUP


def test_a_partial_window_withholds_canonical_values_but_exposes_what_was_seen():
    windows = F.FlowWindows()
    windows.start_coverage(10 * S)
    windows.add(trade(1, second=15, qty="2"))
    view = windows.view(at_ns=20 * S, connected=True, age_ms=0)["60s"]
    assert view["coverage"] == PARTIAL
    assert view["buy_btc"] is None and view["net_btc"] is None and view["imbalance_btc"] is None
    assert view["observed_buy_btc"] == "2" and view["observed_is_lower_bound"] is True
    assert view["trades"] == 1


def test_the_full_sixty_second_warmup_elapses_before_every_window_is_complete():
    windows = F.FlowWindows()
    windows.start_coverage(0)
    assert windows.view(at_ns=59 * S, connected=True, age_ms=0)["60s"]["coverage"] == PARTIAL
    assert windows.view(at_ns=60 * S, connected=True, age_ms=0)["60s"]["coverage"] == COMPLETE


def test_an_interruption_restarts_coverage_and_the_window_says_which_one():
    windows = F.FlowWindows()
    windows.start_coverage(0)
    windows.interrupt(100 * S, F.INTERRUPTION_ID_JUMP)
    view = windows.view(at_ns=101 * S, connected=True, age_ms=0)
    assert view["60s"]["coverage"] == PARTIAL
    assert view["60s"]["coverage_reason"] == F.INTERRUPTION_ID_JUMP
    assert windows.counters()["interruptions"] == 1


def test_a_disconnected_stream_is_unknown_with_nothing_observed():
    _, windows = tape_with(trade(1, second=59, qty="2"))
    view = windows.view(at_ns=60 * S, connected=False, age_ms=0)["5s"]
    assert view["coverage"] == UNKNOWN
    assert view["buy_btc"] is None and view["observed_buy_btc"] is None
    assert view["trades"] is None


def test_a_stale_stream_is_unknown_even_while_connected():
    _, windows = tape_with(trade(1, second=10))
    view = windows.view(at_ns=60 * S, connected=True, age_ms=50_000)
    assert view["5s"]["coverage"] == UNKNOWN


def test_silence_before_the_first_trade_is_warmup_and_not_staleness():
    """A quiet market sends nothing; at the measured 0.65 trades/s that is the normal case."""
    windows = F.FlowWindows()
    windows.start_coverage(0)
    view = windows.view(at_ns=70 * S, connected=True, age_ms=None)
    assert view["60s"]["coverage"] == COMPLETE


def test_losing_the_connection_removes_coverage_entirely():
    windows = F.FlowWindows()
    windows.start_coverage(0)
    windows.lose_coverage(F.INTERRUPTION_RECONNECT)
    assert windows.counters()["has_coverage"] is False
    assert windows.view(at_ns=60 * S, connected=True, age_ms=0)["5s"]["coverage"] == UNKNOWN


def test_the_flow_tape_is_bounded_and_an_overflow_invalidates_coverage():
    windows = F.FlowWindows(max_records=3)
    windows.start_coverage(0)
    for index in range(5):
        windows.add(trade(index + 1, second=10 + index * 0.1))
    assert len(windows.tape) == 3
    assert windows.counters()["overflows"] == 2
    assert windows.counters()["coverage_reason"] == F.INTERRUPTION_OVERFLOW
    assert windows.view(at_ns=11 * S, connected=True, age_ms=0)["5s"]["coverage"] == PARTIAL


def test_a_reconnect_restarts_the_silence_clock_but_keeps_the_dedupe_memory():
    """Freshness is per connection; deduplication is per process. The contract splits them."""
    tape = T.TradeTape()
    tape.on_trade(trade(10, second=1))
    assert tape.age_ms(90 * S) == 89_000
    tape.on_connect()
    assert tape.age_ms(90 * S) is None          # this socket has delivered nothing yet
    assert tape.on_trade(trade(10, second=91)) == T.DUPLICATE
    assert tape.high_water_id == 10
