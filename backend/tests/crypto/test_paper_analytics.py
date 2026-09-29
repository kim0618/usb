"""Performance analytics folded out of the ledger."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.crypto.paper.analytics import (
    AUTO, build_trades, drawdown, longest_losing_streak, reconcile, summarize,
)
from app.crypto.paper.engine import PaperEngine
from tests.crypto.conftest import make_config, quote, started_engine

D = Decimal


def round_trip(engine: PaperEngine, *, side: str, qty: str, open_ts: int, close_ts: int,
               open_bid: str, open_ask: str, close_bid: str, close_ask: str) -> None:
    engine.apply_market(quote(open_ts - 10, bid=open_bid, ask=open_ask))
    engine.submit_order(ts_ms=open_ts, side=side, qty=D(qty), intent="OPEN",
                        request_id=f"o{open_ts}")
    engine.apply_market(quote(close_ts - 10, bid=close_bid, ask=close_ask))
    engine.submit_order(ts_ms=close_ts, side=side, qty=D(qty), intent="CLOSE",
                        request_id=f"c{close_ts}")


def two_trade_engine(tiers, *, taker: str = "0") -> PaperEngine:
    engine = started_engine(make_config(taker=taker), tiers)
    round_trip(engine, side="LONG", qty="0.010", open_ts=1_100, close_ts=2_100,
               open_bid="100000.0", open_ask="100000.0", close_bid="100500.0", close_ask="100500.0")
    round_trip(engine, side="SHORT", qty="0.010", open_ts=3_100, close_ts=4_100,
               open_bid="100500.0", open_ask="100500.0", close_bid="100800.0", close_ask="100800.0")
    return engine


# ------------------------------------------------------------------ trades

def test_a_flat_to_flat_round_trip_becomes_one_trade(tiers) -> None:
    engine = two_trade_engine(tiers)
    trades = build_trades(engine.ledger.events)
    assert [trade.side for trade in trades] == ["LONG", "SHORT"]
    assert trades[0].entry_price == D("100000.0") and trades[0].exit_price == D("100500.0")
    assert trades[0].gross_pnl == D("5.000") and trades[0].is_win
    assert trades[1].gross_pnl == D("-3.000") and not trades[1].is_win
    assert all(trade.origin == "MANUAL" for trade in trades)


def test_scaling_in_and_partially_closing_is_still_one_trade(tiers) -> None:
    engine = started_engine(make_config(taker="0"), tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o1")
    engine.apply_market(quote(1_500, bid="100200.0", ask="100200.0"))
    engine.submit_order(ts_ms=1_600, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o2")
    engine.apply_market(quote(2_000, bid="100600.0", ask="100600.0"))
    engine.submit_order(ts_ms=2_100, side="LONG", qty=D("0.008"), intent="CLOSE", request_id="p1")
    engine.submit_order(ts_ms=2_200, side="LONG", qty=D("0.012"), intent="CLOSE", request_id="c1")

    trades = build_trades(engine.ledger.events)
    assert len(trades) == 1
    assert trades[0].exits == 2
    assert trades[0].qty == D("0.020")
    # Weighted average of the two entry fills: ask 100000.1 then 100200.0.
    assert trades[0].entry_price == (D("100000.1") + D("100200.0")) / 2
    assert trades[0].gross_pnl == (D("100600.0") - trades[0].entry_price) * D("0.020")


def test_a_trade_carries_its_hold_time_and_both_excursions(tiers) -> None:
    engine = started_engine(make_config(taker="0"), tiers)
    engine.submit_order(ts_ms=1_000, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    engine.apply_market(quote(2_000, bid="99000.0", ask="99000.0"))   # against us
    engine.apply_market(quote(3_000, bid="102000.0", ask="102000.0")) # for us
    engine.submit_order(ts_ms=4_000, side="LONG", qty=D("0.010"), intent="CLOSE", request_id="c")

    trade = build_trades(engine.ledger.events)[0]
    assert trade.hold_ms == 3_000
    assert trade.max_adverse_excursion == (D("99000.0") - D("100000.1")) * D("0.010")
    assert trade.max_favourable_excursion == (D("102000.0") - D("100000.1")) * D("0.010")
    assert trade.max_adverse_excursion < 0 < trade.max_favourable_excursion


def test_a_liquidated_round_trip_is_labelled_as_such(tiers) -> None:
    engine = started_engine(make_config(leverage="100", taker="0"), tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    engine.apply_market(quote(2_000, bid="50000.0", ask="50000.1", mark="50000.0"))
    trade = build_trades(engine.ledger.events)[0]
    assert trade.liquidated is True and trade.origin == "LIQUIDATION"
    assert trade.net_pnl < 0


def test_an_emergency_exit_is_distinguished_from_an_ordinary_close(tiers) -> None:
    engine = started_engine(make_config(taker="0"), tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    engine.set_mode(ts_ms=1_200, action="EMERGENCY_ON", confirmed=True)
    assert build_trades(engine.ledger.events)[0].origin == "EMERGENCY"


def test_an_open_position_is_not_counted_as_a_trade_yet(tiers) -> None:
    engine = started_engine(make_config(taker="0"), tiers)
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o")
    assert build_trades(engine.ledger.events) == []


# ------------------------------------------------------------------ summary

def test_the_summary_reports_every_required_figure(tiers) -> None:
    engine = two_trade_engine(tiers, taker="0.0006")
    summary = summarize(engine.ledger.events,
                        starting_capital=engine.account.starting_capital_usdt)
    for key in ("trades", "wins", "losses", "win_rate", "gross_pnl", "fees", "funding",
                "net_pnl", "avg_win", "avg_loss", "expectancy", "profit_factor",
                "max_drawdown", "longest_losing_streak", "hold_ms_median",
                "max_adverse_excursion", "max_favourable_excursion", "by_leverage", "by_origin"):
        assert key in summary, key
    assert summary["trades"] == 2 and summary["wins"] == 1 and summary["losses"] == 1
    assert Decimal(summary["win_rate"]) == D("0.5")


def test_every_fee_lands_on_a_trade_including_the_one_charged_at_entry(tiers) -> None:
    """The opening fee is written before POSITION_OPEN. If the fold misses it, per-trade net
    quietly overstates by half the round trip's cost."""
    engine = two_trade_engine(tiers, taker="0.0006")
    trades = build_trades(engine.ledger.events)
    summary = summarize(engine.ledger.events, starting_capital=D("1000"))
    assert sum((trade.fees for trade in trades), D(0)) == Decimal(summary["fees"])
    assert all(trade.fees > 0 for trade in trades)
    assert all(trade.exits == 1 for trade in trades)


def test_net_pnl_is_gross_minus_fees_minus_funding_and_nothing_else(tiers) -> None:
    engine = two_trade_engine(tiers, taker="0.0006")
    summary = summarize(engine.ledger.events,
                        starting_capital=engine.account.starting_capital_usdt)
    assert (Decimal(summary["net_pnl"])
            == Decimal(summary["gross_pnl"]) - Decimal(summary["fees"]) - Decimal(summary["funding"]))
    # And that net equals what the account actually holds.
    account = engine.account
    assert Decimal(summary["ending_equity"]) == account.wallet_balance


def test_the_summary_reconciles_against_the_account_the_engine_carries(tiers) -> None:
    engine = two_trade_engine(tiers, taker="0.0006")
    account = engine.account
    result = reconcile(engine.ledger.events, starting_capital=account.starting_capital_usdt,
                       account_realized=account.realized_pnl, account_fees=account.cumulative_fees,
                       account_funding=account.cumulative_funding_paid)
    assert result["gross_pnl_matches"] and result["fees_match"] and result["funding_matches"]


def test_a_zero_ratio_prints_as_zero_not_as_an_exponent(tiers) -> None:
    """Decimal division returns 0E+10 for an exact zero, which reads like a magnitude."""
    engine = two_trade_engine(tiers, taker="0")
    # Force a run where nothing won: both round trips above are one win and one loss, so use
    # the loss-only path by closing the winner back out at a worse price.
    losing = started_engine(make_config(taker="0"), tiers)
    round_trip(losing, side="LONG", qty="0.010", open_ts=1_100, close_ts=2_100,
               open_bid="100000.0", open_ask="100000.0", close_bid="99500.0", close_ask="99500.0")
    summary = summarize(losing.ledger.events, starting_capital=D("1000"))
    assert summary["profit_factor"] == "0"
    assert "E" not in summary["profit_factor"]
    assert summary["win_rate"] == "0"
    assert "E" not in str(summary["max_drawdown_fraction"])


def test_profit_factor_is_none_when_nothing_has_lost_yet(tiers) -> None:
    engine = started_engine(make_config(taker="0"), tiers)
    round_trip(engine, side="LONG", qty="0.010", open_ts=1_100, close_ts=2_100,
               open_bid="100000.0", open_ask="100000.0", close_bid="100500.0", close_ask="100500.0")
    summary = summarize(engine.ledger.events, starting_capital=D("1000"))
    assert summary["profit_factor"] is None
    assert summary["avg_loss"] is None and summary["losses"] == 0


def test_an_empty_ledger_reports_zeros_rather_than_failing(tiers) -> None:
    summary = summarize([], starting_capital=D("744"))
    assert summary["trades"] == 0 and summary["win_rate"] is None
    assert summary["max_drawdown"] == "0" and summary["ending_equity"] == "744"
    assert summary["by_origin"] == {} and summary["by_leverage"] == {}


def test_drawdown_is_measured_from_the_running_peak(tiers) -> None:
    worst, fraction = drawdown([D("100"), D("120"), D("90"), D("110"), D("60")])
    assert worst == D("60")                       # 120 -> 60
    assert fraction == D("60") / D("120")
    assert drawdown([]) == (D(0), None)
    assert drawdown([D("100"), D("110")]) == (D(0), None)


def test_the_losing_streak_counts_consecutive_losses_only(tiers) -> None:
    class Fake:
        def __init__(self, win: bool) -> None: self.is_win = win
    pattern = [True, False, False, True, False, False, False, True]
    assert longest_losing_streak([Fake(win) for win in pattern]) == 3
    assert longest_losing_streak([]) == 0


# ------------------------------------------------------------------ grouping

def test_results_can_be_split_by_leverage_side_and_origin(tiers) -> None:
    engine = started_engine(make_config(taker="0", leverage="10"), tiers)
    round_trip(engine, side="LONG", qty="0.010", open_ts=1_100, close_ts=2_100,
               open_bid="100000.0", open_ask="100000.0", close_bid="100500.0", close_ask="100500.0")
    engine.set_leverage(ts_ms=2_200, leverage=D("25"))
    round_trip(engine, side="SHORT", qty="0.010", open_ts=3_100, close_ts=4_100,
               open_bid="100500.0", open_ask="100500.0", close_bid="100800.0", close_ask="100800.0")
    summary = summarize(engine.ledger.events, starting_capital=D("1000"))
    assert set(summary["by_leverage"]) == {"10", "25"}
    assert summary["by_leverage"]["10"]["wins"] == 1
    assert summary["by_leverage"]["25"]["wins"] == 0
    assert set(summary["by_side"]) == {"LONG", "SHORT"}


def test_auto_is_a_schema_bucket_that_d4_never_fills(tiers) -> None:
    """The field exists so D5 can use it. A run with no AUTO trades must not invent the bucket."""
    engine = two_trade_engine(tiers)
    summary = summarize(engine.ledger.events, starting_capital=D("1000"))
    assert AUTO not in summary["by_origin"]
    assert set(summary["by_origin"]) == {"MANUAL"}
    trades = build_trades(engine.ledger.events)
    assert all(hasattr(trade, "origin") for trade in trades)


def test_funding_is_attributed_to_the_trade_that_was_open_when_it_settled(tiers) -> None:
    settlement = 8 * 60 * 60 * 1000
    engine = started_engine(make_config(taker="0"), tiers, ts_ms=settlement - 2_000,
                            first_quote=quote(settlement - 2_000, funding_rate="0.0001"))
    engine.submit_order(ts_ms=settlement - 1_500, side="LONG", qty=D("0.010"), intent="OPEN",
                        request_id="o")
    engine.apply_market(quote(settlement + 1_000, funding_rate="0.0001"))
    engine.submit_order(ts_ms=settlement + 2_000, side="LONG", qty=D("0.010"), intent="CLOSE",
                        request_id="c")
    trade = build_trades(engine.ledger.events)[0]
    assert trade.funding > 0
    assert trade.net_pnl == trade.gross_pnl - trade.fees - trade.funding
    summary = summarize(engine.ledger.events, starting_capital=D("1000"))
    assert Decimal(summary["funding"]) == trade.funding


def test_ending_equity_matches_the_wallet_even_while_a_position_is_still_open(tiers) -> None:
    """The bug this pins: the equity path only moves on a *closed* trade, but `net_pnl` counts
    every fee. With a position still open the two disagreed, and the summary reported an
    equity the account had never held."""
    engine = started_engine(make_config(taker="0.0006"), tiers)
    round_trip(engine, side="LONG", qty="0.010", open_ts=1_100, close_ts=2_100,
               open_bid="100000.0", open_ask="100000.0", close_bid="100500.0", close_ask="100500.0")
    # Now open a second position and leave it open, so its entry fee is spent but unattributed.
    engine.apply_market(quote(3_000, bid="100500.0", ask="100500.0"))
    engine.submit_order(ts_ms=3_100, side="SHORT", qty=D("0.010"), intent="OPEN", request_id="open")

    summary = summarize(engine.ledger.events,
                        starting_capital=engine.account.starting_capital_usdt)
    assert summary["has_open_position"] is True
    assert Decimal(summary["open_position_fees"]) > 0
    assert Decimal(summary["ending_equity"]) == engine.account.wallet_balance
    # And the two published figures agree with each other, which is what broke.
    assert (Decimal(summary["ending_equity"])
            == engine.account.starting_capital_usdt + Decimal(summary["net_pnl"]))
    # The closed-trade curve is still available, and it is the higher of the two.
    assert Decimal(summary["closed_trade_equity"]) > Decimal(summary["ending_equity"])


def test_reconcile_checks_the_wallet_not_only_the_three_totals(tiers) -> None:
    engine = started_engine(make_config(taker="0.0006"), tiers)
    round_trip(engine, side="LONG", qty="0.010", open_ts=1_100, close_ts=2_100,
               open_bid="100000.0", open_ask="100000.0", close_bid="100500.0", close_ask="100500.0")
    engine.apply_market(quote(3_000, bid="100500.0", ask="100500.0"))
    engine.submit_order(ts_ms=3_100, side="SHORT", qty=D("0.010"), intent="OPEN", request_id="open")
    account = engine.account
    result = reconcile(engine.ledger.events, starting_capital=account.starting_capital_usdt,
                       account_realized=account.realized_pnl, account_fees=account.cumulative_fees,
                       account_funding=account.cumulative_funding_paid)
    assert result["wallet_matches"] is True
    assert Decimal(result["account_wallet_balance"]) == account.wallet_balance


def test_a_flat_run_leaves_the_two_equity_figures_identical(tiers) -> None:
    engine = two_trade_engine(tiers, taker="0.0006")
    summary = summarize(engine.ledger.events,
                        starting_capital=engine.account.starting_capital_usdt)
    assert summary["has_open_position"] is False
    assert summary["ending_equity"] == summary["closed_trade_equity"]
    assert Decimal(summary["ending_equity"]) == engine.account.wallet_balance


# ------------------------------------------------------------------ D4 additions

def test_payoff_ratio_is_average_win_over_average_loss(tiers) -> None:
    from tests.crypto.conftest import make_config, quote, started_engine
    engine = started_engine(make_config(taker="0"), tiers, first_quote=quote(1_000))
    # One winner and one loser, so both averages exist.
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o1")
    engine.apply_market(quote(1_200, bid="100200.0", ask="100200.1"))
    engine.submit_order(ts_ms=1_300, side="LONG", qty=D("0.010"), intent="CLOSE", request_id="c1")
    engine.submit_order(ts_ms=1_400, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o2")
    engine.apply_market(quote(1_500, bid="100100.0", ask="100100.1"))
    engine.submit_order(ts_ms=1_600, side="LONG", qty=D("0.010"), intent="CLOSE", request_id="c2")

    summary = summarize(engine.ledger.events, starting_capital=D("1000"))
    expected = Decimal(summary["avg_win"]) / -Decimal(summary["avg_loss"])
    assert Decimal(summary["payoff_ratio"]) == expected


def test_payoff_ratio_is_absent_rather_than_infinite_when_nothing_has_lost(tiers) -> None:
    from tests.crypto.conftest import make_config, quote, started_engine
    engine = started_engine(make_config(taker="0"), tiers, first_quote=quote(1_000))
    engine.submit_order(ts_ms=1_100, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o1")
    engine.apply_market(quote(1_200, bid="100200.0", ask="100200.1"))
    engine.submit_order(ts_ms=1_300, side="LONG", qty=D("0.010"), intent="CLOSE", request_id="c1")
    # Dividing by nothing is not an infinite payoff, it is an unanswered question.
    assert summarize(engine.ledger.events, starting_capital=D("1000"))["payoff_ratio"] is None


def test_mean_hold_time_is_reported_alongside_the_median(tiers) -> None:
    from tests.crypto.conftest import make_config, quote, started_engine
    engine = started_engine(make_config(), tiers, first_quote=quote(1_000))
    engine.submit_order(ts_ms=1_000, side="LONG", qty=D("0.010"), intent="OPEN", request_id="o1")
    engine.apply_market(quote(3_000))
    engine.submit_order(ts_ms=3_000, side="LONG", qty=D("0.010"), intent="CLOSE", request_id="c1")
    summary = summarize(engine.ledger.events, starting_capital=D("1000"))
    assert summary["hold_ms_mean"] == 2_000
    assert summary["hold_ms_median"] == 2_000


def test_trades_are_grouped_by_the_utc_hour_they_were_entered(tiers) -> None:
    from tests.crypto.conftest import make_config, quote, started_engine
    # 2021-02-01T05:00:00Z and 2021-02-01T09:00:00Z.
    five, nine = 1_612_155_600_000, 1_612_170_000_000
    engine = started_engine(make_config(), tiers, first_quote=quote(five))
    for index, entry in enumerate((five, nine)):
        engine.apply_market(quote(entry))
        engine.submit_order(ts_ms=entry, side="LONG", qty=D("0.010"), intent="OPEN",
                            request_id=f"o{index}")
        engine.apply_market(quote(entry + 60_000))
        engine.submit_order(ts_ms=entry + 60_000, side="LONG", qty=D("0.010"), intent="CLOSE",
                            request_id=f"c{index}")

    by_hour = summarize(engine.ledger.events, starting_capital=D("1000"))["by_hour_utc"]
    # A perpetual trades around the clock, so the hour is a real axis rather than a session.
    assert set(by_hour) == {"05", "09"}
    assert by_hour["05"]["trades"] == by_hour["09"]["trades"] == 1


def test_an_empty_run_reports_no_hour_buckets_rather_than_twentyfour_zeroes(tiers) -> None:
    assert summarize([], starting_capital=D("1000"))["by_hour_utc"] == {}
