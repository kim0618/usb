"""PnL / fee breakdown. The panel must never be able to disagree with the engine: every preview
figure is checked against what the real engine then does, and every confirmed figure against the
analytics fold of the same ledger."""
from __future__ import annotations

from decimal import Decimal

from app.crypto.paper import pnl_breakdown
from app.crypto.paper.analytics import build_trades
from app.crypto.paper.engine import PaperEngine
from tests.crypto.conftest import make_config, quote, started_engine

D = Decimal


def _open(engine: PaperEngine, side: str = "LONG", qty: str = "0.010", ts: int = 1_100) -> None:
    engine.submit_order(ts_ms=ts, side=side, qty=D(qty), intent="OPEN", request_id=f"open-{ts}")


def test_flat_account_has_no_position_preview(engine: PaperEngine) -> None:
    assert pnl_breakdown.open_position_preview(engine) == {"position_open": False}


def test_preview_leaves_ledger_and_account_untouched(engine: PaperEngine) -> None:
    _open(engine)
    engine.apply_market(quote(1_200, bid="100500.0", ask="100500.1"))
    before_events = len(engine.ledger.events)
    before = (engine.account.position.signed_qty, engine.account.cumulative_fees,
              engine.account.realized_pnl, engine.account.wallet_balance)

    preview = pnl_breakdown.open_position_preview(engine)

    assert preview["position_open"] and preview["close_feasible"]
    assert len(engine.ledger.events) == before_events
    assert (engine.account.position.signed_qty, engine.account.cumulative_fees,
            engine.account.realized_pnl, engine.account.wallet_balance) == before


def test_expected_close_matches_the_real_close(engine: PaperEngine) -> None:
    _open(engine)
    engine.apply_market(quote(1_200, bid="100500.0", ask="100500.1", mark="100510.0"))
    preview = pnl_breakdown.open_position_preview(engine)
    capital = engine.account.capital_base_usdt

    real = engine.submit_order(ts_ms=1_300, side="LONG", qty=D("0.010"), intent="CLOSE",
                               request_id="close-1")

    assert preview["expected_close_fill_price"] == real["fill_price"]
    assert preview["expected_close_fee"] == real["fee"]
    assert preview["expected_segment_net_if_closed"] == engine.account.wallet_balance - capital


def test_segment_net_if_closed_satisfies_the_brief_identity(engine: PaperEngine) -> None:
    """segment realized - confirmed fees -/+ funding + unrealized - close fee - close slippage."""
    _open(engine)
    engine.apply_market(quote(1_200, bid="99800.0", ask="99800.1", mark="99790.0"))
    p = pnl_breakdown.open_position_preview(engine)
    account = engine.account
    segment_fees_and_funding = account.cash_charges - account.charges_at_anchor
    identity = (p["segment_realized_pnl"] - segment_fees_and_funding + p["unrealized_pnl"]
                - p["expected_close_fee"] - p["expected_close_slippage"])
    assert p["expected_segment_net_if_closed"] == identity
    assert p["segment_net_pnl"] + p["unrealized_pnl"] - p["expected_close_fee"] \
        - p["expected_close_slippage"] == p["expected_segment_net_if_closed"]


def test_slippage_is_the_mark_to_fill_gap_split_into_spread_and_depth(config, tiers) -> None:
    thin = started_engine(config, tiers, first_quote=quote(
        1_000, asks=[["100000.1", "10"]], bids=[["100000.0", "10"]]))
    _open(thin, qty="0.020")
    # Mark above the best bid, and the bid too thin for the whole close: part walks a level.
    thin.apply_market(quote(1_200, mark="100100.0",
                            bids=[["100050.0", "0.005"], ["100040.0", "1"]], asks=[["100050.1", "1"]]))
    p = pnl_breakdown.open_position_preview(thin)
    qty = D("0.020")
    assert p["expected_close_spread_cost"] == (D("100100.0") - D("100050.0")) * qty
    walked = (D("100050.0") * D("0.005") + D("100040.0") * D("0.015")) / qty
    assert p["expected_close_fill_price"] == walked
    assert p["expected_close_depth_cost"] == (D("100050.0") - walked) * qty
    assert p["expected_close_slippage"] == (D("100100.0") - walked) * qty


def test_close_that_cannot_fill_reports_the_engines_reason(tiers) -> None:
    engine = started_engine(make_config(capital_krw="100000000"), tiers, first_quote=quote(1_000))
    _open(engine, qty="1.000")
    engine.apply_market(quote(1_200, bid_qty="0.1"))
    p = pnl_breakdown.open_position_preview(engine)
    assert p["close_feasible"] is False
    assert p["close_reject_code"] == "NO_LIQUIDITY"
    assert p["expected_segment_net_if_closed"] is None
    assert p["unrealized_pnl"] is not None  # the price PnL is still shown


def test_closed_trade_fee_split_agrees_with_analytics(engine: PaperEngine) -> None:
    _open(engine, qty="0.010", ts=1_100)
    engine.apply_market(quote(1_150, bid="100200.0", ask="100200.1"))
    _open(engine, qty="0.010", ts=1_160)  # scale in: a second entry fee
    engine.submit_order(ts_ms=1_200, side="LONG", qty=D("0.005"), intent="CLOSE", request_id="part")
    engine.apply_market(quote(1_300, bid="100300.0", ask="100300.1"))
    engine.submit_order(ts_ms=1_310, side="LONG", qty=D("0.015"), intent="CLOSE", request_id="rest")
    _open(engine, side="SHORT", qty="0.010", ts=1_400)
    engine.submit_order(ts_ms=1_500, side="SHORT", qty=D("0.010"), intent="CLOSE", request_id="s")

    trades = build_trades(engine.ledger.events)
    rows = pnl_breakdown.closed_trade_breakdowns(engine.ledger.events)
    assert len(rows) == len(trades) == 2
    for row, trade in zip(rows, trades):
        assert row["index"] == trade.index
        assert row["entry_fee"] + row["exit_fee"] == trade.fees
        assert row["gross_realized_pnl"] == trade.gross_pnl
        assert row["funding"] == trade.funding
        assert row["net_realized_pnl"] == trade.net_pnl
        assert row["entry_fee"] > 0 and row["exit_fee"] > 0
    # Two entry fills and two exit fills on the first trade.
    fees = [e for e in engine.ledger.events if e["event_type"] == "FEE"]
    assert rows[0]["entry_fee"] == D(str(fees[0]["amount"])) + D(str(fees[1]["amount"]))
    assert rows[0]["exit_fee"] == D(str(fees[2]["amount"])) + D(str(fees[3]["amount"]))


def test_open_position_confirmed_costs_include_partial_exits(engine: PaperEngine) -> None:
    _open(engine, qty="0.020")
    engine.submit_order(ts_ms=1_200, side="LONG", qty=D("0.005"), intent="CLOSE", request_id="part")
    p = pnl_breakdown.open_position_preview(engine)
    fees = [D(str(e["amount"])) for e in engine.ledger.events if e["event_type"] == "FEE"]
    assert p["entry_fee"] == fees[0]
    assert p["partial_exit_fee"] == fees[1]


def test_liquidation_fee_is_an_exit_fee_and_has_no_slippage(tiers) -> None:
    engine = started_engine(make_config(leverage="50"), tiers, first_quote=quote(1_000))
    _open(engine, qty="0.050")
    engine.apply_market(quote(1_200, bid="97000.0", ask="97000.1", mark="97000.0"))
    rows = pnl_breakdown.closed_trade_breakdowns(engine.ledger.events)
    assert rows and rows[-1]["liquidated"]
    trade = build_trades(engine.ledger.events)[-1]
    assert rows[-1]["exit_fee"] > 0
    assert rows[-1]["exit_slippage"] == 0
    assert rows[-1]["entry_fee"] + rows[-1]["exit_fee"] == trade.fees


def test_fifty_x_shows_why_the_total_shrank(tiers) -> None:
    """At 50x the round-trip fee is a large share of margin. The breakdown must make a small
    favourable move with a negative net readable at a glance: gross up, fees larger."""
    engine = started_engine(make_config(leverage="50", capital_krw="10000000"), tiers,
                            first_quote=quote(1_000, bid_qty="100", ask_qty="100"))
    _open(engine, qty="0.500")
    engine.apply_market(quote(1_200, bid="100008.0", ask="100008.1", mark="100008.0",
                              bid_qty="100", ask_qty="100"))
    p = pnl_breakdown.open_position_preview(engine)
    assert p["unrealized_pnl"] > 0
    assert p["expected_position_net_if_closed"] < 0
    assert p["entry_fee"] + p["expected_close_fee"] > p["unrealized_pnl"]


def test_signed_effect_fields_are_the_negated_costs(engine: PaperEngine) -> None:
    _open(engine)
    engine.apply_market(quote(1_200, bid="100500.0", ask="100500.1", mark="100510.0"))
    p = pnl_breakdown.open_position_preview(engine)
    assert p["funding_pnl"] == -p["funding"]
    assert p["expected_close_slippage_pnl"] == -p["expected_close_slippage"]
    # The position waterfall adds up exactly from the displayed effects (no partial exits here).
    assert (p["unrealized_pnl"] - p["entry_fee"] + p["funding_pnl"] - p["expected_close_fee"]
            + p["expected_close_slippage_pnl"]) == p["expected_position_net_if_closed"]


def test_krw_conversion_covers_money_fields_only(engine: PaperEngine) -> None:
    _open(engine)
    p = pnl_breakdown.open_position_preview(engine)
    krw = pnl_breakdown.to_krw(p, D("1344"))
    assert krw["expected_position_net_if_closed"] == p["expected_position_net_if_closed"] * D("1344")
    assert "expected_close_fill_price" not in krw and "qty" not in krw
    flat = pnl_breakdown.to_krw({"expected_close_fee": None}, D("1344"))
    assert flat == {"expected_close_fee": None}


def test_no_double_slippage_in_closed_trade_net(engine: PaperEngine) -> None:
    """Net = gross - entry fee - exit fee - funding. Slippage is inside gross and is not taken
    again, so net equals the analytics net even when the fills were far from the mark."""
    engine.apply_market(quote(1_050, bid="100000.0", ask="100010.0", mark="99950.0"))
    _open(engine)
    engine.apply_market(quote(1_150, bid="99990.0", ask="100000.0", mark="100050.0"))
    engine.submit_order(ts_ms=1_160, side="LONG", qty=D("0.010"), intent="CLOSE", request_id="c")
    row = pnl_breakdown.closed_trade_breakdowns(engine.ledger.events)[-1]
    trade = build_trades(engine.ledger.events)[-1]
    assert row["slippage"] > 0
    assert row["net_realized_pnl"] == trade.net_pnl
    assert row["net_realized_pnl"] == (row["gross_realized_pnl"] - row["entry_fee"]
                                       - row["exit_fee"] - row["funding"])


def test_funding_during_position_is_attributed_and_shown(engine: PaperEngine) -> None:
    engine.apply_market(quote(28_799_000, funding_rate="0.0001", next_funding_time_ms=28_800_000))
    _open(engine, ts=28_799_100)
    engine.apply_market(quote(28_800_500, funding_rate="0.0001", next_funding_time_ms=57_600_000))
    p = pnl_breakdown.open_position_preview(engine)
    paid = [D(str(e["amount_paid"])) for e in engine.ledger.events if e["event_type"] == "FUNDING"]
    assert paid and p["funding"] == sum(paid) and p["funding_pnl"] == -sum(paid)
    engine.submit_order(ts_ms=28_800_600, side="LONG", qty=D("0.010"), intent="CLOSE", request_id="c")
    row = pnl_breakdown.closed_trade_breakdowns(engine.ledger.events)[-1]
    assert row["funding"] == sum(paid) == build_trades(engine.ledger.events)[-1].funding
